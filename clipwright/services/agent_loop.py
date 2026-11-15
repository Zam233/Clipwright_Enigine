"""Kernel 原生 tool-use loop（PRD §2.2）：模型拿工具 schema 自主行动。

关键区别（也是"agent 还是编排管线"的分界线）：

- **不再**让模型先吐 `steps[]` 再由代码按数组执行；
- 每轮把 `surface.schemas()` 作为 `tools` 传给 provider（派发类工具
  `delegate_agent` / `research_agent` 已在工具面内，见 `tool/surface.py`），
  模型发 tool_call → 执行 → 结果按 provider 协议回填 → 模型继续或给终态。

Plan 只作为**初始假设**（`initial_hypothesis`）放进提示词，不做循环游标。

开关见 `settings.kernel_mode`（`tool_loop` 默认 / `shadow` / `plan` 回滚）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from clipwright.config import logger, settings
from clipwright.services.task_events import emit

#: 派发类工具名（唯一定义在工具面，这里只是可读别名，避免两处 schema 漂移）
DELEGATE_TOOL = "delegate_agent"
RESEARCH_TOOL = "research_agent"


class LoopAborted(RuntimeError):
    """工具循环被外部闸中止（取消/暂停/预算/墙钟）——由 Kernel 解释成任务终态。

    `__control_flow__ = True` 是给 `LLMService._do_tool_call` 看的：它必须把这个
    异常**透传**出去，而不是当成工具失败回填给模型（否则闸变成"告诉模型出错了"）。
    """

    __control_flow__ = True

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def agent_tool_schemas() -> list[dict[str, Any]]:
    """派发类工具的 schema（直接从工具面取，不再手抄一份）。"""
    from clipwright.tool.surface import AGENT_TOOLS, build_surface

    return [e.schema for e in build_surface() if e.name in AGENT_TOOLS]


#: 可"同参数直接复用"的工具（纯只读查询：摸底/看目录/读报告/查 run）。
#: 写操作与派发**不进**这张表——它们必须真的执行。
_REPEATABLE_TOOLS = frozenset({
    "list_dir", "list_artifacts", "list_reports", "read_report", "read_artifact",
    "list_agent_runs", "get_agent_run", "media_probe", "describe_llm_mg",
    "list_files", "read_file", "inspect",
})
#: 结构性护栏（**不是**预算闸）：单次 tool loop 最多这么多次模型往返 / 工具调用，
#: 以及一次 LLM 调用内部最多这么多个工具往返。0 配置下靠它们兜住"死循环打 provider"。
#: 真正的终止条件在 `agent_kernel._gate_hit`（无进展 / 重复动作 / 降级 / 完成标准 / 取消）。
STRUCTURAL_MAX_ROUNDS = 600
STRUCTURAL_MAX_CALLS = 4000
STRUCTURAL_TOOL_ROUNDS = 24

#: (task_id, idem_key) → 上次结果；有界（model 摸底次数远小于此）
_TOOL_SEEN: dict[tuple[str, str], dict[str, Any]] = {}

#: 同一工具在同任务里**连续失败**的次数（与参数无关）与最后一条错误。
#: 为什么需要：同参数 3 次的护栏抓不到"换着参数撞同一个坑"——edit 阶段对空素材
#: 目录裁了 41 次 video_trim（每次 start_sec 不同），墙钟直接烧到上限。
_TOOL_FAILS_BY_NAME: dict[tuple[str, str], int] = {}
_TOOL_LAST_ERROR: dict[tuple[str, str], str] = {}
#: 连续失败到这个次数就不再执行该工具（把"重试"换成"换方案"）
_TOOL_FAIL_LIMIT = 5


def _is_repeatable(name: str) -> bool:
    return str(name) in _REPEATABLE_TOOLS


def _skill_meta(name: str) -> dict[str, Any]:
    """工具调用事件的 kind/skill 标记（审计：主 agent 的技能调用与工具不可分）。

    能力名由 SkillRegistry 判定；异常时按普通工具处理，绝不让事件层炸掉执行。
    """
    try:
        from clipwright.tool.surface import is_skill

        if is_skill(name):
            return {"kind": "skill", "skill": name}
    except Exception:  # noqa: BLE001
        pass
    return {"kind": "tool"}


def _seq_of(event: Any) -> str:
    """把上一条事件变成 `causation_id`（`seq:N`）；拿不到就空——不编造。"""
    seq = getattr(event, "seq", None)
    return str(seq) if seq is not None else ""


def is_agent_tool(name: str) -> bool:
    from clipwright.tool.surface import AGENT_TOOLS

    return name in AGENT_TOOLS


def tool_loop_available() -> tuple[bool, str]:
    """现在能不能真的跑 tool loop；返回 `(可用, 原因)`。

    三个必要条件，缺一就明确降级回 plan adapter（不允许"看起来在跑 loop"）：

    1. `kernel_mode` 是 `tool_loop` / `shadow`；
    2. provider 声明 `tool_calls` 能力（协议层支持原生工具回填）；
    3. 有可用的模型凭据——**必须连 role binding 一起看**：真实调用走
       `_resolve_role_binding("main")`，role-bound 部署里 `settings.llm_api_key`
       可以是空的。只看全局 key 会把"能跑"误判成"不能跑"，静默退回 plan。

    第 3 条同时保证：单元测试环境（注入 fake planner、不配 key）走 plan adapter，
    不会因为默认 `tool_loop` 而对真实 provider 发起网络请求。
    """
    mode = str(getattr(settings, "kernel_mode", "plan"))
    if mode not in ("tool_loop", "shadow"):
        return False, f"kernel_mode={mode}"
    try:
        from clipwright.providers.base import llm_capabilities
        from clipwright.services.llm import _resolve_llm_provider

        impl = _resolve_llm_provider(settings.llm_provider)
        if impl is None:
            return False, "no_provider_impl"
        if not llm_capabilities(impl, settings.llm_model).tool_calls:
            return False, f"provider={settings.llm_provider} tool_calls=false"
    except Exception as exc:  # noqa: BLE001 — 能力自省失败按不可用处理
        return False, f"capability_probe_failed:{type(exc).__name__}"
    if not _has_credential():
        return False, "no_llm_credential"
    return True, "ok"


def _has_credential() -> bool:
    """凭据判定：全局 key / role binding key / 本地免 key provider 三者任一。"""
    if str(getattr(settings, "llm_api_key", "") or "").strip():
        return True
    from clipwright.providers.llm import llm_provider_is_local

    if llm_provider_is_local(getattr(settings, "llm_provider", "")):
        return True
    try:
        from clipwright.services.llm import _resolve_role_binding

        for role in ("main", ""):
            bound = _resolve_role_binding(role) or {}
            if str(bound.get("api_key") or "").strip():
                return True
    except Exception:  # noqa: BLE001 — 探不到绑定就当没有
        return False
    return False


def build_tools(closeout: bool = False,
                closeout_extra: Any = ()) -> list[dict[str, Any]]:
    """本轮可用的工具集 = 工具面（已含 `delegate_agent` / `research_agent`）。

    `closeout=True`（软着陆收口模式）：只给只读档位工具。收口期"不许再扩展任务范围"
    这件事必须由**工具面**保证——写在提示词里只是建议，模型可以不理。
    `closeout_extra`：收口期仍放行的少数工具（质量阶段派发，预算已由 escrow 冻结）。
    """
    from clipwright.tool.surface import schemas as surface_schemas

    try:
        return list(surface_schemas(closeout=closeout,
                                    closeout_extra=tuple(closeout_extra or ())))
    except Exception as exc:  # noqa: BLE001 — 工具面构建失败要响，但循环仍可退化
        logger.warning("工具面构建失败: %s", exc)
        return agent_tool_schemas()


#: plan_only 任务（主 agent 派发"生成制作规划书"）的系统提示词。
#:
#: 与创作任务的差别只有一条：**不要去做出片**。旧实现没有这个分支，于是一个
#: `plan_only=true` 的任务会照着"完成标准是 final_timeline"一路跑到 material/edit，
#: 既烧钱又和需求侧的规划书流程打架（PRD plan-dispatch D2）。
def _p(name: str) -> str:
    """取提示词文本：插件符号覆盖优先，否则 prompts.py 默认值（B5 常量覆盖）。

    原先本模块把三份系统提示词内联在这里（`_PLAN_ONLY_SYSTEM` / `_CLOSEOUT_BLOCK` /
    `_normal_system_prompt()`），与 `clipwright/prompts.py` 的 LOOP_* 常量**逐字节相同**
    （sha256 一致，见台账 Batch 5）。保留内联副本等于留一份会漂移的第二真相，所以改成
    运行时读常量；读发生在调用时，插件覆盖立即生效。

    延迟导入避免 services ↔ plugins 循环。
    """
    try:
        from clipwright.plugins.prompt_overrides import resolve_constant

        return resolve_constant(name)
    except Exception:  # noqa: BLE001 — 覆盖层不可用就用默认值，绝不因此报错
        from clipwright import prompts

        return str(getattr(prompts, name, ""))


_PLAN_ONLY_SYSTEM = _p("LOOP_PLAN_ONLY_SYSTEM")


def _is_plan_only(task: Any) -> bool:
    request = getattr(task, "request", task)
    return bool(getattr(request, "plan_only", False))


#: 软着陆收口模式的权威规则（devplan 2026-11-02 §6 提示词原文）。
#:
#: 为什么必须换提示词而不是"少给点工具就行"：只收工具面的话，模型会反复尝试
#: 用剩下的只读工具"再找找办法"，把收口窗口耗在探查上。这里把三件事说死：
#: ① 到上限**不是**停止交付的理由；② 不许声称没验证的事已完成；③ 必须在窗口内
#: 交出「做了什么 / 还剩什么 / 缺什么证据」，否则连 capped 都拿不到。
_CLOSEOUT_BLOCK = _p("LOOP_CLOSEOUT_BLOCK")


def _closeout_extra_tools(task: Any) -> tuple[str, ...]:
    """收口期额外放行的工具名（质量阶段派发通道）。

    条件：escrow 还留有余额（或金额本来就不限）且质量阶段确实没跑过。
    名字写在**工具面这一层**是有意的：`delegate_agent` 的档位是 costly，
    收口期要不要放它是"工具面策略"，不是业务 agent 的判断。
    """
    from clipwright.tool.surface import DELEGATE_AGENT

    escrow = getattr(task, "escrow", None)
    state = str(getattr(escrow, "state", "disabled"))
    remaining = float(getattr(escrow, "quality_remaining_usd", 0.0) or 0.0)
    unbounded = state == "disabled"
    if not unbounded and remaining <= 0:
        return ()
    return (DELEGATE_AGENT,)


def _is_closeout(task: Any) -> bool:
    closeout = getattr(task, "closeout", None)
    return str(getattr(closeout, "mode", "normal") or "normal") == "soft_landing"


def _system_prompt(task: Any) -> str:
    base = _p("LOOP_PLAN_ONLY_SYSTEM") if _is_plan_only(task) else _p("LOOP_NORMAL_SYSTEM")
    if _is_closeout(task):
        return base + _p("LOOP_CLOSEOUT_BLOCK")
    return base


def _normal_system_prompt() -> str:
    """正常创作任务的系统提示词（读 prompts.py 的 LOOP_NORMAL_SYSTEM）。"""
    return _p("LOOP_NORMAL_SYSTEM")


#: 正常创作任务的系统提示词（供诊断/测试引用的导入期快照）。
#: 真正拼 prompt 的地方一律走 `_system_prompt()` → `_p()`，插件覆盖才会生效。
_NORMAL_SYSTEM = _normal_system_prompt()


def _budget_note(task: Any) -> str:
    """回合预算提示（E2E 2026-10-30 实测：模型不知道自己在烧回合）。

    一次任务里 121/161 次调用都是"再确认一下现状"，最后撞 160 回合上限失败，
    而 final_timeline 早在第 16 回合就有了。把剩余回合明确写进提示词，
    并给出"剩余多少就该收尾"的具体阈值。
    """
    try:
        limits = getattr(task, "limits", None)
        usage = getattr(task, "usage", None)
        max_turns = int(getattr(limits, "max_turns", 0) or 0)
        used = int(getattr(usage, "turns", 0) or 0)
    except Exception:  # noqa: BLE001
        return ""
    if max_turns <= 0:
        return ""
    remaining = max(0, max_turns - used)
    # PRD W4.4：文案搬到 clipwright/prompts.py（build_budget_note），
    # 这里只负责算数——改文案不再需要动这段逻辑。
    # B5-T4：改走覆盖层解析 —— 插件可替换 build_budget_note（失败自动回落默认实现）。
    from clipwright.plugins.prompt_overrides import safe_builder

    return safe_builder("build_budget_note")(remaining=remaining, max_turns=max_turns, used=used)


def _user_prompt(task: Any, context_text: str) -> str:
    request = getattr(task, "request", task)
    topic = str(getattr(request, "topic", "") or "")
    hypothesis = ""
    plan = getattr(task, "plan", None)
    if plan is not None and getattr(plan, "steps", None):
        hypothesis = "\n\n## 初始假设（仅供参照，不是必须执行的步骤表）\n" + "\n".join(
            f"- {getattr(s, 'title', '') or getattr(s, 'capability', '')}" for s in plan.steps[:8]
        )
    plan_only_note = ""
    if _is_plan_only(task):
        plan_only_note = (
            "\n\n## 本次目标（plan_only）\n"
            "只产出**制作规划书所需的分镜场景**：派一次 `delegate_agent(stage=\"structure\")`，"
            "拿到场景后立刻收尾汇报。不要进入素材/剪辑/动画/音频阶段。"
        )
    return (f"## 用户需求\n{topic}\n\n## 当前上下文\n{context_text}"
            f"{plan_only_note}{hypothesis}{_budget_note(task)}")


async def run_loop(task: Any, *, context_text: str = "",
                   shadow: bool = False,
                   guard: Any = None,
                   on_tool_call: Any = None,
                   dry_run: bool = False,
                   closeout: Any = None) -> dict[str, Any]:
    """跑完一次 tool-use loop，返回 `{status, finish, tool_calls, cost, stopped_reason}`。

    - `shadow=True`（PRD §5.2）：照常让模型选工具、照常记录调用序列与成本，但**不执行**
      ——工具执行器只回填"录制/影子结果"，用于把 tool loop 的决策与 plan 模式的决策做对比。
    - `guard`：无参可调用，返回非空字符串即中止（取消/暂停/预算/墙钟）。
      模型请求**进行中**也能停：检查点落在每次工具执行前，中止后 Kernel 从检查点恢复。
    - `on_tool_call`：每次真实工具调用后回调（Kernel 用它累计 usage.tool_calls / cost）。
    - `dry_run=True`（预览模式）：原子工具走 `surface.execute(dry_run=True)` 的**结构预检**
      （参数/路径可验证，不产生副作用），派发类工具**直接拒绝**（预览不派子 agent、
      不写产物、不花钱）。这是用户勾了"只预览"时唯一正确的行为——
      旧实现让 dry-run 停在失效字段上，预览变真跑。
    """
    from clipwright.config import settings
    from clipwright.services.llm import LLMService

    task_id = str(getattr(task, "id", "") or "")
    # 每次 run_loop 都是新的思考缓冲（同一任务续跑时不该继承上一次的残留）
    _THINKING.pop(task_id, None)
    # 收口模式（devplan 2026-11-02 §6）：工具面只留只读档位。参数可以由 Kernel 显式传，
    # 也可以从 task.closeout 自己判断——两处都不依赖调用方记得传。
    _closeout = bool(closeout) or (closeout is None and _is_closeout(task))
    # 质量阶段派发在收口期仍要放行：它的预算已被 escrow 提前冻结（用户裁定 2：
    # 总额耗尽也不许影响质量 agent）。是否真的还有余额由 Kernel 决定，见
    # `closeout_extra_tools()`；执行器还会二次校验只允许 quality 这一个阶段。
    _closeout_extra = _closeout_extra_tools(task) if _closeout else ()
    tools = build_tools(closeout=_closeout, closeout_extra=_closeout_extra)
    emit(task_id, "narration",
         "主 agent 开始行动（工具循环·影子）" if shadow else "主 agent 开始行动（工具循环）",
         {"mode": "shadow" if shadow else "tool_loop", "tools": len(tools)})

    calls: list[dict[str, Any]] = []
    #: (工具名, 参数指纹) → 连续失败次数；用来阻断"同一个错反复撞"的空转
    fail_streak: dict[str, int] = {}
    started = time.monotonic()
    limits = getattr(task, "limits", None)
    # 2026-11-02 用户裁定：回合/工具调用**不再是预算闸**（0 = 不限）。
    # 这里剩下的两个数是**结构性护栏**（防单次 LLM 调用里无限打转把 provider 打爆），
    # 不是"到点就收尾"的中止条件——真正的终止条件在 `agent_kernel._gate_hit`
    # （无进展 N=6 / 跨阶段重复动作熔断 / 降级未处置 / 完成标准 / 用户取消）。
    max_rounds = int(getattr(settings, "agent_loop_max_turns", 0)
                     or getattr(limits, "max_turns", 0) or STRUCTURAL_MAX_ROUNDS)
    max_calls = int(getattr(settings, "agent_loop_max_tool_calls", 0)
                    or getattr(limits, "max_tool_calls", 0) or STRUCTURAL_MAX_CALLS)

    # 收口窗口独立于普通轮次（devplan §6）：进收口后按 closeout.max_rounds 而不是
    # 普通 max_rounds 计轮，否则"600 轮后的 8 轮收口"根本跑不起来。
    _rounds_cap = max_rounds
    if _closeout:
        _co = getattr(task, "closeout", None)
        _rounds_cap = int(getattr(_co, "max_rounds", 8) or 8)

    async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        # 提前占位：下面几个"拒绝"分支也要引用它（否则第一支就 NameError）
        call_event: Any = None
        # 闸在**每次工具执行前**检查：模型请求进行中也能停（PRD §5.2 验收「取消在
        # 模型请求中间也能恢复」）——旧实现的闸只在 plan-step 边界跑，900s 阶段停不住。
        if guard is not None:
            reason = guard()
            if reason:
                raise LoopAborted(str(reason))
        # 收口期只允许派**质量阶段**（它的预算已被 escrow 冻结）。其它阶段的派发一律
        # 拒绝并留痕：工具面放行 delegate_agent 是为了这条通道，不是为了重开执行范围。
        if _closeout and name in _closeout_extra:
            from clipwright.tool.surface import DELEGATE_AGENT as _DA

            if name == _DA:
                _stage = str((args or {}).get("stage") or "").strip()
                if _stage != "quality":
                    calls.append({"tool": name, "ok": False, "status": "refused",
                                  "duration_ms": 0})
                    emit(task_id, "tool_result",
                         f"{name}({_stage}) → refused（收口期只允许派质量阶段）",
                         {"tool": name, "stage": _stage, "status": "refused",
                          "reason": "closeout_scope"})
                    return {"status": "refused",
                            "error": "收口期只允许派质量阶段（quality）；其它阶段不再开启"}
        if len(calls) >= max_calls:
            # 上限也要留痕：静默丢弃会让"为什么没做完"变成谜
            calls.append({"tool": name, "ok": False, "status": "refused",
                          "duration_ms": 0})
            emit(task_id, "tool_result", f"{name} → refused（调用次数到顶）",
                 {"tool": name, "status": "refused", "limit": max_calls},
                 causation_id=f"seq:{_seq_of(call_event)}")
            return {"status": "refused",
                    "error": f"工具调用次数已达上限（{max_calls}）"}
        call_event = emit(task_id, "tool_call", f"{name}",
                          {"tool": name, "args": args, "shadow": shadow,
                           **_skill_meta(name)})
        if shadow:
            # 影子：不执行副作用，只记录"模型想调什么"
            calls.append({"tool": name, "ok": True, "status": "shadowed",
                          "duration_ms": 0})
            return {"status": "ok", "shadow": True,
                    "note": "shadow 模式未执行；调用序列已记录用于对比"}
        if dry_run and is_agent_tool(name):
            result = {"status": "refused",
                      "error": f"dry-run 预览模式不派发子 agent（{name}）",
                      "note": "预览只做结构预检；要看真实产出请关掉 dry_run"}
            calls.append({"tool": name, "ok": False, "status": "refused",
                          "duration_ms": 0})
            emit(task_id, "tool_result", f"{name} → refused（dry-run）",
                 {"tool": name, "status": "refused", "dry_run": True},
                 causation_id=f"seq:{_seq_of(call_event)}")
            return result

        t0 = time.monotonic()
        try:
            from clipwright.tool.surface import execute as surface_execute
            from clipwright.tool.surface import idem_key_for as _idem_key

            _key = _idem_key(task_id, name, args)
            # 重复查询护栏（E2E 2026-10-30）：模型会在每轮重新"摸底"，
            # 一次任务里 list_dir 58 次、read_artifact 38 次，把 160 个回合烧光。
            # 只读工具的同参数调用**直接返回上次结果**并标注 deduped；要真重跑就带
            # refresh=true（会走不同幂等键）。写操作/派发不受影响。
            # 回合见底就禁止再"摸底"：查询类工具直接拒，并把收尾指引回给模型。
            # 这是**确定性**约束——否则模型会把最后几个回合也花在 list_dir 上，
            # 然后撞上限失败（E2E 2026-10-30：final_timeline 早就有，仍烧满 160 回合）。
            try:
                _limits = getattr(task, "limits", None)
                _used = int(getattr(getattr(task, "usage", None), "turns", 0) or 0)
                _max = int(getattr(_limits, "max_turns", 0) or 0)
                _remaining = (_max - _used) if _max > 0 else 999
            except Exception:  # noqa: BLE001
                _remaining = 999
            if _remaining <= 5 and _is_repeatable(name):
                _refused = {
                    "status": "refused",
                    "error": (f"剩余回合只剩 {_remaining}：不再执行查询类工具（{name}）。"
                              "请立刻用已有信息收尾：需要留收据就调 acknowledge_degradation，"
                              "然后直接 finish。"),
                }
                calls.append({"tool": name, "ok": False, "status": "refused",
                              "duration_ms": 0})
                emit(task_id, "tool_result", f"{name} → refused（回合预算见底）",
                     {"tool": name, "status": "refused", "remaining_turns": _remaining},
                     causation_id=f"seq:{_seq_of(call_event)}")
                return _refused

            _dedupe = bool(getattr(settings, "agent_tool_dedupe", True))
            if _dedupe and not dry_run and _is_repeatable(name):
                _cached = _TOOL_SEEN.get((task_id, _key))
                if _cached is not None:
                    result = dict(_cached)
                    result["deduped"] = True
                    result["note"] = (
                        "同参数已查过，直接返回上次结果（见上下文/上文）。"
                        "不要重复摸底；要重跑请带 refresh=true。")
                    calls.append({"tool": name, "ok": True, "deduped": True,
                                  "status": result.get("status"), "duration_ms": 0})
                    emit(task_id, "tool_result",
                         f"{name} → 复用上次结果（重复查询已拦截）",
                         {"tool": name, "deduped": True, "status": result.get("status")},
                         causation_id=f"seq:{_seq_of(call_event)}")
                    return result

            # 同一工具在当前任务里连续失败太多次 = 这个能力在这个环境里就是不可用
            # （E2E 2026-10-31 实测：edit 阶段对着空素材目录裁 41 次 video_trim，
            # 每次报错都带不同的 start_sec，所以"同参数 3 次"的护栏一个都没命中，
            # 最后把墙钟烧到 3600s 上限）。这里按**工具**计数，与参数无关。
            _name_key = (task_id, name)
            _name_fails = _TOOL_FAILS_BY_NAME.get(_name_key, 0)
            if _name_fails >= _TOOL_FAIL_LIMIT and not dry_run:
                _last_err = str(_TOOL_LAST_ERROR.get(_name_key) or "")[:200]
                _refused = {
                    "status": "refused",
                    "error": (f"{name} 在本任务已连续失败 {_name_fails} 次"
                              f"（最后一次：{_last_err}）。这个能力在当前环境不可用——"
                              "换方案（换素材/换工具/改用占位卡），或如实说明做不到；"
                              "不要再用同一工具重试。"),
                    "blocked_tool": name,
                    "failure_count": _name_fails,
                }
                calls.append({"tool": name, "ok": False, "status": "refused",
                              "duration_ms": 0})
                emit(task_id, "tool_result", f"{name} → refused（该工具已连续失败 {_name_fails} 次）",
                     {"tool": name, "status": "refused", "blocked_tool": name,
                      "failure_count": _name_fails},
                     causation_id=f"seq:{_seq_of(call_event)}")
                return _refused

            # 原子工具与派发类工具走**同一个入口**（surface.execute）：
            # 同一份档位裁定、同一份幂等键、同一份审计记录。
            result = await surface_execute(
                name, args, pipeline_id=task_id,
                idem_key=_key, task=task,
                dry_run=dry_run,
            )
            if result.get("status") in ("ok", "dry_run"):
                _TOOL_FAILS_BY_NAME.pop(_name_key, None)
                _TOOL_LAST_ERROR.pop(_name_key, None)
            else:
                if len(_TOOL_FAILS_BY_NAME) > 400:
                    _TOOL_FAILS_BY_NAME.clear()
                    _TOOL_LAST_ERROR.clear()
                _TOOL_FAILS_BY_NAME[_name_key] = _TOOL_FAILS_BY_NAME.get(_name_key, 0) + 1
                _TOOL_LAST_ERROR[_name_key] = str(result.get("error") or "")[:300]
            if _dedupe and not dry_run and _is_repeatable(name) \
                    and result.get("status") in ("ok", "dry_run"):
                if len(_TOOL_SEEN) > 400:
                    _TOOL_SEEN.clear()
                _TOOL_SEEN[(task_id, _key)] = dict(result)
        except LoopAborted:
            raise
        except Exception as exc:  # noqa: BLE001 — 工具异常不能让循环崩
            result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        elapsed = int((time.monotonic() - t0) * 1000)
        calls.append({"tool": name, "ok": result.get("status") == "ok",
                      "status": result.get("status"), "duration_ms": elapsed})

        # 同一调用反复失败 = 环境缺东西（比如没有工作区），不是模型"再想想"能解决的。
        # 实测：没有工作区时模型会把 10 次调用全花在 list_dir/list_reports 的 error 上，
        # 最后交不出时间线还烧满回合。这里直接中止，让 Kernel 如实收尾。
        fingerprint = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)[:200]}"
        if result.get("status") in ("ok", "dry_run"):
            fail_streak.pop(fingerprint, None)
        else:
            fail_streak[fingerprint] = fail_streak.get(fingerprint, 0) + 1
            # 进度播报（PRD §14）：同一工具/参数连续失败 2 次 → 让用户看见卡在哪
            if fail_streak[fingerprint] == 2:
                try:
                    from clipwright.services import progress as _progress

                    _progress.emit_progress(
                        task_id, trigger="tool_failure_streak",
                        action=f"{name} 连续失败 2 次",
                        conclusion=str(result.get("error") or "")[:160],
                        next_action="换实现或换参数，不再重复同一调用",
                        failure_streak={"tool": name, "fingerprint": fingerprint[:80],
                                        "count": 2,
                                        "error_summary": str(result.get("error") or "")[:160]},
                        once_key=f"fail_streak:{fingerprint[:60]}")
                except Exception:  # noqa: BLE001 — 播报失败不影响循环
                    pass
            if fail_streak[fingerprint] >= 3:
                raise LoopAborted(
                    f"同一工具连续失败 3 次（{name}）：{str(result.get('error') or '')[:120]}"
                )
        if on_tool_call is not None:
            try:
                # 把**真实调用参数**一并回调：录制/回放与 digest 都按参数建键，
                # 只给结果会让"录了却回放不到"（键对不上）。
                on_tool_call(name, result, args)
            except TypeError:
                on_tool_call(name, result)
            except Exception:  # noqa: BLE001 — 记账失败不影响执行
                pass
        emit(task_id, "tool_result", f"{name} → {result.get('status')}",
             {"tool": name, "status": result.get("status"),
              "error": result.get("error"), "duration_ms": elapsed,
              "brief": _brief_of(result), **_skill_meta(name)},
             causation_id=f"seq:{_seq_of(call_event)}")
        return result

    def _on_preamble(content: str, round_no: int, tool_calls: list[Any]) -> None:
        """B2：模型的"要动手之前说的话"。先入事件流，再执行工具。

        `before_tool` 里带上本轮要调的工具名：前端据此把这段叙述挂在正确的工具
        调用之前，而不是当成又一条普通日志。
        """
        names: list[str] = []
        for tc in tool_calls:
            name = getattr(tc, "name", None)
            if not name and isinstance(tc, dict):
                name = tc.get("name") or ((tc.get("function") or {}).get("name"))
            if name:
                names.append(str(name))
        emit(task_id, "narration", content,
             {"kind": "preamble", "round": round_no,
              "before_tool": names[:5], "tool_count": len(tool_calls),
              "shadow": shadow})

    def _on_reasoning(delta: str, round_no: int) -> None:
        """B5：provider 给的 reasoning 逐轮转发成 `thinking`（不再闷在日志里）。

        顺序契约：同一轮里 thinking → narration(preamble) → tool_call。
        `generate` 的 on_reasoning 在本轮 tool_calls 判定**之前**触发，因此 thinking
        天然早于 narration。
        """
        if not delta:
            return
        _append_thinking(task_id, delta)
        emit(task_id, "thinking", delta[:2000],
             {"delta": delta, "done": False, "round": round_no, "shadow": shadow})

    thinking_round = {"n": 0}

    def _reasoning_cb(delta: str, round_no: int = 1) -> None:
        thinking_round["n"] = max(thinking_round["n"], int(round_no or 1))
        _on_reasoning(delta, round_no)

    # B5：任务级 reasoning_effort（未设置时沿用模型配置；provider 不支持则在
    # llm 层记 degradation 并在事件流里给出"已忽略"）。
    _request = getattr(task, "request", task)
    _effort = ""
    try:
        _extra = getattr(_request, "extra", None) or {}
        _effort = str((_extra or {}).get("reasoning_effort") or "").strip().lower()
    except Exception:  # noqa: BLE001
        _effort = ""
    reasoning_kwargs: dict[str, Any] = {"reasoning_effort": _effort} if _effort else {}
    try:
        from clipwright.services.llm import MAX_OUTPUT_TOKENS

        resp = await LLMService().with_tools(
            system_prompt=_system_prompt(task),
            user_prompt=_user_prompt(task, context_text),
            tool_executor=_executor,
            tools=tools,
            max_tool_rounds=max(1, min(_rounds_cap, STRUCTURAL_TOOL_ROUNDS)),
            on_preamble=_on_preamble,
            on_reasoning=_reasoning_cb,
            pipeline_id=task_id,
            # 主 agent 的 tool_call arguments 可能是大对象（例如把 43 个场景内联
            # 进 delegate_agent）。不给预算时会被 provider 默认截断 → 半个 JSON →
            # `JSONDecodeError: Unterminated string` → 整个任务 failed
            # （E2E 2026-10-28 实测 char 3337 处断掉）。
            max_tokens=MAX_OUTPUT_TOKENS,
            **reasoning_kwargs,
        )
    except LoopAborted as exc:
        emit(task_id, "gate_hit", f"触闸：{exc.reason}",
             {"reason": exc.reason, "tool_calls": len(calls)})
        return {
            "status": "aborted",
            "error": exc.reason,
            "tool_calls": calls,
            "stopped_reason": exc.reason,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 — provider 不支持工具/网络问题
        # 工具参数 JSON 断裂（模型把大对象内联进 delegate_agent 时最常发生）不该
        # 直接判任务死刑：给模型**一次**纠错机会，明确要求改用 artifact/input_refs。
        # 现代 harness（Claude Code / opencode）都是"回报格式错误让模型重试"，
        # 而不是把一次格式失误升级成整任务失败（E2E 2026-10-29 真实事故）。
        import json as _json

        recoverable = isinstance(exc, _json.JSONDecodeError)
        if recoverable and not locals().get("_json_retry_used"):
            emit(task_id, "narration",
                 "工具调用参数不是合法 JSON（多为大对象内联被截断），已要求主 agent 重发",
                 {"kind": "tool_args_json_retry", "error": str(exc)[:200]}, span="kernel")
            logger.warning("tool-use loop 参数 JSON 断裂，带纠错提示重试一次: %s", exc)
            _json_retry_used = True
            retry_hint = (
                "\n\n## 上一轮失败（必须修正）\n"
                f"你上一次工具调用的参数不是合法 JSON：`{str(exc)[:160]}`。\n"
                "原因几乎总是**把大对象内联进了 `inputs`**（报告全文/场景数组/候选片段）。\n"
                "请重新发起同一次调用，并且：\n"
                "1. `inputs` 只放小值或 `{\"artifact\": \"art_xxx\", \"path\": \"...\"}` 引用；\n"
                "2. 报告/数组/长文案一律用 `input_refs`（工作区相对路径）传，不要复制正文；\n"
                "3. 参数必须是单个合法 JSON 对象，不要省略引号或括号。"
            )
            try:
                resp = await LLMService().with_tools(
                    system_prompt=_system_prompt(task),
                    user_prompt=_user_prompt(task, context_text + retry_hint),
                    tool_executor=_executor,
                    tools=tools,
                    max_tool_rounds=max(1, min(_rounds_cap, STRUCTURAL_TOOL_ROUNDS)),
                    on_preamble=_on_preamble,
                    on_reasoning=_reasoning_cb,
                    pipeline_id=task_id,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    **reasoning_kwargs,
                )
            except Exception as exc2:  # noqa: BLE001 — 重试仍失败才判失败
                logger.warning("tool-use loop 重试后仍失败: %s", exc2)
                return {
                    "status": "error",
                    "error": f"{type(exc2).__name__}: {exc2}",
                    "tool_calls": calls,
                    "stopped_reason": "loop_error",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "failure": {"category": "external_unavailable", "retryable": "retry_same",
                                "blocking_point": str(exc2)[:200], "evidence": []},
                }
        else:
            logger.warning("tool-use loop 失败: %s", exc)
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "tool_calls": calls,
                "stopped_reason": "loop_error",
                "duration_ms": int((time.monotonic() - started) * 1000),
                "failure": {"category": "external_unavailable", "retryable": "retry_same",
                            "blocking_point": str(exc)[:200], "evidence": []},
            }

    text = str(getattr(resp, "content", "") or "").strip()
    # B5：思考收尾（有 thinking 才发 done，避免前端凭空显示"思考结束"）
    if _THINKING.get(task_id):
        emit(task_id, "thinking", "",
             {"delta": "", "done": True, "round": thinking_round["n"] or 1,
              "shadow": shadow})
    emit(task_id, "report", "主 agent 本轮结论",
         {"stage": "kernel", "status": "ok", "bullets": [text[:300]] if text else [],
          "tool_calls": len(calls)}, span="kernel")
    return {
        "status": "ok",
        "finish": {"conclusion": text[:800], "completed": [], "unfinished": [],
                   "reason": "tool_loop_finished"},
        "tool_calls": calls,
        # 模型往返次数（≈ 每次工具调用前一轮 + 最后一轮）：Kernel 用它在**多轮之间**
        # 判回合闸；单轮内的闸由 tool_calls 计数负责。
        "rounds": len(calls) + 1,
        "dry_run": bool(dry_run),
        "stopped_reason": "model_final",
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


async def _dispatch_agent(task: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """（保留给脚本/测试直接派发用；主循环走 surface.execute）"""
    from clipwright.tool.surface import execute as surface_execute

    return await surface_execute(
        name, args, pipeline_id=str(getattr(task, "id", "") or ""), task=task,
    )


def _brief_of(result: dict[str, Any]) -> str:
    out = result.get("output")
    if isinstance(out, dict):
        for key in ("summary", "conclusion", "brief"):
            value = out.get(key)
            if value:
                return str(value)[:220]
        if out:
            return json.dumps({k: out[k] for k in list(out)[:4]}, ensure_ascii=False)[:220]
    return str(result.get("error") or "")[:220]


# ── B5：thinking 缓冲（按 task_id 隔离，不再复用全局单槽 `_LAST_REASONING`）──
#
# 背景：全局单槽在并发子任务之间会互相覆盖（A 任务的思考被 B 任务取走），而且
# agent_loop 此前**根本没有**发 thinking——`agent_kernel._emit_thinking` 是死代码，
# 前端只能显示"未提供思考内容"。这里按 task_id 存一份可取的缓冲：
# 运行期逐轮追加，收尾时 `take_thinking(task_id)` 取走并清空。

_THINKING: dict[str, list[str]] = {}
_THINKING_MAX_CHARS = 20_000


def _append_thinking(task_id: str, delta: str) -> None:
    if not delta:
        return
    buf = _THINKING.setdefault(task_id, [])
    buf.append(delta)
    # 防爆：超长时丢最早的块（思考只是过程信息，不能撑爆内存）
    total = sum(len(x) for x in buf)
    while buf and total > _THINKING_MAX_CHARS:
        total -= len(buf.pop(0))


def take_thinking(task_id: str) -> str:
    """取走并清空某任务的思考缓冲（诊断/回放/测试用）。"""
    parts = _THINKING.pop(task_id, [])
    return "".join(parts)


__all__ = ["DELEGATE_TOOL", "RESEARCH_TOOL", "LoopAborted", "agent_tool_schemas",
           "build_tools", "is_agent_tool", "run_loop", "take_thinking",
           "tool_loop_available"]


