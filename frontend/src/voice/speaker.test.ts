import { describe, expect, it, vi } from "vitest";
import {
  SpeakerTurnController,
  fetchSpeakerSettings,
  saveSpeakerSettings,
  type VerifyResponse,
} from "./speaker";

const acceptedDecision: VerifyResponse = {
  accepted: true,
  speaker_token: "token",
  score: 0.82,
  threshold: 0.60,
  speaker_ms: 68,
  roundtrip_ms: 92,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("SpeakerTurnController", () => {
  it("does not accept or submit until verification resolves", async () => {
    const decision = deferred<VerifyResponse>();
    const onAccepted = vi.fn();
    const onUtterance = vi.fn();
    const controller = new SpeakerTurnController({
      verify: () => decision.promise,
      onAccepted,
      onUtterance,
      onRejected: vi.fn(),
    });

    controller.begin(4, new Uint8Array(32_000));
    controller.finish(new Uint8Array(64_000));
    expect(onAccepted).not.toHaveBeenCalled();
    expect(onUtterance).not.toHaveBeenCalled();

    decision.resolve(acceptedDecision);
    await decision.promise;
    await Promise.resolve();

    expect(onAccepted).toHaveBeenCalledWith(4);
    expect(onUtterance).toHaveBeenCalledWith({
      generation: 4,
      token: "token",
      pcm: expect.any(Uint8Array),
    });
  });

  it("rejection never calls accepted or utterance callbacks", async () => {
    const onAccepted = vi.fn();
    const onUtterance = vi.fn();
    const onRejected = vi.fn();
    const controller = new SpeakerTurnController({
      verify: async () => ({
        accepted: false,
        score: 0.53,
        threshold: 0.60,
        speaker_ms: 67,
        roundtrip_ms: 88,
      }),
      onAccepted,
      onUtterance,
      onRejected,
    });

    controller.begin(2, new Uint8Array(32_000));
    controller.finish(new Uint8Array(40_000));
    await Promise.resolve();
    await Promise.resolve();

    expect(onAccepted).not.toHaveBeenCalled();
    expect(onUtterance).not.toHaveBeenCalled();
    expect(onRejected).toHaveBeenCalledWith("rejected", expect.objectContaining({ score: 0.53 }));
  });

  it("ignores stale verification results after reset", async () => {
    const decision = deferred<VerifyResponse>();
    const onAccepted = vi.fn();
    const onUtterance = vi.fn();
    const onRejected = vi.fn();
    const controller = new SpeakerTurnController({
      verify: () => decision.promise,
      onAccepted,
      onUtterance,
      onRejected,
    });

    controller.begin(3, new Uint8Array(32_000));
    controller.finish(new Uint8Array(64_000));
    controller.reset();
    decision.resolve({ ...acceptedDecision, speaker_token: "stale-token" });
    await decision.promise;
    await Promise.resolve();

    expect(controller.hasPending()).toBe(false);
    expect(onAccepted).not.toHaveBeenCalled();
    expect(onUtterance).not.toHaveBeenCalled();
    expect(onRejected).not.toHaveBeenCalled();
  });
});

describe("speaker settings API", () => {
  it("loads and saves the paired phone threshold", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        threshold: 0.60, minimum: 0.30, maximum: 0.80,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        threshold: 0.67, minimum: 0.30, maximum: 0.80,
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await fetchSpeakerSettings()).threshold).toBe(0.60);
    expect((await saveSpeakerSettings(0.67)).threshold).toBe(0.67);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/speaker/settings", {
      credentials: "same-origin",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/speaker/settings", {
      method: "PUT",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ threshold: 0.67 }),
    });
    vi.unstubAllGlobals();
  });
});
