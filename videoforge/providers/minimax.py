"""MiniMax H3 视频生成适配器。

协议要点（platform.minimax.io）：
  POST /v2/video_generation            创建异步任务 → {"task_id": "..."}
  GET  /v2/query/video_generation/{id} 轮询 → task.status / task.content.url
  content[] 多模态数组：{"type":"text"} 必有；首帧图 {"type":"image_url","role":"first_frame"}
文生视频必须给 ratio；图生视频 ratio 恒为 adaptive（不传）。
"""

from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .base import ProviderError, ProviderResult
from ..config import MiniMaxConfig
from ..schemas import Shot
from ..utils import ensure_dir, log, probe_duration, warn

_ERR_HINT = {
    1002: ("触发限流，稍后重试", True),
    1004: ("鉴权失败：检查 MINIMAX_API_KEY", False),
    1008: ("账户余额不足，去充值", False),
    1026: ("提示词被判定含敏感内容，需要改写镜头描述", False),
    2013: ("参数不合法，检查 duration/ratio/resolution", False),
}


class MiniMaxProvider:
    name = "minimax"
    supports_keyframe = True

    def __init__(self, cfg: MiniMaxConfig):
        self.cfg = cfg
        self.session = requests.Session()
        # 本地网关不能被系统代理劫持
        self.session.trust_env = not cfg.is_local
        self.billable_seconds = 0

    # ---------------- 基础 ---------------- #
    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:                     # 本地部署常常不校验 key
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + path

    def probe(self) -> Dict[str, Any]:
        """探活：本地网关根路径 404 也算通，只要不是连接失败/5xx。"""
        out: Dict[str, Any] = {"ok": False, "base_url": self.cfg.base_url,
                               "mode": self.cfg.mode, "local": self.cfg.is_local}
        try:
            r = self.session.get(self._url("/"), timeout=8)
            out.update({"ok": r.status_code < 500, "status": r.status_code,
                        "server": r.headers.get("server", "")})
        except requests.RequestException as exc:
            out["error"] = str(exc)
        return out

    def _image_ref(self, path: Path) -> Optional[str]:
        """本地关键帧 → data URL。文件太大就放弃，退回纯文生视频。"""
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size > 20 * 1024 * 1024:
            warn(f"关键帧 {path.name} 超过 20MB，跳过首帧参考")
            return None
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{data}"

    # ---------------- 提交 ---------------- #
    def build_content(self, shot: Shot, keyframe: Optional[Path]) -> tuple[List[dict], bool]:
        prompt = (shot.video_prompt or shot.description or "").strip()
        if not prompt:
            raise ProviderError("镜头缺少 video_prompt", retryable=False)
        content: List[dict] = [{"type": "text", "text": prompt}]
        used_frame = False
        if keyframe and Path(keyframe).exists() and self.cfg.local_image_mode != "skip":
            ref = self._image_ref(Path(keyframe))
            if ref:
                content.append({"type": "image_url", "image_url": {"url": ref}, "role": "first_frame"})
                used_frame = True
        return content, used_frame

    def submit(self, shot: Shot, keyframe: Optional[Path]) -> str:
        content, used_frame = self.build_content(shot, keyframe)
        duration = int(max(4, min(15, shot.duration or 6)))
        payload: Dict[str, Any] = {
            "model": self.cfg.video_model,
            "content": content,
            "duration": duration,
            "resolution": self.cfg.resolution,
        }
        if not used_frame:                       # 图生视频由图片决定画幅
            payload["ratio"] = self.cfg.ratio
        if shot.seed:
            payload["seed"] = int(shot.seed)

        try:
            r = self.session.post(self._url(self.cfg.submit_path), headers=self._headers(),
                                  json=payload, timeout=120)
        except requests.RequestException as exc:
            raise ProviderError(f"提交任务网络异常：{exc}", retryable=True) from exc

        try:
            data = r.json()
        except ValueError:
            raise ProviderError(f"提交返回非 JSON (HTTP {r.status_code}): {r.text[:300]}", retryable=True)

        if r.status_code >= 400 or "task_id" not in data:
            raise self._explain(data, r.status_code)

        self.billable_seconds += duration
        log(f"{shot.id} 任务已提交 task_id={data['task_id']} ({duration}s/{self.cfg.resolution})",
            tag="minimax")
        return str(data["task_id"])

    def _explain(self, data: Dict[str, Any], http_code: int) -> ProviderError:
        err_obj = data.get("error") or {}
        code = 0
        msg = ""
        if isinstance(err_obj, dict):
            msg = str(err_obj.get("message") or "")
            try:
                code = int(str(err_obj.get("http_code") or 0))
            except ValueError:
                code = 0
        base = data.get("base_resp") or {}
        if base and base.get("status_code"):
            code = int(base.get("status_code") or code)
            msg = base.get("status_msg") or msg
        if not msg:
            msg = f"HTTP {http_code} {str(data)[:220]}"
        # 从 "xxx (1026)" 里抠错误码
        for c, (hint, retryable) in _ERR_HINT.items():
            if f"({c})" in msg or code == c:
                return ProviderError(f"MiniMax {c}: {hint}（原始信息：{msg}）",
                                     retryable=retryable, code=str(c))
        return ProviderError(f"MiniMax 提交失败：{msg}", retryable=http_code in (429, 500, 502, 503, 504))

    # ---------------- 轮询 ---------------- #
    def poll(self, task_id: str, shot_id: str = "") -> str:
        deadline = time.time() + self.cfg.task_timeout
        last_status = ""
        while time.time() < deadline:
            time.sleep(self.cfg.poll_interval)
            try:
                path = self.cfg.query_path.format(task_id=task_id)
                r = self.session.get(self._url(path), headers=self._headers(), timeout=60)
                data = r.json()
            except (requests.RequestException, ValueError) as exc:
                warn(f"{shot_id} 轮询抖动，继续等：{exc}")
                continue

            task = data.get("task") or {}
            status = str(task.get("status") or "").lower()
            if status and status != last_status:
                log(f"{shot_id} 状态：{status}", tag="minimax")
                last_status = status
            if status == "succeeded":
                url = ((task.get("content") or {}).get("url")) or task.get("video_url")
                if not url:
                    raise ProviderError(f"任务成功但没有视频 URL：{str(task)[:300]}", retryable=False)
                return str(url)
            if status in ("failed", "cancelled"):
                detail = task.get("error") or task.get("message") or ""
                raise ProviderError(f"任务 {status}：{detail}", retryable=status == "failed")
            if not status:
                base = data.get("base_resp") or {}
                if base.get("status_code") not in (None, 0):
                    raise self._explain(data, r.status_code)
        raise ProviderError(f"任务 {task_id} 超时（{self.cfg.task_timeout}s）", retryable=True)

    # ---------------- 下载 ---------------- #
    def download(self, url: str, out_path: Path) -> None:
        ensure_dir(out_path.parent)
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        with self.session.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        fh.write(chunk)
        tmp.replace(out_path)

    # ---------------- 对外 ---------------- #
    def generate(self, shot: Shot, out_path: Path, keyframe: Optional[Path] = None) -> ProviderResult:
        task_id = self.submit(shot, keyframe)
        shot.task_id = task_id
        url = self.poll(task_id, shot.id)
        self.download(url, Path(out_path))
        dur = probe_duration(out_path)
        log(f"{shot.id} 视频已落盘 {Path(out_path).name} ({dur:.1f}s)", tag="minimax")
        return ProviderResult(path=str(out_path), provider="minimax", task_id=task_id,
                              duration=dur, seed=shot.seed,
                              meta={"url": url, "model": self.cfg.video_model,
                                    "resolution": self.cfg.resolution})
