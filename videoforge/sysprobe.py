"""首次运行环境探测与推荐配置。

目标：换一台电脑，双击 exe 就能跑，不用先教用户「去装 ffmpeg」「去开 NVIDIA 编码」。
探测结果同时用于设置页的「系统自检」。
"""

from __future__ import annotations

import ctypes
import glob
import json
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import APP_ROOT, PROJECT_ROOT, Settings
from .encode import available_encoders, usable_encoders, video_args
from .utils import find_ffmpeg, read_json, run


# --------------------------------------------------------------------------- #
# 硬件与运行时
# --------------------------------------------------------------------------- #
def _ram_gb() -> float:
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        try:
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / 1024 ** 3, 1)
        except Exception:
            return 0.0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 ** 2, 1)
    except OSError:
        pass
    return 0.0


def _gpu() -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": "", "vram_gb": 0.0, "vendor": ""}
    exe = shutil.which("nvidia-smi")
    if not exe:
        return out
    try:
        proc = run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], timeout=6)
        line = (proc.stdout or "").strip().splitlines()[0]
        name, _, mem = line.rpartition(",")
        out.update({"name": name.strip(), "vram_gb": round(float(mem.strip()) / 1024, 1),
                    "vendor": "nvidia"})
    except Exception:
        pass
    return out


def _fonts() -> Dict[str, Any]:
    candidates: List[str] = []
    if os.name == "nt":
        win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        pairs = [("msyh.ttc", "Microsoft YaHei"), ("msyhbd.ttc", "Microsoft YaHei"),
                 ("simhei.ttf", "SimHei"), ("simsun.ttc", "SimSun")]
        for fn, name in pairs:
            if (win / fn).exists() and name not in candidates:
                candidates.append(name)
    else:
        try:
            proc = subprocess.run(["fc-list", ":lang=zh", "family"], capture_output=True,
                                  text=True, timeout=8)
            for line in (proc.stdout or "").splitlines():
                name = line.split(",")[0].strip()
                if name and name not in candidates:
                    candidates.append(name)
        except Exception:
            pass
    return {"cjk": candidates, "ok": bool(candidates)}


def _webview2() -> Dict[str, Any]:
    if os.name != "nt":
        return {"ok": True, "note": "非 Windows，跳过"}
    paths = glob.glob(r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application\*\msedgewebview2.exe")
    paths += glob.glob(r"C:\Program Files\Microsoft\EdgeWebView\Application\*\msedgewebview2.exe")
    if paths:
        return {"ok": True, "path": paths[0]}
    try:
        import winreg
        key = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            version = winreg.QueryValueEx(k, "pv")[0]
        return {"ok": True, "version": version}
    except Exception:
        return {"ok": False, "note": "未发现 WebView2，将退回浏览器模式"}


def _port_free(port: int = 8765) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _http_probe(url: str, timeout: float = 2.5) -> Dict[str, Any]:
    import requests

    session = requests.Session()
    session.trust_env = False
    try:
        r = session.get(url, timeout=timeout)
        return {"ok": r.status_code < 500, "status": r.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def probe(settings: Optional[Settings] = None) -> Dict[str, Any]:
    s = settings or Settings.load()
    p: Dict[str, Any] = {
        "os": {"system": platform.system(), "release": platform.release(),
               "machine": platform.machine(), "python": platform.python_version()},
        "cpu": {"cores": os.cpu_count() or 1, "name": platform.processor() or ""},
        "ram_gb": _ram_gb(),
        "disk_free_gb": round(shutil.disk_usage(APP_ROOT).free / 1024 ** 3, 1),
        "gpu": _gpu(),
        "fonts": _fonts(),
        "webview2": _webview2(),
        "port_8765_free": _port_free(),
    }

    ff: Dict[str, Any] = {"ok": False, "path": "", "encoders": []}
    try:
        ff["path"] = find_ffmpeg()
        enc = available_encoders()
        usable = usable_encoders()
        ff["ok"] = True
        ff["encoders"] = [e for e in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264", "aac") if e in enc]
        ff["usable"] = [e for e in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264") if e in usable]
        proc = run([ff["path"], "-hide_banner", "-filters"], timeout=20)
        filters = proc.stdout or ""
        ff["libass"] = " ass " in filters or filters.find(" ass ") > 0
        ff["xfade"] = " xfade " in filters
    except Exception as exc:
        ff["error"] = str(exc)
    p["ffmpeg"] = ff

    comfy = _http_probe(s.comfy.base_url.rstrip("/") + "/system_stats", timeout=2)
    p["comfyui"] = comfy

    video = {"mode": s.minimax.mode, "base_url": s.minimax.base_url, "cloud_ready": s.minimax.cloud_ready,
             "local_ready": s.minimax.local_ready, "workflow": s.minimax.video_workflow}
    if s.minimax.mode in ("local",) or (s.minimax.mode == "auto" and s.minimax.is_local):
        video["probe"] = _http_probe(s.minimax.base_url, timeout=2)
    p["video"] = video
    p["llm"] = {"mode": s.deepseek.mode, "base_url": s.deepseek.base_url,
                "cloud_ready": bool(s.deepseek.api_key), "local": s.deepseek.is_local}
    return p


# --------------------------------------------------------------------------- #
# 推荐配置
# --------------------------------------------------------------------------- #
def recommend(p: Dict[str, Any], s: Settings) -> Dict[str, Any]:
    gpu = p.get("gpu") or {}
    vram = float(gpu.get("vram_gb") or 0)
    ram = float(p.get("ram_gb") or 0)
    cores = int((p.get("cpu") or {}).get("cores") or 2)
    encoders = (p.get("ffmpeg") or {}).get("usable") or ["libx264"]
    fonts = (p.get("fonts") or {}).get("cjk") or []

    render: Dict[str, Any] = {}
    if "h264_nvenc" in encoders:
        render["encoder"] = "h264_nvenc"
    elif "h264_qsv" in encoders:
        render["encoder"] = "h264_qsv"
    elif "h264_amf" in encoders:
        render["encoder"] = "h264_amf"
    else:
        render["encoder"] = "libx264"
        render["preset"] = "veryfast" if cores <= 8 else "medium"

    # 低配机器自动降到 720p，别让用户对着 10 分钟的合成进度条发呆
    if ram and ram < 8:
        render.update({"width": 1280, "height": 720, "crf": 21})
    if fonts:
        render["subtitle_font"] = fonts[0]

    comfy: Dict[str, Any] = {}
    if vram >= 16:
        comfy.update({"width": 1536, "height": 864})
    elif vram >= 10:
        comfy.update({"width": 1344, "height": 768})
    elif vram >= 6:
        comfy.update({"width": 1152, "height": 640})
    else:
        comfy.update({"width": 1024, "height": 576})
    # 视频模型（MiniMax H3 / WAN 等）按 32 对齐，SD 系按 8 即可；
    # 统一取 32 的倍数，免得自动配置给出 648 这种工作流会直接报错的尺寸
    for key in ("width", "height"):
        comfy[key] = max(256, int(round(comfy[key] / 32)) * 32)

    local_video = (s.minimax.mode == "local") or bool(s.minimax.video_workflow)
    workers = 1 if local_video else max(2, min(4, cores // 2 or 2))

    return {
        "render": render,
        "comfy": comfy,
        "pipeline": {"max_workers": workers},
        "minimax": {"resolution": "768P" if local_video else s.minimax.resolution},
    }


def _merge_config(updates: Dict[str, Dict[str, Any]], p: Dict[str, Any]) -> None:
    path = PROJECT_ROOT / "config.json"
    data = read_json(path, default={}) or {}
    for section, values in updates.items():
        data.setdefault(section, {}).update(values)
    data.setdefault("_meta", {})
    data["_meta"].update({
        "auto_configured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hardware": {"cpu_cores": p["cpu"]["cores"], "ram_gb": p["ram_gb"],
                     "gpu": p["gpu"].get("name", ""), "vram_gb": p["gpu"].get("vram_gb", 0),
                     "encoder": updates.get("render", {}).get("encoder", "")},
    })
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_first_run() -> bool:
    data = read_json(PROJECT_ROOT / "config.json", default={}) or {}
    return not (data.get("_meta") or {}).get("auto_configured_at")


def auto_configure(settings: Optional[Settings] = None, *, force: bool = False) -> Dict[str, Any]:
    s = settings or Settings.load()
    if not force and not is_first_run():
        return {"skipped": True, "reason": "已自动配置过"}
    p = probe(s)
    rec = recommend(p, s)
    _merge_config(rec, p)
    return {"skipped": False, "probe": p, "applied": rec}


def doctor_summary() -> Dict[str, Any]:
    s = Settings.load()
    p = probe(s)
    p["recommend"] = recommend(p, s)
    p["encoder_resolved"] = video_args(s.render, 20)[1]
    p["first_run"] = is_first_run()
    return p
