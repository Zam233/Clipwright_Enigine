"""路由清单生成器（PRD B12 / G15）。

为什么需要：`docs/api_reference.md` 与 `docs/frontend-backend-parity.md` 里的路由数量
是**手抄快照**（225/236 条），代码一改就漂移，排障时按文档找不到接口。
这里直接从 FastAPI 的 `app.routes` 导出 `METHOD PATH` 清单，文档引用生成物即可。

用法::

    python scripts/route_inventory.py                       # 打印到 stdout
    python scripts/route_inventory.py --out docs/route_inventory.txt
    python scripts/route_inventory.py --json                # 机器可读

说明：
- 只导出去重后的 `METHOD PATH`（HEAD/OPTIONS 略过），按 path 排序；
- `--prefix` 可按前缀过滤（例如 `--prefix /api/task`）；
- 不依赖运行中的服务，不需要数据库；导入 app 只做注册。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SKIP_METHODS = {"HEAD", "OPTIONS"}


def collect_routes(prefix: str = "") -> list[str]:
    """返回 `["GET /api/task", ...]`（去重、排序）。"""
    from clipwright.main import app  # 延迟导入：避免脚本被 import 时拉起重依赖

    rows: set[str] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = str(getattr(route, "path", "") or "")
        if not methods or not path:
            continue
        if prefix and not path.startswith(prefix):
            continue
        for method in methods:
            if method in _SKIP_METHODS:
                continue
            rows.add(f"{method} {path}")
    return sorted(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 Clipwright 后端路由清单")
    parser.add_argument("--out", default="", help="写入文件（缺省打印到 stdout）")
    parser.add_argument("--prefix", default="", help="只导出该路径前缀下的路由")
    parser.add_argument("--json", action="store_true", help="输出 JSON（含条数）")
    args = parser.parse_args(argv)

    routes = collect_routes(prefix=args.prefix)
    if args.json:
        text = json.dumps({"count": len(routes), "routes": routes}, ensure_ascii=False, indent=2)
    else:
        header = f"# 自动生成：python scripts/route_inventory.py（共 {len(routes)} 条）"
        text = "\n".join([header, *routes])

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"[OK] {len(routes)} routes -> {target}")
    else:
        # Windows 控制台可能是 cp936，避免 ✅ 之类字符炸掉
        sys.stdout.reconfigure(errors="replace") if hasattr(sys.stdout, "reconfigure") else None
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
