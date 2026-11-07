"""数据模型：剧本 / 分镜 / 项目，全部可 JSON 序列化，便于断点续跑与看板展示。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import ensure_dir, now_iso, read_json, write_json

SHOT_STATUS = ("pending", "keyframe", "rendered", "voiced", "failed", "skipped")


def _s(v: Any, default: str = "") -> str:
    if v is None:
        return default
    if isinstance(v, (list, tuple)):
        return " / ".join(str(x) for x in v if x is not None)
    return str(v).strip()


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class Character:
    name: str = ""
    look: str = ""          # 中文外形描述（给编剧参考）
    look_en: str = ""       # 英文外形描述（喂给出图/视频模型，保证一致性）
    voice: str = ""         # 音色备注

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Character":
        return cls(
            name=_s(d.get("name")),
            look=_s(d.get("look") or d.get("appearance")),
            look_en=_s(d.get("look_en") or d.get("appearance_en")),
            voice=_s(d.get("voice")),
        )


@dataclass
class Shot:
    id: str = ""
    index: int = 0
    act: str = ""                       # 所属段落/幕
    duration: int = 6                   # 秒，MiniMax H3 限 4~15
    scene: str = ""                     # 场景（中文）
    description: str = ""               # 画面内容（中文）
    camera: str = ""                    # 运镜（中文）
    dialogue: str = ""                  # 台词 / 旁白（中文）
    speaker: str = ""                   # 说话人，旁白写 "旁白"
    subtitle: str = ""                  # 字幕文本，留空则用 dialogue
    sfx: str = ""                       # 音效提示
    image_prompt: str = ""              # 英文关键帧提示词
    video_prompt: str = ""              # 英文视频提示词
    negative_prompt: str = ""
    lora: str = ""                      # 该镜头指定 LoRA，留空用全局
    lora_strength: float = 0.0
    seed: int = 0
    transition: str = ""                # cut / fade / dissolve / wipeleft / slideup / zoomin ...
    transition_duration: float = 0.0    # 0 = 用全局默认
    enabled: bool = True                # 手动剪辑：关掉的镜头不进成片
    trim_start: float = 0.0             # 手动剪辑：片段入点（秒）
    # ---- 剪辑参数（对齐剪映常用项）----
    speed: float = 1.0                  # 0.25 ~ 4.0
    reverse: bool = False
    mirror: bool = False
    volume: float = 1.0                 # 0 ~ 2
    filter_preset: str = ""             # none/cinematic/bw/vintage/cool/warm/vivid
    brightness: float = 0.0             # -0.5 ~ 0.5
    contrast: float = 1.0               # 0.5 ~ 2.0
    saturation: float = 1.0             # 0 ~ 2.0
    temperature: float = 0.0            # -1(冷) ~ 1(暖)
    title_text: str = ""                # 画面上叠加的文字层
    title_position: str = "bottom"      # top / center / bottom
    title_size: int = 0                 # 0 = 按分辨率自动
    status: str = "pending"
    provider: str = ""
    task_id: str = ""
    keyframe: str = ""
    clip: str = ""
    audio: str = ""
    audio_duration: float = 0.0
    clip_duration: float = 0.0
    attempts: int = 0
    error: str = ""
    updated_at: str = ""

    # ---------------- 序列化 ---------------- #
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Shot":
        shot = cls()
        for f, meta in cls.__dataclass_fields__.items():  # type: ignore[attr-defined]
            if f not in d:
                continue
            val = d[f]
            if meta.type in ("int", int):
                setattr(shot, f, _i(val, meta.default))
            elif meta.type in ("float", float):
                setattr(shot, f, _f(val, meta.default))
            elif meta.type in ("bool", bool):
                setattr(shot, f, bool(val))
            else:
                setattr(shot, f, _s(val) if not isinstance(val, (dict, list)) else val)
        return shot

    # ---------------- 派生 ---------------- #
    @property
    def text(self) -> str:
        return self.subtitle or self.dialogue

    @property
    def voiced(self) -> bool:
        return bool(self.audio and self.audio_duration > 0)

    @property
    def done(self) -> bool:
        return self.status in ("rendered", "voiced", "skipped")

    def prompt_fingerprint(self) -> str:
        raw = "|".join([self.video_prompt, self.image_prompt, str(self.duration),
                        self.lora, self.video_model_key()])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def video_model_key(self) -> str:
        return f"{self.provider}:{self.seed}"


@dataclass
class Script:
    title: str = ""
    logline: str = ""
    synopsis: str = ""
    tone: str = ""
    genre: str = ""
    target_duration: int = 60
    language: str = "zh"
    narrator: str = ""
    characters: List[Character] = field(default_factory=list)
    beats: List[Dict[str, str]] = field(default_factory=list)
    style_prompt: str = ""       # 全局影像风格（英文，拼进每条提示词）
    music_mood: str = ""
    raw: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Script":
        return cls(
            title=_s(d.get("title")),
            logline=_s(d.get("logline")),
            synopsis=_s(d.get("synopsis")),
            tone=_s(d.get("tone")),
            genre=_s(d.get("genre")),
            target_duration=_i(d.get("target_duration"), 60),
            language=_s(d.get("language"), "zh"),
            narrator=_s(d.get("narrator")),
            characters=[Character.from_dict(c) for c in d.get("characters") or [] if isinstance(c, dict)],
            beats=[b if isinstance(b, dict) else {"summary": _s(b)} for b in d.get("beats") or []],
            style_prompt=_s(d.get("style_prompt")),
            music_mood=_s(d.get("music_mood")),
            raw=_s(d.get("raw")),
        )


@dataclass
class Project:
    slug: str = ""
    title: str = ""
    brief: str = ""
    aspect: str = "16:9"
    resolution: str = "1920x1080"
    fps: int = 30
    script: Script = field(default_factory=Script)
    shots: List[Shot] = field(default_factory=list)
    bgm: str = ""
    output: str = ""
    created_at: str = ""
    updated_at: str = ""
    mock: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---------------- 目录 ---------------- #
    @property
    def dir(self) -> Path:
        return Path(self.meta.get("_dir", "."))

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    def setup_dirs(self) -> None:
        for sub in ("keyframes", "clips", "audio", "cache", "output", "logs"):
            ensure_dir(self.path(sub))

    # ---------------- 生命周期 ---------------- #
    @classmethod
    def create(cls, directory: Path | str, *, title: str, brief: str, slug: str,
               aspect: str = "16:9", resolution: str = "1920x1080", fps: int = 30,
               mock: bool = False) -> "Project":
        directory = Path(directory)
        ensure_dir(directory)
        p = cls(
            slug=slug, title=title, brief=brief, aspect=aspect,
            resolution=resolution, fps=fps, mock=mock,
            created_at=now_iso(), updated_at=now_iso(),
        )
        p.meta["_dir"] = str(directory.resolve())
        p.setup_dirs()
        return p

    @classmethod
    def load(cls, directory: Path | str) -> "Project":
        directory = Path(directory)
        data = read_json(directory / "project.json", default=None)
        if data is None:
            raise FileNotFoundError(f"{directory} 下没有 project.json，先跑 plan")
        p = cls(
            slug=_s(data.get("slug")),
            title=_s(data.get("title")),
            brief=_s(data.get("brief")),
            aspect=_s(data.get("aspect"), "16:9"),
            resolution=_s(data.get("resolution"), "1920x1080"),
            fps=_i(data.get("fps"), 30),
            script=Script.from_dict(data.get("script") or {}),
            shots=[Shot.from_dict(s) for s in data.get("shots") or []],
            bgm=_s(data.get("bgm")),
            output=_s(data.get("output")),
            created_at=_s(data.get("created_at")),
            updated_at=_s(data.get("updated_at")),
            mock=bool(data.get("mock")),
            meta=data.get("meta") or {},
        )
        p.meta["_dir"] = str(directory.resolve())
        p.setup_dirs()
        return p

    def save(self) -> None:
        self.updated_at = now_iso()
        write_json(self.path("project.json"), self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # ---------------- 统计 ---------------- #
    @property
    def total_duration(self) -> int:
        return sum(s.duration for s in self.shots)

    @property
    def voiced_duration(self) -> float:
        return round(sum(s.audio_duration for s in self.shots), 2)

    def counts(self) -> Dict[str, int]:
        c = {k: 0 for k in SHOT_STATUS}
        for s in self.shots:
            c[s.status] = c.get(s.status, 0) + 1
        return c

    def progress(self) -> Dict[str, Any]:
        total = len(self.shots)
        rendered = sum(1 for s in self.shots if s.clip)
        voiced = sum(1 for s in self.shots if s.voiced)
        return {
            "total": total, "rendered": rendered, "voiced": voiced,
            "failed": sum(1 for s in self.shots if s.status == "failed"),
            "percent": round((rendered / total * 100) if total else 0, 1),
            "duration": self.total_duration,
        }

    def pending(self, stage: str = "clip", force: bool = False) -> List[Shot]:
        out = []
        for s in self.shots:
            if force:
                out.append(s)
            elif stage == "keyframe" and not s.keyframe:
                out.append(s)
            elif stage == "clip" and not s.clip:
                out.append(s)
            elif stage == "voice" and s.text and not s.voiced:
                out.append(s)
        return out

    def shot(self, shot_id: str) -> Optional[Shot]:
        for s in self.shots:
            if s.id == shot_id:
                return s
        # 允许传序号
        try:
            idx = int(shot_id)
            return self.shots[idx] if 0 <= idx < len(self.shots) else None
        except (TypeError, ValueError):
            return None

    def summary(self) -> str:
        pr = self.progress()
        return (f"{self.slug} | 《{self.title}》 | 镜头 {pr['total']} 条 / {pr['duration']}s | "
                f"已出片 {pr['rendered']} | 已配音 {pr['voiced']} | 失败 {pr['failed']}")
