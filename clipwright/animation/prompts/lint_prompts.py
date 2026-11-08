"""提示词 lint（PRD §16.6）：把"提示词写错了"从线上事故变成 CI 红灯。

检查项（全部可机器判定，不靠人读）：

1. **段落齐全**：七段旧标题（Intent / Semantic constraints / Composition / Motion /
   Anti-patterns / Example / Style reference）缺一即 error（`text_specs` 的加载器与
   `validate_text_registry` 就按这七段切分）；`Constants` / `Output` / `Self-check` /
   `Fallback` 四段是 v2 新增，缺失为 error（已迁移的类型必须补齐）。
   `Anti-patterns` 与 `Self-check` 各 ≥1 条。
2. **占位符**：发布版提示词里禁止残留 `{{...}}`（模板 `_base.md` 例外，它本来就是模板）。
3. **数值合法**：`min_duration < max_duration <= 60`；元素上限 ≤ 3（text）；单行 ≤ 14 字（text）。
4. **easing**：正文里的 easing 名字必须命中目标路径白名单——
   drawtext（`prompts/text/`）只认 `keyframes_expr.EASING_EXPR` 的 21 个名字；
   MG（`prompts/types/`）只认 `mg.validator.VALID_EASINGS` 的 8 个名字。
   `## Example` 里的 JSON 代码块按**可渲染 def** 处理：它必须过 `validate_mg_json`
   （MG 名字），因此那一段不参与正文的 easing 扫描——两类名字的差异在 `_base.md` 里写明。
5. **不支持能力词**：drawtext 提示词出现 粒子 / 模糊 / 3D / 遮罩 / 独立卡片层 / 发光 等
   drawtext 不具备的效果时，所在行必须标注「MG HTML」或「本期不落」，否则 error。
6. **风格模块引用**：提示词若写了 `S<数字>`，必须能在 `style_catalog.json` 里找到。

用法：

    python -m clipwright.animation.prompts.lint_prompts            # 全部提示词
    python -m clipwright.animation.prompts.lint_prompts --strict    # 有 error 即退出码 1
    python -m clipwright.animation.prompts.lint_prompts --only text/emphasis_keyword
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from clipwright.animation.mg.validator import VALID_EASINGS
from clipwright.utils.keyframes_expr import EASING_EXPR

PROMPTS_DIR = Path(__file__).resolve().parent
CATALOG_PATH = PROMPTS_DIR / "style_catalog.json"

LEGACY_SECTIONS = (
    "Intent", "Semantic constraints", "Composition", "Motion",
    "Anti-patterns", "Example", "Style reference",
)
V2_SECTIONS = ("Constants", "Output", "Self-check", "Fallback")
REQUIRED_SECTIONS = LEGACY_SECTIONS + V2_SECTIONS

#: 已迁移到 v2（九段式）的类型提示词：这些必须补齐新增四段，其余类型给 warning
MIGRATED_TYPES = {"bar_chart", "flow_chart", "comparison", "line_chart"}

#: 风格模块正文（`prompts/styles/S*.md`）走自己的段落要求，不是类型提示词
STYLE_SECTIONS = ("视觉语言", "关键参数倾向", "适用与回避", "禁止项", "使用纪律")

#: 模板文件（含 `{{占位符}}`，本来就是给人填的）
TEMPLATE_NAMES = {"_base.md"}

#: drawtext（FFmpeg drawtext 关键帧）做不到的效果词；出现时必须标注替代路径
UNSUPPORTED_DRAWTEXT = ("粒子", "模糊", "blur", "3D", "遮罩", "mask", "独立卡片", "发光", "扫光")
ANNOTATIONS = ("MG HTML", "本期不落")

MIN_DURATION_MAX = 60.0
TEXT_MAX_ELEMENTS = 3
TEXT_MAX_LINE_CHARS = 14

_EASING_TOKEN = re.compile(r"\b(ease[a-z-]*|linear|back-out|bounce|elastic-out)\b")
#: 反例行的标记词：命中则整行跳过白名单扫描（这一行在讲"不要用"）
NEGATION_MARKERS = ("不要写", "禁止", "不得", "不许", "不认", "MG 名字", "不能用")
_NUM_MIN_MAX = re.compile(r"min_duration\s*[=:]\s*([0-9.]+)\s*[,，]?\s*max_duration\s*[=:]\s*([0-9.]+)")
_STYLE_REF = re.compile(r"\bS(\d{1,2})\b")


@dataclass
class Finding:
    level: str          # error | warning
    path: str
    rule: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


def _sections(body: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current = ""
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            out.setdefault(current, [])
            continue
        if current:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _code_blocks(body: str) -> list[str]:
    return re.findall(r"```(?:json)?\s*\n(.*?)```", body, flags=re.S)


def _strip_code_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def _kind_of(path: Path) -> str:
    """`text` = drawtext 路径；`type` = MG 路径。"""
    return "text" if path.parent.name == "text" else "type"


def check_style_module(path: Path) -> Report:
    """风格模块只检查自己的骨架与目录一致性，不套类型提示词的规则。"""
    rep = Report()
    rel = path.relative_to(PROMPTS_DIR).as_posix() if PROMPTS_DIR in path.parents else path.name
    body = path.read_text(encoding="utf-8")
    secs = _sections(body)
    for name in STYLE_SECTIONS:
        if name not in secs:
            rep.findings.append(Finding("error", rel, "sections", f"风格模块缺少段落 `## {name}`"))
    if not secs.get("禁止项", "").strip():
        rep.findings.append(Finding("error", rel, "sections", "`## 禁止项` 不得为空"))
    if not body.lstrip().startswith("#"):
        rep.findings.append(Finding("error", rel, "sections", "缺少一级标题 `# S编号 名称`"))
    return rep


def check_file(path: Path, *, catalog_ids: set[str] | None = None) -> Report:
    if path.parent.name == "styles":
        return check_style_module(path)
    if path.name in TEMPLATE_NAMES:
        # 模板本身就是骨架 + 占位符，段落与占位符规则都不适用于它
        return Report()
    rep = Report()
    rel = path.relative_to(PROMPTS_DIR).as_posix() if PROMPTS_DIR in path.parents else path.name
    body = path.read_text(encoding="utf-8")
    kind = _kind_of(path)
    is_template = path.name in TEMPLATE_NAMES
    # v2 新增四段在已迁移提示词里是 error，未迁移的老提示词先 warning（迁移后自动升级）
    strict_v2 = kind == "text" or path.stem in MIGRATED_TYPES

    secs = _sections(body)
    for name in REQUIRED_SECTIONS:
        if name in secs:
            continue
        level = "error" if (name in LEGACY_SECTIONS or strict_v2) else "warning"
        rep.findings.append(Finding(level, rel, "sections", f"缺少段落 `## {name}`"))
    for name in ("Anti-patterns", "Self-check"):
        if name in secs and not any(ln.strip().startswith(("-", "*", "["))
                                    for ln in secs[name].splitlines()):
            rep.findings.append(Finding(
                "error", rel, "sections", f"`## {name}` 至少要有一条（- / * / [ ] 开头）"))
    if not is_template:
        for m in re.finditer(r"\{\{[^}]*\}\}", body):
            rep.findings.append(Finding(
                "error", rel, "placeholder", f"残留未展开占位符 {m.group(0)}"))

    # 数值：min/max duration 与元素/字数上限
    for m in _NUM_MIN_MAX.finditer(body):
        lo, hi = float(m.group(1)), float(m.group(2))
        if not (0 < lo < hi <= MIN_DURATION_MAX):
            rep.findings.append(Finding(
                "error", rel, "numbers",
                f"min_duration/max_duration 非法：{lo}–{hi}（要求 0 < min < max <= {MIN_DURATION_MAX:g}）"))
    if kind == "text":
        for m in re.finditer(r"(?:元素数|元素上限|elements?)\D{0,6}(\d+)", body):
            n = int(m.group(1))
            if n > TEXT_MAX_ELEMENTS:
                rep.findings.append(Finding(
                    "error", rel, "numbers", f"元素上限 {n} 超过 {TEXT_MAX_ELEMENTS}"))
        for m in re.finditer(r"(?:≤|<=)\s*(\d+)\s*字", body):
            n = int(m.group(1))
            if n > TEXT_MAX_LINE_CHARS:
                rep.findings.append(Finding(
                    "error", rel, "numbers", f"单行字数上限 {n} 超过 {TEXT_MAX_LINE_CHARS}"))

    # easing：正文（去掉 Example 代码块）按路径白名单
    prose = "\n".join(v for k, v in secs.items() if k != "Example")
    prose = _strip_code_blocks(prose)
    allowed = set(EASING_EXPR) if kind == "text" else set(VALID_EASINGS)
    # 反例行（"不要写 back-out"/"禁止 bounce"）本身就是在讲哪些名字不能用，
    # 扫描时整行跳过——否则提示词越写清禁令，lint 越报错。
    scan_lines = [
        ln for ln in prose.splitlines()
        if not any(neg in ln for neg in NEGATION_MARKERS)
    ]
    for tok in sorted(set(_EASING_TOKEN.findall("\n".join(scan_lines)))):
        if tok in ("ease",) and kind == "type":
            continue      # MG 白名单里就有 `ease`
        if tok not in allowed:
            rep.findings.append(Finding(
                "error", rel, "easing",
                f"easing `{tok}` 不在 {'drawtext' if kind == 'text' else 'MG'} 白名单里"
                f"（可用：{'/'.join(sorted(allowed))}）"))

    # drawtext 不支持的能力词：必须标注替代路径
    if kind == "text":
        for line in prose.splitlines():
            low = line.lower()
            hit = [w for w in UNSUPPORTED_DRAWTEXT if w.lower() in low]
            if hit and not any(a.lower() in low for a in ANNOTATIONS):
                rep.findings.append(Finding(
                    "error", rel, "capability",
                    f"提到 {hit[0]} 但没标注「MG HTML」或「本期不落」：{line.strip()[:60]}"))
    else:
        for line in prose.splitlines():
            low = line.lower()
            hit = [w for w in ("模糊", "blur", "3D", "遮罩", "mask") if w.lower() in low]
            if hit and not any(a.lower() in low for a in ANNOTATIONS):
                rep.findings.append(Finding(
                    "warning", rel, "capability",
                    f"MG 提示词提到 {hit[0]} 但未标注路径支持情况：{line.strip()[:60]}"))

    # Example 的 JSON 必须真能渲染
    for block in _code_blocks(secs.get("Example", "")):
        text = block.strip()
        if not text.startswith("{"):
            continue
        try:
            mg_def = json.loads(text)
        except json.JSONDecodeError as exc:
            rep.findings.append(Finding(
                "error", rel, "example", f"Example JSON 解析失败: {exc}"))
            continue
        from clipwright.animation.mg.validator import validate_mg_json

        ok, errs = validate_mg_json(mg_def)
        if not ok:
            rep.findings.append(Finding(
                "error", rel, "example", f"Example 不是可渲染 def：{errs[:3]}"))

    # 风格模块引用必须真实存在
    if catalog_ids is not None:
        for sid in sorted(set(f"S{int(m)}" for m in _STYLE_REF.findall(body))):
            if sid not in catalog_ids:
                rep.findings.append(Finding(
                    "error", rel, "style", f"引用了不存在的风格模块 {sid}"))

    return rep


def _targets(only: str = "") -> list[Path]:
    if only:
        p = PROMPTS_DIR / only
        if p.suffix != ".md":
            p = p.with_suffix(".md")
        return [p] if p.is_file() else []
    out: list[Path] = []
    for sub in ("text", "types", "styles"):
        out.extend(sorted((PROMPTS_DIR / sub).glob("*.md")))
    return out


def catalog_ids() -> set[str]:
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(m.get("id")) for m in (data.get("modules") or []) if m.get("id")}


def lint(only: str = "") -> Report:
    rep = Report()
    ids = catalog_ids()
    for path in _targets(only):
        sub = check_file(path, catalog_ids=ids)
        rep.findings.extend(sub.findings)
    return rep


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提示词 lint（PRD §16.6）")
    ap.add_argument("--strict", action="store_true", help="有 error 时退出码 1")
    ap.add_argument("--only", default="", help="只检查一个文件（相对 prompts/，可省 .md）")
    args = ap.parse_args(list(argv) if argv is not None else None)

    rep = lint(args.only)
    for f in rep.findings:
        print(f"[{f.level}] {f.path} · {f.rule} · {f.message}")
    errs, warns = len(rep.errors()), len(rep.warnings())
    print(f"\nlint: {errs} error / {warns} warning")
    if args.strict and errs:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
