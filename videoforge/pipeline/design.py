"""设定图：开拍前把角色三视图和场景板定下来，后续所有镜头吃同一套参考。

为什么要单独一步：
- 关键帧走 IP-Adapter，参考图一旦每镜不同，人脸就会飘；
- 所以先出一张「定妆三视图」和一张「空镜板」，锁定（locked）后不再重出，
  再让每镜的 cast / scene_ref 指过去，一致性才有落点。

约定：
- 角色图存 images.main，场景图存 images.wide（与素材库的角色/场景语义一致）；
- 种子由名字哈希得到，改名会换种子，不改名重跑结果稳定；
- 已 locked 且已有图时跳过，除非显式 force。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

from ..schemas import Project, Shot
from ..series import add_asset, create_series, load_series, save_series, set_asset_image
from ..utils import ffmpeg, log, warn

SCENE_SIZE = (1216, 704)         # 16:9 空镜板

CHAR_NEG = (
    "close-up, waist-up, cropped legs, cropped feet, text, watermark, logo, extra people, "
    "deformed hands, extra limbs, blurry, lowres, busy background, anime, illustration"
)
SCENE_PROMPT = (
    "empty establishing shot of {look}, no people, no text, cinematic lighting, "
    "wide angle, photorealistic film still, detailed set dressing"
)
SCENE_NEG = "people, person, crowd, text, watermark, logo, deformed, lowres, blurry"

# 单张「三视图」SDXL 排不出版面，改成三张单人视图分别出，再横向拼成设定表
CHAR_VIEWS = (
    ("front", "wide full body shot of {look}, front view, standing straight, "
              "entire figure from head to shoes clearly visible, small figure in frame, "
              "plain light grey studio backdrop, photorealistic, soft even light, sharp focus, no text"),
    ("side", "wide full body shot of {look}, side profile facing left, standing straight, "
             "entire figure from head to shoes clearly visible, small figure in frame, "
             "plain light grey studio backdrop, photorealistic, soft even light, sharp focus, no text"),
    ("back", "wide full body shot of {look}, seen from behind, back view, standing straight, "
             "entire figure from head to shoes clearly visible, small figure in frame, "
             "plain light grey studio backdrop, photorealistic, soft even light, sharp focus, no text"),
)
CHAR_VIEW_SIZE = (704, 1152)      # 单张 3:4 竖版，全身放得下


def _seed(name: str, base: int) -> int:
    h = int(hashlib.sha1((name or "x").encode("utf-8")).hexdigest()[:8], 16)
    return base + h % 90_000


def _patch(slug: str, kind: str, aid: str, patch: Dict[str, Any]) -> None:
    """素材字段里 locked / prompt 这类不在 update_asset 白名单里，直接改 JSON。"""
    data = load_series(slug)
    for asset in data.get(kind) or []:
        if asset.get("id") == aid:
            asset.update(patch)
            break
    save_series(data)


class Designer:
    def __init__(self, providers):
        self.providers = providers

    # ------------------------------------------------------------------ #
    def _series(self, project: Project) -> Dict[str, Any]:
        if project.series_id:
            try:
                return load_series(project.series_id)
            except Exception as exc:
                warn(f"系列 {project.series_id} 读不出来，重建一个：{exc}")
        data = create_series(f"{project.title or '未命名'} · 素材",
                             style_prompt=project.script.style_prompt or "")
        project.series_id = data["slug"]
        return data

    def _asset(self, series: Dict[str, Any], kind: str, name: str,
               look_en: str, seed: int) -> Dict[str, Any]:
        for a in series.get(kind) or []:
            if a.get("name") == name:
                return a
        asset = add_asset(project_series(series), kind, name, look_en=look_en, seed=seed)
        series.setdefault(kind, []).append(asset)
        return asset

    # ------------------------------------------------------------------ #
    def _character(self, project: Project, series: Dict[str, Any], name: str,
                   look_en: str, force: bool) -> bool:
        """三张单人视图（正/侧/背）→ 横拼成设定表；正脸存 main 给 IP-Adapter 用。"""
        seed = _seed(name, 7_000)
        asset = self._asset(series, "characters", name, look_en, seed)
        if (asset.get("images") or {}).get("main") and asset.get("locked") and not force:
            return False
        comfy = self.providers.comfy
        if comfy is None:
            raise RuntimeError("设定图需要本地 ComfyUI：到「设置 → 本地出图环境」配置后再试")

        style = (project.script.style_prompt or "").strip()
        prev = (comfy.cfg.width, comfy.cfg.height)
        comfy.cfg.width, comfy.cfg.height = CHAR_VIEW_SIZE
        paths = []
        ref = None
        try:
            for key, tpl in CHAR_VIEWS:
                prompt = tpl.format(look=look_en or name)
                if style:
                    prompt = f"{prompt}, {style}"
                out = project.path("cache") / f"design_{asset['id']}_{key}.png"
                shot = Shot(id=f"design-{asset['id']}-{key}", duration=1,
                            image_prompt=prompt, negative_prompt=CHAR_NEG, seed=seed)
                result = comfy.generate_keyframe(shot, out, ref_image=ref)
                if not result.ok or not Path(result.path).exists():
                    raise RuntimeError(f"{name} 的 {key} 视图没出来：{shot.error or '未产出文件'}")
                paths.append(Path(result.path))
                if key == "front":
                    ref = Path(result.path)      # 侧/背以正面为参考，锁住同一张脸
        finally:
            comfy.cfg.width, comfy.cfg.height = prev

        slug = project_series(series)
        sheet = project.path("cache") / f"design_{asset['id']}_sheet.png"
        if not self._hstack(paths, sheet):
            sheet = paths[0]
        rel_main = set_asset_image(slug, "characters", asset["id"], "main", paths[0].read_bytes())
        rel_sheet = set_asset_image(slug, "characters", asset["id"], "sheet", sheet.read_bytes())
        for (key, _), p in zip(CHAR_VIEWS[1:], paths[1:]):
            set_asset_image(slug, "characters", asset["id"], key, p.read_bytes())
        _patch(slug, "characters", asset["id"],
               {"seed": seed, "locked": True, "sheet": rel_sheet,
                "views": [k for k, _ in CHAR_VIEWS]})
        asset.setdefault("images", {})["main"] = rel_main
        asset["locked"] = True
        log(f"{name} 三视图已固定（seed {seed}）", tag="design")
        return True

    def _hstack(self, paths, dst: Path) -> bool:
        if len(paths) < 2:
            return False
        # 占位图/坏图就别浪费一次 ffmpeg
        if any(not Path(p).exists() or Path(p).stat().st_size < 1024 for p in paths):
            return False
        args = []
        for p in paths:
            args += ["-i", str(p)]
        filt = "".join(f"[{i}:v]" for i in range(len(paths))) + f"hstack=inputs={len(paths)}"
        try:
            ffmpeg([*args, "-filter_complex", filt, "-y", str(dst)], timeout=180)
            return dst.exists()
        except Exception as exc:
            warn(f"设定表拼接失败（用正视图代替）：{exc}")
            return False

    # ------------------------------------------------------------------ #
    def _generate(self, project: Project, series: Dict[str, Any], kind: str,
                  name: str, look_en: str, prompt_tpl: str, negative: str,
                  size: tuple, role: str, base_seed: int, force: bool) -> bool:
        if not name:
            return False
        seed = _seed(name, base_seed)
        asset = self._asset(series, kind, name, look_en, seed)
        has_image = bool((asset.get("images") or {}).get(role))
        if has_image and asset.get("locked") and not force:
            return False
        if self.providers.comfy is None:
            raise RuntimeError("设定图需要本地 ComfyUI：到「设置 → 本地出图环境」配置后再试")

        style = (project.script.style_prompt or "").strip()
        prompt = prompt_tpl.format(look=look_en or name)
        if style:
            prompt = f"{prompt}, {style}"
        out = project.path("cache") / f"design_{kind}_{asset['id']}.png"
        shot = Shot(id=f"design-{asset['id']}", duration=1, image_prompt=prompt,
                    negative_prompt=negative, seed=seed)
        comfy = self.providers.comfy
        prev = (comfy.cfg.width, comfy.cfg.height)
        comfy.cfg.width, comfy.cfg.height = size
        try:
            result = comfy.generate_keyframe(shot, out)
        finally:
            comfy.cfg.width, comfy.cfg.height = prev
        if not result.ok or not Path(result.path).exists():
            raise RuntimeError(f"{name} 设定图生成失败：{shot.error or '未产出文件'}")

        rel = set_asset_image(project_series(series), kind, asset["id"], role,
                              Path(result.path).read_bytes())
        _patch(project_series(series), kind, asset["id"],
               {"seed": seed, "prompt": prompt, "locked": True, "images_ready": True})
        asset.setdefault("images", {})[role] = rel
        asset["locked"] = True
        log(f"{name} 设定图已固定（{kind}，seed {seed}）", tag="design")
        return True

    # ------------------------------------------------------------------ #
    def run(self, project: Project, force: bool = False) -> Dict[str, int]:
        script = project.script
        chars = [c for c in (script.characters or []) if c.name]
        scenes = [s for s in (script.scenes or []) if s.name]
        if not chars and not scenes:
            return {"characters": 0, "scenes": 0, "skipped": 0, "note": "剧本没有角色/场景"}

        series = self._series(project)
        stats = {"characters": 0, "scenes": 0, "skipped": 0}
        for ch in chars:
            if self._character(project, series, ch.name, ch.look_en, force):
                stats["characters"] += 1
            else:
                stats["skipped"] += 1
        for sc in scenes:
            if self._generate(project, series, "scenes", sc.name, sc.look_en,
                              SCENE_PROMPT, SCENE_NEG, SCENE_SIZE, "wide", 9_000, force):
                stats["scenes"] += 1
            else:
                stats["skipped"] += 1

        self.assign(project, series)
        project.meta["design"] = {"characters": stats["characters"], "scenes": stats["scenes"],
                                  "skipped": stats["skipped"], "locked": True}
        return stats

    # ------------------------------------------------------------------ #
    def assign(self, project: Project, series: Optional[Dict[str, Any]] = None) -> int:
        """把镜头的 cast / scene_ref 指到设定图，后面出图就自动吃参考。"""
        series = series or (load_series(project.series_id) if project.series_id else {})
        chars = series.get("characters") or []
        scenes = series.get("scenes") or []
        changed = 0
        for shot in project.shots:
            text = " ".join(x for x in (shot.description, shot.scene, shot.dialogue,
                                        shot.speaker, shot.act) if x)
            picked = [a["id"] for a in chars if a.get("name") and a["name"] in text]
            if picked and shot.cast != picked[:2]:
                shot.cast = picked[:2]
                changed += 1
            elif not shot.cast and chars:
                shot.cast = [chars[0]["id"]]
                changed += 1
            name = shot.scene or ""
            match = next((a for a in scenes if a.get("name") and
                          (a["name"] in name or (name and name in a["name"]))), None)
            target = match["id"] if match else (scenes[0]["id"] if scenes and not shot.scene_ref else "")
            if target and shot.scene_ref != target:
                shot.scene_ref = target
                changed += 1
        return changed


def project_series(series: Dict[str, Any]) -> str:
    slug = str(series.get("slug") or "").strip()
    if not slug:
        raise ValueError("系列缺少 slug")
    return slug
