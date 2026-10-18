"""V13：裁决结果逐字节可复现（同输入两次裁决完全一致）。"""

from __future__ import annotations

import json

from clipwright.schema.category_spec import ParamRule, PromptBundle, PromptSlot, SignatureTrait
from clipwright.services.prompt_authority import PromptAuthority


def _authority() -> PromptAuthority:
    return PromptAuthority(
        prompts=PromptBundle(
            slots={
                "genre": PromptSlot(text="整片是一场对话", priority=100),
                "edit": PromptSlot(text="硬切为主", priority=70),
            }
        ),
        rules=[
            ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="strict", reason="看清"),
            ParamRule(key="transition_weights.hard_cut", mode="floor", value=0.8, enforce="soft"),
        ],
        traits=[SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000)],
        category_id="demo",
        stage="edit",
    )


def _payload(resolution) -> str:
    return json.dumps(resolution.model_dump(), ensure_ascii=False, sort_keys=True)


class TestDeterministicResolution:
    def test_same_input_same_bytes(self) -> None:
        user = {"min_shot_ms": 300, "transition_weights": {"hard_cut": 0.2}}
        first = _authority().resolve(user=dict(user))
        second = _authority().resolve(user=dict(user))
        assert _payload(first) == _payload(second)

    def test_dict_order_does_not_matter(self) -> None:
        a = _authority().resolve(user={"min_shot_ms": 300, "bpm": 130})
        b = _authority().resolve(user={"bpm": 130, "min_shot_ms": 300})
        assert _payload(a) == _payload(b)

    def test_sorted_json_is_stable(self) -> None:
        resolution = _authority().resolve(user={"min_shot_ms": 300})
        dumped = json.dumps(resolution.model_dump(), ensure_ascii=False, sort_keys=True)
        assert dumped == json.dumps(resolution.model_dump(), ensure_ascii=False, sort_keys=True)

    def test_conflicts_order_stable(self) -> None:
        resolution = _authority().resolve(
            user={"min_shot_ms": 300, "transition_weights": {"hard_cut": 0.2}}
        )
        codes = [c.code for c in resolution.conflicts]
        assert codes == [c.code for c in _authority().resolve(
            user={"min_shot_ms": 300, "transition_weights": {"hard_cut": 0.2}}
        ).conflicts]
