import { useState } from "react";
import { AudioCapture } from "../audio/capture";
import { recordEnrollmentSample } from "./enrollment";
import { enrollSpeaker, type SpeakerProfile } from "./speaker";

export const ENROLLMENT_PROMPTS = [
  "今天我想用声音和本地助手自然地聊一聊。",
  "请记住我的语气，并忽略房间里其他人的声音。",
  "以后只有我说话时，才开始新的语音对话。",
] as const;

interface SpeakerEnrollmentProps {
  onEnrolled: (profile: SpeakerProfile) => void;
  onCancel?: () => void;
}

export function SpeakerEnrollment({ onEnrolled, onCancel }: SpeakerEnrollmentProps) {
  const [promptIndex, setPromptIndex] = useState(0);
  const [samples, setSamples] = useState<Uint8Array<ArrayBuffer>[]>([]);
  const [currentSample, setCurrentSample] = useState<Uint8Array<ArrayBuffer>>();
  const [recording, setRecording] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const record = async () => {
    setRecording(true);
    setCurrentSample(undefined);
    setError("");
    try {
      const sample = await recordEnrollmentSample({ capture: new AudioCapture() });
      setCurrentSample(sample);
    } catch (caught) {
      const message = caught instanceof DOMException && caught.name === "NotAllowedError"
        ? "请在 Safari 网站设置中允许麦克风"
        : caught instanceof Error ? caught.message : "录音失败，请重试";
      setError(message);
    } finally {
      setRecording(false);
    }
  };

  const acceptCurrent = async () => {
    if (!currentSample) return;
    const completed = [...samples, currentSample];
    if (completed.length < ENROLLMENT_PROMPTS.length) {
      setSamples(completed);
      setPromptIndex(completed.length);
      setCurrentSample(undefined);
      setError("");
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      onEnrolled(await enrollSpeaker(completed));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "声音录入失败，请重新录制");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="enrollment-card" aria-labelledby="enrollment-title">
      <div className="enrollment-heading">
        <div>
          <p className="enrollment-kicker">VOICE ID · {promptIndex + 1} / 3</p>
          <h2 id="enrollment-title">先让我记住你的声音</h2>
        </div>
        {onCancel && (
          <button type="button" className="text-action" onClick={onCancel} disabled={recording || submitting}>
            取消
          </button>
        )}
      </div>

      <div className="voiceprint-progress" aria-label={`录入进度 ${promptIndex + 1} / 3`}>
        {ENROLLMENT_PROMPTS.map((_, index) => (
          <span
            key={index}
            className={index < samples.length ? "done" : index === promptIndex ? "current" : ""}
          />
        ))}
      </div>

      <blockquote>{ENROLLMENT_PROMPTS[promptIndex]}</blockquote>
      <p className="enrollment-guide">在安静环境中自然读出这句话，录音持续 4.5 秒。</p>

      <div className="enrollment-actions">
        <button className="record-sample" type="button" onClick={record} disabled={recording || submitting}>
          <span className={recording ? "record-pulse active" : "record-pulse"} aria-hidden="true" />
          {recording ? "正在录音…" : currentSample ? "重新录本段" : "录制这句话"}
        </button>
        {currentSample && (
          <button className="accept-sample" type="button" onClick={acceptCurrent} disabled={submitting}>
            {submitting ? "正在建立声纹…" : promptIndex === 2 ? "完成录入" : "使用这段"}
          </button>
        )}
      </div>
      {currentSample && !submitting && <p className="sample-ready">本段已录好，可重录或继续</p>}
      {error && <p className="enrollment-error" role="alert">{error}</p>}
    </section>
  );
}
