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

# 搜索缓存: query_hash → list[result]
_search_cache: dict[str, list[dict]] = {}
_SEARCH_CACHE_MAX = 200

# 素材校验有界重试：单场景最多重试次数 + 触发重试的最低分数阈值
_MAX_VALIDATION_RETRIES = 2
_VALIDATION_THRESHOLD = 0.35

# 启发式分词：拉丁词 + CJK 字符
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+")


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


class _CachedResult:
    """缓存结果的轻量封装，使属性访问与 AssetResult 对象一致。"""
    __slots__ = ("asset", "source_name", "score")
    class _Asset:
        __slots__ = ("id", "title", "url", "local_path", "duration_sec", "tags")
        def __init__(self, d: dict):
            self.id = d["asset_id"]
            self.title = d.get("title", "")
            self.url = d.get("url", "")
            self.local_path = d.get("local_path", "")
            self.duration_sec = d.get("duration_sec", 0)
            self.tags = d.get("tags", [])
    def __init__(self, d: dict):
        self.asset = self._Asset(d)
        self.source_name = d.get("source_name", "")
        self.score = d.get("score", 0.0)


async def _llm_search_queries_batch(
    scenes: list[dict[str, Any]],
    pipeline_id: str = "",
    persona_style: str = "",
    brief_hint: str = "",
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

    prompt = (
        "你是一个视频素材搜索关键词生成器。为以下每个场景分别生成 3-5 个具体、"
        "可搜索的视觉关键词，用于在视频素材库搜索 B-roll 画面。\n\n"
        + "\n".join(scene_lines) +
        "\n\n要求：\n"
        "- 每个关键词必须是**具体的视觉画面**（如'城市夜景''键盘打字'），不能是抽象概念\n"
        "- 优先推荐实拍风格画面\n"
        "- 按场景编号输出，每行格式：\"场景N: 关键词1/关键词2/关键词3\"\n"
        "- 只输出关键词行，不要额外说明"
        f"{style_hint}{brief_hint_text}"
    )

    llm = LLMService()
    try:
        resp = await asyncio.wait_for(llm.ask(prompt, use_flash=True), timeout=45)
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
    retry_hint: bool = False,
) -> list[str]:
    """调用 LLM 生成具体视觉搜索词（考虑 Persona 风格 + 简报素材偏好）。

    retry_hint: 重试时置 True，提示 LLM 换一个角度/方向重新生成，避免重复词。
    """
    from clipwright.services.llm import LLMService

    style_hint = f"\n- 优先匹配风格: {persona_style}" if persona_style else ""
    brief_hint_text = f"\n- 简报素材要求: {brief_hint}" if brief_hint else ""
    retry_hint_text = (
        "\n- 上次生成的关键词未能命中合适素材，请换一个角度/场景方向重新生成，"
        "避免与上次雷同" if retry_hint else ""
    )

    prompt = (
        f"你是一个视频素材搜索关键词生成器。根据以下场景信息，生成 3-5 个具体、可搜索的视觉关键词，"
        f"用于在视频素材库中搜索合适的 B-roll 画面。\n\n"
        f"场景标题: {scene_title}\n"
        f"关键词: {', '.join(keywords)}\n"
        f"场景描述: {description}\n\n"
        f"要求：\n"
        f"- 每个关键词必须是**具体的视觉画面**（如'城市夜景''键盘打字''街头涂鸦'），"
        f"不能是抽象概念（如'社会矛盾''心理防御'）\n"
        f"- 优先推荐实拍风格的画面\n"
        f"- 用中文输出，每行一个关键词\n"
        f"- 只输出关键词，不要序号和说明{retry_hint_text}{style_hint}{brief_hint_text}"
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
        resp = await asyncio.wait_for(llm.ask(prompt, use_flash=True), timeout=30)
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


async def _validate_video_frame(asset: Any, expected_text: str) -> float:
    """校验单个素材候选是否匹配场景，返回 0-1 分（非视觉路径）。

    校验顺序：
    1. 素材无可访问 URL → 0.0（无法校验，不作为最优候选）。
    2. 尝试注册的 frame_validator 工具：若返回真实分数（含 match_score）则使用，
       全黑帧返回 0.0；若工具未注册或未返回真实分数则回退启发式。
    3. 启发式回退（_heuristic_title_match_score）：用素材 title+tags 与
       场景 expected_text（场景标题 + 关键词）做 token/F1 重叠打分：
       完全无关 → 0.0；充分覆盖 → 接近 1.0。
    """
    title, tags, video_url = _asset_parts(asset)
    if not video_url:
        return 0.0
    try:
        from clipwright.tool.registry import ToolRegistry
        tool = ToolRegistry.get("frame_validator")
        if tool is not None:
            result = await asyncio.wait_for(
                tool.execute(video_url=video_url, expected_text=expected_text),
                timeout=15,
            )
            output = result.output or {}
            if output.get("is_blank"):
                logger.debug("MaterialAgent: 跳过全黑帧 %s", video_url[:40])
                return 0.0
            if "match_score" in output:
                try:
                    return float(output["match_score"])
                except (TypeError, ValueError):
                    pass
    except asyncio.TimeoutError:
        logger.debug("MaterialAgent: 帧验证超时 %s", video_url[:40])
    except Exception as e:
        logger.debug("MaterialAgent: 帧验证失败 %s: %s", video_url[:40], e)
    return _heuristic_title_match_score(title, tags, expected_text)


def _search_cache_key(query: str, source_ids: list[str] | None) -> str:
    raw = f"{query}:{json.dumps(source_ids or [], sort_keys=True)}"
    return hashlib.md5(raw.encode()).hexdigest()


async def _search_with_cache(
    query: str,
    top_k: int = 5,
    source_ids: list[str] | None = None,
) -> list[Any]:
    """带缓存的素材搜索。"""
    global _search_cache
    key = _search_cache_key(query, source_ids)
    if key in _search_cache:
        logger.debug("MaterialAgent: 缓存命中 %s", query[:30])
        return [_CachedResult(d) for d in _search_cache[key]]

    results = await MaterialRegistry.search(
        query=query,
        top_k_per_source=top_k,
        source_ids=source_ids,
    )
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
            "source_name": r.source_name,
            "score": r.score,
        })

    if len(_search_cache) < _SEARCH_CACHE_MAX:
        _search_cache[key] = cached
    return results


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
        frame_count: int = 3,
    ) -> float:
        """使用视觉 LLM 分析候选素材帧内容，返回语义匹配分 (0-1)。"""
        try:
            from clipwright.tool.registry import ToolRegistry
            result = await ToolRegistry.execute(
                "vision_llm",
                asset=asset,
                scene_context={
                    "title": scene_title,
                    "keywords": scene_keywords,
                    "description": scene_description,
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
            return float(score)
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
                return MaterialOutput(
                    decision=AgentDecision.PASS,
                    candidate_clips=[],
                    material_notes=["无场景，跳过素材匹配"],
                )

            if not has_sources:
                logger.warning("MaterialAgent: 无注册素材源，将使用文字占位")
                return MaterialOutput(
                    decision=AgentDecision.PASS,
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

            pref_orientation = context.extra_params.get("orientation", "landscape")

            # ── 视觉 LLM 分析开关 ──
            plugin_config = input_data.material_plugin_config or {}
            use_vision_llm = bool(plugin_config.get("enable_visual_llm", False))
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

            results = await asyncio.gather(*(_process_scene(i, s) for i, s in enumerate(scenes)))
            candidate_clips = [r for r in results if r is not None]

            notes: list[str] = []
            if use_vision_llm:
                notes.append(f"视觉LLM分析已启用 (每候选 {vision_frame_count} 帧)")

            return MaterialOutput(
                decision=AgentDecision.PASS,
                candidate_clips=candidate_clips,
                material_notes=notes,
            )

        except Exception as e:
            logger.exception("MaterialAgent 失败: %s", e)
            return self.build_error_output(str(e), MaterialOutput)

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
        """处理单个场景：LLM 搜索词（或批量结果）→ 搜索 → 帧校验 → 打分排序 + 有界重试。"""
        scene_title = scene.get("title", "")
        scene_keywords = scene.get("keywords", [])
        description = scene.get("description", "")

        search_queries = batch_query or await _llm_search_queries(
            scene_title, scene_keywords, description,
            pipeline_id=pipeline_id,
            persona_style=" ".join(persona_style_keywords),
            brief_hint=brief_material_hint,
        )

        all_results = []
        seen_ids = set()
        for query in search_queries:
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

        logger.info("MaterialAgent: 场景[%d] 去重后 %d 条候选", i, len(all_results))

        validated: list[tuple[Any, float]] = []
        validated_ids: set[str] = set()
        validate_sem = asyncio.Semaphore(4)

        def _asset_id_of(r) -> str:
            """提取候选素材 ID（兼容对象与 dict）。"""
            return r.asset.id if hasattr(r, 'asset') else r.get("asset_id", "")

        def _video_url_of(r) -> str:
            """提取候选素材的可访问 URL（兼容对象与 dict）。"""
            if hasattr(r, 'asset'):
                return r.asset.url or r.asset.local_path or ""
            return r.get("url", "") or r.get("local_path", "")

        async def _validate(r):
            """按 gate 校验单个候选，返回 (候选, 分数) 或 None。"""
            async with validate_sem:
                if use_vision_llm:
                    asset_obj = r.asset if hasattr(r, 'asset') else r
                    score = await self._validate_via_vision_llm(
                        asset_obj,
                        scene_title,
                        scene_keywords,
                        description,
                        frame_count=vision_frame_count,
                    )
                else:
                    if not _video_url_of(r):
                        return None
                    score = await _validate_video_frame(
                        r, f"{scene_title} {' '.join(scene_keywords)}"
                    )
                return (r, score)

        async def _validate_batch(candidates: list[Any]) -> None:
            """有界并行校验一组候选并合并进 validated。"""
            scores = await asyncio.gather(*(_validate(r) for r in candidates))
            for s in scores:
                if s is not None:
                    rid = _asset_id_of(s[0])
                    if rid:
                        validated_ids.add(rid)
                    validated.append(s)

        # 帧校验并行：对前 8 个候选用有界 gather 校验（限制额外 API 开销）
        top_candidates = all_results[:8]
        if top_candidates:
            await _validate_batch(top_candidates)

        if not validated:
            validated = [(r, 0.5) for r in all_results[:5]]

        # ── 有界重试：最优校验分低于阈值时换素材/换搜索词，最多 _MAX_VALIDATION_RETRIES 次 ──
        retries_used = 0
        retried = False
        validation_note = ""
        while retries_used < _MAX_VALIDATION_RETRIES:
            best_score = max((ms for _, ms in validated), default=0.0)
            if best_score >= _VALIDATION_THRESHOLD:
                break
            retries_used += 1
            retried = True
            if retries_used == 1:
                # 尝试 1：换素材 — 从未校验过的候选中取下一个（搜索序）重新校验
                swap_candidates = [
                    r for r in all_results if _asset_id_of(r) not in validated_ids
                ][:4]
                if pipeline_id:
                    add_event(pipeline_id, "material", "validation_retry",
                              f"场景[{i}] 校验分不足({best_score:.2f})，尝试1: 换素材重新校验")
                if not swap_candidates:
                    continue
                await _validate_batch(swap_candidates)
            else:
                # 尝试 2：换搜索词 — 重新生成搜索词并搜索新候选
                if pipeline_id:
                    add_event(pipeline_id, "material", "validation_retry",
                              f"场景[{i}] 校验分不足({best_score:.2f})，尝试2: 重新生成搜索词")
                retry_queries = await _llm_search_queries(
                    scene_title, scene_keywords, description,
                    pipeline_id=pipeline_id,
                    persona_style=" ".join(persona_style_keywords),
                    brief_hint=brief_material_hint,
                    retry_hint=True,
                )
                new_results: list[Any] = []
                for query in retry_queries:
                    results = await _search_with_cache(
                        query, top_k=5,
                        source_ids=source_ids if isinstance(source_ids, list) else None,
                    )
                    for r in results:
                        rid = _asset_id_of(r)
                        if rid and rid not in seen_ids:
                            seen_ids.add(rid)
                            new_results.append(r)
                if new_results:
                    await _validate_batch(new_results[:8])

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

        # P2: Persona 风格匹配分（每个候选项使用自身的 tags）
        validated.sort(key=lambda x: (
            x[1] * 0.5 +                                    # 帧验证匹配度
            _orientation_score(x[0]) * 0.25 +               # 方向优先级
            self._persona_style_score(
                x[0],
                x[0].asset.tags if hasattr(x[0], 'asset') and hasattr(x[0].asset, 'tags') else [],
                persona_style_keywords,
                input_data.persona_config,
            ) * 0.25                                         # Persona 风格匹配度
        ), reverse=True)

        suggested = []
        for r, ms in validated[:5]:
            if hasattr(r, 'asset'):
                suggested.append({
                    "asset_id": r.asset.id,
                    "title": r.asset.title,
                    "type": r.asset.type,
                    "url": r.asset.url,
                    "local_path": r.asset.local_path,
                    "score": round(ms, 3),
                    "source": r.source_name if hasattr(r, 'source_name') else "",
                    "duration_sec": r.asset.duration_sec,
                    "tags": r.asset.tags,
                })
            else:
                suggested.append({
                    "asset_id": r.get("asset_id", ""),
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "local_path": r.get("local_path", ""),
                    "score": round(ms, 3),
                    "duration_sec": r.get("duration_sec", 0),
                    "tags": r.get("tags", []),
                })

        best_score = max((ms for _, ms in validated), default=0.0)
        if retried:
            validation_note = f"retry_{retries_used}_best_score_{best_score:.2f}"
        return {
            "scene_index": i,
            "scene_title": scene_title,
            "suggested_assets": suggested,
            "score": best_score,
            "query": " | ".join(search_queries),
            "retried": retried,
            "validation_note": validation_note,
        }
