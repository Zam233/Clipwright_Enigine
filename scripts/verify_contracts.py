"""X01b：双仓契约 fixture 同字节校验（CI/维护窗口探针）。

后端 `tests/contracts/agent_result_v2/*.json` 由 `scripts/dump_contracts.py` 生成，
前端 `src/services/api/contracts/agent_result_v2/*.json` 必须与它**逐字节相同**。

用法：
    python scripts/verify_contracts.py
    python scripts/verify_contracts.py --frontend J:\\Clipweight-Client

退出码：0 = 一致；1 = 缺文件/内容漂移/前端仓不存在（可用 --allow-missing 放宽）。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tests" / "contracts" / "agent_result_v2"
FRONTEND_REL = Path("src") / "services" / "api" / "contracts" / "agent_result_v2"
DEFAULT_FRONTEND = Path(r"J:\Clipweight-Client")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", default=str(DEFAULT_FRONTEND))
    parser.add_argument("--allow-missing", action="store_true",
                        help="前端仓不在本机时不算失败（CI 单仓场景）")
    args = parser.parse_args(argv)

    backend_files = sorted(BACKEND.glob("*.json"))
    if not backend_files:
        print(f"[FAIL] 后端 fixture 缺失：{BACKEND}")
        return 1

    frontend = Path(args.frontend) / FRONTEND_REL
    if not frontend.is_dir():
        msg = f"[SKIP] 前端 fixture 目录不存在：{frontend}"
        print(msg)
        return 0 if args.allow_missing else 1

    failed: list[str] = []
    for path in backend_files:
        mirror = frontend / path.name
        if not mirror.is_file():
            failed.append(f"{path.name}: 前端缺文件")
            continue
        a, b = _sha(path), _sha(mirror)
        if a != b:
            failed.append(f"{path.name}: sha 漂移 backend={a[:12]} frontend={b[:12]}")

    frontend_only = {p.name for p in frontend.glob("*.json")} - {p.name for p in backend_files}
    if frontend_only:
        failed.append("前端多出 fixture：" + ", ".join(sorted(frontend_only)))

    if failed:
        print("[FAIL] 双仓契约不一致：")
        for line in failed:
            print("  -", line)
        print("修复：python scripts/dump_contracts.py --frontend-out <前端目录>")
        return 1

    print(f"[OK] {len(backend_files)} 份 fixture 双仓同字节（{frontend}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
