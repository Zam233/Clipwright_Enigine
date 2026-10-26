"""Agent 基类。

每个 Agent 是一个 LangGraph 节点，包含策略注册表。
Agent 本身不写死逻辑，它只是"调度器"——根据 Persona 和视频类型选择策略。
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from clipwright.schema.agent import AgentContext, AgentDecision

I = TypeVar("I", bound=BaseModel)
O = TypeVar("O", bound=BaseModel)


def uid(prefix: str = "ag") -> str:
    """生成短唯一 ID，供 Agent 共用。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def unified_llm_call(
    name: str,
    coro_factory,
    retries: int = 1,
    timeout: float = 300,
    retry_delay_sec: float = 1.5,
):
    """生产加固 1.4：全仓统一的 LLM/工具调用收口 — 超时 + 瞬时失败重试（线性退避）。

    重试仅针对「请求确已失败」的瞬时错误（rate limit/429/connection/reset），
    永久错误直接抛出，由调用方决定降级。
    F7: 超时不再重试——底层请求经 asyncio.to_thread 执行，wait_for 超时无法
    真正取消线程内的 provider 请求；此时重试会造成同一请求双份并发（双份
    token 计费 + 线程泄漏）。超时直接抛出，由调用方走兜底降级。
    """
    from clipwright.config import logger

    last_exc: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            return await asyncio.wait_for(coro_factory(), timeout=timeout)
        except asyncio.TimeoutError:
            raise
        except Exception as e:  # noqa: BLE001 — 按错误特征判定是否瞬时
            low = str(e).lower()
            transient = any(k in low for k in (
                "timeout", "rate limit", "429", "connection", "reset", "temporarily",
            ))
            if not transient:
                raise
            last_exc = e
        if attempt < retries:
            logger.warning(
                "%s: 调用失败（%s），%.1fs 后第 %d 次重试",
                name, last_exc, retry_delay_sec * (attempt + 1), attempt + 1,
            )
            await asyncio.sleep(retry_delay_sec * (attempt + 1))
    raise last_exc  # type: ignore[misc]


class BaseAgent(ABC, Generic[I, O]):
    """Agent 基类。"""

    agent_name: str = ""
    timeout_sec: int = 300  # LLM 调用默认超时（秒）——大 prompt + 多工具调用时 120s 不足

    def __init__(self) -> None:
        self._strategy_registry: dict[str, Any] = {}

    @abstractmethod
    async def execute(self, input_data: I, context: AgentContext) -> O:
        """执行 Agent 的核心逻辑。"""
        ...

    def register_strategy(self, name: str, strategy: Any) -> None:
        """注册策略到策略注册表。"""
        self._strategy_registry[name] = strategy

    def get_strategy(self, name: str) -> Any:
        """从注册表获取策略。"""
        return self._strategy_registry.get(name)

    def build_error_output(self, error_msg: str, output_cls: type[O],
                           *, category: str = "internal_defect",
                           retryable: str = "retry_same") -> O:
        """构造执行失败输出（F9：必须携带结构化 FailureReport）。

        这是「执行失败」而非「判定失败」——后者（如质检判片子不合格）不应设置
        error，见 ``schema.agent.AgentOutputBase`` 的约定说明。
        """
        from clipwright.schema.agent import FailureReport

        return output_cls(
            decision=AgentDecision.FAIL,
            error=error_msg,
            failure=FailureReport(
                category=category,
                blocking_point=str(error_msg)[:200],
                retryable=retryable,
            ),
        )

    # ── 动态能力面：工具 / 技能实时解析（不再写死工具名）──────────────
    #
    # 背景：旧实现把 ``ToolRegistry.execute("frame_validator", ...)`` 写进 Agent，
    # 于是「谁来实现这个能力」被固化在源码里——插件新装/卸载、依赖缺失、
    # 实现换人，Agent 全都感知不到。现在 Agent 只表达**能力**，由
    # ``tool.capabilities`` 在调用时刻从注册表解析（含插件声明的 capability）。

    #: 能力名 → 优先工具名（Agent 可以在子类里覆盖，或由 Persona 配置注入）
    capability_prefer: dict[str, tuple[str, ...]] = {}
    #: 允许进入本 Agent 的 LLM tool-use 的工具白名单（空 = 不开启工具循环）。
    #: 白名单是**名字集合**，每次调用时重新解析，因此插件新增/卸载即时生效。
    tool_whitelist: tuple[str, ...] = ()

    def resolve_tool(self, capability: str):
        """按能力名解析到当前可用的工具实例（None = 该能力当前无人实现）。"""
        from clipwright.tool.capabilities import resolve

        return resolve(capability, prefer=self.capability_prefer.get(capability, ()))

    def list_agent_tools(self) -> list[Any]:
        """本 Agent 当前可见（agent_callable + 依赖/配置就绪）的全部工具，实时。"""
        from clipwright.tool.registry import ToolRegistry

        return ToolRegistry.list_agent_callable()

    def list_skills(self) -> list[Any]:
        """当前可用技能列表，实时（插件卸载后立刻消失）。"""
        from clipwright.skill.registry import SkillRegistry

        return [s for s in SkillRegistry.list() if s.available]

    def resolve_skill(self, capability: str):
        """按能力名（或技能名）解析到当前可用的技能实例。"""
        from clipwright.tool.capabilities import skill_resolve

        return skill_resolve(capability, prefer=self.capability_prefer.get(capability, ()))

    def capability_report(self) -> dict[str, Any]:
        """诊断快照：本 Agent 声明关心什么、当前落到了哪个实现。

        写进 pipeline trace 后，"为什么这次没用上 X" 一眼可查。
        """
        from clipwright.tool.capabilities import explain

        caps = sorted(set(self.capability_prefer) | set(self.tool_whitelist))
        return {
            "agent": self.agent_name,
            "tools": [explain(c) for c in caps],
            "skills": [s.name for s in self.list_skills()],
        }

    async def call_tool(
        self,
        capability: str,
        *,
        prefer: list[str] | tuple[str, ...] | None = None,
        **kwargs: Any,
    ):
        """按能力名调用工具；能力缺失时**返回结构化 NOT_FOUND**，不抛异常。

        调用方仍按 ``result.status == "success"`` 判定（与旧写法一致），
        因此把硬编码调用换成 ``await self.call_tool("video_trim", ...)``
        不会改变控制流，只是多了一条"能力不可用"的降级记录。
        """
        from clipwright.schema.tool import ToolExecResult, ToolStatus
        from clipwright.tool.capabilities import resolve
        from clipwright.tool.registry import ToolRegistry

        names = tuple(prefer) if prefer is not None else self.capability_prefer.get(capability, ())
        tool = resolve(capability, prefer=names)
        if tool is None:
            self._note_missing_capability(capability)
            # 兼容路径：能力有内置别名时仍按名字发起调用——注册表没这个工具会返回
            # NOT_FOUND；测试/延迟注册环境（execute 被替换）则照旧生效。
            # 这样"改成能力解析"不会改变既有的失败语义。
            from clipwright.tool.capabilities import fallback_name

            alt = fallback_name(capability)
            if alt:
                return await ToolRegistry.execute(alt, **kwargs)
            return ToolExecResult(
                status=ToolStatus.NOT_FOUND,
                tool_name=capability,
                error=f"能力 '{capability}' 当前无可用实现（未注册 / 依赖缺失 / 未配置）",
            )
        return await ToolRegistry.execute(tool.name, **kwargs)

    async def call_skill(self, capability: str, **kwargs: Any):
        """按能力名调用技能；缺失时返回结构化 NOT_FOUND。"""
        from clipwright.schema.skill import SkillExecResult, SkillStatus
        from clipwright.skill.registry import SkillRegistry

        skill = self.resolve_skill(capability)
        if skill is None:
            self._note_missing_capability(f"skill:{capability}")
            return SkillExecResult(
                status=SkillStatus.NOT_FOUND,
                skill_name=capability,
                error=f"技能 '{capability}' 当前不可用",
            )
        return await SkillRegistry.execute(skill.name, **kwargs)

    @staticmethod
    def _note_missing_capability(capability: str) -> None:
        try:
            from clipwright.services.degradation import note_degradation

            note_degradation(
                "capability_unavailable",
                detail=f"能力 {capability} 当前无可用实现",
                scope="agent",
                impact="该步骤跳过或使用兜底，不中断管线",
            )
        except Exception:  # noqa: BLE001 — 降级记录失败不该带崩调用方
            pass

    def tool_schemas(self, names: Any = None, fmt: str = "openai") -> list[dict[str, Any]]:
        """把工具编译成 LLM tool schema（按名字实时解析，缺失的静默跳过）。"""
        from clipwright.tool.registry import ToolRegistry

        wanted = list(names) if names is not None else list(self.tool_whitelist)
        out: list[dict[str, Any]] = []
        for name in wanted:
            tool = ToolRegistry.get(name)
            if tool is None:
                continue
            try:
                if not tool.is_available():
                    continue
                checker = getattr(tool, "is_configured", None)
                if callable(checker) and not checker():
                    continue
                out.append(tool.to_llm_tool(fmt))
            except Exception:  # noqa: BLE001 — 单个工具 schema 失败不影响其余
                continue
        return out

    async def run_tool_loop(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: Any = None,
        max_rounds: int = 3,
        max_calls: int = 6,
        pipeline_id: str = "",
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """一次受控的 LLM tool-use 回合（F10）。

        - **白名单**：只暴露 ``tools``（缺省=``tool_whitelist``）里解析得到的工具；
          插件卸载后该名字自然解析失败并被跳过，因此列表是"实时"的。
        - **有界**：``max_rounds`` 轮、``max_calls`` 次；超限由 ``with_tools`` 自身
          的轮数上限收口，且这里再包一层 timeout。
        - **可降级**：没有可用工具、LLM 不可用、超时 → 返回 ``{"status": "skipped"|"error"}``，
          调用方继续走原逻辑（引擎/插件缺失绝不能把管线带塌）。
        """
        schemas = self.tool_schemas(tools)
        if not schemas:
            return {"status": "skipped", "reason": "no_tools", "tool_calls": []}

        from clipwright.services.llm import LLMService

        llm = getattr(self, "_llm", None) or LLMService()
        calls: list[dict[str, Any]] = []

        async def _executor(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
            if len(calls) >= max_calls:
                return {"status": "error", "error": f"工具调用次数上限 {max_calls} 已达"}
            allowed = {s["function"]["name"] for s in schemas}
            if name not in allowed:
                return {"status": "error", "error": f"工具 {name} 不在本 Agent 的本次白名单内"}
            calls.append({"tool": name, "input": tool_input})
            from clipwright.tool.registry import ToolRegistry

            result = await ToolRegistry.execute(name, **tool_input)
            if hasattr(result, "model_dump"):
                return result.model_dump(mode="json")
            return result if isinstance(result, dict) else {"result": str(result)}

        try:
            kwargs: dict[str, Any] = {}
            if output_schema:
                kwargs["output_schema"] = output_schema
            resp = await self.run_with_timeout(
                lambda: llm.with_tools(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    tool_executor=_executor,
                    tools=schemas,
                    max_tool_rounds=max(1, max_rounds),
                    pipeline_id=pipeline_id,
                    **kwargs,
                )
            )
        except asyncio.TimeoutError:
            return {"status": "error", "reason": "timeout", "tool_calls": calls}
        except Exception as e:  # noqa: BLE001 — 工具循环失败按降级处理
            from clipwright.config import logger

            logger.warning("%s: 工具循环失败（%s）", self.agent_name, e)
            return {"status": "error", "reason": str(e)[:200], "tool_calls": calls}

        return {
            "status": "ok",
            "content": getattr(resp, "content", "") or "",
            "tool_calls": calls,
        }

    async def run_with_timeout(self, coro_factory, timeout: float | None = None):
        """带统一超时的 LLM/工具调用封装（超时抛 asyncio.TimeoutError，由调用方降级）。

        用法::

            try:
                result = await agent.run_with_timeout(lambda: llm.ask(...))
            except asyncio.TimeoutError:
                result = fallback

        超时默认取 ``self.timeout_sec``（各 Agent 可覆盖）。
        """
        return await asyncio.wait_for(coro_factory(), timeout=timeout or self.timeout_sec)

    async def llm_call_with_retry(
        self,
        coro_factory,
        retries: int = 1,
        timeout: float | None = None,
        retry_delay_sec: float = 1.5,
    ):
        """生产加固 1.4：统一 LLM 调用收口（委托模块级 unified_llm_call）。"""
        return await unified_llm_call(
            self.agent_name, coro_factory,
            retries=retries, timeout=timeout or self.timeout_sec,
            retry_delay_sec=retry_delay_sec,
        )

    async def llm_or_fallback(self, coro_factory, fallback, timeout: float | None = None, retries: int = 1):
        """LLM 调用 + 重试 + 超时兜底：失败一律返回 fallback 并记录日志。

        F9：兜底成功≠没出事——这里必须登记降级，否则「LLM 挂了、用模板顶上」
        在下游看来与「LLM 正常产出」完全一样。
        """
        from clipwright.config import logger
        from clipwright.services.degradation import note_degradation

        try:
            return await self.llm_call_with_retry(coro_factory, retries=retries, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("%s: LLM 调用超时，使用兜底", self.agent_name)
            note_degradation(
                "llm_timeout_fallback",
                detail=f"{self.agent_name}: LLM 调用超时（>{timeout or self.timeout_sec}s）",
                scope=self.agent_name,
                impact="该步骤使用兜底结果继续，成品质量可能下降",
            )
            return fallback
        except Exception as e:
            logger.warning("%s: LLM 调用失败（%s），使用兜底", self.agent_name, e)
            note_degradation(
                "llm_error_fallback",
                detail=f"{self.agent_name}: LLM 调用失败（{e}）",
                scope=self.agent_name,
                impact="该步骤使用兜底结果继续，成品质量可能下降",
            )
            return fallback

    @staticmethod
    def quality_issues_hint(context: Any) -> str:
        """C6: 自愈重做时提取上一轮质检问题段落（注入 LLM 提示词尾部）。

        pipeline_v2 自愈循环此前把 _quality_issues 写入 extra_params，但没有任何
        Agent 读取——被要求重做的 Agent 不知道为什么重做。无问题/非自愈时返回空串。
        """
        issues = (getattr(context, "extra_params", {}) or {}).get("_quality_issues") or []
        lines: list[str] = []
        for item in issues[:5]:
            if isinstance(item, dict):
                lines.append(
                    f"- [{item.get('severity', '')}] {item.get('category', '')}: "
                    f"{str(item.get('message', ''))[:120]}"
                )
        if not lines:
            return ""
        return ("\n\n## 上一轮质检发现问题（本次重做必须针对性修复，不要重复同类问题）\n"
                + "\n".join(lines))
