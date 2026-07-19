import { describe, expect, it } from "vitest";
import { LatencyTracker } from "./latency";

describe("LatencyTracker", () => {
  it("correlates browser and backend stages for one generation", () => {
    let now = 100;
    const tracker = new LatencyTracker(() => now);
    tracker.recordSpeakerRoundtrip(86);
    tracker.commit(3, 2);

    now = 400;
    tracker.observeControl({
      type: "transcript.final", generation_id: 3, text: "问题",
    });
    now = 520;
    tracker.observeControl({
      type: "assistant.delta", generation_id: 3, text: "回答",
    });
    tracker.observeControl({
      type: "turn.metrics",
      generation_id: 3,
      turn_id: 2,
      final: false,
      asr_ms: 280,
      model_first_text_ms: 115,
      first_sentence_ms: 170,
      tts_first_audio_ms: 125,
      response_first_audio_ms: 710,
      sentence_count: 1,
    });
    now = 900;
    const report = tracker.observeFirstAudio(895, 900);

    expect(tracker.snapshot).toMatchObject({
      generation_id: 3,
      turn_id: 2,
      speaker_roundtrip_ms: 86,
      commit_to_transcript_ms: 300,
      commit_to_first_text_ms: 420,
      commit_to_first_audio_ms: 795,
      first_audio_to_enqueue_ms: 5,
      asr_ms: 280,
      tts_first_audio_ms: 125,
    });
    expect(report).toMatchObject({
      type: "client.metrics",
      stage: "first_audio",
      generation_id: 3,
      turn_id: 2,
      commit_to_first_audio_ms: 795,
    });
  });

  it("ignores stale events and reports completion once", () => {
    let now = 0;
    const tracker = new LatencyTracker(() => now);
    tracker.commit(5, 4);
    tracker.observeControl({ type: "assistant.delta", generation_id: 4, text: "旧" });
    expect(tracker.snapshot?.commit_to_first_text_ms).toBeUndefined();
    now = 40;
    const first = tracker.observeControl({ type: "turn.end", generation_id: 5 });
    const second = tracker.observeControl({ type: "turn.end", generation_id: 5 });
    expect(first.report?.stage).toBe("complete");
    expect(second.report).toBeUndefined();
  });
});
