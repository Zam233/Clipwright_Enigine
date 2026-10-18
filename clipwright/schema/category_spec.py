"""类型插件规范契约（PRD Part I F1–F5、F14）。

一份类型定义由四块组成：
- ``PromptBundle``：10 个提示词槽位（身份层 3 + 阶段层 7）；
- ``ParamRule``：机器可读的参数规则（"提示词优先"可执行的部分）；
- ``SignatureTrait``：类型同一性的可测代理指标；
- ``PlanSchema`` / ``PlanDocument``：动态规划的维度声明与产物（PRD §II.13）。

设计约束：本模块只做**纯数据与纯函数校验**，不读写文件、不调 LLM、不碰管线，
以便被 API、Agent、CLI 与单测共用。
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ── 槽位（F1）───────────────────────────────────────────────────────────────

#: 身份层：定义"这是什么片子"，参数无法替代。
IDENTITY_SLOTS: tuple[str, ...] = ("genre", "anti_patterns", "lexicon")
#: 阶段层：逐阶段注入的执行规范。
STAGE_SLOTS: tuple[str, ...] = (
    "structure",
    "material",
    "edit",
    "animation",
    "audio",
    "subtitle",
    "quality",
)
SLOT_KEYS: tuple[str, ...] = IDENTITY_SLOTS + STAGE_SLOTS
#: `global` 是 `genre` 的历史别名（D2 简化入口）。
SLOT_ALIASES: dict[str, str] = {"global": "genre"}

#: 单类型提示词总量上限（D3 已拍板）。
MAX_PROMPT_CHARS_TOTAL = 4000
#: 单阶段注入上限（超出按 priority 丢弃）。
MAX_PROMPT_CHARS_PER_STAGE = 1200

#: 不可执行数字的显式豁免标记。
WAIVER_MARKER = "〔不可执行〕"

_NUMBER_CLAIM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:ms|毫秒|秒|s|%|倍)")
_SLOW_STYLE_WORDS = ("缓慢", "长镜", "慢节奏", "舒缓", "不急于")


class PromptSlot(BaseModel):
    """单个提示词槽位。"""

    text: str = ""
    priority: int = Field(default=50, ge=0, le=100)
    max_chars: Optional[int] = Field(default=None, gt=0)

    def effective_text(self) -> str:
        """按 max_chars 截断（按句优先，保底硬截）。"""
        text = self.text or ""
        if self.max_chars is None or len(text) <= self.max_chars:
            return text
        return _truncate_by_sentence(text, self.max_chars)


class ParamRule(BaseModel):
    """机器可读参数规则（F3）。

    mode 语义：floor/ceiling/exact 作用数值；forbid/require 作用枚举与开关。
    enforce=strict 时覆盖用户显式值（冲突 C1），soft 时只记录（C2）。
    """

    key: str
    mode: Literal["floor", "ceiling", "exact", "forbid", "require"]
    value: Any = None
    reason: str = ""
    enforce: Literal["strict", "soft"] = "soft"

    @field_validator("key")
    @classmethod
    def _key_not_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("param_rule.key 不能为空")
        return v.strip()


class SignatureTrait(BaseModel):
    """同一性的可测代理指标（§I.3.4）。"""

    name: str
    mode: Literal["require_ge", "require_le", "require_between"] = "require_ge"
    value: float = 0.0
    value_max: Optional[float] = None
    enforce: Literal["strict", "soft"] = "soft"
    reason: str = ""

    def check(self, actual: float) -> bool:
        if self.mode == "require_ge":
            return actual >= float(self.value)
        if self.mode == "require_le":
            return actual <= float(self.value)
        upper = float(self.value_max if self.value_max is not None else self.value)
        return min(float(self.value), upper) <= actual <= max(float(self.value), upper)


class PlanSchema(BaseModel):
    """本类型的规划维度声明（F14）。"""

    required: list[str] = Field(default_factory=lambda: ["sections", "material_strategy"])
    optional: list[str] = Field(default_factory=list)
    custom_allowed: bool = True


#: 规划维度的已知键（供校验器识别"未知维度"）。
PLAN_DIMENSIONS: tuple[str, ...] = (
    "sections",
    "screen_kinds",
    "beats",
    "material_strategy",
    "risks",
    "acceptance",
    "continuity",
)


class TypeDeclares(BaseModel):
    """类型声明块（F5）：把原先硬编码在 Agent 里的类型行为搬回来。"""

    required_animation_markers: list[str] = Field(default_factory=list)
    outro_animation: Optional[bool] = None
    opening_animation: Optional[bool] = None
    semantic_qa_default: Optional[bool] = None
    search_hint: str = ""
    neighbors: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    plan_schema: Optional[PlanSchema] = None


class PromptBundle(BaseModel):
    """一个类型的完整提示词包。"""

    slots: dict[str, PromptSlot] = Field(default_factory=dict)
    version: int = 1

    def normalized(self) -> "PromptBundle":
        """别名归一（global → genre）+ 去空槽位。"""
        out: dict[str, PromptSlot] = {}
        for key, slot in (self.slots or {}).items():
            real = SLOT_ALIASES.get(key, key)
            if not (slot.text or "").strip():
                continue
            if real in out and out[real].priority >= slot.priority:
                continue
            out[real] = slot
        return PromptBundle(slots=out, version=self.version)

    def text_of(self, key: str) -> str:
        slot = self.normalized().slots.get(SLOT_ALIASES.get(key, key))
        return slot.effective_text() if slot else ""

    def identity_text(self) -> str:
        """身份层合并文本（genre → anti_patterns → lexicon，固定顺序）。"""
        parts: list[str] = []
        for key in IDENTITY_SLOTS:
            text = self.text_of(key).strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    def stage_text(self, stage: str) -> str:
        return self.text_of(stage).strip()

    def total_chars(self) -> int:
        return sum(len(s.effective_text()) for s in self.normalized().slots.values())


# ── 规划文档（II.13）────────────────────────────────────────────────────────


class PlanSection(BaseModel):
    """段落/乐章级规划。"""

    id: str
    name: str = ""
    t0: float = 0.0
    t1: float = 0.0
    purpose: str = ""
    screen_kinds: list[str] = Field(default_factory=list)
    key_events: list[dict[str, Any]] = Field(default_factory=list)


class BeatGrid(BaseModel):
    bpm: float = 0.0
    beat_zero_sec: float = 0.0
    policy: str = ""

    @property
    def beat_sec(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.0


class MaterialStrategy(BaseModel):
    sources: list[str] = Field(default_factory=list)
    grounding: str = ""
    gaps: list[str] = Field(default_factory=list)
    license_policy: str = ""


class RiskItem(BaseModel):
    risk: str
    mitigation: str = ""


class AcceptanceItem(BaseModel):
    metric: str
    target: Any = None


class CustomSection(BaseModel):
    """类型未覆盖的额外规划维度——必须写 reason（§II.13.5）。"""

    name: str
    reason: str = ""
    content: Any = ""


class PlanDocument(BaseModel):
    """整体规划（plan-first 的上游产物）。"""

    version: int = 1
    type: str = ""
    target_duration_sec: float = 0.0
    sections: list[PlanSection] = Field(default_factory=list)
    screen_kinds: list[str] = Field(default_factory=list)
    beats: Optional[BeatGrid] = None
    material_strategy: Optional[MaterialStrategy] = None
    risks: list[RiskItem] = Field(default_factory=list)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)
    custom_sections: list[CustomSection] = Field(default_factory=list)
    legacy: bool = False

    def dimension(self, name: str) -> Any:
        return getattr(self, name, None)


# ── 校验（纯函数）──────────────────────────────────────────────────────────


class SpecIssue(BaseModel):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    where: str = ""


def _truncate_by_sentence(text: str, limit: int) -> str:
    """按句截断，尽量不切在句中；找不到句读则硬截。"""
    head = text[:limit]
    for sep in ("。", "！", "？", "\n", "；", ". ", "! ", "? "):
        idx = head.rfind(sep)
        if idx >= int(limit * 0.5):
            return head[: idx + len(sep)].rstrip()
    return head.rstrip()


def find_dangling_numbers(
    prompts: PromptBundle, rules: list[ParamRule]
) -> list[SpecIssue]:
    """「不许悬空数字」：文本里出现可量化边界却无对应 param_rule。

    判定：含数字断言的槽位，**至少**要出现某条规则的键名（点号末段）或规则值，
    否则视为"写了但机器执行不了"。带 ``〔不可执行〕`` 标记的槽位整体豁免。
    """
    issues: list[SpecIssue] = []
    normalized = prompts.normalized()
    rule_keys = {r.key for r in rules}
    rule_tails = {r.key.split(".")[-1] for r in rules}
    rule_values = {str(r.value) for r in rules if r.value is not None}
    for key, slot in normalized.slots.items():
        text = slot.effective_text()
        if WAIVER_MARKER in text:
            continue
        claims = _NUMBER_CLAIM_RE.findall(text)
        if not claims:
            continue
        mentions_rule = any(k in text for k in rule_keys | rule_tails) or any(
            v in text for v in rule_values if v
        )
        if not mentions_rule:
            issues.append(
                SpecIssue(
                    code="dangling_number",
                    message=(
                        f"槽位 {key} 含可量化边界 {claims[:3]}，但文本未引用任何 "
                        f"param_rule（键名或值）；无法执行等于没写"
                        f"（可加 {WAIVER_MARKER} 豁免并说明理由）"
                    ),
                    where=key,
                )
            )
    return issues


def validate_param_rules(rules: list[ParamRule]) -> list[SpecIssue]:
    """规则自洽性：同 key 取更严；floor/ceiling 出现不可能区间报错。"""
    issues: list[SpecIssue] = []
    by_key: dict[str, list[ParamRule]] = {}
    for rule in rules:
        by_key.setdefault(rule.key, []).append(rule)

    for key, group in by_key.items():
        floors = [float(r.value) for r in group if r.mode == "floor"]
        ceilings = [float(r.value) for r in group if r.mode == "ceiling"]
        if floors and ceilings:
            lo, hi = max(floors), min(ceilings)
            if lo > hi:
                issues.append(
                    SpecIssue(
                        code="impossible_range",
                        message=f"{key} 的 floor({lo}) 大于 ceiling({hi})，区间不可满足",
                        where=key,
                    )
                )
        modes = {r.mode for r in group}
        if "forbid" in modes and "require" in modes:
            issues.append(
                SpecIssue(
                    code="forbid_require_conflict",
                    message=f"{key} 同时声明 forbid 与 require",
                    where=key,
                )
            )
    return issues


def validate_traits_against_genre(
    prompts: PromptBundle, traits: list[SignatureTrait]
) -> list[SpecIssue]:
    """提示词与代理指标不得自相矛盾（最容易写出"假规范"的地方）。"""
    issues: list[SpecIssue] = []
    genre = prompts.text_of("genre")
    slow = any(word in genre for word in _SLOW_STYLE_WORDS)
    for trait in traits:
        if slow and trait.name == "avg_shot_ms" and trait.mode == "require_le" and trait.value < 1500:
            issues.append(
                SpecIssue(
                    code="trait_genre_contradiction",
                    message=(
                        "genre 描述为缓慢/长镜，但指标要求 avg_shot_ms ≤ "
                        f"{trait.value}（<1500ms）；提示词说的是 A、机器验的是 B"
                    ),
                    where=trait.name,
                )
            )
        if trait.mode == "require_between" and trait.value_max is None:
            issues.append(
                SpecIssue(
                    code="trait_missing_upper",
                    message=f"指标 {trait.name} 用 require_between 但缺少 value_max",
                    where=trait.name,
                )
            )
    return issues


def validate_bundle(
    prompts: PromptBundle,
    rules: Optional[list[ParamRule]] = None,
    traits: Optional[list[SignatureTrait]] = None,
) -> list[SpecIssue]:
    """类型定义的完整校验（供 API / CLI / 单测共用）。"""
    rules = rules or []
    traits = traits or []
    issues: list[SpecIssue] = []

    normalized = prompts.normalized()
    for key in normalized.slots:
        if key not in SLOT_KEYS:
            issues.append(
                SpecIssue(code="unknown_slot", message=f"未知槽位 {key}", where=key)
            )
    if not normalized.text_of("genre").strip():
        issues.append(
            SpecIssue(code="genre_required", message="genre 为必填（类型同一性）")
        )
    if prompts.total_chars() > MAX_PROMPT_CHARS_TOTAL:
        issues.append(
            SpecIssue(
                code="prompt_too_long",
                message=(
                    f"提示词合计 {prompts.total_chars()} 字符，超过上限 "
                    f"{MAX_PROMPT_CHARS_TOTAL}"
                ),
            )
        )

    issues.extend(find_dangling_numbers(prompts, rules))
    issues.extend(validate_param_rules(rules))
    issues.extend(validate_traits_against_genre(prompts, traits))
    return issues


# ── 数值 helper（供裁决器复用）─────────────────────────────────────────────


def get_path(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in str(path).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    parts = str(path).split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return data
