"""重复只读查询护栏（E2E 2026-10-30）。

现场：一次任务 161 次工具调用里有 121 次是 list_dir/read_artifact/list_artifacts/
list_reports/read_report —— 模型每轮都重新"摸底"，160 回合被烧光、任务触闸失败。
护栏只拦**只读**工具的同参数重复；写操作与派发必须真的执行。
"""

from __future__ import annotations

import pytest

from clipwright.services import agent_loop


def test_repeatable_set_covers_read_tools_only():
    assert agent_loop._is_repeatable("list_dir")  # noqa: SLF001
    assert agent_loop._is_repeatable("read_report")  # noqa: SLF001
    assert not agent_loop._is_repeatable("video_trim")  # noqa: SLF001
    assert not agent_loop._is_repeatable("delegate_agent")  # noqa: SLF001
    assert not agent_loop._is_repeatable("write_file")  # noqa: SLF001


def test_tool_seen_cache_is_bounded_and_keyed_by_args():
    agent_loop._TOOL_SEEN.clear()  # noqa: SLF001
    from clipwright.tool.surface import idem_key_for

    k1 = idem_key_for("task_d", "list_dir", {"path": "materials"})
    k2 = idem_key_for("task_d", "list_dir", {"path": "renders"})
    assert k1 != k2
    agent_loop._TOOL_SEEN[("task_d", k1)] = {"status": "ok", "output": {"files": []}}  # noqa: SLF001
    assert ("task_d", k2) not in agent_loop._TOOL_SEEN  # noqa: SLF001
    agent_loop._TOOL_SEEN.clear()  # noqa: SLF001
