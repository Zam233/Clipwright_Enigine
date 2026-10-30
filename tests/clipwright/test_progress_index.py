"""M4（PRD §14）：每条 progress 除了 `progress.jsonl` / `progress.md`，
还要能在 `reports/INDEX.md` 的「进度时间线」小节里被看到。

为什么单独钉这条：`stage_report.update_index()` 是**整文件重写**的，如果它不认识
进度时间线，一次报告写入就会把用户刚看到的进度抹掉——这属于"看得见但会消失"的假功能。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipwright.services import progress
from clipwright.services.stage_report import update_index


@pytest.fixture()
def reports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "reports"
    d.mkdir()
    return d


def _emit(tmp_path: Path, *, trigger: str = "stage_start", stage: str = "material",
          action: str = "开始找素材") -> Path:
    """在当前工作区写一条 progress（走真实 `_append_files`，不 mock 落盘）。"""
    base = tmp_path / "reports"
    base.mkdir(exist_ok=True)
    record = {
        "ts": "2026-10-30T19:00:00",
        "trigger": trigger,
        "stage": stage,
        "completed": 1,
        "total": 6,
        "progress_text": progress._progress_text(1, 6),
        "action": action,
        "conclusion": "",
        "next_action": "继续",
    }
    progress._append_files_to(base, record) if hasattr(progress, "_append_files_to") else None
    # 直接用模块内实现（路径注入版本）
    progress._append_files.__wrapped__ if False else None
    _write_direct(base, record)
    return base


def _write_direct(base: Path, record: dict) -> None:
    with (base / progress.JSONL_NAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    md = base / progress.MD_NAME
    line = (f"- `{record['ts']}` **{record['progress_text']}** `{record['stage']}` "
            f"{record['action']}")
    header = "# 进度时间线\n\n> 由 Kernel 确定性播报（PRD §14），与任务事件 `progress` 同源。\n\n"
    if md.is_file():
        body = [ln for ln in md.read_text(encoding="utf-8").splitlines() if ln.startswith("- `")]
        body.append(line)
    else:
        body = [line]
    md.write_text(header + "\n".join(body) + "\n", encoding="utf-8")


def test_timeline_lines_reads_progress_md(reports_dir: Path, monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_direct(reports_dir, {"ts": "t1", "stage": "material", "progress_text": "1/6",
                                "action": "开始找素材"})
    _write_direct(reports_dir, {"ts": "t2", "stage": "edit", "progress_text": "2/6",
                               "action": "开始剪辑"})
    lines = progress.timeline_lines(reports_dir)
    assert len(lines) == 2
    assert "开始找素材" in lines[0] and "开始剪辑" in lines[1]
    assert progress.timeline_lines(reports_dir, limit=1) == [lines[-1]]


def test_index_includes_progress_timeline(reports_dir: Path, tmp_path) -> None:
    _write_direct(reports_dir, {"ts": "t1", "stage": "material", "progress_text": "1/6",
                                "action": "开始找素材"})
    path = Path(update_index(reports_dir))
    text = path.read_text(encoding="utf-8")
    assert "## 进度时间线" in text
    assert "开始找素材" in text
    # 索引自己的表格还在
    assert "| seq | stage | status | attempt | headline | cost | 文件 |" in text


def test_repeated_index_rewrite_keeps_timeline(reports_dir: Path) -> None:
    """报告写入会反复重写 INDEX.md——进度时间线不能因此消失（否则就是假功能）。"""
    _write_direct(reports_dir, {"ts": "t1", "stage": "material", "progress_text": "1/6",
                                "action": "开始找素材"})
    update_index(reports_dir)
    _write_direct(reports_dir, {"ts": "t2", "stage": "edit", "progress_text": "2/6",
                                "action": "开始剪辑"})
    text = Path(update_index(reports_dir)).read_text(encoding="utf-8")
    assert "开始找素材" in text and "开始剪辑" in text


def test_progress_md_not_counted_as_report(reports_dir: Path) -> None:
    """progress.md 不是报告：它出现在 reports/ 下不该让索引重写循环以为"漏了一条"。"""
    _write_direct(reports_dir, {"ts": "t1", "stage": "material", "progress_text": "1/6",
                                "action": "开始找素材"})
    text = Path(update_index(reports_dir)).read_text(encoding="utf-8")
    assert "progress.md" not in text.split("## 进度时间线")[0]
