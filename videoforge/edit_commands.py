"""把一句人话翻译成剪辑操作（剪映「文本编辑」的轻量版）。

有 DeepSeek 就让模型解析；没有 key 时用本地规则兜底，保证离线也能用几条常见指令。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .editing import FILTER_PRESETS, TITLE_POSITIONS, TRANSITIONS
from .schemas import Project, Shot
from .utils import clamp

ALLOWED_FIELDS = {
    "speed", "volume", "filter_preset", "brightness", "contrast", "saturation",
    "temperature", "mirror", "reverse", "transition", "transition_duration",
    "title_text", "title_position", "enabled", "duration", "saturation",
}

SYSTEM = """你是视频剪辑指令解析器。把用户的一句话翻译成结构化操作，只输出 JSON。

可选字段与取值：
- speed: 0.25~4 的数字（1=原速）
- volume: 0~2（1=原音量）
- filter_preset: none / cinematic / bw / vintage / cool / warm / vivid
- brightness: -0.5~0.5
- contrast: 0.5~2（1=原样）
- saturation: 0~2（1=原样）
- temperature: -1~1（负=冷，正=暖）
- mirror: true/false（水平镜像）
- reverse: true/false（倒放）
- transition: cut / fade / dissolve / wipeleft / wiperight / slideup / slidedown / zoomin / fadeblack / fadewhite / circleopen
- transition_duration: 0.1~1 秒
- title_text: 叠加在画面上的文字（空字符串=清除）
- title_position: top / center / bottom
- enabled: true/false（false=这条镜头不进成片）
- duration: 1~15 秒

target 规则：
- "all" = 全部镜头
- ["S01","S03"] = 指定镜头
- "even" / "odd" = 偶数/奇数镜
- "first" / "last" = 第一条/最后一条

输出格式（不要 markdown 围栏）：
{"ops":[{"target":"all","set":{"filter_preset":"bw","contrast":1.1}}],"reply":"一句话说明改了什么"}
只改用户提到的参数，不要顺手改别的。"""


def _shot_ids(project: Project, target: Any) -> List[Shot]:
    shots = project.shots
    if target in (None, "all", "全部"):
        return list(shots)
    if target == "first":
        return shots[:1]
    if target == "last":
        return shots[-1:]
    if target == "even":
        return [s for i, s in enumerate(shots) if i % 2 == 1]
    if target == "odd":
        return [s for i, s in enumerate(shots) if i % 2 == 0]
    if isinstance(target, str):
        target = [target]
    if isinstance(target, list):
        ids = {str(t).upper() for t in target}
        return [s for s in shots if s.id.upper() in ids]
    return []


def _coerce(key: str, value: Any) -> Any:
    if key in ("speed",):
        return clamp(float(value), 0.25, 4.0)
    if key == "volume":
        return clamp(float(value), 0.0, 2.0)
    if key == "brightness":
        return clamp(float(value), -0.5, 0.5)
    if key == "contrast":
        return clamp(float(value), 0.5, 2.0)
    if key == "saturation":
        return clamp(float(value), 0.0, 2.0)
    if key == "temperature":
        return clamp(float(value), -1.0, 1.0)
    if key == "transition_duration":
        return clamp(float(value), 0.1, 1.0)
    if key == "duration":
        return int(clamp(float(value), 1, 15))
    if key in ("mirror", "reverse", "enabled"):
        return bool(value)
    if key == "filter_preset":
        return value if value in FILTER_PRESETS else "none"
    if key == "transition":
        return value if value in TRANSITIONS else "cut"
    if key == "title_position":
        return value if value in TITLE_POSITIONS else "bottom"
    return str(value)


def apply_ops(project: Project, ops: List[Dict[str, Any]]) -> List[str]:
    changes: List[str] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        targets = _shot_ids(project, op.get("target"))
        fields = {k: v for k, v in (op.get("set") or {}).items() if k in ALLOWED_FIELDS}
        if not targets or not fields:
            continue
        for shot in targets:
            for key, val in fields.items():
                try:
                    setattr(shot, key, _coerce(key, val))
                except (TypeError, ValueError):
                    continue
            if shot.transition != "cut" and shot.transition_duration <= 0:
                shot.transition_duration = 0.4
            if shot.duration < 4 and shot.clip:
                shot.duration = max(4, shot.duration)
        names = ", ".join(k for k in fields)
        changes.append(f"{len(targets)} 条镜头：{names}")
    return changes


# --------------------------------------------------------------------------- #
# 本地规则兜底
# --------------------------------------------------------------------------- #
def _local_ops(text: str, project: Project) -> Dict[str, Any]:
    t = text.strip()
    sets: Dict[str, Any] = {}
    reply: List[str] = []

    m = re.search(r"(\d+(?:\.\d+)?)\s*[xX倍]", t)
    if m:
        sets["speed"] = clamp(float(m.group(1)), 0.25, 4)
        reply.append(f"速度 {sets['speed']}x")
    if re.search(r"加快|加速|快一点|快放", t):
        sets.setdefault("speed", 1.25)
        reply.append("加速")
    if re.search(r"放慢|慢动作|慢一点|减速", t):
        sets.setdefault("speed", 0.75)
        reply.append("放慢")
    if re.search(r"黑白|去色", t):
        sets["filter_preset"] = "bw"
        reply.append("黑白")
    elif re.search(r"复古|胶片", t):
        sets["filter_preset"] = "vintage"
        reply.append("复古")
    elif re.search(r"电影|青橙", t):
        sets["filter_preset"] = "cinematic"
        reply.append("电影感")
    elif re.search(r"冷调|冷色", t):
        sets["filter_preset"] = "cool"
        reply.append("冷调")
    elif re.search(r"暖调|暖色", t):
        sets["filter_preset"] = "warm"
        reply.append("暖调")
    elif re.search(r"鲜艳|高饱和", t):
        sets["filter_preset"] = "vivid"
        reply.append("高饱和")
    for key, pattern in (("dissolve", r"叠化|溶解"), ("fade", r"淡入淡出|淡入"),
                         ("wipeleft", r"左划|向左转场"), ("wiperight", r"右划"),
                         ("zoomin", r"放大转场|推入"), ("fadeblack", r"黑场"),
                         ("fadewhite", r"白闪|闪白"), ("cut", r"硬切")):
        if re.search(pattern, t):
            sets["transition"] = key
            reply.append(f"转场 {TRANSITIONS[key]['label']}")
            break
    if re.search(r"镜像|翻转", t):
        sets["mirror"] = True
        reply.append("镜像")
    if re.search(r"倒放|反向", t):
        sets["reverse"] = True
        reply.append("倒放")
    m = re.search(r"音量\s*(?:调到|设为)?\s*(\d{1,3})\s*%", t)
    if m:
        sets["volume"] = clamp(int(m.group(1)) / 100, 0, 2)
        reply.append(f"音量 {m.group(1)}%")
    if re.search(r"静音|去掉声音", t):
        sets["volume"] = 0.0
        reply.append("静音")
    # 先找「第 N 镜 / S03」这种精确目标；没有才落到全部
    nums = re.findall(r"第\s*(\d{1,2})\s*(?:镜|条|个|段)", t)
    ids = re.findall(r"[Ss](\d{1,2})", t)
    specific = [f"S{int(i):02d}" for i in nums + ids]
    target: Any = specific or "all"
    if not sets:
        return {"ops": [], "reply": "没听懂要改哪个参数，试试「全部镜头调成黑白」或「第2镜1.5倍速」"}

    # 只对某一条生效的参数，和全片参数分开，别一股脑套到所有镜头上
    per_shot = {"speed", "volume", "mirror", "reverse", "title_text", "duration", "enabled"}
    if specific and set(sets) & per_shot:
        ops = []
        one = {k: v for k, v in sets.items() if k in per_shot}
        rest = {k: v for k, v in sets.items() if k not in per_shot}
        if one:
            ops.append({"target": specific, "set": one})
        if rest:
            ops.append({"target": "all", "set": rest})
        return {"ops": ops, "reply": "、".join(reply) + " 已应用"}
    return {"ops": [{"target": target, "set": sets}], "reply": "、".join(reply) + " 已应用"}


def interpret(text: str, project: Project, llm=None) -> Dict[str, Any]:
    if llm is None or not getattr(llm, "available", False):
        return _local_ops(text, project)

    shots_brief = [{"id": s.id, "dur": s.duration, "scene": s.scene[:20]} for s in project.shots]
    try:
        data = llm.json([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"镜头列表：{json.dumps(shots_brief, ensure_ascii=False)}\n\n指令：{text}"},
        ], temperature=0.2, max_tokens=1200)
        if not isinstance(data, dict) or not isinstance(data.get("ops"), list):
            raise ValueError("结构不符")
        return {"ops": data["ops"], "reply": str(data.get("reply") or "")}
    except Exception as exc:
        from .utils import warn
        warn(f"AI 指令解析失败，改用本地规则：{exc}")
        return _local_ops(text, project)
