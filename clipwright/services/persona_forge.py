"""PersonaForge — Persona 智能构建器。

对应 Persona.md 中的 Persona 生成 Agent。
支持三种构建模式：
- 对话引导：LLM 提问 → 用户回答 → 生成
- 自然语言描述：用户描述风格 → LLM 映射为参数
- 脚本分析：上传脚本/口播文本 → 提取语言层参数 + 论证结构

所有模式都通过 IsoBase LLM 服务驱动。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from clipwright.persona.loader import load_persona_by_id
from clipwright.persona.repository import PersonaRepository
from clipwright.schema.persona import (
    AudioConfig,
    ConstraintsConfig,
    IdentityConfig,
    LanguageConfig,
    ParameterLayer,
    PersonaManifest,
    RhythmConfig,
    VisualConfig,
)
from clipwright.services.llm import LLMService

# ── 提示词模板 ──────────────────────────────────────

SYSTEM_FORGE_PROMPT = """你是一个专业的视频创作者风格分析师。你的任务是将用户对创作风格的描述，精确映射为结构化的 Persona 配置参数。

Persona 参数包含以下维度：
1. identity: 创作者身份（语调、立场、知识领域）
2. language: 语言风格（学术密度、网络用语比例、句长、句式模式）
3. rhythm: 剪辑节奏（节奏配置、基准镜头时长、密度等级）
4. visual: 视觉风格（配色方案、动画风格、转场偏好）
5. audio: 音频风格（BGM 使用模式、目标响度）
6. constraints: 创作约束（最长时长、引用要求、禁用主题）

注意：
- 参数值必须合理、互不矛盾
- 每个字段附带 confidence (0-1) 标记推断可靠程度
- 从文本中明确提到的信息 confidence 给 0.8-1.0
- 从文本暗示推断的 confidence 给 0.5-0.7
- 完全默认的字段 confidence 给 0.1-0.3

返回格式为 JSON，键名与上述维度一致。"""


SYSTEM_SCRIPT_ANALYSIS_PROMPT = """你是一个文本风格分析师。分析用户提供的脚本/口播文本，提取以下量化指标和质化特征：

## 量化指标
- academic_density: 学术/专业术语占比 (0-1)
- slang_ratio: 网络用语/口语化表达占比 (0-1)
- max_sentence_len: 最长句子的字数
- avg_sentence_len: 平均句长
- sentence_variance_target: 句子长度方差 (0-1)
- rhetorical_question_ratio: 反问句比例 (0-1)
- first_person_ratio: 第一人称比例 (0-1)
- second_person_ratio: 第二人称比例 (0-1)
- imperative_ratio: 祈使句比例 (0-1)

## 质化特征
- tone: 整体语调标签
- forbidden_patterns: 检测到的陈词滥调/冗余表达模式
- allowed_patterns: 检测到的特色表达模式

## 论证结构
- structure_type: 论证结构类型（如：problem_solution / hook_explanation_conclusion / narrative_arc）
- sections: 检测到的段落功能标签列表（hook / body_theory / body_evidence / body_counterargument / conclusion / real_world_return）
- hook_to_body_ratio: 破题段占全文比例
- theory_density_peak_position: 理论密度峰值位置（0-1）
- real_world_return_position: 回到现实位置（0-1）

返回严格 JSON 格式，包含上述所有字段。"""


SYSTEM_DIALOGUE_PROMPT = """你是一个视频创作风格顾问。你的任务是通过一系列问题，引导用户明确自己的创作风格。

每次回答后，追问最多 2 个相关问题来缩小范围。当收集到足够信息后，生成完整的 Persona 配置。

问题覆盖以下维度（每次选 1-2 个提问）：
1. 身份与语调：你想营造什么形象？冷峻/热情/学术/吐槽？
2. 语言风格：用词偏向专业还是通俗？句子长度偏好？
3. 剪辑节奏：视频偏快还是偏慢？停顿多还是信息密集？
4. 视觉风格：主色调？文字动画偏好？转场习惯？
5. 音频风格：BGM 选择倾向？音量偏好？
6. 创作约束：视频时长范围？是否有禁用内容？

当用户说"生成"或信息足够时，输出完整 JSON Persona 配置。
当前已收集的信息：{collected_info}
"""


class PersonaForge:
    """Persona 智能构建器。"""

    def __init__(self) -> None:
        self._llm = LLMService()
    # ── 模式 A：自然语言描述 → Persona ─────────────────

    async def from_prompt(
        self,
        description: str,
        persona_id: str,
        persona_name: str = "",
    ) -> PersonaManifest:
        """将用户对创作风格的自然语言描述映射为 Persona 参数层。

        Args:
            description: 用户对风格的描述文本
            persona_id: 生成的 Persona 唯一标识
            persona_name: 生成的 Persona 名称
        """
        if not self._has_api_key():
            return self._fallback_persona(persona_id, persona_name, description)

        user_prompt = (
            f"请根据以下对视频创作风格的描述，生成 Persona 参数层配置：\n\n"
            f"{description}\n\n"
            f"persona_id: {persona_id}\n"
            f"persona_name: {persona_name or persona_id}\n"
            f"version: 1.0.0\n\n"
            f"返回 JSON 对象，包含 identity / language / rhythm / visual / audio / constraints "
            f"六个顶级键。每个字段值应为 {{value, confidence}} 格式。"
        )

        result = await self._llm.structured_output(
            system_prompt=SYSTEM_FORGE_PROMPT,
            user_prompt=user_prompt,
        )

        return self._build_manifest(result, persona_id, persona_name)

    # ── 模式 B：脚本/口播文本分析 → Persona ─────────────

    async def from_script(
        self,
        script: str,
        persona_id: str,
        persona_name: str = "",
        script_format: str = "txt",
    ) -> PersonaManifest:
        """分析脚本/口播文本，生成 Persona（语言层高度精确）。

        Args:
            script: 脚本或口播文本内容
            persona_id: Persona ID
            persona_name: Persona 名称
            script_format: 文本格式 (txt / srt / md)
        """
        # 基础统计预处理（不依赖 LLM）
        stats = self._basic_script_stats(script)

        if not self._has_api_key():
            return self._fallback_from_script(stats, persona_id, persona_name)

        user_prompt = (
            f"分析以下{'字幕' if script_format == 'srt' else '脚本'}文本，"
            f"提取创作者的语言风格参数和论证结构。\n\n"
            f"基础统计参考：\n"
            f"- 总字数: {stats['total_chars']}\n"
            f"- 总句数: {stats['sentence_count']}\n"
            f"- 平均句长: {stats['avg_sentence_len']:.1f} 字\n"
            f"- 最长句: {stats['max_sentence_len']} 字\n\n"
            # 资源保护上限 16000 字（避免超长脚本撑爆上下文）
            f"文本内容：\n```\n{script[:16000]}\n```\n\n"
            f"persona_id: {persona_id}\n"
            f"返回严格 JSON。"
        )

        result = await self._llm.structured_output(
            system_prompt=SYSTEM_SCRIPT_ANALYSIS_PROMPT,
            user_prompt=user_prompt,
        )

        return self._build_manifest_from_script(
            result, stats, persona_id, persona_name,
        )

    # ── 模式 C：对话引导 ────────────────────────────────

    async def dialogue_generate_questions(
        self,
        persona_id: str,
        existing_answers: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, str]]:
        """根据已有回答，生成后续对话引导问题。

        Args:
            persona_id: Persona ID
            existing_answers: 已收集的问答对

        Returns:
            问题列表 [{question, category, field}]
        """
        collected = json.dumps(existing_answers or {}, ensure_ascii=False)
        prompt = SYSTEM_DIALOGUE_PROMPT.replace("{collected_info}", str(collected))
        prompt += (
            "\n\n请根据当前已收集的信息，生成最多 2 个问题继续引导用户。"
            "\n返回 JSON 数组：[{question, category, field}]"
        )

        result = await self._llm.structured_output(
            system_prompt=prompt,
            user_prompt="请生成下一步引导问题。",
        )

        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "questions" in result:
            return result["questions"]
        return [{"question": "请描述你的视频创作风格", "category": "identity", "field": "tone"}]

    async def dialogue_build(
        self,
        persona_id: str,
        persona_name: str,
        answers: dict[str, Any],
    ) -> PersonaManifest:
        """将对话过程中收集的回答编译为 Persona。

        Args:
            persona_id: Persona ID
            persona_name: Persona 名称
            answers: 对话问答对 {category: {field: value, ...}, ...}
        """
        if not self._has_api_key():
            desc = "; ".join(
                f"{k}: {v}" for k, v in answers.items()
                if isinstance(v, str)
            )
            return self._fallback_persona(persona_id, persona_name, desc)

        answers_json = json.dumps(answers, ensure_ascii=False)
        user_prompt = (
            f"以下是一组视频创作者对自身风格的描述问答。\n"
            f"请综合所有回答，生成完整的 Persona 参数层配置。\n\n"
            f"问答记录：\n{answers_json}\n\n"
            f"persona_id: {persona_id}\n"
            f"persona_name: {persona_name}\n"
            f"version: 1.0.0\n\n"
            f"返回 JSON 对象。"
        )

        result = await self._llm.structured_output(
            system_prompt=SYSTEM_FORGE_PROMPT,
            user_prompt=user_prompt,
        )

        return self._build_manifest(result, persona_id, persona_name)

    # ── 模式 D：迭代优化 ────────────────────────────────

    async def refine(
        self,
        persona: PersonaManifest,
        feedback: str,
    ) -> PersonaManifest:
        """根据用户反馈迭代优化 Persona 配置。

        支持的操作：
        - "节奏太慢了" → 调整 rhythm.base_shot_duration_ms
        - "学术味太重" → 调整 language.academic_density
        - "文字动画太花" → 调整 visual.animation_styles
        - 复杂描述 → LLM 推断需要调整的字段

        Args:
            persona: 现有 Persona
            feedback: 用户反馈文本

        Returns:
            调整后的新 Persona
        """
        if not self._has_api_key():
            # 无 API key 时返回原 Persona
            return persona

        current_json = persona.model_dump(mode="json")
        user_prompt = (
            f"当前 Persona 配置：\n```json\n{json.dumps(current_json, ensure_ascii=False, indent=2)}\n```\n\n"
            f"用户反馈：{feedback}\n\n"
            f"请根据反馈调整配置。返回完整的更新后 JSON。"
        )

        result = await self._llm.structured_output(
            system_prompt=(
                "你是一个 Persona 优化专家。用户会给你当前 Persona 配置和反馈意见。"
                "请精确识别需要调整的参数并修改。保持其他参数不变。返回完整 JSON。"
            ),
            user_prompt=user_prompt,
        )

        return self._build_manifest(result, persona.persona_id, persona.persona_name)

    # ── 内部方法 ──────────────────────────────────────

    def _build_manifest(
        self,
        llm_result: dict[str, Any],
        persona_id: str,
        persona_name: str,
    ) -> PersonaManifest:
        """将 LLM 返回的 JSON 编译为 PersonaManifest。"""
        param = self._extract_parameter_layer(llm_result, persona_id)
        return PersonaManifest(
            persona_id=persona_id,
            persona_name=persona_name or persona_id,
            version="1.0.0",
            parameter=param,
            description=f"由 PersonaForge 自动生成（{self._llm.provider}）",
        )

    def _build_manifest_from_script(
        self,
        llm_result: dict[str, Any],
        stats: dict[str, Any],
        persona_id: str,
        persona_name: str,
    ) -> PersonaManifest:
        """从脚本分析结果构建 PersonaManifest。"""
        param_data: dict[str, Any] = {"persona_id": persona_id, "version": "1.0.0"}

        # 语言层 — 从 LLM 结果提取，用量化指标覆盖
        lang = llm_result.get("language", llm_result)
        if not isinstance(lang, dict):
            lang = {}

        lang.setdefault("academic_density", stats.get("academic_density", 0.1))
        lang.setdefault("max_sentence_len", stats.get("max_sentence_len", 30))
        lang.setdefault("slang_ratio", stats.get("slang_ratio", 0.05))

        # 如果 LLM 返回了顶级字段，合并
        lang_llm = llm_result.get("language", {})
        if isinstance(lang_llm, dict):
            lang.update(lang_llm)

        param_data["language"] = lang

        # 身份 — 从 LLM 的 tone 推断
        tone = llm_result.get("tone", "neutral")
        param_data["identity"] = {
            "tone": tone,
            "knowledge_domains": llm_result.get("knowledge_domains", []),
        }

        # 节奏 — 从论证结构推断
        structure = llm_result.get("argument_structure", llm_result.get("structure", {}))
        if isinstance(structure, dict):
            cut_profile = self._infer_cut_profile(structure)
            param_data["rhythm"] = {
                "cut_profile": cut_profile,
                "base_shot_duration_ms": self._infer_shot_duration(cut_profile),
            }

        param = ParameterLayer(**param_data)

        return PersonaManifest(
            persona_id=persona_id,
            persona_name=persona_name or persona_id,
            version="1.0.0",
            parameter=param,
            description=f"由 PersonaForge 从脚本分析生成",
        )

    def _extract_parameter_layer(
        self,
        data: dict[str, Any],
        persona_id: str,
    ) -> ParameterLayer:
        """从 LLM 返回的嵌套数据提取 ParameterLayer。

        处理 {value, confidence} 格式和直接值格式。
        """
        extract = self._unwrap_confidence

        identity_data = data.get("identity", {})
        language_data = data.get("language", {})
        rhythm_data = data.get("rhythm", {})
        visual_data = data.get("visual", {})
        audio_data = data.get("audio", {})
        constraints_data = data.get("constraints", {})

        return ParameterLayer(
            persona_id=persona_id,
            identity=IdentityConfig(
                tone=extract(identity_data, "tone", "neutral"),
                position=identity_data.get("position"),
                class_perspective=extract(identity_data, "class_perspective", None),
                knowledge_domains=extract(identity_data, "knowledge_domains", []),
            ),
            language=LanguageConfig(
                academic_density=float(extract(language_data, "academic_density", 0.1)),
                slang_ratio=float(extract(language_data, "slang_ratio", 0.05)),
                max_sentence_len=int(extract(language_data, "max_sentence_len", 30)),
                variance_target=float(extract(language_data, "variance_target", 0.6)),
                forbidden_patterns=extract(language_data, "forbidden_patterns", []),
                allowed_patterns=extract(language_data, "allowed_patterns", []),
            ),
            rhythm=RhythmConfig(
                cut_profile=extract(rhythm_data, "cut_profile", "even_flow"),
                surge_sections=extract(rhythm_data, "surge_sections", []),
                pause_sections=extract(rhythm_data, "pause_sections", []),
                base_shot_duration_ms=int(extract(rhythm_data, "base_shot_duration_ms", 5000)),
                cut_density_tier=extract(rhythm_data, "cut_density_tier", "medium"),
            ),
            visual=VisualConfig(
                palette=extract(visual_data, "palette", "neutral"),
                primary_color=extract(visual_data, "primary_color", None),
                accent_color=extract(visual_data, "accent_color", None),
                animation_styles=extract(visual_data, "animation_styles", {}),
                transition_weights=extract(visual_data, "transition_weights", {}),
            ),
            audio=AudioConfig(
                bgm_slots=PersonaForge._normalize_bgm_slots(extract(audio_data, "bgm_slots", {})),
                voice_model=extract(audio_data, "voice_model", None),
                target_loudness_lufs=float(extract(audio_data, "target_loudness_lufs", -16)),
            ),
            constraints=ConstraintsConfig(
                max_duration_sec=int(extract(constraints_data, "max_duration_sec", 900)),
                min_duration_sec=int(extract(constraints_data, "min_duration_sec", 30)),
                require_source_citation=bool(extract(constraints_data, "require_source_citation", False)),
                forbidden_topics=extract(constraints_data, "forbidden_topics", []),
            ),
        )

    @staticmethod
    def _basic_script_stats(script: str) -> dict[str, Any]:
        """对脚本做基础文本统计。"""
        # 去除 SRT 时间轴标记
        text = re.sub(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}", "", script)
        text = re.sub(r"\d+", "", text)
        text = re.sub(r"\s+", " ", text).strip()

        # 分句
        sentences = re.split(r"[。！？.!?\n]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]

        total_chars = len(text)
        sentence_count = len(sentences) or 1
        sentence_lens = [len(s) for s in sentences]

        # 估算学术密度（通过常见学术词汇检测）
        academic_terms = [
            "因此", "然而", "基于", "本质上", "从某种角度", "逻辑", "论证", "理论",
            "框架", "维度", "范式", "方法论", "实证", "量化", "归因", "异化",
            "therefore", "however", "based on", "essentially", "theoretical",
            "framework", "paradigm", "methodology", "empirical",
        ]
        term_count = sum(text.count(t) for t in academic_terms)
        academic_density = min(term_count / max(sentence_count, 1) * 0.1, 1.0)

        # 估算网络用语密度
        slang_terms = ["哈哈", "真的吗", "不是吧", "绝了", "太强了", "离谱",
                       "笑死", "无语", "真的", "其实", "就是说", "然后呢"]
        slang_count = sum(text.count(t) for t in slang_terms)
        slang_ratio = min(slang_count / max(total_chars, 1) * 2, 1.0)

        return {
            "total_chars": total_chars,
            "sentence_count": sentence_count,
            "avg_sentence_len": sum(sentence_lens) / sentence_count if sentence_count else 0,
            "max_sentence_len": max(sentence_lens) if sentence_lens else 0,
            "sentence_lens": sentence_lens,
            "academic_density": round(academic_density, 2),
            "slang_ratio": round(slang_ratio, 2),
        }

    @staticmethod
    def _unwrap_confidence(data: dict, key: str, default: Any = None) -> Any:
        """从 {value, confidence} 格式或直接值中提取。"""
        if key not in data:
            return default
        val = data[key]
        if isinstance(val, dict) and "value" in val:
            return val["value"]
        return val

    @staticmethod
    def _infer_cut_profile(structure: dict) -> str:
        """从论证结构推断节奏配置。"""
        sections = structure.get("sections", []) if isinstance(structure, dict) else []
        if not sections:
            return "even_flow"
        if "hook" in sections and "real_world_return" in sections:
            return "surge_pause"
        if "theory_acceleration" in str(sections):
            return "fast_but_controlled"
        return "even_flow"

    @staticmethod
    def _normalize_bgm_slots(val: Any) -> dict[str, list[str]]:
        """标准化 bgm_slots 为 dict[str, list[str]]。"""
        if not isinstance(val, dict):
            return {}
        result: dict[str, list[str]] = {}
        for k, v in val.items():
            if isinstance(v, list):
                result[k] = [str(item) for item in v]
            elif isinstance(v, str):
                result[k] = [v]
            elif v is not None:
                result[k] = [str(v)]
        return result

    @staticmethod
    def _infer_shot_duration(cut_profile: str) -> int:
        """从节奏配置推断基准镜头时长。"""
        mapping = {
            "rapid_fire": 1500,
            "fast_but_controlled": 3500,
            "surge_pause": 6000,
            "even_flow": 5000,
            "smooth_flow": 6000,
            "gentle_flow": 8000,
            "natural_flow": 6000,
        }
        return mapping.get(cut_profile, 5000)

    # ── 离线回退 ──────────────────────────────────────

    @staticmethod
    def _has_api_key() -> bool:
        """检查是否配置了 LLM API key。"""
        from clipwright.config import settings
        return bool(settings.llm_api_key)

    def _fallback_persona(
        self,
        persona_id: str,
        persona_name: str,
        description: str = "",
    ) -> PersonaManifest:
        """无 API key 时返回基于描述的默认 Persona。"""
        import hashlib

        # 从描述中提取简单特征
        desc_lower = description.lower()
        academic_terms = ["学术", "理论", "研究", "分析", "论证", "批判",
                          "academic", "theory", "analysis"]
        slang_terms = ["吐槽", "搞笑", "娱乐", "轻松", "日常",
                       "funny", "casual", "chill"]
        tech_terms = ["科技", "数码", "评测", "技术", "极客",
                      "tech", "digital", "review", "geek"]

        academic_count = sum(1 for t in academic_terms if t in desc_lower)
        slang_count = sum(1 for t in slang_terms if t in desc_lower)
        tech_count = sum(1 for t in tech_terms if t in desc_lower)

        if academic_count > slang_count and academic_count > tech_count:
            tone = "critical_intellectual"
        elif tech_count > academic_count and tech_count > slang_count:
            tone = "tech_enthusiast"
        elif slang_count > 0:
            tone = "casual_humor"
        elif any(w in desc_lower for w in ["冷峻", "严肃", "formal", "serious"]):
            tone = "formal_authoritative"
        elif any(w in desc_lower for w in ["热情", "温暖", "warm", "friendly"]):
            tone = "warm_storyteller"
        else:
            tone = "neutral"

        param = ParameterLayer(
            persona_id=persona_id,
            identity=IdentityConfig(tone=tone),
            language=LanguageConfig(),
            rhythm=RhythmConfig(),
            visual=VisualConfig(),
            audio=AudioConfig(),
            constraints=ConstraintsConfig(),
        )

        return PersonaManifest(
            persona_id=persona_id,
            persona_name=persona_name or persona_id,
            version="1.0.0",
            parameter=param,
            description=f"由 PersonaForge 离线生成（无 API key）",
        )

    def _fallback_from_script(
        self,
        stats: dict[str, Any],
        persona_id: str,
        persona_name: str,
    ) -> PersonaManifest:
        """无 API key 时，仅用量化统计生成 Persona。"""
        param = ParameterLayer(
            persona_id=persona_id,
            identity=IdentityConfig(tone="neutral"),
            language=LanguageConfig(
                academic_density=stats.get("academic_density", 0.1),
                slang_ratio=stats.get("slang_ratio", 0.05),
                max_sentence_len=stats.get("max_sentence_len", 30),
            ),
            rhythm=RhythmConfig(),
            visual=VisualConfig(),
            audio=AudioConfig(),
            constraints=ConstraintsConfig(),
        )

        return PersonaManifest(
            persona_id=persona_id,
            persona_name=persona_name or persona_id,
            version="1.0.0",
            parameter=param,
            description="由 PersonaForge 从脚本离线分析生成（无 API key）",
        )

    # ── 保存 ──────────────────────────────────────────

    async def save_persona(
        self,
        manifest: PersonaManifest,
        repo: Optional[PersonaRepository] = None,
    ) -> Path:
        """保存生成的 Persona 到磁盘。"""
        if repo is None:
            from clipwright.config import settings
            repo = PersonaRepository(settings.persona_dir)
        repo.save_manifest(manifest)
        return repo.root_dir / manifest.persona_id / "persona.yaml"
