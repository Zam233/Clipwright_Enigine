"""阶段接线审计：找出「字段声明了，但没人给它赋值」的死入口。

背景（2026-10-29）：persona 断链就是这样被发现的——`AnimationInput.persona_prompt`
与 `visual_config` 在 `stage_inputs.assemble` 里从黑板取，但 `stage_merge.MERGE_KEYS`
从不写这两个键，于是真实 pipeline 里恒为空。同类缺陷不会自己暴露，只能扫出来。

本脚本做四件事（AST，不执行任何业务代码）：

1. `*Input` 的每个字段 → `stage_inputs.assemble` 里的取值表达式（含一层局部变量回溯）；
2. 该字段在别处是否有显式构造传入（`XInput(...)` 的关键字）；
3. 字段名是否在黑板白名单（`stage_merge.MERGE_KEYS`）；
4. `extra_params` 的读取键 vs 写入点，以及 prompt 装配里的截断 / persona 路径硬编码。

用法：

    python scripts/audit_stage_plumbing.py                # 人类可读
    python scripts/audit_stage_plumbing.py --json out.json
    python scripts/audit_stage_plumbing.py --strict       # 有死入口则退出码 1（进 CI）

已知豁免写在 `ALLOW_UNWIRED`，每条必须带理由；新增死入口会让 `--strict` 失败。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "clipwright"
FILES = [p for p in ROOT.rglob("*.py") if "__pycache__" not in str(p)]
INPUT_CLASSES = {
    "StructureInput", "MaterialInput", "EditInput", "AnimationInput",
    "AudioInput", "QualityInput",
}

#: 已确认「故意不接线」的字段（每条必须有理由，否则就是缺陷）
ALLOW_UNWIRED: dict[str, str] = {
    "StructureInput.context": "由 assemble 参数 ctx 直接注入，不走 data",
    "MaterialInput.context": "同上",
    "EditInput.context": "同上",
    "AnimationInput.context": "同上",
    "AudioInput.context": "同上",
    "QualityInput.context": "同上",
}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _parse(p: Path) -> ast.Module | None:
    try:
        return ast.parse(_read(p))
    except SyntaxError:
        return None


def input_fields() -> dict[str, list[str]]:
    tree = _parse(ROOT / "schema" / "agent.py")
    out: dict[str, list[str]] = {}
    if tree is None:
        return out
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in INPUT_CLASSES:
            out[node.name] = [
                stmt.target.id for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            ]
    return out


def _assemble_function() -> ast.FunctionDef | None:
    tree = _parse(ROOT / "services" / "stage_inputs.py")
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "assemble":
            return node
    return None


def assemble_sources() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """返回 ({Input: {field: 表达式}}, {局部变量: 表达式})，局部变量回溯一层。"""
    fn = _assemble_function()
    if fn is None:
        return {}, {}
    src = _read(ROOT / "services" / "stage_inputs.py")

    locals_: dict[str, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            seg = ast.get_source_segment(src, node.value) or ""
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    locals_[tgt.id] = seg.strip()

    def resolve(expr: str, depth: int = 2) -> str:
        expr = expr.strip()
        if depth <= 0:
            return expr
        if re.fullmatch(r"[A-Za-z_]\w*", expr) and expr in locals_:
            return resolve(locals_[expr], depth - 1)
        return expr

    out: dict[str, dict[str, str]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in INPUT_CLASSES:
            fields: dict[str, str] = {}
            for kw in node.keywords:
                if not kw.arg:
                    continue
                seg = ast.get_source_segment(src, kw.value) or ""
                fields[kw.arg] = resolve(seg.replace("\n", " "))[:160]
            out.setdefault(node.func.id, {}).update(fields)
    return out, locals_


def explicit_sites() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in FILES:
        if p.name == "stage_inputs.py":
            continue
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in INPUT_CLASSES:
                for kw in node.keywords:
                    if kw.arg:
                        out.setdefault(f"{node.func.id}.{kw.arg}", []).append(
                            f"{p.name}:{node.lineno}"
                        )
    return out


def board_whitelist() -> set[str]:
    text = _read(ROOT / "services" / "stage_merge.py")
    keys: set[str] = set()
    for m in re.finditer(r'"[a-z_]+":\s*\(([^)]*)\)', text):
        keys.update(re.findall(r'"([\w]+)"', m.group(1)))
    keys.add("timeline")  # merge() 里单独处理
    keys.add("final_timeline")
    return keys


def extra_params_channels() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    reads: dict[str, list[str]] = {}
    writes: dict[str, list[str]] = {}
    for p in FILES:
        text = _read(p)
        for m in re.finditer(r'extra_params(?:\.get\(|\[)\s*["\']([\w]+)["\']', text):
            reads.setdefault(m.group(1), []).append(f"{p.name}:{text[:m.start()].count(chr(10)) + 1}")
        for m in re.finditer(r'extra_params\s*\[\s*["\']([\w]+)["\']\s*\]\s*=', text):
            writes.setdefault(m.group(1), []).append(f"{p.name}:{text[:m.start()].count(chr(10)) + 1}")
        for m in re.finditer(r'extra_params\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', text):
            for k in re.findall(r'["\']([\w]+)["\']\s*:', m.group(1)):
                writes.setdefault(k, []).append(f"{p.name}:{text[:m.start()].count(chr(10)) + 1}")
    return reads, writes


def truncations() -> list[dict[str, Any]]:
    out = []
    for p in FILES:
        text = _read(p)
        for m in re.finditer(
            r"(persona[a-z_]*|guide|style_description|prompt|description|text_content)\s*\[:\s*(\d+)\]",
            text,
        ):
            out.append({
                "file": p.name,
                "line": text[: m.start()].count("\n") + 1,
                "expr": m.group(0),
                "limit": int(m.group(2)),
            })
    return sorted(out, key=lambda r: -r["limit"])


def hardcoded_persona_colors() -> list[dict[str, Any]]:
    out = []
    pattern = re.compile(
        r'config\[\s*["\'](primary_color|secondary_color|accent_color|text_color)["\']\s*\]'
        r'\s*=\s*["\'](#[0-9A-Fa-f]{3,8})["\']'
    )
    for p in FILES:
        text = _read(p)
        if "persona" not in text.lower():
            continue
        for m in pattern.finditer(text):
            out.append({
                "file": p.name,
                "line": text[: m.start()].count("\n") + 1,
                "key": m.group(1),
                "value": m.group(2),
            })
    return out


def run() -> dict[str, Any]:
    fields = input_fields()
    sources, _ = assemble_sources()
    explicit = explicit_sites()
    board = board_whitelist()
    reads, writes = extra_params_channels()

    rows = []
    for cls, names in fields.items():
        for name in names:
            expr = sources.get(cls, {}).get(name, "")
            key = f"{cls}.{name}"
            reads_board = f'data.get("{name}"' in expr or f"data.get('{name}'" in expr
            has_extra = "extra.get" in expr
            wired_other = bool(
                explicit.get(key)
                or "getattr(" in expr
                or "_as_timeline" in expr
                or (expr.startswith(("brief", "plan", "ctx", "skeleton", "subset")))
            )
            if not expr:
                verdict = "NOT_ASSEMBLED"
            elif reads_board and name not in board and not has_extra and not explicit.get(key):
                # 从黑板取一个"没有任何阶段会写"的键 —— 就是 persona 断链那一类
                verdict = "BOARD_KEY_WITHOUT_WRITER"
            elif wired_other or name in board or has_extra:
                verdict = "OK"
            else:
                verdict = "DEAD_ENTRY"
            rows.append({
                "field": key,
                "assemble_expr": expr,
                "explicit_sites": explicit.get(key, []),
                "in_board": name in board,
                "verdict": verdict,
                "allowlisted": key in ALLOW_UNWIRED,
            })

    dead = [r for r in rows
            if r["verdict"] in ("DEAD_ENTRY", "BOARD_KEY_WITHOUT_WRITER") and not r["allowlisted"]]

    persona_keys = ("persona_prompt", "persona_config", "visual_config", "vision_prompt")
    persona_channels = {
        key: {
            "assemble_reads_board": any(key in (sources.get(c, {}) or {}) for c in INPUT_CLASSES),
            "in_board_whitelist": key in board,
            "explicit_sites": explicit.get(f"AnimationInput.{key}", [])
                             + explicit.get(f"StructureInput.{key}", []),
        }
        for key in persona_keys
    }

    return {
        "generated_from": str(ROOT),
        "fields_total": len(rows),
        "dead_entries": dead,
        "persona_channels": persona_channels,
        "extra_params_read_without_writer": {
            k: v for k, v in reads.items() if k not in writes
        },
        "prompt_truncations": truncations(),
        "hardcoded_persona_colors": hardcoded_persona_colors(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="阶段接线审计（死入口/断链）")
    ap.add_argument("--json", dest="json_out", help="输出 JSON 路径")
    ap.add_argument("--strict", action="store_true", help="有死入口则退出码 1")
    args = ap.parse_args()

    report = run()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(text, encoding="utf-8")

    persona = report["persona_channels"]
    unwired_persona = [k for k, v in persona.items()
                       if not v["in_board_whitelist"] and not v["explicit_sites"]]
    if args.strict and (report["dead_entries"] or unwired_persona):
        print(f"\n[strict] 死入口 {len(report['dead_entries'])} 个，未接线 persona 通道: {unwired_persona}",
              flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
