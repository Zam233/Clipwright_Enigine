"""打包交付六件套（PRD C12 / R-18，验收 V37）。

用法::

    python -m tools.pack_dist --bundle examples/mv_poc/plate_shot --out out/dist/plate_shot
    python -m tools.pack_dist --bundle examples/mv_poc/plate_shot --out out/dist/plate_shot \
        --preview out/plate_shot/preview.mp4 --json

退出码：0 = 必需件齐全（预览缺失不算失败，但会在 missing 里列出）；1 = 结构问题。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clipwright.services.mv_bundle import load_bundle, pack_dist, validate_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MV 工程包 → 交付六件套")
    parser.add_argument("--bundle", required=True, help="工程包目录（含 mv.json/cues.json）")
    parser.add_argument("--out", required=True, help="交付目录")
    parser.add_argument("--preview", default="", help="可选：预览视频路径")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        bundle = load_bundle(args.bundle)
    except FileNotFoundError as exc:
        print(f"失败：工程包不存在 {exc}", file=sys.stderr)
        return 1

    issues = validate_bundle(bundle)
    structural = [i for i in issues if i["code"] != "bundle_missing_file"]
    if structural:
        print(json.dumps({"ok": False, "issues": structural}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    result = pack_dist(
        bundle,
        args.out,
        preview=args.preview or None,
    )
    payload = {
        "ok": not [name for name in result["missing"] if name != "preview.mp4"],
        "bundle": str(bundle.root),
        "mv_id": bundle.mv_id,
        **result,
        "issues": issues,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"交付目录: {result['path']}")
        print("已生成: " + ", ".join(result["files"]))
        if result["missing"]:
            print("缺少: " + ", ".join(result["missing"]))
        if issues:
            print(f"结构提示 {len(issues)} 条")
    return 0 if payload["ok"] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
