"""Kernel 工具面（路线 C / C1）。

C0 量出的事实：57 项能力里只有 12 项（21%）当前能被 LLM 看见。C1 要把它抬到 ≥90%，
**但不是把 45 个工具类的 `agent_callable` 全翻成 True**——那个标记是给阶段 Agent 用的
（`StructureAgent` 走 `ToolRegistry.list_agent_callable()`），翻它等于把 45 个能力
顺手塞进管线的结构 Agent，属于"为了修 A 把 B 弄坏"。

所以这里做的是**第二层门面**：

- `build_surface()` 依据 `capability_matrix` 的档位裁定，决定"Kernel 能看见什么"：
  `read` / `write_local` / `costly` 可直调；**`dangerous` 一律不进直接工具面**
  （它只能经执行网关，见 PRD F6 红线 2/5）。
- schema 复用 `BaseTool.to_llm_tool("openai")`（参数从 `execute` 签名推断），
  再补上档位/幂等/成本提示——模型据此知道"这步会花钱"。
- 执行统一走 `execute()`：幂等缓存（同一 `idem_key` 不重复干活）+ 审计记录 +
  危险档拒绝（给出"请走执行网关"的可执行提示，而不是一句 forbidden）。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from clipwright.config import logger

#: 可直接进 Kernel 工具面的档位（dangerous 只能经 ExecutionGateway）
SURFACE_TIERS: tuple[str, ...] = ("read", "write_local", "costly")

#: 派发类工具（modern-agent PRD §2.4）：子 agent 就是工具，不再有"能力名解释器"。
#: 它们不在 ToolRegistry 里（不是原子工具），但必须和普通工具同处一个工具面——
#: 否则模型看到的是"一堆原子工具"而不是"可以派活的 agent 系统"。
DELEGATE_AGENT = "delegate_agent"
RESEARCH_AGENT = "research_agent"
GET_AGENT_RUN = "get_agent_run"
LIST_AGENT_RUNS = "list_agent_runs"
CANCEL_AGENT_RUN = "cancel_agent_run"
AGENT_TOOLS: tuple[str, ...] = (DELEGATE_AGENT, RESEARCH_AGENT,
                                GET_AGENT_RUN, LIST_AGENT_RUNS, CANCEL_AGENT_RUN)

_AGENT_TOOL_SPECS: dict[str, dict[str, Any]] = {
    DELEGATE_AGENT: {
        "description": (
            "派一个阶段子 agent 做一件具体的事（structure/material/edit/animation/audio/quality）。"
            "inputs 必须显式给出（上一份结果的 artifact 引用或内联值），运行时不会替你从黑板补。"
            "返回 AgentResult：summary / outputs(preview) / writes / needs / report_path。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "stage": {"type": "string",
                          "enum": ["structure", "material", "edit", "animation",
                                   "audio", "quality"]},
                "brief": {"type": "string", "description": "这次要它做什么（一句话）"},
                "inputs": {"type": "object",
                           "description": "显式输入；artifact 引用形如 {\"artifact\":\"art_xx\",\"path\":\"...\"}"},
                "expect": {"type": "object",
                           "description": "期望产出字段 → schema 名（用于结果校验）"},
            },
            "required": ["stage"],
        },
    },
    RESEARCH_AGENT: {
        "description": (
            "派一个只读调研员核实事实（文件是否存在、素材库里有什么、外部资料）。"
            "它不能写文件、不能再派子 agent。返回 AgentResult（summary/outputs/cost）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "调研角色（如 素材核查员）"},
                "brief": {"type": "string", "description": "要核实的具体问题"},
                "tools": {"type": "array", "items": {"type": "string"},
                          "description": "允许它用的只读工具子集"},
            },
            "required": ["brief"],
        },
    },
    GET_AGENT_RUN: {
        "description": (
            "查一次子 agent 的结果：给 run_id 查那一次；给 stage（edit/quality/…）"
            "查该任务最近一次同阶段 run。返回 AgentResult 全量（含失败原因与报告路径）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "stage": {"type": "string"},
            },
        },
    },
    LIST_AGENT_RUNS: {
        "description": "列出本次任务派过的所有子 agent（含状态、成本、结论摘要）。",
        "parameters": {"type": "object", "properties": {}},
    },
    CANCEL_AGENT_RUN: {
        "description": "取消一个还在跑的子 agent（不可取消时如实报错，不假装成功）。",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
}

#: agent 工具各自的档位（get/list 只读；cancel 是控制动作，按本地写处理）
_AGENT_TOOL_TIER: dict[str, str] = {
    DELEGATE_AGENT: "costly",
    RESEARCH_AGENT: "costly",
    GET_AGENT_RUN: "read",
    LIST_AGENT_RUNS: "read",
    CANCEL_AGENT_RUN: "write_local",
}

#: 确定性领域工具（PRD §4.4 必须保留）：闭包重算与阶段计划校验。
#: 它们是"领域不变量"，不是编排：模型可以选择要不要重算，但不能跳过校验。
DOMAIN_TOOLS: tuple[str, ...] = ("replan_closure", "validate_stage_plan")

_MAX_RECORDS = 500
_MAX_CACHE = 512


class SurfaceError(RuntimeError):
    """工具面构建/调用失败。"""


@dataclass(frozen=True)
class SurfaceEntry:
    name: str
    tier: str
    description: str
    schema: dict[str, Any]
    available: bool
    idempotent: bool
    dry_run: bool
    cost_hint: str = ""

    def to_public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("schema", None)  # 目录接口不返回整份 schema（太大），另开 /schema
        return payload


@dataclass
class ToolCallRecord:
    """一次 Kernel 工具调用（审计最小集，字段与 PRD A3 对齐）。"""

    name: str
    tier: str
    pipeline_id: str
    idem_key: str
    args_brief: str
    status: str
    duration_ms: float
    error: Optional[str] = None
    #: 工具自报成本；None = 未知（costly 档必须如实记"未知"，而不是记 0）
    cost_usd: Optional[float] = None
    replayed: bool = False
    ts: float = field(default_factory=time.time)

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


_RECORDS: list[ToolCallRecord] = []
_IDEM_CACHE: dict[str, dict[str, Any]] = {}


def records(pipeline_id: str = "") -> list[dict[str, Any]]:
    if pipeline_id:
        return [r.to_public() for r in _RECORDS if r.pipeline_id == pipeline_id]
    return [r.to_public() for r in _RECORDS]


def clear_records() -> None:
    _RECORDS.clear()


def clear_cache() -> None:
    _IDEM_CACHE.clear()


_COST_HINT: dict[str, str] = {
    "costly": "会花钱或长耗时：调用前先确认预算与必要性",
    "write_local": "只写项目工作区产物，可重做",
    "read": "只读，可重复调用",
}


def _describe(entry_name: str, tier: str, base: str) -> str:
    hint = _COST_HINT.get(tier, "")
    text = base or entry_name
    return f"{text}［{tier}：{hint}］" if hint else text


def build_surface(*, tiers: Iterable[str] = SURFACE_TIERS) -> list[SurfaceEntry]:
    """构建 Kernel 可直调的工具面（按档位过滤，dangerous 永不入内）。"""
    from clipwright.services.capability_matrix import (  # 延迟导入避免环
        TIERS,
        CapabilityMatrixError,
        build_matrix,
    )

    allowed = tuple(t for t in tiers if t in TIERS)
    if any(t == "dangerous" for t in allowed):
        raise SurfaceError(
            "dangerous 档不允许进直接工具面（只能经 ExecutionGateway 授权执行）"
        )

    from clipwright.tool.registry import ToolRegistry

    matrix = {e.name: e for e in build_matrix() if e.kind == "tool"}
    out: list[SurfaceEntry] = []
    for name, entry in matrix.items():
        if entry.tier not in allowed:
            continue
        tool = ToolRegistry.get(name)
        if tool is None:  # 注册表与矩阵漂移（矩阵已强校验，这里兜底）
            continue
        try:
            schema = tool.to_llm_tool("openai")
        except Exception as e:  # noqa: BLE001 — schema 推断失败不该炸整个面
            raise SurfaceError(f"工具 {name} 的 schema 推断失败: {e}") from e
        base_desc = str((schema.get("function") or {}).get("description") or "")
        schema["function"]["description"] = _describe(name, entry.tier, base_desc)
        out.append(
            SurfaceEntry(
                name=name,
                tier=entry.tier,
                description=schema["function"]["description"],
                schema=schema,
                available=entry.available,
                idempotent=entry.idempotent,
                dry_run=entry.dry_run,
                cost_hint=_COST_HINT.get(entry.tier, ""),
            )
        )
    if not out:
        raise CapabilityMatrixError("工具面为空：注册表或矩阵未初始化")
    out.extend(_skill_surface_entries(allowed))
    out.extend(_agent_surface_entries(allowed))
    out.extend(_domain_surface_entries(allowed))
    return sorted(out, key=lambda e: (e.tier, e.name))


def _domain_surface_entries(allowed: tuple[str, ...]) -> list[SurfaceEntry]:
    """确定性领域工具进工具面（否则 plan adapter 的旧步骤会被工具面拒绝）。"""
    if "read" not in allowed:
        return []
    specs = {
        "replan_closure": (
            "改一个阶段后，按依赖闭包算出还需要连带重做哪些阶段（只算，不执行）。",
            {"type": "object",
             "properties": {"stage": {"type": "string"},
                            "reason": {"type": "string"}},
             "required": ["stage"]},
        ),
        "validate_stage_plan": (
            "校验一份阶段计划：依赖顺序、必需输入、非法组合。返回问题清单。",
            {"type": "object",
             "properties": {"stages": {"type": "array", "items": {"type": "string"}}},
             "required": ["stages"]},
        ),
    }
    entries: list[SurfaceEntry] = []
    for name, (desc, params) in specs.items():
        text = _describe(name, "read", desc)
        entries.append(SurfaceEntry(
            name=name, tier="read", description=text,
            schema={"type": "function",
                    "function": {"name": name, "description": text,
                                 "parameters": params}},
            available=True, idempotent=True, dry_run=False,
            cost_hint=_COST_HINT["read"],
        ))
    return entries


def _agent_surface_entries(allowed: tuple[str, ...]) -> list[SurfaceEntry]:
    """派发/查询类 agent 工具进同一工具面（agent 就是工具）。"""
    entries: list[SurfaceEntry] = []
    for name in AGENT_TOOLS:
        tier = _AGENT_TOOL_TIER[name]
        if tier not in allowed:
            continue
        spec = _AGENT_TOOL_SPECS[name]
        desc = _describe(name, tier, spec["description"])
        entries.append(
            SurfaceEntry(
                name=name,
                tier=tier,
                description=desc,
                schema={
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": spec["parameters"],
                    },
                },
                available=True,
                idempotent=False,
                dry_run=False,
                cost_hint=_COST_HINT.get(tier, ""),
            )
        )
    return entries


def _skill_surface_entries(allowed: tuple[str, ...]) -> list[SurfaceEntry]:
    """把 Skill 也放进工具面（否则主 Agent **一个技能都调不了**）。

    之前 `build_surface()` 只遍历 matrix 里 kind=="tool" 的条目，于是 4 个内置技能
    （分镜分析/字幕生成/节奏分析/配音脚本）永远进不了 Kernel——`surface.execute`
    也会以"不在工具面内"拒绝。技能本身已实现 `to_llm_tool()` 与
    `SkillRegistry.execute()`，缺的只是这层接线。
    """
    from clipwright.skill import register_builtin_skills
    from clipwright.skill.registry import SkillRegistry

    if not SkillRegistry.list():
        register_builtin_skills()

    entries: list[SurfaceEntry] = []
    for info in SkillRegistry.list():
        if "write_local" not in allowed:
            break
        skill = SkillRegistry.get(info.name)
        if skill is None:
            continue
        try:
            schema = skill.to_llm_tool("openai")
        except Exception as e:  # noqa: BLE001
            raise SurfaceError(f"技能 {info.name} 的 schema 推断失败: {e}") from e
        function = schema.get("function") or {}
        base_desc = str(function.get("description") or "")
        function["description"] = _describe(info.name, "write_local", base_desc + "（组合技能）")
        entries.append(
            SurfaceEntry(
                name=str(info.name),
                tier="write_local",
                description=str(function["description"]),
                schema=schema,
                available=bool(info.available),
                idempotent=False,
                dry_run=False,
                cost_hint=_COST_HINT["write_local"],
            )
        )
    return entries


def schemas(*, tiers: Iterable[str] = SURFACE_TIERS, available_only: bool = True
            ) -> list[dict[str, Any]]:
    """给 LLM 的 tools 参数（openai 格式）。"""
    return [
        e.schema
        for e in build_surface(tiers=tiers)
        if e.available or not available_only
    ]


def surface_stats(*, tiers: Iterable[str] = SURFACE_TIERS) -> dict[str, Any]:
    """工具面覆盖度（A7 的判据就用这个）。"""
    from clipwright.services.capability_matrix import coverage

    entries = build_surface(tiers=tiers)
    stat = coverage()
    by_tier: dict[str, int] = {}
    for e in entries:
        by_tier[e.tier] = by_tier.get(e.tier, 0) + 1
    # 分母与分子必须同口径：都只算"允许直调档位里的**工具**"，
    # 否则把 Skill 算进分子会让覆盖率 >100%（这属于自欺欺人）。
    tool_pool = {
        e.name for e in build_matrix_cached()
        if e.kind == "tool" and e.tier in SURFACE_TIERS and e.available
    }
    covered = [e for e in entries if e.available and e.name in tool_pool]
    skills = [e for e in entries if _is_skill(e.name)]
    return {
        "total_capabilities": stat["total"],
        "surface_count": len(entries),
        "surface_available": len([e for e in entries if e.available]),
        "by_tier": by_tier,
        "pool": len(tool_pool),
        "coverage_ratio": round(len(covered) / len(tool_pool), 4) if tool_pool else 0.0,
        "skills_in_surface": len(skills),
        "skills_available": len([e for e in skills if e.available]),
        "dry_run_capable": sum(1 for e in entries if e.dry_run),
        # 结构预检对全部工具可用（参数/路径可验证），但它是 structural 而非 semantic
        "preflight_capable": len(entries),
        "dangerous_excluded": stat["dangerous"],
    }


def build_matrix_cached():  # noqa: ANN201 — 转发器，仅为可读性
    from clipwright.services.capability_matrix import build_matrix

    return build_matrix()


def _preflight(entry: SurfaceEntry, payload: dict[str, Any]) -> dict[str, Any]:
    """结构化预检（dry-run 的诚实版本）。

    只说**能验证的事**：必填参数齐不齐、输入路径在白名单内且存在、输出路径会写哪儿。
    不承诺"滤镜语义正确"——那种 dry-run 得真跑一遍才有意义，代价等于执行。
    所以返回里带 `fidelity: "structural"`，避免被当成"保证能成功"。
    """
    checks: list[dict[str, Any]] = []
    ok = True
    schema = entry.schema.get("function", {}).get("parameters", {}) or {}
    required = list(schema.get("required") or [])
    missing = [k for k in required if not str(payload.get(k) or "").strip()]
    if missing:
        ok = False
        checks.append({"check": "required_args", "ok": False, "detail": f"缺少必填参数: {missing}"})
    else:
        checks.append({"check": "required_args", "ok": True, "detail": f"必填参数齐全: {required}"})

    for key in ("input_path", "video_path", "audio_path", "file_path", "path"):
        value = str(payload.get(key) or "")
        if not value or value.startswith(("http://", "https://")):
            continue
        from pathlib import Path

        p = Path(value)
        if not p.exists():
            ok = False
            checks.append({"check": "input_exists", "ok": False, "detail": f"输入不存在: {value}"})
            continue
        try:
            from clipwright.security import assert_allowed_path

            assert_allowed_path(p)
            checks.append({"check": "input_allowlist", "ok": True, "detail": value})
        except Exception as e:  # noqa: BLE001 — 预检失败要如实说
            ok = False
            checks.append({"check": "input_allowlist", "ok": False, "detail": str(e)})

    would_write = str(payload.get("output_path") or "")
    if would_write:
        checks.append({"check": "output_path", "ok": True, "detail": would_write})
    return {
        "status": "dry_run",
        "dry_run": True,
        "fidelity": "structural",
        "entry_dry_run": entry.dry_run,
        "would_call": entry.name,
        "tier": entry.tier,
        "ok": ok,
        "checks": checks,
        "note": (
            "只做了结构预检：参数/路径可验证，滤镜与编码语义未经执行验证"
            if not entry.dry_run
            else "该工具声明了真实 dry-run，本次不产生副作用"
        ),
    }


def _cost_of(result: Any) -> Optional[float]:
    """工具自报成本（美元）；没报就是 None，**不要伪造 0**。

    工具的作者知道这次调用花了多少（例如视觉模型按次计费），由它在
    `ToolExecResult` 上挂 `cost_usd`，或把 `cost_usd` 放进 output。两者都没有时
    返回 None，让调用方把这次记成"成本未知"而不是 0。
    """
    direct = getattr(result, "cost_usd", None)
    if isinstance(direct, (int, float)):
        return round(float(direct), 6)
    output = getattr(result, "output", None)
    if isinstance(output, dict):
        value = output.get("cost_usd")
        if isinstance(value, (int, float)):
            return round(float(value), 6)
    return None


def _is_skill(name: str) -> bool:
    try:
        from clipwright.skill.registry import SkillRegistry

        return SkillRegistry.get(name) is not None
    except Exception:  # noqa: BLE001
        return False


async def _execute_skill(name: str, payload: dict[str, Any]) -> Any:
    """执行一个 Skill（失败也返回结构化结果，不抛给 Kernel）。"""
    from clipwright.schema.skill import SkillExecResult, SkillStatus
    from clipwright.skill.registry import SkillRegistry

    try:
        return await SkillRegistry.execute(name, **payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("Skill 执行异常 %s: %s", name, e)
        return SkillExecResult(status=SkillStatus.ERROR, skill_name=name,
                               error=f"{type(e).__name__}: {e}")


async def _execute_domain(name: str, payload: dict[str, Any], *,
                         pipeline_id: str, idem_key: str,
                         task: Any = None) -> dict[str, Any]:
    """确定性领域工具（闭包/校验）：走 stage_closure，不产生执行副作用。"""
    from clipwright.schema.task import PlanStep
    from clipwright.services import stage_closure

    if task is None and pipeline_id:
        try:
            from clipwright.services.agent_kernel import AgentKernel

            task = AgentKernel().get(pipeline_id)
        except Exception:  # noqa: BLE001 — 无上下文时 stage_closure 会如实拒绝
            task = None
    if task is None:
        return {"status": "refused", "error": f"{name} 需要任务上下文"}
    step = PlanStep(id=f"{name}", capability=name, args=payload)
    entry = next(e for e in build_surface() if e.name == name)
    started = time.monotonic()
    result = await stage_closure.run(task, step)
    status = str(result.get("status") or "error")
    result["record"] = _audit(name, entry, pipeline_id, idem_key, payload, status,
                              (time.monotonic() - started) * 1000,
                              result.get("error"))
    return result


async def _execute_agent(name: str, payload: dict[str, Any], *,
                         pipeline_id: str, idem_key: str,
                         task: Any = None) -> dict[str, Any]:
    """agent 类工具的统一定向：派发 / 查询 / 取消，全部返回 AgentResult 形状。"""
    from clipwright.services import agent_run

    if task is None and pipeline_id:
        try:
            from clipwright.services.agent_kernel import AgentKernel

            task = AgentKernel().get(pipeline_id)
        except Exception as exc:  # noqa: BLE001 — 反查失败按缺少上下文处理
            logger.warning("agent 工具反查任务失败 %s: %s", pipeline_id, exc)
    entry = next(e for e in build_surface() if e.name == name)

    if name == CANCEL_AGENT_RUN:
        # 兼容旧参数名：历史检查点里存的是 subagent_id
        run_id = str(payload.get("run_id") or payload.get("subagent_id") or "")
        ok, detail = agent_run.cancel(
            run_id, task_id=str(getattr(task, "id", "") or pipeline_id))
        out = {"status": "ok" if ok else "error",
               "output": detail, "error": None if ok else detail.get("error")}
        out["record"] = _audit(name, entry, pipeline_id, idem_key, payload,
                               out["status"], 0.0, out.get("error"))
        return out

    if name in (GET_AGENT_RUN, LIST_AGENT_RUNS):
        if name == LIST_AGENT_RUNS:
            runs = agent_run.list_runs(str(getattr(task, "id", "") or pipeline_id))
            try:
                from clipwright.services import task_blackboard as bb

                board = bb.describe(str(getattr(task, "id", "") or pipeline_id))
            except Exception:  # noqa: BLE001 — 黑板不可用不影响 run 列表
                board = {}
            out = {"status": "ok",
                   "output": {"count": len(runs), "runs": runs[:50],
                              # 兼容旧字段名（历史提示词/前端仍读这两个键）
                              "stages": runs[:50], "blackboard": board}}
        else:
            found = agent_run.inspect(
                run_id=str(payload.get("run_id") or payload.get("subagent_id") or ""),
                task_id=str(getattr(task, "id", "") or pipeline_id),
                stage=str(payload.get("stage") or ""),
            )
            if found is None:
                out = {"status": "error", "error": "没有找到对应的 run",
                       "failure": {"category": "not_found",
                                   "retryable": "needs_upstream",
                                   "blocking_point": "run_id/stage 未命中",
                                   "evidence": []}}
            else:
                # 兼容旧 `wait_for_stage` 语义：查一次即视作已收敛（同步派发本来就等完了）
                status = str(found.get("status") or "")
                out = {"status": "ok",
                       "output": {**found, "waited": True,
                                  "converged": status not in ("pending", "running")}}
        out["record"] = _audit(name, entry, pipeline_id, idem_key, payload,
                               out["status"], 0.0, out.get("error"))
        return out

    if task is None:
        return {"status": "refused",
                "error": f"{name} 需要任务上下文（task 或有效 pipeline_id），当前都没有"}

    from clipwright.schema.run import RunSpec

    spec = RunSpec(
        kind="stage" if name == DELEGATE_AGENT else "research",
        stage=str(payload.get("stage") or ""),
        role=str(payload.get("role") or ""),
        brief=str(payload.get("brief") or ""),
        inputs=dict(payload.get("inputs") or {}),
        expect=dict(payload.get("expect") or {}),
        tools=list(payload.get("tools") or []),
        depth=1,
        idem_key=idem_key[:120],
        trace_id=pipeline_id,
    )
    result = await agent_run.run(spec, task)
    # 语义要分清：ok/degraded/needs 是"跑完了"，refused 是"不许跑"，其余才是 error
    if result.status in ("ok", "degraded", "needs"):
        status = "ok"
    elif result.status == "refused":
        status = "refused"
    else:
        status = "error"
    out = {
        "status": status,
        "output": result.to_tool_payload(),
        "error": result.error or None,
        # 失败/拒绝要把结构化原因透出来（Kernel 与模型都靠它决定下一步）
        "failure": result.failure,
        "cost_usd": result.cost_usd,
        "cost_unknown": bool(result.cost_unknown),
        "replayed": False,
    }
    out["record"] = _audit(name, entry, pipeline_id, idem_key, payload, status,
                           float(result.duration_ms),
                           result.error or None, cost_usd=result.cost_usd)
    return out


async def execute(
    name: str,
    args: Optional[dict[str, Any]] = None,
    *,
    pipeline_id: str = "",
    idem_key: str = "",
    dry_run: bool = False,
    task: Any = None,
) -> dict[str, Any]:
    """执行一次 Kernel 工具调用（唯一入口）。

    返回统一形状：`{status, output?, error?, replayed?, dry_run?, record}`。
    `status` 取值：ok / error / refused / dry_run。

    派发类工具（`delegate_agent` / `research_agent`）也走这里：与原子工具同一个入口、
    同一份审计、同一个幂等键。它们需要 `Task` 上下文，`task` 未传时按 `pipeline_id`
    向 Kernel 反查——这样"模型调工具"和"代码调工具"仍是同一条路。
    """
    from clipwright.tool.registry import ToolRegistry

    payload = dict(args or {})
    # 工作区上下文注入（closeout B11 发现的真 bug）：
    # 工具面是唯一入口，但 Task 的工作区信息只存在 `request.extra` 里，
    # 模型并不知道 `workspace_root`/`project_id` 该填什么 → list_artifacts /
    # list_reports / write_file 全部报"未找到工作区"，模型只能把回合烧在 error 上。
    # 这里在**入口**补齐（只填 schema 里确实有、且调用方没给的键），不改变工具语义。
    if task is not None:
        extra_ctx = dict(getattr(getattr(task, "request", None), "extra", None) or {})
        provided = {
            "workspace_root": extra_ctx.get("workspace_root"),
            "project_id": extra_ctx.get("workspace_project_id") or extra_ctx.get("project_id"),
        }
        try:
            entry0 = next((e for e in build_surface() if e.name == name), None)
            props = ((entry0.schema.get("function", {}) if entry0 else {})
                     .get("parameters", {}) or {}).get("properties", {}) or {}
        except Exception:  # noqa: BLE001 — schema 拿不到就不注入
            props = {}
        for key, value in provided.items():
            if value and key in props and not str(payload.get(key) or ""):
                payload[key] = value
    if name in AGENT_TOOLS:
        return await _execute_agent(name, payload, pipeline_id=pipeline_id,
                                   idem_key=idem_key, task=task)
    if name in DOMAIN_TOOLS:
        return await _execute_domain(name, payload, pipeline_id=pipeline_id,
                                     idem_key=idem_key, task=task)

    surface = {e.name: e for e in build_surface()}
    entry = surface.get(name)
    if entry is None:
        # 可能是 dangerous 档，也可能根本不存在——两种都要给出可执行提示
        from clipwright.services.capability_matrix import TIER_MAP

        if TIER_MAP.get(name) == "dangerous":
            return {
                "status": "refused",
                "error": (
                    f"{name} 属于 dangerous 档，不能作为普通工具直调——"
                    "请经执行网关（POST /api/pipeline/{pid}/exec）发起，由风险判定与人工授权把关"
                ),
            }
        return {"status": "refused", "error": f"工具不在 Kernel 工具面内: {name}"}
    if not entry.available:
        return {"status": "refused", "error": f"工具依赖不满足，当前不可用: {name}"}

    if dry_run:
        return _preflight(entry, payload)

    key = idem_key.strip()
    if key and key in _IDEM_CACHE:
        cached = dict(_IDEM_CACHE[key])
        cached["replayed"] = True
        _audit(name, entry, pipeline_id, key, payload, cached.get("status", "ok"),
               0.0, cached.get("error"), replayed=True)
        return cached

    started = time.monotonic()
    try:
        if _is_skill(name):
            result = await _execute_skill(name, payload)
        else:
            result = await ToolRegistry.execute(name, **payload)
        duration_ms = (time.monotonic() - started) * 1000
        raw_status = getattr(result, "status", "ok")
        # 三种工具/技能的状态枚举序列化方式不完全一致（"success" / "ToolStatus.SUCCESS" /
        # 枚举对象），统一归一化，避免"成功被当成失败"这种最难发现的错。
        status = str(raw_status).lower().rsplit(".", 1)[-1]
        ok = status in ("success", "ok")
        cost = _cost_of(result)
        payload_out: dict[str, Any] = {
            "status": "ok" if ok else "error",
            "output": getattr(result, "output", None),
            "output_path": getattr(result, "output_path", None),
            "error": getattr(result, "error", None),
            "warning": getattr(result, "warning", None),
            "cost_usd": cost,
            # costly 档却报不出成本 → 标明"未知"，不假装是 0
            "cost_unknown": cost is None and entry.tier == "costly",
            "replayed": False,
        }
        # 技能内部的工具调用轨迹（主代理要能看出"这个技能实际干了什么"）
        tool_calls = getattr(result, "tool_calls", None)
        if tool_calls:
            payload_out["tool_calls"] = list(tool_calls)[:20]
    except Exception as e:  # noqa: BLE001 — 工具异常不能带崩 Kernel
        duration_ms = (time.monotonic() - started) * 1000
        payload_out = {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "replayed": False,
        }
        logger.warning("Kernel 工具调用异常 %s: %s", name, e)

    payload_out["record"] = _audit(
        name, entry, pipeline_id, key, payload, payload_out["status"],
        duration_ms, payload_out.get("error"),
        cost_usd=payload_out.get("cost_usd"),
    )
    if key:
        if len(_IDEM_CACHE) >= _MAX_CACHE:
            _IDEM_CACHE.pop(next(iter(_IDEM_CACHE)))
        _IDEM_CACHE[key] = dict(payload_out)
    return payload_out


def _audit(name: str, entry: SurfaceEntry, pipeline_id: str, idem_key: str,
           args: dict[str, Any], status: str, duration_ms: float,
           error: Optional[str], *, cost_usd: Optional[float] = None,
           replayed: bool = False) -> dict[str, Any]:
    import json

    brief = json.dumps(args, ensure_ascii=False, default=str)
    record = ToolCallRecord(
        name=name,
        tier=entry.tier,
        pipeline_id=pipeline_id,
        idem_key=idem_key,
        args_brief=brief[:300],
        status=status,
        duration_ms=round(duration_ms, 2),
        error=error,
        cost_usd=cost_usd,
        replayed=replayed,
    )
    _RECORDS.append(record)
    if len(_RECORDS) > _MAX_RECORDS:
        del _RECORDS[: len(_RECORDS) - _MAX_RECORDS]
    return record.to_public()
