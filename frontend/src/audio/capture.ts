import { StreamingResampler } from "./pcm";

export class AudioCapture {
  private context?: AudioContext;
  private stream?: MediaStream;
  private source?: MediaStreamAudioSourceNode;
  private worklet?: AudioWorkletNode;
  private sink?: GainNode;

  async start(onSamples: (samples16k: Float32Array) => void): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前浏览器不支持麦克风采集");
    this.context = new AudioContext({ latencyHint: "interactive" });
    await this.context.audioWorklet.addModule("/capture-worklet.js");
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        sampleRate: 48_000,
      },
    });
    this.source = this.context.createMediaStreamSource(this.stream);
    this.worklet = new AudioWorkletNode(this.context, "voice-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.sink = this.context.createGain();
    this.sink.gain.value = 0;
    const resampler = new StreamingResampler(this.context.sampleRate, 16_000);
    this.worklet.port.onmessage = ({ data }) => {
      const source = data instanceof Float32Array ? data : new Float32Array(data);
      const converted = resampler.push(source);
      if (converted.length) onSamples(converted);
    };
    this.source.connect(this.worklet).connect(this.sink).connect(this.context.destination);
    await this.context.resume();
  }

  async stop(): Promise<void> {
    this.worklet?.disconnect();
    this.source?.disconnect();
    this.sink?.disconnect();
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    if (this.context && this.context.state !== "closed") await this.context.close();
    this.context = undefined;
    this.stream = undefined;
  }
}
