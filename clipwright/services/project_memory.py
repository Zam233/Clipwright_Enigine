"""项目决策记忆（V8）——`memory.md` 四段式人可读日志。

为什么需要
----------
项目已经有**机器可读**的历史：时间线版本（`versionApi`）、Agent 状态、Persona 学习事件。
缺的是**人可读的决策日志**：为什么这里用硬切？为什么这个场景换了素材？
下一个 Agent（或下一次的自己）需要的是"当时的判断与理由"，而不是一堆快照。

四段式（与 video-use 的 `project.md` 同思路）：
- **Strategy（策略）**：这个片子要走什么路线（选题角度、节奏定位、受众）；
- **Decisions（决策）**：做了哪些不可逆或代价高的选择（素材取舍、结构改动、风格定调）；
- **Reasoning（理由）**：每条决策背后的理由与依据（数据、用户反馈、约束）；
- **Outstanding（待办）**：还没解决的问题、已知风险、需要复核的点。

写法上刻意保持**纯 Markdown + 追加式**：人可以手改、可以 diff、可以贴进 PR；
程序只在对应段落追加条目（不做覆盖，避免把人的编辑冲掉）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

#: 四个段落（顺序即渲染顺序）
SECTIONS: tuple[str, ...] = ("Strategy", "Decisions", "Reasoning", "Outstanding")

#: 段落标题（中英并列，便于人读）
SECTION_TITLES: dict[str, str] = {
    "Strategy": "Strategy · 策略",
    "Decisions": "Decisions · 决策",
    "Reasoning": "Reasoning · 理由",
    "Outstanding": "Outstanding · 待办",
}

_HEADER = "# 项目决策记忆"
_SECTION_RE = re.compile(r"^##\s+(Strategy|Decisions|Reasoning|Outstanding)\b.*$", re.MULTILINE)


@dataclass
class MemoryEntry:
    """一条记忆：段落 + 文本 + 时间戳（可选来源）。"""
    section: str
    text: str
    at: str = ""
    source: str = ""


@dataclass
class ProjectMemory:
    """解析后的记忆文档（四段 + 段落外的前言）。"""
    entries: dict[str, list[MemoryEntry]] = field(default_factory=lambda: {s: [] for s in SECTIONS})
    preamble: str = ""

    def add(self, section: str, text: str, *, source: str = "") -> MemoryEntry:
        key = _normalize_section(section)
        entry = MemoryEntry(section=key, text=text.strip(), at=_stamp(), source=source)
        if entry.text:
            self.entries[key].append(entry)
        return entry

    def extend(self, section: str, texts: Iterable[str], *, source: str = "") -> int:
        return sum(1 for t in texts if self.add(section, t, source=source).text)

    def count(self) -> int:
        return sum(len(v) for v in self.entries.values())

    def is_empty(self) -> bool:
        return self.count() == 0

    def to_markdown(self) -> str:
        lines: list[str] = [_HEADER, ""]
        if self.preamble.strip():
            lines.extend([self.preamble.strip(), ""])
        for section in SECTIONS:
            lines.append(f"## {SECTION_TITLES[section]}")
            lines.append("")
            items = self.entries.get(section) or []
            if not items:
                lines.append("（暂无）")
            for entry in items:
                suffix = f"（{entry.at}{'，' + entry.source if entry.source else ''}）" if entry.at else ""
                lines.append(f"- {entry.text}{suffix}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _normalize_section(section: str) -> str:
    """段落名归一化（大小写/中文别名都能认）。"""
    key = (section or "").strip()
    for candidate in SECTIONS:
        if key.lower() == candidate.lower():
            return candidate
    aliases = {
        "策略": "Strategy", "方向": "Strategy",
        "决策": "Decisions", "决定": "Decisions",
        "理由": "Reasoning", "原因": "Reasoning", "依据": "Reasoning",
        "待办": "Outstanding", "遗留": "Outstanding", "风险": "Outstanding",
    }
    if key in aliases:
        return aliases[key]
    raise ValueError(f"未知段落: {section!r}（可用：{', '.join(SECTIONS)}）")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def parse_memory(markdown: str) -> ProjectMemory:
    """解析 `memory.md`：按 `## 段落` 切分，`- 条目` 逐条读入（容错，不抛异常）。"""
    memory = ProjectMemory()
    current: str | None = None
    preamble: list[str] = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        if line.startswith("# ") and not line.startswith("## "):
            continue
        match = _SECTION_RE.match(line)
        if match:
            try:
                current = _normalize_section(match.group(1))
            except ValueError:
                current = None
            continue
        if current is None:
            if line.strip():
                preamble.append(line)
            continue
        if not line.strip():
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", line.strip())
        text = bullet.group(1).strip() if bullet else line.strip()
        if text in ("（暂无）", "(暂无)"):
            continue
        # 末尾的 `（2026-09-13 10:00，来源）` 是元信息，解析回字段
        at, source = "", ""
        meta = re.search(r"[（(]([^（）()]*)[）)]\s*$", text)
        if meta:
            inner = meta.group(1)
            text = text[: meta.start()].strip()
            parts = [p.strip() for p in inner.split("，") if p.strip()]
            if parts and re.match(r"^\d{4}-\d{2}-\d{2}", parts[0]):
                at = parts[0]
                source = parts[1] if len(parts) > 1 else ""
            else:
                source = parts[0] if parts else ""
        if text:
            memory.entries[current].append(MemoryEntry(section=current, text=text, at=at, source=source))
    memory.preamble = "\n".join(preamble)
    return memory


def memory_path(project_dir: str | Path) -> Path:
    """项目目录下的 `memory.md` 路径（与 `project.json` 同级）。"""
    return Path(project_dir) / "memory.md"


def load_memory(project_dir: str | Path) -> ProjectMemory:
    """读项目记忆；文件不存在时返回空记忆（不报错——新项目本来就没有）。"""
    path = memory_path(project_dir)
    if not path.exists():
        return ProjectMemory()
    try:
        return parse_memory(path.read_text(encoding="utf-8"))
    except Exception:
        return ProjectMemory()


def save_memory(project_dir: str | Path, memory: ProjectMemory) -> Path:
    """写项目记忆（原子替换：先写临时文件再 replace，避免半截文件）。"""
    path = memory_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(memory.to_markdown(), encoding="utf-8")
    tmp.replace(path)
    return path


def append_entries(
    project_dir: str | Path,
    entries: dict[str, Iterable[str]] | Iterable[tuple[str, str]],
    *,
    source: str = "",
) -> ProjectMemory:
    """向项目记忆**追加**条目并落盘（最常用入口：Agent 收尾时记一笔）。

    `entries` 可以是 `{"Decisions": ["..."], "Outstanding": ["..."]}`，
    也可以是 `[("Decisions", "..."), ...]`。返回更新后的记忆。
    """
    memory = load_memory(project_dir)
    items: Iterable[tuple[str, str]]
    if isinstance(entries, dict):
        pairs: list[tuple[str, str]] = []
        for section, texts in entries.items():
            # 字符串本身就是可迭代对象——不特判就会把 "竖屏科普" 拆成 4 个单字条目
            if isinstance(texts, str):
                pairs.append((section, texts))
            else:
                pairs.extend((section, str(t)) for t in texts)
        items = pairs
    else:
        items = entries
    for section, text in items:
        memory.add(section, text, source=source)
    save_memory(project_dir, memory)
    return memory


def render_summary(memory: ProjectMemory, *, limit: int = 5) -> str:
    """紧凑摘要（喂给 LLM 的形态）：每段最多 `limit` 条，超出只记数量。"""
    if memory.is_empty():
        return ""
    lines: list[str] = ["# 项目决策记忆（摘要）"]
    for section in SECTIONS:
        items = memory.entries.get(section) or []
        if not items:
            continue
        lines.append(f"## {section}")
        for entry in items[-limit:]:
            lines.append(f"- {entry.text}")
        if len(items) > limit:
            lines.append(f"- （另有 {len(items) - limit} 条更早记录）")
    return "\n".join(lines)
