"""项目打包：把一个项目连同素材装进单个 `.vfpkg`，换台机器也能接着改。

为什么不是「直接压缩 projects 目录」：
- `project.json` 里存的是绝对路径，换个盘符就全断；导入时必须把路径改回相对形式，
  再按新位置还原，否则界面上一堆 404。
- 素材动辄几百 MB，`output/` 里的成片往往不需要跟着走，所以默认不打包成片。
- 别人的包不一定干净：zip-slip、符号链接、超大解压都要在这里拦掉。

包内结构：
    manifest.json   {format, format_version, app_version, slug, exported_at, files[]}
    project.json    路径已改成相对路径（与项目目录同构）
    keyframes/ clips/ audio/ output/   只收被引用到的素材（成片默认不收）
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .schemas import Project

FORMAT = "vfpkg"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
PROJECT_JSON = "project.json"
# 解压上限：一条 4K 成片也就 1~2G，给到 8G 足够，同时挡住 zip bomb
MAX_UNCOMPRESSED = 8 * 1024 ** 3
MAX_MEMBERS = 8000
SLUG_RE = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")


class PackageError(ValueError):
    """包不合法（格式、版本、路径越界、超限）。"""


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
def _relative_to(project_dir: Path, value: str) -> Optional[str]:
    """把项目内的绝对路径转成相对路径；项目外的文件不打包。"""
    if not value:
        return None
    p = Path(value)
    if not p.is_absolute():
        p = project_dir / p
    try:
        return p.resolve().relative_to(project_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _portable_project(project: Project, mapping: Dict[str, str]) -> Dict[str, Any]:
    """把 project.json 里的路径换成包内相对路径；mapping: 原相对路径 -> 包内路径。"""
    data = project.to_dict()
    data["slug"] = project.dir.name
    data["meta"] = {k: v for k, v in (data.get("meta") or {}).items() if k != "_dir"}

    for shot in data.get("shots") or []:
        for field in ("keyframe", "clip", "audio"):
            rel = _relative_to(project.dir, str(shot.get(field) or ""))
            shot[field] = mapping.get(rel, "") if rel else ""

    for field in ("bgm", "output"):
        rel = _relative_to(project.dir, str(data.get(field) or ""))
        data[field] = mapping.get(rel, "") if rel else ""
    return data


def export_project(project: Project, dest: Path, *, include_output: bool = False) -> Dict[str, Any]:
    """导出项目包，返回 manifest（便于接口回传统计）。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    project_dir = project.dir.resolve()

    wanted: List[str] = []                      # 原相对路径（保持 keyframes/clips/audio 结构）
    for shot in project.shots:
        for field in ("keyframe", "clip", "audio"):
            rel = _relative_to(project_dir, str(getattr(shot, field) or ""))
            if rel:
                wanted.append(rel)
    rel_bgm = _relative_to(project_dir, project.bgm)
    if rel_bgm:
        wanted.append(rel_bgm)
    rel_out = _relative_to(project_dir, project.output) if include_output else None
    if rel_out:
        wanted.append(rel_out)

    mapping: Dict[str, str] = {}
    entries: List[Dict[str, Any]] = []
    missing: List[str] = []
    for rel in wanted:
        src = project_dir / rel
        if not src.exists() or src.is_dir():
            missing.append(rel)
            continue
        mapping[rel] = rel
        entries.append({"path": rel, "size": src.stat().st_size, "sha256": _sha256(src)})

    portable = _portable_project(project, mapping)
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "slug": project.dir.name,
        "title": project.title,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "include_output": bool(include_output),
        "shots": len(project.shots),
        "files": entries,
        "missing": missing,
    }

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(PROJECT_JSON, json.dumps(portable, ensure_ascii=False, indent=2))
        for rel, arc in mapping.items():
            src = project_dir / rel
            if src.exists() and src.is_file():
                zf.write(src, arc, compress_type=zipfile.ZIP_STORED)   # 已是压缩格式，别再费 CPU

    manifest["path"] = str(dest)
    manifest["size"] = dest.stat().st_size
    return manifest


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #
def _safe_member(name: str) -> bool:
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    parts = Path(name.replace("\\", "/")).parts
    if any(p in ("..", "") for p in parts):
        return False
    return True


MEDIA_PREFIXES = ("keyframes/", "clips/", "audio/", "output/", "cache/")


def _media_member(name: str) -> bool:
    """只认项目里约定的素材目录，别的一律当可疑文件。"""
    return name.replace("\\", "/").startswith(MEDIA_PREFIXES)


def _read_manifest(zf: zipfile.ZipFile) -> Dict[str, Any]:
    try:
        raw = zf.read(MANIFEST)
    except KeyError:
        raise PackageError("不是 VisionForge 项目包：缺少 manifest.json")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackageError("manifest.json 不是合法 JSON")
    if manifest.get("format") != FORMAT:
        raise PackageError(f"包格式不对：{manifest.get('format')!r}")
    version = int(manifest.get("format_version") or 0)
    if version < 1 or version > FORMAT_VERSION:
        raise PackageError(f"包版本 {version} 不受支持（本机支持 ≤ {FORMAT_VERSION}）")
    return manifest


def _free_slug(root: Path, base: str) -> str:
    clean = SLUG_RE.sub("-", (base or "imported").strip()).strip("-._") or "imported"
    clean = clean[:60]
    if not (root / clean).exists():
        return clean
    for i in range(2, 100):
        cand = f"{clean}-{i}"
        if not (root / cand).exists():
            return cand
    return f"{clean}-{int(time.time())}"


def import_package(source: Path | bytes, projects_root: Path, *,
                   slug: Optional[str] = None) -> Project:
    """把 .vfpkg 还原成一个新项目（同名已存在就自动加后缀，绝不覆盖）。"""
    projects_root = Path(projects_root)
    projects_root.mkdir(parents=True, exist_ok=True)

    if isinstance(source, bytes):
        zf = zipfile.ZipFile(io.BytesIO(source))
    else:
        zf = zipfile.ZipFile(Path(source))

    with zf:
        manifest = _read_manifest(zf)
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise PackageError(f"包内文件太多（{len(infos)} > {MAX_MEMBERS}）")
        total = 0
        for info in infos:
            if not _safe_member(info.filename):
                raise PackageError(f"包内路径不安全：{info.filename}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:                       # 符号链接一律拒绝
                raise PackageError(f"包内含符号链接：{info.filename}")
            if info.filename not in (MANIFEST, PROJECT_JSON) and not _media_member(info.filename):
                raise PackageError(f"包内出现未知路径：{info.filename}")
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise PackageError("包解压后体积超限，已拒绝")

        dest_slug = _free_slug(projects_root, slug or manifest.get("slug") or "imported")
        final_dir = projects_root / dest_slug
        tmp_dir = projects_root / f".import-{dest_slug}-{int(time.time() * 1000)}"
        tmp_dir.mkdir(parents=True)

        try:
            for info in infos:
                if info.is_dir():
                    continue
                target = (tmp_dir / info.filename).resolve()
                if not str(target).startswith(str(tmp_dir.resolve())):
                    raise PackageError(f"包内路径越界：{info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, length=1 << 20)

            data = json.loads((tmp_dir / PROJECT_JSON).read_text(encoding="utf-8"))
            data["slug"] = dest_slug

            def restore(value: Any) -> str:
                rel = str(value or "")
                if not _media_member(rel):
                    return ""
                target = (tmp_dir / rel).resolve()
                if not str(target).startswith(str(tmp_dir.resolve())) or not target.exists():
                    return ""
                return str(final_dir / rel)

            for shot in data.get("shots") or []:
                for field in ("keyframe", "clip", "audio"):
                    shot[field] = restore(shot.get(field))
                shot["task_id"] = ""
            for field in ("bgm", "output"):
                data[field] = restore(data.get(field))
            (tmp_dir / PROJECT_JSON).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            tmp_dir.rename(final_dir)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    project = Project.load(final_dir)
    project.meta["imported_from"] = manifest.get("slug") or ""
    project.meta["imported_at"] = datetime.now().isoformat(timespec="seconds")
    project.save()
    return project


def inspect_package(source: Path | bytes) -> Dict[str, Any]:
    """读 manifest 做预览，不解压素材（设置页/导入前确认用）。"""
    if isinstance(source, bytes):
        zf = zipfile.ZipFile(io.BytesIO(source))
    else:
        zf = zipfile.ZipFile(Path(source))
    with zf:
        manifest = _read_manifest(zf)
    return {
        "slug": manifest.get("slug"),
        "title": manifest.get("title"),
        "app_version": manifest.get("app_version"),
        "exported_at": manifest.get("exported_at"),
        "shots": manifest.get("shots"),
        "files": len(manifest.get("files") or []),
        "size": sum(int(f.get("size") or 0) for f in manifest.get("files") or []),
        "include_output": manifest.get("include_output"),
        "missing": manifest.get("missing") or [],
    }
