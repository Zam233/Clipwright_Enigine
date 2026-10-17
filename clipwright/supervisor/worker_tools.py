"""临时 worker 的工具集（PRD F10 ∩ F11 / 开发计划 B8 ∩ B9）。

这里解决一个容易糊弄过去的问题：**「工具子集」不能只是规格单上的字符串**。
worker 真的拿着 `run_shell` 时，必须有东西把模型发出的 tool_call 接住并送到
执行网关；否则「带执行权限的 worker 要过两级审核门」审的是一个不存在的权限。

两条纪律：

1. **执行类工具一律走 `supervisor.gateway`**——判定、授权、沙盒、审计一个都不能绕。
   worker 自己不做风险判断，也不持有子进程。
2. **派生类工具对 worker 直接拒绝**（星型拓扑，PRD A19）：worker 不能派生 worker，
   也不能取消兄弟节点（取消权在编排层/用户手里）。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Iterable

from clipwright.config import logger

from .worker_registry import EXEC_TOOLS, SUPERVISOR_TOOLS

#: 工具名 → LLM tool schema（openai 格式，provider 层统一转换；
#: 与 `StructureAgent` 的既有用法保持一致，不引入第二种格式）
_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "run_shell": {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "执行一条 shell 命令（Windows 走 pwsh/cmd，POSIX 走 sh）。"
                "会先经安全判定：低风险直接执行，中高风险等待用户授权，"
                "命中硬底线（读凭据/不可逆破坏）直接拒绝。返回退出码与输出尾部。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "命令原文"},
                    "cwd": {"type": "string", "description": "工作目录（留空用管线默认目录）"},
                },
                "required": ["command"],
            },
        },
    },
    "run_python": {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "执行一段 Python 脚本（子进程，隔离用户 site-packages，默认禁网）。"
                "同样经过安全判定与授权门。适合作业：读文件、算指标、跑 ffprobe 之类的验证。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "脚本正文"},
                    "cwd": {"type": "string", "description": "工作目录（留空用管线默认目录）"},
                },
                "required": ["code"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "只读地读取一个文本文件（最多返回 4000 字符）。不执行任何东西。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "max_chars": {"type": "integer", "description": "最多返回多少字符"},
                },
                "required": ["path"],
            },
        },
    },
    "list_dir": {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "只读地列出一个目录的条目（最多 200 项）。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "目录路径"}},
                "required": ["path"],
            },
        },
    },
    "get_subagent_status": {
        "type": "function",
        "function": {
            "name": "get_subagent_status",
            "description": "只读地查询本管线某个子代理的状态与最近输出。",
            "parameters": {
                "type": "object",
                "properties": {"subagent_id": {"type": "string"}},
                "required": ["subagent_id"],
            },
        },
    },
    "list_subagents": {
        "type": "function",
        "function": {
            "name": "list_subagents",
            "description": "只读地列出本管线的全部子代理（扁平列表）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
}


def schemas_for(tools: Iterable[str]) -> list[dict[str, Any]]:
    """把工具名列表翻译成 LLM tool schema 列表（未定义的工具忽略）。"""
    out: list[dict[str, Any]] = []
    for name in tools:
        schema = _TOOL_SCHEMAS.get(str(name))
        if schema is not None:
            out.append(schema)
    return out


def _error(message: str) -> dict[str, Any]:
    return {"error": message}


def _read_file(path: str, max_chars: int = 4000) -> dict[str, Any]:
    p = str(path or "").strip()
    if not p:
        return _error("path 为空")
    try:
        with open(os.path.expanduser(p), "r", encoding="utf-8", errors="replace") as fh:
            return {"path": p, "content": fh.read()[: int(max_chars or 4000)]}
    except Exception as e:  # noqa: BLE001 — 读失败如实回报给模型
        return _error(f"读取失败: {type(e).__name__}: {e}")


def _list_dir(path: str) -> dict[str, Any]:
    p = str(path or "").strip()
    if not p:
        return _error("path 为空")
    try:
        entries = sorted(os.listdir(os.path.expanduser(p)))[:200]
        return {"path": p, "entries": entries}
    except Exception as e:  # noqa: BLE001
        return _error(f"列目录失败: {type(e).__name__}: {e}")


def build_executor(
    pipeline_id: str,
    subagent_id: str = "",
    *,
    allowed: Iterable[str] = (),
    cwd: str = "",
) -> Callable[[str, dict[str, Any]], Any]:
    """为单个 worker 构造工具执行器（异步）。

    `allowed` 是**该 worker 被授予**的工具名；执行器再校验一次，
    防止模型凭空调用没授权的工具（构造期校验 + 运行期校验双保险）。
    """
    allow = {str(x) for x in allowed}

    async def executor(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        tool = str(name or "")
        args = tool_input if isinstance(tool_input, dict) else {}

        if tool not in SUPERVISOR_TOOLS:
            return _error(f"未知工具: {tool}")
        if tool not in allow:
            return _error(f"工具不在该 worker 的授权范围内: {tool}")
        if tool in ("spawn_subagent", "cancel_subagent"):
            # 星型拓扑：worker 不得派生/取消子代理（PRD A19）
            return _error("子代理不得再派生或取消子代理（星型拓扑，深度固定 1 层）")

        if tool == "read_file":
            return _read_file(str(args.get("path", "")), int(args.get("max_chars") or 4000))
        if tool == "list_dir":
            return _list_dir(str(args.get("path", "")))
        if tool == "list_subagents":
            from .tracker import get_tracker

            nodes = get_tracker(pipeline_id).list_nodes()
            return {
                "count": len(nodes),
                "subagents": [
                    {"id": n.id, "role": n.role, "status": n.status, "last_output": n.last_output}
                    for n in nodes
                ],
            }
        if tool == "get_subagent_status":
            from .tracker import get_tracker

            node = get_tracker(pipeline_id).get(str(args.get("subagent_id", "")))
            if node is None:
                return _error("子代理不存在")
            return {"id": node.id, "role": node.role, "status": node.status,
                    "last_output": node.last_output, "duration_ms": node.duration_ms}

        # 执行类：全部经网关（判定 → 授权 → 沙盒 → 审计）
        from . import gateway as gw

        target_cwd = str(args.get("cwd") or cwd or "")
        try:
            if tool == "run_python":
                out = await gw.run_python(
                    pipeline_id, str(args.get("code", "")), cwd=target_cwd,
                )
            else:
                out = await gw.run_command(
                    pipeline_id, str(args.get("command", "")), cwd=target_cwd,
                )
        except Exception as e:  # noqa: BLE001 — 网关异常也要如实回报，不静默
            logger.warning("worker 执行工具失败 %s: %s", tool, e)
            return _error(f"执行失败: {type(e).__name__}: {e}")

        status = str(out.get("status", ""))
        if status == "pending_approval":
            # 关键语义：**没执行**。必须让模型知道它现在只有「等」，不能假装拿到了结果
            return {
                "status": "pending_approval",
                "note": "该命令需要用户授权，尚未执行。请先用只读手段继续，或在结论里说明等待授权。",
            }
        if status == "rejected":
            return {"status": "rejected", "reason": str(out.get("reason", ""))}
        return {
            "status": status or "done",
            "exitCode": out.get("exitCode"),
            "outputTail": str(out.get("outputTail", ""))[:4000],
        }

    return executor


def available_schemas() -> dict[str, Any]:
    """运维/前端用：哪些工具能真正执行（其余只是允许名单）。"""
    return {name: ("exec" if name in EXEC_TOOLS else "read") for name in sorted(_TOOL_SCHEMAS)}
