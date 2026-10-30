"""M6（PRD §16.6）：提示词 lint 与风格模块索引的可执行验收。

三类断言：
1. 仓库现状必须通过 strict lint（六类文字 + 四类 MG 已迁移，其余老提示词只给 warning）；
2. lint 真的能抓到问题——用临时文件造违规（缺段、非法 easing、未标注的 drawtext 能力、
   残留占位符、越界数值、不可渲染 Example），逐条断言 error；
3. 风格模块目录与正文一一对应，检索脚本 deterministic、默认只给 ≤3 条摘要。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from clipwright.animation.prompts import lint_prompts as L

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "clipwright" / "animation" / "prompts"


# ── 现状：strict 通过 ─────────────────────────────────────


def test_repo_prompts_pass_strict_lint() -> None:
    rep = L.lint()
    errors = rep.errors()
    assert errors == [], "\n".join(f"{f.path} · {f.rule} · {f.message}" for f in errors)
    # 老类型的 v2 段落提示以 warning 记，可见但不拦 CI
    assert isinstance(rep.warnings(), list)


def test_cli_exit_codes() -> None:
    assert L.main([]) == 0
    assert L.main(["--strict"]) == 0


def test_migrated_prompts_have_all_sections() -> None:
    ids = L.MIGRATED_TYPES
    for tid in ids:
        p = PROMPTS / "types" / f"{tid}.md"
        rep = L.check_file(p, catalog_ids=L.catalog_ids())
        assert rep.errors() == [], (tid, [f.message for f in rep.errors()])
    for p in sorted((PROMPTS / "text").glob("*.md")):
        assert p.name in L.TEMPLATE_NAMES or L.check_file(p).errors() == [], p.name


def test_text_registry_still_valid() -> None:
    """改提示词不能把 `validate_text_registry` 弄红（七段标题是加载器的切分依据）。"""
    from clipwright.animation.text_specs import (
        missing_text_prompts, validate_text_registry,
    )

    assert missing_text_prompts() == []
    assert validate_text_registry() == []


# ── 负例：lint 真能抓到 ───────────────────────────────────


def _write(tmp_path: Path, kind: str, body: str, name: str = "demo") -> Path:
    d = tmp_path / kind
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(body, encoding="utf-8")
    return p


GOOD = """# 演示

## Intent
讲清楚。

## Semantic constraints
- 一行。

## Composition
居中。

## Motion
0.3s 入场。

## Anti-patterns
- 别乱来。

## Example
示例。

## Style reference
S3。

## Constants
| 常量 | 值 |
|---|---|
| 片段时长 | min_duration=1.0，max_duration=6.0 |

## Output
只输出 JSON。

## Self-check
- [ ] 检查。

## Fallback
失败就降级。
"""


def test_missing_section_is_error(tmp_path, monkeypatch) -> None:
    p = _write(tmp_path, "text", GOOD.replace("## Self-check", "## 自检"))
    rep = L.check_file(p)
    assert any(f.rule == "sections" and "Self-check" in f.message for f in rep.errors())


def test_empty_antipatterns_is_error(tmp_path) -> None:
    p = _write(tmp_path, "text", GOOD.replace("## Anti-patterns\n- 别乱来。", "## Anti-patterns\n"))
    rep = L.check_file(p)
    assert any("Anti-patterns" in f.message for f in rep.errors())


def test_placeholder_residue_is_error(tmp_path) -> None:
    p = _write(tmp_path, "text", GOOD + "\n补一句 {{未展开}}。\n")
    rep = L.check_file(p)
    assert any(f.rule == "placeholder" for f in rep.errors())


def test_bad_duration_is_error(tmp_path) -> None:
    p = _write(tmp_path, "text",
               GOOD.replace("min_duration=1.0，max_duration=6.0", "min_duration=8，max_duration=4"))
    rep = L.check_file(p)
    assert any(f.rule == "numbers" and "min_duration" in f.message for f in rep.errors())


def test_text_easing_outside_drawtext_whitelist_is_error(tmp_path) -> None:
    p = _write(tmp_path, "text", GOOD + "\n用 bounce 入场。\n")
    rep = L.check_file(p)
    assert any(f.rule == "easing" and "bounce" in f.message for f in rep.errors())


def test_prohibition_line_is_exempt_from_easing_scan(tmp_path) -> None:
    """提示词写「不要写 back-out」本身不该报错——否则越写清禁令越红。"""
    p = _write(tmp_path, "text", GOOD + "\n不要写 back-out／bounce，那是 MG 名字。\n")
    rep = L.check_file(p)
    assert [f for f in rep.errors() if f.rule == "easing"] == []


def test_drawtext_capability_needs_annotation(tmp_path) -> None:
    p = _write(tmp_path, "text", GOOD + "\n入场用粒子散开。\n")
    rep = L.check_file(p)
    assert any(f.rule == "capability" for f in rep.errors())

    ok = _write(tmp_path, "text", GOOD + "\n粒子散开需要 MG HTML 路径。\n", name="ok")
    assert [f for f in L.check_file(ok).errors() if f.rule == "capability"] == []


def test_unrenderable_example_is_error(tmp_path) -> None:
    bad = GOOD.replace("## Example\n示例。",
                       '## Example\n```json\n{"animation_id": "x", "elements": []}\n```')
    p = _write(tmp_path, "text", bad)
    rep = L.check_file(p)
    assert any(f.rule == "example" for f in rep.errors())


def test_unknown_style_module_is_error(tmp_path) -> None:
    p = _write(tmp_path, "text", GOOD.replace("## Style reference\nS3。", "## Style reference\nS99。"))
    rep = L.check_file(p, catalog_ids=L.catalog_ids())
    assert any(f.rule == "style" and "S99" in f.message for f in rep.errors())


# ── 风格模块目录 ─────────────────────────────────────────


def _catalog() -> dict:
    return json.loads((PROMPTS / "style_catalog.json").read_text(encoding="utf-8"))


def test_catalog_has_s1_to_s12_with_metadata() -> None:
    mods = _catalog()["modules"]
    assert [m["id"] for m in mods] == [f"S{i}" for i in range(1, 13)]
    required = {
        "id", "name", "series", "keywords_zh", "keywords_en", "palette_hex",
        "motion_tags", "composition_tags", "suitable_types", "avoid_types",
        "forbidden", "renderer_paths", "reference_source", "prompt_version", "status",
    }
    for m in mods:
        assert required <= set(m), (m["id"], required - set(m))
        assert m["palette_hex"], m["id"]
        assert m["renderer_paths"], m["id"]


def test_every_catalog_module_has_body() -> None:
    for m in _catalog()["modules"]:
        p = PROMPTS / "styles" / f"{m['id']}.md"
        assert p.is_file(), m["id"]
        assert L.check_file(p).errors() == [], m["id"]


def test_styles_cli_search_show_match() -> None:
    def run(*args: str) -> str:
        r = subprocess.run(
            [sys.executable, "-X", "utf8", "scripts/styles.py", *args],
            capture_output=True, cwd=str(ROOT),
        )
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-400:]
        return r.stdout.decode("utf-8")

    assert "S4" in run("search", "图表")
    assert "# S3 极简几何" in run("show", "S3")
    matched = json.loads(run("match", "--type", "bar_chart", "--hint", "数据", "--json"))
    assert matched and matched[0]["id"] == "S4"


def test_styles_cli_defaults_and_determinism() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import importlib

        styles = importlib.import_module("styles")
        a = styles.search(["图表"])
        b = styles.search(["图表"])
        assert a == b                      # 确定性
        assert len(a) <= styles.DEFAULT_LIMIT
        assert styles.search(["绝对不存在的词"]) == []
        # avoid_types 过滤：bar_chart 在 S2/S5 的回避列表里
        ids = [x["id"] for x in styles.match("bar_chart", "数据")]
        assert "S2" not in ids and "S5" not in ids
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("sid", [f"S{i}" for i in range(1, 13)])
def test_style_body_has_discipline_sections(sid: str) -> None:
    body = (PROMPTS / "styles" / f"{sid}.md").read_text(encoding="utf-8")
    for name in L.STYLE_SECTIONS:
        assert f"## {name}" in body, (sid, name)
