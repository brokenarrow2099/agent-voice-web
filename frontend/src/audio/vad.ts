export interface VADResult {
  speechStarted: boolean;
  verificationSample?: Float32Array;
  utterance?: Float32Array;
}

export interface VADOptions {
  sampleRate: number;
  speechStartMs: number;
  verificationMs: number;
  preRollMs: number;
  silenceMs: number;
  minimumSpeechMs: number;
  maximumUtteranceMs: number;
}

const defaults: VADOptions = {
  sampleRate: 16_000,
  speechStartMs: 120,
  verificationMs: 1_000,
  preRollMs: 250,
  silenceMs: 800,
  minimumSpeechMs: 250,
  maximumUtteranceMs: 45_000,
};

export class VoiceActivityDetector {
  private readonly options: VADOptions;
  private preRoll: Float32Array[] = [];
  private preRollSamples = 0;
  private candidate: Float32Array[] = [];
  private candidateSamples = 0;
  private utterance: Float32Array[] = [];
  private utteranceSamples = 0;
  private voicedSamples = 0;
  private silenceSamples = 0;
  private verificationEmitted = false;
  private active = false;
  noiseFloor = 0.002;

  constructor(options: Partial<VADOptions> = {}) {
    this.options = { ...defaults, ...options };
  }

  push(frame: Float32Array, playbackActive: boolean): VADResult {
    if (frame.length === 0) return { speechStarted: false };
    const energy = rms(frame);
    const baseThreshold = Math.max(0.012, this.noiseFloor * 3);
    const threshold = playbackActive ? baseThreshold * 2 : baseThreshold;
    const voiced = energy >= threshold;
    let speechStarted = false;
    let verificationSample: Float32Array | undefined;

    if (!this.active) {
      if (voiced) {
        this.candidate.push(frame.slice());
        this.candidateSamples += frame.length;
        if (this.candidateSamples >= millisecondsToSamples(this.options.speechStartMs, this.options.sampleRate)) {
          this.active = true;
          speechStarted = true;
          this.utterance = [...this.preRoll, ...this.candidate];
          this.utteranceSamples = this.preRollSamples + this.candidateSamples;
          this.voicedSamples = this.candidateSamples;
          this.candidate = [];
          this.candidateSamples = 0;
          this.preRoll = [];
          this.preRollSamples = 0;
          verificationSample = this.takeVerificationSample();
        }
      } else {
        this.candidate = [];
        this.candidateSamples = 0;
        this.updateNoiseFloor(energy);
        this.addPreRoll(frame);
      }
      return { speechStarted, verificationSample };
    }

    this.utterance.push(frame.slice());
    this.utteranceSamples += frame.length;
    if (voiced) {
      this.voicedSamples += frame.length;
      this.silenceSamples = 0;
    } else {
      this.silenceSamples += frame.length;
    }
    verificationSample = this.takeVerificationSample();

    const silenceLimit = millisecondsToSamples(this.options.silenceMs, this.options.sampleRate);
    const hardLimit = millisecondsToSamples(this.options.maximumUtteranceMs, this.options.sampleRate);
    if (this.silenceSamples > silenceLimit || this.utteranceSamples >= hardLimit) {
      const minimum = millisecondsToSamples(this.options.minimumSpeechMs, this.options.sampleRate);
      const completed = this.voicedSamples >= minimum ? concatenate(this.utterance) : undefined;
      this.resetUtterance();
      return { speechStarted: false, verificationSample, utterance: completed };
    }
    return { speechStarted: false, verificationSample };
  }

  reset(): void {
    this.preRoll = [];
    this.preRollSamples = 0;
    this.candidate = [];
    this.candidateSamples = 0;
    this.resetUtterance();
  }

  private addPreRoll(frame: Float32Array): void {
    this.preRoll.push(frame.slice());
    this.preRollSamples += frame.length;
    const target = millisecondsToSamples(this.options.preRollMs, this.options.sampleRate);
    while (this.preRoll.length > 1 && this.preRollSamples - this.preRoll[0].length >= target) {
      this.preRollSamples -= this.preRoll[0].length;
      this.preRoll.shift();
    }
  }

  private updateNoiseFloor(energy: number): void {
    const bounded = Math.min(0.02, Math.max(0.0001, energy));
    this.noiseFloor = this.noiseFloor * 0.95 + bounded * 0.05;
  }

  private takeVerificationSample(): Float32Array | undefined {
    const target = millisecondsToSamples(this.options.verificationMs, this.options.sampleRate);
    if (this.verificationEmitted || this.voicedSamples < target) return undefined;
    this.verificationEmitted = true;
    return concatenate(this.utterance);
  }

  private resetUtterance(): void {
    this.active = false;
    this.utterance = [];
    this.utteranceSamples = 0;
    this.voicedSamples = 0;
    this.silenceSamples = 0;
    this.verificationEmitted = false;
  }
}

function rms(frame: Float32Array): number {
  let total = 0;
  for (const sample of frame) total += sample * sample;
  return Math.sqrt(total / frame.length);
}

function millisecondsToSamples(milliseconds: number, sampleRate: number): number {
  return Math.round((milliseconds * sampleRate) / 1000);
}

function concatenate(chunks: Float32Array[]): Float32Array {
  const size = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const result = new Float32Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}
