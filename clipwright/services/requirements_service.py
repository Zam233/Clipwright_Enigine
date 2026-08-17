"""需求服务 — MongoDB 持久化 + LLM 编排 + 对话管理。

Phase 1 加固：
・MongoDB 持久化（服务重启恢复会话）
・LLM 调用自动重试（3次指数退避）
・JSON 解析降级 + 容错
・对话历史窗口管理（超长历史自动截断摘要）
・Session TTL 自动清理

Phase 2 增强（对账注记 2026-08：以下两项均未实现，保留为规划）：
・文件上传 → RAG 索引（当前仅文本提取 ≤5000 字符）
・SSE 流式推送 LLM 推理过程（当前完整响应缓冲后一次性推送）
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
from clipwright.schema.timeline import Timeline, Track

# ── 常量 ──────────────────────────────────────────

MAX_HISTORY_ROUNDS = 20          # 超过 20 轮对话后压缩
MAX_MESSAGE_LENGTH = 50000       # 单条消息最大字符
LLM_RETRY_MAX = 3                # LLM 最大重试次数
LLM_RETRY_BASE_DELAY = 2.0      # 重试基础延迟（秒）
BRIEF_GENERATE_TIMEOUT = 180     # 简报生成单次调用超时（秒）
PLAN_TRANSLATE_TIMEOUT = 240     # 规划书翻译单次调用超时（秒）
SESSION_TTL_HOURS = 48           # 会话过期时间
SESSION_CLEANUP_INTERVAL = 3600  # 清理检查间隔（秒）

# 时间线数值调整字段白名单 + 边界钳制（值域外拒绝/钳位）
_ADJUST_FIELDS: dict[str, tuple[type, Any]] = {
    "speed": (float, lambda v: max(0.25, min(4.0, float(v)))),
    "volume": (float, lambda v: max(0.0, min(1.0, float(v)))),
    "opacity": (float, lambda v: max(0.0, min(1.0, float(v)))),
    "duration_sec": (float, lambda v: max(0.1, float(v))),
    "start_sec": (float, lambda v: max(0.0, float(v))),
    "source_offset_sec": (float, lambda v: max(0.0, float(v))),
    "font_size": (float, lambda v: max(8.0, min(200.0, float(v)))),
    "font_color": (str, lambda v: v if isinstance(v, str) else "#FFFFFF"),
    "text": (str, lambda v: str(v)),
}


_memory_sessions: dict[str, dict] = {}

# E5: 会话级缓存 — RAG 检索 (session_id → (query, result, ts)) 与 Persona 上下文 (persona_id → (result, ts))。
# TTL 10 分钟；同 session 同 query / 同 persona 复用，避免每条 chat 消息重复 embedding 与序列化。
_SESSION_CACHE_TTL = 600.0
_session_knowledge_cache: dict[str, tuple[str, str, float]] = {}
_persona_context_cache: dict[str, tuple[dict, float]] = {}


def clear_session_caches() -> None:
    """清空会话级缓存（测试/重置用）。"""
    _session_knowledge_cache.clear()
    _persona_context_cache.clear()


def _mongo_ok() -> bool:
    """检查 MongoDB 是否已连接。"""
    try:
        from clipwright.context import mongo
        return mongo.is_connected
    except Exception:
        return False


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


async def _web_tool_executor(tool_name: str, tool_input: dict) -> dict:
    """执行 web_search / web_fetch 工具调用（W1）。失败返回空 dict，绝不抛异常。"""
    try:
        from clipwright.tool.registry import ToolRegistry
        result = await ToolRegistry.execute(tool_name, **tool_input)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result if isinstance(result, dict) else {"result": str(result)}
    except Exception as e:
        logger.warning("requirements chat 工具 %s 执行失败: %s", tool_name, e)
        return {"results": []} if tool_name == "web_search" else {"content": ""}


async def _build_web_context(query: str, max_results: int = 3) -> str:
    """联网搜索并拼接事实参考段落（W2）。未配置/失败/空结果返回 ""。"""
    try:
        from clipwright.services.web_search import WebSearchService
        svc = WebSearchService()
        if not svc.is_configured():
            return ""
        results = await svc.search(query[:200], max_results=max_results)
        if not results:
            return ""
        lines = [
            f"- 标题: {r.get('title', '')} | 摘要: {r.get('snippet', '')} "
            f"| 来源: {r.get('url', '')}"
            for r in results[:max_results]
        ]
        return "\n".join(lines)
    except Exception:
        return ""


def _material_library_overview() -> str:
    """生成素材库概览一行（A2）。素材库为空返回 ""；以源粒度统计，不做逐源 I/O。

    素材库注册表 MaterialRegistry.list() 仅返回 {id, name}（无类型字段），
    故概览按源粒度呈现：`素材库可用: {n} 个素材源: {name 列表}`。
    任何异常（注册表不可用等）一律返回 ""，保证零变化。
    """
    try:
        from clipwright.material.registry import MaterialRegistry
        sources = MaterialRegistry.list()
        if not sources:
            return ""
        names = ", ".join(str(s.get("name") or s.get("id") or "?") for s in sources[:10])
        return f"素材库可用: {len(sources)} 个素材源: {names}"
    except Exception:
        return ""


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
- **用户提供的原始文稿是创作方案的唯一依据**，你必须仔细阅读并基于文稿内容生成方案，所有字段（标题、概述、核心信息、结构建议等）都应真实反映文稿内容，不得编造或遗漏文稿中的关键信息。
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
    "special_requirements": [],
    "production_plan": "制作方案 (如 A/B/C)",
    "reference_style": "参考风格描述",
    "bgm_requirement": "BGM需求",
    "era_background": "年代背景",
    "material_requirements": {
      "type": "素材类型",
      "source": "推荐来源",
      "preference": "素材偏好",
      "timeliness": "时效性要求"
    },
    "animation_style": {
      "style": "动画风格名",
      "tone": "色调描述",
      "fonts": {"title": "...", "body": "...", "number": "..."},
      "icons": "图标方案"
    },
    "asset_ratio": {"footage": "30-40%", "mg": "60-70%"}
  },
  "is_ready": false,
  "missing_info": ["还未了解的信息"]
}

当 is_ready=true 时，brief_draft 必须完整填写。
如果用户提供了以上信息，填入对应字段。未提供的信息可留空或省略。不要编造用户未提供的信息。
"""


PLAN_TRANSLATE_SYSTEM = """你是一位专业的视频创作顾问。请将结构 Agent 生成的场景规划翻译为用户友好的 Markdown 规划书。

Markdown 规划书必须使用以下场景表格：
| 场景标题 | 时长 | 口播脚本 | 画面描述 |
|---|---|---|---|

其中“画面描述”单元格按已有信息包含以下子项，并用 `<br>` 分隔：
- 素材库: xxx
- 素材内容: xxx
- 素材偏好: xxx
- 动画描述: xxx（仅在存在时填写）

## 输出格式（纯 JSON）
{
  "summary": "规划书总体摘要",
  "sections": [
    {"title": "段落标题", "description": "段落描述", "scenes": [1, 2, 3]}
  ],
  "markdown_content": "完整的 Markdown 格式规划书，包含上述四列表格和总时长统计",
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
                # P0-6: 线程内执行（_io 在事件循环线程会返回未 await 的协程导致清理静默失效）
                deleted = await asyncio.to_thread(
                    RequirementsSessionModel.delete_many, {
                        "updated_time": {"$lt": cutoff},
                        "status": {"$nin": ["pipeline_running", "pipeline_done"]},
                    }
                )
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
                    from clipwright.services.async_util import spawn_background
                    spawn_background(_cleanup_expired_sessions(), name="requirements-session-cleanup")
            except RuntimeError:
                pass

    async def _record_llm_usage(
        self,
        agent_name: str,
        pipeline_id: str = "",
        status: str = "success",
    ) -> None:
        """C2: 记录最近一次 LLM 调用用量（requirements 会话级，写入 llm_tracker）。

        LLMService.generate（structured_output/with_tools/chat）会缓存 last_usage；
        ask 不缓存，故仅对走 generate 的调用点接线。失败仅告警不阻断。
        """
        usage = getattr(self._llm, "last_usage", None)
        if not usage:
            return
        try:
            from clipwright.services.llm_tracker import record_llm_call
            await record_llm_call(
                pipeline_id=pipeline_id or "requirements",
                agent_name=agent_name,
                model=usage.get("model", "unknown"),
                provider=usage.get("provider", "unknown"),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                duration_ms=0,
                status=status,
            )
        except Exception as e:
            logger.warning("requirements LLM 用量记录失败: %s", e)

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

        session_dict = {
            "session_id": session_id,
            "status": "gathering",
            "messages": messages,
            "user_inputs": user_inputs or {},
            "creative_brief": None,
            "production_plan": None,
        }
        if _mongo_ok():
            try:
                model = RequirementsSessionModel(
                    _id=session_id,
                    status="gathering",
                    messages=messages,
                    user_inputs=user_inputs or {},
                    extra={},
                )
                model.insert()
            except Exception as e:
                logger.warning("MongoDB 会话创建失败，使用内存: %s", e)
        _memory_sessions[session_id] = session_dict
        logger.info("需求会话已创建: %s (mongo=%s)", session_id, _mongo_ok())
        return session_dict

    async def load_or_create_session(
        self, session_id: str, user_inputs: dict | None = None
    ) -> dict:
        """加载现有会话，不存在则创建。"""
        if not session_id:
            return self.create_session(user_inputs)
        # P0-6: _io() 在事件循环线程返回协程 → 必须 offload 到线程执行
        existing = await asyncio.to_thread(self.get_session, session_id)
        if existing:
            return existing
        return self.create_session(user_inputs)

    def get_session(self, session_id: str) -> dict | None:
        """从 MongoDB 加载会话。"""
        mem = _memory_sessions.get(session_id)
        if mem:
            return mem
        if _mongo_ok():
            try:
                model = RequirementsSessionModel.find_by_id(session_id)
                if model:
                    return model.to_dict()
            except Exception as e:
                logger.warning("MongoDB 会话查询失败: %s", e)
        return None

    def delete_session(self, session_id: str) -> None:
        _memory_sessions.pop(session_id, None)
        if _mongo_ok():
            try:
                model = RequirementsSessionModel.find_by_id(session_id)
                if model:
                    model.delete()
            except Exception as e:
                logger.warning("MongoDB 会话删除失败: %s", e)

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
        session_data = await asyncio.to_thread(self.get_session, session_id)
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
            result = await self._handle_gathering(messages, brief_data, status, user_inputs, session_id)
            brief_data = result.get("brief_draft", brief_data)
            is_ready = result.get("is_ready", False)
            # 只要生成了完整方案草稿就进入待确认状态。不完全依赖 LLM 的 is_ready 标志——
            # 否则前端已展示方案（brief_ready）而后端仍停留 gathering，用户确认后会被
            # 当作普通 gathering 消息处理（回复"请继续描述你的想法"），无法生成规划书。
            if isinstance(brief_data, dict) and brief_data:
                is_ready = True
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
            if await self._is_confirm(user_message):
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
            if await self._is_confirm(user_message):
                status = "plan_confirmed"
                messages.append({
                    "role": "assistant", "content": "规划书已确认！即将启动视频制作流程。",
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {},
                })
            else:
                # B6/E2: 规划书反馈闭环——不重置回 gathering，而是按反馈修订规划书
                # 并复用已确认的 raw_scenes（仅重新翻译，不重跑 StructureAgent）。
                status, revised = await self._handle_plan_revision(
                    messages, brief_data, plan_data, user_inputs, session_id, user_message,
                )
                if revised:
                    plan_data = revised

        # 持久化到 MongoDB（offload 到线程：_persist 内的 find_by_id 走 _io()，
        # 在事件循环线程会返回未 await 的协程导致写入静默失败；线程内无事件循环则同步执行）
        await asyncio.to_thread(
            self._persist, session_id, status, messages, brief_data, plan_data, user_inputs,
        )
        session = (await asyncio.to_thread(self.get_session, session_id)) or {}
        msgs = session.get("messages", [])
        last_assistant = next(
            (m["content"] for m in reversed(msgs) if m.get("role") == "assistant"),
            "",
        )
        return {**session, "reply": last_assistant}

    async def _handle_plan_revision(
        self,
        messages: list[dict],
        brief_data: dict | None,
        plan_data: dict | None,
        user_inputs: dict,
        session_id: str,
        feedback: str,
    ) -> tuple[str, dict | None]:
        """plan_ready 非确认消息 → 按反馈修订规划书（B6/E2）。

        复用现有 raw_scenes 只重新翻译（不重跑 StructureAgent LLM），
        修订成功保持 plan_ready；失败保留旧规划书并提示。
        返回 (status, new_plan_or_None)。
        """
        existing_raw = []
        if isinstance(plan_data, dict):
            raw = plan_data.get("raw_scenes")
            if isinstance(raw, list):
                existing_raw = raw
        new_plan = await self._generate_plan(
            brief_data, user_inputs, session_id,
            feedback=feedback, existing_raw_scenes=existing_raw or None,
        )
        if new_plan:
            messages.append({
                "role": "assistant",
                "content": (
                    f"### 📋 已按反馈修订规划书\n\n共 **{new_plan.get('scene_count', 0)}** 个场景，"
                    f"预估总时长 **{new_plan.get('total_duration_sec', 0):.0f}秒**\n\n"
                    "确认无误请输入「确认」，或继续提出修改意见。"
                ),
                "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                "metadata": {"plan_ready": True},
            })
            return "plan_ready", new_plan
        messages.append({
            "role": "assistant",
            "content": "修订规划书时遇到问题，已保留原规划书。请重试或提出新的修改意见。",
            "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
            "metadata": {"plan_ready": True},
        })
        return "plan_ready", None

    # ── 时间线编辑（选中素材 + 自然语言指令） ──────────

    async def edit_timeline(
        self,
        session_id: str,
        user_message: str,
        timeline: dict[str, Any],
        selected_clip_ids: list[str],
        region_start_sec: float | None = None,
        region_end_sec: float | None = None,
    ) -> dict:
        """时间线编辑：意图分类 → 换素材 / 重做动画 / 数值调整 → 返回 proposed_timeline。

        timeline 入参为 dict；构建子集/合并时须 Timeline.model_validate 转为 pydantic，
        返回前 model_dump(mode="json") 序列化。

        W12: 区域级返工 — 提供 region_start/end 时，把编辑范围限制在该时间窗内的片段
        （意图分类只看到区域内片段；selected_clip_ids 若为空则自动取区域内片段）。
        """
        await self._ensure_cleanup()
        session_data = await asyncio.to_thread(self.get_session, session_id)
        if not session_data:
            return {"error": "Session not found"}

        messages = session_data.get("messages", [])
        user_inputs = session_data.get("user_inputs", {})
        brief_data = session_data.get("creative_brief")
        plan_data = session_data.get("production_plan")

        # 追加用户消息，保留对话上下文
        messages.append({
            "role": "user",
            "content": truncate_message(user_message),
            "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
            "metadata": {"edit": True},
        })

        proposed: Timeline | None = Timeline.model_validate(timeline) if timeline else None

        # W12: 区域级返工 — 收集时间窗内的片段 id（供意图分类与编辑动作限定范围）
        region_clip_ids: list[str] = []
        if proposed is not None and region_start_sec is not None and region_end_sec is not None:
            lo, hi = min(region_start_sec, region_end_sec), max(region_start_sec, region_end_sec)
            for track in proposed.tracks or []:
                for clip in track.clips or []:
                    if clip.start_sec < hi and clip.start_sec + clip.duration_sec > lo:
                        region_clip_ids.append(clip.id)
        scope_ids = selected_clip_ids if selected_clip_ids else region_clip_ids

        reply_parts: list[str] = []
        action = "adjust"  # 最保守默认：不回退、不越权

        # LLM 意图分类（flash 轻量模型，temperature=0）；失败回退 adjust
        try:
            intent = await self._llm.structured_output(
                system_prompt=(
                    "你是视频时间线编辑意图分类器。用户选中了时间轴上的若干片段并输入自然语言指令，"
                    "判断用户意图并仅输出符合 schema 的 JSON：\n"
                    "- replace_material：换素材/换画面/替换片段素材/找更合适的视频/换更明亮的图片\n"
                    "- redo_animation：重做动画/动画重来/换个动画效果/让动画动起来\n"
                    "- adjust：数值/参数调整（速度、音量、透明度、时长、位置、字号、颜色等）或其它"
                ),
                user_prompt=f"选中片段: {selected_clip_ids}\n指令: {user_message}",
                output_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["replace_material", "redo_animation", "adjust"]},
                    },
                    "required": ["action"],
                },
                temperature=0,
                max_tokens=16,
                pipeline_id=session_id,
                use_flash=True,
            )
            if isinstance(intent, dict) and intent.get("action") in ("replace_material", "redo_animation", "adjust"):
                action = intent["action"]
            await self._record_llm_usage("requirements.edit_intent", session_id)
        except Exception as e:
            logger.warning("编辑意图分类失败，默认 adjust: %s", e)

        if proposed is not None and scope_ids:
            try:
                if action == "replace_material":
                    proposed, notes = await self._edit_replace_material(
                        proposed, scope_ids, session_id, brief_data, plan_data, user_inputs,
                    )
                    reply_parts.extend(notes)
                elif action == "redo_animation":
                    proposed, notes = await self._edit_redo_animation(
                        proposed, scope_ids, session_id, brief_data, plan_data, user_inputs,
                    )
                    reply_parts.extend(notes)
                else:
                    proposed, notes = await self._edit_adjust(proposed, scope_ids, user_message)
                    reply_parts.extend(notes)
            except Exception as e:
                logger.exception("时间线编辑执行失败: %s", e)
                reply_parts.append(f"编辑执行出错：{str(e)[:120]}")

        status = session_data.get("status", "plan_ready")
        reply = "；".join(reply_parts) if reply_parts else "已收到，未对时间线做任何修改。"
        messages.append({
            "role": "assistant",
            "content": reply,
            "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
            "metadata": {"edit": True, "action": action},
        })
        await asyncio.to_thread(
            self._persist, session_id, status, messages, brief_data, plan_data, user_inputs,
        )
        add_event(session_id, "system", "info", f"时间线编辑[{action}]: {reply[:100]}")

        return {
            "reply": reply,
            "action": action,
            "proposed_timeline": proposed.model_dump(mode="json") if proposed is not None else timeline,
        }

    def _find_clip(self, timeline: Timeline, clip_id: str) -> tuple[Track, Any] | None:
        """按 id 在时间线中查找 clip（返回所在轨道 + clip）。"""
        for track in timeline.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return track, clip
        return None

    async def _edit_replace_material(
        self,
        timeline: Timeline,
        selected_clip_ids: list[str],
        session_id: str,
        brief_data: dict | None,
        plan_data: dict | None,
        user_inputs: dict[str, Any],
    ) -> tuple[Timeline, list[str]]:
        """替换素材：对每个选中 clip 构建 1-场景骨架 → MaterialAgent → 取最优建议素材替换。"""
        from clipwright.agents.material_agent import MaterialAgent
        from clipwright.schema.agent import AgentContext, MaterialInput
        from clipwright.persona.loader import load_persona_by_id, resolve_inheritance

        topic = user_inputs.get("topic", "")
        persona_id = user_inputs.get("persona_id", "default")
        plugin_id = user_inputs.get("category_plugin_id", "knowledge_longform")
        persona_config: dict[str, Any] = {}
        try:
            manifest = load_persona_by_id(persona_id)
            manifest = resolve_inheritance(manifest)
            persona_config = manifest.parameter.model_dump(mode="json") if manifest.parameter else {}
        except Exception:
            pass

        context = AgentContext(
            pipeline_id=session_id, persona_id=persona_id,
            category_plugin_id=plugin_id, topic=topic, extra_params=dict(user_inputs),
        )
        notes: list[str] = []
        for cid in selected_clip_ids:
            found = self._find_clip(timeline, cid)
            if not found:
                continue
            _track, clip = found
            clip_text = clip.text if isinstance(getattr(clip, "text", None), str) else None
            scene_title = clip_text or clip.metadata.get("source_title") or "选中片段"
            scene = {
                "title": scene_title,
                "keywords": [],
                "description": clip.metadata.get("description") or clip_text or "",
            }
            try:
                out = await MaterialAgent().execute(
                    MaterialInput(
                        context=context,
                        script_skeleton={"scenes": [scene]},
                        persona_config=persona_config,
                        creative_brief=brief_data,
                        production_plan=plan_data,
                    ),
                    context,
                )
            except Exception as e:
                logger.exception("换素材失败 clip=%s: %s", cid, e)
                notes.append(f"片段 {cid} 换素材失败")
                continue
            candidates = list(getattr(out, "candidate_clips", None) or [])
            asset = None
            if candidates:
                suggested = candidates[0].get("suggested_assets") or []
                if suggested:
                    asset = suggested[0]
            # 空值守卫：无候选/无注册素材源时保持原片段，不得 IndexError
            new_asset_id = ""
            if asset:
                new_asset_id = asset.get("asset_id") or asset.get("url") or asset.get("local_path") or ""
            if not new_asset_id:
                notes.append("未找到替代素材，已保留原片段")
                continue
            clip.asset_id = new_asset_id
            merged = dict(clip.metadata or {})
            if asset.get("url"):
                merged["url"] = asset["url"]
            if asset.get("local_path"):
                merged["local_path"] = asset["local_path"]
            if asset.get("title"):
                merged["source_title"] = asset["title"]
            merged.setdefault("label", f"v_{cid}")
            clip.metadata = merged
            notes.append(f"片段 {cid} 已更换素材")
        return timeline, notes

    async def _edit_redo_animation(
        self,
        timeline: Timeline,
        selected_clip_ids: list[str],
        session_id: str,
        brief_data: dict | None,
        plan_data: dict | None,
        user_inputs: dict[str, Any],
    ) -> tuple[Timeline, list[str]]:
        """重做动画：构建只含选中 clip 的子集时间线 → AnimationAgent → 按 id 合并回当前时间线。"""
        from clipwright.agents.animation_agent import AnimationAgent
        from clipwright.schema.agent import AgentContext, AnimationInput

        persona_id = user_inputs.get("persona_id", "default")
        plugin_id = user_inputs.get("category_plugin_id", "knowledge_longform")
        topic = user_inputs.get("topic", "")
        context = AgentContext(
            pipeline_id=session_id, persona_id=persona_id,
            category_plugin_id=plugin_id, topic=topic, extra_params=dict(user_inputs),
        )

        selected = set(selected_clip_ids)
        subset_tracks: list[Track] = []
        for track in timeline.tracks:
            keep = [c for c in track.clips if c.id in selected]
            if keep:
                subset_tracks.append(Track(
                    id=track.id, name=track.name, kind=track.kind, index=track.index,
                    clips=keep, locked=track.locked, muted=track.muted,
                ))
        if not subset_tracks:
            return timeline, ["未找到选中片段，未执行动画重做"]

        subset = Timeline(
            id=timeline.id, width=timeline.width, height=timeline.height,
            fps=timeline.fps, duration_sec=timeline.duration_sec, tracks=subset_tracks,
        )
        visual_config: dict[str, Any] = {}
        if isinstance(brief_data, dict):
            anim_style = brief_data.get("animation_style")
            if isinstance(anim_style, dict):
                visual_config = anim_style
        try:
            out = await AnimationAgent().execute(
                AnimationInput(
                    context=context, timeline=subset, visual_config=visual_config,
                    creative_brief=brief_data, production_plan=plan_data,
                ),
                context,
            )
        except Exception as e:
            logger.exception("动画重做失败: %s", e)
            return timeline, [f"动画重做失败：{str(e)[:100]}"]

        new_tl = out.timeline
        if new_tl is None or not new_tl.tracks:
            return timeline, ["动画重做无结果，时间线未变化"]

        # 按 id 合并回当前时间线；新增 clip 追加到其轨道
        out_by_id: dict[str, Any] = {}
        for tr in new_tl.tracks:
            for c in tr.clips:
                out_by_id[c.id] = c
        merged_count = 0
        for track in timeline.tracks:
            for i, clip in enumerate(track.clips):
                if clip.id in out_by_id:
                    track.clips[i] = out_by_id.pop(clip.id)
                    merged_count += 1
        for c in out_by_id.values():
            for track in timeline.tracks:
                if track.id == c.track_id:
                    track.clips.append(c)
                    break
        return timeline, [f"已重做动画（更新 {merged_count} 个片段）"]

    async def _edit_adjust(
        self,
        timeline: Timeline,
        selected_clip_ids: list[str],
        user_message: str,
    ) -> tuple[Timeline, list[str]]:
        """数值调整：LLM 解析 ops → 白名单字段 + 边界校验后应用。"""
        selected = set(selected_clip_ids)
        ops: list[dict[str, Any]] = []
        try:
            resp = await self._llm.structured_output(
                system_prompt=(
                    "你是时间线数值调整解析器。根据用户指令输出对选中片段的属性调整操作，仅输出符合 schema 的 JSON："
                    "{\"ops\":[{\"clip_id\":\"...\",\"field\":\"...\",\"value\":...}]}。"
                    "clip_id 必须是选中片段之一。field 取值：speed/volume/opacity/duration_sec/start_sec/"
                    "source_offset_sec/font_size/font_color/text。value 为数值或字符串。"
                    "若指令不涉及任何数值调整，输出 {\"ops\":[]}。"
                ),
                user_prompt=f"选中片段: {sorted(selected)}\n指令: {user_message}",
                output_schema={
                    "type": "object",
                    "properties": {
                        "ops": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "clip_id": {"type": "string"},
                                    "field": {"type": "string"},
                                    "value": {},
                                },
                                "required": ["clip_id", "field", "value"],
                            },
                        }
                    },
                    "required": ["ops"],
                },
                temperature=0,
                max_tokens=256,
                pipeline_id="",
                use_flash=True,
            )
            ops = (resp or {}).get("ops") or []
            await self._record_llm_usage("requirements.edit_adjust")
        except Exception as e:
            logger.warning("数值调整解析失败: %s", e)
            return timeline, ["无法解析调整指令"]

        notes: list[str] = []
        applied = 0
        for op in ops:
            cid = op.get("clip_id")
            field = op.get("field")
            value = op.get("value")
            if cid not in selected or field not in _ADJUST_FIELDS:
                continue
            found = self._find_clip(timeline, cid)
            if not found:
                continue
            _track, clip = found
            try:
                kind, clamp_fn = _ADJUST_FIELDS[field]
                if kind is float:
                    setattr(clip, field, clamp_fn(value))
                else:
                    setattr(clip, field, clamp_fn(value))
                applied += 1
            except (TypeError, ValueError):
                continue
        notes.append(f"已应用 {applied} 项调整")
        return timeline, notes

    async def _is_confirm(self, message: str) -> bool:
        """判断用户回复是否为对当前方案的确认/批准。

        策略：明确的确认/否定/提问先用启发式快速判定（可靠、零延迟），
        仅在语义模糊（如委婉表达、夹带修改意见）时才调用 LLM 判断。
        这样「确认，请生成规划书」这类明确确认不会被 LLM 误判为「提出新需求」。
        """
        msg = message.strip()
        if not msg:
            return False

        # ── 启发式优先处理明确情况 ──
        low = msg.lower()
        # 提问不是确认
        if low.endswith(("?", "？")):
            return False
        # 明确的强确认短语（任意位置出现即可）
        strong = ["已确认", "确认无误", "确认通过", "确认实施", "批准通过", "确认，请生成", "确认并"]
        if any(phrase in low for phrase in strong):
            return True
        # 以否定词开头 → 非确认（"不可以"、"不要"、"有问题"、"不满意"）
        if low.startswith(("不", "没", "别", "勿", "莫", "未")):
            return False
        # 以明确确认词开头 → 确认（"确认…"、"可以…"、"好的…"、"同意…"）
        affirm_starts = ["没问题", "就这样", "可以了", "好的", "确认", "同意", "可以",
                         "行", "ok", "yes", "y", "对", "嗯", "确定", "通过", "批准"]
        if any(low.startswith(kw) or low == kw for kw in affirm_starts):
            return True

        # ── 语义模糊 → 用 LLM 判断 ──
        try:
            resp = await self._llm.structured_output(
                system_prompt=(
                    "你是意图分类器。判断用户对一份已生成的视频方案/规划书的回复，"
                    "是否表示「确认 / 同意 / 批准 / 通过 / 就这样」（即接受当前方案、进入下一步）。\n"
                    "以下情况都【不是】确认：否定（不可以、不要、有问题、不满意）、"
                    "提问（这样行吗？可以吗？）、以及提出任何修改意见或新需求。\n"
                    "仅输出符合 schema 的 JSON，不要输出其他内容。"
                ),
                user_prompt=f"用户回复：{msg}",
                output_schema={
                    "type": "object",
                    "properties": {"is_confirm": {"type": "boolean"}},
                    "required": ["is_confirm"],
                },
                temperature=0,
                max_tokens=16,
                pipeline_id="",
                use_flash=True,  # 简单意图判断 → flash 轻量模型
            )
            if isinstance(resp, dict) and "is_confirm" in resp:
                await self._record_llm_usage("requirements.confirm")
                return bool(resp["is_confirm"])
        except Exception as e:
            logger.debug("LLM 确认判断失败，回退关键词启发式: %s", e)
        return self._is_confirm_heuristic(msg)

    @staticmethod
    def _is_confirm_heuristic(message: str) -> bool:
        """关键词启发式确认判断（LLM 不可用时的降级方案）。"""
        msg = message.strip().lower()
        # Questions are not confirmations
        if msg.endswith(("?", "？")):
            return False
        # Strong unambiguous affirmative phrases
        strong = ["已确认", "确认无误", "确认通过", "确认实施", "批准通过"]
        if any(phrase in msg for phrase in strong):
            return True
        # Negation markers invert any affirmation ("不可以", "不要就这样", "有问题")
        negations = ("不", "没", "别", "勿", "莫", "未")
        if any(neg in msg for neg in negations):
            return False
        # Start-only affirmative tokens
        start_tokens = [
            "没问题", "就这样", "可以了", "好的", "确认", "同意", "可以",
            "行", "ok", "yes", "y", "对", "嗯", "确定", "通过", "批准",
        ]
        return any(msg.startswith(kw) or msg == kw for kw in start_tokens)

    # ── LLM 需求收集 ──────────────────────────

    async def _handle_gathering(
        self, messages: list[dict], brief_data: dict | None, status: str,
        user_inputs: dict | None = None, session_id: str = "",
    ) -> dict:
        """调用 LLM 收集需求（注入 Persona 上下文）。"""
        context = ""
        if brief_data:
            context = f"\n\n当前方案草稿:\n{json.dumps(brief_data, ensure_ascii=False, indent=2)}"

        # 注入完整 Persona 上下文
        persona_full = self._build_full_persona_context(user_inputs or {})
        if persona_full:
            persona_text = json.dumps(persona_full.get("config", {}), ensure_ascii=False, indent=2)
            persona_prompt = persona_full.get("prompt", "")
            context += f"\n\n## 创作者完整风格配置\n{persona_text}"
            if persona_prompt:
                context += f"\n\n## 创作者风格指引 (Prompt)\n{persona_prompt}"

        persona_id = user_inputs.get("persona_id", "default") if user_inputs else "default"
        topic = user_inputs.get("topic", "") if user_inputs else ""
        script = user_inputs.get("script_text", "") if user_inputs else ""
        rag_context = await self._retrieve_knowledge(persona_id, f"{topic} {script}", session_id)
        if rag_context:
            context += f"\n\n## 知识库参考\n{rag_context}"

        user_prompt = messages[-1]["content"] if messages else "请开始对话。"
        if script:
            user_prompt = (
                f"## 用户提供的原始文稿（请基于此文稿生成创作方案）\n"
                f"{script[:12000]}\n\n"
                f"## 用户最新输入\n{user_prompt}"
            )
        llm_kwargs = {
            "system_prompt": CREATIVE_BRIEF_SYSTEM + context,
            "user_prompt": user_prompt,
        }
        # W1: 联网搜索工具门控接入（Bocha/百度）——配置开启时 LLM 可自主搜索；
        # 未配置/失败/无工具调用时行为与现状完全一致（落到下方 llm_call_with_retry 原路径）。
        try:
            from clipwright.services.web_search import WebSearchService
            from clipwright.tool.registry import ToolRegistry

            if WebSearchService().is_configured():
                tool_schemas = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or f"Execute {tool.name}",
                            "parameters": tool.to_llm_tool("openai")["function"]["parameters"],
                        },
                    }
                    for tool in ToolRegistry.list_agent_callable()
                ]
                if tool_schemas:
                    resp = await asyncio.wait_for(
                        self._llm.with_tools(
                            **llm_kwargs,
                            tool_executor=_web_tool_executor,
                            tools=tool_schemas,
                            pipeline_id="",
                        ),
                        timeout=BRIEF_GENERATE_TIMEOUT,
                    )
                    # 解析 with_tools 返回的 content（复用 structured_output 的 JSON 提取逻辑）
                    content = getattr(resp, "content", "") or ""
                    await self._record_llm_usage("requirements.gathering", session_id)
                    parsed = self._parse_llm_json(content)
                    if parsed:
                        return parsed
                    # content 非 JSON（如 LLM 直接回复文本）→ 构造与现状一致的返回
                    default_reply = "请继续描述你的想法。"
                    return {
                        "reply": content if isinstance(content, str) and content else default_reply,
                        "brief_draft": {},
                        "is_ready": False,
                    }
        except Exception as e:
            logger.warning("requirements chat with_tools 路径失败，回退原路径: %s", e)
        # 原路径：未配置 / 无可用工具 / with_tools 异常或超时 → 行为与现状完全一致
        try:
            result = await asyncio.wait_for(
                llm_call_with_retry(self._llm, "creative_brief", pipeline_id="", **llm_kwargs),
                timeout=BRIEF_GENERATE_TIMEOUT,
            )
            await self._record_llm_usage("requirements.gathering", session_id)
            return result
        except asyncio.TimeoutError:
            logger.warning("创意简报生成超时（>%ds），返回空方案", BRIEF_GENERATE_TIMEOUT)
            return {
                "reply": "生成创意简报时超时，请再试一次或补充描述。",
                "brief_draft": {},
                "is_ready": False,
                "missing_info": ["请重新描述创作需求"],
            }

    @staticmethod
    def _parse_llm_json(content: Any) -> dict | None:
        """解析 LLM 返回的 JSON（复用 structured_output 的 fence 剥离 + json.loads 逻辑）。

        成功返回 dict；非字符串 / 非 JSON / 非 dict（如数组或文本）一律返回 None。
        """
        if not isinstance(content, str):
            return None
        content = content.strip().lstrip("\ufeff")
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(line for line in lines if not line.startswith("```"))
        if not content:
            return None
        try:
            result = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return None
        return result if isinstance(result, dict) else None

    @staticmethod
    def _build_full_persona_context(user_inputs: dict) -> dict:
        """加载 Persona 的完整参数配置和 Prompt 指引（E5：按 persona_id 缓存 TTL 10min）。"""
        persona_id = user_inputs.get("persona_id", "")
        if not persona_id:
            return {}
        now = time.time()
        cached = _persona_context_cache.get(persona_id)
        if cached and (now - cached[1]) < _SESSION_CACHE_TTL:
            return dict(cached[0])
        try:
            from clipwright.persona.loader import load_persona_by_id
            manifest = load_persona_by_id(persona_id)
            if not manifest:
                return {}
            result = {
                "config": manifest.parameter.model_dump(mode="json") if manifest.parameter else {},
                "prompt": manifest.prompt or "",
            }
            _persona_context_cache[persona_id] = (result, now)
            return result
        except Exception as e:
            logger.debug("Persona 上下文加载失败: %s", e)
            return {}

    async def _retrieve_knowledge(self, persona_id: str, query: str, session_id: str = "") -> str:
        """检索 Persona 知识库并返回可注入 Prompt 的上下文（E5：会话级缓存 TTL 10min）。"""
        if session_id:
            now = time.time()
            cached = _session_knowledge_cache.get(session_id)
            if cached and cached[0] == query and (now - cached[2]) < _SESSION_CACHE_TTL:
                return cached[1]
        try:
            from clipwright.rag.retriever import Retriever
            retriever = Retriever()
            result = await retriever.retrieve(persona_id=persona_id, query=query)
            context = result.context if result and result.context else ""
            if session_id:
                _session_knowledge_cache[session_id] = (query, context, time.time())
            return context
        except Exception:
            return ""

    # ── 规划书生成 ──────────────────────────

    async def _generate_plan(
        self, brief_data: dict | None, user_inputs: dict, session_id: str,
        feedback: str = "", existing_raw_scenes: list | None = None,
    ) -> dict | None:
        """调用 StructureAgent（注入 Persona）并翻译为规划书。

        B6/E2：规划书修订路径——当已有 raw_scenes（简报未变、本会话已生成过场景）时
        跳过 StructureAgent 重新生成，仅带反馈重新 _translate_plan，避免重复完整 LLM 生成。
        """
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

            # 提取 animation_intents
            animation_intents = []
            if brief_data:
                raw_intents = brief_data.get("animation_intents", [])
                if isinstance(raw_intents, list):
                    animation_intents = raw_intents

            # B6/E2: 复用已确认的 raw_scenes 修订路径——不重跑 StructureAgent，
            # 仅带反馈重新翻译场景（简报未变、已有场景时）。
            if isinstance(existing_raw_scenes, list) and existing_raw_scenes:
                logger.info(
                    "规划书修订: 复用 %d 个已确认场景（跳过 StructureAgent），feedback=%s",
                    len(existing_raw_scenes), feedback[:50],
                )
                web_context = await _build_web_context(
                    f"{topic} {user_inputs.get('script_text', '')}"[:300]
                )
                return await self._translate_plan(
                    existing_raw_scenes, brief_data,
                    user_inputs.get("script_text", ""), feedback=feedback,
                    web_context=web_context,
                )

            agent = StructureAgent()
            context = AgentContext(
                pipeline_id=session_id,
                persona_id=persona_id,
                category_plugin_id=plugin_id,
                topic=topic,
                extra_params={
                    "video_mode": user_inputs.get("video_mode", "voiceover"),
                    "script_text": user_inputs.get("script_text", ""),
                    "audio_duration_sec": user_inputs.get("audio_duration_sec", 300),
                    "animation_intents": animation_intents,
                    "dub_segments": user_inputs.get("dub_segments", []),
                },
            )

            script = user_inputs.get("script_text", "")
            rag_context = await self._retrieve_knowledge(persona_id, f"{topic} {script}", session_id)

            # W2: 注入联网搜索结果作为事实参考（未配置/失败/空结果 → 零变化）
            web_context = await _build_web_context(f"{topic} {script}"[:300])
            if web_context:
                if rag_context:
                    rag_context += f"\n\n## 联网搜索参考\n{web_context}"
                else:
                    rag_context = f"## 联网搜索参考\n{web_context}"

            # A2: 注入素材库概览（空素材库 → 无额外段落，零变化）
            overview = _material_library_overview()
            if overview:
                if rag_context:
                    rag_context += f"\n\n## 素材库概览\n{overview}"
                else:
                    rag_context = f"## 素材库概览\n{overview}"

            result = await agent.execute(
                StructureInput(
                    context=context,
                    persona_config=persona_config,
                    persona_prompt=persona_prompt,
                    rag_context=rag_context,
                    creative_brief=brief_data,
                ),
                context,
            )
            # C2: StructureAgent 的 LLM 用量（requirements 会话级，非 pipeline）
            agent_usage = getattr(result, "_llm_usage", None)
            if agent_usage:
                try:
                    from clipwright.services.llm_tracker import record_llm_call
                    await record_llm_call(
                        pipeline_id=session_id or "requirements",
                        agent_name="requirements.structure",
                        model=agent_usage.get("model", "unknown"),
                        provider=agent_usage.get("provider", "unknown"),
                        input_tokens=agent_usage.get("input_tokens", 0),
                        output_tokens=agent_usage.get("output_tokens", 0),
                        duration_ms=0,
                    )
                except Exception as e:
                    logger.warning("StructureAgent 用量记录失败: %s", e)
            scenes = result.scenes or []
            if not scenes:
                logger.warning("StructureAgent 返回空场景")
                return None

            return await self._translate_plan(
                scenes, brief_data, script, feedback=feedback, web_context=web_context,
            )

        except Exception as e:
            logger.exception("规划书生成失败: %s", e)
            return None

    async def _translate_plan(
        self, scenes: list[dict], brief_data: dict | None, script_text: str = "",
        feedback: str = "", web_context: str = "",
    ) -> dict:
        """翻译场景为规划书。"""
        scenes_json = json.dumps(scenes, ensure_ascii=False, indent=2)
        brief_json = json.dumps(brief_data, ensure_ascii=False) if brief_data else "{}"

        system_prompt = PLAN_TRANSLATE_SYSTEM
        if feedback:
            system_prompt += (
                f"\n\n## 用户修改意见（规划书必须体现这些修订）\n{feedback[:2000]}"
            )
        if script_text:
            system_prompt += f"\n\n## 原始文稿（规划书必须忠实反映此内容）\n{script_text[:8000]}"
        system_prompt += f"\n\n参考方案:\n{brief_json}"
        if web_context:
            system_prompt += f"\n\n## 联网搜索参考\n{web_context}"

        try:
            result = await asyncio.wait_for(
                llm_call_with_retry(self._llm, "plan_translate", pipeline_id="", **{
                    "system_prompt": system_prompt,
                    "user_prompt": f"结构 Agent 输出:\n{scenes_json}",
                }),
                timeout=PLAN_TRANSLATE_TIMEOUT,
            )
            await self._record_llm_usage("requirements.plan_translate")
        except asyncio.TimeoutError:
            # LLM 翻译超时 → 使用基础规划书，绝不挂起
            logger.warning("规划书翻译超时（>%ds），使用基础规划书", PLAN_TRANSLATE_TIMEOUT)
            result = {
                "summary": "规划书已生成（LLM 翻译超时，使用基础版本）。",
                "sections": [],
                "markdown_content": self._default_markdown(scenes),
                "total_duration_sec": sum(s.get("duration_sec", 0) for s in scenes),
                "scene_count": len(scenes),
            }

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
        lines.append("| 场景标题 | 时长 | 口播脚本 | 画面描述 |")
        lines.append("|---|---|---|---|")
        for scene in scenes:
            title = str(scene.get("title", "")).replace("|", "\\|").replace("\n", "<br>")
            voiceover = str(scene.get("voiceover_script", "")).replace("|", "\\|").replace("\n", "<br>")
            visual = str(scene.get("visual_description", "")).replace("|", "\\|").replace("\n", "<br>")
            lines.append(
                f"| {title} | {scene.get('duration_sec', 0)}s | {voiceover} | {visual} |"
            )
        total = sum(s.get("duration_sec", 0) for s in scenes)
        lines.append(f"\n**总时长**: {total:.0f}s ({total/60:.1f}分钟)\n---\n")
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
        if session_id in _memory_sessions:
            _memory_sessions[session_id].update({
                "status": status,
                "messages": messages,
                "creative_brief": brief,
                "production_plan": plan,
            })

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
            elif ext in ("png", "jpg", "jpeg", "webp", "gif"):
                # C6: 附件图片理解 — 视觉模型描述图片并提取标签，替代占位符
                content = await self._describe_image(file_path, file_name)
            else:
                content = f"[上传文件: {file_name}]"
        except Exception as e:
            content = f"[文件读取失败: {e}]"

        msg = (
            f"用户上传了参考文件 **{file_name}**，内容摘要如下：\n\n```\n{content[:1000]}\n```"
            if content else f"用户上传了文件: {file_name}"
        )

        # P0-6: process_upload 为 async 上下文 → 线程内执行同步 Mongo 操作
        session = await asyncio.to_thread(self.get_session, session_id)
        if session:
            messages = session.get("messages", [])
            messages.append({
                "role": "user",
                "content": msg,
                "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                "metadata": {"file": file_name, "type": "upload"},
            })
            await asyncio.to_thread(
                self._persist,
                session_id, session.get("status", "gathering"),
                messages, session.get("creative_brief"),
                session.get("production_plan"), session.get("user_inputs", {}),
            )

        return {"content_preview": content[:500], "file_name": file_name}

    async def _describe_image(self, file_path: str, file_name: str) -> str:
        """C6: 附件图片理解 — 用 VisionService 提取描述/标签，失败时返回占位。"""
        try:
            from clipwright.services.vision import VisionService
            result = await VisionService().analyze_image(file_path)
            desc = result.get("description") or ""
            tags = result.get("tags") or []
            labels = result.get("labels") or []
            parts = [f"[图片附件: {file_name}]"]
            if desc:
                parts.append(f"画面描述：{str(desc)[:400]}")
            if tags:
                parts.append(f"识别标签：{'、'.join(str(t) for t in tags[:12])}")
            if labels:
                parts.append(f"分类：{'、'.join(str(l) for l in labels[:8])}")
            return "\n".join(parts)[:1200]
        except Exception as e:
            logger.info("图片理解失败（非致命，回退占位）: %s", e)
            return f"[图片附件: {file_name}（自动理解失败，可手动描述）]"

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
        """SSE 流式推送对话结果（typing → result）。

        已知限制 / Known limitation: 并非真正的 token-by-token 流式——底层 LLM 调用为非流式
        （structured_output），完整响应先缓冲再一次性推送；本次范围内不做重构。
        """
        # 1. 先返回用户消息确认 + typing 指示
        yield {"type": "status", "data": "typing"}

        # 2. 异步处理完整对话
        result = await self.chat(session_id, user_message)

        # 3. 推送结果
        yield {"type": "result", "data": result}
