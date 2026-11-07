"""开发期 QA 工具：通过 CDP 打开页面、执行 JS、截图。

用法：
  python tools/qa_shot.py --url http://127.0.0.1:8765 --out .tmp_check/a.png --wait 3
  python tools/qa_shot.py --url ... --js "document.querySelector('[data-view=timeline]').click()" --wait 2
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def edge_exe() -> str:
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("找不到 Edge")


def wait_json(url: str, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.3)
    raise SystemExit(f"CDP 未就绪：{url}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait", type=float, default=3.0)
    ap.add_argument("--js", default="")
    ap.add_argument("--width", type=int, default=1480)
    ap.add_argument("--height", type=int, default=940)
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    import tempfile
    tmp_root = tempfile.mkdtemp(prefix="vf-qa-")
    profile = os.path.join(tmp_root, f"cdp-{int(time.time())}")
    log_path = os.path.join(tmp_root, "edge-cdp.log")
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([
        edge_exe(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        f"--remote-debugging-port={args.port}", f"--user-data-dir={profile}",
        f"--window-size={args.width},{args.height}", "about:blank",
    ], stdout=log, stderr=subprocess.STDOUT)
    try:
        try:
            wait_json(f"http://127.0.0.1:{args.port}/json/version")
        except SystemExit:
            log.flush()
            print(open(log_path, encoding="utf-8", errors="replace").read()[-800:])
            raise
        req = urllib.request.Request(
            f"http://127.0.0.1:{args.port}/json/new?{args.url}", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            target = json.loads(r.read().decode())
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=30)
        seq = [0]

        def call(method: str, params: dict | None = None) -> dict:
            seq[0] += 1
            ws.send(json.dumps({"id": seq[0], "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == seq[0]:
                    return msg

        call("Page.enable")
        call("Runtime.enable")
        if args.js:
            time.sleep(max(0.5, args.wait * 0.4))
            res = call("Runtime.evaluate", {"expression": args.js, "awaitPromise": True})
            print("JS:", json.dumps(res.get("result", {}), ensure_ascii=False)[:400])
        time.sleep(args.wait)
        shot = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": args.full})
        data = shot.get("result", {}).get("data")
        if not data:
            raise SystemExit(f"截图失败：{shot}")
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "wb") as fh:
            fh.write(base64.b64decode(data))
        print("saved", args.out)
        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
