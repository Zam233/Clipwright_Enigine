"""素材 Agent（MaterialAgent）— 素材智能匹配 + 缓存 + 降级。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.config import logger, settings
from clipwright.material import MaterialRegistry
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    MaterialInput,
    MaterialOutput,
)
from clipwright.services.llm import LLMService
from clipwright.services.trace import add_event
# PRD W4.4：关键词提示词已搬到 clipwright/prompts.py（文案逐字一致）
from clipwright.prompts import (build_material_queries_batch_prompt, build_material_queries_prompt, build_material_english_prompt)  # noqa: E402
from clipwright.plugins.prompt_overrides import safe_builder

# 搜索缓存: query_hash → list[result]
# TTL + LRU 淘汰（原 dict 满后静默拒绝写入，缓存悄悄退化）
_search_cache: dict[str, tuple[float, list[dict]]] = {}
_SEARCH_CACHE_MAX = 200
_SEARCH_CACHE_TTL_SEC = 3600.0

# 有界重选阈值：首轮最优匹配分低于该值 → 触发至多一次补充搜索（防无限成本 D9）。
# 可经 plugin_config.reselect_threshold 覆盖。
_RESELECT_THRESHOLD = 0.45


def _llm_configured() -> bool:
    """是否已配置任何 LLM（主/视觉 LLM 的 api_key 或 base_url，含 Ollama 兼容 API）。"""
    return bool(
        settings.llm_api_key
        or settings.vision_llm_api_key
        or settings.llm_base_url
        or settings.vision_llm_base_url
    )


def annotate_mv_placement(
    scenes: list[dict[str, Any]],
    candidate_clips: list[dict[str, Any]],
    *,
    bits_per_second: float | None = None,
) -> dict[str, Any] | None:
    """把"每段落到哪个镜头"写回场景（S 轨接线；纯逻辑，便于单测）。

    ``candidate_clips`` 是 MaterialAgent 的中间产物（每个场景一条，含
    ``suggested_assets``）；这里把它们摊平成候选池，交给 ``mv_material`` 打分落位。
    返回 ``placement_report``；没有任何候选时返回 ``None``（不产生空噪音）。
    """
    from clipwright.services.mv_cues import build_grid
    from clipwright.services.mv_material import annotate_scenes_with_placement

    pool: list[dict[str, Any]] = []
    for clip in candidate_clips or []:
        for asset in clip.get("suggested_assets") or []:
            if not isinstance(asset, dict):
                continue
            asset_id = asset.get("asset_id") or asset.get("id") or ""
            if not asset_id:
                continue  # 没有 id 的候选落不了位，直接跳过（不产生空引用）
            pool.append(
                {
                    "asset_id": asset_id,
                    "description": asset.get("description") or asset.get("title") or "",
                    "duration_sec": asset.get("duration_sec") or asset.get("duration") or 0.0,
                    "tags": asset.get("tags") or (),
                    "license_ok": bool(asset.get("license_ok", True)),
                }
            )
    if not pool:
        return None
    grid = build_grid(bits_per_second) if bits_per_second and bits_per_second > 0 else None
    return annotate_scenes_with_placement(scenes, pool, grid=grid)


# 文本相关性兜底：纯本地分词，零网络。拉丁词整词 + 单个 CJK 字符。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize_text(*parts: Any) -> set[str]:
    """纯文本分词：拉丁单词整词 + 单个 CJK 字符。空/None 安全。"""
    tokens: set[str] = set()
    for part in parts:
        if not part:
            continue
        tokens.update(_LATIN_RE.findall(str(part).lower()))
        tokens.update(_CJK_RE.findall(str(part)))
    return tokens


def _text_relevance_score(
    narration_text: str,
    material_intent: str,
    scene_keywords: list[str],
    asset: Any,
) -> float:
    """C4b 文本相关性兜底分（0-1）— Containment（包含计数）系数。

    公式：|A ∩ B| / min(|A|, |B|)，A = 场景词表（旁白 + 素材意图 + 场景关键词），
    B = 素材词表（title + tags）。任一侧为空 → 0。
    选择 Containment 而非 Jaccard：素材侧词表短（title+tags），该系数衡量
    "素材词汇有多少出现在场景旁白/意图中"，子集命中（如「夜景」⊂「城市夜景」）
    得高分，贴合"该场景旁白/意图是否与素材标题/标签相关"的排序目标。
    """
    if isinstance(asset, dict):
        title = asset.get("title") or ""
        tags = asset.get("tags") or []
    else:
        title = getattr(asset, "title", "") or ""
        tags = getattr(asset, "tags", None) or []
    scene_tokens = _tokenize_text(
        narration_text, material_intent, " ".join(scene_keywords or [])
    )
    asset_tokens = _tokenize_text(title, " ".join(tags))
    if not scene_tokens or not asset_tokens:
        return 0.0
    overlap = len(scene_tokens & asset_tokens)
    return overlap / min(len(scene_tokens), len(asset_tokens))


def _derive_reselect_query(
    narration_text: str,
    material_intent: str,
    scene_keywords: list[str],
) -> str:
    """C4c 重选补充搜索词：本地抽取，零额外 LLM 往返。

    选择本地抽取而非再调一次 _llm_search_queries：后者与首轮同源（标题/关键词/
    描述），很可能产出相同查询命中相同搜索缓存，无法带来新候选；本地抽取以
    narration_text/material_intent 为新词源（正是首轮低分想要补抓的视觉信息），
    且测试可确定性 mock。
    复用 _tokenize_text：拉丁整词视为高信息词直接入查询；CJK 按单字切分不成
    查询词，回退到原始旁白/素材意图文本（截断 80 字）作为查询。
    """
    tokens = _tokenize_text(
        narration_text, material_intent, " ".join(scene_keywords or [])
    )
    latin = sorted(t for t in tokens if _LATIN_RE.fullmatch(t))
    if latin:
        return " ".join(latin)[:80]
    raw = " ".join(filter(None, [narration_text, material_intent])).strip()
    return raw[:80] or " ".join(scene_keywords or [])[:80]


class _CachedResult:
    """缓存结果的轻量封装，使属性访问与 AssetResult 对象一致。"""
    __slots__ = ("asset", "source_name", "score")
    class _Asset:
        __slots__ = ("id", "title", "url", "local_path", "duration_sec", "tags", "type",
                     "license", "license_url", "author", "source_url", "resolution")
        def __init__(self, d: dict):
            self.id = d["asset_id"]
            self.title = d.get("title", "")
            self.url = d.get("url", "")
            self.local_path = d.get("local_path", "")
            self.duration_sec = d.get("duration_sec", 0)
            self.tags = d.get("tags", [])
            self.type = d.get("type", "")
            # license 与对象面对齐（缓存命中/未命中打分行为一致）
            self.license = d.get("license", "")
            # P2-29: 缓存命中路径保留授权四字段（缺失曾导致署名缺失）
            self.license_url = d.get("license_url", "")
            self.author = d.get("author", "")
            self.source_url = d.get("source_url", "")
            self.resolution = d.get("resolution", "")
    def __init__(self, d: dict):
        self.asset = self._Asset(d)
        self.source_name = d.get("source_name", "")
        self.score = d.get("score", 0.0)


# 轮 84（数码评测专项）：品类定向检索提示——搜索词 prompt 此前品类无关，
# 评测类的"产品特写/参数图/跑分对比"镜头意图传不到检索层
_CATEGORY_SEARCH_HINTS: dict[str, str] = {
    "digital_review": (
        "- 数码评测类：优先搜索产品特写（如'智能手机特写''笔记本开箱'）、"
        "参数/数据图表（如'跑分对比图''参数表格'）、使用场景实拍；"
        "涉及具体型号时图库通常无解，改搜同类产品的通用特写\n"
    ),
    "knowledge_longform": (
        "- 知识讲解类：优先搜索与讲解概念对应的具象画面（实验室/城市/历史影像/自然现象），"
        "抽象概念用隐喻画面表达\n"
    ),
}


def _category_search_hint(category_plugin_id: str) -> str:
    """选片规范：优先类型声明（declares.search_hint），其次 material 阶段槽位，最后旧字典。

    V1：material 是 10 槽位里唯一**没有 LLM 消费者**的阶段（选片走检索），所以它的
    自然语言规范在这里落到检索式上——否则该槽位就是"写了没人读"的死配置。
    """
    from clipwright.services.type_spec_runtime import search_hint
    from clipwright.services.type_prompts import first_line

    legacy = _CATEGORY_SEARCH_HINTS.get(category_plugin_id or "", "")
    declared = search_hint(category_plugin_id, legacy)
    if declared:
        return declared
    return first_line(category_plugin_id or "", "material") or legacy


#: 需要"按句落位"的类型（MV/PV）；也可用 ``extra_params.mv_placement`` 显式开启。
_MV_PLACEMENT_CATEGORIES = frozenset({"lyric_mv", "mv", "pv", "music_video"})


def _placement_enabled(context: Any) -> bool:
    extra = getattr(context, "extra_params", {}) or {}
    if extra.get("mv_placement"):
        return True
    return str(getattr(context, "category_plugin_id", "") or "") in _MV_PLACEMENT_CATEGORIES


async def _llm_search_queries_batch(
    scenes: list[dict[str, Any]],
    pipeline_id: str = "",
    persona_style: str = "",
    brief_hint: str = "",
    category_hint: str = "",
) -> list[list[str]] | None:
    """一次 LLM 调用为全部场景生成搜索词（省 N-1 次 LLM 往返）。

    返回顺序与 scenes 对齐的查询词列表；失败/解析不出时返回 None，
    由调用方回退到逐场景生成。
    """
    from clipwright.services.llm import LLMService

    style_hint = f"\n- 优先匹配风格: {persona_style}" if persona_style else ""
    brief_hint_text = f"\n- 简报素材要求: {brief_hint}" if brief_hint else ""

    scene_lines = []
    for i, scene in enumerate(scenes):
        title = scene.get("title", "")
        keywords = ", ".join(scene.get("keywords", []) or [])
        desc = (scene.get("description", "") or "")[:120]
        scene_lines.append(f"[{i + 1}] 标题: {title} | 关键词: {keywords} | 描述: {desc}")

    prompt = safe_builder("build_material_queries_batch_prompt")(
        scene_lines, category_hint=category_hint,
        persona_style=persona_style, brief_hint=brief_hint,
    )

    llm = LLMService()
    try:
        resp = await asyncio.wait_for(llm.ask(prompt, use_flash=True), timeout=900)
        if not (resp.success and resp.content):
            return None
        result: list[list[str]] = [[] for _ in scenes]
        for line in resp.content.strip().splitlines():
            m = re.match(r"^\s*场景?\s*(\d+)\s*[:：]\s*(.+)$", line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(scenes):
                queries = [q.strip() for q in re.split(r"[、,，/|]", m.group(2)) if q.strip()][:5]
                if queries:
                    result[idx] = queries
        if any(result):
            if pipeline_id:
                add_event(pipeline_id, "material", "llm", f"LLM 搜索词批量生成: {len(scenes)} 场景")
            logger.info("MaterialAgent: LLM 批量搜索词=%s", result)
            return result
    except asyncio.TimeoutError:
        logger.warning("MaterialAgent: LLM 批量搜索词生成超时")
    except Exception as e:
        logger.warning("MaterialAgent: LLM 批量搜索词失败: %s", e)
    return None


async def _llm_search_queries(
    scene_title: str,
    keywords: list[str],
    description: str,
    pipeline_id: str = "",
    persona_style: str = "",
    brief_hint: str = "",
) -> list[str]:
    """调用 LLM 生成具体视觉搜索词（考虑 Persona 风格 + 简报素材偏好）。"""
    from clipwright.services.llm import LLMService

    style_hint = f"\n- 优先匹配风格: {persona_style}" if persona_style else ""
    brief_hint_text = f"\n- 简报素材要求: {brief_hint}" if brief_hint else ""

    prompt = safe_builder("build_material_queries_prompt")(
        scene_title, keywords, description,
        persona_style=persona_style, brief_hint=brief_hint,
    )
    if pipeline_id:
        add_event(pipeline_id, "material", "llm", f"LLM 搜索词生成: {scene_title}")

    from clipwright.plugins.prompt_registry import PluginPromptRegistry
    plugin_prompts = PluginPromptRegistry.get_for_agent("material")
    if plugin_prompts:
        prompt += "\n\n## 插件能力扩展\n" + "\n\n".join(plugin_prompts)

    llm = LLMService()
    try:
        import asyncio
        # 搜索词生成是简单任务 → 使用 flash 轻量模型
        resp = await asyncio.wait_for(llm.ask(prompt, use_flash=True), timeout=900)
        if resp.success and resp.content:
            queries = [q.strip() for q in resp.content.strip().split("\n") if q.strip()][:5]
            if pipeline_id:
                add_event(pipeline_id, "material", "llm_result", f"搜索词: {queries}")
            logger.info("MaterialAgent: LLM 搜索词=%s", queries)
            return queries
    except asyncio.TimeoutError:
        logger.warning("MaterialAgent: LLM 搜索词生成超时")
    except Exception as e:
        logger.warning("MaterialAgent: LLM 搜索词失败: %s", e)

    # 降级：直接用场景标题+关键词
    fallback = [scene_title] + keywords[:2]
    logger.info("MaterialAgent: 降级搜索词=%s", fallback)
    return fallback


def _has_cjk(text: str) -> bool:
    """是否含中日韩字符（判断查询是否需要翻译成英文）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


async def _to_english_queries(queries: list[str], *, pipeline_id: str = "") -> list[str]:
    """把中文检索词翻成英文（图库是英文索引，中文命中为 0）。

    有界：单次 flash LLM 调用；失败返回 []（调用方保持原行为）。
    """
    import re as _re

    text = " / ".join(str(q) for q in queries if str(q).strip())[:1200]
    if not text:
        return []
    prompt = safe_builder("build_material_english_prompt")(text)
    try:
        resp = await asyncio.wait_for(LLMService().ask(prompt, use_flash=True), timeout=120)
        raw = str(getattr(resp, "content", "") or "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in _re.split(r"[/\n、]", raw) if p.strip()]
        out = [p for p in parts if not _has_cjk(p)][:10]
        if pipeline_id:
            add_event(pipeline_id, "material", "llm_result", f"英文检索词: {out}")
        return out
    except Exception as exc:  # noqa: BLE001 — 翻译失败不阻断选材
        logger.warning("MaterialAgent: 检索词英译失败（保持原查询）: %s", exc)
        return []


async def _validate_video_frame(r: Any, expected_text: str) -> float:
    """校验单个素材候选是否匹配场景，返回 0-1 分。

    合并双契约（按入参形态分派）：
    - r 为素材对象（本地线：MaterialAsset / 搜索结果）→ 本地语义：
      无可访问 URL → 0.0；frame_validator 全黑帧 → 0.0；
      工具失败/无分数 → 标题+tags 与 expected_text 的 token/F1 启发式。
    - r 为 URL 字符串（远程线调用约定）→ 远程语义：
      无 URL → 0.5；全黑帧 → 0.0；超时 → 0.3；失败 → 0.0。
    """
    title, tags, video_url = "", [], ""
    if isinstance(r, str):
        video_url = r
    else:
        title, tags, video_url = _asset_parts(r)

    if not video_url:
        # 本地线：对象形态无可访问 URL → 0.0（不作为最优候选）
        if not isinstance(r, str):
            return 0.0
        return 0.5

    return await _validate_video_frame_inner(r, video_url, title, tags, expected_text)


async def _validate_video_frame_inner(
    r: Any, video_url: str, title: str, tags: list, expected_text: str,
) -> float:
    # 能力动态解析：任何声明 capability="frame_check" 的工具（含插件）都可接管，
    # 没有插件时落到内置 frame_validator；两者都没有才走启发式。
    from clipwright.tool.capabilities import resolve as _resolve_capability
    try:
        tool = _resolve_capability("frame_check")
        if tool is None:
            if isinstance(r, str):
                return 0.5
            return _heuristic_title_match_score(title, tags, expected_text)
        result = await asyncio.wait_for(
            tool.execute(video_url=video_url, expected_text=expected_text),
            timeout=15,
        )
        output = result.output or {}
        if output.get("is_blank"):
            logger.debug("MaterialAgent: 跳过全黑帧 %s", video_url[:40])
            return 0.0
        # 轮 88（P0 修复：选片近似随机）：活版 frame_validator 的 match_score
        # 是「帧健康占比」（无黑帧/过曝样本比例，健康视频恒 1.0），不是语义
        # 相关度——旧实现直接把它当候选分返回，健康视频一律满分，标题相关性
        # 兜底被完全绕过。现在语义启发式为分、帧健康为乘数。
        _sem = _heuristic_title_match_score(title, tags, expected_text)
        _health = output.get("match_score")
        if isinstance(_health, (int, float)):
            return round(_sem * max(0.0, min(1.0, float(_health))), 3)
        return round(_sem, 3)
    except asyncio.TimeoutError:
        logger.debug("MaterialAgent: 帧验证超时 %s", video_url[:40])
        if isinstance(r, str):
            return 0.3
    except Exception as e:
        logger.debug("MaterialAgent: 帧验证失败 %s: %s", video_url[:40], e)
        if isinstance(r, str):
            return 0.0
    if isinstance(r, str):
        return 0.5
    return _heuristic_title_match_score(title, tags, expected_text)


def _search_cache_key(query: str, source_ids: list[str] | None) -> str:
    raw = f"{query}:{json.dumps(source_ids or [], sort_keys=True)}"
    return hashlib.md5(raw.encode()).hexdigest()


async def _search_with_cache(
    query: str,
    top_k: int = 5,
    source_ids: list[str] | None = None,
) -> list[Any]:
    """带缓存的素材搜索（B12: TTL + LRU 淘汰）。"""
    global _search_cache
    import time as _time
    key = _search_cache_key(query, source_ids)
    entry = _search_cache.get(key)
    if entry is not None:
        ts, data = entry
        if _time.time() - ts <= _SEARCH_CACHE_TTL_SEC:
            logger.debug("MaterialAgent: 缓存命中 %s", query[:30])
            return [_CachedResult(d) for d in data]
        _search_cache.pop(key, None)  # TTL 过期

    # 暗区补齐（PRD modern-agent-review M2）：素材源检索不走 call_tool，
    # 以前只有缓存与 logger。记成内部操作（含耗时/结果数），随阶段轨迹上报。
    from clipwright.services import agent_context as _ctx

    with _ctx.operation(f"material_search:{','.join(source_ids or ['all'])[:40]}",
                        source="internal") as _op:
        results = await MaterialRegistry.search(
            query=query,
            top_k_per_source=top_k,
            source_ids=source_ids,
        )
        _op["detail"] = f"{len(results)} 条结果（query={query[:40]}）"
    # 场景候选排除音频——BGM 由 AudioAgent 独立链路检索；音频混入
    # 视频场景候选会被 video_trim 拒绝，烧掉候选位并把场景挤成占位
    results = [
        r for r in results
        if str(getattr(r.asset, "type", "")) != "audio"
    ]
    # 轮 81：空结果不写缓存——「真无结果」与「源失败/限流」不可区分，
    # 负缓存会把一次 429/网络抖动放大成 TTL 内同查询全部空候选
    if not results:
        return results
    # 只缓存 AssetResult 对象的外部表示
    cached = []
    for r in results:
        cached.append({
            "asset_id": r.asset.id,
            "title": r.asset.title,
            "url": r.asset.url,
            "local_path": r.asset.local_path,
            "duration_sec": r.asset.duration_sec,
            "tags": r.asset.tags,
            "type": getattr(r.asset, "type", ""),
            "license": getattr(r.asset, "license", ""),
            # 轮 82（版权链）：缓存命中路径同样保留授权字段
            "license_url": getattr(r.asset, "license_url", ""),
            "author": getattr(r.asset, "author", ""),
            "source_url": getattr(r.asset, "source_url", ""),
            "resolution": getattr(r.asset, "resolution", ""),
            "source_name": r.source_name,
            "score": r.score,
        })

    if len(_search_cache) >= _SEARCH_CACHE_MAX:
        # LRU——淘汰最旧条目（原实现满后静默拒绝写入）
        oldest = min(_search_cache.items(), key=lambda kv: kv[1][0])
        _search_cache.pop(oldest[0], None)
    _search_cache[key] = (_time.time(), cached)
    return results




# ── 合并：本地线纯函数 helper（本地测试与评分兜底依赖）——
# 与远程线的 _text_relevance_score/_derive_reselect_query 并存，互不调用。

_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+")


_VISION_SCORE_CACHE: dict[str, float] = {}


_VISION_SCORE_CACHE_MAX = 512


_MAX_VALIDATION_ATTEMPTS = 6


_VALIDATION_THRESHOLD = 0.35

# 启发式分词：拉丁词 + CJK 字符


def _text_tokens(text: str) -> set[str]:
    """将文本切分为可比较的 token 集合：拉丁词 + CJK 相邻字符二元组。

    中文无空格分词，用相邻字符二元组（bigram）可避免单字误匹配
    （如「城市夜景」与「风景」共享「景」但不共享二元组）。
    """
    latin = set(_LATIN_TOKEN_RE.findall(text.lower()))
    chars = _CJK_CHAR_RE.findall(text)
    if len(chars) >= 2:
        bigrams = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
    else:
        bigrams = set(chars)
    return latin | bigrams


def _heuristic_title_match_score(
    title: str, tags: list[str], expected_text: str
) -> float:
    """标题/标签 vs 场景文本的启发式匹配分 (0-1)。

    评分方法：
    - 分词：拉丁文按单词切分；中文按相邻字符二元组（bigram）切分。
    - 用 F1（调和平均）衡量素材 title+tags 与场景文本的 token 重叠：
      - 完全无关（无重叠 token）→ 0.0
      - 场景 token 被充分覆盖 → 接近 1.0
    - 空素材文本 → 0.0（无法判断，不选为最优）
    - 空场景文本 → 0.5（中性分）
    """
    expected_tokens = _text_tokens(expected_text)
    if not expected_tokens:
        return 0.5
    asset_text = " ".join([title or ""] + list(tags or []))
    asset_tokens = _text_tokens(asset_text)
    if not asset_tokens:
        return 0.0
    overlap = len(expected_tokens & asset_tokens)
    if overlap == 0:
        return 0.0
    precision = overlap / len(asset_tokens)
    recall = overlap / len(expected_tokens)
    denom = precision + recall
    f1 = (2 * precision * recall / denom) if denom > 0 else 0.0
    return min(1.0, f1)


def _asset_parts(asset: Any) -> tuple[str, list[str], str]:
    """提取候选素材的 (title, tags, url)，兼容 Asset 对象 / 搜索结果包装 / dict。"""
    if hasattr(asset, "asset"):
        asset = asset.asset
    if hasattr(asset, "title"):
        return (
            asset.title or "",
            list(asset.tags or []),
            asset.url or asset.local_path or "",
        )
    return (
        asset.get("title", ""),
        list(asset.get("tags", []) or []),
        asset.get("url", "") or asset.get("local_path", ""),
    )


def _split_search_queries(queries: list[str]) -> list[str]:
    """把 LLM 生成的长句/多词搜索词切分为 2-6 字短关键词组合（后处理兜底）。

    - 按 [、,，/|] 与空白切分，过滤空串与单字；
    - 含 CJK 且 >8 字的长短语按 2/3 字滑窗拆成子查询（保留『黑板』『粉笔』等短关键词）；
    - 已是短词 / 空列表 → 原样返回 / 空列表，不崩。
    """
    parts: list[str] = []
    for q in queries or []:
        for seg in re.split(r"[、,，/|；;]+", q):
            for piece in seg.split():
                t = piece.strip()
                if not t or len(t) == 1:
                    continue
                if len(t) > 8 and _CJK_CHAR_RE.search(t):
                    # 长 CJK 短语 → 2/3 字滑窗子查询
                    for size in (3, 2):
                        for i in range(len(t) - size + 1):
                            parts.append(t[i:i + size])
                else:
                    parts.append(t)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def clear_material_vision_cache() -> None:
    """清空素材视觉校验缓存（测试/重置用）。"""
    _VISION_SCORE_CACHE.clear()

# 素材校验有界寻源：单场景最多尝试轮数 + 触发重试的最低分数阈值
# （原 2 次重试在素材源命中不佳时过早放弃，改为 6 轮持续寻源）




def orientation_from_brief(
    brief: dict | None, extra_params: dict | None = None,
) -> str:
    """素材方向：显式 extra 优先，其次按 brief.asset_ratio 推导（竖屏比例→portrait）。"""
    explicit = str((extra_params or {}).get("orientation") or "").strip()
    if explicit in ("portrait", "landscape"):
        return explicit
    ratio = str((brief or {}).get("asset_ratio") or "").replace(" ", "")
    return "portrait" if ratio in ("9:16", "3:4", "4:5") else "landscape"


class MaterialAgent(BaseAgent[MaterialInput, MaterialOutput]):
    """素材 Agent：根据脚本骨架语义检索匹配素材。"""

    agent_name = "material_agent"

    @staticmethod
    def _extract_persona_style(persona_config: dict) -> str:
        """从 Persona 配置提取视觉风格关键词（用于搜索和评分）。"""
        visual = persona_config.get("visual", {}) or {}
        identity = persona_config.get("identity", {}) or {}

        parts = []
        tone = identity.get("tone", "")
        palette = visual.get("palette", "")
        anim = visual.get("animation_styles", {}) or {}

        if tone:
            parts.append(tone.replace("_", " "))
        if palette:
            parts.append(palette.replace("_", " "))
        if isinstance(anim, dict) and anim.get("text_intro"):
            style = anim["text_intro"].replace("_", " ")
            parts.append(style)

        return ", ".join(parts) if parts else ""

    def _persona_style_score(
        self, asset_obj, tags: list[str], persona_style: str, persona_config: dict
    ) -> float:
        """根据素材标签与 Persona 风格的匹配度打分。"""
        if not persona_style:
            return 0.5

        score = 0.5

        # 检查素材标签是否匹配 Persona 风格
        style_keywords = persona_style.lower().split(", ")
        asset_str = " ".join((tags or [])).lower()

        matches = sum(1 for kw in style_keywords if kw in asset_str)
        if matches > 0:
            score = min(1.0, 0.5 + matches * 0.15)

        # 检查调色板偏好
        palette = (persona_config.get("visual", {}) or {}).get("palette", "")
        if palette and palette.lower().replace("_", " ") in asset_str:
            score = min(1.0, score + 0.2)

        return score

    async def _validate_via_vision_llm(
        self,
        asset: Any,
        scene_title: str,
        scene_keywords: list[str],
        scene_description: str,
        narration_text: str = "",
        material_intent: str = "",
        frame_count: int = 3,
    ) -> float:
        """使用视觉 LLM 分析候选素材帧内容，返回语义匹配分 (0-1)。

        E10 合并：URL→score 缓存——同素材多场景复用省多模态调用；
        仅缓存成功分数，失败/异常不写缓存。"""
        url = ""
        if hasattr(asset, "asset"):
            url = getattr(asset.asset, "url", "") or getattr(asset.asset, "local_path", "")
        elif hasattr(asset, "url"):
            url = getattr(asset, "url", "") or getattr(asset, "local_path", "")
        elif isinstance(asset, dict):
            url = asset.get("url", "") or asset.get("local_path", "")
        if url and url in _VISION_SCORE_CACHE:
            return _VISION_SCORE_CACHE[url]
        try:
            # 能力动态解析：插件可用 capability="vision_score" 接管视觉打分，
            # 缺实现时返回 NOT_FOUND，下面的 output 兜底保持旧行为。
            from clipwright.tool.capabilities import resolve as _resolve_capability
            from clipwright.tool.registry import ToolRegistry as _ToolRegistry

            _vision_tool = _resolve_capability("vision_score")
            if _vision_tool is None:
                return 0.5
            # 走 BaseAgent.call_tool 而不是直连注册表：视觉打分是 material 阶段最贵、
            # 最容易超时的调用，必须进轨迹与事件流（主 agent 要能看见"这一场打了多久、
            # 失败几次"），否则又变成子 agent 偷偷干活（E2E 2026-10-29 裁定）。
            result = await self.call_tool(
                "vision_score",
                prefer=(_vision_tool.name,),
                asset=asset,
                scene_context={
                    "title": scene_title,
                    "keywords": scene_keywords,
                    "description": scene_description,
                    "narration_text": narration_text,
                    "material_intent": material_intent,
                },
                frame_count=frame_count,
            )
            output = result.output or {}
            score = output.get("score", 0.5)
            extraction_method = output.get("extraction_method", "unknown")
            frames = output.get("frames_analyzed", 0)
            logger.info(
                "MaterialAgent: [视觉LLM] scene=%s score=%.3f method=%s frames=%d",
                scene_title, score, extraction_method, frames,
            )
            score = float(score)
            if url:
                if len(_VISION_SCORE_CACHE) >= _VISION_SCORE_CACHE_MAX:
                    _VISION_SCORE_CACHE.pop(next(iter(_VISION_SCORE_CACHE)))
                _VISION_SCORE_CACHE[url] = score
            return score
        except Exception as e:
            logger.debug("MaterialAgent: 视觉LLM验证失败，降级: %s", e)
            return 0.5

    async def execute(
        self, input_data: MaterialInput, context: AgentContext
    ) -> MaterialOutput:
        try:
            scenes = input_data.script_skeleton.get("scenes", [])
            has_sources = len(MaterialRegistry.list()) > 0
            logger.info("MaterialAgent: %d 个场景, %d 个素材源", len(scenes), len(MaterialRegistry.list()))

            # ── 提取 Persona 视觉风格偏好 ──
            persona_style_keywords = self._extract_persona_style(input_data.persona_config)

            # ── 提取简报素材要求（类型/来源/偏好）──
            brief_material_hint = ""
            try:
                brief = input_data.creative_brief or {}
                mat_req = brief.get("material_requirements") or {}
                if isinstance(mat_req, dict):
                    parts = []
                    for k, label in (("type", "类型"), ("source", "来源"), ("preference", "偏好"), ("timeliness", "时效性")):
                        v = mat_req.get(k)
                        if v:
                            parts.append(f"{label}: {v}")
                    brief_material_hint = "；".join(parts)
            except Exception:
                pass

            if not scenes:
                # D7（PRD §19.5）：scenes 为空是**上游没给**，不是"素材阶段完成"。
                # 旧实现返回 PASS + 空候选，于是 editor 成为第一个失败点且归因 unknown。
                from clipwright.schema.agent import FailureReport

                _reason = "分镜场景为空：structure 没有产出可用 scenes"
                return MaterialOutput(
                    decision=AgentDecision.FAIL,
                    error=_reason,
                    failure=FailureReport(
                        category="input_insufficient",
                        blocking_point=_reason[:200],
                        retryable="needs_upstream",
                        needs=[{"from_stage": "structure", "field": "scenes",
                                "why": "素材匹配需要场景（title/description/时长）"}],
                        evidence=[{"kind": "empty_scenes", "text": "scenes=[]"}],
                    ),
                    candidate_clips=[],
                    material_notes=["无场景，无法匹配素材"],
                )

            if not has_sources:
                logger.warning("MaterialAgent: 无注册素材源，将使用文字占位")
                return MaterialOutput(
                    decision=AgentDecision.PASS,
                    # 无素材源仍然是"完成"（文字占位是合法产物），
                    # 但必须**显式登记降级**，让上层能区分"素材为空"与"素材正常"。
                    degradations=[{
                        "kind": "material_placeholder",
                        "detail": "无注册素材源，所有场景使用文字占位",
                        "scope": "global",
                        "impact": "成片以文字/纯色占位，画面质量显著下降",
                    }],
                    candidate_clips=[{
                        "scene_index": i,
                        "scene_title": scene.get("title", ""),
                        "suggested_assets": [],
                        "score": 0.0,
                        "note": "无注册素材源，将使用文字占位",
                    } for i, scene in enumerate(scenes)],
                    material_notes=["无注册素材源，所有场景使用文字占位"],
                )

            source_ids = context.extra_params.get("material_source_ids", None)
            if isinstance(source_ids, list) and len(source_ids) == 0:
                source_ids = None

            # orientation 此前只有读取点。来源：显式 extra → brief.asset_ratio（9:16 竖屏）
            pref_orientation = orientation_from_brief(
                input_data.creative_brief, context.extra_params
            )

            # ── 视觉 LLM 分析开关 ──
            # 显式插件配置优先；未显式配置时，LLM 已配置（含 Ollama base_url）则默认开启
            plugin_config = input_data.material_plugin_config or {}
            use_vision_llm = bool(plugin_config.get("enable_visual_llm", _llm_configured()))
            vision_frame_count = int(plugin_config.get("visual_llm_frame_count", 3))

            candidate_clips = []

            # ── 并行处理场景 ──
            # 每场景的 LLM 搜索词 + 搜索 + 校验相互独立 → 场景级有界并行（默认 4）
            scene_concurrency = max(1, int(getattr(settings, "material_concurrency", 4)))

            # 尝试一次 LLM 调用为全部场景生成搜索词（省 N-1 次往返）；失败回退逐场景
            batch_queries = await _llm_search_queries_batch(
                scenes,
                pipeline_id=context.pipeline_id,
                persona_style=" ".join(persona_style_keywords),
                brief_hint=brief_material_hint,
                category_hint=_category_search_hint(context.category_plugin_id),
            )

            sem = asyncio.Semaphore(scene_concurrency)

            async def _process_scene(i: int, scene: dict[str, Any]) -> dict[str, Any]:
                async with sem:
                    return await self._process_scene(
                        i, scene,
                        persona_style_keywords=persona_style_keywords,
                        brief_material_hint=brief_material_hint,
                        source_ids=source_ids,
                        pref_orientation=pref_orientation,
                        use_vision_llm=use_vision_llm,
                        vision_frame_count=vision_frame_count,
                        input_data=input_data,
                        pipeline_id=context.pipeline_id,
                        batch_query=batch_queries[i] if batch_queries else None,
                    )

            results = await asyncio.gather(
                *(_process_scene(i, s) for i, s in enumerate(scenes)),
                return_exceptions=True,
            )
            # 进度可见（现代 agent 化）：材料阶段是最长的一段（43 场逐场检索 + 视觉 LLM），
            # 主 agent 不该在 15 分钟里什么都看不到——场景级完成度实时进事件流。
            from clipwright.services.agent_context import emit_progress as _emit_progress

            _ok_scenes = sum(1 for r in results if not isinstance(r, Exception) and r is not None)
            _emit_progress(
                f"素材检索完成：{_ok_scenes}/{len(scenes)} 场有候选"
                f"（异常 {sum(1 for r in results if isinstance(r, Exception))} 场）",
                {"stage": "material", "scenes": len(scenes), "ok": _ok_scenes},
            )
            # 单场景异常降级为空候选占位，不让整 Agent 失败
            candidate_clips = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.warning("MaterialAgent: 场景[%d] 处理异常（降级占位）: %s", i, r)
                    add_event(context.pipeline_id, "material", "warning",
                              f"场景[{i}] 素材搜索异常，降级文字占位: {str(r)[:120]}")
                    candidate_clips.append({
                        "scene_index": i,
                        "scene_title": scenes[i].get("title", "") if i < len(scenes) else "",
                        "suggested_assets": [],
                        "score": 0.0,
                        "note": f"场景处理异常降级: {str(r)[:80]}",
                    })
                elif r is not None:
                    candidate_clips.append(r)

            notes: list[str] = []
            if use_vision_llm:
                notes.append(f"视觉LLM分析已启用 (每候选 {vision_frame_count} 帧)")

            # ── 候选素材**真下载**到工作区（material → edit 的缺口，2026-10-31 用户实测）──
            #
            # 症状：material 交了 43 场 / 187 条候选，`url` 全有、`local_path` 全空，
            # `materials/` 目录是空的；下游 edit 拿着 pexels 远程 URL 去 trim，
            # 于是 41 次空剪、最后按墙钟触闸收尾。
            #
            # 根因：素材源（Pexels/JSON）只给 URL，**没有任何地方把这些 URL 下载下来**，
            # 而 edit 阶段不应该做网络 I/O。所以下载落在 material：选完人就下，
            # 回填 `local_path`；下不动就如实登记降级（含真实原因），别让下游猜。
            download_stats = await self._download_candidates(candidate_clips, context)
            if download_stats.get("attempted"):
                ok_n = int(download_stats.get("ok") or 0)
                tried = int(download_stats.get("attempted") or 0)
                reason = str(download_stats.get("reason") or "")
                notes.append(
                    f"素材下载：成功 {ok_n}/{tried}"
                    + (f"；失败原因：{reason[:120]}" if ok_n < tried else "")
                )
                if ok_n < tried:
                    logger.warning(
                        "MaterialAgent: 素材下载失败 %d/%d（原因：%s）",
                        tried - ok_n, tried, reason[:200],
                    )

            # S 轨接线：MV 类型（或显式开启）时，把"每句落到哪个镜头"写回场景并留痕
            placement_stats = None
            if _placement_enabled(context):
                try:
                    placement_stats = annotate_mv_placement(
                        scenes,
                        candidate_clips,
                        bits_per_second=float((context.extra_params or {}).get("bpm") or 0) or None,
                    )
                except Exception as exc:  # noqa: BLE001 - 落位失败不得阻断选材
                    logger.warning("MaterialAgent: 素材落位失败（已跳过）: %s", exc)
                    placement_stats = None
            if placement_stats is not None:
                notes.append(
                    f"素材落位：{placement_stats['covered']}/{placement_stats['total']} 段有依据，"
                    f"缺口 {len(placement_stats['gaps'])}、授权不明 {len(placement_stats['rejected'])}"
                )

            # 场景级 note（如 C4c 重选结果）上浮到全局 material_notes
            for r in candidate_clips:
                notes.extend(r.pop("material_notes", []))

            # 占位如实上报（E2E 2026-10-30 用户裁定）：没有匹配到任何素材的场景，
            # 下游 edit 只能落文字/纯色卡——这是"回退画面"，必须登记成降级，
            # 而不是把 `suggested_assets=[]` 当正常产物交上去（主 agent 只有看见它，
            # 才可能重派 material 把占位换成真实画面）。
            placeholders = [
                (int(r.get("scene_index", i)), str(r.get("scene_title") or ""))
                for i, r in enumerate(candidate_clips)
                if not (r.get("suggested_assets") or [])
            ]
            if placeholders:
                scope_preview = "、".join(f"场景[{idx}]" for idx, _ in placeholders[:6])
                more = f" 等 {len(placeholders)} 场" if len(placeholders) > 6 else ""
                placeholder_degradations = [{
                    "kind": "material_placeholder",
                    "detail": f"{scope_preview}{more}无匹配素材，下游只能用文字/纯色占位",
                    "scope": f"scene:{idx}",
                    "impact": f"「{title[:30]}」该段画面是占位卡，不是实拍素材",
                } for idx, title in placeholders[:12]]
                # 下载失败也要登记：只有 URL 没有本地文件 = edit 拿不到东西剪，
                # 这是与"没有候选"不同的失败，主 agent 需要看见真实原因才能改策略。
                if int(download_stats.get("attempted") or 0) > int(download_stats.get("ok") or 0):
                    placeholder_degradations.append({
                        "kind": "material_download_failed",
                        "detail": (f"素材下载失败 {download_stats.get('attempted')}/"
                                   f"{download_stats.get('ok')}："
                                   f"{str(download_stats.get('reason') or '')[:120]}"),
                        "scope": "stage:material",
                        "impact": "候选只有远程 URL、没有本地文件，edit 无法裁剪真实素材",
                    })
                notes.append(
                    f"占位场景 {len(placeholders)}/{len(candidate_clips)} 个"
                    f"（{'、'.join(str(i) for i, _ in placeholders[:8])}）"
                )
                logger.warning(
                    "MaterialAgent: %d/%d 个场景无匹配素材 → 文字/纯色占位（已登记降级）",
                    len(placeholders), len(candidate_clips),
                )
                return MaterialOutput(
                    decision=AgentDecision.PASS,
                    degradations=placeholder_degradations,
                    candidate_clips=candidate_clips,
                    material_notes=notes,
                )

            # 没有占位场景、但下载失败同样要报：候选全是远程 URL + 本地无文件时，
            # edit 阶段只会产出空剪（2026-10-31 实测 41 次），主 agent 必须看见真原因。
            download_degradations: list[dict[str, Any]] = []
            if int(download_stats.get("attempted") or 0) > int(download_stats.get("ok") or 0):
                download_degradations.append({
                    "kind": "material_download_failed",
                    "detail": (f"素材下载失败：尝试 {download_stats.get('attempted')}、"
                               f"成功 {download_stats.get('ok')}；"
                               f"{str(download_stats.get('reason') or '')[:120]}"),
                    "scope": "stage:material",
                    "impact": "候选只有远程 URL、没有本地文件，edit 无法裁剪真实素材",
                })
            return MaterialOutput(
                decision=AgentDecision.PASS,
                degradations=download_degradations,
                candidate_clips=candidate_clips,
                material_notes=notes,
            )

        except Exception as e:
            logger.exception("MaterialAgent 失败: %s", e)
            return self.build_error_output(str(e), MaterialOutput)

    async def _download_candidates(
        self, candidate_clips: list[dict[str, Any]], context: AgentContext,
    ) -> dict[str, Any]:
        """把每个场景的**首选**候选真下载到工作区，回填 `local_path`。

        三条纪律：
        1. **从 material 下载，不让 edit 做网络 I/O**（edit 的职责是剪，不是抓）；
        2. **有预算**：每阶段最多 `_FETCH_MAX` 个、并发 `_FETCH_CONCURRENCY`，
           不做"43 场 × 每条候选 5 条"的全量下载；
        3. **连续失败就停**：离线环境里每个 URL 都要等超时，连续 `_FETCH_BREAKER`
           个失败说明网络不通——立刻停手并记录真实原因，别把阶段时间烧在等超时上。
        """
        import os

        root = str((getattr(context, "extra_params", None) or {}).get("workspace_root") or "")
        if not root:
            # 没有工作区就不下载（落点无处可去），如实说明
            return {"attempted": 0, "ok": 0, "reason": "no workspace_root"}
        try:
            from clipwright.services import web_assets
        except Exception as exc:  # noqa: BLE001 — 下载器不可用不阻断选材
            logger.warning("MaterialAgent: 素材下载器不可用（跳过下载）: %s", exc)
            return {"attempted": 0, "ok": 0, "reason": f"downloader unavailable: {exc}"}

        max_items = int(os.environ.get("CLIPWRIGHT_MATERIAL_FETCH_MAX", "48"))
        concurrency = int(os.environ.get("CLIPWRIGHT_MATERIAL_FETCH_CONCURRENCY", "3"))
        breaker_after = int(os.environ.get("CLIPWRIGHT_MATERIAL_FETCH_BREAKER", "5"))
        # 单文件上限：素材站的"原始尺寸"直链常见 20–40MB，20MB 的默认上限会把它们全挡掉
        max_bytes = int(os.environ.get("CLIPWRIGHT_MATERIAL_FETCH_MAX_BYTES", str(60 * 1024 * 1024)))

        targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        skipped_images = 0
        _VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")
        _IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
        for clip in candidate_clips:
            for asset in (clip.get("suggested_assets") or [])[:1]:
                if not isinstance(asset, dict):
                    continue
                if str(asset.get("local_path") or "").strip():
                    continue
                url = str(asset.get("url") or "")
                if not url.startswith(("http://", "https://")):
                    continue
                # 只下**视频**：编辑阶段只会 trim 视频（图片目前没有"静帧成片"的工具能力），
                # 把图片也下下来只会占盘 + 让 edit 白试一次。图片候选保留 URL 供人工/后续使用。
                kind = str(asset.get("type") or "")
                is_video = kind == "video" or url.split("?")[0].lower().endswith(_VIDEO_EXT)
                if not is_video:
                    skipped_images += 1
                    continue
                targets.append((clip, asset))
        if not targets:
            return {"attempted": 0, "ok": 0, "reason": "", "skipped_images": skipped_images}
        targets = targets[:max_items]

        stats: dict[str, Any] = {"attempted": 0, "ok": 0, "reason": "", "skipped_images": skipped_images}
        stop = {"value": False}
        consecutive_fail = {"n": 0}
        lock = asyncio.Lock()
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _one(clip: dict[str, Any], asset: dict[str, Any]) -> None:
            async with sem:
                async with lock:
                    if stop["value"]:
                        return
                    stats["attempted"] += 1
                res = None
                err = ""
                # 单条最多试 2 次：CDN 抖动很常见（实测同一批 4 成功、3 个 ConnectError），
                # 一次连不上就判该素材不可用太浪费；但也绝不多试，离线时不能等超时。
                for attempt in range(2):
                    try:
                        res = await web_assets.fetch_and_store(
                            str(asset.get("url") or ""),
                            root=root,
                            title=str(asset.get("title") or ""),
                            # 这是素材源 API 直接给的媒体文件地址，不是爬站页面：
                            # 跳过 robots.txt 往返（SSRF/域名治理仍在 fetch_and_store 里）
                            skip_robots=True,
                            max_bytes=max_bytes,
                        )
                    except Exception as exc:  # noqa: BLE001 — 单条失败如实计
                        res = None
                        err = f"{type(exc).__name__}: {exc}"[:200]
                    else:
                        err = "" if getattr(res, "ok", False) else str(getattr(res, "error", "") or "")
                    if res is not None and getattr(res, "ok", False):
                        break
                    if attempt == 0:
                        await asyncio.sleep(0.8)
                async with lock:
                    if res is not None and getattr(res, "ok", False):
                        asset["local_path"] = str(getattr(res, "local_path", "") or "")
                        asset["local_bytes"] = int(getattr(res, "bytes", 0) or 0)
                        asset["local_sha256"] = str(getattr(res, "sha256", "") or "")
                        stats["ok"] += 1
                        consecutive_fail["n"] = 0
                        return
                    consecutive_fail["n"] += 1
                    if not stats["reason"] and err:
                        stats["reason"] = err
                    if consecutive_fail["n"] >= breaker_after:
                        stop["value"] = True

        await asyncio.gather(*(_one(c, a) for c, a in targets), return_exceptions=True)
        if stop["value"]:
            stats["reason"] = (stats["reason"] or "连续下载失败") + "（连续失败即停）"
        logger.info(
            "MaterialAgent: 素材下载 %d/%d 成功（预算 %d，并发 %d）",
            stats["ok"], stats["attempted"], max_items, concurrency,
        )
        return stats

    async def _process_scene(
        self,
        i: int,
        scene: dict[str, Any],
        persona_style_keywords: list[str],
        brief_material_hint: str,
        source_ids: list[str] | None,
        pref_orientation: str,
        use_vision_llm: bool,
        vision_frame_count: int,
        input_data: MaterialInput,
        pipeline_id: str,
        batch_query: list[str] | None = None,
    ) -> dict[str, Any]:
        """处理单个场景：LLM 搜索词（或批量结果）→ 搜索 → 帧校验 → 打分排序。"""
        scene_title = scene.get("title", "")
        scene_keywords = scene.get("keywords", [])
        description = scene.get("description", "")

        # 场景级旁白（仅本场景，不引入整脚本旁白）+ 素材意图（内容/偏好）
        narration_text = scene.get("voiceover_script") or description
        visual_desc = scene.get("visual_description", {}) or {}
        material_intent = " ".join(filter(None, [
            (visual_desc.get("material_content") or "").strip(),
            (visual_desc.get("material_preference") or "").strip(),
        ]))

        search_queries = batch_query or await _llm_search_queries(
            scene_title, scene_keywords, description,
            pipeline_id=pipeline_id,
            persona_style=" ".join(persona_style_keywords),
            brief_hint=brief_material_hint,
        )

        all_results = []
        seen_ids = set()

        async def _run_queries(queries: list[str]) -> None:
            for query in queries:
                results = await _search_with_cache(
                    query, top_k=5, source_ids=source_ids if isinstance(source_ids, list) else None,
                )
                for r in results:
                    rid = r.asset.id if hasattr(r, 'asset') else r.get("asset_id", "")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        all_results.append(r)
                if len(all_results) >= 15:
                    break

        await _run_queries(search_queries)

        # 中文查询兜底（E2E 2026-10-28）：Pexels/Unsplash/Pixabay 是英文索引，
        # 中文关键词命中率为 0 —— 43 个场景全空，剪辑只能退化成"背景+文字"。
        # 一条都没搜到时，把查询翻成英文再搜一轮（有界：每个场景最多一次）。
        if not all_results and any(_has_cjk(q) for q in search_queries):
            translated = await _to_english_queries(search_queries, pipeline_id=pipeline_id)
            if translated:
                logger.info(
                    "MaterialAgent: 场景[%d] 中文查询零命中，改用英文重搜: %s",
                    i, translated[:5],
                )
                await _run_queries(translated)

        logger.info("MaterialAgent: 场景[%d] 去重后 %d 条候选", i, len(all_results))

        validated = []
        # 帧校验并行：对前 4 个候选用有界 gather 校验（限制额外 API 开销），
        # 选择逻辑保持「原顺序第一个 >0.3 命中」，与串行版结果一致。
        # _validate 提升到场景级，供首轮与 C4c 重选复用同一校验路径。
        validate_sem = asyncio.Semaphore(4)

        async def _validate(r):
            async with validate_sem:
                if use_vision_llm:
                    asset_obj = r.asset if hasattr(r, 'asset') else r
                    score = await self._validate_via_vision_llm(
                        asset_obj,
                        scene_title,
                        scene_keywords,
                        description,
                        narration_text=narration_text,
                        material_intent=material_intent,
                        frame_count=vision_frame_count,
                    )
                else:
                    video_url = ""
                    if hasattr(r, 'asset'):
                        video_url = r.asset.url or r.asset.local_path or ""
                    else:
                        video_url = r.get("url", "") or r.get("local_path", "")
                    if not video_url:
                        return None
                    # 合并：传结果对象（union _validate_video_frame 兼容对象/URL，
                    # 对象形态可走标题启发式兜底，gate_off 场景可得高分）
                    score = await _validate_video_frame(
                        r, f"{scene_title} {' '.join(scene_keywords)}"
                    )
                return (r, score)

        # E2E 2026-10-28：视觉 LLM 打分是**每候选一次多模态调用**。43 场景 × 8 候选
        # × （首轮 + 重选）会把 material 阶段顶到 15 分钟超时（实测超时后主 agent
        # 重试一次，白烧 30 分钟）。有视觉打分时只看前 2 个，够排序用。
        top_candidates = all_results[:2] if use_vision_llm else all_results[:8]
        if top_candidates:
            scores = await asyncio.gather(*(_validate(r) for r in top_candidates))
            for s in scores:
                if s is not None:
                    validated.append(s)

        if not validated:
            validated = [(r, 0.5) for r in all_results[:5]]

        def _orientation_score(asset_obj) -> float:
            resolution = ""
            if hasattr(asset_obj, 'resolution'):
                resolution = asset_obj.resolution or ""
            elif isinstance(asset_obj, dict):
                resolution = asset_obj.get("resolution", "")
            w, h = 0, 0
            if isinstance(resolution, str) and "x" in resolution:
                try:
                    parts = resolution.split("x")
                    w, h = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
            if w <= 0 or h <= 0:
                return 0.5
            orient = "landscape" if w > h else "portrait"
            match = 1.0 if orient == pref_orientation else 0.3
            quality = min(1.0, (w * h) / (1920 * 1080))
            return match * 0.7 + quality * 0.3

        # 无视觉结果（未启用视觉/校验全失败/占位 0.5）时，
        # 排序键用本地文本相关性分替代占位 0.5；真实校验分（含 frame_validator）仍优先。
        def _match_sort_score(r, ms: float) -> float:
            if ms == 0.5:
                asset_obj = r.asset if hasattr(r, "asset") else r
                return _text_relevance_score(
                    narration_text, material_intent, scene_keywords, asset_obj
                )
            return ms

        # 首轮与 C4c 重选共用：排序 → 取前 5 → 最优分（复用 Todo 8 的排序键逻辑）
        def _rank_candidates(cands: list) -> tuple[list, float]:
            # Persona 风格匹配分（每个候选项使用自身的 tags）
            cands.sort(key=lambda x: (
                _match_sort_score(x[0], x[1]) * 0.5 +          # 帧验证匹配度（占位 → 文本相关性）
                _orientation_score(x[0]) * 0.25 +               # 方向优先级
                self._persona_style_score(
                    x[0],
                    x[0].asset.tags if hasattr(x[0], 'asset') and hasattr(x[0].asset, 'tags') else [],
                    persona_style_keywords,
                    input_data.persona_config,
                ) * 0.25                                         # Persona 风格匹配度
            ), reverse=True)

            sug = []
            for r, ms in cands[:5]:
                if hasattr(r, 'asset'):
                    sug.append({
                        "asset_id": r.asset.id,
                        "title": r.asset.title,
                        "type": r.asset.type,
                        "url": r.asset.url,
                        "local_path": r.asset.local_path,
                        "score": round(ms, 3),
                        "source": r.source_name if hasattr(r, 'source_name') else "",
                        "duration_sec": r.asset.duration_sec,
                        "tags": r.asset.tags,
                        # 轮 82（版权链）：授权信息透传到候选/时间线，商业交付可追溯
                        "license": getattr(r.asset, "license", ""),
                        "license_url": getattr(r.asset, "license_url", ""),
                        "author": getattr(r.asset, "author", ""),
                        "source_url": getattr(r.asset, "source_url", ""),
                    })
                else:
                    sug.append({
                        "asset_id": r.get("asset_id", ""),
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "local_path": r.get("local_path", ""),
                        "score": round(ms, 3),
                        "duration_sec": r.get("duration_sec", 0),
                        "tags": r.get("tags", []),
                        "license": r.get("license", ""),
                        "license_url": r.get("license_url", ""),
                        "author": r.get("author", ""),
                        "source_url": r.get("source_url", ""),
                    })
            best = max((ms for _, ms in cands), default=0.0)
            return sug, best

        suggested, best_score = _rank_candidates(validated)

        # ── C4c 有界重选循环：首轮最优分低于阈值 → 至多一次补充搜索（防无限成本 D9）──
        reselect_meta = {"triggered": False, "query": "", "improved_score": 0.0}
        scene_notes: list[str] = []
        plugin_config = input_data.material_plugin_config or {}
        threshold = float(plugin_config.get("reselect_threshold", _RESELECT_THRESHOLD))
        # 注意：**不要**因为"首轮全是 0 分"就跳过重选——重选用的是另一条由旁白
        # 推导的查询（C4c），不同查询的命中率并不相关（实测首轮 0 分的场景，
        # 重选仍能拿到可用素材）。
        if best_score < threshold and validated:
            reselect_query = _derive_reselect_query(narration_text, material_intent, scene_keywords)
            reselect_meta["triggered"] = True
            reselect_meta["query"] = reselect_query
            logger.info(
                "MaterialAgent: 场景[%d] 触发重选 阈值=%.2f 当前=%.2f 查询=%s",
                i, threshold, best_score, reselect_query,
            )
            if reselect_query:
                try:
                    reselect_candidates = []
                    new_results = await _search_with_cache(
                        reselect_query, top_k=5,
                        source_ids=source_ids if isinstance(source_ids, list) else None,
                    )
                    for r in new_results:
                        rid = r.asset.id if hasattr(r, 'asset') else r.get("asset_id", "")
                        if rid and rid not in seen_ids:
                            seen_ids.add(rid)
                            reselect_candidates.append(r)
                    reselect_validated = []
                    if reselect_candidates:
                        scores2 = await asyncio.gather(*(_validate(r) for r in reselect_candidates))
                        for s in scores2:
                            if s is not None:
                                reselect_validated.append(s)
                    if reselect_validated:
                        old_best = best_score
                        validated.extend(reselect_validated)
                        suggested, best_score = _rank_candidates(validated)
                        reselect_meta["improved_score"] = best_score
                        if best_score > old_best:
                            note = f"已按文案重选素材（{reselect_query}）: {old_best:.2f} → {best_score:.2f}"
                            scene_notes.append(note)
                        else:
                            note = f"重选未改善（{reselect_query}）: 保持原素材 {best_score:.2f}"
                            scene_notes.append(note)
                            logger.warning("MaterialAgent: 场景[%d] %s", i, note)
                    else:
                        reselect_meta["improved_score"] = best_score
                        scene_notes.append(
                            f"重选无新增候选（{reselect_query}），保持原素材 {best_score:.2f}"
                        )
                except Exception as e:
                    reselect_meta["improved_score"] = best_score
                    logger.warning("MaterialAgent: 场景[%d] 重选失败: %s", i, e)
                    scene_notes.append(f"重选失败（{reselect_query}），保持原素材 {best_score:.2f}")
            else:
                # 无旁白/意图/关键词可派生查询 → 无词可搜，跳过重选
                reselect_meta["triggered"] = False

        return {
            "scene_index": i,
            "scene_title": scene_title,
            "suggested_assets": suggested,
            "score": best_score,
            "query": " | ".join(search_queries),
            "reselect": reselect_meta,
            "material_notes": scene_notes,
        }

