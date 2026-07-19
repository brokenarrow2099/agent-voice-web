export type TTSVoice =
  | "serena"
  | "vivian"
  | "uncle_fu"
  | "dylan"
  | "eric"
  | "ryan"
  | "aiden"
  | "ono_anna"
  | "sohee";

export interface VoicePreset {
  id: TTSVoice;
  name: string;
  language: "中文" | "英文" | "日文" | "韩文";
  gender: "女声" | "男声";
  character: string;
}

export const DEFAULT_TTS_VOICE: TTSVoice = "serena";
export const VOICE_STORAGE_KEY = "claude-voice.tts-voice";

export const TTS_VOICES: readonly VoicePreset[] = [
  { id: "serena", name: "Serena", language: "中文", gender: "女声", character: "温暖自然" },
  { id: "vivian", name: "Vivian", language: "中文", gender: "女声", character: "明亮年轻" },
  { id: "uncle_fu", name: "Uncle Fu", language: "中文", gender: "男声", character: "沉稳醇厚" },
  { id: "dylan", name: "Dylan", language: "中文", gender: "男声", character: "北京口音" },
  { id: "eric", name: "Eric", language: "中文", gender: "男声", character: "成都口音" },
  { id: "ryan", name: "Ryan", language: "英文", gender: "男声", character: "自然从容" },
  { id: "aiden", name: "Aiden", language: "英文", gender: "男声", character: "清晰年轻" },
  { id: "ono_anna", name: "Ono Anna", language: "日文", gender: "女声", character: "柔和清澈" },
  { id: "sohee", name: "Sohee", language: "韩文", gender: "女声", character: "轻快自然" },
];

const OFFICIAL_VOICES = new Set<TTSVoice>(TTS_VOICES.map((voice) => voice.id));

export function isTtsVoice(value: string): value is TTSVoice {
  return OFFICIAL_VOICES.has(value as TTSVoice);
}

export function readStoredVoice(storage: Pick<Storage, "getItem">): TTSVoice {
  const voice = storage.getItem(VOICE_STORAGE_KEY);
  return voice && isTtsVoice(voice) ? voice : DEFAULT_TTS_VOICE;
}

export function writeStoredVoice(
  storage: Pick<Storage, "setItem">,
  voice: TTSVoice,
): void {
  storage.setItem(VOICE_STORAGE_KEY, voice);
}
