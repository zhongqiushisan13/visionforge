"""本地出图环境：检测 ComfyUI / 底模 / IP-Adapter，并支持一键安装。

为什么不在安装包里塞模型：
- GitHub Release 单资产上限 2GB，而 SDXL + CLIP + IP-Adapter 约 9.7GB；
- ComfyUI 是 GPL-3.0，捆绑进闭源包会带来额外义务；
- 每次发版都要用户重下 10GB。

所以做成三件事：**能检测、能一键装、可以不装**（云端 H3 与占位片始终可用）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .config import Settings
from .utils import ensure_dir, log

HF = "https://huggingface.co"
MIRROR = "https://hf-mirror.com"
GH_PROXY = "https://gh-proxy.com/"

# dest 相对 ComfyUI 根目录；sha256 为 None 时只校验大小与文件头
CATALOG: Dict[str, Dict[str, Any]] = {
    "sdxl-base": {
        "label": "SDXL 底模 1.0（0.9vae）",
        "tier": "standard",
        "kind": "model",
        "size": 6938078334,
        "sha256": "e6bb9ea85bbf7bf6478a7c6d18b71246f22e95d41bcdd80ed40aa212c33cfeff",
        "dest": "models/checkpoints/sd_xl_base_1.0_0.9vae.safetensors",
        "probe": ("checkpoint", "sd_xl_base_1.0_0.9vae.safetensors"),
        "urls": [
            f"{MIRROR}/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0_0.9vae.safetensors",
            f"{HF}/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0_0.9vae.safetensors",
        ],
    },
    "clip-vit-h": {
        "label": "CLIP ViT-H 图像编码器",
        "tier": "standard",
        "kind": "model",
        "size": 2528373448,
        "sha256": "6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030",
        "dest": "models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
        "probe": ("clip", "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"),
        "urls": [
            f"{MIRROR}/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors",
            f"{HF}/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors",
        ],
    },
    "ipa-sdxl": {
        "label": "IP-Adapter plus（SDXL）",
        "tier": "standard",
        "kind": "model",
        "size": 847517512,
        "sha256": "3f5062b8400c94b7159665b21ba5c62acdcd7682262743d7f2aefedef00e6581",
        "dest": "models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors",
        "probe": ("ipadapter", "ip-adapter-plus_sdxl_vit-h.safetensors"),
        "urls": [
            f"{MIRROR}/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors",
            f"{HF}/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors",
        ],
    },
    "ipa-node": {
        "label": "IP-Adapter 节点（ComfyUI_IPAdapter_plus）",
        "tier": "standard",
        "kind": "zip",
        "size": 303104,
        "sha256": None,
        "dest": "custom_nodes/ComfyUI_IPAdapter_plus",
        "probe": ("node", "IPAdapterAdvanced"),
        "urls": [
            f"{GH_PROXY}https://github.com/cubiq/ComfyUI_IPAdapter_plus/archive/refs/heads/main.zip",
            "https://github.com/cubiq/ComfyUI_IPAdapter_plus/archive/refs/heads/main.zip",
        ],
    },
    "sd15-base": {
        "label": "SD1.5 底模（轻量档）",
        "tier": "light",
        "kind": "model",
        "size": 2132626808,
        "sha256": None,
        "dest": "models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors",
        "probe": ("checkpoint", "v1-5-pruned-emaonly-fp16.safetensors"),
        "urls": [
            f"{MIRROR}/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly-fp16.safetensors",
            f"{HF}/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly-fp16.safetensors",
        ],
    },
    "ipa-sd15": {
        "label": "IP-Adapter（SD1.5，轻量档）",
        "tier": "light",
        "kind": "model",
        "size": 44278012,
        "sha256": None,
        "dest": "models/ipadapter/ip-adapter_sd15.safetensors",
        "probe": ("ipadapter", "ip-adapter_sd15.safetensors"),
        "urls": [
            f"{MIRROR}/h94/IP-Adapter/resolve/main/models/ip-adapter_sd15.safetensors",
            f"{HF}/h94/IP-Adapter/resolve/main/models/ip-adapter_sd15.safetensors",
        ],
    },
}

STANDARD = ("sdxl-base", "clip-vit-h", "ipa-sdxl", "ipa-node")


# --------------------------------------------------------------------------- #
# 检测
# --------------------------------------------------------------------------- #
def _lists(provider) -> Dict[str, List[str]]:
    out = {"checkpoint": [], "clip": [], "ipadapter": [], "node": []}
    try:
        info = provider._info()
    except Exception:
        return out
    try:
        out["checkpoint"] = list(info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0])
    except Exception:
        pass
    try:
        out["clip"] = list(info["CLIPVisionLoader"]["input"]["required"]["clip_name"][0])
    except Exception:
        pass
    try:
        out["ipadapter"] = list(info["IPAdapterModelLoader"]["input"]["required"]["ipadapter_file"][0])
    except Exception:
        pass
    out["node"] = [k for k in ("IPAdapterAdvanced", "IPAdapterUnifiedLoader") if k in info]
    return out


def status(settings: Settings, provider=None) -> Dict[str, Any]:
    from .providers.comfyui import ComfyUIProvider

    prov = provider or ComfyUIProvider(settings)
    url = settings.comfy.base_url
    reachable, version, error = False, "", ""
    try:
        stats = prov.verify()
        reachable = True
        version = str((stats.get("system") or {}).get("comfyui_version") or "")
    except Exception as exc:
        error = str(exc)

    lists = _lists(prov) if reachable else {"checkpoint": [], "clip": [], "ipadapter": [], "node": []}
    items = []
    for item_id, meta in CATALOG.items():
        kind, name = meta["probe"]
        ready = name in (lists.get(kind) or []) if kind != "node" else bool(lists.get("node"))
        items.append({
            "id": item_id, "label": meta["label"], "tier": meta["tier"],
            "kind": meta["kind"], "size": meta["size"], "ready": ready,
            "dest": meta["dest"],
        })

    root = settings.comfy.root
    root_ok = bool(root) and (Path(root) / "models").is_dir()
    standard_ready = all(i["ready"] for i in items if i["id"] in STANDARD)
    light_ready = all(i["ready"] for i in items if i["id"] in ("sd15-base", "clip-vit-h", "ipa-sd15", "ipa-node"))
    disk_free = 0
    if root_ok:
        try:
            disk_free = shutil.disk_usage(root).free
        except OSError:
            pass
    return {
        "comfy": {"url": url, "reachable": reachable, "version": version, "error": error},
        "root": {"path": root, "valid": root_ok},
        "workflows": {
            "dir": str(settings.workflows_dir),
            "image": settings.comfy.workflow,
            "video": settings.minimax.video_workflow,
        },
        "items": items,
        "ready": {"standard": standard_ready, "light": light_ready},
        "disk_free": disk_free,
        "tiers": {
            "standard": [i for i in STANDARD],
            "light": ["sd15-base", "clip-vit-h", "ipa-sd15", "ipa-node"],
        },
    }


def set_root(settings: Settings, root: str) -> Dict[str, Any]:
    """校验并记住 ComfyUI 目录（只认含 models 的目录，或含 main.py 的目录）。"""
    p = Path(root).expanduser()
    if not p.is_dir():
        raise ValueError(f"目录不存在：{p}")
    if not (p / "models").is_dir() and (p / "ComfyUI" / "models").is_dir():
        p = p / "ComfyUI"
    if not (p / "models").is_dir():
        raise ValueError("这个目录下没有 models/，选 ComfyUI 主目录（含 main.py 和 models/）")
    settings.comfy.root = str(p)
    return {"root": str(p)}


# --------------------------------------------------------------------------- #
# 下载与校验
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path: Path, meta: Dict[str, Any]) -> bool:
    if not path.exists():
        return False
    if meta.get("size") and path.stat().st_size != meta["size"]:
        return False
    if meta.get("sha256"):
        return _sha256(path) == meta["sha256"]
    head = path.open("rb").read(8)
    return path.stat().st_size > 1024 and not head.startswith(b"<!")


def _download_once(url: str, part: Path, meta: Dict[str, Any], job=None) -> bool:
    pos = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={pos}-"} if pos else {}
    with requests.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as r:
        if r.status_code == 416:                     # 已经下完
            return True
        if r.status_code not in (200, 206):
            raise RuntimeError(f"HTTP {r.status_code}")
        if r.status_code == 200 and pos:             # 服务端不支持续传，重来
            pos = 0
            part.unlink(missing_ok=True)
        total = int(r.headers.get("Content-Length") or 0) + pos
        mode = "ab" if pos else "wb"
        done, t0, last = pos, time.time(), 0.0
        with part.open(mode) as fh:
            for chunk in r.iter_content(1 << 20):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                now = time.time()
                if job and now - last > 2:
                    pct = (done / total * 100) if total else 0
                    speed = (done - pos) / max(0.1, now - t0) / 1e6
                    job.add_log(f"下载中 {pct:.0f}%  {done/1e6:.0f}/{total/1e6:.0f} MB  {speed:.1f} MB/s")
                    last = now
    return True


def fetch(item_id: str, root: Path, job=None, mirror: bool = True) -> Path:
    meta = CATALOG[item_id]
    urls = list(meta["urls"])
    if not mirror:
        urls.reverse()
    dest = Path(root) / meta["dest"]
    if meta["kind"] == "zip":
        dest = Path(root) / "custom_nodes"
        target = Path(root) / meta["dest"]
        if (target / "__init__.py").exists():
            log(f"{item_id} 已安装，跳过")
            return target
        tmp_zip = Path(root) / "custom_nodes" / "_ipa_node.zip"
        _fetch_file(item_id, meta, urls, tmp_zip, job)
        ensure_dir(dest)
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(dest)
        inner = next(p for p in dest.iterdir() if p.is_dir() and p.name.lower().startswith("comfyui_ipadapter"))
        if inner != target:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            inner.rename(target)
        tmp_zip.unlink(missing_ok=True)
        return target
    ensure_dir(dest.parent)
    return _fetch_file(item_id, meta, urls, dest, job)


def _fetch_file(item_id: str, meta: Dict[str, Any], urls: List[str], dest: Path, job=None) -> Path:
    if verify_file(dest, meta):
        log(f"{item_id} 已就绪，跳过下载")
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    last_err: Optional[Exception] = None
    for attempt in range(3):
        for url in urls:
            try:
                if job:
                    job.add_log(f"开始下载 {meta['label']}：{url.split('/')[2]}")
                _download_once(url, part, meta, job)
                if verify_file(part, meta):
                    part.replace(dest)
                    if job:
                        job.add_log(f"下载完成 {meta['label']}（{dest.stat().st_size/1e9:.2f} GB）")
                    return dest
                last_err = RuntimeError("校验没通过（大小或哈希不符），可能下载不完整")
            except Exception as exc:
                last_err = exc
                if job:
                    job.add_log(f"这次没成：{exc}，{attempt + 1}/3 换源重试")
                time.sleep(2)
    raise RuntimeError(f"{meta['label']} 下载失败：{last_err}")


def import_local(item_id: str, root: Path, source: Path, job=None) -> Path:
    """把用户自己下好的文件放进 ComfyUI 目录（离线部署用）。"""
    meta = CATALOG[item_id]
    source = Path(source)
    if not source.exists():
        raise ValueError(f"文件不存在：{source}")
    if not verify_file(source, meta):
        raise ValueError("文件大小或哈希对不上，确认下的是正确的版本")
    if meta["kind"] == "zip":
        dest = Path(root) / "custom_nodes"
        ensure_dir(dest)
        with zipfile.ZipFile(source) as zf:
            zf.extractall(dest)
        return dest
    dest = Path(root) / meta["dest"]
    ensure_dir(dest.parent)
    shutil.copy2(source, dest)
    if job:
        job.add_log(f"已导入 {meta['label']}")
    return dest


def install(settings: Settings, item_ids: List[str], job=None, mirror: bool = True) -> Dict[str, Any]:
    root = settings.comfy.root
    if not root or not (Path(root) / "models").is_dir():
        raise ValueError("还没设置 ComfyUI 目录，先在设置页选一下")
    done, failed = [], []
    for item_id in item_ids:
        if item_id not in CATALOG:
            failed.append({"id": item_id, "error": "未知组件"})
            continue
        try:
            fetch(item_id, Path(root), job=job, mirror=mirror)
            done.append(item_id)
        except Exception as exc:
            failed.append({"id": item_id, "error": str(exc)[:300]})
            if job:
                job.add_log(f"失败 {item_id}：{exc}")
    return {"ok": done, "failed": failed}


# --------------------------------------------------------------------------- #
# 手动控制：启动 / 停止 / 打开 ComfyUI 自己的界面
# --------------------------------------------------------------------------- #
def ui_url(settings: Settings) -> str:
    """ComfyUI 自带的 Web 界面地址（和 VisionForge 是两个页面，端口不同）。"""
    return settings.comfy.base_url.rstrip("/")


def _port_of(settings: Settings) -> int:
    raw = ui_url(settings)
    tail = raw.rsplit(":", 1)[-1].split("/")[0]
    try:
        return int(tail)
    except ValueError:
        return 8188


def comfy_main(root: str) -> Optional[Path]:
    """ComfyUI 主目录可能是 ComfyUI/ 本身，也可能是它的上一层。"""
    if not root:
        return None
    base = Path(root)
    for cand in (base / "main.py", base / "ComfyUI" / "main.py"):
        if cand.exists():
            return cand
    return None


def comfy_python(root: str) -> Optional[str]:
    """优先用便携包自带的 python_embeded，其次系统 Python。"""
    if root:
        base = Path(root)
        for cand in (base.parent / "python_embeded" / "python.exe",
                     base / "python_embeded" / "python.exe",
                     base.parent / "python_embeded" / "python",
                     base / "venv" / "Scripts" / "python.exe"):
            if cand.exists():
                return str(cand)
    return shutil.which("python") or shutil.which("python3")


def _netstat_pid(port: int) -> Optional[int]:
    """找监听该端口的进程（Windows 用 netstat，其它平台交给 psutil 缺省不查）。"""
    if os.name != "nt":
        return None
    try:
        # 别用 text=True：中文系统的 netstat 是 GBK，按 UTF-8 解码会直接抛异常
        raw = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=20).stdout or b""
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return None
    for line in text.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}"):
            try:
                return int(parts[-1])
            except ValueError:
                continue
    return None


def launch(settings: Settings, *, wait: float = 0.0) -> Dict[str, Any]:
    """拉起 ComfyUI；已经在跑就只回报地址，不重复启动。"""
    from .providers.comfyui import ComfyUIProvider

    url = ui_url(settings)
    try:
        ComfyUIProvider(settings).verify()
        return {"ok": True, "already": True, "url": url}
    except Exception:
        pass

    main = comfy_main(settings.comfy.root)
    if main is None:
        raise ValueError("没有找到 ComfyUI 主目录，先在设置页指定 ComfyUI 路径")
    py = comfy_python(settings.comfy.root)
    if not py:
        raise ValueError("找不到可用的 Python，无法启动 ComfyUI（便携包应带 python_embeded）")

    port = _port_of(settings)
    logfile = ensure_dir(main.parent / "logs") / "comfyui-launch.log"
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    env["PYTHONIOENCODING"] = "utf-8"

    args = [py, "-s", str(main), "--port", str(port), "--listen", "127.0.0.1",
            "--preview-method", "none"]
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | NEW_PROCESS_GROUP
    with open(logfile, "ab") as fh:
        proc = subprocess.Popen(args, cwd=str(main.parent), env=env,
                                stdout=fh, stderr=subprocess.STDOUT,
                                close_fds=True, creationflags=flags)
    log(f"ComfyUI 已启动 pid={proc.pid}，日志 {logfile}", tag="comfy")

    deadline = time.time() + max(0.0, wait)
    from .providers.comfyui import ComfyUIProvider
    while time.time() < deadline:
        try:
            ComfyUIProvider(settings).verify()
            return {"ok": True, "started": True, "pid": proc.pid, "url": url}
        except Exception:
            time.sleep(2)
    return {"ok": True, "started": True, "pid": proc.pid, "url": url, "warming": True}


def stop(settings: Settings) -> Dict[str, Any]:
    """按端口找到 ComfyUI 进程并结束；不是我们启动的也能停。"""
    port = _port_of(settings)
    pid = _netstat_pid(port)
    if not pid:
        return {"ok": True, "stopped": False, "reason": f"端口 {port} 上没有进程"}
    if os.name != "nt":
        raise ValueError("当前系统暂不支持一键停止，请手动结束 ComfyUI 进程")
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True, timeout=30)
    time.sleep(1.5)
    return {"ok": True, "stopped": True, "pid": pid}
