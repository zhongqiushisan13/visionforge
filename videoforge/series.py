"""系列与素材库：短剧的连续性资产。

为什么挂在「系列」而不是「项目」：一集是一个项目，角色和场景却要跨集共用。
库在系列层，第二集新建时直接继承，人物和客厅不用重建。

目录结构：
    series/<slug>/
        series.json
        characters/<id>/main.png, full.png, face_1.png ...
        scenes/<id>/wide.png, angle_1.png ...
        props/<id>/main.png ...

每张图都记来源（ai / upload / stock）与许可说明——短剧要商用，版权得能自证。
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import APP_ROOT
from .utils import ensure_dir, find_ffmpeg, log, now_iso, read_json, run, warn, write_json

KINDS = ("characters", "scenes", "props")
KIND_LABEL = {"characters": "角色", "scenes": "场景", "props": "道具"}
IMAGE_ROLES = {
    "characters": ("main", "full", "face"),
    "scenes": ("wide", "angle"),
    "props": ("main",),
}
MAX_ASSETS = 200


def series_root() -> Path:
    return APP_ROOT / "series"


def _safe_id(text: str, prefix: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", str(text or "")).strip("-").lower()[:24]
    return slug or f"{prefix}-{int(time.time() * 1000) % 100000}"


def series_dir(slug: str) -> Path:
    return series_root() / Path(slug).name


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def _blank(title: str = "未命名系列") -> Dict[str, Any]:
    return {
        "slug": "", "title": title, "style_prompt": "", "negative_prompt": "",
        "created_at": now_iso(), "updated_at": now_iso(),
        "characters": [], "scenes": [], "props": [],
    }


def create_series(title: str, *, style_prompt: str = "", negative_prompt: str = "",
                  slug: str = "") -> Dict[str, Any]:
    root = series_root()
    ensure_dir(root)
    base = _safe_id(slug or title, "series")
    final, n = base, 2
    while (root / final).exists():
        final, n = f"{base}-{n}", n + 1
    data = _blank(title.strip() or base)
    data["slug"] = final
    data["style_prompt"] = style_prompt.strip()
    data["negative_prompt"] = negative_prompt.strip()
    save_series(data)
    return data


def save_series(data: Dict[str, Any]) -> Dict[str, Any]:
    slug = str(data.get("slug") or "").strip()
    if not slug:
        raise ValueError("系列缺少 slug")
    data["updated_at"] = now_iso()
    d = series_dir(slug)
    ensure_dir(d)
    write_json(d / "series.json", data)
    return data


def load_series(slug: str) -> Dict[str, Any]:
    d = series_dir(slug)
    data = read_json(d / "series.json", default=None)
    if not isinstance(data, dict):
        raise FileNotFoundError(f"系列不存在：{slug}")
    data.setdefault("slug", d.name)
    for kind in KINDS:
        data.setdefault(kind, [])
    return data


def list_series() -> List[Dict[str, Any]]:
    root = series_root()
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for d in root.iterdir():
        if not (d / "series.json").exists():
            continue
        try:
            s = load_series(d.name)
        except (OSError, ValueError):
            continue
        out.append({
            "slug": s["slug"], "title": s.get("title") or s["slug"],
            "updated_at": s.get("updated_at"), "created_at": s.get("created_at"),
            "characters": len(s.get("characters") or []),
            "scenes": len(s.get("scenes") or []),
            "props": len(s.get("props") or []),
            "style_prompt": s.get("style_prompt") or "",
            "cover": _cover(s),
        })
    return sorted(out, key=lambda x: x.get("updated_at") or "", reverse=True)


def _cover(s: Dict[str, Any]) -> str:
    for kind in KINDS:
        for asset in s.get(kind) or []:
            main = (asset.get("images") or {}).get("main") or (asset.get("images") or {}).get("wide")
            if main:
                return main
    return ""


def _find(data: Dict[str, Any], kind: str, aid: str) -> Optional[Dict[str, Any]]:
    for asset in data.get(kind) or []:
        if asset.get("id") == aid:
            return asset
    return None


# --------------------------------------------------------------------------- #
# 素材增删改
# --------------------------------------------------------------------------- #
def add_asset(slug: str, kind: str, name: str, *, look_en: str = "",
              desc_en: str = "", asset_id: str = "", seed: int = 0,
              source: str = "ai") -> Dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"未知素材类型：{kind}")
    data = load_series(slug)
    if sum(len(data.get(k) or []) for k in KINDS) >= MAX_ASSETS:
        raise ValueError(f"单个系列最多 {MAX_ASSETS} 个素材")
    aid = _safe_id(asset_id or name, kind[:-1])
    if _find(data, kind, aid):
        aid = f"{aid}-{int(time.time() * 1000) % 1000}"
    asset = {
        "id": aid, "name": name.strip() or aid,
        "look_en" if kind == "characters" else "desc_en": (look_en or desc_en).strip(),
        "images": {"main": ""} if kind != "scenes" else {"wide": ""},
        "seed": int(seed or 0), "source": source,
        "created_at": now_iso(),
    }
    if kind == "characters":
        asset["images"]["full"] = ""
    ensure_dir(asset_dir(slug, kind, aid))
    data.setdefault(kind, []).append(asset)
    save_series(data)
    return asset


def update_asset(slug: str, kind: str, aid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_series(slug)
    asset = _find(data, kind, aid)
    if not asset:
        raise KeyError(f"素材不存在：{aid}")
    for key in ("name", "look_en", "desc_en", "source", "license", "note"):
        if key in patch:
            asset[key] = str(patch[key]).strip()
    if "seed" in patch:
        try:
            asset["seed"] = int(patch["seed"] or 0)
        except (TypeError, ValueError):
            pass
    save_series(data)
    return asset


def delete_asset(slug: str, kind: str, aid: str) -> None:
    data = load_series(slug)
    before = len(data.get(kind) or [])
    data[kind] = [a for a in (data.get(kind) or []) if a.get("id") != aid]
    if len(data[kind]) == before:
        raise KeyError(f"素材不存在：{aid}")
    save_series(data)
    shutil.rmtree(series_dir(slug) / kind / Path(aid).name, ignore_errors=True)


def asset_dir(slug: str, kind: str, aid: str) -> Path:
    return series_dir(slug) / kind / Path(aid).name


def set_asset_image(slug: str, kind: str, aid: str, role: str,
                    data: bytes, suffix: str = ".png") -> str:
    """保存一张素材图，返回相对 series 目录的路径（project.json 里就存这个）。"""
    if kind not in KINDS:
        raise ValueError(f"未知素材类型：{kind}")
    role = re.sub(r"[^0-9a-zA-Z_]", "", role or "main") or "main"
    if suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        suffix = ".png"
    series = load_series(slug)
    asset = _find(series, kind, aid)
    if not asset:
        raise KeyError(f"素材不存在：{aid}")
    d = ensure_dir(asset_dir(slug, kind, aid))
    target = d / f"{role}{suffix.lower()}"
    target.write_bytes(data)
    rel = target.relative_to(series_dir(slug)).as_posix()
    images = asset.setdefault("images", {})
    if role in ("main", "full", "wide"):
        images[role] = rel
    else:
        extras = [p for p in images.get("extras", []) if Path(p).name != target.name]
        extras.append(rel)
        images["extras"] = extras[:12]
    save_series(series)
    return rel


# --------------------------------------------------------------------------- #
# 提示词与末帧
# --------------------------------------------------------------------------- #
def compose_prompt(series: Dict[str, Any], shot: Any) -> str:
    """把选中的角色/场景拼成英文补充描述，追加到 image_prompt 后面。

    只拼「外形」，不拼剧情——剧情已经在分镜提示词里了，重复会互相打架。
    """
    parts: List[str] = []
    cast = getattr(shot, "cast", None) or []
    for cid in cast:
        asset = _find(series, "characters", cid)
        look = (asset or {}).get("look_en") or ""
        if look:
            parts.append(f"character {asset.get('name') or cid}: {look}")
    scene_ref = getattr(shot, "scene_ref", "") or ""
    if scene_ref:
        asset = _find(series, "scenes", scene_ref)
        desc = (asset or {}).get("desc_en") or ""
        if desc:
            parts.append(f"location {asset.get('name') or scene_ref}: {desc}")
    return "; ".join(parts)


def asset_path(series_slug: str, rel: str) -> Optional[Path]:
    if not rel:
        return None
    p = (series_dir(series_slug) / rel).resolve()
    try:
        p.relative_to(series_dir(series_slug).resolve())
    except ValueError:
        return None
    return p if p.exists() else None


def extract_last_frame(clip: Path | str, out_png: Path | str, *, offset: float = 0.15) -> bool:
    """抽一段视频的最后一帧，用来给下一个镜头接戏。

    多取一点余量（默认往前 0.15s）：H3 的尾帧常有轻微糊化，太贴边会拿到过渡帧。
    """
    clip, out_png = Path(clip), Path(out_png)
    if not clip.exists():
        return False
    ensure_dir(out_png.parent)
    ff = find_ffmpeg()
    proc = run([ff, "-y", "-hide_banner", "-loglevel", "error",
                "-sseof", f"-{max(0.05, offset):.2f}", "-i", str(clip),
                "-frames:v", "1", "-q:v", "2", str(out_png)], timeout=120)
    if out_png.exists() and out_png.stat().st_size > 1024:
        return True
    warn(f"抽末帧失败：{clip.name} {proc.stderr[-200:] if proc.stderr else ''}")
    return False
