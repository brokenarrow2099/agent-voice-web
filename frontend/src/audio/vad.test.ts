import { describe, expect, it } from "vitest";
import { VoiceActivityDetector } from "./vad";

const frame = (amplitude: number, milliseconds = 20) =>
  Float32Array.from({ length: milliseconds * 16 }, (_, i) => (i % 2 ? amplitude : -amplitude));

describe("VoiceActivityDetector", () => {
  it("requires 120 ms of speech and preserves 250 ms pre-roll", () => {
    const vad = new VoiceActivityDetector();
    for (let i = 0; i < 20; i += 1) vad.push(frame(0.002), false);
    for (let i = 0; i < 5; i += 1) expect(vad.push(frame(0.08), false).speechStarted).toBe(false);
    const started = vad.push(frame(0.08), false);
    expect(started.speechStarted).toBe(true);
    for (let i = 0; i < 9; i += 1) vad.push(frame(0.08), false);
    for (let i = 0; i < 40; i += 1) vad.push(frame(0.001), false);
    const committed = vad.push(frame(0.001), false).utterance;
    expect(committed).toBeDefined();
    expect(committed!.length).toBeGreaterThanOrEqual(250 * 16 + 120 * 16);
  });

  it("commits after 800 ms silence but ignores sub-250 ms speech", () => {
    const vad = new VoiceActivityDetector();
    for (let i = 0; i < 6; i += 1) vad.push(frame(0.1), false);
    for (let i = 0; i < 40; i += 1) vad.push(frame(0.001), false);
    expect(vad.push(frame(0.001), false).utterance).toBeUndefined();

    for (let i = 0; i < 15; i += 1) vad.push(frame(0.1), false);
    let utterance: Float32Array | undefined;
    for (let i = 0; i < 41; i += 1) utterance = vad.push(frame(0.001), false).utterance ?? utterance;
    expect(utterance).toBeDefined();
  });

  it("uses a higher threshold during playback", () => {
    const quietSpeech = frame(0.018);
    const normal = new VoiceActivityDetector();
    const playback = new VoiceActivityDetector();
    let normalStarted = false;
    let playbackStarted = false;
    for (let i = 0; i < 8; i += 1) {
      normalStarted ||= normal.push(quietSpeech, false).speechStarted;
      playbackStarted ||= playback.push(quietSpeech, true).speechStarted;
    }
    expect(normalStarted).toBe(true);
    expect(playbackStarted).toBe(false);
  });

  it("adapts its noise floor and hard-commits at 45 seconds", () => {
    const vad = new VoiceActivityDetector();
    for (let i = 0; i < 100; i += 1) vad.push(frame(0.004), false);
    expect(vad.noiseFloor).toBeGreaterThan(0.002);
    let utterance: Float32Array | undefined;
    for (let i = 0; i < 2251; i += 1) utterance = vad.push(frame(0.1), false).utterance ?? utterance;
    expect(utterance).toBeDefined();
  });

  it("emits exactly one verification sample after 1000 ms and keeps the full utterance", () => {
    const vad = new VoiceActivityDetector({ verificationMs: 1_000 });
    const samples: Float32Array[] = [];
    let utterance: Float32Array | undefined;
    for (let i = 0; i < 80; i += 1) {
      const result = vad.push(frame(0.08), true);
      if (result.verificationSample) samples.push(result.verificationSample);
    }
    for (let i = 0; i < 41; i += 1) {
      utterance = vad.push(frame(0.001), true).utterance ?? utterance;
    }
    expect(samples).toHaveLength(1);
    expect(samples[0].length).toBeGreaterThanOrEqual(16_000);
    expect(utterance).toBeDefined();
    expect(utterance!.length).toBeGreaterThan(samples[0].length);
  });
});
