import { floatToPcm16 } from "../audio/pcm";

export interface EnrollmentCapture {
  start(onSamples: (samples: Float32Array) => void): Promise<void>;
  stop(): Promise<void>;
}

export interface EnrollmentClock {
  wait(durationMs: number): Promise<void>;
}

interface RecordEnrollmentOptions {
  capture: EnrollmentCapture;
  durationMs?: number;
  clock?: EnrollmentClock;
}

const browserClock: EnrollmentClock = {
  wait: (durationMs) => new Promise((resolve) => window.setTimeout(resolve, durationMs)),
};

export async function recordEnrollmentSample({
  capture,
  durationMs = 4_500,
  clock = browserClock,
}: RecordEnrollmentOptions): Promise<Uint8Array<ArrayBuffer>> {
  const maximumSamples = Math.round(durationMs * 16);
  const chunks: Float32Array[] = [];
  let sampleCount = 0;

  try {
    await capture.start((incoming) => {
      const remaining = maximumSamples - sampleCount;
      if (remaining <= 0) return;
      const chunk = incoming.length > remaining ? incoming.slice(0, remaining) : incoming.slice();
      chunks.push(chunk);
      sampleCount += chunk.length;
    });
    await clock.wait(durationMs);
  } finally {
    await capture.stop();
  }

  const samples = concatenate(chunks, sampleCount);
  if (samples.length === 0 || rms(samples) < 0.012) {
    throw new Error("声音太小，请靠近手机后重新录制");
  }
  return floatToPcm16(samples);
}

function concatenate(chunks: Float32Array[], size: number): Float32Array {
  const result = new Float32Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

function rms(samples: Float32Array): number {
  let total = 0;
  for (const sample of samples) total += sample * sample;
  return Math.sqrt(total / samples.length);
}
