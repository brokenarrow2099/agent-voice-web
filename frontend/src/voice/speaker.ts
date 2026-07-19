export interface SpeakerProfile {
  enrolled: boolean;
  created_at?: string;
  model_id?: string;
}

export interface SpeakerSettings {
  threshold: number;
  minimum: number;
  maximum: number;
}

export interface VerifyResponse {
  accepted: boolean;
  speaker_token?: string;
  score: number;
  threshold: number;
  speaker_ms: number;
  roundtrip_ms: number;
}

export interface AcceptedUtterance {
  generation: number;
  token: string;
  pcm: Uint8Array<ArrayBuffer>;
}

export type SpeakerRejection = "rejected" | "unavailable";

interface SpeakerTurnCallbacks {
  verify: (generation: number, pcm: Uint8Array<ArrayBuffer>) => Promise<VerifyResponse>;
  onAccepted: (generation: number) => void;
  onUtterance: (utterance: AcceptedUtterance) => void;
  onRejected: (reason: SpeakerRejection, decision?: VerifyResponse) => void;
}

interface PendingTurn {
  sequence: number;
  generation: number;
  pcm?: Uint8Array<ArrayBuffer>;
  decision?: VerifyResponse;
}

export async function fetchSpeakerProfile(): Promise<SpeakerProfile> {
  const response = await fetch("/api/speaker/profile", { credentials: "same-origin" });
  if (!response.ok) throw new Error("无法读取声纹状态");
  return response.json() as Promise<SpeakerProfile>;
}

export async function fetchSpeakerSettings(): Promise<SpeakerSettings> {
  const response = await fetch("/api/speaker/settings", { credentials: "same-origin" });
  if (!response.ok) throw new Error("无法读取声纹设置");
  return response.json() as Promise<SpeakerSettings>;
}

export async function saveSpeakerSettings(threshold: number): Promise<SpeakerSettings> {
  const response = await fetch("/api/speaker/settings", {
    method: "PUT",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ threshold }),
  });
  if (!response.ok) throw new Error(await responseMessage(response, "无法保存声纹设置"));
  return response.json() as Promise<SpeakerSettings>;
}

export async function enrollSpeaker(
  samples: readonly Uint8Array<ArrayBuffer>[],
): Promise<SpeakerProfile> {
  const form = new FormData();
  for (const [index, sample] of samples.entries()) {
    form.append(
      "samples",
      new Blob([sample], { type: "application/octet-stream" }),
      `sample-${index + 1}.pcm`,
    );
  }
  const response = await fetch("/api/speaker/enroll", {
    method: "POST",
    credentials: "same-origin",
    body: form,
  });
  if (!response.ok) throw new Error(await responseMessage(response, "声音录入失败"));
  return response.json() as Promise<SpeakerProfile>;
}

export async function verifySpeaker(
  generation: number,
  pcm: Uint8Array<ArrayBuffer>,
): Promise<VerifyResponse> {
  const started = performance.now();
  const response = await fetch(`/api/speaker/verify?generation_id=${generation}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/octet-stream" },
    body: pcm,
  });
  if (!response.ok) {
    throw new Error(response.status === 409 ? "请先录入声音" : "声纹服务暂不可用");
  }
  const decision = await response.json() as Omit<VerifyResponse, "roundtrip_ms">;
  return { ...decision, roundtrip_ms: performance.now() - started };
}

export class SpeakerTurnController {
  private sequence = 0;
  private pending?: PendingTurn;

  constructor(private readonly callbacks: SpeakerTurnCallbacks) {}

  begin(generation: number, sample: Uint8Array<ArrayBuffer>): void {
    const sequence = ++this.sequence;
    this.pending = { sequence, generation };
    void this.callbacks.verify(generation, sample).then(
      (decision) => this.resolve(sequence, decision),
      () => this.reject(sequence, "unavailable"),
    );
  }

  finish(pcm: Uint8Array<ArrayBuffer>): void {
    if (!this.pending) return;
    this.pending.pcm = pcm;
    this.flush();
  }

  reset(): void {
    this.sequence += 1;
    this.pending = undefined;
  }

  hasPending(): boolean {
    return this.pending !== undefined;
  }

  private resolve(sequence: number, decision: VerifyResponse): void {
    const pending = this.pending;
    if (!pending || pending.sequence !== sequence) return;
    if (!decision.accepted) {
      this.reject(sequence, "rejected", decision);
      return;
    }
    if (!decision.speaker_token) {
      this.reject(sequence, "unavailable");
      return;
    }
    pending.decision = decision;
    this.callbacks.onAccepted(pending.generation);
    this.flush();
  }

  private reject(
    sequence: number,
    reason: SpeakerRejection,
    decision?: VerifyResponse,
  ): void {
    if (!this.pending || this.pending.sequence !== sequence) return;
    this.pending = undefined;
    this.callbacks.onRejected(reason, decision);
  }

  private flush(): void {
    const pending = this.pending;
    const token = pending?.decision?.speaker_token;
    if (!pending?.pcm || !pending.decision?.accepted || !token) return;
    this.pending = undefined;
    this.callbacks.onUtterance({
      generation: pending.generation,
      token,
      pcm: pending.pcm,
    });
  }
}

async function responseMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : fallback;
  } catch {
    return fallback;
  }
}
