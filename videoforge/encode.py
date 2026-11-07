"""编码器选择：有 N 卡就走 NVENC，没有就回 libx264。

自动配置里最值钱的一项——1080p 合成在纯 CPU 上能跑几分钟，NVENC 能压到几十秒。
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from .config import RenderConfig
from .utils import find_ffmpeg, run

# 编码器优先级：硬编优先，画质兜底
_PRIORITY = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")


@lru_cache(maxsize=1)
def available_encoders() -> set[str]:
    try:
        proc = run([find_ffmpeg(), "-hide_banner", "-encoders"], timeout=30)
    except Exception:
        return {"libx264"}
    names = set()
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.add(parts[1])
    return names or {"libx264"}


def _smoke(codec: str) -> bool:
    """编译进来 ≠ 能用。跑一帧真实编码，确认驱动/显卡在位。"""
    try:
        proc = run([find_ffmpeg(), "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.2",
                    "-frames:v", "1", "-c:v", codec, "-f", "null", "-"], timeout=25)
        return proc.returncode == 0
    except Exception:
        return False


@lru_cache(maxsize=1)
def usable_encoders() -> set[str]:
    have = available_encoders()
    out = {"libx264"} if "libx264" in have else set()
    for codec in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if codec in have and _smoke(codec):
            out.add(codec)
    return out or have


def resolve(cfg: RenderConfig) -> str:
    want = (cfg.encoder or "auto").lower()
    usable = usable_encoders()
    if want != "auto":
        return want if want in usable else "libx264"
    for name in _PRIORITY:
        if name in usable:
            return name
    return "libx264"


def video_args(cfg: RenderConfig, crf: int | None = None, *, fast: bool = False) -> List[str]:
    """返回 -c:v ... 一组参数。crf 沿用 x264 的语义（越小越清晰）。"""
    codec = resolve(cfg)
    q = int(cfg.crf if crf is None else crf)
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4" if fast else "p5",
                "-rc", "vbr", "-cq", str(q), "-b:v", "0", "-pix_fmt", "yuv420p"]
    if codec == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", str(q), "-pix_fmt", "nv12"]
    if codec == "h264_amf":
        return ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q),
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast" if fast else cfg.preset,
            "-crf", str(q), "-pix_fmt", "yuv420p"]
