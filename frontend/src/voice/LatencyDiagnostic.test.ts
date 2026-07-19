import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LatencyDiagnostic, formatLatency } from "./LatencyDiagnostic";

describe("LatencyDiagnostic", () => {
  it("keeps the empty diagnostic quiet", () => {
    const html = renderToStaticMarkup(createElement(LatencyDiagnostic, {}));
    expect(html).toContain("首声");
    expect(html).toContain("—");
    expect(html).toContain('aria-expanded="false"');
  });

  it("renders only available stage values", () => {
    const html = renderToStaticMarkup(createElement(LatencyDiagnostic, {
      snapshot: {
        generation_id: 2,
        turn_id: 1,
        commit_to_first_audio_ms: 1840,
        asr_ms: 310,
        tts_first_audio_ms: 142,
      },
    }));
    expect(html).toContain("首声 1.8s");
    expect(html).toContain("语音识别");
    expect(html).toContain("TTS 首包");
    expect(html).not.toContain("模型首字");
  });

  it("formats instrument readings compactly", () => {
    expect(formatLatency(86.4)).toBe("86ms");
    expect(formatLatency(1840)).toBe("1.8s");
  });
});
