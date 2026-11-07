"""基础设施：ffmpeg 定位、子进程、重试、日志、文件原子读写。"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, TypeVar

T = TypeVar("T")

# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
_COLOR = {
    "dim": "\033[2m", "red": "\033[31m", "green": "\033[32m",
    "yellow": "\033[33m", "blue": "\033[36m", "bold": "\033[1m", "reset": "\033[0m",
}
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text: str, color: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{_COLOR.get(color, '')}{text}{_COLOR['reset']}"


def log(msg: str, *, tag: str = "vf", color: str = "") -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"{_c(ts, 'dim')} {_c(f'[{tag}]', 'blue')} {msg}"
    print(_c(line, color) if color else line, flush=True)


def ok(msg: str) -> None:
    log(msg, color="green")


def warn(msg: str) -> None:
    log(msg, tag="warn", color="yellow")


def err(msg: str) -> None:
    log(msg, tag="error", color="red")


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe
# --------------------------------------------------------------------------- #
_FFMPEG_CACHE: Optional[str] = None


def find_ffmpeg() -> str:
    """按 env → PATH → imageio-ffmpeg 内置 的顺序找 ffmpeg。"""
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE
    candidates = [os.environ.get("VF_FFMPEG", ""), shutil.which("ffmpeg") or ""]
    for c in candidates:
        if c and Path(c).exists():
            _FFMPEG_CACHE = c
            return c
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            _FFMPEG_CACHE = exe
            return exe
    except Exception:
        pass
    raise RuntimeError(
        "找不到 ffmpeg。装一个（winget install Gyan.FFmpeg）或执行 python -m pip install imageio-ffmpeg"
    )


def find_ffprobe() -> Optional[str]:
    p = shutil.which("ffprobe")
    if p:
        return p
    ff = Path(find_ffmpeg())
    guess = ff.with_name(ff.stem.replace("ffmpeg", "ffprobe") + ff.suffix)
    return str(guess) if guess.exists() else None


def run(cmd: Sequence[str], *, timeout: int = 600, check: bool = False,
        quiet: bool = True, cwd: Optional[Path | str] = None) -> subprocess.CompletedProcess:
    """跑子进程；Windows 下不弹黑框，统一 UTF-8。"""
    kwargs: dict[str, Any] = dict(
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if cwd:
        kwargs["cwd"] = str(cwd)
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(list(cmd), **kwargs)
    if check and proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise RuntimeError(f"命令失败({proc.returncode}): {' '.join(map(str, cmd[:6]))}...\n{tail}")
    if not quiet and proc.stderr:
        log(proc.stderr[-800:], tag="ffmpeg")
    return proc


def ffmpeg(args: Sequence[str], *, timeout: int = 1800, quiet: bool = True,
           cwd: Optional[Path | str] = None) -> subprocess.CompletedProcess:
    return run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args],
               timeout=timeout, check=True, quiet=quiet, cwd=cwd)


def probe_duration(path: Path | str) -> float:
    """取时长：优先 ffprobe，没有就解析 ffmpeg -i 的 stderr。"""
    path = str(path)
    probe = find_ffprobe()
    if probe:
        proc = run([probe, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1", path], timeout=60)
        try:
            return round(float((proc.stdout or "").strip()), 3)
        except ValueError:
            pass
    proc = run([find_ffmpeg(), "-hide_banner", "-i", path], timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr or "")
    if m:
        h, mi, s = m.group(1), m.group(2), m.group(3)
        return round(int(h) * 3600 + int(mi) * 60 + float(s), 3)
    return 0.0


def probe_streams(path: Path | str) -> dict:
    probe = find_ffprobe()
    if not probe:
        return {}
    proc = run([probe, "-v", "error", "-print_format", "json", "-show_streams",
                "-show_format", str(path)], timeout=60)
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def has_audio(path: Path | str) -> bool:
    data = probe_streams(path)
    return any(s.get("codec_type") == "audio" for s in data.get("streams", []))


# --------------------------------------------------------------------------- #
# 重试 / 杂项
# --------------------------------------------------------------------------- #
def retry(times: int = 3, base_delay: float = 2.0, max_delay: float = 30.0,
          exceptions: tuple = (Exception,), on_error: Optional[Callable[[Exception, int], None]] = None):
    """指数退避 + 抖动。第 n 次失败后等 base*2^(n-1)，封顶 max_delay。"""

    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args, **kwargs) -> T:
            last: Optional[Exception] = None
            for attempt in range(1, times + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # type: ignore[misc]
                    last = exc
                    if on_error:
                        on_error(exc, attempt)
                    if attempt >= times:
                        break
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay *= 0.75 + random.random() * 0.5
                    time.sleep(delay)
            raise last  # type: ignore[misc]

        return wrapper

    return deco


def slugify(text: str, fallback: str = "project") -> str:
    text = unicodedata.normalize("NFKC", text or "").strip()
    ascii_part = "".join(ch for ch in text if ch.isalnum() and ch.isascii()).lower()
    if ascii_part:
        return re.sub(r"-+", "-", ascii_part)[:48] or fallback
    # 纯中文：用拼音不可靠，退化为时间戳
    return f"{fallback}-{time.strftime('%m%d-%H%M')}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def ensure_dir(p: Path | str) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path | str, data: Any) -> None:
    """原子写。临时文件名带线程号，避免并发保存互相踩（Windows 上会直接 Permission denied）。"""
    import threading

    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(f"{p.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(4):
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(p)
            return
        except PermissionError:
            if attempt >= 3:
                raise
            time.sleep(0.08 * (attempt + 1))
    if tmp.exists():
        tmp.unlink(missing_ok=True)


def write_text(path: Path | str, text: str) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(text, encoding="utf-8")


def human_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def chunked(seq: Iterable[T], size: int) -> Iterable[list[T]]:
    buf: list[T] = []
    for item in seq:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def safe_filename(name: str, maxlen: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "").strip(" ._")
    return (name or "untitled")[:maxlen]


class Timer:
    def __enter__(self) -> "Timer":
        self.t0 = time.time()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.elapsed = time.time() - self.t0
