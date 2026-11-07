"""剪辑参数表：转场 / 滤镜预设 / 时间轴计算。

单一事实来源——渲染器、接口、界面都从这里取，避免三处各写一份魔法字符串。
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

# ffmpeg xfade 的 transition 名，None 表示硬切（不重叠）
TRANSITIONS: Dict[str, Dict[str, object]] = {
    "cut":       {"label": "硬切",     "xfade": None},
    "fade":      {"label": "淡入淡出", "xfade": "fade"},
    "dissolve":  {"label": "叠化",     "xfade": "dissolve"},
    "wipeleft":  {"label": "左划",     "xfade": "wipeleft"},
    "wiperight": {"label": "右划",     "xfade": "wiperight"},
    "slideup":   {"label": "上滑",     "xfade": "slideup"},
    "slidedown": {"label": "下滑",     "xfade": "slidedown"},
    "zoomin":    {"label": "放大推入", "xfade": "zoomin"},
    "fadeblack": {"label": "黑场过渡", "xfade": "fadeblack"},
    "fadewhite": {"label": "白闪",     "xfade": "fadewhite"},
    "circleopen": {"label": "圆形展开", "xfade": "circleopen"},
}

# 每个预设都是一串 ffmpeg 滤镜；值都按「不过分」的原则调，避免一眼假
FILTER_PRESETS: Dict[str, Dict[str, object]] = {
    "none":      {"label": "原片", "chain": []},
    "cinematic": {"label": "电影青橙", "chain": [
        "curves=preset=medium_contrast",
        "colorbalance=rs=0.05:gs=-0.01:bs=0.09",
        "eq=saturation=1.08",
    ]},
    "bw":        {"label": "黑白", "chain": ["hue=s=0", "eq=contrast=1.12"]},
    "vintage":   {"label": "复古胶片", "chain": [
        "curves=preset=vintage", "eq=saturation=0.82:contrast=1.05",
    ]},
    "cool":      {"label": "冷调", "chain": ["colorbalance=bs=0.12:rs=-0.04"]},
    "warm":      {"label": "暖调", "chain": ["colorbalance=rs=0.12:bs=-0.06"]},
    "vivid":     {"label": "高饱和", "chain": ["eq=saturation=1.3:contrast=1.06"]},
}

TITLE_POSITIONS = {"top": 1, "center": 2, "bottom": 3}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def transition_name(shot) -> str:
    name = (shot.transition or "cut").lower()
    return name if name in TRANSITIONS else "cut"


def transition_xfade(shot) -> str | None:
    return TRANSITIONS[transition_name(shot)]["xfade"]  # type: ignore[return-value]


def default_transition_duration(default: float = 0.4) -> float:
    return clamp(default, 0.1, 1.0)


def shot_transition_duration(shot, default: float = 0.4) -> float:
    td = float(getattr(shot, "transition_duration", 0) or 0)
    if td <= 0:
        td = default_transition_duration(default)
    return clamp(td, 0.1, 1.0)


def timeline(shots: Sequence, default_td: float = 0.4) -> Tuple[List[float], float]:
    """返回每个镜头的起止起点（含转场重叠）与总时长。

    第 i 条与第 i+1 条之间若为 xfade 转场，时间轴会重叠 td 秒：
        start[0] = 0
        start[i] = start[i-1] + dur[i-1] - overlap[i-1]
    """
    starts: List[float] = []
    cursor = 0.0
    for i, shot in enumerate(shots):
        starts.append(round(cursor, 4))
        dur = float(shot.duration)
        overlap = 0.0
        if i + 1 < len(shots):
            nxt = shots[i + 1]
            if transition_xfade(nxt) is not None:
                overlap = min(shot_transition_duration(nxt, default_td), dur * 0.5,
                              float(nxt.duration) * 0.5)
        cursor += dur - overlap
    total = cursor
    if shots:
        total = starts[-1] + float(shots[-1].duration)
    return starts, round(total, 3)


def overlaps(shots: Sequence, default_td: float = 0.4) -> List[float]:
    """第 i 个元素表示镜头 i 之后的转场重叠秒数（最后一条恒为 0）。"""
    out: List[float] = []
    for i, shot in enumerate(shots):
        if i + 1 >= len(shots):
            out.append(0.0)
            continue
        nxt = shots[i + 1]
        td = shot_transition_duration(nxt, default_td) if transition_xfade(nxt) else 0.0
        out.append(round(min(td, float(shot.duration) * 0.5, float(nxt.duration) * 0.5), 4))
    return out


def color_chain(shot) -> List[str]:
    """按镜头参数拼调色链：预设 → 亮度/对比/饱和 → 色温。"""
    chain: List[str] = []
    preset = FILTER_PRESETS.get(getattr(shot, "filter_preset", "") or "none")
    if preset:
        chain.extend(preset["chain"])  # type: ignore[arg-type]
    eq_parts = []
    if abs(float(shot.brightness or 0)) > 1e-3:
        eq_parts.append(f"brightness={clamp(float(shot.brightness), -0.5, 0.5):.3f}")
    if abs(float(shot.contrast or 1) - 1) > 1e-3:
        eq_parts.append(f"contrast={clamp(float(shot.contrast), 0.5, 2.0):.3f}")
    if abs(float(shot.saturation or 1) - 1) > 1e-3:
        eq_parts.append(f"saturation={clamp(float(shot.saturation), 0, 2.0):.3f}")
    if eq_parts:
        chain.append("eq=" + ":".join(eq_parts))
    temp = clamp(float(getattr(shot, "temperature", 0) or 0), -1, 1)
    if abs(temp) > 0.02:
        rs, bs = (0.14 * temp, -0.10 * temp)
        chain.append(f"colorbalance=rs={rs:.3f}:bs={bs:.3f}")
    return chain
