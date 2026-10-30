"""风格模块检索（PRD §16.6）：`search` / `show` / `match`。

三条纪律：
1. **默认只注入 ≤3 个候选摘要**——12 份模块正文一次拼进 prompt 只会把模型预算烧在风格描述上；
2. 正文按需读（`show <id>`），选定模块后才和类型提示词一起用；
3. 检索是纯本地、确定性排序（分数 → 编号），同输入两次结果必须一致。

用法：

    python scripts/styles.py search 图表 趋势
    python scripts/styles.py show S4
    python scripts/styles.py match --type bar_chart --hint "蓝黄对比 数据生长"
    python scripts/styles.py search 极简 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "clipwright" / "animation" / "prompts"
CATALOG_PATH = PROMPTS_DIR / "style_catalog.json"
STYLES_DIR = PROMPTS_DIR / "styles"

#: 默认注入的候选摘要条数上限（超过只给摘要，不给正文）
DEFAULT_LIMIT = 3


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    p = path or CATALOG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def modules(catalog: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return list((catalog or load_catalog()).get("modules") or [])


def _haystack(mod: dict[str, Any]) -> str:
    parts: list[str] = [str(mod.get("id") or ""), str(mod.get("name") or ""),
                        str(mod.get("series") or "")]
    for key in ("keywords_zh", "keywords_en", "motion_tags",
                "composition_tags", "suitable_types"):
        parts.extend(str(x) for x in (mod.get(key) or []))
    return " ".join(parts).lower()


def score(mod: dict[str, Any], terms: Iterable[str]) -> int:
    """命中计分：中文关键词 3 分、标签/类型 2 分、其它字段 1 分。"""
    terms = [t.strip().lower() for t in terms if str(t).strip()]
    if not terms:
        return 0
    total = 0
    kw = " ".join(str(x) for x in (mod.get("keywords_zh") or [])).lower()
    tags = " ".join(
        str(x) for x in list(mod.get("motion_tags") or [])
        + list(mod.get("composition_tags") or [])
        + list(mod.get("suitable_types") or [])
    ).lower()
    hay = _haystack(mod)
    for t in terms:
        if t in kw:
            total += 3
        elif t in tags:
            total += 2
        elif t in hay:
            total += 1
    return total


def search(terms: Iterable[str], *, catalog: dict[str, Any] | None = None,
           limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """按关键词检索并排序（分数降序 → 编号升序，保证确定性）。"""
    scored = [(score(m, terms), str(m.get("id") or ""), m) for m in modules(catalog)]
    hits = [x for x in scored if x[0] > 0]
    hits.sort(key=lambda x: (-x[0], x[1]))
    return [_summary(m, s) for s, _, m in hits[:max(1, limit)]]


def _summary(mod: dict[str, Any], sc: int = 0) -> dict[str, Any]:
    return {
        "id": mod.get("id"),
        "name": mod.get("name"),
        "series": mod.get("series"),
        "palette_hex": mod.get("palette_hex") or [],
        "motion_tags": mod.get("motion_tags") or [],
        "renderer_paths": mod.get("renderer_paths") or [],
        "score": sc,
    }


def match(type_id: str, hint: str = "", *, catalog: dict[str, Any] | None = None,
          limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """按类型适配过滤 + 关键词排序：`suitable_types` 命中加分，`avoid_types` 命中剔除。"""
    terms = [t for t in (hint or "").replace("，", " ").replace(",", " ").split() if t]
    out: list[tuple[int, str, dict[str, Any]]] = []
    for mod in modules(catalog):
        if type_id and type_id in (mod.get("avoid_types") or []):
            continue
        sc = score(mod, terms)
        if type_id and type_id in (mod.get("suitable_types") or []):
            sc += 4
        out.append((sc, str(mod.get("id") or ""), mod))
    out.sort(key=lambda x: (-x[0], x[1]))
    # 有命中的时候不给 0 分凑数：候选列表只留真正相关的（≤limit）
    positive = [x for x in out if x[0] > 0]
    picked = positive or out
    return [_summary(m, s) for s, _, m in picked[:max(1, limit)]]


def show(module_id: str, *, styles_dir: Path | None = None) -> str:
    """读单个模块正文（选定后再注入，不预加载 12 份）。"""
    mid = str(module_id or "").strip().upper()
    path = (styles_dir or STYLES_DIR) / f"{mid}.md"
    if not mid or not path.is_file():
        raise SystemExit(f"未知风格模块: {module_id!r}（可选 S1–S12）")
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="风格模块检索（PRD §16.6）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="按关键词检索候选摘要")
    p_search.add_argument("terms", nargs="+")
    p_search.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p_search.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="读单个模块正文")
    p_show.add_argument("module_id")

    p_match = sub.add_parser("match", help="按类型 + 提示文本匹配")
    p_match.add_argument("--type", dest="type_id", default="")
    p_match.add_argument("--hint", default="")
    p_match.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p_match.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "search":
        items = search(args.terms, limit=args.limit)
    elif args.cmd == "match":
        items = match(args.type_id, args.hint, limit=args.limit)
    else:
        print(show(args.module_id))
        return 0

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("没有匹配的风格模块；换关键词，或用 `show S3` 直接看某一个。")
        return 0
    for it in items:
        print(f"{it['id']} {it['name']}（{it['series']}）"
              f"  配色 {'/'.join(it['palette_hex'])}"
              f"  运动 {'、'.join(it['motion_tags'])}"
              f"  路径 {'/'.join(it['renderer_paths'])}  分数 {it['score']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
