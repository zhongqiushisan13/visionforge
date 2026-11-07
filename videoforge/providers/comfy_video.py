"""ComfyUI 本地视频工作流：给「本地部署出片」准备的第二条路。

不绑定具体模型（WAN / Hunyuan / LTX / SVD 都能用），靠节点标题定位：
  POSITIVE / NEGATIVE → 提示词
  含 width+height 的节点 → 分辨率
  含 length / num_frames / video_frames 的节点 → 帧数
  SAMPLER → seed / steps / cfg
  LOAD_IMAGE → 首帧（会自动上传关键帧）
  SAVE → 输出（SaveVideo / VHS_VideoCombine 的 `gifs` 列表也认）
"""

from __future__ import annotations

import copy
import time

import requests
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import ProviderError, ProviderResult
from .comfyui import SAVE_CLASSES, ComfyUIProvider
from ..config import Settings
from ..schemas import Shot
from ..utils import ensure_dir, log, probe_duration, warn

FRAME_KEYS = ("length", "num_frames", "video_frames", "frames", "frame_count")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".gif")


class ComfyVideoProvider(ComfyUIProvider):
    name = "comfyui-video"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.video_cfg = settings.minimax
        self.workflow_name = self.video_cfg.video_workflow

    # ---------------- 探活 ---------------- #
    def probe(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"ok": False, "workflow": self.workflow_name}
        try:
            self.verify()
            out["ok"] = True
        except Exception as exc:
            out["error"] = str(exc)
        path = Path(self.workflow_name)
        if not path.is_absolute() and self.workflow_name:
            path = self.s.workflows_dir / self.workflow_name
        out["workflow_exists"] = bool(self.workflow_name) and path.exists()
        return out

    # ---------------- 上传首帧 ---------------- #
    def upload_image(self, path: Path) -> str:
        url = self.cfg.base_url.rstrip("/") + "/upload/image"
        with open(path, "rb") as fh:
            files = {"image": (path.name, fh.read(), "image/png")}
        data = {"overwrite": "true", "type": "input"}
        try:
            r = self.session.post(url, files=files, data=data, timeout=60)
            r.raise_for_status()
            info = r.json()
        except Exception as exc:
            raise ProviderError(f"上传首帧到 ComfyUI 失败：{exc}", retryable=True) from exc
        sub = info.get("subfolder") or ""
        name = info.get("name") or path.name
        return f"{sub}/{name}" if sub else name

    # ---------------- 工作流补丁 ---------------- #
    def _find(self, wf: Dict[str, Any], title: str) -> Optional[str]:
        for nid, node in wf.items():
            if isinstance(node, dict) and str((node.get("_meta") or {}).get("title", "")).upper() == title:
                return nid
        return None

    def patch_workflow(self, shot: Shot, keyframe: Optional[Path]) -> tuple[Dict[str, Any], int]:
        if not self.workflow_name:
            raise ProviderError("没有配置 ComfyUI 视频工作流（MINIMAX_VIDEO_WORKFLOW）", retryable=False)
        wf = copy.deepcopy(self.load_workflow(self.workflow_name))
        fps = max(1, self.s.render.fps)
        frames = max(8, int(shot.duration * fps))
        seed = shot.seed or int(time.time() * 1000) % 2_147_483_647

        pos, neg = self._find(wf, "POSITIVE"), self._find(wf, "NEGATIVE")
        for nid, text in ((pos, shot.video_prompt or shot.image_prompt),
                          (neg, shot.negative_prompt)):
            if nid and nid in wf:
                wf[nid].setdefault("inputs", {})["text"] = text

        for node in wf.values():
            if not isinstance(node, dict):
                continue
            cls = node.get("class_type", "")
            inputs = node.setdefault("inputs", {})
            if cls in ("KSampler", "KSamplerAdvanced"):
                inputs["seed"] = seed
                if "steps" in inputs:
                    inputs["steps"] = max(8, int(self.cfg.steps))
                if "cfg" in inputs:
                    inputs["cfg"] = self.cfg.cfg
            if "width" in inputs and "height" in inputs:
                inputs["width"] = self.cfg.width
                inputs["height"] = self.cfg.height
            for key in FRAME_KEYS:
                if key in inputs and isinstance(inputs[key], (int, float)):
                    inputs[key] = frames
            if cls == "LoadImage" and keyframe and Path(keyframe).exists():
                inputs["image"] = self.upload_image(Path(keyframe))
            if cls in SAVE_CLASSES or cls in ("SaveVideo", "VHS_VideoCombine", "SaveAnimatedWEBP"):
                if "frame_rate" in inputs:
                    inputs["frame_rate"] = fps
                if "fps" in inputs:
                    inputs["fps"] = fps
        return wf, seed

    def wait_video(self, prompt_id: str, shot_id: str = "") -> Dict[str, Any]:
        deadline = time.time() + max(120, int(self.video_cfg.video_timeout))
        while time.time() < deadline:
            try:
                r = self.session.get(f"{self.cfg.base_url.rstrip('/')}/history/{prompt_id}", timeout=30)
                data = r.json() or {}
                if prompt_id in data:
                    entry = data[prompt_id]
                    status = entry.get("status") or {}
                    if str(status.get("status_str") or "").lower() == "error":
                        raise ProviderError(f"ComfyUI 执行失败：{str(status)[:400]}", retryable=False)
                    return entry
            except requests.RequestException as exc:
                warn(f"{shot_id} 轮询 ComfyUI 抖动：{exc}")
            time.sleep(max(1.0, self.cfg.poll_interval))
        raise ProviderError(f"ComfyUI 视频超时（{self.video_cfg.video_timeout}s）", retryable=True)

    # ---------------- 下载 ---------------- #
    def _fetch_video(self, entry: Dict[str, Any], out_path: Path) -> Path:
        items: List[dict] = []
        for node_out in (entry.get("outputs") or {}).values():
            for key in ("gifs", "videos", "images"):
                items.extend(node_out.get(key) or [])
        videos = [x for x in items if str(x.get("filename", "")).lower().endswith(VIDEO_EXT)]
        if not videos:
            raise ProviderError(f"ComfyUI 跑完了但没有视频输出：{str(entry.get('outputs'))[:300]}",
                                retryable=False)
        item = videos[0]
        params = {"filename": item.get("filename", ""), "subfolder": item.get("subfolder", ""),
                  "type": item.get("type", "output")}
        r = self.session.get(self.cfg.base_url.rstrip("/") + "/view", params=params, timeout=300)
        r.raise_for_status()
        ensure_dir(out_path.parent)
        out_path.write_bytes(r.content)
        return out_path

    # ---------------- 对外 ---------------- #
    def generate(self, shot: Shot, out_path: Path, keyframe: Optional[Path] = None) -> ProviderResult:
        wf, seed = self.patch_workflow(shot, keyframe)
        pid = self.queue(wf)
        log(f"{shot.id} 视频工作流入队 prompt_id={pid}", tag="comfy")
        entry = self.wait_video(pid, shot.id)
        self._fetch_video(entry, Path(out_path))
        dur = probe_duration(out_path) or shot.duration
        log(f"{shot.id} 本地视频完成 {Path(out_path).name} ({dur:.1f}s)", tag="comfy")
        return ProviderResult(path=str(out_path), provider="comfyui-video", task_id=pid,
                              duration=dur, seed=seed, meta={"workflow": self.workflow_name})


def build_video_provider(settings: Settings) -> Optional[ComfyVideoProvider]:
    cfg = settings.minimax
    if not cfg.video_workflow:
        return None
    return ComfyVideoProvider(settings)
