"""执行能力桥（路线 C：主 Agent 的 pwsh/bash / python 通道）。

**为什么必须有这一层**：C1 把 `run_shell` / `run_python` 判成 dangerous、排除在工具面外；
于是主 agent 实际上"不能跑命令"——它只能调媒体工具。用户要求的"能跑 python、能跑 shell"
就落空了。正确做法不是把它们塞进工具面（那会绕过授权），而是**把它接到执行网关上**：

    Kernel Step(run_shell) → 本模块 → supervisor.gateway.run_command
        → 硬底线标记 → 棘轮 → 独立 LLM 判定 → 分流
        → low: 直接执行并回结果
        → med/high/critical: 进待授权队列，任务**暂停**等人工点过

三条语义细节：
1. `pending_approval` **不是失败**：Step 保持未完成、任务转 paused，用户授权后 `resume` 继续；
2. 授权后由网关执行，本模块不缓存也不重试（避免"授权一次跑两次"）；
3. 未授权就 resume → 网关会再判一次并再次进队列（幂等，不会偷偷执行）。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.task import PlanStep, Task


def enabled() -> bool:
    from clipwright.config import settings

    return str(getattr(settings, "agent_mode", "off") or "off").lower() == "on"


async def run(task: Task, step: PlanStep) -> dict[str, Any]:
    """执行一步 shell / python（经网关）。"""
    if not enabled():
        return {
            "status": "refused",
            "error": "agent_mode != on：执行能力仅在主 Agent 模式下可用",
        }

    from clipwright.supervisor import gateway as gw

    args = dict(step.args or {})
    cwd = str(args.get("cwd") or "")
    timeout = float(args.get("timeout_sec") or 0.0)

    try:
        if step.capability == "run_python":
            code = str(args.get("code") or "")
            if not code.strip():
                return {"status": "refused", "error": "run_python 需要 code"}
            out = await gw.run_python(task.id, code, cwd=cwd, timeout_sec=timeout or 60.0)
        else:
            command = str(args.get("command") or "")
            if not command.strip():
                return {"status": "refused", "error": "run_shell 需要 command"}
            out = await gw.run_command(task.id, command, cwd=cwd, timeout_sec=timeout or 60.0)
    except Exception as e:  # noqa: BLE001 — 网关异常转成 Step 失败，不炸 Kernel
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    status = str(out.get("status") or "")
    if status == "pending_approval":
        approval = out.get("approval") or {}
        return {
            "status": "pending_approval",
            "approval_id": approval.get("id"),
            "risk": approval.get("riskLevel"),
            "reasons": approval.get("riskReasons") or [],
            "command": approval.get("command") or args.get("command") or args.get("code", "")[:80],
            "note": "命令被判定需要人工授权，任务已暂停；授权后 resume 会继续",
        }
    if status == "rejected":
        return {"status": "refused", "error": str(out.get("reason") or "被安全策略拒绝")}

    exit_code = out.get("exitCode")
    ok = exit_code == 0
    return {
        "status": "ok" if ok else "error",
        "output": {
            "exit_code": exit_code,
            "output_tail": str(out.get("outputTail") or "")[:2000],
            "duration_ms": out.get("durationMs"),
        },
        "error": None if ok else f"退出码 {exit_code}",
    }
