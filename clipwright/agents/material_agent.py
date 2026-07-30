"""素材 Agent（MaterialAgent）— 素材智能匹配 + 缓存 + 降级。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.config import logger
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


async def _llm_search_queries(
    scene_title: str,
    keywords: list[str],
    description: str,
    pipeline_id: str = "",
    persona_style: str = "",
) -> list[str]:
    """调用 LLM 生成具体视觉搜索词（考虑 Persona 风格）。"""
    from clipwright.services.llm import LLMService

    style_hint = f"\n- 优先匹配风格: {persona_style}" if persona_style else ""

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
        f"- 只输出关键词，不要序号和说明{style_hint}"
    )
    if pipeline_id:
        add_event(pipeline_id, "material", "llm", f"LLM 搜索词生成: {scene_title}")

    llm = LLMService()
    try:
        import asyncio
        resp = await asyncio.wait_for(llm.ask(prompt), timeout=30)
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


async def _validate_video_frame(video_url: str, expected_text: str) -> float:
    """用视觉服务验证视频帧（占位 — 委托 FrameValidator 或视觉模型）。"""
    if not video_url:
        return 0.5
    try:
        import asyncio
        # 尝试从 tool stubs 获取 FrameValidator，不存在则跳过
        from clipwright.tool.registry import ToolRegistry
        tool = ToolRegistry.get("frame_validator")
        if tool is None:
            return 0.5
        result = await asyncio.wait_for(
            tool.execute(video_url=video_url, expected_text=expected_text),
            timeout=15,
        )
        output = result.output or {}
        if output.get("is_blank"):
            logger.debug("MaterialAgent: 跳过全黑帧 %s", video_url[:40])
            return 0.0
        return output.get("match_score", 0.5)
    except asyncio.TimeoutError:
        logger.debug("MaterialAgent: 帧验证超时 %s", video_url[:40])
        return 0.3
    except Exception as e:
        logger.debug("MaterialAgent: 帧验证失败 %s: %s", video_url[:40], e)
        return 0.0


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

            for i, scene in enumerate(scenes):
                scene_title = scene.get("title", "")
                scene_keywords = scene.get("keywords", [])
                description = scene.get("description", "")

                search_queries = await _llm_search_queries(
                    scene_title, scene_keywords, description,
                    pipeline_id=context.pipeline_id,
                    persona_style=persona_style_keywords,
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

                validated = []
                for r in all_results[:8]:
                    if use_vision_llm:
                        asset_obj = r.asset if hasattr(r, 'asset') else r
                        match_score = await self._validate_via_vision_llm(
                            asset_obj,
                            scene_title,
                            scene_keywords,
                            description,
                            frame_count=vision_frame_count,
                        )
                    else:
                        video_url = ""
                        if hasattr(r, 'asset'):
                            video_url = r.asset.url or r.asset.local_path or ""
                        else:
                            video_url = r.get("url", "") or r.get("local_path", "")
                        if not video_url:
                            continue
                        match_score = await _validate_video_frame(
                            video_url, f"{scene_title} {' '.join(scene_keywords)}"
                        )
                    validated.append((r, match_score))
                    if match_score > 0.3:
                        break

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
                candidate_clips.append({
                    "scene_index": i,
                    "scene_title": scene_title,
                    "suggested_assets": suggested,
                    "score": best_score,
                    "query": " | ".join(search_queries),
                })

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
