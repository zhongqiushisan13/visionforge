"""一键打包成 Windows 桌面应用（dist/VisionForge/VisionForge.exe）。

用法：
    python build_app.py            # 生成图标 + 打包
    python build_app.py --clean    # 先删 build/dist
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Windows 控制台默认不是 UTF-8（CI 上是 cp1252），中文日志会直接 UnicodeEncodeError 把构建打断
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "VisionForge"
VERSION_INFO = ROOT / "build" / "version_info.txt"

VERSION_TEXT = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 2, 2, 0), prodvers=(1, 2, 2, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'TONWIN'),
        StringStruct('FileDescription', 'VisionForge AI 视频编导台'),
        StringStruct('FileVersion', '1.2.2.0'),
        StringStruct('InternalName', 'VisionForge'),
        StringStruct('OriginalFilename', 'VisionForge.exe'),
        StringStruct('ProductName', 'VisionForge'),
        StringStruct('ProductVersion', '1.2.2.0'),
        StringStruct('LegalCopyright', 'Copyright (C) 2026')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def step(msg: str) -> None:
    print(f"\n\033[36m==> {msg}\033[0m" if sys.stdout.isatty() else f"\n==> {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="清理 build/dist 后重打")
    ap.add_argument("--no-icon", action="store_true")
    args = ap.parse_args()

    if args.clean and (ROOT / "build").exists():
        step("清理 build/")
        shutil.rmtree(ROOT / "build", ignore_errors=True)

    # 不直接删 dist：整目录批量删除会被安全策略拦。
    # 先把旧产物改名挪开，构建成功后由人/脚本自行清理。
    if DIST.exists():
        old = DIST.with_name(f"VisionForge.old.{int(time.time())}")
        step(f"旧产物改名 -> {old.name}")
        DIST.rename(old)

    icon = ROOT / "assets" / "visionforge.ico"
    if not args.no_icon:
        # 图标已入库时直接复用：CI 不该为了画图标再装 Pillow
        if icon.exists():
            step(f"复用已有图标 {icon.name}")
        else:
            step("生成应用图标")
            subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)

    step("写入版本信息")
    VERSION_INFO.parent.mkdir(parents=True, exist_ok=True)
    VERSION_INFO.write_text(VERSION_TEXT, encoding="utf-8")

    step("PyInstaller 打包（首次约 2~5 分钟）")
    t0 = time.time()
    import PyInstaller.__main__

    PyInstaller.__main__.run([
        str(ROOT / "VisionForge.spec"),
        "--noconfirm",
        "--clean",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
    ])
    elapsed = time.time() - t0

    exe = DIST / "VisionForge.exe"
    if not exe.exists():
        print("打包失败：没有找到 exe")
        return 1

    # 把用户真正要碰的东西放到 exe 旁边（否则都埋在 _internal 里，谁也找不着）
    step("整理发布目录")
    for name in (".env.example", "README.md", "config.example.json"):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, DIST / name)
    if (DIST / "examples").exists():
        shutil.rmtree(DIST / "examples", ignore_errors=True)
    if (ROOT / "examples").exists():
        shutil.copytree(ROOT / "examples", DIST / "examples", dirs_exist_ok=True)
    (DIST / "projects").mkdir(exist_ok=True)
    (DIST / "logs").mkdir(exist_ok=True)

    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1048576
    print(f"\n\033[32m打包完成\033[0m  {exe}")
    print(f"目录体积 {size:.0f} MB，用时 {elapsed:.0f}s")
    print("首次运行会在 exe 同级生成 projects/ 与 logs/；把 .env.example 复制成 .env 填 key。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
