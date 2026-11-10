"""基础设施：ffmpeg 定位、子进程、重试、日志、文件原子读写。"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
from collections import deque
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
_RECENT_WARNINGS: deque[str] = deque(maxlen=100)


def _remember_warning(msg: str) -> None:
    _RECENT_WARNINGS.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}")


def recent_warnings(limit: int = 10) -> list[str]:
    """doctor 页展示最近的警告/被吞异常，避免失败只表现为“没动”。"""
    return list(_RECENT_WARNINGS)[-max(1, limit):]


def swallowed(context: str, exc: BaseException) -> None:
    """记录一处被吞掉的异常。允许降级，但不允许无声降级。"""
    warn(f"{context}：{type(exc).__name__}: {exc}")


def _c(text: str, color: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{_COLOR.get(color, '')}{text}{_COLOR['reset']}"


def _emit(line: str) -> None:
    """往 stdout 写一行日志，绝不因为编码问题把主流程带崩。

    英文版 Windows 的控制台是 cp1252，中文日志一 print 就是 UnicodeEncodeError；
    而日志恰好夹在提交任务、切 provider 这些关键路径上，一崩就是“莫名其妙失败”。
    打包成 windowed exe 后 sys.stdout 还可能是 None，同样要兜住。
    """
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(line + "\n")
        stream.flush()
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "utf-8"
        try:
            stream.write(line.encode(enc, "replace").decode(enc) + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
    except (OSError, ValueError):
        # 控制台已关闭 / 流被换成了不可写对象
        pass


def log(msg: str, *, tag: str = "vf", color: str = "") -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"{_c(ts, 'dim')} {_c(f'[{tag}]', 'blue')} {msg}"
    _emit(_c(line, color) if color else line)


def ok(msg: str) -> None:
    log(msg, color="green")


def warn(msg: str) -> None:
    _remember_warning(msg)
    log(msg, tag="warn", color="yellow")


def err(msg: str) -> None:
    log(msg, tag="error", color="red")


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe
# --------------------------------------------------------------------------- #
_FFMPEG_CACHE: Optional[str] = None
_FFMPEG_OK: dict[str, bool] = {}


def _probe_ffmpeg(path: str) -> bool:
    """用一条最小 drawtext 命令验活（占位片段、字幕烧录都依赖 drawtext）。

    Windows 上的 ffmpeg 是各家自己编的，能力差异很大：缺 fontconfig 配置时会
    刷 `Fontconfig error`，个别构建在并发下还出现过 0xC0000005 崩溃。
    与其等出片时才炸，不如启动时先验一遍；不过的直接跳过。
    """
    if path in _FFMPEG_OK:
        return _FFMPEG_OK[path]
    cmd = [
        path, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
        "-vf", "drawtext=text='vf':fontcolor=white:fontsize=10:x=2:y=2",
        "-frames:v", "1", "-f", "null", "-",
    ]
    kwargs: dict[str, Any] = dict(capture_output=True, timeout=30)
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, **kwargs)
        ok_flag = proc.returncode == 0
        if not ok_flag:
            warn(f"{Path(path).name} 自检失败（退出码 {proc.returncode}），换下一个 ffmpeg")
    except Exception as exc:  # 超时 / 无法执行
        warn(f"{Path(path).name} 自检异常：{type(exc).__name__}，换下一个 ffmpeg")
        ok_flag = False
    _FFMPEG_OK[path] = ok_flag
    return ok_flag


def find_ffmpeg() -> str:
    """按 环境变量 → 打包自带 → PATH 的顺序找 ffmpeg，并逐个做 drawtext 自检。

    为什么要自带优先：打包版里那条 ffmpeg 是构建时锁定的，codec / libass / xfade
    都可预期；PATH 上的可能是任何构建（实测 WinGet 装完 Gyan 的 full 构建，在
    并发生成占位画面时出现过 0xC0000005 崩溃，换自带的那条就正常）。
    自检仍然保留——真碰到跑不了 drawtext 的构建，直接跳过，别等到出片才炸。
    """
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE

    env_pick = os.environ.get("VF_FFMPEG", "")
    bundled = ""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            bundled = exe
    except Exception:
        pass
    system_pick = shutil.which("ffmpeg") or ""

    order = [env_pick]
    if getattr(sys, "frozen", False):
        order += [bundled, system_pick]      # 打包版：自带的更可预期
    else:
        order += [system_pick, bundled]      # 源码运行：尊重用户环境
    candidates: list[str] = []
    for c in order:
        if c and Path(c).exists() and c not in candidates:
            candidates.append(c)
    if not candidates:
        raise RuntimeError(
            "找不到 ffmpeg。装一个（winget install Gyan.FFmpeg）或执行 python -m pip install imageio-ffmpeg"
        )
    for c in candidates:
        if _probe_ffmpeg(c):
            if c != candidates[0]:
                log(f"ffmpeg 选用 {c}（前一个候选未通过自检）", tag="env")
            _FFMPEG_CACHE = c
            return c
    warn("所有 ffmpeg 候选都没通过 drawtext 自检，沿用第一个；字幕/占位画面可能出错")
    _FFMPEG_CACHE = candidates[0]
    return _FFMPEG_CACHE


def find_ffprobe() -> Optional[str]:
    # 先找与已选 ffmpeg 同目录的 ffprobe，避免 PATH 上是另一套构建
    try:
        ff = Path(find_ffmpeg())
        guess = ff.with_name(ff.stem.replace("ffmpeg", "ffprobe") + ff.suffix)
        if guess.exists():
            return str(guess)
    except Exception:
        pass
    return shutil.which("ffprobe") or None


def _run_dir() -> Path:
    from .config import APP_ROOT

    return ensure_dir(APP_ROOT / "run")


def _child_pid_file(pid: int) -> Path:
    return _run_dir() / f"child-{os.getpid()}-{pid}.json"


def _child_registry_enabled() -> bool:
    """受限环境（沙箱、杀软拦删除）可关掉子进程登记，避免清理阶段卡住。"""
    return os.environ.get("VF_NO_CHILD_REGISTRY", "").strip().lower() not in (
        "1", "true", "yes", "on")


def _register_child(pid: int, cmd: Sequence[str]) -> Optional[Path]:
    """登记子进程，供下次启动时清理崩溃后留下的孤儿 ffmpeg。"""
    if not _child_registry_enabled():
        return None
    try:
        path = _child_pid_file(pid)
        path.write_text(json.dumps({"owner": os.getpid(), "pid": pid,
                                    "cmd": [str(x) for x in cmd], "ts": now_iso()},
                                   ensure_ascii=False), encoding="utf-8")
        return path
    except OSError as exc:
        swallowed("登记子进程失败", exc)
        return None


def _unregister_child(path: Optional[Path]) -> None:
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        proc = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=10)
        return str(pid) in (proc.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=20)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)


def reap_orphans() -> int:
    """清理上一轮崩溃留下的 ffmpeg/ffprobe 子进程。

    只处理 `run/child-*.json` 里登记的进程，并且要求登记它的 VisionForge
    主进程已经不在；不会扫全系统的 ffmpeg，避免误杀用户自己的转码任务。
    """
    killed = 0
    run_dir = _run_dir()
    if not _child_registry_enabled():
        # 关掉登记时也别去碰这些文件：受限环境下删除本身就会卡住
        return 0
    for meta_path in run_dir.glob("child-*.json"):
        data = read_json(meta_path, default={}) or {}
        try:
            owner = int(data.get("owner") or 0)
            pid = int(data.get("pid") or 0)
        except (TypeError, ValueError):
            meta_path.unlink(missing_ok=True)
            continue
        cmd = [str(x) for x in data.get("cmd") or []]
        exe = Path(cmd[0]).name.lower() if cmd else ""
        if owner == os.getpid() or _pid_running(owner):
            continue
        if pid and _pid_running(pid) and exe.startswith(("ffmpeg", "ffprobe")):
            try:
                _kill_tree(pid)
                killed += 1
                warn(f"已清理上次崩溃留下的 {exe}（PID {pid}）")
            except Exception as exc:
                swallowed(f"清理孤儿进程失败（PID {pid}）", exc)
        meta_path.unlink(missing_ok=True)
    return killed


def run(cmd: Sequence[str], *, timeout: int = 600, check: bool = False,
        quiet: bool = True, cwd: Optional[Path | str] = None) -> subprocess.CompletedProcess:
    """跑子进程；Windows 下不弹黑框，统一 UTF-8，并登记 PID 供孤儿清理。"""
    kwargs: dict[str, Any] = dict(
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    if cwd:
        kwargs["cwd"] = str(cwd)
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(list(cmd), **kwargs)
    pid_file = _register_child(proc.pid, cmd)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            _kill_tree(proc.pid)
        finally:
            stdout, stderr = proc.communicate()
        raise
    finally:
        _unregister_child(pid_file)
    done = subprocess.CompletedProcess(list(cmd), proc.returncode, stdout, stderr)
    if check and done.returncode != 0:
        tail = (done.stderr or "")[-1500:]
        # 崩溃类退出码给个十六进制，0xC0000005 比 3221225477 好认得多
        code = done.returncode
        label = f"{code} / 0x{code & 0xFFFFFFFF:08X}" if code < 0 or code > 255 else str(code)
        raise RuntimeError(f"命令失败({label}): {' '.join(map(str, cmd[:6]))}...\n{tail}")
    if not quiet and done.stderr:
        log(done.stderr[-800:], tag="ffmpeg")
    return done


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
