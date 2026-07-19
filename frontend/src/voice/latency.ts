import type { TurnMetricsEvent, VoiceEvent } from "./reducer";

export interface LatencySnapshot {
  generation_id: number;
  turn_id: number;
  speaker_roundtrip_ms?: number;
  commit_to_transcript_ms?: number;
  commit_to_first_text_ms?: number;
  commit_to_first_audio_ms?: number;
  first_audio_to_enqueue_ms?: number;
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

export interface ClientMetricsPayload extends Record<string, unknown> {
  type: "client.metrics";
  generation_id: number;
  turn_id: number;
  stage: "first_audio" | "complete";
  speaker_roundtrip_ms?: number;
  commit_to_transcript_ms?: number;
  commit_to_first_text_ms?: number;
  commit_to_first_audio_ms?: number;
  first_audio_to_enqueue_ms?: number;
}

interface Observation {
  report?: ClientMetricsPayload;
}

const BACKEND_FIELDS = [
  "audio_ms",
  "asr_ms",
  "model_first_text_ms",
  "first_sentence_ms",
  "tts_first_audio_ms",
  "response_first_audio_ms",
  "model_total_ms",
  "turn_total_ms",
  "sentence_count",
] as const;

export class LatencyTracker {
  snapshot?: LatencySnapshot;
  private committedAt?: number;
  private pendingSpeakerRoundtrip?: number;
  private firstAudioReported = false;
  private completionReported = false;

  constructor(private readonly now: () => number = () => performance.now()) {}

  recordSpeakerRoundtrip(milliseconds: number): void {
    if (Number.isFinite(milliseconds) && milliseconds >= 0) {
      this.pendingSpeakerRoundtrip = milliseconds;
    }
  }

  commit(generation: number, turnId: number): void {
    this.committedAt = this.now();
    this.snapshot = {
      generation_id: generation,
      turn_id: turnId,
      ...(this.pendingSpeakerRoundtrip === undefined
        ? {}
        : { speaker_roundtrip_ms: this.pendingSpeakerRoundtrip }),
    };
    this.pendingSpeakerRoundtrip = undefined;
    this.firstAudioReported = false;
    this.completionReported = false;
  }

  observeControl(message: VoiceEvent): Observation {
    if (!this.snapshot || !("generation_id" in message)) return {};
    if (message.generation_id !== this.snapshot.generation_id) return {};
    if (message.type === "transcript.final" && this.snapshot.commit_to_transcript_ms === undefined) {
      this.snapshot.commit_to_transcript_ms = this.elapsedNow();
    } else if (message.type === "assistant.delta" && this.snapshot.commit_to_first_text_ms === undefined) {
      this.snapshot.commit_to_first_text_ms = this.elapsedNow();
    } else if (message.type === "turn.metrics") {
      this.mergeBackend(message);
    } else if (message.type === "turn.end" && !this.completionReported) {
      this.completionReported = true;
      return { report: this.report("complete") };
    }
    return {};
  }

  observeFirstAudio(receivedAt: number, enqueuedAt: number): ClientMetricsPayload | undefined {
    if (!this.snapshot || this.committedAt === undefined || this.firstAudioReported) {
      return undefined;
    }
    this.firstAudioReported = true;
    this.snapshot.commit_to_first_audio_ms = Math.max(0, receivedAt - this.committedAt);
    this.snapshot.first_audio_to_enqueue_ms = Math.max(0, enqueuedAt - receivedAt);
    return this.report("first_audio");
  }

  private elapsedNow(): number {
    return Math.max(0, this.now() - (this.committedAt ?? this.now()));
  }

  private mergeBackend(message: TurnMetricsEvent): void {
    for (const field of BACKEND_FIELDS) {
      const value = message[field];
      if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
        this.snapshot![field] = value;
      }
    }
    if (typeof message.outcome === "string") this.snapshot!.outcome = message.outcome;
  }

  private report(stage: "first_audio" | "complete"): ClientMetricsPayload {
    const current = this.snapshot!;
    return {
      type: "client.metrics",
      generation_id: current.generation_id,
      turn_id: current.turn_id,
      stage,
      speaker_roundtrip_ms: current.speaker_roundtrip_ms,
      commit_to_transcript_ms: current.commit_to_transcript_ms,
      commit_to_first_text_ms: current.commit_to_first_text_ms,
      commit_to_first_audio_ms: current.commit_to_first_audio_ms,
      first_audio_to_enqueue_ms: current.first_audio_to_enqueue_ms,
    };
  }
}
