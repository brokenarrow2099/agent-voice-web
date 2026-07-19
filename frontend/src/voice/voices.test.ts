import { describe, expect, it } from "vitest";
import {
  DEFAULT_TTS_VOICE,
  TTS_VOICES,
  readStoredVoice,
  writeStoredVoice,
} from "./voices";

function memoryStorage(initial?: string) {
  let value = initial ?? null;
  return {
    getItem: () => value,
    setItem: (_key: string, next: string) => { value = next; },
  };
}

describe("TTS voice presets", () => {
  it("contains the nine official voices with Chinese male and female choices", () => {
    expect(TTS_VOICES).toHaveLength(9);
    expect(TTS_VOICES.filter((voice) => voice.language === "中文" && voice.gender === "女声").map((voice) => voice.id))
      .toEqual(["serena", "vivian"]);
    expect(TTS_VOICES.filter((voice) => voice.language === "中文" && voice.gender === "男声").map((voice) => voice.id))
      .toEqual(["uncle_fu", "dylan", "eric"]);
  });

  it("falls back to Serena when storage is missing or invalid", () => {
    expect(readStoredVoice(memoryStorage())).toBe(DEFAULT_TTS_VOICE);
    expect(readStoredVoice(memoryStorage("../../voice"))).toBe(DEFAULT_TTS_VOICE);
  });

  it("persists only an official voice id", () => {
    const storage = memoryStorage();
    writeStoredVoice(storage, "dylan");
    expect(readStoredVoice(storage)).toBe("dylan");
  });
});
