"""本地 API 服务：桌面壳（pywebview）与浏览器共用同一套接口。

设计取向：单机、单用户、无鉴权；所有重活丢给 JobManager，接口只负责「提交 / 查询 / 落库」。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import APP_ROOT, BUNDLE_ROOT, PROJECT_ROOT, Settings
from ..edit_commands import apply_ops, interpret
from ..editing import FILTER_PRESETS, TITLE_POSITIONS, TRANSITIONS, timeline
from ..llm import DeepSeek
from ..pipeline import Director
from ..providers.comfyui import ComfyUIProvider
from ..schemas import Project, Shot
from ..sysprobe import auto_configure, doctor_summary
from ..utils import find_ffmpeg, probe_duration, read_json
from .jobs import MANAGER, Job


def _static_dir() -> Path:
    for cand in (Path(__file__).parent / "static",
                 BUNDLE_ROOT / "videoforge" / "web" / "static",
                 APP_ROOT / "videoforge" / "web" / "static"):
        if (cand / "index.html").exists():
            return cand
    return Path(__file__).parent / "static"


STATIC = _static_dir()
EDITABLE_SHOT_FIELDS = {
    "duration": int, "subtitle": str, "dialogue": str, "speaker": str, "scene": str,
    "camera": str, "video_prompt": str, "image_prompt": str, "negative_prompt": str,
    "transition": str, "enabled": bool, "trim_start": float, "lora": str,
    "lora_strength": float, "seed": int, "act": str, "description": str, "sfx": str,
    # 剪辑参数
    "speed": float, "volume": float, "reverse": bool, "mirror": bool,
    "filter_preset": str, "brightness": float, "contrast": float,
    "saturation": float, "temperature": float, "transition_duration": float,
    "title_text": str, "title_position": str, "title_size": int,
}


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _settings() -> Settings:
    return Settings.load(PROJECT_ROOT)


def _projects_root() -> Path:
    root = _settings().projects_root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_dir(slug: str) -> Path:
    d = _projects_root() / Path(slug).name
    if not (d / "project.json").exists():
        raise HTTPException(404, f"项目不存在：{slug}")
    return d


def _load(slug: str) -> Project:
    return Project.load(_project_dir(slug))


def _media_url(slug: str, sub: str, name: str, path: str | Path | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        stamp = int(p.stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/media/{slug}/{sub}/{p.name}?v={stamp}"


def _shot_payload(slug: str, s: Shot) -> Dict[str, Any]:
    d = s.to_dict()
    d["text"] = s.text
    d["keyframe_url"] = _media_url(slug, "keyframes", s.id, s.keyframe)
    d["clip_url"] = _media_url(slug, "clips", s.id, s.clip)
    d["audio_url"] = _media_url(slug, "audio", s.id, s.audio)
    return d


def _project_payload(p: Project) -> Dict[str, Any]:
    slug = p.dir.name
    d = p.to_dict()
    d["progress"] = p.progress()
    d["shots"] = [_shot_payload(slug, s) for s in p.shots]
    d["output_url"] = _media_url(slug, "output", "", p.output)
    d["cover_url"] = next((_media_url(slug, "keyframes", s.id, s.keyframe)
                           for s in p.shots if s.keyframe), "")
    d["srt_url"] = _media_url(slug, "output", "", p.path("output") / "subtitles.srt")
    d["counts"] = p.counts()
    enabled = [s for s in p.shots if s.enabled]
    starts, total = timeline(enabled, 0.4)
    d["timeline"] = {"ids": [s.id for s in enabled], "starts": starts, "total": total}
    return d


def _summary(p: Project) -> Dict[str, Any]:
    slug = p.dir.name
    pr = p.progress()
    return {
        "slug": slug, "title": p.title, "logline": p.script.logline,
        "updated_at": p.updated_at, "created_at": p.created_at,
        "shots": pr["total"], "duration": pr["duration"], "rendered": pr["rendered"],
        "percent": pr["percent"], "has_output": bool(p.output and Path(p.output).exists()),
        "output_url": _media_url(slug, "output", "", p.output),
        "cover_url": next((_media_url(slug, "keyframes", s.id, s.keyframe)
                           for s in p.shots if s.keyframe), ""),
        "mock": p.mock, "aspect": p.aspect,
    }


def _write_env(updates: Dict[str, str]) -> None:
    env_path = PROJECT_ROOT / ".env"
    lines: List[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    seen = set()
    out: List[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    env_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    for key, val in updates.items():
        os.environ[key] = val


def _merge_config(section: str, values: Dict[str, Any]) -> None:
    path = PROJECT_ROOT / "config.json"
    data = read_json(path, default={}) or {}
    data.setdefault(section, {}).update(values)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _guard_busy() -> None:
    if MANAGER.is_busy():
        j = MANAGER.active()[0]
        raise HTTPException(409, f"已有任务在跑（{j.kind}），等它跑完再操作")


# --------------------------------------------------------------------------- #
# 应用
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    app = FastAPI(title="VisionForge", version=__version__, docs_url="/api/docs")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    # ---------------- 基础 ---------------- #
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/bootstrap")
    def bootstrap() -> Dict[str, Any]:
        s = _settings()
        projects = []
        for d in sorted(_projects_root().iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if (d / "project.json").exists():
                try:
                    projects.append(_summary(Project.load(d)))
                except Exception:
                    continue
        active = [j.to_dict() for j in MANAGER.active()]
        return {
            "version": __version__,
            "projects": projects,
            "active_jobs": active,
            "providers": {
                "deepseek": {"enabled": s.deepseek.enabled, "model": s.deepseek.model},
                "minimax": {"enabled": s.minimax.enabled, "model": s.minimax.video_model,
                            "resolution": s.minimax.resolution},
                "comfyui": {"enabled": bool(s.comfy.base_url), "url": s.comfy.base_url,
                            "workflow": s.comfy.workflow, "lora": s.comfy.default_lora},
                "mock": s.pipeline.mock,
                "tts": "minimax" if s.minimax.enabled else "auto",
            },
            "render": {"width": s.render.width, "height": s.render.height,
                       "fps": s.render.fps, "subtitle": s.render.subtitle_enabled},
            "editing": {
                "transitions": [{"key": k, "label": v["label"]} for k, v in TRANSITIONS.items()],
                "filters": [{"key": k, "label": v["label"]} for k, v in FILTER_PRESETS.items()],
                "title_positions": [{"key": "top", "label": "顶部"},
                                    {"key": "center", "label": "居中"},
                                    {"key": "bottom", "label": "底部"}],
            },
            "workspace": str(PROJECT_ROOT),
        }

    @app.get("/api/system/doctor")
    def doctor() -> Dict[str, Any]:
        s = _settings()
        out: Dict[str, Any] = {"deepseek": s.deepseek.enabled, "minimax": s.minimax.enabled}
        try:
            sysinfo = doctor_summary()
            out.update({"system": sysinfo, "recommend": sysinfo.get("recommend"),
                        "encoder": sysinfo.get("encoder_resolved"),
                        "first_run": sysinfo.get("first_run")})
        except Exception as exc:
            out["system_error"] = str(exc)
        try:
            ff = find_ffmpeg()
            out["ffmpeg"] = {"ok": True, "path": ff}
        except Exception as exc:
            out["ffmpeg"] = {"ok": False, "error": str(exc)}
        try:
            comfy = ComfyUIProvider(s)
            stats = comfy.verify()
            loras = comfy.list_loras()
            ckpts = comfy.list_checkpoints()
            out["comfyui"] = {"ok": True, "loras": loras, "checkpoints": ckpts,
                              "system": {k: stats.get(k) for k in ("system", "devices")}}
        except Exception as exc:
            out["comfyui"] = {"ok": False, "error": str(exc), "loras": [], "checkpoints": []}
        return out

    @app.get("/api/workflows")
    def list_workflows() -> Dict[str, Any]:
        root = _settings().workflows_dir
        files = sorted(p.name for p in root.glob("*.json")) if root.exists() else []
        return {"files": files, "dir": str(root)}

    @app.post("/api/system/video-check")
    def video_check() -> Dict[str, Any]:
        """探测视频通道：本地网关 / ComfyUI 视频工作流是否真的能用。"""
        from ..providers import Providers

        s = _settings()
        providers = Providers(s)
        return {"ok": True, "chain": [p.name for p in providers.video_chain()],
                "probe": providers.probe_video()}

    @app.post("/api/system/autoconfig")
    def autoconfig(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        """重新探测本机并写入推荐配置（编码器/分辨率/并发/字体）。"""
        force = bool(payload.get("force", True))
        info = auto_configure(_settings(), force=force)
        return {"ok": True, **info}

    @app.get("/api/settings")
    def get_settings() -> Dict[str, Any]:
        s = _settings()
        return {
            "deepseek": {"model": s.deepseek.model, "base_url": s.deepseek.base_url,
                         "mode": s.deepseek.mode, "local": s.deepseek.is_local,
                         "api_key_set": bool(s.deepseek.api_key),
                         "key_hint": (s.deepseek.api_key[:6] + "…" + s.deepseek.api_key[-4:])
                         if len(s.deepseek.api_key) > 12 else "",
                         "temperature": s.deepseek.temperature},
            "minimax": {"model": s.minimax.video_model, "resolution": s.minimax.resolution,
                        "ratio": s.minimax.ratio, "tts_model": s.minimax.tts_model,
                        "voice_id": s.minimax.voice_id,
                        "mode": s.minimax.mode, "base_url": s.minimax.base_url,
                        "submit_path": s.minimax.submit_path,
                        "query_path": s.minimax.query_path,
                        "video_workflow": s.minimax.video_workflow,
                        "local": s.minimax.is_local,
                        "api_key_set": bool(s.minimax.api_key)},
            "comfy": {"url": s.comfy.base_url, "workflow": s.comfy.workflow,
                      "checkpoint": s.comfy.checkpoint, "lora": s.comfy.default_lora,
                      "lora_strength": s.comfy.lora_strength, "steps": s.comfy.steps,
                      "cfg": s.comfy.cfg, "width": s.comfy.width, "height": s.comfy.height},
            "render": {"width": s.render.width, "height": s.render.height, "fps": s.render.fps,
                       "encoder": s.render.encoder,
                       "subtitle": s.render.subtitle_enabled,
                       "subtitle_font": s.render.subtitle_font,
                       "subtitle_size": s.render.subtitle_size,
                       "bgm_volume": s.render.bgm_volume, "transition": s.render.transition},
            "pipeline": {"mock": s.pipeline.mock, "workers": s.pipeline.max_workers},
        }

    @app.post("/api/settings")
    def save_settings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        env_updates: Dict[str, str] = {}
        ds = payload.get("deepseek") or {}
        mm = payload.get("minimax") or {}
        if ds.get("api_key"):
            env_updates["DEEPSEEK_API_KEY"] = str(ds["api_key"]).strip()
        if ds.get("model"):
            env_updates["DEEPSEEK_MODEL"] = str(ds["model"]).strip()
        if mm.get("api_key"):
            env_updates["MINIMAX_API_KEY"] = str(mm["api_key"]).strip()
        if mm.get("model"):
            env_updates["MINIMAX_VIDEO_MODEL"] = str(mm["model"]).strip()
        if mm.get("resolution"):
            env_updates["MINIMAX_RESOLUTION"] = str(mm["resolution"]).strip()
        if mm.get("voice_id"):
            env_updates["MINIMAX_VOICE_ID"] = str(mm["voice_id"]).strip()
        if mm.get("mode") in ("auto", "cloud", "local", "comfyui", "mock"):
            env_updates["MINIMAX_MODE"] = str(mm["mode"])
        if mm.get("base_url"):
            env_updates["MINIMAX_BASE_URL"] = str(mm["base_url"]).strip()
        if mm.get("submit_path"):
            env_updates["MINIMAX_SUBMIT_PATH"] = str(mm["submit_path"]).strip()
        if mm.get("query_path"):
            env_updates["MINIMAX_QUERY_PATH"] = str(mm["query_path"]).strip()
        if "video_workflow" in mm:
            env_updates["MINIMAX_VIDEO_WORKFLOW"] = str(mm["video_workflow"]).strip()
        if ds.get("base_url"):
            env_updates["DEEPSEEK_BASE_URL"] = str(ds["base_url"]).strip()
        if ds.get("mode") in ("auto", "cloud", "local"):
            env_updates["DEEPSEEK_MODE"] = str(ds["mode"])
        if env_updates:
            _write_env(env_updates)

        comfy = payload.get("comfy") or {}
        if comfy:
            _merge_config("comfy", {k: comfy[k] for k in
                                    ("url", "workflow", "checkpoint", "lora", "lora_strength",
                                     "steps", "cfg", "width", "height") if k in comfy})
            # 字段名对齐配置文件
            cfg = read_json(PROJECT_ROOT / "config.json", default={}) or {}
            c = cfg.get("comfy", {})
            for a, b in (("url", "base_url"), ("lora", "default_lora")):
                if a in c:
                    c[b] = c.pop(a)
            (PROJECT_ROOT / "config.json").write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        render = payload.get("render") or {}
        if render:
            _merge_config("render", {k: render[k] for k in
                                     ("width", "height", "fps", "subtitle", "subtitle_font",
                                      "subtitle_size", "bgm_volume", "transition", "encoder") if k in render})
        pipe = payload.get("pipeline") or {}
        if pipe:
            _merge_config("pipeline", {k: pipe[k] for k in ("mock", "max_workers") if k in pipe})
        return {"ok": True, "settings": get_settings()}

    # ---------------- 项目 ---------------- #
    @app.get("/api/projects")
    def list_projects() -> List[Dict[str, Any]]:
        out = []
        for d in _projects_root().iterdir():
            if (d / "project.json").exists():
                try:
                    out.append(_summary(Project.load(d)))
                except Exception:
                    continue
        return sorted(out, key=lambda x: x.get("updated_at") or "", reverse=True)

    @app.post("/api/projects")
    def create_project(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        _guard_busy()
        brief = (payload.get("brief") or "").strip()
        if len(brief) < 4:
            raise HTTPException(400, "需求描述太短，至少写一句人话")

        def run(job: Job) -> Dict[str, Any]:
            s = _settings()
            if payload.get("mock") is not None:
                s.pipeline.mock = bool(payload.get("mock"))
            d = Director(s)
            p = d.plan(brief,
                       title=payload.get("title") or "",
                       duration=int(payload.get("duration") or 60),
                       aspect=payload.get("aspect") or "16:9",
                       style=payload.get("style") or "",
                       language=payload.get("language") or "zh",
                       shot_hint=int(payload.get("shot_hint") or 0))
            return {"slug": p.dir.name, "title": p.title, "shots": len(p.shots),
                    "duration": p.total_duration}

        job = MANAGER.submit("plan", run)
        return {"job": job.id}

    @app.get("/api/projects/{slug}")
    def get_project(slug: str) -> Dict[str, Any]:
        return _project_payload(_load(slug))

    @app.patch("/api/projects/{slug}")
    def patch_project(slug: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        p = _load(slug)
        if "title" in payload:
            p.title = str(payload["title"]).strip() or p.title
        if "bgm" in payload:
            p.bgm = str(payload["bgm"])
        script = payload.get("script") or {}
        for field in ("logline", "synopsis", "style_prompt", "music_mood", "narrator", "tone"):
            if field in script:
                setattr(p.script, field, str(script[field]))
        p.save()
        return _project_payload(p)

    @app.post("/api/projects/{slug}/reorder")
    def reorder(slug: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        p = _load(slug)
        order = [str(x) for x in payload.get("order") or []]
        by_id = {s.id: s for s in p.shots}
        if sorted(order) != sorted(by_id):
            raise HTTPException(400, "排序列表与镜头不匹配")
        p.shots = [by_id[i] for i in order]
        for i, s in enumerate(p.shots, 1):
            s.index = i
        p.save()
        return {"ok": True, "order": [s.id for s in p.shots]}

    @app.delete("/api/projects/{slug}")
    def delete_project(slug: str) -> Dict[str, Any]:
        d = _project_dir(slug)
        if MANAGER.is_busy():
            raise HTTPException(409, "有任务在跑，先停一停")
        trash = _projects_root().parent / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / f"{slug}-{int(time.time())}"
        shutil.move(str(d), str(target))
        return {"ok": True, "moved_to": str(target)}

    # ---------------- 镜头 ---------------- #
    @app.patch("/api/projects/{slug}/shots/{sid}")
    def patch_shot(slug: str, sid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        p = _load(slug)
        shot = p.shot(sid)
        if not shot:
            raise HTTPException(404, f"镜头不存在：{sid}")
        for key, val in payload.items():
            if key not in EDITABLE_SHOT_FIELDS:
                continue
            caster = EDITABLE_SHOT_FIELDS[key]
            try:
                if caster is bool:
                    setattr(shot, key, bool(val))
                elif caster is int:
                    setattr(shot, key, int(val))
                elif caster is float:
                    setattr(shot, key, float(val))
                else:
                    setattr(shot, key, str(val))
            except (TypeError, ValueError):
                raise HTTPException(400, f"字段 {key} 取值不合法：{val}")
        # 统一夹紧，别让界面塞进来的脏值把 ffmpeg 弄崩
        shot.duration = int(max(1, min(15, shot.duration)))
        shot.trim_start = max(0.0, float(shot.trim_start))
        shot.speed = max(0.25, min(4.0, float(shot.speed or 1.0)))
        shot.volume = max(0.0, min(2.0, float(shot.volume if shot.volume is not None else 1.0)))
        shot.brightness = max(-0.5, min(0.5, float(shot.brightness or 0.0)))
        shot.contrast = max(0.5, min(2.0, float(shot.contrast or 1.0)))
        shot.saturation = max(0.0, min(2.0, float(shot.saturation or 1.0)))
        shot.temperature = max(-1.0, min(1.0, float(shot.temperature or 0.0)))
        shot.transition_duration = max(0.0, min(1.0, float(shot.transition_duration or 0.0)))
        if shot.transition not in TRANSITIONS:
            shot.transition = "cut"
        if shot.filter_preset not in FILTER_PRESETS:
            shot.filter_preset = "none"
        if shot.title_position not in TITLE_POSITIONS:
            shot.title_position = "bottom"
        shot.title_size = int(max(0, min(160, shot.title_size or 0)))
        shot.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        p.save()
        return {"ok": True, "shot": _shot_payload(slug, shot)}

    @app.post("/api/projects/{slug}/shots/{sid}/move")
    def move_shot(slug: str, sid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        p = _load(slug)
        idx = next((i for i, s in enumerate(p.shots) if s.id == sid), -1)
        if idx < 0:
            raise HTTPException(404, "镜头不存在")
        delta = -1 if str(payload.get("direction")) == "up" else 1
        new = max(0, min(len(p.shots) - 1, idx + delta))
        if new != idx:
            p.shots.insert(new, p.shots.pop(idx))
            p.save()
        return {"ok": True, "order": [s.id for s in p.shots]}

    @app.post("/api/projects/{slug}/shots/{sid}/split")
    def split_shot(slug: str, sid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """在当前镜头第 N 秒切开，变成两条（共享同一素材，入点自动换算）。"""
        p = _load(slug)
        shot = p.shot(sid)
        if not shot:
            raise HTTPException(404, "镜头不存在")
        try:
            at = float(payload.get("at") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "切点不合法")
        if at < 1 or at > shot.duration - 1:
            raise HTTPException(400, f"切点要在 1~{shot.duration - 1} 秒之间")
        at = int(round(at))

        import copy

        tail = Shot.from_dict(copy.deepcopy(shot.to_dict()))
        n = len(p.shots) + 1
        while any(s.id == f"S{n:02d}" for s in p.shots):
            n += 1
        tail.id = f"S{n:02d}"
        tail.duration = shot.duration - at
        tail.trim_start = round(shot.trim_start + at * float(shot.speed or 1.0), 3)
        tail.status = "rendered" if shot.clip else "pending"
        tail.audio = ""
        tail.audio_duration = 0.0
        tail.subtitle = ""
        tail.dialogue = ""
        tail.title_text = ""
        shot.duration = at
        shot.status = "rendered" if shot.clip else "pending"

        idx = p.shots.index(shot)
        p.shots.insert(idx + 1, tail)
        for i, s in enumerate(p.shots, 1):
            s.index = i
        p.save()
        return {"ok": True, "head": _shot_payload(slug, shot), "tail": _shot_payload(slug, tail)}

    @app.post("/api/projects/{slug}/edit-command")
    def edit_command(slug: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """一句话改剪辑：DeepSeek 解析指令 → 批量改参数；没有 key 走本地规则。"""
        p = _load(slug)
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "指令不能为空")
        s = _settings()
        llm = DeepSeek(s.deepseek) if (s.deepseek.enabled and not s.pipeline.mock) else None
        result = interpret(text, p, llm)
        ops = result.get("ops") or []
        if not ops:
            return {"ok": False, "reply": result.get("reply") or "没解析出可执行的操作", "changed": []}
        changed = apply_ops(p, ops)
        if not changed:
            return {"ok": False, "reply": "操作没有命中任何字段", "changed": []}
        p.save()
        return {"ok": True, "reply": result.get("reply") or "已应用",
                "changed": changed, "ops": ops, "project": _project_payload(p)}

    @app.post("/api/projects/{slug}/shots/{sid}/duplicate")
    def duplicate_shot(slug: str, sid: str) -> Dict[str, Any]:
        p = _load(slug)
        shot = p.shot(sid)
        if not shot:
            raise HTTPException(404, "镜头不存在")
        import copy

        clone = Shot.from_dict(copy.deepcopy(shot.to_dict()))
        n = len(p.shots) + 1
        while any(s.id == f"S{n:02d}" for s in p.shots):
            n += 1
        clone.id = f"S{n:02d}"
        clone.status = "pending"
        clone.keyframe = clone.clip = clone.audio = ""
        clone.task_id = ""
        clone.audio_duration = 0.0
        idx = p.shots.index(shot)
        p.shots.insert(idx + 1, clone)
        p.save()
        return {"ok": True, "shot": _shot_payload(slug, clone)}

    @app.delete("/api/projects/{slug}/shots/{sid}")
    def delete_shot(slug: str, sid: str) -> Dict[str, Any]:
        p = _load(slug)
        before = len(p.shots)
        p.shots = [s for s in p.shots if s.id != sid]
        if len(p.shots) == before:
            raise HTTPException(404, "镜头不存在")
        for i, s in enumerate(p.shots, 1):
            s.index = i
        p.save()
        return {"ok": True, "shots": len(p.shots)}

    @app.post("/api/projects/{slug}/shots/{sid}/replace")
    async def replace_clip(slug: str, sid: str, request: Request,
                           filename: str = "upload.mp4") -> Dict[str, Any]:
        p = _load(slug)
        shot = p.shot(sid)
        if not shot:
            raise HTTPException(404, "镜头不存在")
        body = await request.body()
        if len(body) < 1024:
            raise HTTPException(400, "文件是空的")
        suffix = Path(filename).suffix.lower() or ".mp4"
        if suffix not in (".mp4", ".mov", ".m4v", ".webm", ".mkv"):
            raise HTTPException(400, "只收视频文件")
        dst = p.path("clips") / f"{shot.id}{suffix}"
        dst.write_bytes(body)
        shot.clip = str(dst)
        shot.clip_duration = probe_duration(dst)
        shot.status = "rendered"
        shot.provider = "manual"
        shot.error = ""
        shot.duration = max(1, min(15, int(round(shot.clip_duration)) or shot.duration))
        p.save()
        return {"ok": True, "shot": _shot_payload(slug, shot)}

    @app.post("/api/projects/{slug}/shots/import")
    async def import_clip(slug: str, request: Request, filename: str = "import.mp4") -> Dict[str, Any]:
        p = _load(slug)
        body = await request.body()
        if len(body) < 1024:
            raise HTTPException(400, "文件是空的")
        suffix = Path(filename).suffix.lower() or ".mp4"
        n = len(p.shots) + 1
        while any(s.id == f"S{n:02d}" for s in p.shots):
            n += 1
        sid = f"S{n:02d}"
        dst = p.path("clips") / f"{sid}{suffix}"
        dst.write_bytes(body)
        dur = probe_duration(dst) or 5.0
        shot = Shot(id=sid, index=len(p.shots) + 1, duration=max(1, min(15, int(round(dur)))),
                    scene="手动导入素材", description=Path(filename).stem,
                    dialogue="", subtitle="", status="rendered", clip=str(dst),
                    clip_duration=dur, provider="manual", transition="cut")
        p.shots.append(shot)
        p.save()
        return {"ok": True, "shot": _shot_payload(slug, shot)}

    @app.post("/api/projects/{slug}/bgm")
    async def upload_bgm(slug: str, request: Request, filename: str = "bgm.mp3") -> Dict[str, Any]:
        p = _load(slug)
        body = await request.body()
        if len(body) < 1024:
            raise HTTPException(400, "文件是空的")
        suffix = Path(filename).suffix.lower() or ".mp3"
        dst = p.path("audio") / f"bgm{suffix}"
        dst.write_bytes(body)
        p.bgm = str(dst)
        p.save()
        return {"ok": True, "bgm": str(dst)}

    @app.post("/api/projects/{slug}/shots/{sid}/revise")
    def revise_shot(slug: str, sid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        _guard_busy()

        def run(job: Job) -> Dict[str, Any]:
            s = _settings()
            d = Director(s)
            p = Project.load(_project_dir(slug))
            shot = d.revise(p, sid, str(payload.get("note") or ""))
            return {"shot": shot.id, "status": shot.status}

        job = MANAGER.submit("revise", run, slug)
        return {"job": job.id}

    # ---------------- 动作 ---------------- #
    @app.post("/api/projects/{slug}/actions")
    def project_action(slug: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        _guard_busy()
        action = str(payload.get("action") or "")
        if action not in ("voice", "keyframes", "clips", "render", "assemble", "all"):
            raise HTTPException(400, f"未知操作：{action}")
        force = bool(payload.get("force"))
        ids = payload.get("shots") or []
        _project_dir(slug)  # 校验存在

        def run(job: Job) -> Dict[str, Any]:
            s = _settings()
            d = Director(s)
            p = Project.load(_project_dir(slug))
            if p.mock:                       # mock 工程永远不碰付费接口
                s.pipeline.mock = True
            subset = [x for x in p.shots if x.id in ids] if ids else None
            result: Dict[str, Any] = {"action": action}
            if action == "voice":
                result["voice"] = d.voice(p, subset, force=force)
            elif action == "keyframes":
                result["keyframes"] = d.keyframes(p, subset, force=force)
            elif action == "clips":
                result["clips"] = d.clips(p, subset, force=force)
            elif action == "render":
                result["render"] = d.render(p, force=force, shots=subset)
            elif action == "assemble":
                result["assemble"] = d.assemble(p, force=force)
            elif action == "all":
                result["render"] = d.render(p, force=force, shots=subset)
                result["assemble"] = d.assemble(p, force=force)
            p.save()
            result["project"] = _summary(Project.load(_project_dir(slug)))
            return result

        job = MANAGER.submit(action, run, slug)
        return {"job": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, since: int = 0) -> Dict[str, Any]:
        job = MANAGER.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        data = job.to_dict(since=since)
        if job.slug:
            try:
                p = Project.load(_projects_root() / job.slug)
                data["progress"] = p.progress()
            except Exception:
                pass
        return data

    @app.get("/api/projects/{slug}/storyboard")
    def storyboard(slug: str) -> Dict[str, Any]:
        p = _load(slug)
        md = p.path("storyboard.md")
        return {"markdown": md.read_text(encoding="utf-8") if md.exists() else ""}

    # ---------------- 媒体 ---------------- #
    @app.get("/media/{slug}/{path:path}")
    def media(slug: str, path: str) -> FileResponse:
        base = _project_dir(slug).resolve()
        target = (base / path).resolve()
        if not str(target).startswith(str(base)) or not target.exists() or target.is_dir():
            raise HTTPException(404, "文件不存在")
        return FileResponse(str(target), headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


app = create_app()
