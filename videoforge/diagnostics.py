"""诊断包：把排障要用的信息打成一个小 zip，**默认脱敏**。

为什么要有它：用户反馈「跑不动 / 报错」，来回来去问版本、显卡、编码器、Key 有没有配，
效率极低。这里一次性收齐，并且默认把 Key、token、用户名、家目录路径全部打码——
诊断包是可以直接发到群里的东西，不该带着用户的密钥出门。

包内结构：
    meta.json      版本、平台、Python、是否打包、工作目录、启动方式
    settings.json  生效配置（密钥已打码）+ 相关环境变量（值已打码）
    doctor.json    系统自检（ffmpeg / 编码器 / 字体 / ComfyUI 探测）
    projects.json  项目清单，只留 slug 与统计，不含标题、台词
    logs/runtime.log  日志尾部（去密钥、去用户名）
    README.txt     给看包的人一句说明
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import __version__
from .config import APP_ROOT, Settings

# 环境变量里出现这些词的，值一律打码
SECRET_HINTS = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
# 形如 sk-xxxx / Bearer xxxx 的串，即使藏在日志里也要抹掉；保留前缀便于定位是哪类凭据
SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 ***"),
    (re.compile(r"(?i)([?&](?:token|key|api_key|apikey)=)[^&\s\"']{6,}"), r"\1***"),
)
LOG_TAIL_LINES = 400


def mask_value(value: str) -> str:
    """短值全星号，长值留头尾 4 位——够定位是哪把 Key，又不足以复用。"""
    s = str(value or "")
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}{'*' * min(12, len(s) - 8)}{s[-4:]}"


def is_secret_name(name: str) -> bool:
    upper = (name or "").upper()
    return any(hint in upper for hint in SECRET_HINTS)


def collect_secrets(settings: Optional[Settings] = None) -> List[str]:
    """收集当前进程里真实存在的密钥，用于在任意文本里做定点清除。"""
    found: List[str] = []
    if settings is not None:
        for value in (settings.deepseek.api_key, settings.minimax.api_key):
            if value and len(value) >= 6:
                found.append(value)
    for key, val in os.environ.items():
        if is_secret_name(key) and val and len(val) >= 6:
            found.append(val)
    # 长的先替换，避免短串把长串切碎
    return sorted(set(found), key=len, reverse=True)


def scrub_text(text: str, secrets: Optional[Iterable[str]] = None) -> str:
    """把文本里的密钥、家目录、用户名抹掉；日志和 JSON 都要过这一道。"""
    out = str(text or "")
    for secret in sorted(set(secrets or []), key=len, reverse=True):
        if secret and secret in out:
            out = out.replace(secret, mask_value(secret))
    for pattern, repl in SECRET_PATTERNS:
        out = pattern.sub(repl, out)

    home = str(Path.home())
    if home and home in out:
        out = out.replace(home, "%USERPROFILE%")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if user and len(user) >= 2:
        out = out.replace(f"\\Users\\{user}", "\\Users\\%USER%")
        out = out.replace(f"/Users/{user}", "/Users/%USER%")
        out = out.replace(f"/home/{user}", "/home/%USER%")
    return out


def _env_snapshot(settings: Optional[Settings]) -> Dict[str, str]:
    """只收 VisionForge 相关的变量名，值统一打码/脱敏。"""
    out: Dict[str, str] = {}
    prefixes = ("VF_", "LLM_", "MINIMAX_", "COMFYUI_", "DEEPSEEK_", "OLLAMA_", "HTTP_PROXY",
                "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    for key, val in sorted(os.environ.items()):
        if not key.upper().startswith(prefixes):
            continue
        if is_secret_name(key):
            out[key] = mask_value(val)
        else:
            out[key] = scrub_text(val, [])
    return out


def _project_index(projects_root: Path) -> List[Dict[str, Any]]:
    """项目清单：不写标题、不写台词，只留能定位问题的统计。"""
    rows: List[Dict[str, Any]] = []
    if not projects_root.exists():
        return rows
    for d in sorted(projects_root.iterdir()):
        f = d / "project.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rows.append({"slug": d.name, "broken": True})
            continue
        shots = data.get("shots") or []
        status: Dict[str, int] = {}
        for s in shots:
            key = str(s.get("status") or "unknown")
            status[key] = status.get(key, 0) + 1
        rows.append({
            "slug": d.name,
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "mock": bool(data.get("mock")),
            "shots": len(shots),
            "duration": sum(int(s.get("duration") or 0) for s in shots),
            "status": status,
            "has_output": bool(data.get("output")),
            "failures": sorted({(s.get("error") or "")[:60] for s in shots if s.get("status") == "failed"}),
        })
    return rows


def _doctor_payload() -> Dict[str, Any]:
    try:
        from .sysprobe import doctor_summary

        return doctor_summary()
    except Exception as exc:  # 自检失败本身也是诊断信息
        return {"error": f"{type(exc).__name__}: {exc}"}


def _log_tail(lines: int = LOG_TAIL_LINES) -> str:
    path = APP_ROOT / "logs" / "runtime.log"
    if not path.exists():
        return ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 512 * 1024))
            chunk = fh.read().decode("utf-8", errors="ignore")
    except OSError as exc:
        return f"[无法读取日志] {exc}"
    tail = chunk.splitlines()[-lines:]
    return "\n".join(tail)


def collect(settings: Optional[Settings] = None, projects_root: Optional[Path] = None,
            doctor: Optional[Dict[str, Any]] = None, log_lines: int = LOG_TAIL_LINES,
            ) -> Dict[str, bytes]:
    """返回 {包内路径: 字节}，不落盘，方便测试与内存直出。"""
    settings = settings or Settings.load(APP_ROOT)
    root = Path(projects_root or settings.projects_root)
    secrets = collect_secrets(settings)

    meta = {
        "app": "VisionForge",
        "version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": scrub_text(sys.executable, secrets),
        "python": sys.version.split()[0],
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "cwd": scrub_text(os.getcwd(), secrets),
        "argv": [scrub_text(a, secrets) for a in sys.argv],
    }

    payload = {
        "meta.json": meta,
        "settings.json": {
            "config": settings.to_dict(),
            "env": _env_snapshot(settings),
        },
        "doctor.json": doctor if doctor is not None else _doctor_payload(),
        "projects.json": _project_index(root),
    }
    files: Dict[str, bytes] = {}
    for name, data in payload.items():
        text = scrub_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), secrets)
        files[name] = text.encode("utf-8")

    log_text = scrub_text(_log_tail(log_lines), secrets)
    if log_text.strip():
        files["logs/runtime.log"] = log_text.encode("utf-8")

    files["README.txt"] = (
        "VisionForge 诊断包（已脱敏）\n\n"
        "包含：版本与系统信息、生效配置、系统自检、项目统计、运行日志尾部。\n"
        "已自动打码：API Key / token / 密码 / 用户名 / 家目录路径。\n"
        "不含：剧本正文、台词、画面素材、成片。\n\n"
        "如果仍有担心，可直接解压检查后再发。\n"
    ).encode("utf-8")
    return files


def write_bundle(dest: Path, *, settings: Optional[Settings] = None,
                 projects_root: Optional[Path] = None,
                 doctor: Optional[Dict[str, Any]] = None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    files = collect(settings=settings, projects_root=projects_root, doctor=doctor)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return dest


def bundle_bytes(*, settings: Optional[Settings] = None,
                 projects_root: Optional[Path] = None,
                 doctor: Optional[Dict[str, Any]] = None) -> bytes:
    buf = io.BytesIO()
    files = collect(settings=settings, projects_root=projects_root, doctor=doctor)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def suggested_name() -> str:
    return f"visionforge-diag-{datetime.now():%Y%m%d-%H%M}.zip"
