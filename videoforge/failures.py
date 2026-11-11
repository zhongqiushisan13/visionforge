"""失败归因：把厂商五花八门的错误串归一成几类，供界面给建议。

只输出机器可读的 kind + 计数 + 样本镜头号，具体文案放在前端词库里，
这样新增一门语言不用改后端。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

# 顺序有意义：越具体的越靠前，避免 "401 timeout" 被归到 timeout
_RULES = (
    ("auth", re.compile(r"401|403|unauthor|invalid\s*api[\s_-]*key|鉴权|认证失败|api[\s_-]*key", re.I)),
    ("rate", re.compile(r"429|rate[\s_-]*limit|too many requests|限流|频率", re.I)),
    ("quota", re.compile(r"余额|欠费|quota|insufficient|budget|预算|402", re.I)),
    ("vram", re.compile(r"out of memory|cuda|显存|\boom\b|allocation", re.I)),
    ("safety", re.compile(r"敏感|sensitive|content policy|blocked|1026|审核", re.I)),
    ("model", re.compile(r"model.{0,12}not|模型不存在|not found|404|无权访问", re.I)),
    ("timeout", re.compile(r"timeout|timed out|超时|deadline", re.I)),
    ("empty", re.compile(r"未产出|空文件|empty|no output", re.I)),
)


def classify(error: str) -> str:
    text = str(error or "")
    for kind, pattern in _RULES:
        if pattern.search(text):
            return kind
    return "other"


def summarize(shots: Iterable[Any], limit_samples: int = 3) -> Dict[str, Any]:
    """按失败类型汇总；没有失败时 total=0。"""
    buckets: Dict[str, Dict[str, Any]] = {}
    total = 0
    for shot in shots:
        if getattr(shot, "status", "") != "failed":
            continue
        total += 1
        kind = classify(getattr(shot, "error", ""))
        item = buckets.setdefault(kind, {"kind": kind, "count": 0, "samples": []})
        item["count"] += 1
        sid = getattr(shot, "id", "")
        if sid and len(item["samples"]) < limit_samples:
            item["samples"].append(sid)
    kinds: List[Dict[str, Any]] = sorted(buckets.values(), key=lambda x: -x["count"])
    return {"total": total, "kinds": kinds}


def failed_shots(shots: Iterable[Any]) -> List[Any]:
    """只挑失败的，用于「重试失败项」。"""
    return [s for s in shots if getattr(s, "status", "") == "failed"]
