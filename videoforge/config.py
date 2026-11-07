"""配置层：.env / config.json / 环境变量 / CLI 覆盖，四层合并，缺 key 不炸只降级。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


def _app_root() -> Path:
    """可写目录：源码运行时是项目根，打包后是 exe 所在目录（.env / projects 都在这）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_root() -> Path:
    """只读资源目录：打包后是 PyInstaller 的解压目录，源码运行时同项目根。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", _app_root()))
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()
BUNDLE_ROOT = _bundle_root()
PROJECT_ROOT = APP_ROOT          # 兼容旧引用：所有可写路径都基于它


# --------------------------------------------------------------------------- #
# .env 解析（不依赖 python-dotenv）
# --------------------------------------------------------------------------- #
def user_env_dir() -> Path:
    """用户级配置目录：Windows 是 %APPDATA%\\VisionForge，其它平台 ~/.config/visionforge。

    Key 放这里而不是 exe 旁边——便携包拷给别人时不会把 Key 一起带走。
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "VisionForge"
    return Path.home() / ".config" / "visionforge"


def env_file_path() -> Path:
    """读 .env 的位置：优先用户目录；老安装（exe 旁边的 .env）继续兼容。"""
    modern = user_env_dir() / ".env"
    legacy = APP_ROOT / ".env"
    if modern.exists() or not legacy.exists():
        return modern
    return legacy


def tighten_file_permissions(path: Path | str) -> None:
    """把含密钥/令牌的文件收紧到当前用户可读（POSIX 600；Windows 用 icacls）。"""
    p = Path(path)
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME", "")
            domain = os.environ.get("USERDOMAIN", "")
            principal = f"{domain}\\{user}" if domain and user else user
            if principal:
                subprocess.run(
                    ["icacls", str(p), "/inheritance:r", "/grant:r", f"{principal}:F"],
                    capture_output=True, timeout=15,
                )
        else:
            os.chmod(p, 0o600)
    except (OSError, subprocess.SubprocessError):
        # 权限收紧失败不拦启动；doctor 会提示最近警告
        pass


def load_dotenv(path: Path | str, override: bool = False) -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:]
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = val


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    return default if v is None or v == "" else v


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    v = _env(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on", "y")


# --------------------------------------------------------------------------- #
# 分组配置
# --------------------------------------------------------------------------- #
@dataclass
class DeepSeekConfig:
    """任何 OpenAI 兼容端点都能当编剧大脑：DeepSeek 云、Ollama、vLLM、LM Studio。"""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    # auto: 有 key 走云，没有但 base_url 是本地就走本地；cloud / local 强制
    mode: str = "auto"
    model: str = "deepseek-v4-flash"
    fallback_models: tuple = ("deepseek-chat",)
    temperature: float = 1.15
    max_tokens: int = 8192
    timeout: int = 240
    retries: int = 3
    # deepseek-v4 系列支持 thinking 开关与 reasoning_effort
    thinking: bool = False
    reasoning_effort: str = ""

    @property
    def is_local(self) -> bool:
        try:
            host = (urlparse(self.base_url).hostname or "").lower()
        except ValueError:
            return False
        return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1") or self.mode == "local"

    @property
    def enabled(self) -> bool:
        if self.mode == "local":
            return bool(self.base_url)
        return bool(self.api_key) or (self.mode == "auto" and self.is_local)


@dataclass
class MiniMaxConfig:
    """MiniMax H3 视频 + speech T2A 配音。"""

    api_key: str = ""
    base_url: str = "https://api.minimax.io"
    # auto: 有 key 走云，否则看本地是否可用；cloud / local(兼容网关) / comfyui / mock
    mode: str = "auto"
    # 兼容网关路径（本地部署的 H3 服务若照 MiniMax v2 协议实现，改这里即可）
    submit_path: str = "/v2/video_generation"
    query_path: str = "/v2/query/video_generation/{task_id}"
    # ComfyUI 视频工作流（本地出片第二条路）
    video_workflow: str = ""
    video_timeout: int = 2400
    video_model: str = "MiniMax-H3"          # MiniMax-H3 / -Highspeed / -Lite / -Max
    resolution: str = "768P"                  # 768P / 2K
    ratio: str = "16:9"
    poll_interval: float = 10.0
    task_timeout: int = 1500                  # 单条镜头最长等 25 分钟
    # 本地关键帧怎么喂给 H3：base64（内联）/ skip（跳过改纯文生视频）
    local_image_mode: str = "base64"
    tts_model: str = "speech-2.8-hd"
    voice_id: str = "male-qn-qingse"
    tts_speed: float = 1.0
    tts_volume: float = 1.0
    tts_pitch: int = 0

    @property
    def is_local(self) -> bool:
        try:
            host = (urlparse(self.base_url).hostname or "").lower()
        except ValueError:
            return False
        return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1") or self.mode == "local"

    @property
    def cloud_ready(self) -> bool:
        return bool(self.api_key) and self.mode in ("auto", "cloud")

    @property
    def local_ready(self) -> bool:
        return self.mode in ("local", "comfyui") and (
            bool(self.video_workflow) or self.mode == "local")

    @property
    def enabled(self) -> bool:
        return self.cloud_ready or self.local_ready


@dataclass
class ComfyConfig:
    """ComfyUI：本地出关键帧，LoRA 走 LoraLoader 节点自动注入。"""

    base_url: str = "http://127.0.0.1:8188"
    workflow: str = "sdxl_txt2img.json"
    checkpoint: str = ""                      # 空则用工作流里既有的
    default_lora: str = ""                    # 例：tonwin_style_v3.safetensors
    lora_strength: float = 0.8
    steps: int = 28
    cfg: float = 6.5
    sampler: str = "dpmpp_2m"
    scheduler: str = "karras"
    width: int = 1344
    height: int = 768
    batch: int = 1
    negative: str = "text, watermark, logo, extra fingers, deformed hands, lowres, jpeg artifacts"
    poll_interval: float = 2.0
    timeout: int = 900

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)


@dataclass
class RenderConfig:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 19
    preset: str = "medium"
    encoder: str = "auto"        # auto / libx264 / h264_nvenc / h264_qsv / h264_amf
    # 字幕（ASS 烧录）
    subtitle_enabled: bool = True
    subtitle_font: str = "Microsoft YaHei"
    subtitle_size: int = 54
    subtitle_color: str = "&H00FFFFFF"       # ASS BGR
    subtitle_outline: int = 3
    subtitle_margin_v: int = 90
    # 声音
    voice_volume: float = 1.0
    bgm_volume: float = 0.18
    bgm: str = ""                             # 本地音频路径，留空则静音铺底
    loudnorm: bool = True
    # 转场
    transition: str = "fade"                  # cut / fade（暗场过渡，时长不变）
    fade_duration: float = 0.35
    title_card: bool = True                   # 片头标题卡
    end_card: bool = True

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class PipelineConfig:
    max_workers: int = 3
    max_retries: int = 3
    mock: bool = False                        # 全离线占位素材，用于打通链路
    projects_dir: str = "projects"
    keep_intermediate: bool = True
    # ---- 预算护栏（单位：元，估算值，不是账单）----
    budget: float = 0.0                       # 单项目付费调用上限；0 = 不限制
    # 单次调用估算价（元）。拍脑袋的保守值，可用 VF_PRICES 覆盖，如
    # {"deepseek": 0.05, "minimax_video": 3.0, "minimax_tts": 0.1}
    prices: Dict[str, float] = field(default_factory=lambda: {
        "deepseek": 0.05, "minimax_video": 3.0, "minimax_tts": 0.1,
        "comfyui": 0.0, "local": 0.0, "mock": 0.0,
    })


@dataclass
class Settings:
    deepseek: DeepSeekConfig = field(default_factory=DeepSeekConfig)
    minimax: MiniMaxConfig = field(default_factory=MiniMaxConfig)
    comfy: ComfyConfig = field(default_factory=ComfyConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    root: Path = field(default_factory=lambda: PROJECT_ROOT)

    # ---------------- 构造 ---------------- #
    @classmethod
    def load(cls, root: Path | str = PROJECT_ROOT, config_file: str = "config.json",
             overrides: Optional[Dict[str, Any]] = None) -> "Settings":
        root = Path(root)
        load_dotenv(env_file_path())          # 用户目录优先，兼容 exe 旁的老 .env
        load_dotenv(root / ".env")            # 老位置仍在则继续生效（不覆盖已有值）
        s = cls._from_env()
        s.root = root
        cfg_path = root / config_file
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                s._merge(data)
            except Exception as exc:  # 配置写坏了不该拦住建视频
                print(f"[warn] config.json 解析失败，已忽略：{exc}")
        if overrides:
            s._merge(overrides)
        return s

    @classmethod
    def _from_env(cls) -> "Settings":
        ds = DeepSeekConfig(
            api_key=_env("DEEPSEEK_API_KEY"),
            base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            mode=_env("DEEPSEEK_MODE", "auto"),
            model=_env("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            temperature=_env_float("DEEPSEEK_TEMPERATURE", 1.15),
            max_tokens=_env_int("DEEPSEEK_MAX_TOKENS", 8192),
            timeout=_env_int("DEEPSEEK_TIMEOUT", 240),
            thinking=_env_bool("DEEPSEEK_THINKING", False),
            reasoning_effort=_env("DEEPSEEK_REASONING_EFFORT", ""),
        )
        mm = MiniMaxConfig(
            api_key=_env("MINIMAX_API_KEY"),
            base_url=_env("MINIMAX_BASE_URL", "https://api.minimax.io"),
            mode=_env("MINIMAX_MODE", "auto"),
            submit_path=_env("MINIMAX_SUBMIT_PATH", "/v2/video_generation"),
            query_path=_env("MINIMAX_QUERY_PATH", "/v2/query/video_generation/{task_id}"),
            video_workflow=_env("MINIMAX_VIDEO_WORKFLOW", ""),
            video_timeout=_env_int("MINIMAX_VIDEO_TIMEOUT", 2400),
            video_model=_env("MINIMAX_VIDEO_MODEL", "MiniMax-H3"),
            resolution=_env("MINIMAX_RESOLUTION", "768P"),
            ratio=_env("MINIMAX_RATIO", "16:9"),
            poll_interval=_env_float("MINIMAX_POLL_INTERVAL", 10.0),
            task_timeout=_env_int("MINIMAX_TASK_TIMEOUT", 1500),
            local_image_mode=_env("MINIMAX_LOCAL_IMAGE_MODE", "base64"),
            tts_model=_env("MINIMAX_TTS_MODEL", "speech-2.8-hd"),
            voice_id=_env("MINIMAX_VOICE_ID", "male-qn-qingse"),
            tts_speed=_env_float("MINIMAX_TTS_SPEED", 1.0),
        )
        cf = ComfyConfig(
            base_url=_env("COMFYUI_BASE_URL", "http://127.0.0.1:8188"),
            workflow=_env("COMFYUI_WORKFLOW", "sdxl_txt2img.json"),
            checkpoint=_env("COMFYUI_CHECKPOINT", ""),
            default_lora=_env("COMFYUI_LORA", ""),
            lora_strength=_env_float("COMFYUI_LORA_STRENGTH", 0.8),
            steps=_env_int("COMFYUI_STEPS", 28),
            cfg=_env_float("COMFYUI_CFG", 6.5),
            width=_env_int("COMFYUI_WIDTH", 1344),
            height=_env_int("COMFYUI_HEIGHT", 768),
        )
        rd = RenderConfig(
            width=_env_int("VF_WIDTH", 1920),
            height=_env_int("VF_HEIGHT", 1080),
            fps=_env_int("VF_FPS", 30),
            crf=_env_int("VF_CRF", 19),
            subtitle_enabled=_env_bool("VF_SUBTITLE", True),
            subtitle_font=_env("VF_SUBTITLE_FONT", "Microsoft YaHei"),
            subtitle_size=_env_int("VF_SUBTITLE_SIZE", 54),
            bgm=_env("VF_BGM", ""),
            bgm_volume=_env_float("VF_BGM_VOLUME", 0.18),
            voice_volume=_env_float("VF_VOICE_VOLUME", 1.0),
            transition=_env("VF_TRANSITION", "fade"),
            title_card=_env_bool("VF_TITLE_CARD", True),
            end_card=_env_bool("VF_END_CARD", True),
        )
        pl = PipelineConfig(
            max_workers=_env_int("VF_WORKERS", 3),
            max_retries=_env_int("VF_RETRIES", 3),
            mock=_env_bool("VF_MOCK", False),
            projects_dir=_env("VF_PROJECTS_DIR", "projects"),
            budget=_env_float("VF_BUDGET", 0.0),
        )
        try:
            custom = json.loads(_env("VF_PRICES", "{}"))
            if isinstance(custom, dict):
                pl.prices.update({k: float(v) for k, v in custom.items()})
        except (ValueError, TypeError):
            pass
        return cls(deepseek=ds, minimax=mm, comfy=cf, render=rd, pipeline=pl)

    def _merge(self, data: Dict[str, Any]) -> None:
        for key, val in (data or {}).items():
            if not hasattr(self, key):
                continue
            cur = getattr(self, key)
            if is_dataclass(cur) and isinstance(val, dict):
                for k, v in val.items():
                    if hasattr(cur, k):
                        setattr(cur, k, v)
            elif not is_dataclass(cur):
                setattr(self, key, val)

    # ---------------- 派生 ---------------- #
    @property
    def projects_root(self) -> Path:
        p = Path(self.pipeline.projects_dir)
        return p if p.is_absolute() else self.root / p

    @property
    def workflows_dir(self) -> Path:
        local = APP_ROOT / "workflows"
        if local.exists():
            return local
        return BUNDLE_ROOT / "workflows"

    def media_provider_chain(self) -> list[str]:
        """镜头生成优先级：mock > 云端/本地兼容 > ComfyUI 视频 > 占位片。"""
        if self.pipeline.mock or self.minimax.mode == "mock":
            return ["local"]
        chain = []
        if self.minimax.cloud_ready or self.minimax.mode == "local":
            chain.append("minimax")
        if self.minimax.mode == "comfyui" or self.minimax.video_workflow:
            chain.append("comfyui-video")
        chain.append("local")
        return chain

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["root"] = str(self.root)
        # 别把密钥写进项目文件里
        d["deepseek"]["api_key"] = _mask(self.deepseek.api_key)
        d["minimax"]["api_key"] = _mask(self.minimax.api_key)
        return d

    def describe(self) -> str:
        def mark(ok: bool) -> str:
            return "OK " if ok else "-- "

        rows = [
            f"{mark(self.deepseek.enabled)}DeepSeek   {self.deepseek.model} @ {self.deepseek.base_url}",
            f"{mark(self.minimax.enabled)}Video      mode={self.minimax.mode} "
            f"{self.minimax.base_url} | {self.minimax.video_model} {self.minimax.resolution}"
            f"{' | workflow=' + self.minimax.video_workflow if self.minimax.video_workflow else ''}",
            f"{mark(bool(self.comfy.base_url))}ComfyUI    {self.comfy.base_url} "
            f"| workflow={self.comfy.workflow} | LoRA={self.comfy.default_lora or '(工作流默认)'}",
            f"--  Render     {self.render.size}@{self.render.fps}fps | 转场={self.render.transition} | "
            f"字幕={'on' if self.render.subtitle_enabled else 'off'}",
            f"--  Mode       {'MOCK 离线占位' if self.pipeline.mock else '真实生成'}",
        ]
        return "\n".join(rows)


def _mask(s: str) -> str:
    if not s:
        return ""
    return s[:4] + "*" * max(0, len(s) - 8) + s[-4:] if len(s) > 8 else "*" * len(s)
