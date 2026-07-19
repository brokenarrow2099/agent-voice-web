import { pcm16ToFloat, StreamingResampler } from "./pcm";

export class AudioPlayer {
  private context?: AudioContext;
  private worklet?: AudioWorkletNode;
  private resampler?: StreamingResampler;
  private playing = false;
  private processingCuePlaying = false;
  private processingCue?: { gain: GainNode; oscillators: OscillatorNode[] };
  private processingCueTimer?: number;

  get isPlaying(): boolean {
    return this.playing || this.processingCuePlaying;
  }

  async start(): Promise<void> {
    if (this.context) {
      await this.context.resume();
      return;
    }
    this.context = new AudioContext({ latencyHint: "interactive" });
    await this.context.audioWorklet.addModule("/playback-worklet.js");
    this.worklet = new AudioWorkletNode(this.context, "voice-playback", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.resampler = new StreamingResampler(24_000, this.context.sampleRate);
    this.worklet.port.onmessage = ({ data }) => {
      if (data?.type === "empty") this.playing = false;
    };
    this.worklet.connect(this.context.destination);
    await this.context.resume();
  }

  enqueue(pcm: ArrayBuffer): void {
    if (!this.context || !this.worklet) return;
    this.stopProcessingCue();
    const source = pcm16ToFloat(pcm);
    const samples = this.resampler!.push(source);
    if (!samples.length) return;
    this.playing = true;
    this.worklet.port.postMessage({ type: "push", samples: samples.buffer }, [samples.buffer]);
  }

  clear(): void {
    this.playing = false;
    this.stopProcessingCue();
    this.resampler?.reset();
    this.worklet?.port.postMessage({ type: "clear" });
  }

  playProcessingCue(): void {
    const context = this.context;
    if (!context || context.state === "closed") return;
    this.stopProcessingCue();

    const now = context.currentTime + 0.02;
    const master = context.createGain();
    master.gain.setValueAtTime(0.72, now);
    master.connect(context.destination);

    const oscillators: OscillatorNode[] = [];
    const notes = [440, 554.37, 659.25];
    notes.forEach((frequency, index) => {
      const start = now + index * 0.32;
      const end = start + 0.92;
      this.addCueTone(context, master, oscillators, frequency, start, end, 0.018);
      this.addCueTone(context, master, oscillators, frequency * 2, start, end - 0.12, 0.0024);
    });

    const cue = { gain: master, oscillators };
    this.processingCue = cue;
    this.processingCuePlaying = true;
    this.processingCueTimer = window.setTimeout(() => {
      if (this.processingCue !== cue) return;
      this.processingCue = undefined;
      this.processingCuePlaying = false;
      this.processingCueTimer = undefined;
      master.disconnect();
    }, 1_700);
  }

  stopProcessingCue(): void {
    if (this.processingCueTimer !== undefined) window.clearTimeout(this.processingCueTimer);
    this.processingCueTimer = undefined;
    this.processingCuePlaying = false;
    const cue = this.processingCue;
    this.processingCue = undefined;
    if (!cue || !this.context || this.context.state === "closed") return;

    const now = this.context.currentTime;
    cue.gain.gain.cancelScheduledValues(now);
    cue.gain.gain.setValueAtTime(Math.max(cue.gain.gain.value, 0.0001), now);
    cue.gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.08);
    for (const oscillator of cue.oscillators) {
      try {
        oscillator.stop(now + 0.09);
      } catch {
        // The tone may already have completed naturally.
      }
    }
    window.setTimeout(() => cue.gain.disconnect(), 120);
  }

  flushJitterBuffer(): void {
    this.worklet?.port.postMessage({ type: "flush" });
  }

  async close(): Promise<void> {
    this.clear();
    this.worklet?.disconnect();
    if (this.context && this.context.state !== "closed") await this.context.close();
    this.context = undefined;
    this.worklet = undefined;
    this.resampler = undefined;
  }

  private addCueTone(
    context: AudioContext,
    destination: AudioNode,
    oscillators: OscillatorNode[],
    frequency: number,
    start: number,
    end: number,
    peak: number,
  ): void {
    const oscillator = context.createOscillator();
    const envelope = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(frequency, start);
    envelope.gain.setValueAtTime(0.0001, start);
    envelope.gain.exponentialRampToValueAtTime(peak, start + 0.13);
    envelope.gain.exponentialRampToValueAtTime(0.0001, end);
    oscillator.connect(envelope);
    envelope.connect(destination);
    oscillator.start(start);
    oscillator.stop(end + 0.02);
    oscillators.push(oscillator);
  }
}
