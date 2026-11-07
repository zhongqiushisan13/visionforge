"""ComfyUI 适配器：API 格式工作流 + LoRA 自动注入 + 队列轮询 + 出图下载。

工作流按 `class_type` 和 `_meta.title` 定位节点，不依赖硬编码节点 id，
所以从 ComfyUI 导出的任何 txt2img 工作流（SDXL / Flux / SD1.5）都能直接用。
文件里顶层的 `_vf` 是元信息，提交前会被剔除，不会污染 prompt 图。
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .base import ProviderError, ProviderResult
from ..config import ComfyConfig, Settings
from ..schemas import Shot
from ..utils import ensure_dir, log, read_json, warn

LORA_CLASSES = ("LoraLoader", "LoraLoaderModelOnly")
SAVE_CLASSES = ("SaveImage", "PreviewImage", "Image Save")


class ComfyUIProvider:
    name = "comfyui"
    supports_keyframe = True

    def __init__(self, settings: Settings):
        self.s = settings
        self.cfg: ComfyConfig = settings.comfy
        self.session = requests.Session()
        self.session.trust_env = False      # 本地服务绝不走系统代理
        self.client_id = str(uuid.uuid4())
        self._object_info: Optional[Dict[str, Any]] = None

    # ---------------- 探活 ---------------- #
    def verify(self) -> Dict[str, Any]:
        try:
            r = self.session.get(self.cfg.base_url.rstrip("/") + "/system_stats", timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            raise ProviderError(f"ComfyUI 连不上（{self.cfg.base_url}）：{exc}", retryable=False) from exc

    def _info(self) -> Dict[str, Any]:
        if self._object_info is None:
            r = self.session.get(self.cfg.base_url.rstrip("/") + "/object_info", timeout=30)
            r.raise_for_status()
            self._object_info = r.json()
        return self._object_info

    def list_loras(self) -> List[str]:
        try:
            data = self._info()
            specs = data.get("LoraLoader", {}).get("input", {}).get("required", {})
            items = specs.get("lora_name", [[]])[0]
            return list(items) if isinstance(items, list) else []
        except Exception as exc:
            warn(f"读取 LoRA 列表失败：{exc}")
            return []

    def list_checkpoints(self) -> List[str]:
        try:
            data = self._info()
            specs = data.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {})
            items = specs.get("ckpt_name", [[]])[0]
            return list(items) if isinstance(items, list) else []
        except Exception:
            return []

    # ---------------- 工作流 ---------------- #
    def load_workflow(self, name: str = "") -> Dict[str, Any]:
        name = name or self.cfg.workflow
        path = Path(name)
        if not path.is_absolute():
            path = self.s.workflows_dir / name
        wf = read_json(path)
        if not wf:
            raise ProviderError(f"工作流不存在或不是合法 JSON：{path}", retryable=False)
        wf.pop("_vf", None)
        return wf

    @staticmethod
    def _nodes_by_class(wf: Dict[str, Any], classes: tuple) -> List[str]:
        return [nid for nid, node in wf.items()
                if isinstance(node, dict) and node.get("class_type") in classes]

    @staticmethod
    def _role_from_meta(wf: Dict[str, Any], title: str) -> Optional[str]:
        for nid, node in wf.items():
            if isinstance(node, dict) and str((node.get("_meta") or {}).get("title", "")).upper() == title:
                return nid
        return None

    def _resolve_roles(self, wf: Dict[str, Any]) -> Dict[str, Optional[str]]:
        pos = self._role_from_meta(wf, "POSITIVE")
        neg = self._role_from_meta(wf, "NEGATIVE")
        clips = self._nodes_by_class(wf, ("CLIPTextEncode",))
        # 没有 title 标记时按 id 顺序猜：第一个正、第二个负
        if not pos and clips:
            pos = clips[0]
        if not neg and len(clips) > 1:
            neg = clips[1]
        return {
            "positive": pos,
            "negative": neg,
            "latent": (self._nodes_by_class(wf, ("EmptyLatentImage", "EmptySD3LatentImage")) or [None])[0],
            "sampler": (self._nodes_by_class(wf, ("KSampler", "KSamplerAdvanced")) or [None])[0],
            "ckpt": (self._nodes_by_class(wf, ("CheckpointLoaderSimple",)) or [None])[0],
            "save": (self._nodes_by_class(wf, SAVE_CLASSES) or [None])[0],
            "lora": (self._nodes_by_class(wf, LORA_CLASSES) or [None])[0],
        }

    def inject_lora(self, wf: Dict[str, Any], lora_name: str, strength: float) -> str:
        """把 LoRA 节点插到 checkpoint 与所有下游 model/clip 消费者之间。"""
        roles = self._resolve_roles(wf)
        if roles["lora"]:
            node = wf[roles["lora"]]
            node.setdefault("inputs", {})
            node["inputs"]["lora_name"] = lora_name
            if "strength_model" in node["inputs"] or node["class_type"] == "LoraLoader":
                node["inputs"]["strength_model"] = strength
            if node["class_type"] == "LoraLoader":
                node["inputs"]["strength_clip"] = strength
            log(f"LoRA 已套用到节点 {roles['lora']}：{lora_name} @ {strength}", tag="comfy")
            return roles["lora"]

        ckpt = roles["ckpt"]
        if not ckpt:
            raise ProviderError("工作流里没有 CheckpointLoaderSimple，无法自动注入 LoRA", retryable=False)

        new_id = "vf_lora"
        while new_id in wf:
            new_id += "_"
        wf[new_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": lora_name,
                "strength_model": strength,
                "strength_clip": strength,
                "model": [ckpt, 0],
                "clip": [ckpt, 1],
            },
            "_meta": {"title": "VF_LORA"},
        }
        # 所有引用 ckpt 输出的地方改指向 LoRA
        for nid, node in wf.items():
            if nid in (new_id, ckpt) or not isinstance(node, dict):
                continue
            for key, val in list((node.get("inputs") or {}).items()):
                if isinstance(val, list) and len(val) == 2 and val[0] == ckpt:
                    node["inputs"][key] = [new_id, val[1]]
        log(f"LoRA 节点已注入：{lora_name} @ {strength}（上游 {ckpt}）", tag="comfy")
        return new_id

    def build_prompt(self, shot: Shot) -> tuple[Dict[str, Any], int]:
        wf = copy.deepcopy(self.load_workflow())
        roles = self._resolve_roles(wf)
        seed = shot.seed or (int(uuid.uuid4().int % 2_147_483_647))

        if roles["positive"]:
            wf[roles["positive"]].setdefault("inputs", {})["text"] = shot.image_prompt
        if roles["negative"] and shot.negative_prompt:
            wf[roles["negative"]].setdefault("inputs", {})["text"] = shot.negative_prompt

        if roles["ckpt"] and self.cfg.checkpoint:
            wf[roles["ckpt"]].setdefault("inputs", {})["ckpt_name"] = self.cfg.checkpoint

        if roles["latent"]:
            wf[roles["latent"]].setdefault("inputs", {}).update({
                "width": self.cfg.width, "height": self.cfg.height,
                "batch_size": max(1, self.cfg.batch),
            })

        if roles["sampler"]:
            inp = wf[roles["sampler"]].setdefault("inputs", {})
            inp["seed"] = seed
            inp["steps"] = self.cfg.steps
            inp["cfg"] = self.cfg.cfg
            inp["sampler_name"] = self.cfg.sampler
            if "scheduler" in inp:
                inp["scheduler"] = self.cfg.scheduler
            inp["denoise"] = 1.0

        if roles["save"]:
            wf[roles["save"]].setdefault("inputs", {})["filename_prefix"] = f"vf_{shot.id}"

        lora = shot.lora or self.cfg.default_lora
        if lora:
            self.inject_lora(wf, lora, shot.lora_strength or self.cfg.lora_strength)

        return wf, seed

    # ---------------- 队列 ---------------- #
    def queue(self, wf: Dict[str, Any]) -> str:
        payload = {"prompt": wf, "client_id": self.client_id}
        try:
            r = self.session.post(self.cfg.base_url.rstrip("/") + "/prompt", json=payload, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"提交工作流失败（ComfyUI 没开？）：{exc}", retryable=True) from exc
        if r.status_code >= 400:
            detail = r.text[:800]
            try:
                data = r.json()
                node_errors = data.get("node_errors") or {}
                msgs = []
                for nid, err in node_errors.items():
                    cls = (err.get("class_type") or nid)
                    for e in err.get("errors", []):
                        msgs.append(f"[{cls}] {e.get('message')} {e.get('details','')}")
                if msgs:
                    detail = "；".join(msgs)
                elif data.get("error"):
                    detail = str(data["error"].get("message") or data["error"])
            except Exception:
                pass
            raise ProviderError(f"ComfyUI 拒绝工作流：{detail}", retryable=False)
        data = r.json()
        pid = data.get("prompt_id")
        if not pid:
            raise ProviderError(f"ComfyUI 没返回 prompt_id：{data}", retryable=True)
        return str(pid)

    def wait(self, prompt_id: str, shot_id: str = "") -> Dict[str, Any]:
        deadline = time.time() + self.cfg.timeout
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
            time.sleep(self.cfg.poll_interval)
        raise ProviderError(f"ComfyUI 出图超时（{self.cfg.timeout}s，prompt_id={prompt_id}）")

    def _fetch_image(self, entry: Dict[str, Any], out_path: Path) -> Path:
        images: List[dict] = []
        for node_out in (entry.get("outputs") or {}).values():
            images.extend(node_out.get("images") or [])
        if not images:
            raise ProviderError("ComfyUI 跑完了但没产出图片，检查 SaveImage 节点", retryable=False)
        img = images[0]
        params = {"filename": img.get("filename", ""), "subfolder": img.get("subfolder", ""),
                  "type": img.get("type", "output")}
        r = self.session.get(self.cfg.base_url.rstrip("/") + "/view", params=params, timeout=120)
        r.raise_for_status()
        ensure_dir(out_path.parent)
        out_path.write_bytes(r.content)
        return out_path

    # ---------------- 对外 ---------------- #
    def generate_keyframe(self, shot: Shot, out_path: Path) -> ProviderResult:
        wf, seed = self.build_prompt(shot)
        pid = self.queue(wf)
        log(f"{shot.id} 关键帧入队 prompt_id={pid} seed={seed}", tag="comfy")
        entry = self.wait(pid, shot.id)
        self._fetch_image(entry, Path(out_path))
        log(f"{shot.id} 关键帧已出图 {Path(out_path).name}", tag="comfy")
        return ProviderResult(path=str(out_path), provider="comfyui", task_id=pid, seed=seed,
                              meta={"lora": shot.lora or self.cfg.default_lora,
                                    "workflow": self.cfg.workflow})
