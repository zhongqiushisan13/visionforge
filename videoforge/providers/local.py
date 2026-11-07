"""离线占位 Provider：纯 ffmpeg 合成「有运动、有字幕、时长准确」的片段。

存在的意义不是好看，而是让整条流水线（含剪辑合成）在没 API Key 时也能跑通验收，
顺便给单元测试当夹具用。
"""

from __future__ import annotations

import hashlib
import random
import re
from pathlib import Path
from typing import Optional

from .base import ProviderResult
from ..config import RenderConfig, Settings
from ..schemas import Shot
from ..utils import ensure_dir, ffmpeg, probe_duration

_PALETTES = [
    ("0x101a2b", "0x2b3f5c"), ("0x1d1a2e", "0x4a3557"), ("0x0f2027", "0x2c5364"),
    ("0x2b1b17", "0x6b3f2a"), ("0x10261f", "0x2f5d4a"), ("0x231a2b", "0x543a55"),
]


def _ascii(text: str, limit: int = 70) -> str:
    text = re.sub(r"[^\x20-\x7E]+", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or "placeholder shot"


def _wrap(text: str, width: int = 38, max_lines: int = 3) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
        else:
            cur = f"{cur} {w}".strip()
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return "\n".join(lines)


class LocalProvider:
    """生成渐变背景 + 缓慢推镜 + 文字标记的占位片段。"""

    name = "local"
    supports_keyframe = True

    def __init__(self, settings: Settings):
        self.s = settings
        self.r: RenderConfig = settings.render
        # 占位片不必按 4K 烧 CPU，720p 出来再交给剪辑台放大
        self.w = min(self.r.width, 1280)
        self.h = int(round(self.w * self.r.height / max(1, self.r.width) / 2) * 2)

    # ---------------- 工具 ---------------- #
    def _seed(self, shot: Shot) -> int:
        base = int(hashlib.sha1(f"{shot.id}{shot.video_prompt}".encode()).hexdigest()[:8], 16)
        return base % 100000

    def _text_file(self, shot: Shot, workdir: Path) -> str:
        """写入同名 txt 并返回**文件名**（配合 cwd 使用，避免 Windows 盘符把滤镜语法搞崩）。"""
        label = _ascii(f"{shot.scene} — {shot.video_prompt}", 90)
        body = f"{shot.id}  ·  {shot.duration}s\n{_wrap(label, 42, 2)}"
        name = f".vf_{shot.id}.txt"
        (workdir / name).write_text(body, encoding="utf-8")
        return name

    def _gradient_source(self, shot: Shot, duration: float = 1.0) -> str:
        rnd = random.Random(self._seed(shot))
        c0, c1 = rnd.choice(_PALETTES)
        return (
            f"gradients=s={self.w}x{self.h}:c0={c0}:c1={c1}"
            f":x0=0:y0=0:x1={self.w}:y1={self.h}"
            f":duration={max(1.0, duration):.2f}:speed=0.012:r={self.r.fps}"
        )

    # ---------------- 关键帧 ---------------- #
    def keyframe(self, shot: Shot, out_path: Path) -> ProviderResult:
        out_path = Path(out_path)
        ensure_dir(out_path.parent)
        txt = self._text_file(shot, out_path.parent)
        vf = (
            f"drawtext=textfile='{txt}':fontcolor=white:fontsize=46:"
            f"line_spacing=14:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.28:boxborderw=28"
        )
        ffmpeg([
            "-f", "lavfi", "-i", self._gradient_source(shot),
            "-frames:v", "1", "-vf", vf, "-q:v", "2", out_path.name,
        ], timeout=120, cwd=out_path.parent)
        (out_path.parent / txt).unlink(missing_ok=True)
        return ProviderResult(path=str(out_path), provider="local", seed=self._seed(shot))

    # ---------------- 片段 ---------------- #
    def clip(self, shot: Shot, out_path: Path, keyframe: Optional[Path] = None) -> ProviderResult:
        out_path = Path(out_path)
        ensure_dir(out_path.parent)
        dur = max(2, int(shot.duration))
        txt = self._text_file(shot, out_path.parent)
        zoom = ("zoompan=z='min(1.0+0.0009*on,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d=1:s={self.w}x{self.h}:fps={self.r.fps}")
        overlay = (
            f"drawtext=textfile='{txt}':fontcolor=white@0.45:fontsize=26:"
            f"line_spacing=8:x=48:y=44,"
            f"fade=t=in:st=0:d=0.35,fade=t=out:st={max(0, dur - 0.4):.2f}:d=0.4,format=yuv420p"
        )
        # 占位片刻意不用关键帧当底：关键帧上印着提示词，糊进成片会盖住字幕
        args = ["-f", "lavfi", "-i", self._gradient_source(shot, dur)]
        vf = f"{zoom},{overlay}"
        try:
            ffmpeg([*args, "-vf", vf, "-r", str(self.r.fps), "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
                    "-an", out_path.name], timeout=600, cwd=out_path.parent)
        finally:
            (out_path.parent / txt).unlink(missing_ok=True)
        return ProviderResult(path=str(out_path), provider="local",
                              duration=probe_duration(out_path), seed=self._seed(shot))
