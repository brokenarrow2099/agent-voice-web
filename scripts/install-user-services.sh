#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SERVICE_DIR="$HOME/.config/systemd/user"
CONFIG_DIR="$HOME/.config/claude-voice"
STATE_DIR="$HOME/.local/state/claude-voice"
VOICE_ENV="$CONFIG_DIR/voice.env"
QWEN_ENV="$HOME/.qwen3tts-vllm-env"
QWEN_MODEL="$HOME/Qwen3-TTS-12Hz-0.6B-CustomVoice"
SPEAKER_ENV="$HOME/.asg-env"
SPEAKER_ROOT="$HOME/agent-service-gateway/index-tts"
SPEAKER_MODEL="$SPEAKER_ROOT/checkpoints/hf_cache/campplus_cn_common.bin"
SPEAKER_SOURCE="$SPEAKER_ROOT/indextts/s2mel/modules/campplus/DTDNN.py"
ROTATE_PAIRING=0

case "${1:-}" in
  "") ;;
  --rotate-pairing) ROTATE_PAIRING=1 ;;
  *) echo "Usage: $0 [--rotate-pairing]" >&2; exit 2 ;;
esac

mkdir -p "$SERVICE_DIR" "$CONFIG_DIR" "$STATE_DIR"
chmod 700 "$STATE_DIR"

if [[ ! -s "$VOICE_ENV" || "$ROTATE_PAIRING" -eq 1 ]]; then
  PAIR_TOKEN="$(openssl rand -hex 24)"
  COOKIE_SECRET="$(openssl rand -hex 48)"
  LOCAL_API_KEY="${ANTHROPIC_API_KEY:-local-sglang-key}"
  {
    printf 'VOICE_PAIRING_TOKEN=%s\n' "$PAIR_TOKEN"
    printf 'VOICE_COOKIE_SECRET=%s\n' "$COOKIE_SECRET"
    printf 'ANTHROPIC_API_KEY=%s\n' "$LOCAL_API_KEY"
    printf 'VOICE_FRONTEND_DIST=%s/frontend/dist\n' "$PROJECT_ROOT"
  } >"$VOICE_ENV"
fi
chmod 600 "$VOICE_ENV"

[[ -x "$QWEN_ENV/bin/python" ]] || {
  echo "Missing Qwen3-TTS vLLM environment: $QWEN_ENV" >&2
  exit 1
}
[[ -x "$QWEN_ENV/bin/vllm-omni" ]] || { echo "Missing vLLM-Omni executable: $QWEN_ENV/bin/vllm-omni" >&2; exit 1; }
[[ -s "$QWEN_MODEL/config.json" ]] || { echo "Missing Qwen3-TTS model config: $QWEN_MODEL" >&2; exit 1; }
find "$QWEN_MODEL" -type f -name '*.safetensors' -size +0c -print -quit | grep -q . || {
  echo "Missing Qwen3-TTS model safetensors: $QWEN_MODEL" >&2
  exit 1
}
"$QWEN_ENV/bin/python" -c 'import torch, vllm, vllm_omni; assert vllm.__version__ == "0.24.0"'

[[ -x "$SPEAKER_ENV/bin/python" ]] || {
  echo "Missing speaker verifier environment: $SPEAKER_ENV" >&2
  exit 1
}
[[ -x "$SPEAKER_ENV/bin/uvicorn" ]] || { echo "Missing speaker verifier uvicorn: $SPEAKER_ENV/bin/uvicorn" >&2; exit 1; }
[[ -s "$SPEAKER_MODEL" ]] || { echo "Missing CAMPPlus weights: $SPEAKER_MODEL" >&2; exit 1; }
[[ -s "$SPEAKER_SOURCE" ]] || { echo "Missing CAMPPlus source: $SPEAKER_SOURCE" >&2; exit 1; }
"$SPEAKER_ENV/bin/python" -c 'import torch, torchaudio, fastapi, uvicorn'

"$PROJECT_ROOT/scripts/generate-lan-certs.sh"
uv sync --project "$PROJECT_ROOT" --extra dev
npm ci --prefix "$PROJECT_ROOT/frontend"
npm run build --prefix "$PROJECT_ROOT/frontend"

install -m 0644 "$PROJECT_ROOT/deploy/qwen3-tts.service" "$SERVICE_DIR/qwen3-tts.service"
install -m 0644 "$PROJECT_ROOT/deploy/speaker-verifier.service" "$SERVICE_DIR/speaker-verifier.service"
install -m 0644 "$PROJECT_ROOT/deploy/claude-voice.service" "$SERVICE_DIR/claude-voice.service"
install -m 0644 "$PROJECT_ROOT/deploy/claude-voice-bootstrap.service" "$SERVICE_DIR/claude-voice-bootstrap.service"
install -m 0644 "$PROJECT_ROOT/deploy/agent-voice.target" "$SERVICE_DIR/agent-voice.target"
install -m 0644 "$PROJECT_ROOT/deploy/searxng.service" "$SERVICE_DIR/searxng.service"
install -m 0644 "$PROJECT_ROOT/deploy/agent-voice-watchdog.service" "$SERVICE_DIR/agent-voice-watchdog.service"
install -m 0644 "$PROJECT_ROOT/deploy/agent-voice-watchdog.timer" "$SERVICE_DIR/agent-voice-watchdog.timer"
chmod 0755 "$PROJECT_ROOT/scripts/agent-voice-watchdog.py" "$PROJECT_ROOT/scripts/manage-searxng.sh"

systemctl --user daemon-reload
systemd-analyze --user verify \
  "$SERVICE_DIR/qwen3-tts.service" \
  "$SERVICE_DIR/speaker-verifier.service" \
  "$SERVICE_DIR/claude-voice.service" \
  "$SERVICE_DIR/claude-voice-bootstrap.service" \
  "$SERVICE_DIR/agent-voice.target" \
  "$SERVICE_DIR/searxng.service" \
  "$SERVICE_DIR/agent-voice-watchdog.service" \
  "$SERVICE_DIR/agent-voice-watchdog.timer"
systemctl --user enable speaker-verifier.service
systemctl --user restart speaker-verifier.service

for _ in {1..30}; do
  curl --silent --fail http://127.0.0.1:8767/health >/dev/null && break
  sleep 2
done
curl --silent --fail http://127.0.0.1:8767/health >/dev/null || {
  echo "Speaker verifier did not become healthy; inspect: journalctl --user -u speaker-verifier" >&2
  exit 1
}

systemctl --user enable qwen3-tts.service
systemctl --user restart qwen3-tts.service

for _ in {1..180}; do
  curl --silent --fail http://127.0.0.1:8766/health >/dev/null && break
  sleep 5
done
curl --silent --fail http://127.0.0.1:8766/health >/dev/null || {
  echo "Qwen3-TTS did not become healthy; inspect: journalctl --user -u qwen3-tts" >&2
  exit 1
}

systemctl --user enable claude-voice.service claude-voice-bootstrap.service
systemctl --user restart claude-voice.service claude-voice-bootstrap.service
systemctl --user enable searxng.service agent-voice.target agent-voice-watchdog.timer
systemctl --user start agent-voice.target agent-voice-watchdog.timer
if ! loginctl show-user "$USER" -p Linger --value | grep -qx yes; then
  loginctl enable-linger "$USER" || echo "Enable linger manually for pre-login startup: loginctl enable-linger $USER" >&2
fi
echo "Voice services installed. Pairing token remains only in $VOICE_ENV"
