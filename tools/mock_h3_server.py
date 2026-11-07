"""本地 H3 兼容网关的最小模拟实现（开发/联调用，不参与打包）。

    python tools/mock_h3_server.py --port 8798 --video path/to/clip.mp4

然后：
    MINIMAX_MODE=local
    MINIMAX_BASE_URL=http://127.0.0.1:8798
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VIDEO = Path("")
TASKS: dict[str, float] = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 安静点
        pass

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
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
        TASKS[task_id] = time.time()
        return self._json({"task_id": task_id})

    def do_GET(self):
        if self.path.startswith("/v2/query/video_generation/"):
            task_id = self.path.rsplit("/", 1)[-1]
            if task_id not in TASKS:
                return self._json({"error": {"message": "no task"}}, 404)
            # 第一次查询 queued，第二次 running，之后 succeeded，模拟真实异步
            age = time.time() - TASKS[task_id]
            status = "queued" if age < 1 else ("running" if age < 2 else "succeeded")
            task = {"status": status}
            if status == "succeeded":
                task["content"] = {"url": f"http://127.0.0.1:{self.server.server_port}/file.mp4"}
            return self._json({"task": task})
        if self.path.startswith("/file.mp4"):
            data = VIDEO.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return self._json({"ok": True, "service": "mock-h3"})


def main() -> None:
    global VIDEO
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8798)
    ap.add_argument("--video", required=True)
    args = ap.parse_args()
    VIDEO = Path(args.video)
    if not VIDEO.exists():
        raise SystemExit(f"视频不存在：{VIDEO}")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock H3 on http://127.0.0.1:{args.port} serving {VIDEO}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
