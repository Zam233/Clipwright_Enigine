"""远程渲染 Worker 启动入口 — ``python -m clipwright.worker``。

默认监听 0.0.0.0:8100；端口可用 ``--port`` 参数或环境变量
``CLIPWRIGHT_WORKER_PORT`` 覆盖（参数优先）。
"""

from __future__ import annotations

import argparse
import os

import uvicorn

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8100


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="clipwright.worker",
        description="启动 ClipWright 远程渲染 Worker",
    )
    parser.add_argument("--host", default=_DEFAULT_HOST, help="监听地址（默认 0.0.0.0）")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="监听端口（默认 8100，环境变量 CLIPWRIGHT_WORKER_PORT 可覆盖）",
    )
    args = parser.parse_args()

    port = args.port or int(os.environ.get("CLIPWRIGHT_WORKER_PORT", _DEFAULT_PORT))
    uvicorn.run("clipwright.worker.api:app", host=args.host, port=port)


if __name__ == "__main__":
    main()
