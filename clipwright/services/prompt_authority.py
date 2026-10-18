"""提示词优先级裁决器（PRD Part I F2，A-03）。

把「平台底线 / 用户输入 / 类型提示词 / 类型参数 / Persona / 默认值」合成：

- 一份**带出处**的最终参数（``params`` + ``trace``）；
- 一份**最终提示词文本**（身份层 + 阶段层，带截断与丢弃记录）；
- 若干**冲突记录**（C1 strict 覆盖用户值、C2 soft 只记录、C9 类型变形）；
- 一份**指标可达性预判**（``traits``）。

裁决顺序（关键，与 PRD §I.3.2 一致）：**L2 先定域，L3 只在域内取值**。
实现上体现为：先按 L5→L4→L3→L1 合并剂量值，再用 ``param_rules`` 作**后置边界**
（strict 覆盖用户值 / soft 只记录），最后用平台规则强制生效；随后判定类型变形。

本模块不调 LLM、不写文件，纯函数式，便于单测与回放。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from clipwright.schema.category_spec import (
    MAX_PROMPT_CHARS_PER_STAGE,
    ParamRule,
    PromptBundle,
    SignatureTrait,
    get_path,
    set_path,
)

Layer = Literal[
    "L0_platform",
    "L1_user",
    "L2_prompt",
    "L3_category",
    "L4_persona",
    "L5_default",
]


class ConflictRecord(BaseModel):
    """一次冲突（对应 PRD §I.3.3 的 C 编号）。"""

    code: str                 # C1 / C2 / C9
    key: str = ""
    message: str = ""
    winner_layer: Layer = "L2_prompt"
    user_value: Any = None
    rule_value: Any = None
    enforce: Literal["strict", "soft"] = "soft"
    reason: str = ""
    rule_source: str = ""


class TraitPreview(BaseModel):
    name: str
    mode: str
    threshold: float
    feasible: Optional[bool] = None
    note: str = ""


class PromptResolution(BaseModel):
    """一次裁决的全部产物（可序列化进 ``prompt_resolution`` 事件）。"""

    params: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, dict[str, Any]] = Field(default_factory=dict)
    prompts: dict[str, str] = Field(default_factory=dict)
    identity_text: str = ""
    slots: list[dict[str, Any]] = Field(default_factory=list)
    dropped_slots: list[str] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    traits: list[TraitPreview] = Field(default_factory=list)
    deformation: Optional[dict[str, Any]] = None

    def param(self, key: str, default: Any = None) -> Any:
        return get_path(self.params, key) if "." in key else self.params.get(key, default)

    def source_of(self, key: str) -> str:
        entry = self.trace.get(key) or {}
        return str(entry.get("source_layer") or "")

    def has_conflict(self, code: str) -> bool:
        return any(c.code == code for c in self.conflicts)


class PromptAuthority:
    """类型规范的裁决器。"""

    def __init__(
        self,
        *,
        prompts: Optional[PromptBundle] = None,
        rules: Optional[list[ParamRule]] = None,
        traits: Optional[list[SignatureTrait]] = None,
        platform_rules: Optional[list[ParamRule]] = None,
        category_id: str = "",
        stage: str = "",
    ) -> None:
        self.prompts = (prompts or PromptBundle()).normalized()
        self.rules = list(rules or [])
        self.traits = list(traits or [])
        #: 平台底线（L0）：一律 strict，且**不可**被用户/类型覆盖。
        self.platform_rules = list(platform_rules or [])
        self.category_id = category_id
        self.stage = stage

    # ── 主入口 ────────────────────────────────────────────

    def resolve(
        self,
        *,
        defaults: Optional[dict[str, Any]] = None,
        persona: Optional[dict[str, Any]] = None,
        category_params: Optional[dict[str, Any]] = None,
        user: Optional[dict[str, Any]] = None,
    ) -> PromptResolution:
        params: dict[str, Any] = {}
        trace: dict[str, dict[str, Any]] = {}

        self._apply_layer(params, trace, defaults or {}, "L5_default", "defaults")
        self._apply_layer(params, trace, persona or {}, "L4_persona", "persona")
        self._apply_layer(
            params, trace, category_params or {}, "L3_category", self.category_id or "category"
        )
        user_given = dict(user or {})
        self._apply_layer(params, trace, user_given, "L1_user", "request")

        conflicts: list[ConflictRecord] = []
        self._apply_type_rules(params, trace, user_given, conflicts)
        self._apply_platform(params, trace, conflicts)

        deformation = self._detect_deformation(params)
        traits = self._preview_traits(params)
        prompts, slots, dropped = self._render_prompts()

        return PromptResolution(
            params=params,
            trace=trace,
            prompts=prompts,
            identity_text=self.prompts.identity_text(),
            slots=slots,
            dropped_slots=dropped,
            conflicts=conflicts,
            traits=traits,
            deformation=deformation,
        )

    # ── 分层写入 ──────────────────────────────────────────

    @staticmethod
    def _apply_layer(
        params: dict[str, Any],
        trace: dict[str, dict[str, Any]],
        incoming: dict[str, Any],
        layer: Layer,
        source_id: str,
    ) -> None:
        for key, value in (incoming or {}).items():
            if value is None:
                continue
            params[key] = value
            trace[key] = {"source_layer": layer, "source_id": source_id}

    # ── L2 规则 ───────────────────────────────────────────

    def _apply_type_rules(
        self,
        params: dict[str, Any],
        trace: dict[str, dict[str, Any]],
        user_given: dict[str, Any],
        conflicts: list[ConflictRecord],
    ) -> None:
        for rule in self.rules:
            current = get_path(params, rule.key)
            if rule.mode == "require":
                if current is None or current is False:
                    set_path(params, rule.key, True)
                    trace[rule.key] = {
                        "source_layer": "L2_prompt",
                        "source_id": f"param_rule:{rule.key}",
                    }
                continue
            if rule.mode == "forbid":
                if current is not None and _same(current, rule.value):
                    _delete_path(params, rule.key)
                    trace[rule.key] = {
                        "source_layer": "L2_prompt",
                        "source_id": f"param_rule:{rule.key}",
                        "removed": True,
                    }
                continue

            new_value, violated = _bounded_value(current, rule)
            if not violated:
                if current is None and new_value is not None:
                    set_path(params, rule.key, new_value)
                    trace[rule.key] = {
                        "source_layer": "L2_prompt",
                        "source_id": f"param_rule:{rule.key}",
                    }
                continue

            # 规则与现有值冲突：按 enforce 决定谁赢
            user_value = user_given.get(rule.key)
            if rule.enforce == "strict":
                set_path(params, rule.key, new_value)
                trace[rule.key] = {
                    "source_layer": "L2_prompt",
                    "source_id": f"param_rule:{rule.key}",
                    "overridden_by": "prompt",
                }
                conflicts.append(
                    ConflictRecord(
                        code="C1",
                        key=rule.key,
                        message=(
                            f"{rule.key} 被类型规范改回 {new_value}"
                            f"（用户给了 {user_value if user_value is not None else current}）"
                        ),
                        winner_layer="L2_prompt",
                        user_value=user_value,
                        rule_value=new_value,
                        enforce="strict",
                        reason=rule.reason,
                    )
                )
            else:
                if current is not None:
                    conflicts.append(
                        ConflictRecord(
                            code="C2",
                            key=rule.key,
                            message=(
                                f"{rule.key} 与 soft 规则不一致：保留现值 {current}"
                                f"（规则建议 {new_value}）"
                            ),
                            winner_layer="L1_user" if user_value is not None else "L3_category",
                            user_value=user_value,
                            rule_value=new_value,
                            enforce="soft",
                            reason=rule.reason,
                        )
                    )
                else:
                    set_path(params, rule.key, new_value)

    def _apply_platform(
        self,
        params: dict[str, Any],
        trace: dict[str, dict[str, Any]],
        conflicts: list[ConflictRecord],
    ) -> None:
        """L0 平台底线：强制生效，且记录被它压掉的用户值。"""
        for rule in self.platform_rules:
            current = get_path(params, rule.key)
            if rule.mode == "forbid":
                if current is not None and _same(current, rule.value):
                    _delete_path(params, rule.key)
                continue
            new_value, violated = _bounded_value(current, rule)
            if new_value is None:
                continue
            if violated or rule.mode == "require":
                previous = current
                set_path(params, rule.key, new_value)
                trace[rule.key] = {
                    "source_layer": "L0_platform",
                    "source_id": f"platform_rule:{rule.key}",
                }
                conflicts.append(
                    ConflictRecord(
                        code="C6",
                        key=rule.key,
                        message=f"{rule.key} 受平台底线约束为 {new_value}（原值 {previous}）",
                        winner_layer="L0_platform",
                        user_value=previous,
                        rule_value=new_value,
                        enforce="strict",
                        reason=rule.reason,
                    )
                )
            elif current is None:
                set_path(params, rule.key, new_value)
                trace[rule.key] = {
                    "source_layer": "L0_platform",
                    "source_id": f"platform_rule:{rule.key}",
                }

    # ── 类型变形（C9）─────────────────────────────────────

    def _detect_deformation(self, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        reasons: list[str] = []
        lo = _num(get_path(params, "min_shot_ms"))
        hi = _num(get_path(params, "max_shot_ms"))
        if lo is not None and hi is not None and lo > hi:
            reasons.append(f"min_shot_ms({lo}) 大于 max_shot_ms({hi})，区间不可满足")
        for trait in self.traits:
            feasible = self._trait_feasible(trait, lo, hi)
            if feasible is False:
                reasons.append(
                    f"指标 {trait.name}（{trait.mode} {trait.value}）"
                    "在当前参数下结构性不可满足"
                )
        if not reasons:
            return None
        return {
            "code": "C9",
            "reasons": reasons,
            "suggested_type": None,
            "note": "只能回落到类型合法域，或显式切换类型；不提供豁免",
        }

    def _preview_traits(self, params: dict[str, Any]) -> list[TraitPreview]:
        lo = _num(get_path(params, "min_shot_ms"))
        hi = _num(get_path(params, "max_shot_ms"))
        out: list[TraitPreview] = []
        for trait in self.traits:
            feasible = self._trait_feasible(trait, lo, hi)
            out.append(
                TraitPreview(
                    name=trait.name,
                    mode=trait.mode,
                    threshold=float(trait.value),
                    feasible=feasible,
                    note="" if feasible is not None else "无法由参数推断，交由采集期计算",
                )
            )
        return out

    @staticmethod
    def _trait_feasible(
        trait: SignatureTrait, lo: Optional[float], hi: Optional[float]
    ) -> Optional[bool]:
        """只对能由镜头时长边界推断的指标给结论，其余返回 None。"""
        if trait.name not in {"avg_shot_ms", "base_shot_ms"}:
            return None
        if trait.mode == "require_le" and lo is not None:
            return lo <= float(trait.value)
        if trait.mode == "require_ge" and hi is not None:
            return hi >= float(trait.value)
        if trait.mode == "require_between" and lo is not None and trait.value_max is not None:
            return lo <= max(float(trait.value), float(trait.value_max))
        return None

    # ── 提示词渲染 ────────────────────────────────────────

    def _render_prompts(
        self,
    ) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
        out: dict[str, str] = {}
        slots: list[dict[str, Any]] = []
        dropped: list[str] = []
        budget = MAX_PROMPT_CHARS_PER_STAGE

        ordered = sorted(
            self.prompts.slots.items(), key=lambda kv: (-kv[1].priority, kv[0])
        )
        used = 0
        for key, slot in ordered:
            text = slot.effective_text()
            if not text:
                continue
            truncated = len(text) < len(slot.text or "")
            if used + len(text) > budget:
                dropped.append(key)
                continue
            used += len(text)
            out[key] = text
            slots.append(
                {
                    "key": key,
                    "priority": slot.priority,
                    "chars": len(text),
                    "truncated": truncated,
                    "source": f"category:{self.category_id}" if self.category_id else "category",
                }
            )
        return out, slots, dropped

    def stage_text(self) -> str:
        """当前阶段应注入的文本（身份层 + 阶段层，固定顺序）。"""
        parts: list[str] = []
        identity = self.prompts.identity_text().strip()
        if identity:
            parts.append(identity)
        if self.stage:
            stage = self.prompts.stage_text(self.stage)
            if stage:
                parts.append(stage)
        return "\n\n".join(parts)


# ── 工具函数 ────────────────────────────────────────────────────────────────


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def _delete_path(data: dict[str, Any], path: str) -> None:
    parts = str(path).split(".")
    cur: Any = data
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _bounded_value(current: Any, rule: ParamRule) -> tuple[Any, bool]:
    """按 mode 计算规则值，并返回是否与原值冲突。"""
    if rule.value is None:
        return None, False
    if rule.mode == "exact":
        return rule.value, current is not None and not _same(current, rule.value)
    cur_num = _num(current)
    rule_num = _num(rule.value)
    if rule_num is None:
        return rule.value, current is not None and not _same(current, rule.value)
    if cur_num is None:
        return rule_num, False
    if rule.mode == "floor":
        return (rule_num if cur_num < rule_num else cur_num), cur_num < rule_num
    if rule.mode == "ceiling":
        return (rule_num if cur_num > rule_num else cur_num), cur_num > rule_num
    return rule_num, False


__all__ = [
    "ConflictRecord",
    "Layer",
    "PromptAuthority",
    "PromptResolution",
    "TraitPreview",
]
