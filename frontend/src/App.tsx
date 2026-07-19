import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { AudioCapture } from "./audio/capture";
import { floatToPcm16 } from "./audio/pcm";
import { AudioPlayer } from "./audio/player";
import { ProcessingCueController } from "./audio/processingCue";
import { VoiceActivityDetector } from "./audio/vad";
import { SpeakerEnrollment } from "./voice/SpeakerEnrollment";
import { SpeakerThreshold } from "./voice/SpeakerThreshold";
import { LatencyDiagnostic } from "./voice/LatencyDiagnostic";
import { LatencyTracker, type LatencySnapshot } from "./voice/latency";
import { processingCueActionForEvent } from "./voice/processingCuePolicy";
import { initialVoiceState, reduceVoiceState, type VoiceEvent, type VoicePhase } from "./voice/reducer";
import {
  SpeakerTurnController,
  fetchSpeakerProfile,
  fetchSpeakerSettings,
  saveSpeakerSettings,
  verifySpeaker,
  type SpeakerProfile,
  type SpeakerSettings,
  type VerifyResponse,
} from "./voice/speaker";
import { VoiceSocket } from "./voice/socket";
import {
  DEFAULT_TTS_VOICE,
  TTS_VOICES,
  isTtsVoice,
  readStoredVoice,
  writeStoredVoice,
  type TTSVoice,
} from "./voice/voices";

const statusLabels: Record<VoicePhase, string> = {
  disconnected: "未连接",
  idle: "准备好了",
  listening: "正在聆听",
  transcribing: "正在识别",
  thinking: "Agent 正在思考",
  tool: "Agent 正在操作",
  speaking: "正在回答",
  error: "需要处理",
};

export default function App() {
  const [state, dispatch] = useReducer(reduceVoiceState, initialVoiceState);
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [localError, setLocalError] = useState("");
  const [speakerProfile, setSpeakerProfile] = useState<SpeakerProfile>();
  const [speakerSettings, setSpeakerSettings] = useState<SpeakerSettings>();
  const [speakerSettingsSaving, setSpeakerSettingsSaving] = useState(false);
  const [lastSpeakerDecision, setLastSpeakerDecision] = useState<VerifyResponse>();
  const [latencySnapshot, setLatencySnapshot] = useState<LatencySnapshot>();
  const [enrollingSpeaker, setEnrollingSpeaker] = useState(false);
  const [voice, setVoice] = useState<TTSVoice>(() => {
    try {
      return readStoredVoice(window.localStorage);
    } catch {
      return DEFAULT_TTS_VOICE;
    }
  });
  const voiceRef = useRef<TTSVoice>(voice);
  const socketRef = useRef<VoiceSocket | undefined>(undefined);
  const captureRef = useRef<AudioCapture | undefined>(undefined);
  const playerRef = useRef(new AudioPlayer());
  const processingCueRef = useRef<ProcessingCueController | undefined>(undefined);
  if (!processingCueRef.current) {
    processingCueRef.current = new ProcessingCueController(
      () => playerRef.current.playProcessingCue(),
      () => playerRef.current.stopProcessingCue(),
    );
  }
  const vadRef = useRef(new VoiceActivityDetector());
  const clientIdRef = useRef("");
  const generationRef = useRef(0);
  const turnRef = useRef(0);
  const verificationStartedRef = useRef(false);
  const speakerSettingsTimerRef = useRef<number | undefined>(undefined);
  const confirmedSpeakerSettingsRef = useRef<SpeakerSettings | undefined>(undefined);
  const latencyTrackerRef = useRef<LatencyTracker | undefined>(undefined);
  if (!latencyTrackerRef.current) latencyTrackerRef.current = new LatencyTracker();
  const speakerControllerRef = useRef<SpeakerTurnController | undefined>(undefined);
  if (!speakerControllerRef.current) {
    speakerControllerRef.current = new SpeakerTurnController({
      verify: async (generation, pcm) => {
        const decision = await verifySpeaker(generation, pcm);
        latencyTrackerRef.current!.recordSpeakerRoundtrip(decision.roundtrip_ms);
        return decision;
      },
      onAccepted: (generation) => {
        generationRef.current = generation;
        socketRef.current?.setGeneration(generation);
        processingCueRef.current?.stop();
        playerRef.current.clear();
        dispatch({ type: "local.barge-in", generation_id: generation });
        dispatch({ type: "local.speaker", status: "accepted" });
      },
      onUtterance: ({ generation, token, pcm }) => {
        const turn = ++turnRef.current;
        latencyTrackerRef.current!.commit(generation, turn);
        setLatencySnapshot({ ...latencyTrackerRef.current!.snapshot! });
        socketRef.current?.sendControl({
          type: "audio.start",
          turn_id: turn,
          generation_id: generation,
          speaker_token: token,
        });
        for (let offset = 0; offset < pcm.byteLength; offset += 32_000) {
          socketRef.current?.sendAudio(pcm.slice(offset, offset + 32_000));
        }
        socketRef.current?.sendControl({
          type: "audio.commit",
          turn_id: turn,
          generation_id: generation,
        });
      },
      onRejected: (reason, decision) => {
        setLastSpeakerDecision(decision);
        dispatch({
          type: "local.speaker",
          status: reason === "rejected" ? "rejected" : "unavailable",
        });
      },
    });
  }

  const handleControl = useCallback((message: VoiceEvent) => {
    if ("generation_id" in message && message.generation_id < generationRef.current) return;
    if ("generation_id" in message && message.generation_id > generationRef.current) {
      generationRef.current = message.generation_id;
      socketRef.current?.setGeneration(message.generation_id);
    }
    const processingCueAction = processingCueActionForEvent(message);
    if (processingCueAction === "begin") processingCueRef.current?.begin();
    if (processingCueAction === "stop") processingCueRef.current?.stop();
    const observation = latencyTrackerRef.current!.observeControl(message);
    if (latencyTrackerRef.current!.snapshot) {
      setLatencySnapshot({ ...latencyTrackerRef.current!.snapshot });
    }
    if (observation.report) socketRef.current?.sendControl(observation.report);
    if (message.type === "audio.end") playerRef.current.flushJitterBuffer();
    dispatch(message);
  }, []);

  const connectSocket = useCallback(async () => {
    const socket = new VoiceSocket({
      onOpen: () => {
        dispatch({ type: "local.connection", connected: true });
        socket.sendControl({
          type: "session.start",
          client_id: clientIdRef.current || "paired-phone",
          generation_id: generationRef.current,
          voice: voiceRef.current,
        });
      },
      onClose: () => {
        processingCueRef.current?.stop();
        verificationStartedRef.current = false;
        speakerControllerRef.current?.reset();
        dispatch({ type: "local.connection", connected: false });
      },
      onControl: handleControl,
      onAudio: (audio) => {
        const receivedAt = performance.now();
        processingCueRef.current?.stop();
        playerRef.current.enqueue(audio);
        const report = latencyTrackerRef.current!.observeFirstAudio(
          receivedAt,
          performance.now(),
        );
        if (latencyTrackerRef.current!.snapshot) {
          setLatencySnapshot({ ...latencyTrackerRef.current!.snapshot });
        }
        if (report) socket.sendControl(report);
      },
    });
    socketRef.current = socket;
    await socket.connect();
  }, [handleControl]);

  const changeVoice = (nextValue: string) => {
    if (!isTtsVoice(nextValue)) return;
    setVoice(nextValue);
    voiceRef.current = nextValue;
    try {
      writeStoredVoice(window.localStorage, nextValue);
    } catch {
      // Voice selection still works for this tab when storage is unavailable.
    }
    socketRef.current?.sendControl({
      type: "session.configure",
      generation_id: generationRef.current,
      voice: nextValue,
    });
  };

  const changeSpeakerThreshold = (threshold: number) => {
    if (!speakerSettings) return;
    setSpeakerSettings({ ...speakerSettings, threshold });
    setSpeakerSettingsSaving(true);
    setLocalError("");
    if (speakerSettingsTimerRef.current !== undefined) {
      window.clearTimeout(speakerSettingsTimerRef.current);
    }
    speakerSettingsTimerRef.current = window.setTimeout(() => {
      void saveSpeakerSettings(threshold).then(
        (confirmed) => {
          confirmedSpeakerSettingsRef.current = confirmed;
          setSpeakerSettings(confirmed);
          setSpeakerSettingsSaving(false);
        },
        (error: unknown) => {
          setSpeakerSettings(confirmedSpeakerSettingsRef.current);
          setSpeakerSettingsSaving(false);
          setLocalError(error instanceof Error ? error.message : "无法保存声纹设置");
        },
      );
    }, 250);
  };

  const handleSamples = useCallback((samples: Float32Array) => {
    if (!socketRef.current?.isOpen()) return;
    const result = vadRef.current.push(samples, playerRef.current.isPlaying);
    const controller = speakerControllerRef.current!;
    if (result.verificationSample) {
      processingCueRef.current?.stop();
      verificationStartedRef.current = true;
      dispatch({ type: "local.speaker", status: "checking" });
      controller.begin(generationRef.current + 1, floatToPcm16(result.verificationSample));
    }
    if (!result.utterance) return;
    const pcm = floatToPcm16(result.utterance);
    if (!verificationStartedRef.current && !controller.hasPending()) {
      dispatch({ type: "local.speaker", status: "checking" });
      controller.begin(generationRef.current + 1, pcm);
    }
    controller.finish(pcm);
    verificationStartedRef.current = false;
  }, []);

  const start = async () => {
    setStarting(true);
    setLocalError("");
    try {
      if (!speakerProfile?.enrolled) throw new Error("请先完成声音录入");
      if (!speakerSettings || speakerSettingsSaving) throw new Error("声纹设置尚未保存");
      if (!window.isSecureContext) throw new Error("需要通过可信 HTTPS 打开，才能使用麦克风");
      const metadata = await fetch("/api/session", { credentials: "same-origin" });
      if (!metadata.ok) throw new Error("配对已失效，请重新使用配对链接打开");
      clientIdRef.current = (await metadata.json()).client_id;
      await playerRef.current.start();
      await connectSocket();
      const capture = new AudioCapture();
      captureRef.current = capture;
      await capture.start(handleSamples);
      setActive(true);
    } catch (error) {
      const message = error instanceof DOMException && error.name === "NotAllowedError"
        ? "没有麦克风权限，请在 Safari 网站设置中允许麦克风"
        : error instanceof Error ? error.message : "无法开始语音对话";
      setLocalError(message);
      socketRef.current?.close();
      processingCueRef.current?.stop();
      await captureRef.current?.stop();
      await playerRef.current.close();
      dispatch({ type: "local.connection", connected: false });
    } finally {
      setStarting(false);
    }
  };

  const end = async () => {
    processingCueRef.current?.stop();
    verificationStartedRef.current = false;
    speakerControllerRef.current?.reset();
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    socketRef.current?.sendControl({ type: "session.end", generation_id: generation });
    socketRef.current?.close();
    await captureRef.current?.stop();
    await playerRef.current.close();
    vadRef.current.reset();
    setActive(false);
    dispatch({ type: "local.connection", connected: false });
  };

  const newConversation = async () => {
    verificationStartedRef.current = false;
    speakerControllerRef.current?.reset();
    if (active) await end();
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    dispatch({ type: "local.barge-in", generation_id: generation });
    setLocalError("");
  };

  useEffect(() => {
    let current = true;
    void Promise.all([fetchSpeakerProfile(), fetchSpeakerSettings()]).then(
      ([profile, settings]) => {
        if (current) {
          setSpeakerProfile(profile);
          setSpeakerSettings(settings);
          confirmedSpeakerSettingsRef.current = settings;
        }
      },
      (error: unknown) => {
        if (current) setLocalError(error instanceof Error ? error.message : "无法读取声纹状态");
      },
    );
    return () => {
      current = false;
      if (speakerSettingsTimerRef.current !== undefined) {
        window.clearTimeout(speakerSettingsTimerRef.current);
      }
      verificationStartedRef.current = false;
      speakerControllerRef.current?.reset();
      processingCueRef.current?.stop();
      socketRef.current?.close();
      void captureRef.current?.stop();
      void playerRef.current.close();
    };
  }, []);

  const phase = active ? state.phase : "disconnected";
  const error = localError || state.error;
  const selectedVoice = TTS_VOICES.find((preset) => preset.id === voice) ?? TTS_VOICES[0];
  const showEnrollment = speakerProfile?.enrolled === false || enrollingSpeaker;
  const speakerHint = {
    idle: "",
    checking: "正在确认说话人",
    accepted: "声纹已确认",
    rejected: lastSpeakerDecision
      ? `匹配 ${Math.round(lastSpeakerDecision.score * 100)}%，要求 ${Math.round(lastSpeakerDecision.threshold * 100)}%`
      : "非本人声音已忽略",
    unavailable: "声纹服务暂不可用",
  }[state.speakerStatus];

  return (
    <main className={`app phase-${phase}${showEnrollment ? " enrollment-mode" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">A</span>
          <div><strong>AGENT VOICE</strong><span>LOCAL / SGLANG</span></div>
        </div>
        <button className="new-chat" type="button" onClick={newConversation}>新对话</button>
      </header>

      <section className="voice-stage" aria-live="polite">
        <div className="orb-wrap">
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          <div className="orb" role="img" aria-label={statusLabels[phase]}>
            <div className="wave"><i /><i /><i /><i /><i /></div>
          </div>
        </div>
        <p className="eyebrow"><span className="status-dot" />{statusLabels[phase]}</p>
        {active && speakerHint && (
          <p className={`speaker-hint status-${state.speakerStatus}`}>{speakerHint}</p>
        )}
      </section>

      {(state.transcript || state.assistant || error) && (
        <section className="conversation" aria-label="对话内容">
          {state.transcript && <article className="message user"><span>你</span><p>{state.transcript}</p></article>}
          {state.assistant && <article className="message assistant"><span>Agent</span><p>{state.assistant}</p></article>}
          {state.toolName && phase === "tool" && <p className="tool-pill">正在使用 {state.toolName}</p>}
          {error && <div className="error-card" role="alert">{error}</div>}
        </section>
      )}

      {showEnrollment ? (
        <SpeakerEnrollment
          onEnrolled={(profile) => {
            setSpeakerProfile(profile);
            setEnrollingSpeaker(false);
            setLocalError("");
          }}
          onCancel={speakerProfile?.enrolled ? () => setEnrollingSpeaker(false) : undefined}
        />
      ) : <footer className="controls">
        <label className="voice-picker">
          <span className="voice-picker-copy">
            <span className="voice-picker-label">回答音色</span>
            <span className="voice-picker-note">
              {selectedVoice.language} · {selectedVoice.gender} · {selectedVoice.character}
            </span>
          </span>
          <span className="voice-picker-select">
            <select
              aria-label="回答音色"
              value={voice}
              onChange={(event) => changeVoice(event.target.value)}
            >
              {TTS_VOICES.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name} · {preset.language}{preset.gender}
                </option>
              ))}
            </select>
          </span>
        </label>
        {!active && speakerSettings && (
          <SpeakerThreshold
            settings={speakerSettings}
            saving={speakerSettingsSaving}
            onChange={changeSpeakerThreshold}
          />
        )}
        {!active ? (
          <button
            className="primary start"
            type="button"
            onClick={start}
            disabled={starting || !speakerProfile?.enrolled || !speakerSettings || speakerSettingsSaving}
          >
            <span className="mic-icon" aria-hidden="true" />
            {starting ? "正在准备…" : speakerProfile && speakerSettings ? "开始对话" : "正在读取设置…"}
          </button>
        ) : (
          <button className="primary stop" type="button" onClick={end}>
            <span className="stop-icon" aria-hidden="true" />结束
          </button>
        )}
        {!active && speakerProfile?.enrolled && (
          <button className="voice-id-action" type="button" onClick={() => setEnrollingSpeaker(true)}>
            重新录入我的声音
          </button>
        )}
        <div className="footer-status">
          <p className="connection"><span className={state.connected ? "online" : ""} />{state.connected ? "语音服务已连接" : active ? "正在连接" : "待机"}</p>
          <LatencyDiagnostic snapshot={latencySnapshot} />
        </div>
      </footer>}
    </main>
  );
}
