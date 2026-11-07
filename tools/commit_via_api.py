"""用 GitHub Git Data API 提交任意一批文件（git https 通道被代理掐住时的替代方案）。"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER, REPO = "zhongqiushisan13", "visionforge"


def token() -> str:
    if os.environ.get("GH_TOKEN"):
        return os.environ["GH_TOKEN"]
    # 不走 shell：Windows 上 shell=True 是 cmd.exe，printf 不存在
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "1"},
    )
    for line in (proc.stdout or "").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"拿不到 GitHub 凭据：{proc.stderr[:200]}")


TOKEN = token()


def api(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "VisionForge-Uploader")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:400]
            if e.code in (502, 503, 504) and attempt < 3:
                time.sleep(3)
                continue
            raise SystemExit(f"{method} {url} -> {e.code}: {detail}")
        except Exception as exc:
            if attempt < 3:
                time.sleep(3)
                continue
            raise SystemExit(f"{method} {url} 网络失败: {exc}")
    raise SystemExit("unreachable")


def main() -> int:
    message = os.environ.get("COMMIT_MSG", "").strip()
    paths = sys.argv[1:]
    if not message or not paths:
        raise SystemExit("用法: COMMIT_MSG='...' python tools/commit_via_api.py <路径...>")

    ref = api("GET", f"https://api.github.com/repos/{OWNER}/{REPO}/git/ref/heads/main")
    head = ref["object"]["sha"]
    commit = api("GET", f"https://api.github.com/repos/{OWNER}/{REPO}/git/commits/{head}")
    base_tree = commit["tree"]["sha"]
    print("head:", head[:7])

    entries = []
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            print("跳过（不存在）:", rel)
            continue
        blob = api("POST", f"https://api.github.com/repos/{OWNER}/{REPO}/git/blobs", {
            "content": base64.b64encode(p.read_bytes()).decode(),
            "encoding": "base64",
        })
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  blob {rel}: {blob['sha'][:7]} ({p.stat().st_size} B)")

    if not entries:
        raise SystemExit("没有可提交的文件")

    tree = api("POST", f"https://api.github.com/repos/{OWNER}/{REPO}/git/trees",
               {"base_tree": base_tree, "tree": entries})
    new = api("POST", f"https://api.github.com/repos/{OWNER}/{REPO}/git/commits",
              {"message": message, "tree": tree["sha"], "parents": [head]})
    api("PATCH", f"https://api.github.com/repos/{OWNER}/{REPO}/git/refs/heads/main", {"sha": new["sha"]})
    print("提交:", new["sha"][:7], "| 文件:", len(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
