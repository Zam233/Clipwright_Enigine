"""回合预算提示与见底护栏（E2E 2026-10-30）。

现场：final_timeline 第 16 回合就有了，模型继续摸底到 160 回合上限失败。
现在：① 提示词里写清剩余回合与收尾阈值；② 剩余 ≤5 时查询类工具直接拒。
"""

from __future__ import annotations

from types import SimpleNamespace

from clipwright.services import agent_loop


def _task(used: int, max_turns: int) -> SimpleNamespace:
    return SimpleNamespace(
        id="task_budget", limits=SimpleNamespace(max_turns=max_turns),
        usage=SimpleNamespace(turns=used),
        request=SimpleNamespace(extra={}),
    )


def test_budget_note_shows_remaining_and_escalates():
    early = agent_loop._budget_note(_task(10, 160))  # noqa: SLF001
    assert "剩余 150/160" in early and "不要重复摸底" in early

    mid = agent_loop._budget_note(_task(140, 160))  # noqa: SLF001
    assert "收尾优先级升高" in mid

    late = agent_loop._budget_note(_task(155, 160))  # noqa: SLF001
    assert "必须立即收尾" in late and "acknowledge_degradation" in late


def test_budget_note_empty_without_limits():
    assert agent_loop._budget_note(SimpleNamespace(id="x")) == ""  # noqa: SLF001


def test_user_prompt_includes_budget_note():
    prompt = agent_loop._user_prompt(_task(150, 160), "上下文")  # noqa: SLF001
    assert "回合预算" in prompt and "剩余 10/160" in prompt
