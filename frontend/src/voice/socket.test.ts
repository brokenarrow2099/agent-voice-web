import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceSocket } from "./socket";

class FakeWebSocket {
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];

  binaryType = "";
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string | ArrayBuffer }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: unknown[] = [];

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(value: unknown) {
    this.sent.push(value);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeWebSocket.instances = [];
});

describe("VoiceSocket connection readiness", () => {
  it("resolves connect only after the WebSocket is open and reports send readiness", async () => {
    vi.stubGlobal("window", {
      location: { protocol: "https:", host: "voice.local:8443" },
      setTimeout,
      clearTimeout,
    });
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const socket = new VoiceSocket({
      onOpen: vi.fn(),
      onClose: vi.fn(),
      onControl: vi.fn(),
      onAudio: vi.fn(),
    });

    let connected = false;
    const connection = socket.connect().then(() => { connected = true; });
    await Promise.resolve();
    expect(connected).toBe(false);
    expect(socket.sendControl({ type: "ping" })).toBe(false);

    FakeWebSocket.instances[0].open();
    await connection;
    expect(connected).toBe(true);
    expect(socket.sendControl({ type: "ping" })).toBe(true);
  });
});
