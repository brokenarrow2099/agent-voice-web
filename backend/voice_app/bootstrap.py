from __future__ import annotations

from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from voice_app.config import get_settings


settings = get_settings()
app = FastAPI(title="Claude Voice Bootstrap", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
async def instructions() -> str:
    target = f"https://{settings.lan_ip}:{settings.https_port}/"
    return f"""<!doctype html><html lang=\"zh-CN\"><meta name=\"viewport\" content=\"width=device-width\">
<title>安装 Claude Voice 证书</title><style>
body{{font-family:-apple-system,'PingFang SC',sans-serif;max-width:620px;margin:0 auto;padding:40px 24px;background:#080b1a;color:#edf8ff;line-height:1.7}}
a{{color:#8deef5}}code{{background:#151d36;padding:3px 6px;border-radius:6px}}li{{margin:12px 0}}
</style><h1>Claude Voice</h1><p>首次在 iPhone 使用，请完成一次本地证书信任：</p><ol>
<li><a href=\"/ca.crt\">下载本地 CA 证书</a>并允许安装描述文件。</li>
<li>打开“设置 → 通用 → VPN 与设备管理”，安装 <code>Claude Voice Local CA</code>。</li>
<li>打开“设置 → 通用 → 关于本机 → 证书信任设置”，为该 CA 开启完全信任。</li>
<li><a href=\"{escape(target)}\">打开安全语音应用</a>，再使用管理员提供的配对链接。</li></ol></html>"""


@app.get("/ca.crt")
async def ca_certificate() -> FileResponse:
    if not settings.ca_cert_path.is_file():
        raise HTTPException(status_code=503, detail="证书尚未生成")
    return FileResponse(
        settings.ca_cert_path,
        media_type="application/x-x509-ca-cert",
        filename="claude-voice-local-ca.crt",
    )


@app.get("/open")
async def open_secure_app() -> RedirectResponse:
    return RedirectResponse(f"https://{settings.lan_ip}:{settings.https_port}/", status_code=307)
