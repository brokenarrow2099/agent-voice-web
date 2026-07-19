import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { SpeakerThreshold } from "./SpeakerThreshold";

describe("SpeakerThreshold", () => {
  it("renders the confirmed percentage and bounded one-percent slider", () => {
    const html = renderToStaticMarkup(
      createElement(SpeakerThreshold, {
        settings: { threshold: 0.30, minimum: 0.30, maximum: 0.80 },
        saving: false,
        onChange: () => undefined,
      }),
    );
    expect(html).toContain("声纹匹配");
    expect(html).toContain("30%");
    expect(html).toContain('min="0.3"');
    expect(html).toContain('max="0.8"');
    expect(html).toContain('step="0.01"');
  });

  it("uses only a short warning below fifty-five percent", () => {
    const html = renderToStaticMarkup(
      createElement(SpeakerThreshold, {
        settings: { threshold: 0.52, minimum: 0.30, maximum: 0.80 },
        saving: false,
        onChange: () => undefined,
      }),
    );
    expect(html).toContain("较宽松");
  });
});
