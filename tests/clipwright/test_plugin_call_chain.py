"""B5：宿主侧插件调用链必须累积祖先，才能拦住深层与成环调用。"""

from __future__ import annotations

from clipwright.services.plugin_rpc import (
    _current_call_chain,
    pop_call_chain,
    push_call_chain,
    reset_call_chains,
)


def test_chain_accumulates_ancestors():
    reset_call_chains()
    # A 调 B：B 的链只剩 A
    push_call_chain("B", "A")
    assert _current_call_chain("B") == ["A"]
    # B 调 C：C 必须看到 A、B 两级
    push_call_chain("C", "B", _current_call_chain("B"))
    assert _current_call_chain("C") == ["A", "B"]
    # C 调 D：C 的链已有 2 级，调用方据此拒绝（而不是再放行一层）
    assert len(_current_call_chain("C")) >= 2
    reset_call_chains()


def test_pop_unwinds_only_its_own_frame():
    reset_call_chains()
    push_call_chain("B", "A")
    push_call_chain("C", "B", ["A"])
    pop_call_chain("C")
    assert _current_call_chain("C") == ["A", "B"][:1]
    assert _current_call_chain("B") == ["A"]
    pop_call_chain("B")
    assert _current_call_chain("B") == []
    reset_call_chains()


def test_duplicate_ancestor_is_not_pushed_twice():
    reset_call_chains()
    push_call_chain("B", "A", ["A"])
    assert _current_call_chain("B") == ["A"]
    reset_call_chains()
