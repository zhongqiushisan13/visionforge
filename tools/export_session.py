"""把当前会话导出成人可读的 Markdown + 原始 jsonl，并打成一个 zip。

用法：
    python tools/export_session.py                 # 自动找本项目最新的会话
    python tools/export_session.py --session xxx.jsonl --out exports
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
_PROJECTS = Path.home() / ".workbuddy" / "projects"


def session_dir() -> Path:
    """会话目录名是工作区路径的编码形式，直接拼容易翻车，按目录名后缀兜底。"""
    exact = _PROJECTS / f"c-{str(ROOT).replace(':', '').lstrip(chr(92)).replace(chr(92), '-')}"
    if exact.exists():
        return exact
    hits = [p for p in _PROJECTS.glob(f"*{ROOT.name}") if p.is_dir()]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else exact

_REMINDER = re.compile(r"<system-reminder\b.*?</system-reminder>", re.S)
_QUERY = re.compile(r"<user_query>(.*?)</user_query>", re.S)
_IDENTITY = re.compile(r"<identity_context>.*?</identity_context>", re.S)


def newest_session() -> Path:
    d = session_dir()
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"没找到会话记录：{d}")
    return files[0]


def load(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def ts(ms: Any) -> str:
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(float(ms) / 1000))
    except (TypeError, ValueError):
        return "--"


def clean_user(text: str) -> str:
    text = _IDENTITY.sub("", text)
    text = _REMINDER.sub("", text)
    m = _QUERY.search(text)
    if m:
        text = m.group(1)
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    # 折叠连续空行
    out: List[str] = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def short_args(raw: Any, limit: int = 150) -> str:
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        return str(raw)[:limit]
    if not isinstance(d, dict):
        return str(d)[:limit]
    bits = []
    for k, v in d.items():
        if k in ("old_string", "new_string", "content", "widget_code"):
            bits.append(f"{k}={len(str(v))}字")
            continue
        s = str(v).replace("\n", " ")
        bits.append(f"{k}={s[:70]}")
        if len(", ".join(bits)) > limit:
            break
    return ", ".join(bits)[:limit]


def fence(text: str) -> str:
    return text.strip("\n")


def build(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    title = ""
    msgs: List[Dict[str, Any]] = []
    calls: Dict[str, Dict[str, Any]] = {}
    order: List[Dict[str, Any]] = []          # 按文件顺序排好的「消息 + 期间工具调用」

    for r in rows:
        t = r.get("type")
        if t == "ai-title" and not title:
            title = str(r.get("aiTitle") or "")
        elif t == "message":
            order.append({"kind": "msg", "at": r.get("timestamp"), "row": r})
            msgs.append(r)
        elif t == "function_call":
            entry = {"name": r.get("name"), "args": r.get("arguments"),
                     "callId": r.get("callId"), "status": "ok", "at": r.get("timestamp")}
            calls[entry["callId"]] = entry
            order.append({"kind": "call", "at": r.get("timestamp"), "call": entry})
        elif t == "function_call_result":
            cid = r.get("callId")
            if cid in calls:
                calls[cid]["status"] = r.get("status") or "ok"

    # 把工具调用挂到「它之后最近的助手消息」前一段
    buckets: List[List[Dict[str, Any]]] = [[] for _ in msgs]
    idx = -1
    msg_index = {id(m): i for i, m in enumerate(msgs)}
    for item in order:
        if item["kind"] == "msg":
            idx = msg_index[id(item["row"])]
        elif idx >= 0:
            buckets[idx].append(item["call"])

    lines: List[str] = []
    round_no = 0
    pending: List[Dict[str, Any]] = []
    summaries = 0
    notices = 0
    for i, m in enumerate(msgs):
        role = m.get("role")
        blocks = m.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        when = ts(m.get("timestamp"))
        tools = pending + buckets[i]
        pending = []

        # 上下文压缩摘要：引擎自己插的续接材料，不是用户发言
        if role == "user" and "<cb_summary>" in text:
            summaries += 1
            pending = tools
            lines.append(f"> 🗜 **{when}** 触发了上下文压缩，以下内容为引擎生成的续接摘要（已折叠，不计入轮次）\n")
            body = text.splitlines()
            keep = 36
            clipped = body[:keep]
            if len(body) > keep:
                clipped.append(f"…（摘要共 {len(body)} 行 / {len(text)} 字，完整内容见原始记录）")
            lines.append("<details><summary>展开压缩摘要（节选）</summary>\n")
            lines.append(fence("\n".join(clipped)) + "\n")
            lines.append("</details>\n")
            continue

        # 后台任务完成通知：系统事件，不是用户发言
        if role == "user" and text.lstrip().startswith("<task-notification>"):
            notices += 1
            tid = re.search(r"<task-id>(.*?)</task-id>", text)
            pending = tools
            lines.append(f"> ⏱ **{when}** 后台任务 `{tid.group(1) if tid else '?'}` 完成通知（系统事件，非用户发言）\n")
            continue

        if role == "user":
            body = clean_user(text)
            if not body:
                pending = tools
                continue
            round_no += 1
            lines.append(f"## 第 {round_no} 轮 · 用户 · {when}\n")
            lines.append(fence(body) + "\n")
        else:
            lines.append(f"### 助手 · {when}\n")
            if text.strip():
                lines.append(fence(text) + "\n")

        if role == "assistant" and tools:
            counter = collections.Counter(c["name"] for c in tools)
            summary = "、".join(f"{k}×{v}" if v > 1 else k for k, v in counter.items())
            lines.append(f"<details><summary>本轮工具调用 {len(tools)} 次：{summary}</summary>\n")
            for c in tools[:80]:
                flag = "" if c["status"] in ("ok", "success", "completed") else f" [{c['status']}]"
                lines.append(f"- `{c['name']}`{flag} — {short_args(c['args'])}")
            if len(tools) > 80:
                lines.append(f"- …其余 {len(tools) - 80} 次见原始记录")
            lines.append("\n</details>\n")

    tool_counter = collections.Counter(c["name"] for c in calls.values())
    span = ""
    if msgs:
        span = f"{ts(msgs[0].get('timestamp'))} → {ts(msgs[-1].get('timestamp'))}"
    return {
        "title": title or "未命名会话",
        "markdown": "\n".join(lines),
        "turns": round_no,
        "messages": len(msgs),
        "summaries": summaries,
        "notices": notices,
        "tool_total": len(calls),
        "tool_top": tool_counter.most_common(8),
        "span": span,
    }


def deliverable_list() -> List[str]:
    rows = []
    for pat, label in (("dist/*.zip", "发布包（不在 Git 里）"),
                       ("dist/VisionForge/VisionForge.exe", "主程序"),
                       ("docs/screenshots/*.png", "README 截图"),
                       ("README.md", "项目说明"), ("LICENSE", "开源协议")):
        for p in sorted(ROOT.glob(pat)):
            if p.is_file():
                rows.append(f"| `{p.relative_to(ROOT).as_posix()}` | {p.stat().st_size / 1048576:.1f} MB | {label} |")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="")
    ap.add_argument("--out", default="exports")
    args = ap.parse_args()

    src = Path(args.session) if args.session else newest_session()
    rows = load(src)
    info = build(rows)

    stamp = time.strftime("%Y-%m-%d", time.localtime(msgs_time(rows) / 1000))
    outdir = ROOT / args.out / f"visionforge-session-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    (outdir / "对话记录.md").write_text(
        f"# {info['title']}\n\n"
        f"- 会话 ID：`{src.stem}`\n"
        f"- 时间跨度：{info['span']}\n"
        f"- 轮次：{info['turns']} · 消息 {info['messages']} 条 · 工具调用 {info['tool_total']} 次\n"
        f"- 最常用工具：" + "、".join(f"{k}({v})" for k, v in info["tool_top"]) + "\n"
        f"- 引擎事件：压缩摘要 {info['summaries']} 次、后台任务通知 {info['notices']} 条（均已折叠，不计入轮次）\n\n"
        + info["markdown"] + "\n",
        encoding="utf-8",
    )

    raw_dir = outdir / "原始记录"
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / src.name).write_text(src.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")

    shots = ROOT / "docs" / "screenshots"
    if shots.exists():
        (outdir / "截图").mkdir(exist_ok=True)
        for f in sorted(shots.glob("*.png")):
            (outdir / "截图" / f.name).write_bytes(f.read_bytes())

    mem = ROOT / ".workbuddy" / "memory"
    if mem.exists():
        (outdir / "项目记忆").mkdir(exist_ok=True)
        for f in mem.glob("*.md"):
            (outdir / "项目记忆" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")

    lines = deliverable_list()
    (outdir / "交付物清单.md").write_text(
        "# 本次会话产出的文件\n\n"
        "| 文件 | 体积 | 说明 |\n|---|---|---|\n" + "\n".join(lines) + "\n\n"
        "> 完整源码见工程目录；`dist/` 与 `projects/` 不进 Git，发布包请走 Release 附件。\n",
        encoding="utf-8",
    )

    (outdir / "README.md").write_text(f"""# 会话记录包 · {info['title']}

导出自 WorkBuddy 本地会话 `{src.stem}`，时间 {info['span']}。

| 文件 | 内容 |
|---|---|
| `对话记录.md` | 人可读的完整对话：{info['turns']} 轮真实问答 + 每轮工具调用折叠块（另折叠 {info['notices']} 条后台任务通知） |
| `原始记录/{src.name}` | 引擎写入的原始 JSONL（含推理、工具参数、结果），无损 |
| `项目记忆/` | 当时写下的工作日志（踩坑记录、决策） |
| `交付物清单.md` | 这次会话产出的关键文件与体积 |

工具调用合计 {info['tool_total']} 次，主要是：{"、".join(f"{k}×{v}" for k, v in info['tool_top'])}。

> 原始 JSONL 里的长文本（文件内容、截图结果）未做裁剪，方便复盘每一步到底改了什么。
""", encoding="utf-8")

    zip_path = outdir.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(outdir.rglob("*")):
            if p.is_file():
                z.write(p, Path(outdir.name) / p.relative_to(outdir))

    print(f"会话：{info['title']}")
    print(f"轮次 {info['turns']} / 消息 {info['messages']} / 工具 {info['tool_total']}")
    print(f"目录：{outdir}")
    print(f"压缩包：{zip_path}  {zip_path.stat().st_size / 1048576:.1f}MB")
    return 0


def msgs_time(rows: List[Dict[str, Any]]) -> float:
    stamps = [r.get("timestamp") for r in rows if isinstance(r.get("timestamp"), (int, float))]
    return float(stamps[0]) if stamps else time.time() * 1000


if __name__ == "__main__":
    raise SystemExit(main())
