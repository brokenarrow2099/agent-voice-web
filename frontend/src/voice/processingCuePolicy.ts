import type { VoiceEvent } from "./reducer";

export type ProcessingCueAction = "begin" | "stop" | "ignore";

export function processingCueActionForEvent(event: VoiceEvent): ProcessingCueAction {
  if (event.type === "tool.start") return "begin";
  if (event.type === "state") {
    return event.state === "thinking" || event.state === "tool" ? "begin" : "stop";
  }
  switch (event.type) {
    case "assistant.delta":
    case "assistant.final":
    case "audio.start":
    case "audio.end":
    case "turn.end":
    case "error":
    case "local.barge-in":
      return "stop";
    case "local.connection":
      return event.connected ? "ignore" : "stop";
    default:
      return "ignore";
  }
}
