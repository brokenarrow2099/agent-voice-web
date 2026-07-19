import { describe, expect, it } from "vitest";
import { initialVoiceState, reduceVoiceState, type VoiceState } from "./reducer";
import { routeSocketData } from "./socket";

describe("voice state reducer", () => {
  it("handles transcript, streamed response, tools and final text", () => {
    let state = initialVoiceState;
    state = reduceVoiceState(state, { type: "state", generation_id: 1, state: "thinking" });
    state = reduceVoiceState(state, { type: "transcript.final", generation_id: 1, text: "你好" });
    state = reduceVoiceState(state, { type: "assistant.delta", generation_id: 1, text: "正在" });
    state = reduceVoiceState(state, { type: "tool.start", generation_id: 1, name: "Read" });
    state = reduceVoiceState(state, { type: "assistant.final", generation_id: 1, text: "已经完成" });
    expect(state.phase).toBe("tool");
    expect(state.transcript).toBe("你好");
    expect(state.assistant).toBe("已经完成");
    expect(state.toolName).toBe("Read");
  });

  it("discards stale generations and clears playback state on barge-in", () => {
    let state: VoiceState = {
      ...initialVoiceState,
      connected: true,
      generation: 4,
      assistant: "旧回答",
      phase: "speaking",
    };
    state = reduceVoiceState(state, { type: "assistant.delta", generation_id: 3, text: "不应出现" });
    expect(state.assistant).toBe("旧回答");
    state = reduceVoiceState(state, { type: "local.barge-in", generation_id: 5 });
    expect(state.generation).toBe(5);
    expect(state.phase).toBe("listening");
    expect(state.assistant).toBe("");
  });

  it("tracks reconnect and errors", () => {
    let state = reduceVoiceState(initialVoiceState, { type: "local.connection", connected: true });
    expect(state.connected).toBe(true);
    state = reduceVoiceState(state, {
      type: "error",
      generation_id: 0,
      code: "tts_failed",
      message: "语音不可用",
      retryable: true,
    });
    expect(state.error).toBe("语音不可用");
  });

  it("does not claim to be listening while disconnected", () => {
    const state = reduceVoiceState(initialVoiceState, {
      type: "local.barge-in",
      generation_id: 1,
    });
    expect(state.phase).toBe("disconnected");
  });

  it("shows speaker verification as a transient non-error state", () => {
    const checking = reduceVoiceState(initialVoiceState, {
      type: "local.speaker",
      status: "checking",
    });
    expect(checking.speakerStatus).toBe("checking");
    expect(checking.phase).not.toBe("error");

    const rejected = reduceVoiceState(checking, {
      type: "local.speaker",
      status: "rejected",
    });
    expect(rejected.speakerStatus).toBe("rejected");
    expect(rejected.generation).toBe(checking.generation);
  });
});

describe("socket binary routing", () => {
  it("routes JSON controls and only current-generation PCM", () => {
    const controls: unknown[] = [];
    const audio: ArrayBuffer[] = [];
    routeSocketData('{"type":"audio.start","generation_id":7,"sample_rate":24000,"channels":1,"sentence_id":1}', 7, controls.push.bind(controls), audio.push.bind(audio));
    routeSocketData(new Uint8Array([1, 0]).buffer, 7, controls.push.bind(controls), audio.push.bind(audio), 7);
    routeSocketData(new Uint8Array([2, 0]).buffer, 8, controls.push.bind(controls), audio.push.bind(audio), 7);
    expect(controls).toHaveLength(1);
    expect(audio).toHaveLength(1);
  });
});
