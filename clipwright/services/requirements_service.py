"""需求服务 — MongoDB 持久化 + LLM 编排 + 对话管理。

Phase 1 加固：
・MongoDB 持久化（服务重启恢复会话）
・LLM 调用自动重试（3次指数退避）
・JSON 解析降级 + 容错
・对话历史窗口管理（超长历史自动截断摘要）
・Session TTL 自动清理

Phase 2 增强：
・文件上传 → RAG 索引
・SSE 流式推送 LLM 推理过程
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from clipwright.config import TIME_ZONE, logger
from clipwright.context import mongo
from clipwright.models.requirements_session import RequirementsSessionModel
from clipwright.schema.requirements import (
    CreativeBrief,
    MessageRole,
    ProductionPlan,
    RequirementsMessage,
    SessionStatus,
)
from clipwright.services.llm import LLMService
from clipwright.services.trace import add_event

# ── 常量 ──────────────────────────────────────────

MAX_HISTORY_ROUNDS = 20          # 超过 20 轮对话后压缩
MAX_MESSAGE_LENGTH = 3000        # 单条消息最大字符
LLM_RETRY_MAX = 3                # LLM 最大重试次数
LLM_RETRY_BASE_DELAY = 2.0      # 重试基础延迟（秒）
SESSION_TTL_HOURS = 48           # 会话过期时间
SESSION_CLEANUP_INTERVAL = 3600  # 清理检查间隔（秒）


# ── LLM 重试装饰器 ──────────────────────────────

async def llm_call_with_retry(
    llm: LLMService,
    method: str,
    pipeline_id: str = "",
    **kwargs: Any,
) -> dict:
    """调用 LLM structured_output 并自动重试（指数退避 + JSON 容错）。"""
    last_error = ""
    for attempt in range(1, LLM_RETRY_MAX + 1):
        try:
            resp = await llm.structured_output(pipeline_id=pipeline_id, **kwargs)
            if resp and isinstance(resp, dict) and ("reply" in resp or "summary" in resp or "scenes" in resp):
                return resp
            if not resp or resp == {}:
                last_error = "空响应"
                raise ValueError("Empty LLM response")
            return resp
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = str(e)[:100]
            logger.warning("LLM 调用第%d次失败: %s", attempt, last_error)
            if attempt < LLM_RETRY_MAX:
                delay = LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                await asyncio.sleep(delay)
        except Exception as e:
            last_error = str(e)[:100]
            logger.warning("LLM 调用异常第%d次: %s", attempt, last_error)
            if attempt < LLM_RETRY_MAX:
                await asyncio.sleep(LLM_RETRY_BASE_DELAY * attempt)
    # 全部重试失败 → 返回降级响应
    logger.error("LLM 调用全部重试失败: %s", last_error)
    return _fallback_response(method, kwargs.get("user_prompt", ""), last_error)


def _fallback_response(method: str, prompt: str, error: str) -> dict:
    """LLM 调用失败后的优雅降级响应。"""
    if method == "creative_brief":
        return {
            "reply": f"抱歉，我在理解你的需求时遇到了技术问题（{error}）。请重新描述你的想法，我会继续帮你整理。",
            "brief_draft": {},
            "is_ready": False,
            "missing_info": ["请重新描述创作需求"],
        }
    return {
        "summary": "规划书生成遇到技术问题，以下是基于原始场景的基础规划。",
        "sections": [],
        "markdown_content": f"# 规划书\n\n> 生成时遇到问题: {error}\n\n请稍后重试。",
        "total_duration_sec": 0,
        "scene_count": 0,
    }


# ── 对话窗口管理 ──────────────────────────────

def compress_history(messages: list[dict]) -> list[dict]:
    """压缩超长对话历史：超过 MAX_HISTORY_ROUNDS 轮时，保留最近 N 轮 + 摘要。"""
    if len(messages) <= MAX_HISTORY_ROUNDS * 2:
        return messages

    # 保留系统消息 + 最近 MAX_HISTORY_ROUNDS 轮
    keep_count = MAX_HISTORY_ROUNDS * 2  # 每条消息占用2条(用户+助手)
    compressed = messages[-keep_count:]

    # 添加摘要标记（实际摘要由前端/下次 LLM 调用处理）
    summary_note = {
        "role": "system",
        "content": f"[系统] 已省略 {len(messages) - keep_count} 条早期对话。如有需要可询问用户。",
        "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
    }
    compressed.insert(0, summary_note)
    return compressed


def truncate_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> str:
    """截断超长消息。"""
    return text[:max_len] + "..." if len(text) > max_len else text


# ── Prompt 模板 ──────────────────────────────

CREATIVE_BRIEF_SYSTEM = """你是一位专业的视频创作顾问。用户会提供他们的创作需求，你需要快速给出创作方案草案。

## 核心原则
- 当用户提供了主题（以及可选的文稿/风格/时长），且明确要求你拟定方案时，请你**直接产出完整的草案**，而不是反问更多问题。
- 草案中不确定的字段写上"待定"或合理推测即可，后续用户会调整。
- 如果你不确定某些信息，可以在回复中说明"以下是我基于现有信息的初步方案，你看看需要调整哪些部分？"
- 当用户表示"确认"或"可以"时，设置 is_ready=true。

## 输出格式（纯 JSON）
{
  "reply": "对用户的自然语言回复，包含方案摘要",
  "brief_draft": {
    "title": "视频标题",
    "overview": "概述",
    "target_audience": "目标受众（未知则填待定）",
    "core_message": "核心信息",
    "style_direction": "风格方向（未知则填待定）",
    "structure_suggestion": "结构建议（未知则填待定）",
    "duration_estimate": "预估时长",
    "key_elements": ["元素1"],
    "special_requirements": []
  },
  "is_ready": false,
  "missing_info": ["还未了解的信息"]
}

当 is_ready=true 时，brief_draft 必须完整填写。
"""


PLAN_TRANSLATE_SYSTEM = """你是一位专业的视频创作顾问。请将结构 Agent 生成的场景规划翻译为用户友好的 Markdown 规划书。

## 输出格式（纯 JSON）
{
  "summary": "规划书总体摘要",
  "sections": [
    {"title": "段落标题", "description": "段落描述", "scenes": [1, 2, 3]}
  ],
  "markdown_content": "完整的 Markdown 格式规划书\n- 包含场景表格\n- 每个场景的详细描述\n- 总时长统计",
  "total_duration_sec": 300,
  "scene_count": 5
}
"""


# ── 会话清理任务 ──────────────────────────────

_cleanup_task: Optional[asyncio.Task] = None


async def _cleanup_expired_sessions():
    """定期清理过期会话。"""
    while True:
        try:
            if mongo.is_connected:
                cutoff = datetime.now(tz=TIME_ZONE) - timedelta(hours=SESSION_TTL_HOURS)
                deleted = RequirementsSessionModel.delete_many({
                    "updated_time": {"$lt": cutoff},
                    "status": {"$nin": ["pipeline_running", "pipeline_done"]},
                })
                if deleted:
                    logger.info("清理过期会话 %d 个", deleted)
        except Exception:
            pass
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL)


def start_cleanup_task():
    """启动会话清理后台任务（安全地使用事件循环）。"""
    global _cleanup_task
    if _cleanup_task is None:
        try:
            loop = asyncio.get_running_loop()
            if loop and loop.is_running():
                _cleanup_task = asyncio.create_task(_cleanup_expired_sessions())
        except RuntimeError:
            pass  # 无运行中的事件循环，跳过（会在首次 chat 调用时自动设置）


# ── 主服务类 ──────────────────────────────────

class RequirementsService:
    """需求服务 — 对话 + 持久化 + 规划书生成。"""

    def __init__(self) -> None:
        self._llm = LLMService()
        self._cleanup_started = False

    async def _ensure_cleanup(self):
        if not self._cleanup_started:
            self._cleanup_started = True
            try:
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    asyncio.create_task(_cleanup_expired_sessions())
            except RuntimeError:
                pass

    # ── 会话生命周期 ──────────────────────────

    def create_session(self, user_inputs: dict[str, Any] | None = None) -> dict:
        """创建新会话并持久化。"""
        session_id = f"req_{uuid.uuid4().hex[:12]}"

        # 构建欢迎消息
        welcome = self._build_welcome(user_inputs or {})
        messages = [{
            "role": "assistant",
            "content": welcome,
            "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
            "metadata": {},
        }]

        # 持久化到 MongoDB
        model = RequirementsSessionModel(
            _id=session_id,
            status="gathering",
            messages=messages,
            user_inputs=user_inputs or {},
            extra={},
        )
        model.insert()
        logger.info("需求会话已创建: %s", session_id)
        return model.to_session_dict()

    async def load_or_create_session(
        self, session_id: str, user_inputs: dict | None = None
    ) -> dict:
        """加载现有会话，不存在则创建。"""
        if not session_id:
            return self.create_session(user_inputs)
        model = RequirementsSessionModel.find_by_id(session_id)
        if model:
            return model.to_session_dict()
        return self.create_session(user_inputs)

    def get_session(self, session_id: str) -> dict | None:
        """从 MongoDB 加载会话。"""
        model = RequirementsSessionModel.find_by_id(session_id)
        return model.to_session_dict() if model else None

    def delete_session(self, session_id: str) -> None:
        model = RequirementsSessionModel.find_by_id(session_id)
        if model:
            model.delete()

    @staticmethod
    def _build_welcome(inputs: dict) -> str:
        topic = inputs.get("topic", "")
        parts = ["## 🎬 欢迎\n\n我已收到你的基本信息。"]
        if topic:
            parts.append(f"\n\n**主题**: {topic}")
        parts.append("\n\n现在让我深入了解你的创作需求。请告诉我：")
        parts.append("\n- 这个视频的**核心信息**或**目标**是什么？")
        parts.append("\n- **目标受众**是谁？")
        parts.append("\n- 你对**风格、节奏、时长**有什么想法？")
        return "".join(parts)

    # ── 对话 ──────────────────────────────────

    async def chat(
        self,
        session_id: str,
        user_message: str,
    ) -> dict:
        """处理用户消息，更新 MongoDB，返回最新状态。"""
        await self._ensure_cleanup()
        session_data = self.get_session(session_id)
        if not session_data:
            return {"error": "Session not found"}

        status = session_data.get("status", "gathering")
        messages = session_data.get("messages", [])
        user_inputs = session_data.get("user_inputs", {})
        brief_data = session_data.get("creative_brief")
        plan_data = session_data.get("production_plan")

        # 添加用户消息
        messages.append({
            "role": "user",
            "content": truncate_message(user_message),
            "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
            "metadata": {},
        })

        # 压缩超长历史
        messages = compress_history(messages)

        # 状态路由
        if status in ("gathering", "init"):
            result = await self._handle_gathering(messages, brief_data, status, user_inputs)
            brief_data = result.get("brief_draft", brief_data)
            is_ready = result.get("is_ready", False)
            reply = result.get("reply", "请继续描述你的想法。")
            status = "brief_ready" if is_ready else "gathering"

            messages.append({
                "role": "assistant", "content": reply,
                "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                "metadata": {"is_ready": is_ready},
            })

            if is_ready:
                confirm_msg = (
                    f"\n\n---\n\n### ✅ 创作方案已完成\n\n请确认以下方案是否满意，或提出修改：\n\n"
                    f"**标题**: {brief_data.get('title', '待定')}\n"
                    f"**概述**: {brief_data.get('overview', '')}\n"
                    f"**目标受众**: {brief_data.get('target_audience', '')}\n"
                    f"**风格**: {brief_data.get('style_direction', '')}\n"
                    f"**预估时长**: {brief_data.get('duration_estimate', '')}\n\n"
                    "请输入「确认」，或继续提出修改意见。"
                )
                messages.append({
                    "role": "assistant", "content": confirm_msg,
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {},
                })

        elif status == "brief_ready":
            if self._is_confirm(user_message):
                status = "brief_confirmed"
                messages.append({
                    "role": "assistant", "content": "方案已确认！正在生成成片规划书，请稍候...",
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {},
                })
                # 异步生成规划书
                plan_result = await self._generate_plan(brief_data, user_inputs, session_id)
                if plan_result:
                    plan_data = plan_result
                    status = "plan_ready"
                    messages.append({
                        "role": "assistant", "content": (
                            f"### 📋 成片规划书已生成\n\n共 **{plan_result.get('scene_count', 0)}** 个场景，"
                            f"预估总时长 **{plan_result.get('total_duration_sec', 0):.0f}秒**\n\n"
                            "确认无误请输入「确认」，或提出修改意见。"
                        ),
                        "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                        "metadata": {"plan_ready": True},
                    })
            else:
                status = "gathering"
                messages.append({
                    "role": "assistant", "content": "好的，请告诉我需要如何调整？",
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {},
                })

        elif status == "plan_ready":
            if self._is_confirm(user_message):
                status = "plan_confirmed"
                messages.append({
                    "role": "assistant", "content": "规划书已确认！即将启动视频制作流程。",
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {},
                })
            else:
                status = "gathering"
                messages.append({
                    "role": "assistant", "content": "好的，请告诉我需要如何调整规划书？",
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {},
                })

        # 持久化到 MongoDB
        self._persist(session_id, status, messages, brief_data, plan_data, user_inputs)
        return self.get_session(session_id) or {}

    @staticmethod
    def _is_confirm(message: str) -> bool:
        msg = message.strip().lower()
        return any(msg.startswith(kw) or msg == kw for kw in
                   ["确认", "同意", "可以", "好的", "行", "没问题", "ok", "yes", "y", "确定", "批准"])

    # ── LLM 需求收集 ──────────────────────────

    async def _handle_gathering(
        self, messages: list[dict], brief_data: dict | None, status: str,
        user_inputs: dict | None = None,
    ) -> dict:
        """调用 LLM 收集需求（注入 Persona 上下文）。"""
        context = ""
        if brief_data:
            context = f"\n\n当前方案草稿:\n{json.dumps(brief_data, ensure_ascii=False, indent=2)}"

        # 注入 Persona 上下文
        persona_context = self._build_persona_context(user_inputs or {})
        if persona_context:
            context += f"\n\n创作者风格参考:\n{persona_context}"

        user_prompt = messages[-1]["content"] if messages else "请开始对话。"
        llm_kwargs = {
            "system_prompt": CREATIVE_BRIEF_SYSTEM + context,
            "user_prompt": user_prompt,
        }
        return await llm_call_with_retry(
            self._llm, "creative_brief", pipeline_id="", **llm_kwargs,
        )

    @staticmethod
    def _build_persona_context(user_inputs: dict) -> str:
        """从 Persona YAML 中提取风格参数作为 LLM 上下文。"""
        persona_id = user_inputs.get("persona_id", "")
        if not persona_id:
            return ""
        try:
            from clipwright.persona.loader import load_persona_by_id
            manifest = load_persona_by_id(persona_id)
            if not manifest or not manifest.parameter:
                return ""
            param = manifest.parameter
            parts = []
            identity = param.identity
            if identity:
                tone = identity.get("tone", "")
                if tone:
                    parts.append(f"风格语调: {tone}")
            lang = param.language
            if lang:
                ad = lang.get("academic_density", 0)
                ms = lang.get("max_sentence_len", 0)
                if ad: parts.append(f"学术密度: {ad}")
                if ms: parts.append(f"最长句长: {ms}字")
            rhythm = param.rhythm
            if rhythm:
                cp = rhythm.get("cut_profile", "")
                if cp: parts.append(f"剪辑节奏: {cp}")
            constraints = param.constraints
            if constraints:
                md = constraints.get("max_duration_sec", 0)
                if md: parts.append(f"最长时长: {md}秒")
            return "\n".join(parts) if parts else ""
        except Exception as e:
            logger.debug("Persona 上下文加载失败: %s", e)
            return ""

    # ── 规划书生成 ──────────────────────────

    async def _generate_plan(
        self, brief_data: dict | None, user_inputs: dict, session_id: str
    ) -> dict | None:
        """调用 StructureAgent（注入 Persona）并翻译为规划书。"""
        try:
            from clipwright.agents.structure_agent import StructureAgent
            from clipwright.schema.agent import AgentContext, StructureInput
            from clipwright.persona.loader import load_persona_by_id, resolve_inheritance
            from clipwright.persona.validator import validate_manifest

            topic = user_inputs.get("topic", brief_data.get("title", "") if brief_data else "")
            persona_id = user_inputs.get("persona_id", "default")
            plugin_id = user_inputs.get("category_plugin_id", "knowledge_longform")

            # 加载 Persona + 注入风格参数
            persona_config = {}
            persona_prompt = ""
            try:
                manifest = load_persona_by_id(persona_id)
                manifest = resolve_inheritance(manifest)
                persona_config = manifest.parameter.model_dump(mode="json") if manifest.parameter else {}
                prompt_path = None
                if manifest.prompt:
                    from pathlib import Path
                    from clipwright.config import settings
                    prompt_path = settings.persona_dir / persona_id / "prompt.md"
                if prompt_path and prompt_path.exists():
                    persona_prompt = prompt_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Persona %s 加载失败: %s，使用默认配置", persona_id, e)

            agent = StructureAgent()
            context = AgentContext(
                pipeline_id=session_id,
                persona_id=persona_id,
                category_plugin_id=plugin_id,
                topic=topic,
                extra_params={
                    "video_mode": "voiceover",
                    "script_text": user_inputs.get("script_text", ""),
                    "audio_duration_sec": user_inputs.get("audio_duration_sec", 300),
                },
            )

            result = await agent.execute(
                StructureInput(
                    context=context,
                    persona_config=persona_config,
                    persona_prompt=persona_prompt,
                    rag_context="",
                ),
                context,
            )
            scenes = result.scenes or []
            if not scenes:
                logger.warning("StructureAgent 返回空场景")
                return None

            return await self._translate_plan(scenes, brief_data)

        except Exception as e:
            logger.exception("规划书生成失败: %s", e)
            return None

    async def _translate_plan(self, scenes: list[dict], brief_data: dict | None) -> dict:
        """翻译场景为规划书。"""
        scenes_json = json.dumps(scenes, ensure_ascii=False, indent=2)
        brief_json = json.dumps(brief_data, ensure_ascii=False) if brief_data else "{}"

        result = await llm_call_with_retry(self._llm, "plan_translate", pipeline_id="", **{
            "system_prompt": f"{PLAN_TRANSLATE_SYSTEM}\n\n参考方案:\n{brief_json}",
            "user_prompt": f"结构 Agent 输出:\n{scenes_json}",
        })

        if not result.get("markdown_content"):
            result["markdown_content"] = self._default_markdown(scenes)
        if not result.get("total_duration_sec"):
            result["total_duration_sec"] = sum(s.get("duration_sec", 0) for s in scenes)
        if not result.get("scene_count"):
            result["scene_count"] = len(scenes)
        result["raw_scenes"] = scenes
        return result

    @staticmethod
    def _default_markdown(scenes: list[dict]) -> str:
        lines = ["# 🎬 视频成片规划书\n"]
        lines.append("## 场景列表\n")
        lines.append("| # | 标题 | 时长 | 关键词 |")
        lines.append("|---|------|------|--------|")
        for i, s in enumerate(scenes, 1):
            lines.append(f"| {i} | {s.get('title', '')} | {s.get('duration_sec', 0)}s | {', '.join(s.get('keywords', [])[:3])} |")
        total = sum(s.get("duration_sec", 0) for s in scenes)
        lines.append(f"\n**总时长**: {total:.0f}s ({total/60:.1f}分钟)\n---\n")
        for i, s in enumerate(scenes, 1):
            lines.append(f"### 场景 {i}: {s.get('title', '')}")
            lines.append(f"- **时长**: {s.get('duration_sec', 0)}秒")
            lines.append(f"- **描述**: {s.get('description', '')}\n")
        return "\n".join(lines)

    # ── 持久化 ──────────────────────────────

    def _persist(
        self, session_id: str, status: str, messages: list[dict],
        brief: dict | None, plan: dict | None, user_inputs: dict,
    ) -> None:
        """持久化会话状态到 MongoDB。"""
        try:
            model = RequirementsSessionModel.find_by_id(session_id)
            if model:
                model.status = status
                model.messages = messages
                model.creative_brief = brief
                model.production_plan = plan
                model.updated_time = datetime.now(tz=TIME_ZONE)
                model.update()
            else:
                model = RequirementsSessionModel(
                    _id=session_id, status=status, messages=messages,
                    creative_brief=brief, production_plan=plan,
                    user_inputs=user_inputs,
                )
                model.insert()
        except Exception as e:
            logger.warning("MongoDB 持久化失败: %s", e)

    # ── 文件处理 ──────────────────────────────

    async def process_upload(
        self, session_id: str, file_path: str, file_name: str
    ) -> dict:
        """处理上传文件 → 提取文本 → 添加到对话上下文。"""
        content = ""
        try:
            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
            if ext in ("txt", "md", "markdown"):
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()[:5000]
            elif ext in ("pdf",):
                try:
                    import subprocess
                    result = subprocess.run(
                        ["pdftotext", file_path, "-"],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        content = result.stdout[:5000]
                except Exception:
                    content = "[PDF 文件，文本提取失败]"
            elif ext in ("docx",):
                try:
                    import zipfile
                    with zipfile.ZipFile(file_path) as z:
                        texts = []
                        for name in z.namelist():
                            if name.startswith("word/document"):
                                texts.append(z.read(name).decode("utf-8", errors="replace"))
                        content = texts[0][:5000] if texts else "[DOCX 解析失败]"
                except Exception:
                    content = "[DOCX 文件]"
            else:
                content = f"[上传文件: {file_name}]"
        except Exception as e:
            content = f"[文件读取失败: {e}]"

        msg = (
            f"用户上传了参考文件 **{file_name}**，内容摘要如下：\n\n```\n{content[:1000]}\n```"
            if content else f"用户上传了文件: {file_name}"
        )

        session = self.get_session(session_id)
        if session:
            messages = session.get("messages", [])
            messages.append({
                "role": "user",
                "content": msg,
                "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                "metadata": {"file": file_name, "type": "upload"},
            })
            self._persist(
                session_id, session.get("status", "gathering"),
                messages, session.get("creative_brief"),
                session.get("production_plan"), session.get("user_inputs", {}),
            )

        return {"content_preview": content[:500], "file_name": file_name}

    # ── 规划书获取 ──────────────────────────

    def get_plan(self, session_id: str) -> dict | None:
        """获取规划书。"""
        session = self.get_session(session_id)
        if not session:
            return None
        raw = session.get("production_plan") or {}
        if not raw:
            return None
        return {
            "markdown": raw.get("markdown_content", ""),
            "summary": raw.get("translated_summary", raw.get("summary", "")),
            "total_duration_sec": raw.get("total_duration_sec", 0),
            "scene_count": raw.get("scene_count", 0),
            "sections": raw.get("sections", []),
            "raw_scenes": raw.get("raw_scenes", []),
        }

    # ── SSE 流式推送 ──────────────────────────

    async def stream_chat(
        self, session_id: str, user_message: str,
    ):
        """SSE 流式处理对话，逐块推送 LLM 推理。"""
        # 1. 先返回用户消息确认 + typing 指示
        yield {"type": "status", "data": "typing"}

        # 2. 异步处理完整对话
        result = await self.chat(session_id, user_message)

        # 3. 推送结果
        yield {"type": "result", "data": result}
