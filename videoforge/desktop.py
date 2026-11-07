"""桌面启动器：本地起服务 → 开一个 WebView2 窗口，没装运行时退化成浏览器。

打包成 exe 后，这里同时负责三件事：日志落盘、崩溃兜底提示、CLI 参数解析。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import Optional

import requests

from .config import APP_ROOT

WINDOW_TITLE = "VisionForge · AI 视频编导台"
LOG_DIR = APP_ROOT / "logs"


# --------------------------------------------------------------------------- #
# 日志与兜底
# --------------------------------------------------------------------------- #
def setup_logging() -> Path:
    """窗口模式没有控制台，所有 print 落到 logs/runtime.log。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "runtime.log"
    try:
        fh = open(log_path, "a", encoding="utf-8", buffering=1)
        fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start =====\n")
        if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
            sys.stdout = fh
            sys.stderr = fh
    except OSError:
        pass
    return log_path


def fatal(message: str) -> None:
    """把错误写到日志，并弹一个原生对话框（窗口模式下用户能看见）。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "startup-error.log", "a", encoding="utf-8") as fh:
            fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n{traceback.format_exc()}\n")
    except OSError:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"VisionForge 启动失败：\n\n{message[:600]}\n\n详细日志：{LOG_DIR / 'startup-error.log'}",
            "VisionForge", 0x10,
        )
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #
def find_port(preferred: int = 8765) -> int:
    for port in (preferred, 8766, 8767, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return int(s.getsockname()[1])
        except OSError:
            continue
    return 0


_free_port = find_port  # 兼容旧名字


def wait_ready(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url + "/api/bootstrap", timeout=1.5,
                             proxies={"http": None, "https": None})
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.25)
    return False


def serve(port: int) -> None:
    """直接传 app 对象，别用 'module:app' 字符串——打包后导入字符串是经典坑。"""
    import uvicorn

    from .web.server import create_app

    config = uvicorn.Config(
        create_app(), host="127.0.0.1", port=port,
        log_level="warning", access_log=False,
        loop="asyncio", http="h11", ws="none", server_header=False,
    )
    uvicorn.Server(config).run()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def run_gui(port: int, url: str) -> None:
    import webview

    icon = APP_ROOT / "assets" / "visionforge.ico"
    kwargs = dict(
        width=1480, height=940, min_size=(1180, 760),
        background_color="#04070f", text_select=False,
    )
    if icon.exists():
        kwargs["icon"] = str(icon)
    webview.create_window(WINDOW_TITLE, url, **kwargs)
    webview.start(debug=False)


def main(argv: Optional[list[str]] = None, port: Optional[int] = None,
         open_browser: bool = False) -> int:
    parser = argparse.ArgumentParser(prog="VisionForge", add_help=True)
    parser.add_argument("--port", type=int, default=0, help="固定端口（默认自动挑空闲端口）")
    parser.add_argument("--serve", action="store_true", help="只起本地服务，不开窗口（调试用）")
    parser.add_argument("--browser", action="store_true", help="用系统浏览器打开，不开桌面窗口")
    args = parser.parse_args(argv)

    log_path = setup_logging()
    chosen = port or args.port or find_port()
    url = f"http://127.0.0.1:{chosen}"

    try:
        print(f"[VisionForge] app_root={APP_ROOT} port={chosen} frozen={getattr(sys, 'frozen', False)}")
        # 首次运行：探测硬件、选编码器、定分辨率，别让用户先学会配环境
        try:
            from .sysprobe import auto_configure
            info = auto_configure()
            if not info.get("skipped"):
                print(f"[VisionForge] 首次自动配置完成：{info.get('applied')}")
        except Exception as exc:
            print(f"[VisionForge] 自动配置跳过：{exc}")

        threading.Thread(target=serve, args=(chosen,), daemon=True, name="vf-server").start()
        if not wait_ready(url):
            raise RuntimeError("本地服务未在规定时间内就绪，检查 logs/runtime.log")

        if args.serve:
            print(f"[VisionForge] serving at {url} (Ctrl+C to stop)")
            while True:
                time.sleep(3600)

        if args.browser or open_browser:
            print(f"[VisionForge] 浏览器模式 {url}")
            webbrowser.open(url)
            while True:
                time.sleep(3600)

        try:
            print(f"[VisionForge] 桌面窗口 {url}")
            run_gui(chosen, url)
        except Exception as exc:
            print(f"[VisionForge] 桌面壳不可用（{exc}），改用浏览器打开 {url}")
            webbrowser.open(url)
            while True:
                time.sleep(3600)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"[VisionForge] fatal: {exc}\n{traceback.format_exc()}")
        fatal(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
