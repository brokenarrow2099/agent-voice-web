export function resampleLinear(
  input: Float32Array,
  inputRate: number,
  outputRate: number,
): Float32Array {
  if (inputRate <= 0 || outputRate <= 0) throw new Error("sample rates must be positive");
  if (input.length === 0) return new Float32Array();
  if (inputRate === outputRate) return input.slice();
  const outputLength = Math.max(1, Math.round((input.length * outputRate) / inputRate));
  const output = new Float32Array(outputLength);
  const ratio = inputRate / outputRate;
  for (let index = 0; index < outputLength; index += 1) {
    const position = index * ratio;
    const left = Math.min(input.length - 1, Math.floor(position));
    const right = Math.min(input.length - 1, left + 1);
    const fraction = position - left;
    output[index] = input[left] * (1 - fraction) + input[right] * fraction;
  }
  return output;
}

export function floatToPcm16(input: Float32Array): Uint8Array<ArrayBuffer> {
  const buffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < input.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[index]));
    view.setInt16(index * 2, sample < 0 ? sample * 32768 : sample * 32767, true);
  }
  return new Uint8Array(buffer);
}

export function pcm16ToFloat(buffer: ArrayBuffer): Float32Array {
  if (buffer.byteLength % 2 !== 0) throw new Error("PCM buffer contains half a sample");
  const view = new DataView(buffer);
  const output = new Float32Array(buffer.byteLength / 2);
  for (let index = 0; index < output.length; index += 1) {
    const value = view.getInt16(index * 2, true);
    output[index] = value < 0 ? value / 32768 : value / 32767;
  }
  return output;
}

export class StreamingResampler {
  private readonly ratio: number;
  private pending = new Float32Array();
  private position = 0;

  constructor(inputRate: number, outputRate: number) {
    if (inputRate <= 0 || outputRate <= 0) throw new Error("sample rates must be positive");
    this.ratio = inputRate / outputRate;
  }

  push(input: Float32Array): Float32Array {
    if (input.length === 0) return new Float32Array();
    const source = new Float32Array(this.pending.length + input.length);
    source.set(this.pending);
    source.set(input, this.pending.length);
    const output: number[] = [];
    while (this.position < source.length) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      if (left >= source.length || (fraction > 0 && left + 1 >= source.length)) break;
      const right = Math.min(source.length - 1, left + 1);
      output.push(source[left] * (1 - fraction) + source[right] * fraction);
      this.position += this.ratio;
    }
    const discard = Math.min(source.length, Math.floor(this.position));
    this.pending = source.slice(discard);
    this.position -= discard;
    return Float32Array.from(output);
  }

  reset(): void {
    this.pending = new Float32Array();
    this.position = 0;
  }
}
