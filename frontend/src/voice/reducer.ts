export type VoicePhase =
  | "disconnected"
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "tool"
  | "speaking"
  | "error";

export type SpeakerStatus = "idle" | "checking" | "accepted" | "rejected" | "unavailable";

export interface VoiceState {
  connected: boolean;
  generation: number;
  phase: VoicePhase;
  transcript: string;
  assistant: string;
  toolName: string;
  error: string;
  speakerStatus: SpeakerStatus;
}

export interface TurnMetricsEvent {
  type: "turn.metrics";
  generation_id: number;
  turn_id: number;
  final: boolean;
  audio_ms?: number;
  asr_ms?: number;
  model_first_text_ms?: number;
  first_sentence_ms?: number;
  tts_first_audio_ms?: number;
  response_first_audio_ms?: number;
  model_total_ms?: number;
  turn_total_ms?: number;
  sentence_count?: number;
  outcome?: string;
}

export type VoiceEvent =
  | { type: "local.connection"; connected: boolean }
  | { type: "local.barge-in"; generation_id: number }
  | { type: "local.speaker"; status: SpeakerStatus }
  | { type: "state"; generation_id: number; state: Exclude<VoicePhase, "disconnected"> }
  | { type: "session.ready"; generation_id: number; client_id: string; resumed?: boolean }
  | { type: "session.configured"; generation_id: number; voice: string }
  | { type: "transcript.final"; generation_id: number; text: string; language?: string }
  | { type: "assistant.delta"; generation_id: number; text: string }
  | { type: "assistant.final"; generation_id: number; text: string }
  | { type: "tool.start"; generation_id: number; name: string }
  | { type: "audio.start"; generation_id: number; sample_rate: number; channels: number; sentence_id: number }
  | { type: "audio.end"; generation_id: number; sentence_id: number; cancelled: boolean }
  | { type: "turn.end"; generation_id: number; speech_available?: boolean; empty?: boolean }
  | TurnMetricsEvent
  | { type: "error"; generation_id: number; code: string; message: string; retryable: boolean }
  | { type: "pong"; generation_id: number; nonce?: string };

export const initialVoiceState: VoiceState = {
  connected: false,
  generation: 0,
  phase: "disconnected",
  transcript: "",
  assistant: "",
  toolName: "",
  error: "",
  speakerStatus: "idle",
};

export function reduceVoiceState(state: VoiceState, action: VoiceEvent): VoiceState {
  if (action.type === "local.connection") {
    return {
      ...state,
      connected: action.connected,
      phase: action.connected ? (state.phase === "disconnected" ? "idle" : state.phase) : "disconnected",
      speakerStatus: action.connected ? state.speakerStatus : "idle",
    };
  }
  if (action.type === "local.speaker") {
    return { ...state, speakerStatus: action.status };
  }
  if (action.type === "local.barge-in") {
    return {
      ...state,
      generation: action.generation_id,
      phase: state.connected ? "listening" : "disconnected",
      transcript: "",
      assistant: "",
      toolName: "",
      error: "",
    };
  }
  if (action.generation_id < state.generation) return state;
  const next = action.generation_id > state.generation ? { ...state, generation: action.generation_id } : state;
  switch (action.type) {
    case "state":
      return {
        ...next,
        phase: action.state,
        ...(action.state === "transcribing" ? { speakerStatus: "idle" as const } : {}),
        error: action.state === "error" ? next.error : "",
        ...(action.state === "transcribing" ? { transcript: "", assistant: "", toolName: "" } : {}),
      };
    case "transcript.final":
      return { ...next, transcript: action.text };
    case "assistant.delta":
      return { ...next, assistant: next.assistant + action.text };
    case "assistant.final":
      return { ...next, assistant: action.text };
    case "tool.start":
      return { ...next, phase: "tool", toolName: action.name };
    case "audio.start":
      return { ...next, phase: "speaking" };
    case "error":
      return { ...next, phase: "error", error: action.message };
    case "turn.end":
      return { ...next, phase: action.empty ? "listening" : next.phase };
    default:
      return next;
  }
}
