import { describe, expect, it } from "vitest";
import { floatToPcm16, pcm16ToFloat, resampleLinear, StreamingResampler } from "./pcm";

describe("PCM conversion and resampling", () => {
  it("resamples 48 kHz capture to exactly 16 kHz", () => {
    const source = Float32Array.from({ length: 480 }, (_, i) => Math.sin((2 * Math.PI * i) / 48));
    const result = resampleLinear(source, 48_000, 16_000);
    expect(result).toHaveLength(160);
    expect(result[0]).toBeCloseTo(source[0], 6);
    expect(result[159]).toBeCloseTo(source[477], 5);
  });

  it("resamples 24 kHz playback to a 48 kHz device rate", () => {
    const source = Float32Array.from([0, 0.5, 1, 0.5]);
    const result = resampleLinear(source, 24_000, 48_000);
    expect(result).toHaveLength(8);
    expect(result[0]).toBeCloseTo(0);
    expect(result[2]).toBeCloseTo(0.5);
    expect(result[4]).toBeCloseTo(1);
  });

  it("round trips signed little-endian int16 without clipping overflow", () => {
    const source = Float32Array.from([-2, -1, -0.25, 0, 0.25, 1, 2]);
    const pcm = floatToPcm16(source);
    expect(pcm.byteLength).toBe(source.length * 2);
    const restored = pcm16ToFloat(pcm.buffer);
    expect(restored[0]).toBeCloseTo(-1, 4);
    expect(restored[3]).toBe(0);
    expect(restored[6]).toBeCloseTo(1, 4);
  });

  it("preserves fractional phase across AudioWorklet chunks", () => {
    const source = Float32Array.from({ length: 4_800 }, (_, i) => Math.sin((2 * Math.PI * i) / 97));
    const stream = new StreamingResampler(48_000, 16_000);
    const chunks: Float32Array[] = [];
    for (let offset = 0; offset < source.length; offset += 128) {
      chunks.push(stream.push(source.slice(offset, offset + 128)));
    }
    const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
    const combined = new Float32Array(length);
    let cursor = 0;
    for (const chunk of chunks) { combined.set(chunk, cursor); cursor += chunk.length; }
    expect(combined.length).toBeGreaterThanOrEqual(1_599);
    expect(combined.length).toBeLessThanOrEqual(1_600);
    expect(combined[1_000]).toBeCloseTo(source[3_000], 5);
  });
});
