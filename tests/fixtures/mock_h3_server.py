"""MiniMax H3 兼容网关测试夹具。

测试里用线程内 HTTP 服务模拟「提交任务 → 异步轮询 → 返回视频 URL」，
不碰真实云端，不产生费用。
"""

from __future__ import annotations

import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional


def make_handler(video_path: Path, ready_after: float = 0.0):
    tasks: dict[str, float] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 测试输出保持安静
            return

        def _json(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - http.server 约定
            if not self.path.startswith("/v2/video_generation"):
                return self._json({"error": {"message": "not found"}}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": {"message": "bad json"}}, 400)
            if not any(item.get("type") == "text" for item in payload.get("content") or []):
                return self._json({"error": {"message": "text required", "http_code": 400}}, 400)
            task_id = uuid.uuid4().hex[:12]
            tasks[task_id] = time.time()
            return self._json({"task_id": task_id})

        def do_GET(self):  # noqa: N802 - http.server 约定
            if self.path.startswith("/v2/query/video_generation/"):
                task_id = self.path.rsplit("/", 1)[-1]
                if task_id not in tasks:
                    return self._json({"error": {"message": "no task"}}, 404)
                age = time.time() - tasks[task_id]
                status = "succeeded" if age >= ready_after else "running"
                task = {"status": status}
                if status == "succeeded":
                    task["content"] = {
                        "url": f"http://127.0.0.1:{self.server.server_port}/file.mp4"
                    }
                return self._json({"task": task})
            if self.path.startswith("/file.mp4"):
                data = video_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return None
            return self._json({"ok": True, "service": "mock-h3"})

    return Handler


class MockH3Server:
    """上下文管理器：自动挑端口、自动关停。"""

    def __init__(self, video_path: Path | str, *, ready_after: float = 0.0):
        self.video_path = Path(video_path)
        self.ready_after = ready_after
        self.httpd: Optional[ThreadingHTTPServer] = None

    def __enter__(self) -> "MockH3Server":
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.video_path, self.ready_after)
        )
        import threading

        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    @property
    def base_url(self) -> str:
        if not self.httpd:
            raise RuntimeError("server not started")
        return f"http://127.0.0.1:{self.httpd.server_port}"


def serve(port: int, video: Path | str, ready_after: float = 2.0) -> None:
    """给 tools/mock_h3_server.py 命令行复用。"""
    video_path = Path(video)
    if not video_path.exists():
        raise SystemExit(f"视频不存在：{video_path}")
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(video_path, ready_after))
    print(f"mock H3 on http://127.0.0.1:{port} serving {video_path}")
    srv.serve_forever()
