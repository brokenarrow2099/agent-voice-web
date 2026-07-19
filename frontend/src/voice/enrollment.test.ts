import { describe, expect, it } from "vitest";
import { recordEnrollmentSample, type EnrollmentClock } from "./enrollment";

const frame = (amplitude: number, milliseconds = 20) =>
  Float32Array.from(
    { length: milliseconds * 16 },
    (_, index) => (index % 2 ? amplitude : -amplitude),
  );

class FakeCapture {
  stopped = false;

  constructor(private readonly frames: Float32Array[]) {}

  async start(onSamples: (samples: Float32Array) => void): Promise<void> {
    for (const samples of this.frames) onSamples(samples);
  }

  async stop(): Promise<void> {
    this.stopped = true;
  }
}

class FakeClock implements EnrollmentClock {
  private resolve?: () => void;

  wait(_durationMs: number): Promise<void> {
    return new Promise((resolve) => {
      this.resolve = resolve;
    });
  }

  advance(): void {
    this.resolve?.();
  }
}

describe("recordEnrollmentSample", () => {
  it("captures one bounded sample and always stops the microphone", async () => {
    const capture = new FakeCapture([frame(0.1, 20), frame(0.1, 20)]);
    const clock = new FakeClock();
    const pending = recordEnrollmentSample({ capture, durationMs: 40, clock });
    await Promise.resolve();
    clock.advance();
    const pcm = await pending;

    expect(pcm.byteLength).toBe(40 * 16 * 2);
    expect(capture.stopped).toBe(true);
  });

  it("rejects a silent sample after stopping the microphone", async () => {
    const capture = new FakeCapture([frame(0.001, 40)]);
    const clock = new FakeClock();
    const pending = recordEnrollmentSample({ capture, durationMs: 40, clock });
    await Promise.resolve();
    clock.advance();

    await expect(pending).rejects.toThrow("声音太小");
    expect(capture.stopped).toBe(true);
  });
});
