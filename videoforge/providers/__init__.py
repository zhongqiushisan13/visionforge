"""Provider 工厂：根据配置决定「谁出关键帧、谁出镜头、谁配音」。

镜头链路按 `settings.media_provider_chain()` 装配：
  mock / local → 占位片
  cloud        → MiniMax 官方 API
  local        → 兼容 MiniMax v2 协议的本地网关（可免 Key）
  comfyui      → 本地 ComfyUI 视频工作流
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .base import ProviderError, ProviderResult
from .comfy_video import ComfyVideoProvider
from .comfyui import ComfyUIProvider
from .local import LocalProvider
from .minimax import MiniMaxProvider
from .tts import SilentTTS, VoiceoverEngine

__all__ = [
    "ProviderError", "ProviderResult", "ComfyUIProvider", "ComfyVideoProvider",
    "LocalProvider", "MiniMaxProvider", "VoiceoverEngine", "SilentTTS",
    "build_providers", "Providers",
]


class Providers:
    """一次性装配好三类能力，供导演调度。"""

    def __init__(self, settings):
        self.s = settings
        self.local = LocalProvider(settings)
        self.minimax = MiniMaxProvider(settings.minimax) if settings.minimax.enabled else None
        self.comfy = ComfyUIProvider(settings) if (settings.comfy.enabled and not settings.pipeline.mock) else None
        self.comfy_video = (ComfyVideoProvider(settings)
                            if (settings.minimax.video_workflow and not settings.pipeline.mock) else None)
        self.voice = VoiceoverEngine(settings.minimax,
                                     prefer="edge" if settings.pipeline.mock else "auto")

    # ---------------- 选择 ---------------- #
    def video_chain(self) -> List[object]:
        chain: List[object] = []
        for name in self.s.media_provider_chain():
            if name == "minimax" and self.minimax:
                chain.append(self.minimax)
            elif name == "comfyui-video" and self.comfy_video:
                chain.append(self.comfy_video)
            elif name == "local":
                chain.append(self.local)
        return chain or [self.local]

    def keyframe_provider(self):
        """ComfyUI 能用就用，否则本地占位图。"""
        if self.comfy:
            return self.comfy
        return self.local

    def keyframe(self, shot, out_path):
        if self.comfy:
            try:
                return self.comfy.generate_keyframe(shot, out_path)
            except ProviderError as exc:
                from ..utils import warn
                warn(f"{shot.id} ComfyUI 出图失败，改用本地占位图：{exc}")
        return self.local.keyframe(shot, out_path)

    def clip(self, shot, out_path, keyframe=None):
        last: Optional[Exception] = None
        for provider in self.video_chain():
            try:
                if provider.name in ("minimax", "comfyui-video"):
                    return provider.generate(shot, out_path, keyframe=keyframe)
                return provider.clip(shot, out_path, keyframe=keyframe)
            except ProviderError as exc:
                last = exc
                from ..utils import warn
                warn(f"{shot.id} {provider.name} 生成失败：{exc}")
                if not exc.retryable:
                    raise
            except Exception as exc:
                last = exc
                from ..utils import warn
                warn(f"{shot.id} {provider.name} 异常：{exc}")
        raise ProviderError(f"{shot.id} 所有视频通道都失败：{last}")

    # ---------------- 体检 ---------------- #
    def probe_video(self) -> Dict[str, object]:
        out: Dict[str, object] = {"chain": [p.name for p in self.video_chain()]}
        # 只探实际会用的通道：mode=comfyui 时不该去 ping api.minimax.io
        if self.minimax and self.minimax in self.video_chain():
            out["minimax"] = self.minimax.probe()
        if self.comfy_video:
            out["comfyui_video"] = self.comfy_video.probe()
        return out


def build_providers(settings) -> Providers:
    return Providers(settings)
