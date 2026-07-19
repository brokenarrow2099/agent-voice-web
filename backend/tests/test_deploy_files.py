from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
GPU_UUID = "0"
CUSTOM_MODEL = "/home/agentvoice/Qwen3-TTS-12Hz-0.6B-CustomVoice"
VLLM_VENV = "/home/agentvoice/.qwen3tts-vllm-env"
OMNI_COMMIT = "d4a869fe5e2edd49af48026051948c8d1018d727"


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_tts_unit_serves_custom_voice_with_vllm_omni_on_5060():
    unit = read("deploy/qwen3-tts.service")

    assert f"Environment=CUDA_VISIBLE_DEVICES={GPU_UUID}" in unit
    assert "Environment=VLLM_USE_FLASHINFER_SAMPLER=0" in unit
    assert "Environment=HF_HUB_OFFLINE=1" in unit
    assert "Environment=TRANSFORMERS_OFFLINE=1" in unit
    assert f"{VLLM_VENV}/bin/vllm-omni serve {CUSTOM_MODEL}" in unit
    assert "--host 127.0.0.1" in unit and "--port 8766" in unit
    assert "--omni" in unit
    assert "--deploy-config" in unit and "vllm_omni/deploy/qwen3_tts.yaml" in unit
    assert "benchmark-qwen3-tts.py" in unit and "--warmup-only" in unit
    assert "Restart=on-failure" in unit
    assert "4090" not in unit
    assert ".qwen3tts-env/bin" not in unit
    assert f"{VLLM_VENV}/bin/vllm serve" not in unit
    assert "0.6B-Base" not in unit
    assert "reference.wav" not in unit


def test_gateway_units_use_https_and_mode_0600_secret_file():
    gateway = read("deploy/claude-voice.service")
    assert "--host 0.0.0.0" in gateway and "--port 8443" in gateway
    assert "--ssl-certfile" in gateway and "--ssl-keyfile" in gateway
    assert "--no-access-log" in gateway
    assert "EnvironmentFile=%h/.config/claude-voice/voice.env" in gateway
    assert f"Environment=CUDA_VISIBLE_DEVICES={GPU_UUID}" in gateway
    assert "Environment=VOICE_ASR_DEVICE=cuda" in gateway
    assert "Environment=VOICE_ASR_DEVICE_INDEX=0" in gateway
    assert "Environment=VOICE_ASR_COMPUTE_TYPE=float16" in gateway
    assert "After=qwen3-tts.service" in gateway
    bootstrap = read("deploy/claude-voice-bootstrap.service")
    assert "--port 8088" in bootstrap
    installer = read("scripts/install-user-services.sh")
    assert "umask 077" in installer
    assert 'chmod 600 "$VOICE_ENV"' in installer
    assert "systemctl --user enable qwen3-tts.service" in installer
    assert "systemctl --user restart qwen3-tts.service" in installer
    assert "systemctl --user restart claude-voice.service claude-voice-bootstrap.service" in installer
    assert "--rotate-pairing" in installer


def test_frontend_uses_agent_voice_brand_without_idle_headline():
    app = read("frontend/src/App.tsx")
    index = read("frontend/index.html")
    manifest = json.loads(read("frontend/public/manifest.webmanifest"))

    assert "AGENT VOICE" in app
    assert ">A</span>" in app
    assert "Agent 正在思考" in app
    assert "Agent 正在操作" in app
    assert ">Agent</span>" in app
    assert "CLAUDE VOICE" not in app
    assert "Claude 正在" not in app
    assert ">Claude</span>" not in app
    assert "把想法说出来" not in app
    assert "<title>Agent Voice</title>" in index
    assert manifest["name"] == "Agent Voice"
    assert manifest["short_name"] == "Agent Voice"
    assert manifest["description"] == "与本地 Agent 即时语音对话"


def test_installer_validates_only_new_vllm_custom_voice_runtime():
    script = read("scripts/install-user-services.sh")

    assert 'QWEN_ENV="$HOME/.qwen3tts-vllm-env"' in script
    assert 'QWEN_MODEL="$HOME/Qwen3-TTS-12Hz-0.6B-CustomVoice"' in script
    assert '[[ -x "$QWEN_ENV/bin/python" ]]' in script
    assert '[[ -x "$QWEN_ENV/bin/vllm-omni" ]]' in script
    assert '[[ -s "$QWEN_MODEL/config.json" ]]' in script
    assert "*.safetensors" in script
    assert "import torch, vllm, vllm_omni" in script
    assert 'vllm.__version__ == "0.24.0"' in script
    assert "pip install" not in script
    assert ".qwen3tts-env/bin" not in script
    assert "0.6B-Base" not in script
    assert "reference.wav" not in script


def test_preparation_script_pins_new_vllm_environment_and_custom_voice_model():
    script = read("scripts/install-qwen3-tts-vllm.sh")

    assert 'VLLM_VERSION="0.24.0"' in script
    assert f'VLLM_OMNI_COMMIT="{OMNI_COMMIT}"' in script
    assert 'MODELSCOPE_VERSION="1.38.1"' in script
    assert '"$UV" venv --python 3.12' in script
    assert "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice" in script
    assert '"$VLLM_ENV/bin/modelscope" download' in script
    assert ".qwen3tts-vllm-env" in script
    assert "Qwen3-TTS-12Hz-0.6B-CustomVoice" in script
    assert ".qwen3tts-env/bin" not in script
    assert "0.6B-Base" not in script
    assert "reference.wav" not in script
    assert "import torch, vllm, vllm_omni" in script
    assert 'vllm.__version__ == "0.24.0"' in script
    assert "*.safetensors" in script


def test_certificate_script_has_all_lan_subject_alt_names_and_key_modes(tmp_path):
    script = read("scripts/generate-lan-certs.sh")
    assert 'chmod 600 "$CA_KEY" "$SERVER_KEY"' in script
    env = os.environ | {
        "VOICE_CERT_DIR": str(tmp_path),
        "VOICE_LAN_IP": "192.0.2.10",
        "VOICE_WG_IP": "10.0.0.2",
        "VOICE_LAN_HOSTNAME": "voice-test-host",
    }
    subprocess.run([str(ROOT / "scripts/generate-lan-certs.sh")], env=env, check=True, capture_output=True)
    details = subprocess.run(
        ["openssl", "x509", "-in", str(tmp_path / "server.crt"), "-noout", "-ext", "subjectAltName"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "IP Address:192.0.2.10" in details
    assert "IP Address:10.0.0.2" in details
    assert "DNS:voice-test-host" in details
    assert "DNS:voice-test-host.local" in details


def test_real_websocket_verifier_obtains_speaker_authorization():
    script = read("scripts/verify-websocket-turn.py")
    assert "/api/speaker/verify" in script
    assert "speaker_token" in script


def test_speaker_service_is_cpu_only_loopback_and_precedes_gateway():
    speaker = read("deploy/speaker-verifier.service")
    gateway = read("deploy/claude-voice.service")
    installer = read("scripts/install-user-services.sh")

    assert 'Environment="CUDA_VISIBLE_DEVICES="' in speaker
    assert "/home/agentvoice/.asg-env/bin/uvicorn speaker_service.server:app" in speaker
    assert "--host 127.0.0.1 --port 8767" in speaker
    assert "After=speaker-verifier.service" in gateway
    assert "speaker-verifier.service" in installer
    assert "curl --silent --fail http://127.0.0.1:8767/health" in installer
    assert "nginx" not in speaker.lower()
