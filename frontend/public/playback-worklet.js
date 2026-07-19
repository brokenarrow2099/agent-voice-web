class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.wasPlaying = false;
    this.started = false;
    this.queuedSamples = 0;
    this.port.onmessage = ({ data }) => {
      if (data.type === "push") {
        const samples = new Float32Array(data.samples);
        this.queue.push(samples);
        this.queuedSamples += samples.length;
      }
      if (data.type === "flush") this.started = true;
      if (data.type === "clear") {
        this.queue = [];
        this.offset = 0;
        this.wasPlaying = false;
        this.started = false;
        this.queuedSamples = 0;
      }
    };
  }

  process(_inputs, outputs) {
    const output = outputs[0][0];
    output.fill(0);
    if (!this.started && this.queuedSamples < sampleRate * 0.08) return true;
    this.started = true;
    let written = 0;
    while (written < output.length && this.queue.length) {
      const current = this.queue[0];
      const available = current.length - this.offset;
      const amount = Math.min(available, output.length - written);
      output.set(current.subarray(this.offset, this.offset + amount), written);
      written += amount;
      this.offset += amount;
      this.queuedSamples -= amount;
      if (this.offset === current.length) {
        this.queue.shift();
        this.offset = 0;
      }
    }
    const playing = written > 0 || this.queue.length > 0;
    if (this.wasPlaying && !playing) {
      this.started = false;
      this.port.postMessage({ type: "empty" });
    }
    this.wasPlaying = playing;
    return true;
  }
}

registerProcessor("voice-playback", PlaybackProcessor);
