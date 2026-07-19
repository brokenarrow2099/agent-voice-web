# 本地联网搜索

Agent 不使用 Anthropic 的 `WebSearch` / `WebFetch`。Claude CLI 在需要联网信息时通过 Bash 调用 `scripts/local-web.py`，后端是只监听 `127.0.0.1:8081` 的本机 SearXNG。

## 网络链路

SearXNG 容器经 Docker bridge 地址 `172.17.0.1:10809` 连接 sing-box。sing-box 继续使用现有规则：国内域名直连，其他请求走 VPN。Docker 入站只绑定 bridge 地址，SearXNG API 只绑定本机回环；两者都不会经 Nginx 暴露到公网。

当前经过逐引擎实测后只启用百度、必应、DuckDuckGo。Brave 会限流，搜狗会触发验证码，Google 和 Wikipedia 在当前出口返回空，因此没有放进默认聚合，避免拖慢搜索。

## 验证

```bash
systemctl --user status sing-box
docker compose -f ~/searxng/docker-compose.yml ps searxng
curl -fsS http://127.0.0.1:8081/config >/dev/null
.venv/bin/python scripts/local-web.py search "SGLang latest release" --limit 3
.venv/bin/python scripts/local-web.py fetch "https://docs.sglang.ai/" --max-chars 1000
```

可复现配置在 `deploy/searxng/`。部署到新机器时，先把
`settings.example.yml` 复制到仓库外并将 `ultrasecretkey` 替换为
`openssl rand -hex 32` 生成的独立值，再通过 `SEARXNG_SETTINGS_FILE` 指向该文件；
不要把生成后的配置提交到 Git。还需要把 `sing-box-docker-inbound.json` 中的
入站对象合并进现有 sing-box `inbounds`，不要覆盖已有 VPN 出站和路由规则。
如果 VPN 健康检查会自动重写 `config.json`，同一个 Docker 入站也必须加入它的
配置生成函数，否则下一次自愈会让搜索再次断线。

`fetch` 只允许公网 HTTP(S) 目标，会在每次跳转前重新解析并拒绝私网、回环、链路本地和保留地址，正文和下载大小也有上限。网页正文应始终被视为不可信数据。
