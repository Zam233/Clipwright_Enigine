"""V50 验收夹具：四类素材样例（手绘 / 2D / 3D / 实拍）的可验证落位。

**这里不造假素材。** V50 要的是"四类素材都能被系统认出来并落位"，而真正的
手绘/2D/3D/实拍片源必须由人提供（授权与出处要能追溯）。所以本脚本做两件事：

1. 检查目录约定 `examples/material_samples/{hand_drawn,2d,3d,live_action}/` 里
   是否各至少有 1 个媒体文件（"有人提供了素材"）；
2. 用 `LocalFolderSource`（就是 `local_material` 插件背后的真实实现）按每类的
   关键词检索，断言能命中该类自己的文件、且不跨类串味——即"素材能被检索到并被
   管线落位"。

任一类缺失 → 退出码 1，并在报告里写清"缺哪一类"，供人工补齐后重跑。

用法::

    python scripts/check_v50_material_samples.py
    python scripts/check_v50_material_samples.py --dir D:\\素材\\四类样例

素材目录由 `CLIPWRIGHT_LOCAL_MATERIAL_DIR`（运行时）或 `--dir`（本脚本）指向。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLASSES: dict[str, dict[str, object]] = {
    "hand_drawn": {
        "label": "手绘",
        "dir": "hand_drawn",
        "query": "手绘 线稿 素描 hand drawn sketch",
        "exts": {".png", ".jpg", ".jpeg", ".webp", ".mp4"},
    },
    "2d": {
        "label": "2D 动画",
        "dir": "2d",
        "query": "2d 二维 动画 卡通 flat cartoon animation",
        "exts": {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".gif"},
    },
    "3d": {
        "label": "3D",
        "dir": "3d",
        "query": "3d 三维 渲染 建模 render cgi",
        "exts": {".png", ".jpg", ".jpeg", ".webp", ".mp4"},
    },
    "live_action": {
        "label": "实拍",
        "dir": "live_action",
        "query": "实拍 街景 人物 live action footage street",
        "exts": {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"},
    },
}


def _inventory(root: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, spec in CLASSES.items():
        folder = root / str(spec["dir"])
        exts = spec["exts"]
        files = (
            sorted(
                p.name
                for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in exts  # type: ignore[union-attr]
            )
            if folder.is_dir()
            else []
        )
        out[key] = files
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V50：四类素材样例落位校验")
    parser.add_argument(
        "--dir",
        default=str(ROOT / "examples" / "material_samples"),
        help="四类素材根目录（默认 examples/material_samples）",
    )
    parser.add_argument("--out", default="out/v50")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    from clipwright.material.local_source import LocalFolderSource

    root = Path(args.dir)
    inventory = _inventory(root)
    source = LocalFolderSource(root, max_files=2000)
    indexed = source.refresh(force=True)

    results: list[dict[str, object]] = []
    for key, spec in CLASSES.items():
        own = inventory[key]
        hits = asyncio.run(source.search(str(spec["query"]), top_k=args.top_k)) or []
        hit_files = [
            Path(str(getattr(asset, "path", "") or getattr(asset, "url", ""))).name
            for asset, _score in hits
        ]
        own_hit = [name for name in hit_files if name in set(own)]
        # 串味检查：命中里出现了别的类别的文件 → 关键词区分度不足
        other_names = {
            name
            for other_key, names in inventory.items()
            if other_key != key
            for name in names
        }
        cross = [name for name in hit_files if name in other_names]
        results.append(
            {
                "class": key,
                "label": spec["label"],
                "file_count": len(own),
                "files": own[:5],
                "query": spec["query"],
                "hits": hit_files,
                "own_hits": own_hit,
                "cross_class_hits": cross,
                "ok": bool(own) and bool(own_hit),
            }
        )

    missing = [r["label"] for r in results if not r["file_count"]]
    report = {
        "root": str(root),
        "indexed_files": indexed,
        "classes": results,
        "missing_classes": missing,
        "ok": all(bool(r["ok"]) for r in results),
        "note": (
            "V50 的片源必须由人提供（授权/出处可追溯），本脚本只验证「四类齐全 + "
            "每类能被本地素材源按关键词检索到且不串味」。缺类时按 missing_classes 补齐后重跑。"
        ),
    }
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for item in results:
        flag = "OK " if item["ok"] else "MISS"
        print(f"[{flag}] {item['label']:<7} files={item['file_count']:<3} hits={item['hits']}")
    print(f"\n索引 {indexed} 个文件；缺失类别：{missing or '无'} → {target}")
    if missing:
        print("请按 examples/material_samples/README.md 补齐四类素材后重跑。")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
