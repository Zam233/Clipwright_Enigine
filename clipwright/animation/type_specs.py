"""动画类型规格（AnimationTypeSpec）与注册表。

背景（用户 2026-10-29）：成片里 86.5% 的逻辑动画是 `diagram_svg.py` 的**静态 SVG**
（6s 片段只有 2 个不同帧、内容占屏中位数 7.1%）。改造方向不是「统一路由到
mg_dynamic」，而是**每种类型一份专属提示词**：LLM 依据「视频类型（category plugin）
+ persona 风格 + 该类型的意图/构图/动效约束 + 本段内容」生成 `mg_def`，再由
Hyperframes 渲染成透明 MOV。

本模块只负责「类型的元信息 + 提示词正文的装配入口」：
- 元信息（id/name/intent/fallback）在 Python 里，便于注册表校验与静态检查；
- 提示词正文在 `clipwright/animation/prompts/types/<id>.md`，便于审阅 diff 与迭代；
- 版本号取正文 SHA-256 前 12 位，自动参与生成缓存键（改了提示词就自然失效）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from clipwright.config import logger

#: 提示词目录（Markdown，一类型一文件）
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts" / "types"

#: 提示词正文的标准分段（loader 只认这些标题）
PROMPT_SECTIONS: tuple[str, ...] = (
    "Intent",
    "Semantic constraints",
    "Composition",
    "Motion",
    "Anti-patterns",
    "Example",
)


@dataclass(frozen=True)
class AnimationTypeSpec:
    """一种逻辑动画类型的规格。

    只承载「这类动画该怎么被生成」的知识；不含任何渲染实现。
    """

    id: str
    name: str
    intent: str
    #: 语义约束（必须表达什么、禁止编造什么）
    semantic_constraints: tuple[str, ...] = ()
    #: 构图策略（元素与层级）
    composition: str = ""
    #: 动效策略（入场/持续/退场，easing 倾向）
    motion: str = ""
    #: 典型劣化（生成后要重点检查）
    anti_patterns: tuple[str, ...] = ()
    #: 失败回退：`diagram_svg` 的 preset id（空 = 无 SVG 回退）
    fallback_preset: str = ""
    #: 失败回退：`mg/templates` 的内置模板 id（空 = 无模板回退）
    fallback_template: str = ""
    #: 提示词正文版本（loader 写入，SHA-256 前 12 位）
    prompt_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "intent": self.intent,
            "semantic_constraints": list(self.semantic_constraints),
            "composition": self.composition,
            "motion": self.motion,
            "anti_patterns": list(self.anti_patterns),
            "fallback_preset": self.fallback_preset,
            "fallback_template": self.fallback_template,
            "prompt_version": self.prompt_version,
        }


@dataclass(frozen=True)
class CategoryAnimationContext:
    """视频类型（category plugin）里与动画有关的白名单字段。

    category 决定「信息节奏与受众」，比 persona 的优先级高；冲突时它说话。
    """

    plugin_id: str = ""
    tone: str = ""
    density: str = ""
    audience: str = ""
    notes: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.plugin_id or self.tone or self.density or self.audience or self.notes)

    def to_prompt(self) -> str:
        if self.is_empty():
            return ""
        rows = [f"- 视频类型插件：{self.plugin_id or '未指定'}"]
        if self.tone:
            rows.append(f"- 语气/调性：{self.tone}")
        if self.density:
            rows.append(f"- 信息密度：{self.density}")
        if self.audience:
            rows.append(f"- 受众：{self.audience}")
        for note in self.notes:
            rows.append(f"- 类型要求：{note}")
        return "\n".join(rows)


@dataclass(frozen=True)
class PersonaAnimationContext:
    """persona 里与动画有关的白名单字段（决定视觉签名，不决定信息节奏）。"""

    persona_id: str = ""
    palette: tuple[str, ...] = ()
    font: str = ""
    motion_speed: str = ""
    style_description: str = ""
    #: 动画相关字段的指纹（进缓存键；避免无关 persona 字段冲掉缓存）
    fingerprint: str = ""

    def is_empty(self) -> bool:
        return not (self.persona_id or self.palette or self.font or self.motion_speed
                    or self.style_description)

    def to_prompt(self) -> str:
        if self.is_empty():
            return ""
        rows: list[str] = []
        if self.persona_id:
            rows.append(f"- 创作者 persona：{self.persona_id}")
        if self.palette:
            rows.append(f"- 配色（必须遵守）：{'、'.join(self.palette)}")
        if self.font:
            rows.append(f"- 字体倾向：{self.font}")
        if self.motion_speed:
            rows.append(f"- 动效节奏：{self.motion_speed}（slow/normal/fast 之一）")
        if self.style_description:
            rows.append(f"- 风格描述：{self.style_description[:600]}")
        return "\n".join(rows)


def persona_fingerprint(persona: PersonaAnimationContext) -> str:
    """persona 动画字段的稳定指纹（进缓存键，不进提示词）。"""
    if persona.fingerprint:
        return persona.fingerprint
    raw = "|".join([
        persona.persona_id,
        ",".join(persona.palette),
        persona.font,
        persona.motion_speed,
        persona.style_description,
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ── 类型正文（提示词的「元信息」部分；正文在 Markdown 里）───────────────

#: 手写覆盖：核心类型给出更具体的构图/动效/约束。
_OVERRIDES: dict[str, dict[str, Any]] = {
    "comparison": {
        "intent": "把两件事摆在同一屏上做对照，让差异一眼可见（不是各说一段）。",
        "semantic_constraints": (
            "左右两侧必须是同一维度的对照项（同一问题、两种答案）",
            "不得使用输入里没有的数字；没有数字就不要编织数据",
            "两侧字数尽量接近，长度差超过一倍时先压缩措辞而不是缩字号",
        ),
        "composition": "左右两栏 + 中央分隔（VS/→/对照标签）；每侧一条主句 + 至多一个补充词；顶部一行结论式小标题。",
        "motion": "左先右后错峰 0.2–0.4s 入场；分隔线先绘制、再由中心向两侧展开；末段保留 0.8s 静止供阅读。",
        "anti_patterns": ("两侧内容不在同一维度", "只换颜色不表达差异", "长句塞进盒子导致压字"),
        "fallback_preset": "comparison",
    },
    "causation": {
        "intent": "把 A→B→C 的因果链讲清楚，强调「因为…所以…」。",
        "semantic_constraints": (
            "每个节点必须能在旁白里找到对应短语，节点顺序不得颠倒",
            "箭头标签只能是推演关系（导致/于是/才），不得凭空加入中间环节",
        ),
        "composition": "横向链条 3–4 个节点 + 箭头；节点盒随文字自适应宽度；顶部一行结论标题。",
        "motion": "节点逐个落定（scale 0.92→1 + fade），箭头在前一节点落定后 0.15s 沿路径绘制；末节点可加一次强调脉冲。",
        "anti_patterns": ("节点之间无箭头只有并列", "箭头方向与语义相反", "把长句硬塞进 170px 盒子"),
        "fallback_preset": "causation",
    },
    "sequence": {
        "intent": "把步骤/流程按顺序铺开，强调「先…再…最后…」。",
        "semantic_constraints": (
            "步骤数必须与旁白列出的动作数一致，不得补造步骤",
            "每步以动词开头，超过 14 字先摘要",
        ),
        "composition": "纵向编号列表或横向步骤条；编号圆 + 一步一行；行距保证 6 步以内不拥挤。",
        "motion": "编号圆先弹入（back-out），文字随行 0.1s 延迟淡入；当前步骤可加高亮条推进。",
        "anti_patterns": ("步骤没有编号", "一步里塞两个动作", "超过 8 步仍强行一屏"),
        "fallback_preset": "sequence",
    },
    "diagram": {
        "intent": "用最简箭头图表达一组递进关系（比 causation 更轻）。",
        "semantic_constraints": ("节点 2–4 个", "节点之间必须是递进或转化关系"),
        "composition": "居中横向排列，箭头分隔；标题一行。",
        "motion": "节点依次淡入上移 8px，箭头描线。",
        "anti_patterns": ("节点过多导致缩字", "与 causation 完全同构却重复出现"),
        "fallback_preset": "diagram",
    },
    "timeline": {
        "intent": "展示时间轴上的演进（时间点 + 事件）。",
        "semantic_constraints": ("时间点必须来自原文；无明确年份时用「早期/此后/如今」这类相对词",),
        "composition": "水平轴线 + 节点；标签上下交替避免碰撞；标题一行。",
        "motion": "轴线先绘制，节点依次落定，当前节点脉冲。",
        "anti_patterns": ("编造年份", "节点标签互相碰撞"),
        "fallback_preset": "timeline",
    },
    "tree": {
        "intent": "层级/分类结构：一个根 + 若干子项。",
        "semantic_constraints": ("子项必须是同一层级的并列项", "根节点来自原文主题词"),
        "composition": "根节点居中上方，子节点横向排布，连线呈树形。",
        "motion": "根节点先落定，连线向下生长，子节点错峰出现。",
        "anti_patterns": ("根节点与子节点语义重复", "子项跨层级混排"),
        "fallback_preset": "tree",
    },
    "venn": {
        "intent": "两个集合的重叠/交集关系。",
        "semantic_constraints": ("必须真的存在交集语义，不能把「对比」硬画成维恩",),
        "composition": "双圆重叠 + 交集标签。",
        "motion": "两圆分别从左右滑入并重叠，交集区域高亮。",
        "anti_patterns": ("无交集却画重叠", "交集标签为空"),
        "fallback_preset": "venn",
    },
    "bar_chart": {
        "intent": "用柱高比较数量级。",
        "semantic_constraints": ("数值必须来自原文或 web_context；没有数值就不画柱状图",),
        "composition": "纵轴基线 + 3–6 根柱 + 直接标注数值。",
        "motion": "柱体从 0 生长，数值随柱顶滚动到位。",
        "anti_patterns": ("编造数值", "柱子过多导致标签斜排"),
        "fallback_preset": "bar_chart",
    },
    "pie_chart": {
        "intent": "展示占比结构。",
        "semantic_constraints": ("各项之和必须为整体；只允许 2–5 项",),
        "composition": "环形/扇形 + 图例。",
        "motion": "扇形依次扫入，图例同步出现。",
        "anti_patterns": ("占比之和不为 100%", "扇区过多"),
        "fallback_preset": "pie_chart",
    },
    "line_chart": {
        "intent": "展示趋势变化。",
        "semantic_constraints": ("至少 3 个数据点且来自原文",),
        "composition": "坐标轴 + 折线 + 数据点。",
        "motion": "折线沿路径绘制，数据点依次出现。",
        "anti_patterns": ("两点连线冒充趋势", "编造数据点"),
        "fallback_preset": "line_chart",
    },
    "quote": {
        "intent": "把一句关键引文放大成视觉主体。",
        "semantic_constraints": ("必须逐字引用原文，不得改写引文内容",),
        "composition": "大字号引文 + 出处小字。",
        "motion": "引文逐行淡入，引号先出现。",
        "anti_patterns": ("改写引文", "引文超过 3 行"),
        "fallback_preset": "quote",
    },
}

#: 通用兜底：catalog 里存在但没手写覆盖的类型也至少拿到这些约束。
_GENERIC_CONSTRAINTS: tuple[str, ...] = (
    "元素内容必须能在本段旁白里找到依据，不得编造事实或数字",
    "文字放不下时先摘要，不允许缩小到不可读或溢出容器",
    "至少两个非装饰元素在片段内发生可观测变化（位置/尺寸/透明度）",
)


def _catalog_types() -> list[dict[str, Any]]:
    """从 AnimationCatalog 取逻辑动画清单（唯一事实来源，避免 id 漂移）。"""
    try:
        from clipwright.animation.catalog import AnimationCatalog

        return list(AnimationCatalog.get_logic_animations() or [])
    except Exception:  # noqa: BLE001 — 目录不可用时不阻塞导入
        logger.warning("AnimationTypeRegistry: 读取 AnimationCatalog 失败", exc_info=True)
        return []


def _build_specs() -> dict[str, AnimationTypeSpec]:
    specs: dict[str, AnimationTypeSpec] = {}
    for item in _catalog_types():
        tid = str(item.get("id") or "").strip()
        if not tid:
            continue
        override = _OVERRIDES.get(tid, {})
        intent = str(override.get("intent") or item.get("desc") or item.get("name") or tid)
        specs[tid] = AnimationTypeSpec(
            id=tid,
            name=str(item.get("name") or tid),
            intent=intent,
            semantic_constraints=tuple(
                override.get("semantic_constraints") or _GENERIC_CONSTRAINTS
            ),
            composition=str(override.get("composition") or ""),
            motion=str(override.get("motion") or ""),
            anti_patterns=tuple(override.get("anti_patterns") or ()),
            fallback_preset=str(override.get("fallback_preset") or ""),
            fallback_template=str(override.get("fallback_template") or ""),
        )

    # 只有手写覆盖、但 catalog 暂时取不到的类型也要在册（离线/测试环境）
    for tid, override in _OVERRIDES.items():
        if tid in specs:
            continue
        specs[tid] = AnimationTypeSpec(
            id=tid,
            name=str(override.get("name") or tid),
            intent=str(override.get("intent") or tid),
            semantic_constraints=tuple(override.get("semantic_constraints") or _GENERIC_CONSTRAINTS),
            composition=str(override.get("composition") or ""),
            motion=str(override.get("motion") or ""),
            anti_patterns=tuple(override.get("anti_patterns") or ()),
            fallback_preset=str(override.get("fallback_preset") or ""),
        )
    return specs


REGISTRY: dict[str, AnimationTypeSpec] = _build_specs()


def get_spec(type_id: str) -> AnimationTypeSpec | None:
    return REGISTRY.get(str(type_id or "").strip())


def all_specs() -> list[AnimationTypeSpec]:
    return sorted(REGISTRY.values(), key=lambda s: s.id)


def known_type_ids() -> tuple[str, ...]:
    return tuple(sorted(REGISTRY))


def _read_prompt_file(type_id: str) -> str:
    path = PROMPTS_DIR / f"{type_id}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def prompt_version_of(type_id: str) -> str:
    body = _read_prompt_file(type_id)
    if not body:
        return ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def load_type_prompt(type_id: str) -> dict[str, str]:
    """读取并结构化某一类型的提示词正文。

    返回 ``{"Intent": "...", "Composition": "...", ...}``；文件缺失返回 ``{}``，
    由调用方决定是走通用提示词还是判为配置错误（注册表校验会提前告警）。
    """
    body = _read_prompt_file(type_id)
    if not body.strip():
        return {}
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {
        name: "\n".join(lines).strip()
        for name, lines in sections.items()
        if name in PROMPT_SECTIONS or True  # 保留自定义段，装配时按需取
    }


def missing_prompt_files(ids: Iterable[str] | None = None) -> list[str]:
    """缺提示词正文的类型（M1 验收用：必须为空）。"""
    target = list(ids) if ids is not None else list(known_type_ids())
    return [tid for tid in target if not _read_prompt_file(tid).strip()]


def validate_registry(strict: bool = False) -> list[str]:
    """注册表自检：缺提示词、fallback 不存在、id 漂移。

    返回问题列表（空 = 通过）。``strict=True`` 时抛 ValueError（CI/启动校验用）。
    """
    problems: list[str] = []
    catalog_ids = {str(i.get("id")) for i in _catalog_types() if i.get("id")}
    if catalog_ids:
        missing = catalog_ids - set(REGISTRY)
        extra = set(REGISTRY) - catalog_ids
        if missing:
            problems.append(f"注册表缺少 catalog 类型: {sorted(missing)}")
        if extra:
            problems.append(f"注册表多出 catalog 未声明的类型: {sorted(extra)}")
    no_prompt = missing_prompt_files()
    if no_prompt:
        problems.append(f"缺少类型提示词文件: {sorted(no_prompt)}")
    for spec in REGISTRY.values():
        if spec.fallback_preset and spec.fallback_preset not in _KNOWN_PRESETS:
            problems.append(f"{spec.id}: fallback_preset={spec.fallback_preset} 不存在")
    if strict and problems:
        raise ValueError("AnimationTypeRegistry 校验失败: " + "; ".join(problems))
    return problems


#: `diagram_svg.py` 能画的 preset（回退白名单；新增 preset 时同步）
_KNOWN_PRESETS: frozenset[str] = frozenset({
    "diagram", "causation", "comparison", "sequence", "timeline", "tree", "venn",
    "bar_chart", "pie_chart", "line_chart", "hierarchy", "sequence_diagram",
    "flow_chart", "radar", "gantt", "venn3", "heatmap", "sankey", "concept",
    "codeblock", "datatable", "quote", "compcard", "orgchart",
})


__all__ = [
    "AnimationTypeSpec",
    "CategoryAnimationContext",
    "PersonaAnimationContext",
    "PROMPTS_DIR",
    "PROMPT_SECTIONS",
    "REGISTRY",
    "all_specs",
    "get_spec",
    "known_type_ids",
    "load_type_prompt",
    "missing_prompt_files",
    "persona_fingerprint",
    "prompt_version_of",
    "validate_registry",
]
