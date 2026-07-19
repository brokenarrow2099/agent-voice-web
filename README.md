# Agent Voice Web

一个面向本地 AI Agent 的实时语音 Web 应用。手机通过 HTTPS PWA 采集语音，本机完成语音识别、Agent 推理、声纹验证和流式语音合成。

这个项目默认使用：

- Faster-Whisper 进行本地 ASR，可选择 CPU 或 CUDA。
- Claude Code CLI 作为 Agent 运行器，但模型请求指向本地 SGLang（默认 `http://127.0.0.1:8060`），不依赖 Anthropic 托管模型 API。
- Qwen3-TTS 0.6B CustomVoice + vLLM-Omni 提供流式 TTS，支持 9 种预设音色且不需要 reference 音频。
- CAMPPlus 进行本地声纹验证，降低旁人声音误触发。
- SearXNG 为不支持内置 WebSearch 的本地模型提供联网搜索。

## 功能

- iPhone、Android 和桌面浏览器均可使用，可安装为 PWA。
- 连续对话、流式文本、流式 PCM 播放和语音打断。
- 每台设备独立保存声纹阈值和音色偏好。
- Agent 长时间无文字输出时播放柔和的状态提示音。
- 屏幕保留完整 Markdown；TTS 跳过代码、工具日志、URL 和 Markdown 表格，并清理不适合朗读的符号。
- 前后端提供逐环节延迟指标，便于定位 ASR、模型和 TTS 延迟。
- 配对令牌、签名 Cookie、请求限流和可选 Nginx HTTPS 入口。

## 架构

```text
手机浏览器 / PWA
       │ HTTPS + WebSocket
       ▼
Agent Voice 网关 :8443
  ├─ Faster-Whisper ASR
  ├─ CAMPPlus 声纹服务 :8767
  ├─ Claude Code CLI → 本地 SGLang :8060
  ├─ Qwen3-TTS :8766
  └─ SearXNG :8081（可选）
```

除 Web 网关外，模型、TTS、声纹和搜索服务都应只监听 `127.0.0.1`。本仓库没有在线演示，也不包含任何实际部署地址。

## 环境要求

- Linux 与 systemd user services
- Python 3.12 和 [uv](https://docs.astral.sh/uv/)
- Node.js 与 npm
- NVIDIA GPU（推荐用于 ASR 和 Qwen3-TTS）
- 已配置为访问本地 SGLang 的 Claude Code CLI
- Qwen3-TTS CustomVoice 模型

部署模板中的 `/home/agentvoice`、GPU 编号 `0`、`192.0.2.10`、`voice.example.com` 和 DNSPod 域名 ID 都是示例值，使用前必须按自己的环境调整。`192.0.2.0/24` 是文档专用地址段，不是可直接访问的局域网地址。

## 安装

先准备 Qwen3-TTS vLLM-Omni 环境和模型：

```bash
./scripts/install-qwen3-tts-vllm.sh
```

复制并填写运行配置：

```bash
mkdir -p ~/.config/claude-voice
cp .env.example ~/.config/claude-voice/voice.env
chmod 600 ~/.config/claude-voice/voice.env
```

至少应设置随机配对令牌，并通过 `VOICE_*` 环境变量覆盖本机模型路径、Claude CLI 路径、LAN 地址和 GPU 设置。随后安装服务：

```bash
./scripts/install-user-services.sh
```

如果只在局域网使用，参见 [iPhone 局域网安装说明](docs/iphone-lan-setup.md)。如需自行配置公网 HTTPS，参见 [公网访问运行手册](docs/public-access-runbook.md)；文档中的域名和地址全部是示例值。

## 配置重点

配置由 `backend/voice_app/config.py` 中的 Pydantic Settings 管理，环境变量统一使用 `VOICE_` 前缀。常用配置包括：

```dotenv
VOICE_PAIRING_TOKEN=generate-a-long-random-token
VOICE_COOKIE_SECRET=generate-an-independent-random-secret
ANTHROPIC_API_KEY=local-sglang-key
VOICE_SGLANG_URL=http://127.0.0.1:8060
VOICE_TTS_URL=http://127.0.0.1:8766
VOICE_SPEAKER_URL=http://127.0.0.1:8767
VOICE_SEARXNG_URL=http://127.0.0.1:8081
VOICE_ASR_DEVICE=cuda
VOICE_ASR_COMPUTE_TYPE=float16
```

这里的 `ANTHROPIC_API_KEY` 只是 Claude Code CLI 连接兼容接口时所需的本地占位值；本项目不会把它当作 Anthropic 官方 API 凭据。

## 安全建议

- 不要提交 `.env`、`dnspod.env`、证书私钥、配对 URL、声纹档案或会话数据库。
- 不要直接暴露 `8060`、`8766`、`8767`、`8081`、`8088` 或 `8443`；公网只应暴露带 TLS、认证和限流的反向代理入口。
- 配对完成后妥善保存 Cookie；泄露时执行 `./scripts/install-user-services.sh --rotate-pairing`。
- 声纹验证只能减少环境误触发，不是活体检测，也不能抵御录音回放或语音合成攻击。

## 开发与验证

```bash
uv sync --extra dev
uv run pytest
uv run ruff check backend scripts
npm ci --prefix frontend
npm test --prefix frontend -- --run
npm run typecheck --prefix frontend
npm run build --prefix frontend
bash -n scripts/*.sh
```

## License

[Apache License 2.0](LICENSE)
