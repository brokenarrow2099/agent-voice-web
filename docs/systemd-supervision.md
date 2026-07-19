# Agent Voice systemd 守护

Agent Voice 的本地链路由彼此独立的 user systemd 单元运行，统一入口是
`agent-voice.target`。SGLang 仍只由现有的 `sglang-qwen36.service` 管理；聚合
target 和 watchdog 不会创建第二个模型进程。

## 日常检查

启动或查看整个服务组：

```bash
systemctl --user start agent-voice.target
systemctl --user status agent-voice.target
systemctl --user status agent-voice-watchdog.timer
```

查看周期巡检日志：

```bash
journalctl --user -u agent-voice-watchdog.service -f
```

单个服务仍可独立检查或重启，例如：

```bash
systemctl --user status sglang-qwen36.service
systemctl --user restart qwen3-tts.service
```

`systemctl --user stop agent-voice.target` 不会停止成员服务，也不会传播重启。
这是刻意设计的保护，避免网关维护连带卸载常驻 GPU 模型。

## 健康巡检

watchdog 每分钟检查以下本机边界：

- SGLang：`127.0.0.1:8060/health`
- SearXNG：`127.0.0.1:8081/healthz`
- sing-box：TCP `127.0.0.1:10809`
- Qwen3-TTS：`127.0.0.1:8766/health`
- 声纹节点：`127.0.0.1:8767/health`
- 语音网关：`127.0.0.1:8443/health/ready`

单个组件连续失败两次后，只重启对应的 user unit。单元处于 `activating` 时
不累计失败；一次恢复后有 600 秒冷却期。状态位于
`~/.local/state/claude-voice/watchdog.json`，只包含失败次数和最近恢复时间。
日志只记录组件、状态、失败次数、动作和延迟，不记录响应正文、对话或凭据。

## SearXNG

`searxng.service` 调用 `scripts/manage-searxng.sh`，默认使用
`~/searxng/docker-compose.yml`。脚本只执行 `up -d searxng` 或
`stop searxng`，不会停止同一 Compose 项目中的 Open WebUI，也不会删除容器、
卷或配置。可通过 `SEARXNG_COMPOSE_FILE` 指向其他 Compose 文件。

## 暂停自动恢复与回滚

只暂停 watchdog，不影响任何业务进程：

```bash
systemctl --user disable --now agent-voice-watchdog.timer
```

同时取消统一 target 的开机入口：

```bash
systemctl --user disable agent-voice.target
```

现有成员单元仍保持原来的 enabled 和 `Restart=` 策略。重新启用时执行：

```bash
systemctl --user enable --now agent-voice.target agent-voice-watchdog.timer
```
