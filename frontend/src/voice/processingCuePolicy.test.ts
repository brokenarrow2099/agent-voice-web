import { describe, expect, it } from "vitest";
import { processingCueActionForEvent } from "./processingCuePolicy";

describe("processingCueActionForEvent", () => {
  it("begins for thinking and tool work", () => {
    expect(processingCueActionForEvent({ type: "state", generation_id: 1, state: "thinking" })).toBe("begin");
    expect(processingCueActionForEvent({ type: "state", generation_id: 1, state: "tool" })).toBe("begin");
    expect(processingCueActionForEvent({ type: "tool.start", generation_id: 1, name: "Bash" })).toBe("begin");
  });

  it("stops at first Agent text or response audio", () => {
    expect(processingCueActionForEvent({ type: "assistant.delta", generation_id: 1, text: "好" })).toBe("stop");
    expect(processingCueActionForEvent({ type: "assistant.final", generation_id: 1, text: "好了" })).toBe("stop");
    expect(processingCueActionForEvent({
      type: "audio.start",
      generation_id: 1,
      sample_rate: 24_000,
      channels: 1,
      sentence_id: 1,
    })).toBe("stop");
  });

  it("stops on terminal and non-processing phases", () => {
    expect(processingCueActionForEvent({ type: "state", generation_id: 1, state: "listening" })).toBe("stop");
    expect(processingCueActionForEvent({ type: "turn.end", generation_id: 1 })).toBe("stop");
    expect(processingCueActionForEvent({
      type: "error",
      generation_id: 1,
      code: "failed",
      message: "失败",
      retryable: false,
    })).toBe("stop");
  });

  it("ignores events that do not change processing feedback", () => {
    expect(processingCueActionForEvent({ type: "transcript.final", generation_id: 1, text: "你好" })).toBe("ignore");
    expect(processingCueActionForEvent({ type: "pong", generation_id: 1 })).toBe("ignore");
  });
});
