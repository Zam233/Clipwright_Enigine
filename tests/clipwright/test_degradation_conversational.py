"""M2：降级处置话术与顺序（用户 2026-10-30 裁定：先解释、再问用户）。"""

from __future__ import annotations

from clipwright.services import agent_kernel as k
from clipwright.services.degradation_ledger import RECEIPT_EXEMPT_KINDS


def _hint() -> str:
    return k._DEGRADATION_DECISION_HINT_HEAD + "…" + k._DEGRADATION_DECISION_HINT_TAIL


def test_hint_demands_asking_user_first():
    h = _hint()
    assert "ask_user" in h
    assert "必须先向用户解释并询问" in h
    assert "等待" in h or "等回答" in h


def test_hint_requires_four_parts_and_length_cap():
    h = _hint()
    for part in ("现象", "影响", "方案 A / B", "推荐"):
        assert part in h, part
    assert "120" in h, "必须有字数上限，否则主 agent 会写长篇"


def test_hint_says_receipt_still_required_after_user_accepts():
    h = _hint()
    assert "用户说「可以接受」不等于收据" in h
    assert "acknowledge_degradation" in h


def test_hint_marks_which_kinds_need_asking():
    h = _hint()
    for kind in ("material_placeholder", "text_motion_weak", "stage_tool_failures"):
        assert kind in h, kind
    # 提示词里用通配写法列出免收据类型（llm_json_* 代表 llm_json_invalid/truncated/empty）
    for marker in ("llm_json_*", "mg_critique_skipped", "text_repair"):
        assert marker in h, marker
    assert "llm_json_invalid" in RECEIPT_EXEMPT_KINDS


def test_hint_forbids_silent_pass():
    h = _hint()
    assert "禁止" in h
    assert "既不问用户" in h
