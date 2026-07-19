import type { VoiceEvent } from "./reducer";

export function routeSocketData(
  data: string | ArrayBuffer,
  currentGeneration: number,
  onControl: (event: VoiceEvent) => void,
  onAudio: (audio: ArrayBuffer) => void,
  audioGeneration = currentGeneration,
): void {
  if (typeof data === "string") {
    const parsed = JSON.parse(data) as VoiceEvent;
    onControl(parsed);
    return;
  }
  if (audioGeneration === currentGeneration) onAudio(data);
}

export interface VoiceSocketCallbacks {
  onOpen: () => void;
  onClose: () => void;
  onControl: (event: VoiceEvent) => void;
  onAudio: (audio: ArrayBuffer) => void;
}

export class VoiceSocket {
  private socket?: WebSocket;
  private reconnectTimer?: number;
  private reconnectAttempt = 0;
  private intentionalClose = false;
  private currentGeneration = 0;
  private audioGeneration = 0;

  constructor(private readonly callbacks: VoiceSocketCallbacks) {}

  connect(): Promise<void> {
    this.intentionalClose = false;
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.socket = new WebSocket(`${scheme}//${window.location.host}/ws/voice`);
    this.socket.binaryType = "arraybuffer";
    let settled = false;
    let resolveConnection!: () => void;
    let rejectConnection!: (error: Error) => void;
    const connection = new Promise<void>((resolve, reject) => {
      resolveConnection = resolve;
      rejectConnection = reject;
    });
    const timeout = window.setTimeout(() => {
      if (settled) return;
      settled = true;
      rejectConnection(new Error("连接语音服务超时，请检查网络和服务状态后重试"));
      this.socket?.close();
    }, 10_000);
    this.socket.onopen = () => {
      settled = true;
      window.clearTimeout(timeout);
      this.reconnectAttempt = 0;
      this.callbacks.onOpen();
      resolveConnection();
    };
    this.socket.onmessage = (message) => {
      if (typeof message.data === "string") {
        const parsed = JSON.parse(message.data) as VoiceEvent;
        if ("generation_id" in parsed && parsed.generation_id >= this.currentGeneration) {
          this.currentGeneration = parsed.generation_id;
        }
        if (parsed.type === "audio.start") this.audioGeneration = parsed.generation_id;
        this.callbacks.onControl(parsed);
      } else {
        routeSocketData(
          message.data as ArrayBuffer,
          this.currentGeneration,
          this.callbacks.onControl,
          this.callbacks.onAudio,
          this.audioGeneration,
        );
      }
    };
    this.socket.onclose = () => {
      window.clearTimeout(timeout);
      if (!settled) {
        settled = true;
        rejectConnection(new Error("无法连接语音服务，请检查配对、网络和服务状态"));
      }
      this.callbacks.onClose();
      if (!this.intentionalClose) this.scheduleReconnect();
    };
    this.socket.onerror = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      rejectConnection(new Error("无法连接语音服务，请检查配对、网络和服务状态"));
    };
    return connection;
  }

  isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  sendControl(event: Record<string, unknown>): boolean {
    if (!this.isOpen()) return false;
    this.socket!.send(JSON.stringify(event));
    return true;
  }

  sendAudio(audio: ArrayBuffer | ArrayBufferView<ArrayBuffer>): boolean {
    if (!this.isOpen()) return false;
    this.socket!.send(audio);
    return true;
  }

  setGeneration(generation: number): void {
    this.currentGeneration = generation;
  }

  close(): void {
    this.intentionalClose = true;
    if (this.reconnectTimer !== undefined) window.clearTimeout(this.reconnectTimer);
    this.socket?.close(1000);
  }

  private scheduleReconnect(): void {
    const delay = Math.min(10_000, 500 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.reconnectTimer = window.setTimeout(() => {
      void this.connect().catch(() => undefined);
    }, delay);
  }
}
