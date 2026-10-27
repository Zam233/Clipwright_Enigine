"""测试用驱动器桩（M3 之后 Kernel 唯一的注入点）。

M3 删掉了计划表执行（`planner`/`executor`/`_run_step`），Kernel 的注入点变成
**驱动器**：`(task, *, context_text, shadow, guard, on_tool_call) -> outcome`。
这些桩让测试仍然能表达"模型调了哪些工具、结果如何、闸在哪停下"，
而不是重新造一套计划表玩具。

约定：
- `on_tool_call(name, result, args)` —— 三参；Kernel 的记账/录制/digest 都走它；
- 返回值必须含 `status`；`ok` 时给 `finish.conclusion`、`rounds`；
- `guard()` 返回非空字符串即视为触闸，桩要如实返回 `aborted`（真实 `run_loop` 也这样）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

OK_OUTPUT = {"duration_sec": 12.5, "frames": 300, "path": "a.mp4"}


class ScriptedDriver:
    """按脚本调用若干工具，每次给固定成功结果。"""

    def __init__(self, calls: Optional[list[tuple[str, dict[str, Any]]]] = None,
                 *, output: Optional[dict[str, Any]] = None,
                 conclusion: str = "已完成") -> None:
        self.calls = calls if calls is not None else [("media_probe", {"input_path": "a.mp4"})]
        self.output = dict(output if output is not None else OK_OUTPUT)
        self.conclusion = conclusion
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001, ARG002
                       guard: Optional[Callable[[], str]] = None,
                       on_tool_call: Optional[Callable[..., None]] = None) -> dict[str, Any]:
        for name, args in self.calls:
            if guard is not None:
                hit = guard()
                if hit:
                    return {"status": "aborted", "stopped_reason": str(hit),
                            "tool_calls": [{"tool": c[0]} for c in self.executed]}
            result = {"status": "ok", "output": dict(self.output)}
            self.executed.append((name, args))
            if on_tool_call is not None:
                on_tool_call(name, result, args)
        return {"status": "ok", "finish": {"conclusion": self.conclusion},
                "tool_calls": [{"tool": c[0]} for c in self.executed],
                "rounds": len(self.executed) + 1, "stopped_reason": "model_final"}


class ResultDriver:
    """按给定结果序列回放（用于失败/等审核/重试等场景）。"""

    def __init__(self, results: list[dict[str, Any]], *,
                 tool: str = "delegate_agent") -> None:
        self.results = list(results)
        self.tool = tool
        self.executed: list[dict[str, Any]] = []

    async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001, ARG002
                       guard: Optional[Callable[[], str]] = None,
                       on_tool_call: Optional[Callable[..., None]] = None) -> dict[str, Any]:
        for result in self.results:
            if guard is not None:
                hit = guard()
                if hit:
                    return {"status": "aborted", "stopped_reason": str(hit),
                            "tool_calls": [{"tool": self.tool}]}
            self.executed.append(result)
            if on_tool_call is not None:
                on_tool_call(self.tool, result, {})
            status = str(result.get("status") or "")
            if status in ("pending_approval", "needs_human_review"):
                return {"status": "aborted", "stopped_reason": "pause",
                        "tool_calls": [{"tool": self.tool}]}
        last = self.results[-1] if self.results else {}
        if str(last.get("status") or "") not in ("ok", ""):
            return {"status": "error", "error": str(last.get("error") or "工具失败"),
                    "stopped_reason": "tool_failed",
                    "failure": last.get("failure") or {"category": "tool_failed",
                                                       "retryable": "permanent",
                                                       "blocking_point": "", "evidence": []},
                    "tool_calls": [{"tool": self.tool}]}
        return {"status": "ok", "finish": {"conclusion": "完成"},
                "tool_calls": [{"tool": self.tool}], "rounds": 2,
                "stopped_reason": "model_final"}


class NoopDriver:
    """不调用任何工具（归属校验等用例只需要 Kernel 能跑起来）。"""

    async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001, ARG002
                       guard=None, on_tool_call=None) -> dict[str, Any]:  # noqa: ANN001
        return {"status": "ok", "finish": {"conclusion": "noop"},
                "tool_calls": [], "rounds": 1, "stopped_reason": "model_final"}


def kernel(tmp_path, driver=None, *, mode: str = "tool_loop"):
    """构造一个注入了驱动器的 Kernel（临时检查点目录）。"""
    from clipwright.services import agent_kernel as ak

    return ak.AgentKernel(ak.KernelDeps(
        driver=driver or NoopDriver(),
        checkpoint_dir=tmp_path / "tasks",
        kernel_mode=mode,
    ))


__all__ = ["NoopDriver", "OK_OUTPUT", "ResultDriver", "ScriptedDriver", "kernel"]
