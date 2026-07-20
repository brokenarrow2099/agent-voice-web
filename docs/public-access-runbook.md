# Claude Voice 公网访问运行手册

本方案把 `https://voice.example.com` 直接发布到家庭动态公网 IPv4。DNSPod DDNS 在本机每两分钟校正一次 A 记录；Nginx 只监听 IPv4 TCP 443，并把 HTTPS/WSS 转发到现有 `https://127.0.0.1:8443`。`8060`、`8766`、`8088` 和 `8443` 均不得做公网映射。

本方案不使用VPS 或任何中继。只产生域名续费和现有家庭网络成本。

## 1. 安装前条件

- 本地 `qwen3-tts`、`claude-voice`、`claude-voice-bootstrap` 三个用户服务健康。
- DNSPod CAM 策略只允许计划中列出的 6 个动作，并限定到 DomainId `12345678`。
- 光猫获得真实但动态的公网 IPv4；本机地址固定为 `192.0.2.10`，WAN 地址固定为 `192.0.2.2`。
- 配对 Cookie 保持 Secure、HttpOnly、SameSite=Strict；配对链接不得进入聊天记录或日志。

先显式安装三个系统包；项目安装器不会代替你运行包管理器：

```bash
sudo apt-get update
sudo apt-get install --yes nginx certbot dnsutils
```

## 2. 本机部署顺序

所有命令都从 `/home/agentvoice/agent-voice-web` 执行。凭据脚本使用隐藏输入，不会从 `.bashrc` 复制或显示密钥。

```bash
./scripts/configure-dnspod-credentials.sh
./scripts/install-public-access.sh --preflight
./scripts/install-public-access.sh --install-ddns
sudo ./scripts/install-public-access.sh --issue-staging
sudo ./scripts/install-public-access.sh --issue-production
sudo ./scripts/install-public-access.sh --install-nginx
```

证书流程先通过 Let's Encrypt staging DNS-01，再签发 production。生产证书验证成功后，安装器会删除仅用于首次证明的 staging 证书 lineage，避免两条续期任务连续改写同一个 `_acme-challenge` TXT 名称。Nginx 每次 reload 前都必须通过 `nginx -t`。在路由器映射尚未开启时，先验证本机入口：

```bash
curl --noproxy '*' --resolve voice.example.com:443:127.0.0.1 -I https://voice.example.com/
openssl s_client -connect 127.0.0.1:443 -servername voice.example.com -verify_return_error </dev/null
```

未配对的 `/` 返回 401 是预期结果；TLS 校验必须返回 code 0。

## 3. 最后开启两级 TCP 443 映射

只有本机 TLS、HTTPS 和 WSS 验证完成后才配置：

1. WAN TCP `443` → `192.0.2.10:443`。
2. 光猫：公网 TCP `443` → WAN `192.0.2.2:443`。

不要开启 UDP 443、端口 80、DMZ、IPv6 入站、远程路由器管理，或对 `8088`、`8443`、`8060`、`8766` 的映射。

关闭 iPhone Wi-Fi，使用蜂窝网络打开 `https://voice.example.com`。先完成配对，再验证麦克风、连续对话、打断、工具操作不朗读和 Qwen3-TTS 返回。配对令牌不要放进命令行、截图或日志。

## 4. 日常状态与续期

```bash
systemctl --user status claude-voice-ddns.timer
journalctl --user -u claude-voice-ddns.service
systemctl status nginx certbot.timer
sudo certbot renew --dry-run
```

DDNS 日志只应显示 `created`、`updated` 或 `unchanged`、公网 IP 和记录 ID，不应出现凭据或 Authorization。Nginx access log 已关闭，避免配对 URL 被记录。证书续期使用保存的 manual DNS hooks；deploy hook 先运行 `nginx -t` 再 reload。

## 5. DNSPod 凭据轮换

在 DNSPod 创建或更新同等最小权限的 CAM 凭据后，重新执行：

```bash
./scripts/configure-dnspod-credentials.sh
systemctl --user start claude-voice-ddns.service
journalctl --user -u claude-voice-ddns.service -n 20 --no-pager
```

确认新凭据成功后撤销旧凭据。`~/.config/claude-voice` 必须保持 0700，`dnspod.env` 必须保持 0600。

## 6. 回滚

回滚时先关闭两级路由器上的 TCP 443 端口映射，顺序为光猫和路由器；确认蜂窝网络已无法连接后，再执行：

```bash
systemctl --user disable --now claude-voice-ddns.timer
sudo rm /etc/nginx/conf.d/claude-voice.conf
sudo nginx -t
sudo systemctl reload nginx
```

这不会停止或修改 `claude-voice`、`qwen3-tts`、SGLang 和局域网证书引导服务。手机仍可通过 `https://192.0.2.10:8443` 使用原有局域网入口。

## 7. 故障定位

- 域名错误：用三个不继承代理的 HTTPS 来源重新确认公网 IPv4，再查询 DNSPod 权威 NS，而不是只看递归 DNS 缓存。
- TLS 错误：检查 production 证书 SAN 和有效期，然后运行 `sudo nginx -t`。
- 502：现有私有证书没有 `127.0.0.1` SAN，应使用证书中的本机名但把连接固定到 loopback：`curl --noproxy '*' --cacert ~/.config/claude-voice/certs/ca.crt --resolve "$(hostname):8443:127.0.0.1" "https://$(hostname):8443/health/ready"`。
- WebSocket 失败：检查 Nginx error log和 `journalctl --user -u claude-voice`，确认只有 TCP 443 被映射。
- 公网失败但局域网正常：检查两级 NAT 映射和运营商公网 IPv4 是否变化；不要临时开放更多端口。
