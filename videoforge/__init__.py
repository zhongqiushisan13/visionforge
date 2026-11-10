"""VisionForge —— 基于 DeepSeek 的 AI 视频编导 Agent。

流水线：剧本 → 分镜 → 关键帧(ComfyUI + LoRA) → 镜头(MiniMax H3) → 配音(T2A) → 剪辑合成(ffmpeg)
"""

__version__ = "1.2.1"
__all__ = ["__version__"]
