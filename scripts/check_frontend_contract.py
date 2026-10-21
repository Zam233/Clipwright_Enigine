"""前端 API 契约对账：把前端实际调用的 `/api/...` 路径与后端路由表比对。

**为什么写这个**：前端 bug 里最难靠单测发现的一类是"调了不存在的端点"——
组件测试都打桩，类型检查也发现不了字符串路径写错。这里直接把两边对起来。

输出：
- 前端调用但后端没有（真 bug：必定 404）
- 后端有但前端没调（只是信息，不算 bug）

用法::

    python scripts/check_frontend_contract.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 前端仓库默认位置（与本仓库同级）
CLIENT_DEFAULT = Path("J:/Clipweight-Client")

_PATH_RE = re.compile(r"""['"`](/api/[^'"`$]*)['"`]""")
#: 模板串里的 `${...}` 段 -> `{}`，用于与后端 `{param}` 风格对齐
_TEMPLATE_RE = re.compile(r"\$\{[^}]*\}")


def frontend_paths(client_root: Path) -> dict[str, set[str]]:
    """返回 {路径模板: {出现文件}}。

    只统计**指向 Clipwright 后端**的调用：`account.ts` 这类走 `/srv`（账号/市场服务，
    8090 独立仓库）的客户端整文件跳过——它们的 `/api/auth`、`/api/credit` 不在本后端，
    把它们算成"缺失"是误报。
    """
    found: dict[str, set[str]] = {}
    api_dir = client_root / "src" / "services" / "api"
    if not api_dir.is_dir():
        return found
    for path in api_dir.rglob("*.ts"):
        if path.name.endswith(".test.ts"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _is_other_service(text):
            continue
        for match in _PATH_RE.finditer(text):
            raw = match.group(1)
            normalized = _normalize(raw)
            if not normalized.startswith("/api/"):
                continue
            found.setdefault(normalized, set()).add(str(path.relative_to(client_root)))
    return found


def _is_other_service(text: str) -> bool:
    """文件是否是"另一个服务"的客户端（baseURL 指向 /srv 等）。"""
    return bool(re.search(r"baseURL:\s*['\"]/(srv|account|market)", text))


def _normalize(path: str) -> str:
    """把 `${id}` / 拼接参数统一成 `{}`，去掉查询串与尾部斜杠。"""
    cleaned = _TEMPLATE_RE.sub("{}", path)
    cleaned = re.split(r"[?#]", cleaned, maxsplit=1)[0]
    cleaned = re.sub(r"\$\{[^}]*\}", "{}", cleaned)
    cleaned = cleaned.rstrip("/")
    return cleaned or "/"


def backend_paths() -> set[str]:
    """从 FastAPI 路由表取后端真实路径（同样归一成 `{}`）。"""
    from clipwright.main import app

    out: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        normalized = re.sub(r"\{[^}]*\}", "{}", path).rstrip("/")
        out.add(normalized or "/")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="前端 API 契约对账")
    parser.add_argument("--client", default=str(CLIENT_DEFAULT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    client_root = Path(args.client)
    if not client_root.is_dir():
        print(f"前端仓库不存在：{client_root}")
        return 2
    front = frontend_paths(client_root)
    back = backend_paths()

    missing = sorted(p for p in front if p not in back)
    unused = sorted(p for p in back if p not in front)
    report = {
        "ok": not missing,
        "frontend_paths": len(front),
        "backend_paths": len(back),
        "missing_in_backend": [{"path": p, "files": sorted(front[p])} for p in missing],
        "backend_only_count": len(unused),
        "backend_only_sample": unused[:15],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"前端路径 {report['frontend_paths']} 条 / 后端路由 {report['backend_paths']} 条")
        if missing:
            print("\n【前端调了但后端没有 —— 必定 404】")
            for item in report["missing_in_backend"]:
                print(f"  {item['path']}   ← {', '.join(item['files'])}")
        else:
            print("\n前端调用的每个 /api 路径后端都有 ✅")
        print(f"\n后端有但前端未调：{report['backend_only_count']} 条（信息，不算 bug）")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
