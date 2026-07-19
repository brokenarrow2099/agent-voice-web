# iPhone 局域网使用 Claude Voice

## 首次安装证书

1. 确保 iPhone 和主机连接同一个可信局域网。
2. 用 Safari 打开 `http://192.0.2.10:8088`。
3. 点击“下载本地 CA 证书”，允许下载描述文件。
4. 打开“设置 → 通用 → VPN 与设备管理”，安装 `Claude Voice Local CA`。
5. 打开“设置 → 通用 → 关于本机 → 证书信任设置”，为该 CA 开启完全信任。
6. 回到 Safari，打开 `https://192.0.2.10:8443`。页面不应再显示证书警告。

这个 CA 只用于本机生成的 Claude Voice 局域网证书。若不再使用，可从“VPN 与设备管理”中删除描述文件，并关闭对应的证书信任。

## 配对

在主机上读取 `~/.config/claude-voice/voice.env` 中的 `VOICE_PAIRING_TOKEN`，然后只在自己的 iPhone Safari 打开：

```text
https://192.0.2.10:8443/pair?token=<VOICE_PAIRING_TOKEN>
```

成功后服务器会设置 Secure、HttpOnly 配对 Cookie 并跳回主页。令牌不会进入 PWA 缓存；Cookie 默认有效 90 天。不要截图或转发完整配对链接。

## 添加到主屏幕

1. 在已成功配对的主页点击 Safari 分享按钮。
2. 选择“添加到主屏幕”。
3. 从主屏幕打开 Claude Voice，点击“开始对话”。
4. 首次使用时允许麦克风权限。

说完后短暂停顿即可提交。Claude 思考和使用工具时界面会显示状态；自然语言结果会显示并朗读。播放过程中直接说话会触发打断，旧回答音频会立即清空。“结束”会停止麦克风、取消当前任务并关闭连接。

## 故障排查

- Safari 提示连接不安全：确认描述文件已安装，并在“证书信任设置”中开启了完全信任。
- 页面显示未配对：重新打开配对 URL；令牌以主机当前 `voice.env` 为准。
- 无法使用麦克风：在“设置 → Safari → 麦克风”或站点设置中允许访问，并确认访问的是 HTTPS 地址。
- 页面无法打开：确认手机仍在同一局域网，并在主机执行 `systemctl --user status claude-voice claude-voice-bootstrap`。
- 没有语音：执行 `systemctl --user status qwen3-tts` 和 `journalctl --user -u qwen3-tts -n 100`。
- 模型回答不可用：检查本地 SGLang 的 `http://127.0.0.1:8060/health`，以及 `journalctl --user -u claude-voice -n 100`。
