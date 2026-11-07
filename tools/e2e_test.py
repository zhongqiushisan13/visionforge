"""端到端验收：建项目 → 渲染 → 手动改剪辑 → 重合成 → 校验成片。

只跑 mock 模式，不消耗任何付费额度。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8765"


def call(method: str, path: str, **kw):
    r = requests.request(method, BASE + path, timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def wait_job(job_id: str, timeout: float = 1800) -> dict:
    end = time.time() + timeout
    last = 0
    while time.time() < end:
        j = call("GET", f"/api/jobs/{job_id}?since={last}")
        last = j.get("log_total", 0)
        for line in j.get("logs") or []:
            print("   ", line)
        if j["status"] in ("done", "failed"):
            if j["status"] == "failed":
                raise RuntimeError("任务失败：" + (j.get("error") or ""))
            return j
        time.sleep(2)
    raise TimeoutError(job_id)


def main() -> int:
    print("== 1. 新建项目（mock）")
    job = call("POST", "/api/projects", json={
        "brief": "自动化验收：深夜便利店，一个店员和一位常客的无声默契，克制温暖。",
        "title": "自动化验收片", "duration": 40, "aspect": "16:9",
        "style": "电影感 35mm", "shot_hint": 8, "mock": True,
    })["job"]
    res = wait_job(job)["result"]
    slug = res["slug"]
    print("   项目:", slug, res)

    p = call("GET", f"/api/projects/{slug}")
    shots = p["shots"]
    assert len(shots) >= 6, "镜头太少"
    assert all(s["duration"] >= 4 for s in shots), "时长越界"
    print(f"   镜头 {len(shots)} 条，总时长 {p['progress']['duration']}s")

    print("== 2. 全量渲染")
    job = call("POST", f"/api/projects/{slug}/actions", json={"action": "render"})["job"]
    r = wait_job(job)["result"]
    print("   ", json.dumps(r.get("render"), ensure_ascii=False))

    p = call("GET", f"/api/projects/{slug}")
    rendered = [s for s in p["shots"] if s["clip_url"]]
    assert len(rendered) == len(p["shots"]), f"只出了 {len(rendered)}/{len(p['shots'])} 条"
    print(f"   已出片 {len(rendered)} 条")

    print("== 3. 手动剪辑：改字幕/时长/trim、停用一条、重排")
    sid = p["shots"][0]["id"]
    call("PATCH", f"/api/projects/{slug}/shots/{sid}", json={
        "subtitle": "这是人工改过的字幕", "dialogue": "这是人工改过的台词",
        "duration": 5, "trim_start": 0.6, "transition": "fade",
    })
    sid2 = p["shots"][1]["id"]
    call("PATCH", f"/api/projects/{slug}/shots/{sid2}", json={"enabled": False})
    order = [s["id"] for s in p["shots"]]
    order = order[1:] + order[:1]
    call("POST", f"/api/projects/{slug}/reorder", json={"order": order})
    p = call("GET", f"/api/projects/{slug}")
    assert p["shots"][0]["id"] == sid2, "重排没生效"
    assert p["shots"][0]["enabled"] is False
    print("   剪辑参数已写入")

    print("== 4. 合成")
    job = call("POST", f"/api/projects/{slug}/actions", json={"action": "assemble", "force": True})["job"]
    r = wait_job(job)["result"]["assemble"]
    print("   ", r)
    out = Path(r["output"])
    assert out.exists() and out.stat().st_size > 100_000, "成片文件异常"
    assert r["shots_excluded"] == [sid2], "停用镜头没被排除"

    srt = out.parent / "subtitles.srt"
    text = srt.read_text(encoding="utf-8")
    assert "这是人工改过的字幕" in text, "字幕修改没进成片"
    print(f"   成片 {out.name} {r['duration']}s {r['size_mb']}MB，字幕已包含人工修改")

    print("== 5. 单镜重拍 + 换素材接口冒烟")
    job = call("POST", f"/api/projects/{slug}/shots/{sid}/revise", json={"note": "雨再大一点"})["job"]
    wait_job(job)
    p = call("GET", f"/api/projects/{slug}")
    print("   重拍后状态:", p["shots"][-1]["status"], "/ 首镜:", p["shots"][0]["id"])

    print("== 6. 二次合成（稳定性）")
    job = call("POST", f"/api/projects/{slug}/actions", json={"action": "assemble", "force": True})["job"]
    r2 = wait_job(job)["result"]["assemble"]
    assert Path(r2["output"]).exists(), "二次合成失败"
    print(f"   OK 二次合成 {r2['duration']}s")

    print("\nALL PASS:", slug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
