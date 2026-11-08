"""确定性 lint（PRD C6 / R-02）：扫描场景脚本里的"墙钟依赖"。

HyperFrames 逐帧 seek 的契约是"渲染器给一个时间值，产出一帧像素"（上游
``determinism-rules.md``）。任何按真实时间推进的 API 都会破坏这一点：

- ``setTimeout`` / ``setInterval`` / ``requestAnimationFrame``：动画自己按墙钟跑；
- ``Date.now()`` / ``new Date()``：结果依赖渲染时刻；
- ``Math.random()``：两次渲染不同（应用 ``prng(t, salt)``）；
- ``fetch`` / ``XMLHttpRequest``：渲染期网络请求（上游明令禁止）；
- ``import()`` 动态加载：多 worker 下解析时序不定。

用法::

    python -m tools.lint_determinism examples/           # 退出码 1 = 有命中
    python -m tools.lint_determinism --json examples/    # 机器可读

允许在行尾加 ``// deterministic-ok`` 显式豁免（必须写理由，便于评审）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

#: (正则, 说明)。顺序固定，输出稳定。
BANNED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bsetTimeout\s*\(", "setTimeout：动画会按墙钟推进，seek 管不到"),
    (r"\bsetInterval\s*\(", "setInterval：同上"),
    (r"\brequestAnimationFrame\s*\(", "requestAnimationFrame：同上"),
    (r"\bDate\s*\.\s*now\s*\(", "Date.now()：结果依赖渲染时刻"),
    (r"\bnew\s+Date\s*\(", "new Date()：同上"),
    (r"\bMath\s*\.\s*random\s*\(", "Math.random()：两次渲染不一致，请用 prng(t, salt)"),
    (r"\bfetch\s*\(", "fetch()：渲染期禁止网络请求"),
    (r"\bXMLHttpRequest\b", "XMLHttpRequest：同上"),
    (r"\bdocument\s*\.\s*cookie\b", "document.cookie：渲染不应依赖外部状态"),
    (r"\blocalStorage\b", "localStorage：跨帧状态，违反纯函数要求"),
    (r"\bsessionStorage\b", "sessionStorage：同上"),
    (r"\bimport\s*\(", "import() 动态加载：多 worker 下时序不定"),
)

_COMPILED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), hint) for pattern, hint in BANNED_PATTERNS
)

#: 参与扫描的扩展名。
SCAN_SUFFIXES: tuple[str, ...] = (".js", ".mjs", ".cjs", ".ts", ".html")
#: 豁免标记。
WAIVER = "deterministic-ok"


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    pattern: str
    hint: str
    snippet: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.hint}\n    {self.snippet}"


def scan_text(text: str, path: str = "<memory>") -> list[Hit]:
    """扫描一段 JS/HTML 文本。"""
    hits: list[Hit] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if WAIVER in raw:
            continue
        for pattern, hint in _COMPILED:
            match = pattern.search(raw)
            if match:
                hits.append(
                    Hit(
                        path=path,
                        line=lineno,
                        pattern=match.group(0),
                        hint=hint,
                        snippet=raw.strip()[:160],
                    )
                )
    return hits


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            if path.suffix.lower() in SCAN_SUFFIXES:
                yield path
            continue
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in SCAN_SUFFIXES:
                yield child


def scan_paths(paths: Iterable[str | Path]) -> list[Hit]:
    """扫描文件或目录（目录递归，只扫 JS/TS/HTML）。"""
    resolved = [Path(p) for p in paths]
    hits: list[Hit] = []
    for file_path in _iter_files(resolved):
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits.extend(scan_text(text, str(file_path)))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描场景脚本里的墙钟/随机依赖")
    parser.add_argument("paths", nargs="+", help="文件或目录（可多个）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    hits = scan_paths(args.paths)
    if args.json:
        print(json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2))
    else:
        for hit in hits:
            print(hit.render())
        if hits:
            print(f"\n共 {len(hits)} 处确定性风险。用 `// {WAIVER}` 显式豁免（需写理由）。")
        else:
            print("确定性 lint 通过：未发现墙钟/随机/网络依赖。")
    return 1 if hits else 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    sys.exit(main())
