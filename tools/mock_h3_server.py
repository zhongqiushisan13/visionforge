"""本地 H3 兼容网关的最小模拟实现（开发/联调用，不参与打包）。

    python tools/mock_h3_server.py --port 8798 --video path/to/clip.mp4

然后：
    MINIMAX_MODE=local
    MINIMAX_BASE_URL=http://127.0.0.1:8798
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tests.fixtures.mock_h3_server import serve


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8798)
    ap.add_argument("--video", required=True)
    args = ap.parse_args()
    serve(args.port, Path(args.video))


if __name__ == "__main__":
    main()
