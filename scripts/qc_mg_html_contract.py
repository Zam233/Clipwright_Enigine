# -*- coding: utf-8 -*-
"""HyperFrames 契约合规检查器 — 校验项目内所有 MG 动画的 mg_html。

HyperFrames contract compliance checker — validates every MG animation clip's
mg_html in a ClipWright project.json against the HyperFrames 0.7.88 contract:

  1. 根 div 携带 data-composition-id  (root div has data-composition-id)
  2. 时间元素带 class="clip"           (timed elements carry class="clip")
  3. 注册 window.__timelines            (registers window.__timelines)
  4. <style> 内 @keyframes 花括号配对     (@keyframes braces balanced inside <style>)
  5. 无未闭合的 div/script 标签          (no unclosed div/script tags)

只读脚本: 不改写任何文件。退出码 0 表示 100% 通过, 否则非 0。
Read-only: never writes files. Exit 0 only when 100% of clips pass.

用法 / Usage:
    python scripts/qc_mg_html_contract.py [project.json]
"""
from __future__ import annotations

import html.parser
import re
import sys

try:  # 强制 UTF-8 输出, 避免 Windows 控制台编码问题
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

DEFAULT_PROJECT = r"D:\Clipweight\projects\proj_56e7f2cd802a\project.json"


class TagBalanceParser(html.parser.HTMLParser):
    """跟踪 div/script 标签栈, 检查是否全部闭合。Tracks div/script open tags."""

    TRACKED_TAGS = {"div", "script"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.TRACKED_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        pass  # div/script 从不自闭合; void tags 无需跟踪

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.TRACKED_TAGS:
            return
        if not self.stack:
            self.errors.append(f"unexpected </{tag}> with empty stack")
            return
        if self.stack[-1] == tag:
            self.stack.pop()
            return
        # 嵌套不匹配 (如 </div> 闭合了 script) → 视为未闭合
        self.errors.append(f"mismatched </{tag}> (stack top is <{self.stack[-1]}>)")
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()


def extract_style_blocks(html: str) -> list[str]:
    """提取 <style>...</style> 内的 CSS 文本。Extract inline <style> blocks."""
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)
    return blocks


def check_braces(style_blocks: list[str]) -> tuple[bool, str]:
    """统计每个 <style> 块内 { 与 } 数量, 必须完全配对。Count { vs } in each block."""
    for i, block in enumerate(style_blocks):
        open_b = block.count("{")
        close_b = block.count("}")
        if open_b != close_b:
            return False, f"<style>#{i}: {{}} unbalanced ({open_b} {{ vs {close_b} }})"
    return True, "ok"


def check_clip(mg_html: str) -> list[str]:
    """对单个 mg_html 执行契约断言, 返回失败项列表 (空 = 通过)。Return failing checks."""
    failures: list[str] = []

    if "data-composition-id" not in mg_html:
        failures.append("missing data-composition-id on root div")
    if not re.search(r'class="mg-el(?: mg-shape)? clip"', mg_html):
        failures.append('missing class="mg-el clip" on timed elements')
    if "window.__timelines" not in mg_html:
        failures.append("missing window.__timelines registration")

    ok, msg = check_braces(extract_style_blocks(mg_html))
    if not ok:
        failures.append(f"keyframes braces: {msg}")

    parser = TagBalanceParser()
    try:
        parser.feed(mg_html)
        parser.close()
    except html.parser.HTMLParseError as e:  # pragma: no cover - 兼容性
        failures.append(f"HTML parse error: {e}")
    if parser.stack:
        failures.append(f"unclosed tags: {parser.stack}")
    failures.extend(parser.errors)

    return failures


def iter_animation_clips(timeline: dict):
    """遍历所有动画片段 (kind=animation 轨道的 clip 或带 mg_html 的 clip)。

    Yield animation clips across the timeline: any clip on a kind=='animation'
    track, plus any clip with a non-empty mg_html (defensive).
    """
    seen: set[str] = set()
    for track in timeline.get("tracks", []):
        is_anim_track = track.get("kind") == "animation"
        for clip in track.get("clips", []):
            cid = clip.get("id")
            if cid in seen:
                continue
            meta = clip.get("metadata") or {}
            if is_anim_track or (meta.get("mg_html") or "").strip():
                seen.add(cid)
                yield clip


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    project_path = argv[0] if argv else DEFAULT_PROJECT

    try:
        import json

        with open(project_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 无法加载项目 {project_path}: {e}", file=sys.stderr)
        return 2

    timeline = data["timeline"]
    animation_clips = list(iter_animation_clips(timeline))
    mg_clips = [c for c in animation_clips if (c.get("metadata") or {}).get("mg_html")]

    print(f"HyperFrames 契约检查 / contract check — {project_path}")
    print(f"动画片段总数 animation clips: {len(animation_clips)}; 带 mg_html 片段: {len(mg_clips)}")
    print("-" * 70)

    passed = failed = 0
    for clip in mg_clips:
        cid = clip.get("id", "?")
        html = (clip.get("metadata") or {}).get("mg_html", "")
        failures = check_clip(html)
        status = "PASS" if not failures else "FAIL"
        print(f"[{status}] {cid}  ({len(html)} bytes)")
        for fail in failures:
            print(f"        - {fail}")
        if failures:
            failed += 1
        else:
            passed += 1

    # 未带 mg_html 的动画片段不参与断言, 仅提示
    skipped = len(animation_clips) - len(mg_clips)
    if skipped:
        print(f"(跳过 {skipped} 个无 mg_html 的动画片段 / skipped non-html animation clips)")

    print("-" * 70)
    print(f"汇总 summary: PASS {passed} / FAIL {failed} / 共 {len(mg_clips)}")
    verdict = "100% 通过" if failed == 0 and mg_clips else "存在失败项"
    print(f"结论 verdict: {verdict}")
    return 0 if (failed == 0 and mg_clips) else 1


if __name__ == "__main__":
    sys.exit(main())
