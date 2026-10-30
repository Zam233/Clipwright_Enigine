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
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional

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
from clipwright.services.llm import LLMService, MAX_OUTPUT_TOKENS
from clipwright.services.trace import add_event
from clipwright.schema.timeline import Timeline, Track

# ── 常量 ──────────────────────────────────────────

MAX_HISTORY_ROUNDS = 20          # 超过 20 轮对话后压缩
MAX_MESSAGE_LENGTH = 50000       # 单条消息最大字符

#: ask_user 的 owner 类型（PRD §15.5）：需求会话问题归属会话，不归属任务
REQUIREMENTS_OWNER = "requirements"

#: 本轮流式被回退的会话（session_id -> 原因）。unified-stream PRD §5.1.3：
#: 回退必须让前端看得见，不能只写 warning 就静默降级。
_STREAM_FALLBACK: dict[str, str] = {}


def _emit_session_thinking(session_id: str, text: str, *, phase: str = "requirements",
                           turn_ts: int | None = None) -> None:
    """把本轮思考落进需求会话事件流（按轮一条，刷新/重连可回放）。

    复用既有 `thinking` 事件类型（契约仍是 29 种），`detail.phase="requirements"`
    让前端能区分"需求对话的思考"与"任务的思考"。写入失败不影响对话本身。
    """
    body = (text or "").strip()
    if not body or not session_id:
        return
    try:
        from clipwright.services import ask_user as _ask_user
        from clipwright.services.task_events import emit

        emit(_ask_user.session_stream_id(session_id), "thinking",
             f"思考 {len(body)} 字",
             {"phase": phase, "delta": body[:200_000], "done": True,
              "chars": len(body),
              # 本轮**开始**时刻（ms）。前端用它排序：晚到的事件也不能改变位置
              # ——否则思考会被排到整段对话末尾（用户 2026-10-31 实测）。
              "turn_ts": int(turn_ts if turn_ts is not None else time.time() * 1000)},
             span="requirements")
    except Exception as exc:  # noqa: BLE001 — 落盘失败不阻断对话
        logger.warning("需求会话思考落盘失败 %s: %s", session_id, exc)


# ── 时间线（session-log 单一真相，PRD docs/agent-session-timeline-prd-2026-10-31.md）──


def _timeline(session_id: str):
    """取会话时间线日志（唯一有序流）。"""
    from clipwright.services import session_log

    return session_log.get_log(session_id)


def _brief_hash(brief: dict | None) -> str:
    """简报指纹：让"这份规划书对应哪版 brief"可判定（PRD §3.2）。"""
    if not isinstance(brief, dict) or not brief:
        return ""
    try:
        raw = json.dumps(brief, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _timeline_turn(session_id: str):
    """开一轮时间线（返回 `(log, Turn)`）；日志不可用时不阻断对话。"""
    try:
        from clipwright.services import session_log

        log = session_log.get_log(session_id)
        turn_no = log.user_turns() + 1
        return log, session_log.Turn(log, turn=turn_no)
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话时间线不可用 %s: %s", session_id, exc)
        return None, None


_BACKFILL_SERVICE: Any = None


def _backfill_service() -> Any:
    """回填用的服务实例（懒建；只读 get_session，不碰 LLM 调用）。"""
    global _BACKFILL_SERVICE
    if _BACKFILL_SERVICE is None:
        _BACKFILL_SERVICE = RequirementsService()
    return _BACKFILL_SERVICE


def ensure_timeline_backfilled(session_id: str, session: dict | None = None) -> int:
    """存量会话回填（T1，PRD agent-timeline-adaptation §4）。

    升级前建的会话磁盘上没有条目，前端打开就是空的（用户看到「还没有可显示的内容」）。
    这里把会话里**还留着**的 `messages` 补成条目；简报/规划书各补一条卡片条目，
    否则老会话点开只剩文字、卡片消失。

    幂等：日志里已有任何条目就直接返回 0（不会重复回填）；写失败不影响打开会话。
    """
    if not session_id:
        return 0
    try:
        from clipwright.services import session_log

        log = session_log.get_log(session_id)
        if not log.is_empty():
            return 0
        data = session
        if data is None:
            data = _backfill_service().get_session(session_id) or {}
        return log.backfill_messages(
            data.get("messages") or [],
            brief=data.get("creative_brief"),
            plan=data.get("production_plan"),
            brief_hash_fn=_brief_hash,
        )
    except Exception as exc:  # noqa: BLE001 — 回填失败不阻断查看会话
        logger.warning("会话 %s 时间线回填失败: %s", session_id, exc)
        return 0

LLM_RETRY_MAX = 3                # LLM 最大重试次数
LLM_RETRY_BASE_DELAY = 2.0      # 重试基础延迟（秒）
BRIEF_GENERATE_TIMEOUT = 900     # 简报生成单次调用超时（秒）
# E2E 2026-10-28 实测：规划书翻译**必须显式给 max_tokens**。不给时下发的是
# provider 默认（deepseek 系 4096），推理型模型把预算全用在 reasoning 上，
# HTTP 200 但正文为空（llm.py:692 那条告警），三次重试全空 → 规划书退化成
# 「LLM 翻译超时，使用基础版本」的兜底表。8192 让 6 场景的规划书一次过。
PLAN_TRANSLATE_MAX_TOKENS = MAX_OUTPUT_TOKENS
# 场景改写（把修改意见落到 raw_scenes 本体）的输出是完整 JSON 数组，
# 同样要显式给预算，否则正文必然为空。
PLAN_SCENE_REVISE_MAX_TOKENS = MAX_OUTPUT_TOKENS
# 超时必须容得下真实生成：实测带 8000 字原文 + 43 段场景的翻译，推理型模型
# 单次就要 6-8 分钟，"420s 超时"其实是把还在正常生成的请求掐断了（日志只写
# 「翻译超时」，看起来像模型没输出）。宁可等，也不要制造假的空结果。
PLAN_TRANSLATE_TIMEOUT = 1800    # 规划书翻译超时（秒）——按"上限"给足
# 精简 prompt 的重试超时：输入已缩小，但仍有 43 段场景，别再按 5 分钟掐
PLAN_TRANSLATE_RETRY_TIMEOUT = 1800
# 需求侧跑 structure 阶段（生成规划书用的 raw_scenes）的墙钟上限。
# 旧实现沿用 run_inline 默认 300s = StructureAgent 自身的 LLM 超时，
# 外层先掐断 → TimeoutError(str 为空) → 只留一句"规划书生成失败:"。
PLAN_STRUCTURE_TIMEOUT = 3600
SESSION_TTL_HOURS = 48           # 会话过期时间
SESSION_CLEANUP_INTERVAL = 3600  # 清理检查间隔（秒）

# 时间线数值调整字段白名单 + 边界钳制（R5 起**唯一事实来源在 edit_intent.FIELD_SPECS**，
# 此处保留别名以兼容既有调用与测试；新增字段请改 edit_intent.py，不要在这里加第二份）。
from clipwright.services.edit_intent import FIELD_SPECS as _FIELD_SPECS

_ADJUST_FIELDS: dict[str, tuple[type, Any]] = dict(_FIELD_SPECS)

#: 会改变片段占用时长的字段（改完必须收拢，否则与相邻片段重叠）。
#: 从 `intent_apply` 取同一份清单，避免两处各写一份而漂移。
from clipwright.services.intent_apply import (  # noqa: E402
    MIN_CLIP_SEC as _MIN_CLIP_SEC,
    STRUCTURAL_DURATION_FIELDS as _STRUCTURAL_DURATION_FIELDS,
)


_memory_sessions: dict[str, dict] = {}

# 会话级缓存 — RAG 检索 (session_id → (query, result, ts)) 与 Persona 上下文 (persona_id → (result, ts))。
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
        # 显式降级标记：调用方（_translate_plan）据此判断"这次并没有拿到正文"，
        # 从而触发降规模重试，而不是把兜底文案当成成功结果（旧实现靠
        # markdown_content 是否为空判断，兜底文案非空 → 永远不重试）。
        "degraded": True,
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


# AI 生成能力概览（D4）：三个生成插件的工具与展示名，可用性以 is_available() 为准
_AI_GEN_TOOLS = [
    ("ai_image_generate", "图片生成"),
    ("ai_video_generate", "视频生成"),
    ("ai_music_generate", "音乐生成"),
]


def _ai_generation_overview() -> str:
    """生成 AI 生成能力概览一行（D4）。无任何可用生成工具时返回 ""，保证零变化。

    可用性即各工具 is_available()（凭据配置检测）——未配置的工具不会被
    Agent 暴露给 LLM，此处也不呈现，保持「能力即已配置」的一致口径。
    """
    try:
        from clipwright.tool.registry import ToolRegistry
        available = []
        for name, label in _AI_GEN_TOOLS:
            tool = ToolRegistry.get(name)
            if tool is not None and tool.is_available():
                available.append(label)
        if not available:
            return ""
        return (
            f"AI 生成能力（已配置可用）: {', '.join(available)}。"
            "素材库难以命中的画面（具象角色/特定品牌/概念视觉）可建议用户采用 AI 生成素材，"
            "生成产物会自动入库并在后续素材阶段编入时间线。"
        )
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
注意：reply 字段必须是 JSON 对象的第一个键（前端依赖此顺序实现流式渐进显示）。
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
    "asset_ratio": {"footage": "30-40%", "mg": "60-70%", "ai_generated": "AI 生成素材占比（可选，建议采用 AI 生成时填写，无则留空）"}
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
                # 线程内执行（_io 在事件循环线程会返回未 await 的协程导致清理静默失效）
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

def apply_plan_drift(
    plan: dict | None,
    drifts: Iterable[dict[str, Any]],
    *,
    max_entries: int = 20,
) -> dict:
    """V58：把执行偏离**并入规划书对象**（写回的那一半，纯函数，可单测）。

    规则：
    - 规划书原样保留（段落/卡点/缺口一律不动），只在 ``_drift`` 下追加偏离；
    - 幂等：同 ``(code, index)`` 只留最后一条——重跑同一条管线不会把记录刷成一堆；
    - 最多保留 ``max_entries`` 条（超了丢最旧的），避免规划书被日志撑爆。
    """
    merged: dict[str, Any] = dict(plan or {})
    existing = list(merged.get("_drift") or [])
    for item in drifts or []:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        key = (record.get("code"), record.get("index"))
        existing = [
            old for old in existing
            if (old.get("code"), old.get("index")) != key
        ]
        existing.append(record)
    if existing:
        merged["_drift"] = existing[-max_entries:]
    return merged


def write_back_plan_drift(session_id: str, drifts: Iterable[dict[str, Any]]) -> int:
    """把偏离写回会话里的规划书（内存缓存 + Mongo）。返回本次并入的条数。

    失败只记日志——写回是留痕，不允许影响出片。
    """
    items = [dict(d) for d in (drifts or []) if isinstance(d, dict)]
    if not session_id or not items:
        return 0
    try:
        session = _memory_sessions.get(session_id)
        model = None
        if _mongo_ok():
            model = RequirementsSessionModel.find_by_id(session_id)
        plan = (session or {}).get("production_plan")
        if plan is None and model is not None:
            plan = getattr(model, "production_plan", None)
            if plan is None and hasattr(model, "to_dict"):
                plan = (model.to_dict() or {}).get("production_plan")
        merged = apply_plan_drift(plan if isinstance(plan, dict) else None, items)
        wrote = False
        if session is not None:
            session["production_plan"] = merged
            wrote = True
        if model is not None:
            model.production_plan = merged
            model.updated_time = datetime.now(tz=TIME_ZONE)
            model.update()
            wrote = True
        # 没有任何落点（会话不存在）→ 如实返回 0，不假装写回成功
        return len(items) if wrote else 0
    except Exception as e:  # noqa: BLE001 - 写回失败不得影响出片
        logger.warning("规划偏离写回失败（已跳过）: %s", e)
        return 0


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
        # 时间线：开场白也是流里的一条（type=content, role=assistant, turn=0），
        # 否则刷新后"欢迎语"没有位置（PRD §3.2）
        try:
            _timeline(session_id).append(
                type="content", content=welcome, source="requirements",
                turn=0, meta={"role": "assistant", "kind": "welcome"},
            )
        except Exception as exc:  # noqa: BLE001 — 时间线失败不阻断建会话
            logger.warning("时间线写开场白失败 %s: %s", session_id, exc)
        logger.info("需求会话已创建: %s (mongo=%s)", session_id, _mongo_ok())
        return session_dict

    async def load_or_create_session(
        self, session_id: str, user_inputs: dict | None = None
    ) -> dict:
        """加载现有会话，不存在则创建。"""
        if not session_id:
            return self.create_session(user_inputs)
        # _io() 在事件循环线程返回协程 → 必须 offload 到线程执行
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

    def list_messages(
        self, session_id: str, *, before_id: str = "", limit: int = 50,
    ) -> dict[str, Any]:
        """需求消息历史分页（unified-stream PRD §M4）。

        `before_id` = 上一页最早消息的 id（不含）；不传则取最新一页。
        返回 `{items, has_more, oldest_id}`；id 由 `_ensure_message_ids` 保证稳定。
        """
        session = self.get_session(session_id) or {}
        msgs = self._ensure_message_ids(list(session.get("messages") or []))
        lim = max(1, min(int(limit or 50), 200))
        end = len(msgs)
        if before_id:
            for i, m in enumerate(msgs):
                if str(m.get("id")) == before_id:
                    end = i
                    break
        start = max(0, end - lim)
        page = msgs[start:end]
        return {
            "items": page,
            "has_more": start > 0,
            "oldest_id": page[0]["id"] if page else "",
            "total": len(msgs),
        }

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

    # ── ask_user（PRD §15.5 / M5c）──────────────────────────
    #
    # 需求对话没有工具轮（一次 LLM structured 生成就出方案），因此这里复用
    # 同一个 `ask_user` 服务与同一套 `question`/`answer` 事件语义，只把调用点
    # 放在 service：LLM 在结构化输出里给出 `ask_user` 指令 → 建问题（归属
    # `requirements:<session_id>` 的事件流）→ 会话进 `waiting_user`；
    # 用户回答走需求会话自己的 answer 路由 → agent 带着答案继续 gather。

    def questions(self, session_id: str) -> list[dict[str, Any]]:
        """该会话的全部问题（含已回答/已失效，按创建时间升序）。"""
        from clipwright.services import ask_user

        if not session_id:
            return []
        return ask_user.list_for_owner(REQUIREMENTS_OWNER, session_id)

    def pending_questions(self, session_id: str) -> list[dict[str, Any]]:
        from clipwright.services import ask_user

        if not session_id:
            return []
        return ask_user.pending(REQUIREMENTS_OWNER, session_id)

    def ask_user_question(
        self, session_id: str, *, question: str,
        options: Any = (), allow_free_text: bool = False,
        default_option_id: str | None = None, timeout_sec: int | None = None,
        context_refs: Any = None,
    ) -> dict[str, Any]:
        """建一个需求会话问题（不阻塞）。参数非法时抛 `ask_user.AskUserError`。"""
        from clipwright.services import ask_user

        return ask_user.create(
            REQUIREMENTS_OWNER, session_id,
            question=question, options=options or (),
            allow_free_text=bool(allow_free_text),
            default_option_id=default_option_id,
            timeout_sec=int(timeout_sec or ask_user.DEFAULT_TIMEOUT_SEC),
            context_refs=context_refs or [],
        )

    async def answer_question(
        self, session_id: str, *, question_id: str,
        option_id: str | None = None, text: str | None = None,
    ) -> dict[str, Any]:
        """回答问题并让 agent 继续。

        返回 `{"answer": {...}, "reply": ..., "status": ...}`；错误以
        `{"error": code, "http_status": n}` 形式如实返回（不假装已收到答案）。
        """
        from clipwright.services import ask_user

        sess = await asyncio.to_thread(self.get_session, session_id)
        if not sess:
            return {"error": "Session not found", "http_status": 404}
        rec = ask_user.get(question_id)
        if not rec:
            return {"error": "question_not_found", "http_status": 410}
        # 归属校验：不能拿别的会话/任务的问题 id 来答
        if rec.get("owner_type") != REQUIREMENTS_OWNER or rec.get("owner_id") != session_id:
            return {"error": "question_not_found", "http_status": 404}
        try:
            ans = ask_user.answer(question_id, option_id=option_id, text=text)
        except ask_user.AskUserError as exc:
            return {"error": exc.code, "http_status": exc.http_status, **exc.detail}

        # 用答案作为用户消息让对话继续（agent 拿到拍板结果后接着干）
        answer_text = self._answer_as_message(rec, ans)
        # 时间线：先落一条 `answer`（用户拍板结果），顺序保证 question → answer → 后续思考/正文。
        # 注意 `chat()` 随后还会写一条 `content(role=user)`——那是**喂给模型的文本**，
        # 与"用户拍板了什么"语义不同，两条并存不算重复（PRD agent-timeline-adaptation T3.4）。
        try:
            _log = _timeline(session_id)
            _log.append(
                type="answer", content=answer_text, source="requirements",
                turn=_log.user_turns() + 1, ref=f"q:{question_id}",
                meta={
                    "question_id": question_id,
                    "option_id": option_id,
                    "text": text,
                    "question": str(rec.get("question") or ""),
                    "answer_source": (ans or {}).get("source") if isinstance(ans, dict) else None,
                },
            )
        except Exception as exc:  # noqa: BLE001 — 时间线写失败不阻断作答
            logger.warning("时间线写 answer 失败 %s: %s", session_id, exc)
        result = await self.chat(session_id, answer_text)
        return {"answer": ans, **result}

    @staticmethod
    def _answer_as_message(rec: dict[str, Any], ans: dict[str, Any]) -> str:
        """把答案还原成一句人话喂回对话（LLM 只认 user 消息）。"""
        options = {str(o.get("id")): str(o.get("label") or o.get("id"))
                   for o in (rec.get("options") or []) if isinstance(o, dict)}
        opt = str(ans.get("option_id") or "")
        label = options.get(opt)
        if label:
            return f"关于「{rec.get('question', '')}」，我选：{label}"
        return f"关于「{rec.get('question', '')}」，我的回答：{ans.get('text') or opt}"

    async def _ask_from_llm(self, result: dict[str, Any], session_id: str) -> dict[str, Any] | None:
        """LLM 结构化输出里的 `ask_user` 指令 → 建问题。

        结构（宽容解析，字段缺失就给安全默认）：
        `{"ask_user": {"question": str, "message": str?, "options": [{"id","label","detail"}],
                       "allow_free_text": bool, "default_option_id": str?, "timeout_sec": int?}}`

        参数非法（无问题文本 / 选项不合法 / 同类问题已挂满）→ **不建问题**，
        返回 None 让本轮按普通回复继续，同时在日志里留证据（不静默吞掉退出）。
        """
        spec = result.get("ask_user") if isinstance(result, dict) else None
        if not isinstance(spec, dict):
            return None
        question = str(spec.get("question") or "").strip()
        if not question:
            logger.warning("需求会话 ask_user 指令缺少 question，忽略")
            return None
        try:
            q = self.ask_user_question(
                session_id, question=question,
                options=spec.get("options") or (),
                allow_free_text=bool(spec.get("allow_free_text")),
                default_option_id=spec.get("default_option_id"),
                timeout_sec=spec.get("timeout_sec"),
                context_refs=spec.get("context_refs"),
            )
        except Exception as exc:  # noqa: BLE001 — 提问失败不能让整轮对话炸掉
            logger.warning("需求会话 ask_user 建问题失败: %s", exc)
            return None
        message = str(spec.get("message") or "").strip()
        return {**q, "message": message or question}

    async def chat(
        self,
        session_id: str,
        user_message: str,
        on_delta: "Callable[[str], Any] | None" = None,
        on_reasoning: "Callable[[str], Any] | None" = None,
        on_event: "Callable[[dict], Any] | None" = None,
    ) -> dict:
        """处理需求对话。on_delta 非空时 gathering 态通过流式回调增量推送
        reply 文本片段（token 级真流式），由 stream_chat 的 SSE 生成器消费。

        `on_reasoning` 收到模型思考内容的**逐块增量**（provider 支持时）。

        时间线接线（PRD agent-session-timeline §4.1）：用户消息与模型输出都写进
        会话唯一有序流（`session.log`），流式部分走 `Turn` 的懒开占位 + 增量填充。
        写时间线失败不能阻断对话——外面包一层，异常只落 warning。
        """
        log, live = _timeline_turn(session_id)
        if log is not None and live is not None:
            try:
                log.append(
                    type="content", content=truncate_message(user_message),
                    source="requirements", turn=live.turn,
                    meta={"role": "user"},
                )
            except Exception as exc:  # noqa: BLE001 — 时间线写入失败不阻断对话
                logger.warning("时间线写用户消息失败 %s: %s", session_id, exc)
        try:
            return await self._chat_router(
                session_id, user_message,
                on_delta=on_delta, on_reasoning=on_reasoning, on_event=on_event,
                live=live,
            )
        except Exception:
            if live is not None:
                try:
                    live.close(state="failed")
                except Exception:  # noqa: BLE001
                    pass
            raise

    async def _chat_router(
        self,
        session_id: str,
        user_message: str,
        on_delta: "Callable[[str], Any] | None" = None,
        on_reasoning: "Callable[[str], Any] | None" = None,
        on_event: "Callable[[dict], Any] | None" = None,
        live: Any = None,
    ) -> dict:
        """状态路由本体（写库/生成方案/规划书）；时间线由 `chat` 与 `_close_turn` 负责。"""
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

        # 本轮新增消息的起点：`messages[_turn_start:]` 就是本轮模型说的话
        _turn_start = len(messages)
        # 本轮是否产生了新方案/新规划书（决定要不要落 report/plan 条目）
        _brief_event: dict | None = None
        _plan_event: dict | None = None
        _question_event: dict | None = None

        # 流式回调包一层：既喂调用方（SSE），也喂时间线（懒开占位 + 增量填充）。
        # 注意必须保留"None 就是不开流式"的语义——`_handle_gathering` 靠它选路径。
        _log_delta: "Callable[[str], Any] | None" = None
        _log_reasoning: "Callable[[str], Any] | None" = None
        if on_delta is not None and live is not None:
            def _log_delta(text: str) -> None:  # type: ignore[misc]
                live.stream("content", text)
                if on_delta is not None:
                    on_delta(text)

            def _log_reasoning(text: str) -> None:  # type: ignore[misc]
                live.stream("thinking", text)
                if on_reasoning is not None:
                    on_reasoning(text)
        else:
            _log_delta = on_delta
            _log_reasoning = on_reasoning

        # 规划书生成阶段帧也要进时间线：确认简报之后到规划书之间不能是一片空白
        # （用户 2026-10-31：生成规划书的过程中不该一帧都不发）。stage 条目按阶段
        # 名 upsert：running 开一条、ok/failed 收尾，位置就是发生时刻。
        _stage_seqs: dict[str, int] = {}
        _on_plan_event = on_event

        if on_event is not None and live is not None:
            def _plan_event_sink(chunk: dict) -> None:  # type: ignore[misc]
                if on_event is not None:
                    on_event(chunk)
                if not isinstance(chunk, dict) or chunk.get("type") != "stage":
                    return
                data = chunk.get("data")
                if not isinstance(data, dict):
                    return
                stage = str(data.get("stage") or "")
                status = str(data.get("status") or "")
                meta = {
                    "stage": stage, "status": status, "role": "system",
                    "phase": str(data.get("phase") or "plan"),
                    "summary": str(data.get("summary") or ""),
                }
                try:
                    seq = _stage_seqs.get(stage)
                    if seq is None:
                        item = live.log.append(
                            type="stage", content=str(data.get("action") or stage),
                            source="requirements", turn=live.turn, meta=meta,
                            state="streaming",
                        )
                        _stage_seqs[stage] = item.seq
                    else:
                        live.log.patch(
                            seq,
                            state=("failed" if status == "failed"
                                   else "streaming" if status == "running" else "done"),
                            meta=meta,
                        )
                except Exception as exc:  # noqa: BLE001 — 阶段条目写失败不影响生成
                    logger.warning("时间线写阶段条目失败 %s: %s", stage, exc)

            _on_plan_event = _plan_event_sink

        # PRD §15.5：会话在等回答时用户直接打字（没用问题卡）→ 当作自由文本回答，
        # 然后把状态放回 gathering 让 agent 继续。不能让 waiting_user 变成死胡同。
        if status == "waiting_user":
            _pending = self.pending_questions(session_id)
            if _pending:
                try:
                    from clipwright.services import ask_user as _ask_user
                    _ask_user.answer(_pending[0]["question_id"],
                                     text=user_message, source="user_text")
                except Exception as exc:  # noqa: BLE001 — 答不上就继续按普通消息处理
                    logger.warning("需求会话 pending 问题自动应答失败: %s", exc)
            status = "gathering"

        # 状态路由
        if status in ("gathering", "init"):
            result = await self._handle_gathering(messages, brief_data, status, user_inputs, session_id,
                                              on_delta=_log_delta, on_reasoning=_log_reasoning)
            # PRD §15.5：LLM 需要用户拍板时给 `ask_user` 指令 → 建问题、等回答，
            # 本轮不再往下推进（不猜、不替用户选）。
            _asked = await self._ask_from_llm(result, session_id)
            if _asked is not None:
                _question_event = _asked
                messages.append({
                    "role": "assistant",
                    "content": _asked.get("message") or _asked.get("question", ""),
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {"ask_user": {"question_id": _asked.get("question_id")}},
                })
                await asyncio.to_thread(
                    self._persist, session_id, "waiting_user", messages, brief_data,
                    plan_data, user_inputs,
                )
                session = (await asyncio.to_thread(self.get_session, session_id)) or {}
                self._close_turn(live, messages, _turn_start,
                                 brief=_brief_event, plan=_plan_event,
                                 question=_question_event)
                return {**session, "status": "waiting_user",
                        "reply": _asked.get("message") or _asked.get("question", ""),
                        "question": _asked}
            _new_brief = result.get("brief_draft")
            # P2-34: LLM 解析失败会返回 brief_draft={}——空结果不覆盖已有方案
            if isinstance(_new_brief, dict) and _new_brief:
                brief_data = _new_brief
                _brief_event = _new_brief
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
                plan_result = await self._generate_plan(
                    brief_data, user_inputs, session_id, on_event=_on_plan_event,
                )
                if plan_result:
                    plan_data = plan_result
                    _plan_event = plan_result
                    status = "plan_ready"
                else:
                    # A-18 兼容：老会话/翻译失败但有 scenes 时，由 scenes 反向生成
                    # legacy plan（标 legacy=true），仍可确认与执行——不再卡在 brief_ready
                    legacy = self._legacy_plan_from_scenes(plan_data, session_id)
                    if legacy is not None:
                        plan_data = legacy
                        _plan_event = legacy
                        status = "plan_ready"
                        messages.append({
                            "role": "assistant",
                            "content": "规划书已按现有场景生成（legacy 模式），可确认执行或要求重做。",
                            "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                            "metadata": {"legacy_plan": True},
                        })
                    else:
                        # 规划书生成失败 → 明确回退（旧实现卡死 brief_confirmed，
                        # 后续任何消息都落不进任何分支，永远返回过期回复）
                        status = "brief_ready"
                        messages.append({
                            "role": "assistant",
                            "content": ("规划书生成遇到技术问题，已回退到方案确认态。"
                                        "回复「确认」重试，或直接提出修改意见。"),
                            "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                            "metadata": {},
                        })
            else:
                # E2E 2026-10-29 发现：这里原本直接把状态打回 `gathering`，只回一句
                # 「好的，请告诉我需要如何调整？」。后果很严重——
                # ① 用户在 brief_ready 下问一句/提一句「直接给我规划书」，brief 已经
                #    生成却被判成"还没聊够"，会话退回 gathering；
                # ② 那条消息本身没被用于修订方案（白说一遍）；
                # ③ 之后再说「确认」也落不到确认分支（状态已不是 brief_ready），
                #    得先说两次才生效。
                # 现在：**保留 brief_ready**，把这条消息当成"修订意见"重新走一次
                # gathering 合并出新方案；新方案出来就再给确认提示，出不来就明说。
                revised = await self._handle_gathering(
                    messages, brief_data, "gathering", user_inputs, session_id,
                    on_delta=_log_delta, on_reasoning=_log_reasoning,
                )
                new_brief = revised.get("brief_draft")
                updated = isinstance(new_brief, dict) and bool(new_brief)
                if updated:
                    brief_data = new_brief
                    _brief_event = new_brief
                messages.append({
                    "role": "assistant",
                    "content": str(revised.get("reply") or "已记下你的修改意见。"),
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {"revision": True},
                })
                # 无论修订是否成功，状态都停在 brief_ready：方案还在，用户可以继续
                # 提意见，也可以直接回「确认」推进到规划书。
                status = "brief_ready"
                if updated and isinstance(brief_data, dict) and brief_data:
                    messages.append({
                        "role": "assistant",
                        "content": (
                            "\n\n---\n\n### ✅ 方案已按你的意见更新\n\n"
                            f"**标题**: {brief_data.get('title', '待定')}\n"
                            f"**概述**: {brief_data.get('overview', '')}\n"
                            f"**风格**: {brief_data.get('style_direction', '')}\n\n"
                            "回「确认」生成制作规划书，或继续提修改意见。"
                        ),
                        "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                        "metadata": {},
                    })
                else:
                    messages.append({
                        "role": "assistant",
                        "content": "方案没有变动。回「确认」生成制作规划书，或再说清要改哪一处。",
                        "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                        "metadata": {},
                    })

        elif status in ("brief_confirmed", "plan_confirmed"):
            # P1-20: 确认中间态此前没有路由分支——用户确认后再发任何消息都
            # 落不进任何分支，会话看起来卡死。给出明确提示与可行的推进方式。
            if status == "plan_confirmed" and user_message.strip() in ("重新生成", "再来一版", "重新生成视频"):
                status = "plan_ready"
                reply = "好的，已回到规划书确认态。回复「确认」将按规划书启动管线。"
            elif status == "brief_confirmed" and await self._is_confirm(user_message):
                # 方案确认态下再次确认 → 重走规划书生成
                status = "brief_confirmed"
                messages.append({
                    "role": "assistant", "content": "收到，正在重新生成成片规划书，请稍候...",
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {},
                })
                plan_result = await self._generate_plan(
                    brief_data, user_inputs, session_id, on_event=_on_plan_event,
                )
                if plan_result:
                    plan_data = plan_result
                    _plan_event = plan_result
                    status = "plan_ready"
                else:
                    messages.append({
                        "role": "assistant", "content": "规划书生成遇到技术问题，请稍后回复「确认」重试。",
                        "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                        "metadata": {},
                    })
            else:
                hint = (
                    "方案已确认，正在生成规划书；无需重复确认，稍候即可看到规划书。"
                    if status == "brief_confirmed"
                    else "规划书已确认，请等待管线启动；如需调整请回复「重新生成」后再提意见。"
                )
                messages.append({
                    "role": "assistant", "content": hint,
                    "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                    "metadata": {},
                })
        elif status == "pipeline_running":
            # 运行中消息不再静默吞掉
            messages.append({
                "role": "assistant",
                "content": "管线正在运行中，请稍候；完成后可在编辑器查看时间线并渲染成片。",
                "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
                "metadata": {},
            })
        elif status == "pipeline_done":
            # 旧实现对已完成会话静默吞消息。支持「重新生成」一键回到
            # plan_ready（规划书仍在会话中，可直接再次 proceed）
            if user_message.strip() in ("重新生成", "再来一版", "重新生成视频"):
                status = "plan_ready"
                reply = "好的，已回到规划书确认态。回复「确认」将按已确认的规划书再次启动管线。"
            else:
                reply = ("本会话的视频已生成完毕。如需调整，请在编辑器中修改时间线；"
                         "回复「重新生成」可按已确认的规划书再次启动管线。")
            messages.append({
                "role": "assistant", "content": reply,
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
                # /E2: 规划书反馈闭环——不重置回 gathering，而是按反馈修订规划书
                # 并复用已确认的 raw_scenes（仅重新翻译，不重跑 StructureAgent）。
                status, revised = await self._handle_plan_revision(
                    messages, brief_data, plan_data, user_inputs, session_id, user_message,
                )
                if revised:
                    plan_data = revised
                    _plan_event = revised

        # 持久化到 MongoDB（offload 到线程：_persist 内的 find_by_id 走 _io()，
        # 在事件循环线程会返回未 await 的协程导致写入静默失败；线程内无事件循环则同步执行）
        await asyncio.to_thread(
            self._persist, session_id, status, messages, brief_data, plan_data, user_inputs,
        )
        # 时间线收口：thinking → content（本轮全部模型发言）→ report → plan → question
        self._close_turn(live, messages, _turn_start,
                         brief=_brief_event, plan=_plan_event,
                         question=_question_event)
        session = (await asyncio.to_thread(self.get_session, session_id)) or {}
        msgs = session.get("messages", [])
        last_assistant = next(
            (m["content"] for m in reversed(msgs) if m.get("role") == "assistant"),
            "",
        )
        return {**session, "reply": last_assistant}

    @staticmethod
    def _close_turn(
        live: Any, messages: list[dict], turn_start: int, *,
        brief: dict | None = None, plan: dict | None = None,
        question: dict | None = None,
    ) -> None:
        """把一轮的条目按**发生顺序**写进时间线（PRD §3.2）。

        顺序就是阅读顺序：思考 → 正文（本轮所有模型发言）→ 简报 → 规划书 → 提问。
        流式正文用权威全文完成占位条目，避免"丢块拼接"出残缺文本。
        """
        if live is None:
            return
        try:
            replies = [
                str(m.get("content") or "")
                for m in messages[turn_start:]
                if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content")
            ]
            live.finish("thinking")
            if replies:
                live.finish("content", content=replies[0], meta={"role": "assistant"})
                for extra in replies[1:]:
                    live.append("content", extra, meta={"role": "assistant"})
            else:
                live.finish("content")
            if isinstance(brief, dict) and brief:
                live.append(
                    "report", brief,
                    meta={
                        "role": "assistant",
                        "title": str(brief.get("title") or ""),
                        "brief_hash": _brief_hash(brief),
                    },
                )
            if isinstance(plan, dict) and plan:
                scenes = plan.get("raw_scenes") or plan.get("scenes") or []
                live.append(
                    "plan", plan,
                    meta={
                        "role": "assistant",
                        "title": str(plan.get("title") or ""),
                        "brief_hash": _brief_hash(brief),
                        "scene_count": len(scenes) if isinstance(scenes, list) else 0,
                        "duration_sec": (
                            plan.get("total_duration_sec")
                            or plan.get("duration_sec")
                            or plan.get("total_duration")
                            or 0
                        ),
                        "legacy": bool(plan.get("legacy")),
                    },
                )
            if isinstance(question, dict) and question:
                qid = str(question.get("question_id") or "")
                live.append(
                    "question", question,
                    meta={"question_id": qid,
                          "question": str(question.get("question") or "")},
                    ref=f"q:{qid}" if qid else None,
                )
        except Exception as exc:  # noqa: BLE001 — 时间线写失败不影响对话结果
            logger.warning("时间线收口失败: %s", exc)

    async def _revise_raw_scenes(
        self, scenes: list[dict], feedback: str,
        brief_data: dict | None, user_inputs: dict,
    ) -> list[dict] | None:
        """批8：按用户修改意见用 LLM 改写 raw_scenes 场景本体（有界）。

        修复契约断裂：旧实现修订只重译规划书文案，场景本体永不变更。
        约束：单次 LLM 调用；失败/解析失败返回 None（回退旧行为——仅重译）。
        """
        if not feedback.strip():
            return None
        try:
            system_prompt = (
                "你是视频规划修订助手。给你当前规划的场景列表（JSON 数组）与用户修改意见，"
                "输出修改后的完整场景 JSON 数组。规则：\n"
                "1. 仅按修改意见做必要的增/删/改，未被提及的场景尽量保持原样；\n"
                "2. 每个场景必须包含字段：title（标题）、description（画面描述）、"
                "duration_sec（数字秒）、keywords（字符串数组）、voiceover_script（口播脚本）；\n"
                "3. 不要在 description 中写动画标记；\n"
                "4. 只输出 JSON 数组，不要输出任何其他文字。"
            )
            user_prompt = (
                "当前场景列表：\n"
                + json.dumps(scenes, ensure_ascii=False)
                + "\n\n用户修改意见："
                + feedback[:1000]
            )
            resp = await self._llm.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                # 场景本体是有内容的 JSON 数组，不给预算时推理型模型会把默认
                # 4096 全用在 reasoning 上、正文返回空（实测 max_tokens=16 那条
                # 告警就是这里漏参数的直接后果），修订因此永远失败。
                max_tokens=PLAN_SCENE_REVISE_MAX_TOKENS,
            )
            content = (resp.content or "").strip()
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].strip().startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                content = "\n".join(lines).strip()
            start = content.find("[")
            end = content.rfind("]")
            if start < 0 or end <= start:
                return None
            revised = json.loads(content[start:end + 1])
            if not isinstance(revised, list) or not revised:
                return None
            valid = []
            for s in revised:
                if (isinstance(s, dict) and str(s.get("title", "")).strip()
                        and str(s.get("description", "")).strip()
                        and float(s.get("duration_sec", 0) or 0) > 0):
                    valid.append(s)
            return valid or None
        except Exception:
            logger.exception("规划修订：场景改写失败（回退原场景）")
            return None

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

        # 区域级返工 — 收集时间窗内的片段 id（供意图分类与编辑动作限定范围）
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
                # P3：局部修补也走统一阶段执行通道（不再各自 new Agent 实例，
                # 否则"管线里能跑、预览里跑不通"的两套行为会一直漂移）
                from clipwright.services import stage_subagent

                out = await stage_subagent.run_inline(
                    "material",
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
            from clipwright.plugins.generated_source import generated_image_entries
            from clipwright.services import stage_subagent

            out = await stage_subagent.run_inline(
                "animation",
                AnimationInput(
                    context=context, timeline=subset, visual_config=visual_config,
                    # AI 生成图片历史 → 语义索引（无历史时为 []，零变化）
                    image_assets=generated_image_entries(),
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
        duration_changes: dict[str, float] = {}
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
                if field in _STRUCTURAL_DURATION_FIELDS:
                    # **改时长必须收拢**，与结构化补丁（trim/变速/set duration_sec）
                    # 共用同一条规则（`reflow_after_duration_change`）：否则 c0 从 4s
                    # 改到 10s 会与 c1 重叠（自查发现的"同义操作两种行为"缺陷，
                    # 两条入口都要修）。
                    before_dur = float(clip.duration_sec or 0.0)
                    target = float(clamp_fn(value))
                    clip.duration_sec = max(_MIN_CLIP_SEC, target)
                    duration_changes[cid] = before_dur - float(clip.duration_sec)
                else:
                    setattr(clip, field, clamp_fn(value))
                applied += 1
            except (TypeError, ValueError):
                continue
        moved: list[str] = []
        from clipwright.services.intent_apply import (
            recalc_timeline_duration as _recalc,
            reflow_after_duration_change as _reflow,
        )

        if duration_changes:
            try:
                moved = _reflow(timeline, duration_changes)
            except Exception as exc:  # noqa: BLE001 — 重排失败不该让调整整体失败
                logger.warning("时长调整后的重排失败（已应用数值）: %s", exc)

        # 总时长要跟着片段实际占用走，否则时间线模型说 10s、渲染输出 13s
        # （自查发现：结构化补丁入口会重算，本入口此前漏了）。
        try:
            _recalc(timeline)
        except Exception as exc:  # noqa: BLE001
            logger.warning("重算总时长失败: %s", exc)
        notes.append(f"已应用 {applied} 项调整")
        if moved:
            notes.append(f"其中 {len(duration_changes)} 项改动了时长，"
                         f"已顺移 {len(moved)} 个后续片段避免重叠")
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
        # 确认词后带转折/修正尾缀 → 不是确认（"好的，不过再长一点"、
        # "可以，但是要加片头"——旧实现误判为确认并直接推进流程）
        affirm_starts = ["没问题", "就这样", "可以了", "好的", "确认", "同意", "可以",
                         "行", "ok", "yes", "y", "对", "嗯", "确定", "通过", "批准"]
        # P1-21: 单字/模糊确认词必须整句匹配——「对比一下」「行业分析」以
        # 「行」「对」开头但并非确认，旧实现 startswith 误判推进流程
        ambiguous = {"行", "对", "嗯", "y", "ok", "yes"}
        for kw in affirm_starts + strong:
            if low.startswith(kw) or low == kw:
                tail = low[len(kw):].strip()
                if not tail:
                    return True
                if kw in ambiguous:
                    if not tail.strip("。！!~？?，,、 …·-—"):
                        return True  # 仅标点/语气符 → 视作整句确认
                    continue  # 带其余内容 → 交由 LLM 语义判断
                if tail.startswith(("但是", "但", "不过", "然而", "除了", "就是",
                                    "可是", "只是", "另外", "还有", "再", "希望",
                                    "建议", "想", "要", "麻烦", "请", "能不能",
                                    "最好", "其实")):
                    return False  # 确认词后夹带修正意见 → 交由 LLM 语义判断
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
        # P1-21: 模糊短词（行/对/嗯/y/ok/yes）必须整句匹配——「对比一下」
        # 「行业分析」以它们开头但并非确认（与 _is_confirm 主路径同语义）
        start_tokens = [
            "没问题", "就这样", "可以了", "好的", "确认", "同意", "可以",
            "行", "ok", "yes", "y", "对", "嗯", "确定", "通过", "批准",
        ]
        ambiguous = {"行", "对", "嗯", "y", "ok", "yes"}
        for kw in start_tokens:
            if msg.startswith(kw) or msg == kw:
                if kw in ambiguous:
                    tail = msg[len(kw):].strip("。！!~？?，,、 …·-—")
                    if tail:
                        return False
                return True
        return False

    # ── LLM 需求收集 ──────────────────────────

    @staticmethod
    def _reply_value_start(buf: str) -> int:
        """定位 JSON 中 reply 字符串值起点（左引号之后）；尚未就绪返回 -1。"""
        key = '"reply"'
        idx = buf.find(key)
        if idx < 0:
            return -1
        colon = buf.find(":", idx + len(key))
        if colon < 0:
            return -1
        q = colon + 1
        while q < len(buf) and buf[q] in " \t\r\n":
            q += 1
        if q >= len(buf):
            return -1
        return q + 1 if buf[q] == '"' else -1

    @staticmethod
    def _safe_json_prefix_len(s: str) -> int:
        """可安全 JSON 解码的前缀长度——不切断未完成的转义序列。"""
        i, n = 0, len(s)
        while i < n:
            if s[i] == "\\":
                if i + 1 >= n:
                    return i
                if s[i + 1] == "u":
                    if i + 6 > n:
                        return i
                    i += 6
                else:
                    i += 2
            else:
                i += 1
        return n

    async def _stream_gathering_llm(self, llm_kwargs: dict, on_delta: Callable,
                                    on_reasoning: Callable | None = None) -> dict:
        """轮68/69：流式 gathering LLM 调用——stream_generate 增量 yield，
        on_delta 回调推送 reply 字段的已解码文本增量。

        reply 为 JSON 首键（CREATIVE_BRIEF_SYSTEM 已保序）；增量提取维护
        「值起点 + 已发送字符数」，每个 chunk 解码新到的安全前缀，只发送新增
        部分（轮69 修复：旧实现 reply_buf 只赋值一次，仅首块内容被推送）。
        """
        messages = [{"role": "user", "content": llm_kwargs.get("user_prompt", "")}]
        system = llm_kwargs.get("system_prompt", "")
        buf = ""
        reply_start = -1
        sent = 0

        async for chunk in self._llm.stream_generate(messages, system_prompt=system):
            # 思考内容逐块转发（PRD §19.4：thinking 真流式；provider 不给就什么都不发）
            _reason = getattr(chunk, "reasoning_content", "") or ""
            if _reason and on_reasoning is not None:
                try:
                    on_reasoning(_reason)
                except Exception:  # noqa: BLE001 — 回调失败不影响生成
                    pass
            delta = getattr(chunk, "content", "") or ""
            if not delta:
                continue
            buf += delta
            if reply_start < 0:
                reply_start = self._reply_value_start(buf)
                if reply_start < 0:
                    continue
            raw = buf[reply_start:]
            # 找未转义的闭合引号 = reply 值结束
            closed = False
            i = 0
            while i < len(raw):
                if raw[i] == "\\":
                    i += 2
                    continue
                if raw[i] == '"':
                    raw = raw[:i]
                    closed = True
                    break
                i += 1
            safe_len = len(raw) if closed else self._safe_json_prefix_len(raw)
            if safe_len <= 0:
                continue
            try:
                decoded = json.loads('"' + raw[:safe_len] + '"')
            except json.JSONDecodeError:
                continue
            if len(decoded) > sent:
                on_delta(decoded[sent:])
                sent = len(decoded)

        # 流结束 → 解析完整 JSON；reply 缺失/为空时用已提取文本兜底
        fallback_reply = self._reply_text_from_buf(buf, reply_start) or "生成完成。"
        parsed = self._parse_llm_json(buf)
        if parsed:
            if not parsed.get("reply"):
                parsed["reply"] = fallback_reply
            return parsed
        return {
            "reply": fallback_reply,
            "brief_draft": {},
            "is_ready": False,
        }

    @staticmethod
    def _reply_text_from_buf(buf: str, reply_start: int) -> str:
        """从完整响应缓冲中取出 reply 字符串值（已解码），失败时返回原文截断。"""
        if reply_start < 0:
            return ""
        raw = buf[reply_start:]
        i = 0
        while i < len(raw):
            if raw[i] == "\\":
                i += 2
                continue
            if raw[i] == '"':
                raw = raw[:i]
                break
            i += 1
        try:
            return json.loads('"' + raw + '"')[:2000]
        except json.JSONDecodeError:
            return raw[:2000]

    async def _handle_gathering(
        self, messages: list[dict], brief_data: dict | None, status: str,
        user_inputs: dict | None = None, session_id: str = "",
        on_delta: "Callable[[str], Any] | None" = None,
        on_reasoning: "Callable[[str], Any] | None" = None,
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

        # 注入 AI 生成能力概览（无可用生成工具 → 零变化），
        # 让需求对话阶段即可主动提出"此处适合 AI 生成素材"的方案建议
        gen_overview = _ai_generation_overview()
        if gen_overview:
            context += f"\n\n## AI 生成能力\n{gen_overview}"

        # P1-22: 多轮澄清记忆——旧实现只把最后一条消息喂给 LLM，用户在
        # 非确认分支提出的修改会被下一轮 LLM 遗忘。拼入最近 N 轮对话。
        recent = [
            m for m in messages[-12:]
            if isinstance(m, dict) and m.get("content")
        ]
        history_text = chr(10).join(
            f"{'用户' if m.get('role') == 'user' else '助手'}: {str(m['content'])[:500]}"
            for m in recent[:-1]
        ) if len(recent) > 1 else ""
        user_prompt = messages[-1]["content"] if messages else "请开始对话。"
        if history_text:
            header = "## 近期对话记录（供理解上下文，最新一条在最后）"
            user_prompt = (
                header + chr(10) + history_text + chr(10) * 2
                + "## 用户最新输入" + chr(10) + user_prompt
            )
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
        # 轮68：真流式路径——on_delta 非空时用 stream_generate 替代
        # structured_output，增量推送 reply 文本（reply 为 JSON 首键，提示词
        # 已保序）。完整 JSON 在流结束后仍通过同一解析逻辑处理。
        if on_delta is not None:
            try:
                import asyncio as _aio
                result = await asyncio.wait_for(
                    self._stream_gathering_llm(llm_kwargs, on_delta, on_reasoning),
                    timeout=BRIEF_GENERATE_TIMEOUT,
                )
                await self._record_llm_usage("requirements.gathering", session_id)
                return result
            except _aio.TimeoutError:
                logger.warning("流式 brief 生成超时")
                return {
                    "reply": "生成创意简报时超时，请再试一次或补充描述。",
                    "brief_draft": {},
                    "is_ready": False,
                }
            except Exception as e:
                # 不再"只写 warning 就静默回退"（unified-stream PRD §1.1）：
                # 回退事实要传到 SSE（reasoning_status.stream_fallback）与 result.diagnostics，
                # 否则前端只能显示一个没有内容的"正在思考"，用户以为按钮坏了。
                logger.warning("流式 brief 失败，回退缓冲路径: %s", e)
                # 记一条 per-session 标记，由 stream_chat 转成
                # reasoning_status(stream_fallback) + result.diagnostics（不静默丢事实）
                _STREAM_FALLBACK[str(session_id)] = f"{type(e).__name__}: {e}"[:300]
                # 流式失败 → 回退缓冲路径（下面的 W1 with_tools / llm_call_with_retry）

        # 联网搜索工具门控接入（Bocha/百度）——配置开启时 LLM 可自主搜索；
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

    def _legacy_plan_from_scenes(self, plan_data: dict | None, session_id: str) -> dict | None:
        """A-18：老会话/翻译失败但有 scenes 时，反向生成 legacy plan（标 legacy）。

        - 没有 ``raw_scenes`` → 返回 ``None``（真没料，不硬造）；
        - 生成后仍走 ``PlanDocument`` 校验（段落连续、时长不漂），校验不过则放弃；
        - 输出的 ``markdown_content`` 由确定性渲染器生成，保证可复核。
        """
        scenes = []
        if isinstance(plan_data, dict):
            scenes = plan_data.get("raw_scenes") or []
        if not scenes:
            return None
        try:
            from clipwright.services.plan_renderer import render_plan_markdown
            from clipwright.services.plan_validator import plan_from_scenes, validate_plan

            plan = plan_from_scenes(scenes)
            result = validate_plan(plan)
            if not result.ok:
                logger.info(
                    "RequirementsService: legacy plan 校验未过（%s），放弃反向生成",
                    [i.code for i in result.errors],
                )
                return None
            payload = dict(plan_data or {})
            payload["plan"] = plan.model_dump()
            payload["raw_scenes"] = scenes
            payload["scene_count"] = len(plan.sections)
            payload["total_duration_sec"] = plan.target_duration_sec
            payload["legacy"] = True
            payload["markdown_content"] = render_plan_markdown(plan)
            payload["summary"] = "按现有场景生成的 legacy 规划（非 plan-first 产物）"
            logger.info("RequirementsService: 会话 %s 使用 legacy plan（%d 段）", session_id[:12], len(plan.sections))
            return payload
        except Exception as exc:  # noqa: BLE001 - 兜底失败不应影响主流程
            logger.warning("RequirementsService: legacy plan 生成失败: %s", exc)
            return None

    async def _generate_plan(
        self, brief_data: dict | None, user_inputs: dict, session_id: str,
        feedback: str = "", existing_raw_scenes: list | None = None,
        on_event: "Callable[[dict], Any] | None" = None,
    ) -> dict | None:
        """调用 StructureAgent（注入 Persona）并翻译为规划书。

        B6/E2：规划书修订路径——当已有 raw_scenes（简报未变、本会话已生成过场景）时
        跳过 StructureAgent 重新生成，仅带反馈重新 _translate_plan，避免重复完整 LLM 生成。
        """
        try:
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

            # /E2: 复用已确认的 raw_scenes 修订路径——不重跑 StructureAgent，
            # 仅带反馈重新翻译场景（简报未变、已有场景时）。
            if isinstance(existing_raw_scenes, list) and existing_raw_scenes:
                # 反馈先经 LLM 改写场景本体（有界改写；失败回退原场景），
                # 修复「修改意见只改规划书文案、场景本体永不变更」的契约断裂
                revised_scenes = await self._revise_raw_scenes(
                    existing_raw_scenes, feedback, brief_data, user_inputs,
                )
                if revised_scenes:
                    logger.info(
                        "规划书修订: 场景本体已按反馈改写（%d → %d 个场景）",
                        len(existing_raw_scenes), len(revised_scenes),
                    )
                    existing_raw_scenes = revised_scenes
                else:
                    logger.info(
                        "规划书修订: 场景改写未产出（回退仅重译文案），feedback=%s",
                        feedback[:50],
                    )
                web_context = await _build_web_context(
                    f"{topic} {user_inputs.get('script_text', '')}"[:300]
                )
                return await self._translate_plan(
                    existing_raw_scenes, brief_data,
                    user_inputs.get("script_text", ""), feedback=feedback,
                    web_context=web_context,
                )

            # 说明：早期这里 `agent = StructureAgent()` 后直接 execute；
            # 现在统一走 `stage_subagent.run_inline("structure", …)`（单一实例化通道）。
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

            # 注入联网搜索结果作为事实参考（未配置/失败/空结果 → 零变化）
            web_context = await _build_web_context(f"{topic} {script}"[:300])
            if web_context:
                if rag_context:
                    rag_context += f"\n\n## 联网搜索参考\n{web_context}"
                else:
                    rag_context = f"## 联网搜索参考\n{web_context}"

            # 注入素材库概览（空素材库 → 无额外段落，零变化）
            overview = _material_library_overview()
            if overview:
                if rag_context:
                    rag_context += f"\n\n## 素材库概览\n{overview}"
                else:
                    rag_context = f"## 素材库概览\n{overview}"

            # 注入 AI 生成能力概览（无可用生成工具 → 零变化）
            gen_overview = _ai_generation_overview()
            if gen_overview:
                if rag_context:
                    rag_context += f"\n\n## AI 生成能力\n{gen_overview}"
                else:
                    rag_context = f"## AI 生成能力\n{gen_overview}"

            from clipwright.services import stage_subagent

            if on_event:
                # 用户 2026-10-31：不要让"确认简报"之后整段静默——
                # 派发结构阶段本身就是一个可见的阶段事实。
                on_event({"type": "stage", "data": {
                    "stage": "structure", "status": "running",
                    "action": "派发结构阶段：生成分镜", "phase": "plan",
                }})
            result = await stage_subagent.run_inline(
                "structure",
                StructureInput(
                    context=context,
                    persona_config=persona_config,
                    persona_prompt=persona_prompt,
                    rag_context=rag_context,
                    creative_brief=brief_data,
                ),
                context,
                # 必须比 StructureAgent.timeout_sec 更宽松：外层先掐断会把
                # 内层"LLM 超时 → 兜底分镜"的降级路径整条跳过，
                # 结果只有一句空消息的「规划书生成失败」。
                timeout_sec=PLAN_STRUCTURE_TIMEOUT,
            )
            # StructureAgent 的 LLM 用量（requirements 会话级，非 pipeline）
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
            if on_event:
                on_event({"type": "stage", "data": {
                    "stage": "structure", "status": "ok" if scenes else "empty",
                    "action": "结构阶段完成", "phase": "plan",
                    "summary": f"{len(scenes)} 个场景" if scenes else "没有产出场景",
                }})
            if not scenes:
                logger.warning("StructureAgent 返回空场景")
                return None

            if on_event:
                on_event({"type": "stage", "data": {
                    "stage": "translate", "status": "running",
                    "action": "翻译规划书（中文可读版）", "phase": "plan",
                }})
            plan = await self._translate_plan(
                scenes, brief_data, script, feedback=feedback, web_context=web_context,
            )
            if on_event:
                on_event({"type": "stage", "data": {
                    "stage": "translate", "status": "ok" if plan else "failed",
                    "action": "规划书翻译完成" if plan else "规划书翻译失败",
                    "phase": "plan",
                }})
            return plan

        except Exception as e:
            # TimeoutError 的 str() 是空串——只打 %s 会留下一句「规划书生成失败: 」，
            # 看不出是超时还是别的。类型名与 timeout 都要进日志。
            logger.exception(
                "规划书生成失败（%s）: %s", type(e).__name__, str(e) or "（无消息）",
            )
            return None

    async def _translate_plan(
        self, scenes: list[dict], brief_data: dict | None, script_text: str = "",
        feedback: str = "", web_context: str = "",
    ) -> dict:
        """翻译场景为规划书。

        E2E 2026-10-28：这份 prompt 里塞了 8000 字原文 + 简报 + 联网参考，
        推理型模型（deepseek-v4-pro）在这么大的输入上频繁"只产出 reasoning、
        正文为空"（HTTP 200 + content=""），三次重试全空 → 规划书退化成兜底表。
        现在的策略是：**先给足预算的完整 prompt，失败再降规模重试一次**
        （原文砍到 2500 字、去掉联网参考），最后才回落到确定性 Markdown。
        """
        scenes_json = json.dumps(scenes, ensure_ascii=False, indent=2)
        # 送给 LLM 的版本要**瘦**：口播原话已经在场景里且不该被改写，
        # 40+ 段原话塞进 prompt 只会把 reasoning 预算吃光、正文返回空。
        slim_scenes = json.dumps(
            [
                {
                    "index": i,
                    "title": s.get("title", ""),
                    "duration_sec": s.get("duration_sec", 0),
                    "keywords": s.get("keywords", []),
                    "description": str(s.get("description", ""))[:80],
                }
                for i, s in enumerate(scenes)
            ],
            ensure_ascii=False,
        )
        brief_json = json.dumps(brief_data, ensure_ascii=False) if brief_data else "{}"

        def _system_prompt(*, compact: bool) -> str:
            text = PLAN_TRANSLATE_SYSTEM
            if feedback:
                text += f"\n\n## 用户修改意见（规划书必须体现这些修订）\n{feedback[:2000]}"
            if script_text:
                # **必须灌注原文**：规划书要贴着作者原意写，砍掉原文会让翻译
                # 只剩场景标题级别的信息，质量显著下降。compact 只用来丢联网参考
                # 之类的附加块，原文一律给足。
                text += f"\n\n## 原始文稿（规划书必须忠实反映此内容）\n{script_text}"
            text += f"\n\n参考方案:\n{brief_json}"
            if web_context and not compact:
                text += f"\n\n## 联网搜索参考\n{web_context[:800]}"
            return text

        async def _call(*, compact: bool, budget: int, timeout: float) -> dict:
            return await asyncio.wait_for(
                llm_call_with_retry(self._llm, "plan_translate", pipeline_id="", **{
                    "system_prompt": _system_prompt(compact=compact),
                    "user_prompt": f"结构 Agent 输出（场景清单）:\n{slim_scenes}",
                    "max_tokens": budget,
                }),
                timeout=timeout,
            )

        result: dict = {}
        try:
            result = await _call(
                compact=False, budget=PLAN_TRANSLATE_MAX_TOKENS,
                timeout=PLAN_TRANSLATE_TIMEOUT,
            )
            await self._record_llm_usage("requirements.plan_translate")
        except asyncio.TimeoutError:
            logger.warning("规划书翻译超时（>%ds）", PLAN_TRANSLATE_TIMEOUT)

        def _has_real_body(plan: dict | None) -> bool:
            if not isinstance(plan, dict) or plan.get("degraded"):
                return False
            return bool(str(plan.get("markdown_content") or "").strip())

        if not _has_real_body(result):
            # 降规模重试：输入变小，reasoning 才有余量给正文
            logger.warning("规划书翻译未产出正文，改用精简 prompt 重试一次")
            try:
                retry = await _call(
                    compact=True, budget=PLAN_TRANSLATE_MAX_TOKENS,
                    timeout=PLAN_TRANSLATE_RETRY_TIMEOUT,
                )
                if _has_real_body(retry):
                    result = retry
                    await self._record_llm_usage("requirements.plan_translate")
                else:
                    logger.warning("精简 prompt 仍未产出正文，使用基础规划书")
            except asyncio.TimeoutError:
                logger.warning("精简 prompt 重试超时（>%ds）", PLAN_TRANSLATE_RETRY_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 — 重试失败不影响兜底
                logger.warning("精简 prompt 重试异常: %s", exc)

        if not _has_real_body(result):
            # 两次都没正文 → 用确定性表格兜底（比一句"生成时遇到问题"可读得多），
            # summary 保留降级说明，不谎称成功。
            result = {
                **result,
                "summary": result.get("summary")
                or "规划书已生成（LLM 未返回正文，使用基础版本）。",
                "markdown_content": self._default_markdown(scenes),
            }
        elif not str(result.get("markdown_content") or "").strip():
            result["markdown_content"] = self._default_markdown(scenes)
        # 无论 LLM 写了什么，**完整镜头表都是权威内容**（口播原话不能被改写），
        # 统一追加在文末：这样规划书里既有 LLM 的制作说明，也有逐镜头的真值表。
        table = self._default_markdown(scenes)
        body = str(result.get("markdown_content") or "")
        if table.strip() and table.strip() not in body:
            result["markdown_content"] = f"{body.rstrip()}\n\n---\n\n{table}"
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

    @staticmethod
    def _ensure_message_ids(messages: list[dict]) -> list[dict]:
        """给没有 id 的消息补稳定 id（unified-stream PRD §M4）。

        分页游标（`before_id`）与前端去重都依赖 id：老会话里消息只有
        role/content/timestamp，前端每次刷新都得重新 uid 一遍，翻页就会重复。
        这里补齐并原样返回（append-only，只补不改）。
        """
        out: list[dict] = []
        for i, m in enumerate(messages or []):
            if not isinstance(m, dict):
                continue
            if not m.get("id"):
                m = {**m, "id": f"msg_{i:05d}"}
            out.append(m)
        return out

    def _persist(
        self, session_id: str, status: str, messages: list[dict],
        brief: dict | None, plan: dict | None, user_inputs: dict,
    ) -> None:
        """持久化会话状态到 MongoDB。"""
        messages = self._ensure_message_ids(messages)
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
                # 轮 82：文件读取 offload（上传文件可达数 MB，同步读冻住事件循环）
                content = (await asyncio.to_thread(
                    lambda: open(file_path, "r", encoding="utf-8", errors="replace").read()
                ))[:5000]
            elif ext in ("pdf",):
                try:
                    import subprocess
                    result = await asyncio.to_thread(
                        subprocess.run,
                        ["pdftotext", file_path, "-"],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        content = result.stdout[:5000]
                except Exception:
                    content = "[PDF 文件，文本提取失败]"
            elif ext in ("docx",):
                try:
                    import re as _re
                    import zipfile
                    with zipfile.ZipFile(file_path) as z:
                        xml = ""
                        for name in z.namelist():
                            if name == "word/document.xml":
                                xml = z.read(name).decode("utf-8", errors="replace")
                                break
                    if xml:
                        # P2-32: 按段落抽取 <w:t> 文本——旧实现把整段 XML 原文
                        # 注入提示词（token 浪费 + 干扰 LLM）
                        lines = []
                        for para in _re.split(r"</w:p>", xml):
                            runs = _re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)
                            line = "".join(runs).strip()
                            if line:
                                lines.append(line)
                        content = (chr(10).join(lines)[:5000]) if lines else "[DOCX 解析失败：未找到文本]"
                    else:
                        content = "[DOCX 解析失败]"
                except Exception:
                    content = "[DOCX 文件]"
            elif ext in ("png", "jpg", "jpeg", "webp", "gif"):
                # 附件图片理解 — 视觉模型描述图片并提取标签，替代占位符
                content = await self._describe_image(file_path, file_name)
            else:
                content = f"[上传文件: {file_name}]"
        except Exception as e:
            content = f"[文件读取失败: {e}]"

        msg = (
            f"用户上传了参考文件 **{file_name}**，内容摘要如下：\n\n```\n{content[:1000]}\n```"
            if content else f"用户上传了文件: {file_name}"
        )

        # process_upload 为 async 上下文 → 线程内执行同步 Mongo 操作
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
        """SSE 流式推送对话结果。

        gathering/init 态走 `stream_generate` 真流式——reply 与思考都以增量帧推送；
        其余状态保持缓冲推送（回复为模板文本，无流式收益）。

        **思考能力必须自报**（unified-stream PRD §5.1）：首帧发 `reasoning_status`，
        明确告知 supported / streaming / available，前端据此三态显示，
        不再用"有没有收到帧"反推能力——否则 provider 不回吐思考时 UI 只能假装思考。
        """
        # 1. 先返回用户消息确认 + typing 指示
        yield {"type": "status", "data": "typing"}

        # 时间线：进程重启后轮询会丢，这里补上（幂等）；同时把已发生的任务事件补投影
        try:
            from clipwright.services import session_projection as _projection

            # 存量会话先回填（老会话磁盘上零条目 → 界面空白，用户 2026-10-31 实测）
            ensure_timeline_backfilled(session_id,
                                       await asyncio.to_thread(self.get_session, session_id))
            _projection.ensure_poller(session_id)
            await asyncio.to_thread(_projection.project, session_id)
        except Exception as exc:  # noqa: BLE001 — 投影失败不影响对话
            logger.warning("会话时间线投影失败 %s: %s", session_id, exc)

        session = await asyncio.to_thread(self.get_session, session_id) or {}
        status = session.get("status", "init")

        cap = self._reasoning_capability()

        # 2. gathering/init 态 → 真流式（on_delta 回调 → 队列 → 逐块 yield）
        if status in ("gathering", "init"):
            yield {"type": "reasoning_status", "data": {
                **cap,
                # 首帧时"是否真的来了内容"还未知：给 None，前端据此显示 pending 而不是 available
                "available": False if not cap.get("supported") else None,
                "phase": status,
            }}

            queue: asyncio.Queue = asyncio.Queue()
            _DONE = object()
            thought: list[str] = []
            _turn_ts = int(time.time() * 1000)

            def _on_delta(text: str) -> None:
                queue.put_nowait({"type": "delta", "data": text})

            def _on_reasoning(text: str) -> None:
                # 思考增量（前端展开后追加）；同时攒起来，本轮结束后落一条事件
                thought.append(text)
                queue.put_nowait({"type": "reasoning", "data": text})
            async def _run() -> None:
                try:
                    result = await self.chat(session_id, user_message, on_delta=_on_delta,
                                             on_reasoning=_on_reasoning)
                    # PRD §15.5：agent 提问 → 单独的 question 帧（前端据此立刻出问题卡），
                    # 再补 result 帧（含同一 question，刷新后从事件流也能重建）
                    _q = result.get("question") if isinstance(result, dict) else None
                    if isinstance(_q, dict) and _q.get("question_id"):
                        queue.put_nowait({"type": "question", "data": _q})

                    # 思考落盘（unified-stream PRD §5.1.4）：按轮**一条**事件，
                    # 刷新/重连可从 `GET /{sid}/events?since_seq=` 回放
                    if thought:
                        _emit_session_thinking(session_id, "".join(thought), turn_ts=_turn_ts)
                    else:
                        # 支持但一块都没来 → 如实说"本轮没有思考内容"，不当成可用
                        queue.put_nowait({"type": "reasoning_status", "data": {
                            **cap, "available": False, "reason": "no_reasoning_this_turn",
                            "phase": status,
                        }})

                    # 流式被静默回退过 → 明说"思考不可用 + 为什么"，并在 result 里留诊断
                    _fb = _STREAM_FALLBACK.pop(str(session_id), "")
                    if _fb and isinstance(result, dict):
                        queue.put_nowait({"type": "reasoning_status", "data": {
                            **cap, "available": False, "reason": "stream_fallback",
                            "detail": _fb, "phase": status,
                        }})
                        result.setdefault("diagnostics", {})["stream_fallback"] = _fb
                    queue.put_nowait({"type": "result", "data": result})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — 转成 SSE error 块，避免流中断无提示
                    logger.exception("流式对话失败: %s", exc)
                    # 异常中断：把开着的流式条目收成 failed，别让界面永远"正在思考"
                    try:
                        _timeline(session_id).close_open("failed")
                    except Exception:  # noqa: BLE001
                        pass
                    queue.put_nowait(
                        {"type": "error", "data": "对话处理失败，请稍后重试"}
                    )
                finally:
                    queue.put_nowait(_DONE)

            task = asyncio.create_task(_run())
            try:
                while True:
                    item = await queue.get()
                    if item is _DONE:
                        break
                    yield item
            finally:
                if not task.done():
                    task.cancel()
            return

        # 3. 其他状态 → 缓冲路径：没有思考流，但**必须周期性发心跳**。
        # 规划书生成常 30–90s，旧实现整段不发帧，代理/浏览器会先把连接判死
        # （实测报「SSE 连接中断，3s 后重连」→ 前端看起来"点了没后续"）。
        yield {"type": "reasoning_status", "data": {
            **cap, "available": False, "reason": "phase_not_streaming", "phase": status,
        }}

        queue_b: asyncio.Queue = asyncio.Queue()
        _DONE_B = object()

        async def _run_buffered() -> None:
            def _on_event(chunk: dict) -> None:
                queue_b.put_nowait(chunk)

            try:
                res = await self.chat(session_id, user_message, on_event=_on_event)
                queue_b.put_nowait({"type": "result", "data": res})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("缓冲对话失败: %s", exc)
                try:
                    _timeline(session_id).close_open("failed")
                except Exception:  # noqa: BLE001
                    pass
                queue_b.put_nowait({"type": "error", "data": "对话处理失败，请稍后重试"})
            finally:
                queue_b.put_nowait(_DONE_B)

        task_b = asyncio.create_task(_run_buffered())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue_b.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat", "data": int(time.time())}
                    continue
                if item is _DONE_B:
                    break
                yield item
        finally:
            if not task_b.done():
                task_b.cancel()

    def _reasoning_capability(self) -> dict[str, Any]:
        """当前 provider/model 是否会回吐思考内容（不猜、不假）。"""
        probe = getattr(self._llm, "reasoning_capability", None)
        if callable(probe):
            try:
                return dict(probe())
            except Exception as exc:  # noqa: BLE001
                logger.warning("reasoning 能力探测失败: %s", exc)
        return {"supported": False, "streaming": False, "reason": "capability_unknown"}
