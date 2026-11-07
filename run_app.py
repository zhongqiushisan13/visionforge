"""VisionForge 桌面端入口：双击 / python run_app.py [--serve] [--port 8765]"""

import sys


def _safe_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


if __name__ == "__main__":
    _safe_stdio()
    from videoforge.desktop import main

    sys.exit(main(sys.argv[1:]))
