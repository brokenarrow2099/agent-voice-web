#!/usr/bin/env bash
set -euo pipefail

VLLM_VERSION="0.24.0"
VLLM_OMNI_COMMIT="d4a869fe5e2edd49af48026051948c8d1018d727"
MODELSCOPE_VERSION="1.38.1"
VLLM_ENV="${QWEN3_TTS_VLLM_ENV:-$HOME/.qwen3tts-vllm-env}"
MODEL_DIR="${QWEN3_TTS_MODEL_DIR:-$HOME/Qwen3-TTS-12Hz-0.6B-CustomVoice}"
MODEL_ID="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"

UV="$(command -v uv)" || {
  echo "uv is required to prepare Qwen3-TTS" >&2
  exit 1
}

if [[ ! -x "$VLLM_ENV/bin/python" ]]; then
  "$UV" venv --python 3.12 --seed "$VLLM_ENV"
fi

"$UV" pip install \
  --python "$VLLM_ENV/bin/python" \
  --torch-backend=auto \
  "vllm==$VLLM_VERSION"
"$UV" pip install \
  --python "$VLLM_ENV/bin/python" \
  "git+https://github.com/vllm-project/vllm-omni.git@$VLLM_OMNI_COMMIT" \
  "modelscope==$MODELSCOPE_VERSION"

mkdir -p "$MODEL_DIR"
"$VLLM_ENV/bin/modelscope" download \
  --model "$MODEL_ID" \
  --local_dir "$MODEL_DIR"

"$VLLM_ENV/bin/python" - <<'PY'
from importlib.metadata import version

import torch, vllm, vllm_omni

assert vllm.__version__ == "0.24.0", vllm.__version__
assert version("vllm-omni") == "0.24.0", version("vllm-omni")
assert version("modelscope") == "1.38.1", version("modelscope")
print(
    "Pinned runtime versions:",
    f"torch={torch.__version__}",
    f"vllm={vllm.__version__}",
    f"vllm-omni={version('vllm-omni')}",
    f"modelscope={version('modelscope')}",
)
PY

[[ -s "$MODEL_DIR/config.json" ]] || {
  echo "CustomVoice model download is incomplete: $MODEL_DIR/config.json is missing" >&2
  exit 1
}
find "$MODEL_DIR" -type f -name '*.safetensors' -size +0c -print -quit | grep -q . || {
  echo "CustomVoice model download is incomplete: no non-empty safetensors found" >&2
  exit 1
}

echo "Qwen3-TTS vLLM-Omni environment is ready: $VLLM_ENV"
echo "CustomVoice model is ready: $MODEL_DIR"
