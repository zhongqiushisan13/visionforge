"""本地 API 的三道锁：token 鉴权、Host 校验、CORS 收紧。

为什么现在才加：服务只监听 127.0.0.1，原本默认「本机=可信」。但浏览器里
任何一个网页都能向 127.0.0.1:8765 发请求（响应读不到，副作用拦不住），
配 DNS rebinding 连读都拦不住；而起任务是会花真钱的（MiniMax 按条计费）。

三道锁分别是：
1. **token**：启动时生成随机 token 写进 `run/token`（仅当前用户可读），
   前端页面由本服务注入 token，跨源页面拿不到，请求全部带 `X-VF-Token`；
2. **Host 校验**：只认 127.0.0.1 / localhost / [::1]，掐死 DNS rebinding；
3. **不启用 CORS**：前后端同源，根本不需要跨源许可。

`VF_ALLOW_UNSAFE_API=1` 可以整体关掉（自动化测试用），生产环境别开。
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import APP_ROOT, tighten_file_permissions

# 无需 token 的接口：只读探活，不泄任何项目信息
PUBLIC_PATHS = {"/api/health"}
TOKEN_COOKIE = "vf_token"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "localhost.localdomain"}


def run_dir() -> Path:
    return APP_ROOT / "run"


def token_path() -> Path:
    return run_dir() / "token"


def unsafe_mode() -> bool:
    return os.environ.get("VF_ALLOW_UNSAFE_API", "") == "1"


def load_or_create_token() -> str:
    """读取 run/token；没有就生成一个（随机 192 bit）。"""
    path = token_path()
    try:
        if path.exists():
            tok = path.read_text(encoding="utf-8").strip()
            if tok:
                return tok
        tok = secrets.token_urlsafe(24)
        run_dir().mkdir(parents=True, exist_ok=True)
        # Windows 上 chmod 基本是摆设，但至少不挡；POSIX 上是实打实的 600
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(tok)
        try:
            tighten_file_permissions(path)
        except OSError:
            pass
        return tok
    except OSError:
        # run/ 写不进（比如只读介质）——退化为每次启动一个内存 token，
        # 本进程内前后端仍配对，只是重启后浏览器要刷新
        return secrets.token_urlsafe(24)


def host_ok(host: str) -> bool:
    """Host 头只认本机回环名，防 DNS rebinding。"""
    h = (host or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):                      # [::1]:8765
        h = h.split("]", 1)[0] + "]"
    elif ":" in h:                             # 127.0.0.1:8765
        h = h.rsplit(":", 1)[0]
    return h in ALLOWED_HOSTS


def _extract_token(headers) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-vf-token", "")


class GuardMiddleware(BaseHTTPMiddleware):
    """Host 校验（全站）+ token 校验（/api/*，公开路径除外）。"""

    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request, call_next):
        if not unsafe_mode():
            if not host_ok(request.headers.get("host", "")):
                return JSONResponse({"detail": "非法 Host（只允许本机回环访问）"},
                                    status_code=400)
            path = request.url.path
            if path.startswith("/api/") and path not in PUBLIC_PATHS:
                tok = _extract_token(request.headers)
                if not tok or not secrets.compare_digest(tok, self.token):
                    return JSONResponse({"detail": "缺少或错误的 X-VF-Token"}, status_code=401)
            elif path.startswith("/media/"):
                # <video>/<img> 不能带自定义头；同源页面由 index 种 SameSite=Strict Cookie。
                tok = request.cookies.get(TOKEN_COOKIE, "") or request.query_params.get("token", "")
                if not tok or not secrets.compare_digest(tok, self.token):
                    return JSONResponse({"detail": "媒体访问未授权"}, status_code=401)
        return await call_next(request)


def install(app, token: str) -> None:
    app.add_middleware(GuardMiddleware, token=token)


def inject_token(html: str, token: str) -> str:
    """把 token 注入给同源的前端页面（跨源页面拿不到这份 HTML）。"""
    snippet = "<script>window.VF_TOKEN=%s;</script>" % json.dumps(token)
    if "</head>" in html:
        return html.replace("</head>", snippet + "</head>", 1)
    return snippet + html
