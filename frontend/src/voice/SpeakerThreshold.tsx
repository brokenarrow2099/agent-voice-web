import type { SpeakerSettings } from "./speaker";

interface SpeakerThresholdProps {
  settings: SpeakerSettings;
  saving: boolean;
  disabled?: boolean;
  onChange: (threshold: number) => void;
}

export function SpeakerThreshold({
  settings,
  saving,
  disabled = false,
  onChange,
}: SpeakerThresholdProps) {
  const percent = Math.round(settings.threshold * 100);
  return (
    <label className="speaker-threshold">
      <span className="speaker-threshold-head">
        <span>声纹匹配</span>
        <output>{percent}%</output>
      </span>
      <input
        aria-label="声纹匹配阈值"
        type="range"
        min={settings.minimum}
        max={settings.maximum}
        step={0.01}
        value={settings.threshold}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
      <span className="speaker-threshold-note" aria-live="polite">
        {saving ? "保存中" : settings.threshold < 0.55 ? "较宽松" : ""}
      </span>
    </label>
  );
}
