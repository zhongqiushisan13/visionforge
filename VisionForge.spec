# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir 模式（启动快，WebView2 DLL 不用每次解压）。"""

import os
import sys
import pathlib

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = pathlib.Path(SPECPATH)
APP_NAME = "VisionForge"
ICON = str(ROOT / "assets" / "visionforge.ico")

datas = [
    (str(ROOT / "videoforge" / "web" / "static"), "videoforge/web/static"),
    (str(ROOT / "workflows"), "workflows"),
    (str(ROOT / "examples"), "examples"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "config.example.json"), "."),
    (str(ROOT / "README.md"), "."),
]

binaries = []
hiddenimports = [
    "clr",
    "pythonnet",
    "videoforge.llm_providers",
    "anyio._backends._asyncio",
    "uvicorn.logging",
    "uvicorn.lifespan.on",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "email.mime.text",
    "email.mime.multipart",
]

# 把带二进制/动态导入的包整体收进来（WebView2 运行时、ffmpeg、TTS、ASGI 栈）
for pkg in ("webview", "imageio_ffmpeg", "uvicorn", "fastapi", "starlette",
            "edge_tts", "h11", "anyio", "pydantic"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # 某个包缺失不该拦住建包
        print(f"[spec] skip {pkg}: {exc}")

# pythonnet 的 clr.pyd / Python.Runtime.dll 不在包目录里，手动带上
try:
    import clr

    clr_dir = pathlib.Path(clr.__file__).parent
    for name in ("clr.pyd", "Python.Runtime.dll"):
        p = clr_dir / name
        if p.exists():
            binaries.append((str(p), "."))
except Exception as exc:
    print(f"[spec] pythonnet binaries skipped: {exc}")

a = Analysis(
    [str(ROOT / "run_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "pandas", "PIL",
              "notebook", "IPython", "pytest", "setuptools", "pip"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=str(ROOT / "build" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
