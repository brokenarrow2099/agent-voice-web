import { useState } from "react";
import type { LatencySnapshot } from "./latency";

export function formatLatency(milliseconds: number): string {
  return milliseconds < 1000
    ? `${Math.round(milliseconds)}ms`
    : `${(milliseconds / 1000).toFixed(1)}s`;
}

interface LatencyDiagnosticProps {
  snapshot?: LatencySnapshot;
}

const ROWS: readonly [keyof LatencySnapshot, string][] = [
  ["speaker_roundtrip_ms", "声纹往返"],
  ["asr_ms", "语音识别"],
  ["model_first_text_ms", "模型首字"],
  ["first_sentence_ms", "首句生成"],
  ["tts_first_audio_ms", "TTS 首包"],
  ["response_first_audio_ms", "后端首声"],
];

export function LatencyDiagnostic({ snapshot }: LatencyDiagnosticProps) {
  const [expanded, setExpanded] = useState(false);
  const primary = snapshot?.commit_to_first_audio_ms;
  const availableRows = ROWS.flatMap(([key, label]) => {
    const value = snapshot?.[key];
    return typeof value === "number" ? [{ key, label, value }] : [];
  });

  return (
    <div className={`latency-diagnostic${expanded ? " expanded" : ""}`}>
      <button
        type="button"
        className="latency-summary"
        aria-expanded={expanded}
        aria-controls="latency-detail"
        onClick={() => setExpanded((value) => !value)}
      >
        首声 {primary === undefined ? "—" : formatLatency(primary)}
      </button>
      <div id="latency-detail" className="latency-detail" hidden={!expanded}>
        <div className="latency-detail-head">
          <span>本轮响应</span>
          <strong>{primary === undefined ? "—" : formatLatency(primary)}</strong>
        </div>
        {availableRows.map(({ key, label, value }) => (
          <div className="latency-row" key={key}>
            <span>{label}</span><i aria-hidden="true" /><output>{formatLatency(value)}</output>
          </div>
        ))}
      </div>
    </div>
  );
}
