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
#: (task_id, idem_key) → 上次结果；有界（model 摸底次数远小于此）
_TOOL_SEEN: dict[tuple[str, str], dict[str, Any]] = {}


def _is_repeatable(name: str) -> bool:
    return str(name) in _REPEATABLE_TOOLS


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
    if str(getattr(settings, "llm_provider", "") or "").lower() in ("ollama",):
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


def build_tools() -> list[dict[str, Any]]:
    """本轮可用的工具集 = 工具面（已含 `delegate_agent` / `research_agent`）。"""
    from clipwright.tool.surface import schemas as surface_schemas

    try:
        return list(surface_schemas())
    except Exception as exc:  # noqa: BLE001 — 工具面构建失败要响，但循环仍可退化
        logger.warning("工具面构建失败: %s", exc)
        return agent_tool_schemas()


def _system_prompt(task: Any) -> str:
    return (
        "你是视频创作系统的主 agent。你直接调用工具来推进任务：可以读取工作区文件/报告/产物，"
        "也可以派子 agent 去做具体阶段。每次只做当前最必要的一步；做完就停下来汇报。\n"
        "规则：\n"
        "- **动手前先说话**：每次调用工具之前，用一两句话说明你打算做什么、为什么这么做"
        "（这段话会直接展示给用户，让人看得懂你在推进什么）；然后立刻调用工具，不要只说不做；\n"
        "- **创作任务的完成标准是产出可渲染时间线**：光读文件、列目录、看报告**不算完成**。"
        "要出片就必须用 delegate_agent 依次派阶段（structure→material→edit→animation→audio），"
        "让 audio 阶段落出 final_timeline；\n"
        "- 不要在没有 timeline 的情况下说\"完成\"——收尾守门会把它判成失败；\n"
        "- 缺输入时先补上游（派对应阶段/调研），不要凭空编造；\n"
        # 降级不可默许（E2E 2026-10-29 用户裁定）：delegate_agent 的返回里
        # `degraded=true / degradations[]` 就是"完成了但打折扣"，模型必须当轮决策。
        "- **降级的阶段结果不算干净成功**：`delegate_agent` 返回里出现 `degraded=true` 或"
        "非空 `degradations[]`（LLM JSON 截断、自批判被跳过、模板兜底、类型化回退…）时，"
        "你必须在同一轮做出决定并立刻执行——重派该阶段 / 换策略 / 明确写清「接受哪一条、"
        "对成片有什么影响」；不许用一句「已完成」把降级带过去，收尾守门会把你推回来重做；\n"
        "- **大对象一律用引用，不要内联**：`delegate_agent` 的 `inputs` 只放小值或"
        " `{\"artifact\": \"art_xxx\", \"path\": \"...\"}` 引用。报告全文、场景数组、候选片段"
        "（几十 KB）**必须**走 `artifact` / `input_refs`——把它们写进 `inputs` 会让本轮工具"
        "参数超出模型输出预算、JSON 断裂，整条任务直接失败（E2E 2026-10-29 真实事故）；\n"
        "- **降级必须留收据**：重派同一阶段并跑成功会自动记 `remediated`；"
        "如果确实要接受，调用 `acknowledge_degradation(kinds=[...], reason=..., impact=...)` "
        "写一条 `accepted` 收据（reason ≥10 字，写真实理由）。既没重派也没收据 → "
        "任务会以 `degradation_unresolved` 失败收尾；\n"
        "- **占位画面必须修，不许当成功**：`material` 返回 `material_placeholder` 降级"
        "（场景无匹配素材 → 文字/纯色卡）时，你要重派 `material` 换更泛的英文关键词/换源/放宽"
        "阈值，把占位换成真实画面；只有确实搜不到时才保留，并在结论里逐场写明原因。"
        "成片里成片的纯色卡=用户眼里的\"偷懒回退\"（E2E 2026-10-30 现场 07:10）；\n"
        "- 派子 agent 时用 inputs 显式传参（artifact 引用或内联值）；\n"
        "- 只读探查最多用于开局摸清现状，别把回合全花在 read_file/list_dir 上；\n"
        "- 工具报「未找到工作区 / 路径不在白名单」属于环境问题：不要反复重试同一个调用，"
        "如实说明缺什么并结束；\n"
        "- 任务受限：回合、工具调用次数、墙钟、预算都会强制中止。"
    )


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
    head = f"\n\n## 回合预算\n剩余 {remaining}/{max_turns} 回合（已用 {used}）。"
    if remaining > 40:
        return head + "按需推进，不要重复摸底。"
    if remaining > 12:
        return head + ("收尾优先级升高：产物齐了就先收尾；"
                       "只做实修，不要再 list/read 摸底。")
    return head + ("**必须立即收尾**：先用 acknowledge_degradation 写收据"
                   "（或说明为何无需），然后直接 finish；不要再调任何查询类工具。")


def _user_prompt(task: Any, context_text: str) -> str:
    request = getattr(task, "request", task)
    topic = str(getattr(request, "topic", "") or "")
    hypothesis = ""
    plan = getattr(task, "plan", None)
    if plan is not None and getattr(plan, "steps", None):
        hypothesis = "\n\n## 初始假设（仅供参照，不是必须执行的步骤表）\n" + "\n".join(
            f"- {getattr(s, 'title', '') or getattr(s, 'capability', '')}" for s in plan.steps[:8]
        )
    return (f"## 用户需求\n{topic}\n\n## 当前上下文\n{context_text}"
            f"{hypothesis}{_budget_note(task)}")


async def run_loop(task: Any, *, context_text: str = "",
                   shadow: bool = False,
                   guard: Any = None,
                   on_tool_call: Any = None,
                   dry_run: bool = False) -> dict[str, Any]:
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
    tools = build_tools()
    emit(task_id, "narration",
         "主 agent 开始行动（工具循环·影子）" if shadow else "主 agent 开始行动（工具循环）",
         {"mode": "shadow" if shadow else "tool_loop", "tools": len(tools)})

    calls: list[dict[str, Any]] = []
    #: (工具名, 参数指纹) → 连续失败次数；用来阻断"同一个错反复撞"的空转
    fail_streak: dict[str, int] = {}
    started = time.monotonic()
    limits = getattr(task, "limits", None)
    max_rounds = int(getattr(settings, "agent_loop_max_turns", 0)
                     or getattr(limits, "max_turns", 30) or 30)
    max_calls = int(getattr(settings, "agent_loop_max_tool_calls", 0)
                    or getattr(limits, "max_tool_calls", 80) or 80)

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
                          {"tool": name, "args": args, "shadow": shadow})
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

            # 原子工具与派发类工具走**同一个入口**（surface.execute）：
            # 同一份档位裁定、同一份幂等键、同一份审计记录。
            result = await surface_execute(
                name, args, pipeline_id=task_id,
                idem_key=_key, task=task,
                dry_run=dry_run,
            )
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
              "brief": _brief_of(result)},
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
            max_tool_rounds=max(1, min(max_rounds, 12)),
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
                    max_tool_rounds=max(1, min(max_rounds, 12)),
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