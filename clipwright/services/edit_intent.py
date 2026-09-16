"""自然语言剪辑意图（R5）——把"用户那句话"变成**可校验的结构化操作**。

为什么需要这一层
----------------
系统里已经有一条 NL→时间线的通路（`requirements_service._edit_adjust`：LLM 产出
`{ops:[{clip_id,field,value}]}` + `_ADJUST_FIELDS` 白名单 + 值域钳制）。它是对的，但太窄：
只能改**已选中片段**的**数值字段**，表达不了"只保留有人脸的镜头""把黑场去掉"
"剪到 60 秒""按运动量重排"这类**内容条件与结构操作**。

R5 不另起炉灶，而是把那条通路**升级成一套完整的意图契约**（`FIELD_SPECS` 与
`OP_SPECS` 成为唯一事实来源，`_ADJUST_FIELDS` 直接复用它），并补上三件事：

1. **op 词表固定**：keep / remove / reorder / trim / set / add / transition。
   未知 op 一律**拒绝**而不是让模型自由发挥——这是"不幻觉、不失控"的落点；
2. **target 有多种寻址方式**：`all` | `selected` | `time_range` | `query` | `filter` | `ref` | `index`，
   其中内容条件（`query`/`filter`）**直接复用 R4 的 `route_query`**——
   这样"找黑场"和"去掉黑场"用的是同一套词汇，不会出现两套互不认识的领域语言；
3. **解析有两条路**：LLM（结构化输出，受 schema 约束）+ **确定性规则解析器**
   （常见说法全覆盖，没有 API key 也能用，且可回归测试）。

诚实的边界：
- 规则解析器覆盖的是**常见说法**（本文件 `EXAMPLES` 与基准夹具），长尾说法会落到
  `unresolved` 并触发 R6 的澄清反问，而不是硬猜；
- 中文数字只支持 〇-十/百/千 的常规写法；
- 复杂复合指令（"把前三段换掉，然后把结尾加速两倍并加字幕"）依赖 LLM 路径，
  规则路径只做单句单意图（多意图也支持但更保守）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from pydantic import BaseModel, Field

#: 平台约束的合法值
PLATFORMS: tuple[str, ...] = ("landscape", "vertical", "square", "portrait", "横屏", "竖屏", "方形")
#: 重排依据
REORDER_KEYS: tuple[str, ...] = ("time", "duration", "brightness", "motion", "sharpness", "score")
#: 可新增的片段类型
ADD_KINDS: tuple[str, ...] = ("text", "caption", "animation", "audio", "image", "shape")

_IMAGE_FIT = ("cover", "contain", "free")
_TEXT_ALIGN = ("left", "center", "right")
_BLEND_MODES = ("normal", "multiply", "screen", "overlay", "darken", "lighten")
_MASK_TYPES = ("none", "rect", "rectangle", "ellipse", "polygon", "star", "heart")


def _clamp_float(low: float, high: float) -> Callable[[Any], float]:
    def _fn(value: Any) -> float:
        return max(low, min(high, float(value)))
    return _fn


def _enum(options: Iterable[str], field_name: str) -> Callable[[Any], str]:
    allowed = tuple(options)

    def _fn(value: Any) -> str:
        text = str(value).strip().lower()
        if text not in allowed:
            raise ValueError(f"{field_name} 取值必须是 {allowed} 之一，收到 {value!r}")
        return text
    return _fn


def _hex_color(value: Any) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", text):
        raise ValueError(f"颜色必须是 #RGB/#RRGGBB 形式，收到 {value!r}")
    return text.upper()


# ── 字段规格（唯一事实来源） ──────────────────────────────
#: `field → (期望类型, 归一化/钳制函数)`。`requirements_service._ADJUST_FIELDS` 直接引用本表，
#: 避免"两套白名单"（改一处漏一处 → 静默不一致）。
FIELD_SPECS: dict[str, tuple[type, Callable[[Any], Any]]] = {
    "speed": (float, _clamp_float(0.25, 4.0)),
    "volume": (float, _clamp_float(0.0, 1.0)),
    "opacity": (float, _clamp_float(0.0, 1.0)),
    "duration_sec": (float, _clamp_float(0.1, 3600.0)),
    "start_sec": (float, _clamp_float(0.0, 36000.0)),
    "source_offset_sec": (float, _clamp_float(0.0, 36000.0)),
    "font_size": (float, _clamp_float(8.0, 200.0)),
    "font_color": (str, _hex_color),
    "text": (str, lambda v: str(v)),
    # 既有时间线字段（此前 NL 改不到，但确实可改）
    "text_align": (str, _enum(_TEXT_ALIGN, "text_align")),
    "font_weight": (str, _enum(("normal", "bold"), "font_weight")),
    "letter_spacing": (float, _clamp_float(-10.0, 50.0)),
    "image_fit": (str, _enum(_IMAGE_FIT, "image_fit")),
    "blend_mode": (str, _enum(_BLEND_MODES, "blend_mode")),
    "mask_type": (str, _enum(_MASK_TYPES, "mask_type")),
    "mask_feather": (float, _clamp_float(0.0, 50.0)),
    "mask_rotation": (float, _clamp_float(-360.0, 360.0)),
    "transition_duration_sec": (float, _clamp_float(0.0, 30.0)),
    "audio_fade_in_sec": (float, _clamp_float(0.0, 30.0)),
    "audio_fade_out_sec": (float, _clamp_float(0.0, 30.0)),
    # 调色/滤镜（R10 之后的实测补缺）：`Clip` 上本来就有这些字段、渲染端也支持，
    # 但**不在白名单里 → NL 根本改不到**。"把画面调亮一点"是最常见的剪辑诉求之一，
    # 实测（生成人工评分表时）它直接落进"解析不出可执行的操作"。
    # 值域与 `schema/timeline.py` 的字段约束一致（1.0 = 原样）。
    "fx_brightness": (float, _clamp_float(0.0, 2.0)),
    "fx_contrast": (float, _clamp_float(0.0, 2.0)),
    "fx_saturation": (float, _clamp_float(0.0, 2.0)),
    "fx_blur": (float, _clamp_float(0.0, 10.0)),
    "fx_hue": (float, _clamp_float(0.0, 360.0)),
}
#: 兼容旧名（`requirements_service` 与既有测试引用 `_ADJUST_FIELDS`）
_ADJUST_FIELDS = FIELD_SPECS

#: target 允许出现的键
TARGET_KEYS: tuple[str, ...] = ("all", "selected", "time_range", "query", "filter", "ref", "index")

#: op 规格：`op → {"requires": [...], "optional": [...], "desc": "..."}`
OP_SPECS: dict[str, dict[str, Any]] = {
    "keep": {"requires": ["target"], "optional": ["limit"],
             "desc": "只保留匹配的片段（其余删除）"},
    "remove": {"requires": ["target"], "optional": [],
               "desc": "删除匹配的片段"},
    "reorder": {"requires": [], "optional": ["target", "by", "desc"],
                "desc": "按依据重排片段（默认按时间）"},
    "trim": {"requires": ["target"], "optional": ["duration_sec", "start_offset_sec", "end_offset_sec"],
             "desc": "裁剪片段时长/头尾"},
    "set": {"requires": ["target", "field"], "optional": ["value"],
            "desc": "设置片段字段（走 FIELD_SPECS 白名单与值域）"},
    "add": {"requires": ["kind"], "optional": ["target", "position", "value", "field", "text"],
            "desc": "新增片段（字幕/动画/音频…）"},
    "transition": {"requires": ["target"], "optional": ["transition", "duration_sec"],
                   "desc": "设置片段转场"},
}


# ── 数据模型 ──────────────────────────────────────────────


class IntentTarget(BaseModel):
    """操作对象（多种寻址方式，至少给一个）。"""
    all: bool = Field(default=False, description="整条时间线")
    selected: bool = Field(default=False, description="用户当前选中的片段")
    time_range: Optional[tuple[float, float]] = Field(default=None, description="时间区间 [t0,t1]")
    query: str = Field(default="", description="自然语言内容条件（走内容检索）")
    filter: dict[str, Any] = Field(default_factory=dict, description="结构化内容条件（走索引筛选）")
    ref: list[str] = Field(default_factory=list, description="显式引用（clip id / shot ref）")
    index: list[int] = Field(default_factory=list, description="第 N 个片段（0 起）")

    def is_empty(self) -> bool:
        return not (self.all or self.selected or self.time_range or self.query.strip()
                    or self.filter or self.ref or self.index)

    def describe(self) -> str:
        if self.all:
            return "整条时间线"
        if self.selected:
            return "选中的片段"
        if self.time_range:
            return f"{self.time_range[0]:.1f}-{self.time_range[1]:.1f}s"
        if self.ref:
            return "指定片段 " + "/".join(self.ref[:3])
        if self.index:
            return "第 " + "、".join(str(i) for i in self.index[:3]) + " 个片段"
        if self.filter:
            return "满足条件 " + "、".join(f"{k}={v}" for k, v in self.filter.items())
        if self.query:
            return f"内容匹配“{self.query}”的片段"
        return "（未指定）"


class IntentOp(BaseModel):
    op: str
    target: IntentTarget = Field(default_factory=IntentTarget)
    field: str = ""
    value: Any = None
    limit: Optional[int] = None
    by: str = ""
    desc: bool = True
    kind: str = ""
    position: str = ""
    transition: str = ""
    duration_sec: Optional[float] = None
    start_offset_sec: Optional[float] = None
    end_offset_sec: Optional[float] = None
    text: str = ""


class IntentConstraints(BaseModel):
    total_duration_sec: Optional[float] = None
    platform: str = ""
    aspect_ratio: str = ""
    no_reuse: bool = False
    keep_source_order: bool = False


class IntentProblem(BaseModel):
    """一条校验问题：`error` 会阻止执行，`warning` 只提示。"""
    level: str = "error"          # error | warning
    where: str = ""               # 例如 "ops[1].field"
    message: str = ""


class EditIntent(BaseModel):
    goal: str = ""
    ops: list[IntentOp] = Field(default_factory=list)
    constraints: IntentConstraints = Field(default_factory=IntentConstraints)
    unresolved: list[str] = Field(default_factory=list,
                                  description="没能解析成操作的部分（必须反问，不许猜）")
    notes: list[str] = Field(default_factory=list)
    source: str = Field(default="", description="解析来源：llm / rules / mixed")


# ── 校验 ──────────────────────────────────────────────────


def validate_intent(
    intent: EditIntent,
    *,
    clip_count: int = 0,
    max_ops: int = 20,
) -> tuple[EditIntent, list[IntentProblem]]:
    """校验并归一化意图：未知 op/字段/取值一律报错，超范围的值钳制并告警。

    **不修改调用方的对象**：返回归一化后的副本，问题列表交给上层决定怎么呈现。
    """
    problems: list[IntentProblem] = []
    if len(intent.ops) > max_ops:
        problems.append(IntentProblem(where="ops", message=f"操作数 {len(intent.ops)} 超过上限 {max_ops}"))

    norm_ops: list[IntentOp] = []
    for i, op in enumerate(intent.ops):
        where = f"ops[{i}]"
        spec = OP_SPECS.get(op.op)
        if spec is None:
            # 幻觉 op：明确拒绝并给出可选清单（比"静默忽略"有用得多）
            problems.append(IntentProblem(
                where=f"{where}.op",
                message=f"不支持的操作 {op.op!r}；可用：{'/'.join(OP_SPECS)}"))
            continue
        data = op.model_dump()
        for key in ("target",):
            if key in spec["requires"] and (op.target is None or op.target.is_empty()):
                problems.append(IntentProblem(
                    where=f"{where}.target",
                    message="该操作必须指定对象（整条时间线/选中/时间区间/内容条件/指定片段）"))
        if op.op in ("keep", "remove", "trim", "transition") and op.target.is_empty():
            continue  # 已经记过问题，跳过后续检查
        if op.target.time_range is not None:
            t0, t1 = op.target.time_range
            if len(op.target.time_range) != 2 or t1 <= t0:
                problems.append(IntentProblem(
                    where=f"{where}.target.time_range",
                    message=f"时间区间非法：{[t0, t1]}（需 t1 > t0）"))
                continue
        if op.op == "reorder":
            by = (op.by or "time").strip().lower()
            if by not in REORDER_KEYS:
                problems.append(IntentProblem(
                    where=f"{where}.by",
                    message=f"重排依据 {op.by!r} 不支持；可用：{'/'.join(REORDER_KEYS)}"))
                by = "time"
            data["by"] = by
        if op.op == "set":
            field = (op.field or "").strip()
            spec_field = FIELD_SPECS.get(field)
            if spec_field is None:
                problems.append(IntentProblem(
                    where=f"{where}.field",
                    message=f"字段 {field!r} 不可通过指令修改；可用：{'/'.join(sorted(FIELD_SPECS))}"))
                continue
            _kind, normalizer = spec_field
            try:
                data["value"] = normalizer(op.value)
            except (TypeError, ValueError) as exc:
                problems.append(IntentProblem(
                    where=f"{where}.value", message=f"值不合法：{exc}"))
                continue
            if isinstance(op.value, (int, float)) and float(data["value"]) != float(op.value):
                problems.append(IntentProblem(
                    level="warning", where=f"{where}.value",
                    message=f"值已钳制：{op.value} → {data['value']}"))
        if op.op == "add":
            kind = (op.kind or "").strip().lower()
            if kind not in ADD_KINDS:
                problems.append(IntentProblem(
                    where=f"{where}.kind",
                    message=f"新增类型 {op.kind!r} 不支持；可用：{'/'.join(ADD_KINDS)}"))
                continue
            data["kind"] = kind
        if op.limit is not None:
            try:
                data["limit"] = max(1, int(op.limit))
            except (TypeError, ValueError):
                problems.append(IntentProblem(level="warning", where=f"{where}.limit",
                                              message="limit 非整数，已忽略"))
                data["limit"] = None
        norm_ops.append(IntentOp(**data))

    cons = intent.constraints.model_copy(deep=True)
    if cons.total_duration_sec is not None:
        try:
            value = float(cons.total_duration_sec)
        except (TypeError, ValueError):
            problems.append(IntentProblem(where="constraints.total_duration_sec", message="总时长非数值"))
            cons.total_duration_sec = None
        else:
            if value <= 0:
                problems.append(IntentProblem(where="constraints.total_duration_sec",
                                              message="总时长必须为正"))
                cons.total_duration_sec = None
            elif value < 3:
                problems.append(IntentProblem(level="warning", where="constraints.total_duration_sec",
                                              message=f"总时长 {value}s 过短，已放宽到 3s"))
                cons.total_duration_sec = 3.0
            else:
                cons.total_duration_sec = value
    if cons.platform:
        norm = str(cons.platform).strip()
        if norm in ("横屏", "横版", "16:9"):
            cons.platform = "landscape"
        elif norm in ("竖屏", "竖版", "9:16"):
            cons.platform = "vertical"
        elif norm in ("方形", "1:1"):
            cons.platform = "square"
        elif norm not in ("landscape", "vertical", "square"):
            problems.append(IntentProblem(where="constraints.platform",
                                          message=f"未知画幅 {norm!r}"))
            cons.platform = ""

    if clip_count and any(o.op in ("keep", "remove", "reorder", "trim") for o in norm_ops) \
            and clip_count == 1:
        problems.append(IntentProblem(level="warning", where="ops",
                                      message="时间线只有一个片段，结构类操作效果有限"))

    norm = intent.model_copy(deep=True)
    norm.ops = norm_ops
    norm.constraints = cons
    return norm, problems


def has_errors(problems: Iterable[IntentProblem]) -> bool:
    return any(p.level == "error" for p in problems)


# ── LLM schema 与提示 ─────────────────────────────────────


def intent_json_schema() -> dict[str, Any]:
    """给 `llm.structured_output` 用的 JSON schema（把 op/字段约束说给模型）。"""
    target_props = {
        "all": {"type": "boolean"},
        "selected": {"type": "boolean"},
        "time_range": {"type": "array", "items": {"type": "number"}},
        "query": {"type": "string"},
        "filter": {"type": "object"},
        "ref": {"type": "array", "items": {"type": "string"}},
        "index": {"type": "array", "items": {"type": "integer"}},
    }
    op_props = {
        "op": {"type": "string", "enum": list(OP_SPECS)},
        "target": {"type": "object", "properties": target_props},
        "field": {"type": "string", "enum": sorted(FIELD_SPECS)},
        "value": {},
        "limit": {"type": "integer"},
        "by": {"type": "string", "enum": list(REORDER_KEYS)},
        "desc": {"type": "boolean"},
        "kind": {"type": "string", "enum": list(ADD_KINDS)},
        "position": {"type": "string"},
        "transition": {"type": "string"},
        "duration_sec": {"type": "number"},
        "start_offset_sec": {"type": "number"},
        "end_offset_sec": {"type": "number"},
        "text": {"type": "string"},
    }
    return {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "ops": {"type": "array", "items": {
                "type": "object", "properties": op_props, "required": ["op"]}},
            "constraints": {
                "type": "object",
                "properties": {
                    "total_duration_sec": {"type": "number"},
                    "platform": {"type": "string", "enum": ["landscape", "vertical", "square"]},
                    "aspect_ratio": {"type": "string"},
                    "no_reuse": {"type": "boolean"},
                    "keep_source_order": {"type": "boolean"},
                },
            },
            "unresolved": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ops"],
    }


INTENT_SYSTEM_PROMPT = (
    "你是视频剪辑指令解析器。把用户的中文指令解析成结构化剪辑意图，只输出符合 schema 的 JSON。\n"
    "可用操作（只能用这些）：\n"
    "- keep：只保留匹配片段（需 target）；remove：删除匹配片段（需 target）；\n"
    "- reorder：重排（by=duration/brightness/motion/sharpness/score/time，desc 是否降序）；\n"
    "- trim：裁剪（duration_sec / start_offset_sec / end_offset_sec）；\n"
    "- set：改字段（field 必须在允许列表内，value 为其新值）；\n"
    "- add：新增片段（kind=text/caption/animation/audio/image/shape）；\n"
    "- transition：设置转场（transition 名称 + duration_sec）。\n"
    "target 用这些键之一或多个：all（整条）/selected（用户选中）/time_range（[t0,t1]）/"
    "query（自然语言内容条件，如“有人脸的镜头”）/filter（结构化条件，如 {\"is_black\":true}）/"
    "ref（片段 id）/index（第几个）。\n"
    f"set 可用字段：{', '.join(sorted(FIELD_SPECS))}。\n"
    "约束放 constraints：total_duration_sec（总时长）、platform（landscape/vertical/square）、"
    "no_reuse（不要重复用同一素材）。\n"
    "**解析不了的部分放进 unresolved**（不要猜、不要编造操作）；若指令不涉及剪辑操作，ops 留空。"
)

#: 几条 Few-shot（与基准夹具同源，保证"文档里写的说法"真能解析）
EXAMPLES: tuple[dict[str, Any], ...] = (
    {"user": "剪到 60 秒", "intent": {"ops": [], "constraints": {"total_duration_sec": 60}}},
    {"user": "去掉黑场", "intent": {"ops": [{"op": "remove",
                                            "target": {"filter": {"is_black": True}}}]}},
    {"user": "只保留有人脸的镜头", "intent": {"ops": [{"op": "keep",
                                                     "target": {"query": "有人脸的镜头"}}]}},
    {"user": "把这些片段按运动量从大到小排", "intent": {"ops": [{"op": "reorder", "by": "motion"}]}},
    {"user": "把选中的速度调到 1.5 倍", "intent": {"ops": [{"op": "set", "target": {"selected": True},
                                                          "field": "speed", "value": 1.5}]}},
    {"user": "给开头加个字幕", "intent": {"ops": [{"op": "add", "kind": "caption",
                                                 "target": {"index": [0]}}]}},
    {"user": "换成竖屏", "intent": {"ops": [], "constraints": {"platform": "vertical"}}},
)


# ── 确定性规则解析（无 LLM 也能用） ────────────────────────

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000}


def parse_cn_number(text: str) -> Optional[float]:
    """中文数字 → 数值（支持"十/十五/二十三/一百二/两"等常规写法，小数用阿拉伯数字）。"""
    text = (text or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    if not all(ch in _CN_DIGITS for ch in text):
        return None
    if "十" not in text and "百" not in text and "千" not in text:
        return float(sum(_CN_DIGITS[ch] for ch in text)) if len(text) <= 2 else None
    total, section, last = 0, 0, 0
    for ch in text:
        val = _CN_DIGITS[ch]
        if val >= 10:
            section = (section or 1) * val
            total += section
            section = 0
            last = 0
        else:
            section += val
            last = val
    return float(total + section)


_NUM = r"(\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+)"


def _num(match: Optional[re.Match[str]], group: int = 1) -> Optional[float]:
    if not match:
        return None
    return parse_cn_number(match.group(group))


#: 指代词（"刚才那段""它们"）——它们是**指代**而不是内容条件，必须交给会话状态解析，
#: 否则会被当成关键词去和素材文本比，结果必然是空匹配（实测踩过）。
DEICTIC_RECENT_WORDS: tuple[str, ...] = ("刚才", "刚刚", "上一个", "那段", "这一段", "那个片段")
DEICTIC_PLURAL_WORDS: tuple[str, ...] = ("它们", "这些", "那些", "这几个", "这几个片段")


#: 显式片段 id 的形态（`c3` / `clip_07` / `mg_ab12`…）——用户会从界面复制 id 来说话。
#: 刻意**不**把普通英文词当 id（否则 "iphone" 会被当引用）。
_ID_LIKE_RE = re.compile(r"^(?:clip|mg|anim|shape|cap|txt|aud|vid|asset)_?[\w\-]{1,40}$|^c\d{1,3}$",
                         re.IGNORECASE)
#: 时间区间说法："0 到 3.5 秒" / "第 5 秒到第 8 秒" / "5-8s"
_RANGE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(rf"{_NUM}\s*(?:到|至|-|~)\s*{_NUM}\s*秒"),
    re.compile(rf"第\s*{_NUM}\s*秒\s*(?:到|至|-|~)\s*第?\s*{_NUM}\s*秒"),
    re.compile(rf"{_NUM}\s*(?:到|至|-|~)\s*{_NUM}\s*s", re.IGNORECASE),
)


def parse_time_range(text: str) -> Optional[tuple[float, float]]:
    """解析"0 到 3.5 秒"这类时间区间（解析不出返回 None）。"""
    for pattern in _RANGE_RES:
        m = pattern.search(text or "")
        if not m:
            continue
        nums = [parse_cn_number(g) for g in m.groups() if g]
        nums = [n for n in nums if n is not None]
        if len(nums) >= 2:
            lo, hi = float(nums[0]), float(nums[1])
            return (lo, hi) if hi > lo else (hi, lo)
    return None


def is_deictic_phrase(text: str) -> bool:
    """是否是指代说法（而非内容描述）。"""
    t = (text or "").strip()
    if not t:
        return False
    if re.fullmatch(r"第\s*\d+\s*个?", t):
        return True
    return any(w in t for w in DEICTIC_RECENT_WORDS + DEICTIC_PLURAL_WORDS)


def _target_from_phrase(phrase: str) -> IntentTarget:
    """内容条件短语 → target：**复用 R4 的路由**，让"去掉黑场"和"找黑场"共享词汇。"""
    phrase = (phrase or "").strip(" 的，。,.!！?？")
    if not phrase:
        return IntentTarget(all=True)
    rng = parse_time_range(phrase)
    if rng:
        return IntentTarget(time_range=rng)
    if _ID_LIKE_RE.fullmatch(phrase):
        return IntentTarget(ref=[phrase])
    ordinal = re.fullmatch(
        r"第\s*([0-9〇零一二三四五六七八九十百千两]+)\s*(?:个|段|条|支|块)?\s*"
        r"(?:片段|镜头|视频|素材|clip)?", phrase, re.I)
    if ordinal:
        # "第 N 个"按**时间线上的位置**理解（可见、可预期），而不是"最近提到的那批里的第 N 个"
        # ——后者要依赖会话状态，用户看不到自己指的是什么（实测这条歧义最容易出错）
        # 中文数字与阿拉伯数字都认（"第三个片段" / "第3个片段"）
        num = parse_cn_number(ordinal.group(1))
        if num:
            return IntentTarget(index=[max(1, int(num)) - 1])
    if is_deictic_phrase(phrase):
        # 指代 → "选中/会话所指"；由 intent_resolver 用会话状态或区域填充
        return IntentTarget(selected=True)
    try:
        from clipwright.services.content_query import route_query

        routed = route_query(phrase)
        filters = routed.get("filters") or {}
        clean = (routed.get("clean_query") or "").strip()
        if filters and not clean:
            return IntentTarget(filter=filters)
        if filters and clean:
            return IntentTarget(filter=filters, query=clean)
        if clean:
            return IntentTarget(query=clean)
    except Exception:
        pass
    return IntentTarget(query=phrase)


#: 显式全量意图（"所有片段/全部/整条时间线"）
_GLOBAL_SCOPE_RE = re.compile(
    r"(所有片段|全部片段|所有的片段|所有镜头|所有素材|整条时间线|整段时间线|整条片|"
    r"全部|所有|每(?:一)?个片段|每个镜头)"
)


def _scope_target(text: str) -> IntentTarget:
    """整体 vs 选中：文字里明说"所有/全部/整条"就是整条时间线，否则按用户选中理解。

    为什么必须区分：`set`/`trim` 这类无内容条件的操作原本一律落到"选中的片段"，
    于是"把**所有**片段速度调成 1.5 倍"会在没选中的会话里直接落空
    （实测就是这样——R7 的"一句话→成片"跑通验收暴露的）。
    中文里"全部/所有"是显式全量意图，与"这段/那几段"（指代）必须分开处理。
    """
    if _GLOBAL_SCOPE_RE.search(text or ""):
        return IntentTarget(all=True)
    return IntentTarget(selected=True)


def parse_intent_rules(message: str) -> EditIntent:
    """确定性规则解析：覆盖常见说法，解析不出的部分进 `unresolved`（不猜）。

    与 LLM 路径共享同一套 op/字段/约束模型，因此校验与执行只有一条通路。
    """
    text = (message or "").strip()
    intent = EditIntent(goal=text, source="rules")
    if not text:
        return intent
    unresolved: list[str] = []
    ops: list[IntentOp] = []
    cons = intent.constraints

    # ── 约束：总时长 / 画幅 / 不要重复素材 ──
    # 允许"总时长**要**90 秒"这类插入词（中文口语里动词/系词几乎必现）
    m = re.search(rf"(?:剪到|剪成|控制在|总时长|时长|长度)\s*(?:要|是|为|大约|约|不超过|控制在)?\s*"
                  rf"{_NUM}\s*秒", text)
    if m:
        cons.total_duration_sec = _num(m)
        text = text.replace(m.group(0), " ")
    m = re.search(r"(竖屏|竖版|横屏|横版|方形|9:16|16:9|1:1)", text)
    if m:
        cons.platform = m.group(1)
        text = text.replace(m.group(1), " ")
    if re.search(r"(别重复用|不要重复用|不重复使用|同一素材只用一次)", text):
        cons.no_reuse = True
        text = re.sub(r"(别重复用|不要重复用|不重复使用|同一素材只用一次)", " ", text)

    # ── set speed ──
    m = re.search(rf"(?:速度|倍速|rate)[^0-9]{{0,6}}{_NUM}\s*(?:倍|x|X)?", text)
    if not m:
        m = re.search(rf"{_NUM}\s*(?:倍速|倍|x|X)", text)
    if m:
        value = _num(m)
        if value:
            ops.append(IntentOp(op="set", target=_scope_target(text),
                                field="speed", value=value))
            text = text.replace(m.group(0), " ")
    # ── set volume ──
    if re.search(r"(音量|声音)", text) and re.search(r"(调大|大一点|提高|调高|调小|小一点|降低|调低)", text):
        bigger = bool(re.search(r"(调大|大一点|提高|调高)", text))
        ops.append(IntentOp(op="set", target=_scope_target(text),
                            field="volume", value=0.9 if bigger else 0.4))
        text = re.sub(r"(音量|声音|调大|大一点|提高|调高|调小|小一点|降低|调低)", " ", text)

    # ── 调色/滤镜（fx_*）──
    # 中文口语几乎不说"亮度参数"，说的是"调亮一点/暗一点/鲜艳点/去色/糊一点"。
    # 值语义是**绝对倍率**（1.0 = 原样），"一点"约 ×1.15、不加程度词约 ×1.3、明显 ×1.6，
    # 这些系数是经验值、需要在真实素材上标定；刻意不做"相对当前值"的运算，
    # 因为补丁是幂等的（同一条指令跑两次结果一致），相对语义会让重跑漂移。
    _fx_prefix = r"(?:把|将)?(?:整个|整体|全部|所有)?(?:画面|视频|镜头|素材|片子|颜色|色调|画质)?"
    for pattern, field, value, label in (
        # 程度强的说法放前面：`亮(?:一点|一些|点)?` 里的程度词是可选的，
        # 会先吃掉"亮很多"里的"亮"，导致强弱两档拿到同一个值（实测踩到）。
        (r"亮很多|亮不少|大幅(?:调亮|提亮)|明显(?:调亮|提亮)", "fx_brightness", 1.6, "大幅调亮"),
        (rf"{_fx_prefix}(?:调|变|弄|弄的)?亮(?:一点|一些|点)?|(?:把|将)?{_fx_prefix}(?:提亮|调亮|加亮)", "fx_brightness", 1.15, "调亮"),
        (rf"{_fx_prefix}(?:调|变|弄)?暗(?:一点|一些|点)?|(?:把|将)?{_fx_prefix}(?:压暗|调暗)", "fx_brightness", 0.85, "调暗"),
        (r"(?:加个|加|提高|增强|增加)?(?:一点|些)?对比(?:度)?(?:高一点|强一点|一点|一些)?", "fx_contrast", 1.25, "加对比度"),
        (r"(?:降低|减|去掉|减少)(?:一点|些)?(?:的)?对比(?:度)?", "fx_contrast", 0.8, "降对比度"),
        (rf"{_fx_prefix}(?:鲜艳|饱和)(?:一点|一些|点)?|(?:加|提高|增强)(?:一点|些)?饱和(?:度)?", "fx_saturation", 1.25, "加饱和度"),
        (r"(?:做成|变成|改成)?(?:去色|黑白|褪色)|(?:降低|减)(?:一点|些)?饱和(?:度)?", "fx_saturation", 0.0, "去色"),
        (rf"{_fx_prefix}(?:模糊|虚化|糊)(?:一点|一些|点)", "fx_blur", 3.0, "加模糊"),
        (rf"{_fx_prefix}(?:画质)?(?:锐化|清晰一点|锐一点|清楚一点)", "fx_blur", 0.0, "去模糊"),
    ):
        m = re.search(pattern, text)
        if not m:
            continue
        ops.append(IntentOp(op="set", target=_scope_target(text), field=field, value=value))
        text = text.replace(m.group(0), " ")
        break       # 一次只说一个画面调整；多个说法（"又亮又艳"）留给 LLM 路径

    # ── remove / keep（内容条件）──
    # 中文里动词可以在前（"去掉黑场"）也可以在后（"把掌声那几段删掉"）；
    # 只支持前者会漏掉一大半真实说法（实测基准里就有）。
    m = re.search(r"(?:去掉|删除|删掉|移除|不要|别用|剔除)\s*(.+)", text)
    if m:
        phrase = re.split(r"[，。；;]|然后|再", m.group(1))[0]
        ops.append(IntentOp(op="remove", target=_target_from_phrase(phrase)))
        text = text.replace(m.group(0), " ")
    else:
        m = re.search(r"(?:把|将)\s*(.+?)\s*(?:那几段|那一段|那一段段|这几段|几段|的部分)?\s*"
                      r"(?:删掉|删除|去掉|移除|剔除|不要了|不要)", text)
        if m:
            ops.append(IntentOp(op="remove", target=_target_from_phrase(m.group(1))))
            text = text.replace(m.group(0), " ")
    m = re.search(r"(?:只保留|只要|留下|只留下|仅保留)\s*(.+)", text)
    if m:
        phrase = re.split(r"[，。；;]|然后|再", m.group(1))[0]
        ops.append(IntentOp(op="keep", target=_target_from_phrase(phrase)))
        text = text.replace(m.group(0), " ")

    # ── reorder ──
    m = re.search(r"(?:按|按照)\s*([^，。；;]{1,12}?)\s*(?:从大到小|降序|由高到低)?\s*"
                  r"(?:重排|排序|排列|排一下|排)", text)
    if m:
        by_raw = m.group(1)
        by = "time"
        if re.search(r"(时长|长度|时间长的|最长)", by_raw):
            by = "duration"
        elif re.search(r"(亮|曝光)", by_raw):
            by = "brightness"
        elif re.search(r"(运动|动感|动作)", by_raw):
            by = "motion"
        elif re.search(r"(清晰|锐)", by_raw):
            by = "sharpness"
        elif re.search(r"(相关|匹配|分)", by_raw):
            by = "score"
        desc = not bool(re.search(r"(从小到大|升序|由低到高)", text))
        ops.append(IntentOp(op="reorder", by=by, desc=desc))
        text = text.replace(m.group(0), " ")

    # ── add ──
    # 允许量词插在动词与名词之间（"加**个**字幕"），且允许"给开头加…"这类前置状语
    m = re.search(r"(?:加|添加|来点|来个|配上|放上|插入)\s*(?:个|一条|一段|一个|一些|点)?\s*"
                  r"(字幕|标题|文字|动画|特效|音效|背景音乐|音乐|图片|贴纸)", text)
    if m:
        kind_map = {"字幕": "caption", "标题": "text", "文字": "text", "动画": "animation",
                    "特效": "animation", "音效": "audio", "音乐": "audio",
                    "背景音乐": "audio", "图片": "image", "贴纸": "image"}
        op = IntentOp(op="add", kind=kind_map[m.group(1)])
        if re.search(r"(开头|开头处|片头|最前面|片首)", text):
            op.target = IntentTarget(index=[0])
            op.position = "start"
        elif re.search(r"(结尾|片尾|最后|末尾)", text):
            op.position = "end"
        ops.append(op)
        text = text.replace(m.group(0), " ")

    # ── transition ──
    m = re.search(r"(?:转场|过渡)(?:都)?(?:改成|换成|用|设为|设为)?\s*([\u4e00-\u9fff]{2,6})?", text)
    if m:
        name = (m.group(1) or "").strip()
        name = name if name and name not in ("改成", "换成") else ""
        ops.append(IntentOp(op="transition", target=IntentTarget(all=True),
                            transition=name or "fade"))
        text = text.replace(m.group(0), " ")

    # ── trim ──
    m = re.search(rf"(?:裁到|裁成|缩短到|truncate 到)\s*{_NUM}\s*秒", text)
    if m:
        value = _num(m)
        if value:
            ops.append(IntentOp(op="trim", target=_scope_target(text),
                                duration_sec=value))
            text = text.replace(m.group(0), " ")

    leftover = re.sub(r"[\s，。,.、;；!！?？]+", "", text)
    if leftover and not ops and cons == IntentConstraints():
        # 完全没解析出东西：把原话交给上层反问（R6），而不是静默当"无需改动"
        unresolved.append(message.strip())
    elif leftover:
        unresolved.append(leftover)
    intent.ops = ops
    intent.constraints = cons
    intent.unresolved = unresolved
    return intent


def describe_intent(intent: EditIntent) -> str:
    """人话总结（R6 的"意图确认卡"直接用它）。"""
    lines: list[str] = []
    if intent.goal:
        lines.append(f"目标：{intent.goal}")
    for op in intent.ops:
        spec = OP_SPECS.get(op.op, {})
        head = f"- {spec.get('desc', op.op)}：{op.target.describe()}"
        extra: list[str] = []
        if op.op == "set":
            extra.append(f"{op.field} → {op.value}")
        if op.op == "reorder":
            extra.append(f"依据 {op.by}{'（降序）' if op.desc else '（升序）'}")
        if op.op == "add":
            extra.append(f"类型 {op.kind}" + (f"，位置 {op.position}" if op.position else ""))
        if op.op == "transition":
            extra.append(f"转场 {op.transition}" + (f" {op.duration_sec}s" if op.duration_sec else ""))
        if op.op == "trim" and op.duration_sec:
            extra.append(f"裁到 {op.duration_sec}s")
        if op.limit:
            extra.append(f"最多 {op.limit} 个")
        lines.append(head + ("（" + "；".join(extra) + "）" if extra else ""))
    c = intent.constraints
    cons_bits = []
    if c.total_duration_sec:
        cons_bits.append(f"总时长 {c.total_duration_sec:g}s")
    if c.platform:
        cons_bits.append({"landscape": "横屏", "vertical": "竖屏", "square": "方形"}.get(c.platform, c.platform))
    if c.no_reuse:
        cons_bits.append("不重复使用素材")
    if cons_bits:
        lines.append("约束：" + "、".join(cons_bits))
    if intent.unresolved:
        lines.append("没理解的部分（需要确认）：" + "；".join(intent.unresolved[:3]))
    return "\n".join(lines) if lines else "（空意图）"


async def parse_intent_llm(message: str, *, llm: Any, context_hint: str = "",
                           pipeline_id: str = "") -> EditIntent:
    """LLM 路径：受 schema 约束的结构化输出 → `EditIntent`（失败返回空意图，不抛异常）。"""
    if llm is None or not message.strip():
        return EditIntent(goal=message, source="llm")
    try:
        resp = await llm.structured_output(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_prompt=(f"已有素材/时间线上下文：{context_hint}\n" if context_hint else "")
                        + f"用户指令：{message}",
            output_schema=intent_json_schema(),
            temperature=0,
            max_tokens=1024,
            pipeline_id=pipeline_id,
            use_flash=True,
        )
    except Exception as exc:  # LLM 不可用不该让指令消失
        from clipwright.config import logger

        logger.info("意图解析 LLM 不可用（改用规则解析）：%s", exc)
        return EditIntent(goal=message, source="llm-failed")
    if not isinstance(resp, dict):
        return EditIntent(goal=message, source="llm-invalid")
    data = dict(resp)
    data.setdefault("goal", message)
    data["source"] = "llm"
    try:
        return EditIntent.model_validate(data)
    except Exception:
        return EditIntent(goal=message, source="llm-invalid")


async def parse_intent(
    message: str,
    *,
    llm: Any = None,
    prefer: str = "rules",
    context_hint: str = "",
    pipeline_id: str = "",
) -> tuple[EditIntent, list[IntentProblem]]:
    """统一入口：按 `prefer` 选解析器，随后**一律过校验**。

    `prefer="rules"`（默认）先用确定性规则；规则解析不出任何操作且给了 LLM 时才回退 LLM——
    这样常见指令走零成本可回归的路径，长尾才花 token。
    """
    intent = parse_intent_rules(message)
    if prefer == "llm" or (not intent.ops and not intent.constraints.model_dump(exclude_defaults=True)
                           and llm is not None):
        llm_intent = await parse_intent_llm(message, llm=llm, context_hint=context_hint,
                                           pipeline_id=pipeline_id)
        if llm_intent.ops or llm_intent.constraints.model_dump(exclude_defaults=True):
            intent = llm_intent
        elif llm is not None:
            intent.source = "rules"
    norm, problems = validate_intent(intent)
    return norm, problems
