"""录制与回放（路线 C / C0 交付物，F9 的地基）。

为什么先做这个：路线 C 放弃了「同输入 → 逐字节同输出」这条旧保障（v1.5 的 A10）。
替代品只有两个：**录制回放**（让一次真实运行可以离线重演）与 **golden runs**（让质量
变化可被看见）。没有它们，C 之后就只剩"感觉没变差"。

设计要点（C0 只做骨架，接线在 C2）：

- **键 = 调用语义指纹**：`sha256(kind, name, canonical_json(args))`。参数按 key 排序、
  浮点归一，避免 dict 顺序不同导致同一调用录两次。
- **只追加**：录制文件是 JSONL，一行一条，便于 diff 与人工核对。
- **回放缺失必须响**：`RecordingMiss` 直接抛，**不允许**静默去调真实模型/工具——
  否则"回放"会变成"偷偷又跑了一遍真实调用"，账单和不确定性都回来了。
- **可重放标记**：矩阵里 `idempotent=False` 的能力在回放时只允许"取录制结果"，
  不触发真实副作用（C2 接线时按这个规则分发）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

#: 录制条目类型（与 PRD F4 事件流同构，便于后续直接喂进事件流）
RECORD_KINDS: tuple[str, ...] = ("llm", "tool", "skill", "pipeline", "subagent")


class RecordingMiss(LookupError):
    """回放时找不到对应录制条目（不许静默放过）。"""


class RecordingError(RuntimeError):
    """录制文件损坏/格式不符。"""


def canonical_json(payload: Any) -> str:
    """稳定序列化：key 排序、紧凑分隔符、浮点归一到 10 位有效数字。"""

    def _norm(value: Any) -> Any:
        if isinstance(value, float):
            # 0.1 + 0.2 这类噪声不该让同一调用录成两条
            return float(f"{value:.10g}")
        if isinstance(value, dict):
            return {str(k): _norm(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
        if isinstance(value, (list, tuple)):
            return [_norm(v) for v in value]
        return value

    return json.dumps(_norm(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def fingerprint(kind: str, name: str, args: Any) -> str:
    """一次调用的语义指纹（录制键 / 幂等键共用同一套推导）。"""
    if kind not in RECORD_KINDS:
        raise ValueError(f"未知录制类别: {kind}（可选 {RECORD_KINDS}）")
    payload = f"{kind}\x00{name}\x00{canonical_json(args)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RecordedCall:
    kind: str
    name: str
    args: Any
    result: Any
    error: Optional[str] = None
    meta: Optional[dict[str, Any]] = None

    @property
    def key(self) -> str:
        return fingerprint(self.kind, self.name, self.args)

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "name": self.name,
            "args": self.args,
            "result": self.result,
            "error": self.error,
            "meta": self.meta or {},
        }


class Recorder:
    """把一次真实运行的调用记进 JSONL（追加写，天然可 diff）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def record(
        self,
        kind: str,
        name: str,
        args: Any,
        result: Any = None,
        *,
        error: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> str:
        call = RecordedCall(kind=kind, name=name, args=args, result=result,
                            error=error, meta=meta or {})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(call.to_json(), ensure_ascii=False) + "\n")
        self._count += 1
        return call.key


class Replayer:
    """按指纹回放；缺失即抛，绝不偷偷调真实实现。"""

    def __init__(self, path: str | Path, *, allow_real: bool = False) -> None:
        self.path = Path(path)
        #: C0 默认 False。允许真跑的模式只在开发调试时手动打开，且要显式告知用户
        self.allow_real = allow_real
        self._entries: dict[str, RecordedCall] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise RecordingError(f"{self.path}:{lineno} 不是合法 JSON: {e}") from e
            for field in ("kind", "name"):
                if field not in raw:
                    raise RecordingError(f"{self.path}:{lineno} 缺少字段 {field}")
            call = RecordedCall(
                kind=str(raw["kind"]),
                name=str(raw["name"]),
                args=raw.get("args"),
                result=raw.get("result"),
                error=raw.get("error"),
                meta=raw.get("meta") or {},
            )
            self._entries[call.key] = call

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def names(self) -> list[str]:
        return sorted({e.name for e in self._entries.values()})

    def get(self, kind: str, name: str, args: Any) -> RecordedCall:
        key = fingerprint(kind, name, args)
        entry = self._entries.get(key)
        if entry is None:
            if self.allow_real:
                raise RecordingMiss(
                    f"录制里没有 {kind}:{name} 这次调用，且 allow_real 模式尚未接真实实现"
                )
            raise RecordingMiss(
                f"录制里没有 {kind}:{name} 这次调用（key={key[:12]}）——"
                "回放不允许偷偷走真实模型/工具"
            )
        return entry

    def result_of(self, kind: str, name: str, args: Any) -> Any:
        entry = self.get(kind, name, args)
        if entry.error:
            raise RecordingError(f"录制里这次调用是失败的：{entry.error}")
        return entry.result


def replayable_names(path: str | Path) -> list[str]:
    """录制里出现过哪些能力（用于校验 golden run 覆盖到了哪些工具）。"""
    return Replayer(path).names


def summarize(path: str | Path) -> dict[str, Any]:
    """录制摘要：条数 / 覆盖能力 / 按类别计数（C6 比对报告用）。"""
    rp = Replayer(path)
    by_kind: dict[str, int] = {}
    for entry in rp._entries.values():  # noqa: SLF001 — 本模块内部聚合
        by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1
    return {
        "path": str(path),
        "calls": len(rp),
        "by_kind": by_kind,
        "capabilities": rp.names,
    }


def merge_recordings(paths: Iterable[str | Path], out: str | Path) -> int:
    """把多份录制合并成一份（同 key 保留第一条，便于按选题分别录完再汇总）。"""
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    written = 0
    with target.open("w", encoding="utf-8") as fh:
        for p in paths:
            rp = Replayer(p)
            for entry in rp._entries.values():  # noqa: SLF001
                if entry.key in seen:
                    continue
                seen.add(entry.key)
                fh.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")
                written += 1
    return written


# ── 录制作用域（Kernel 用）────────────────────────────────────

from contextvars import ContextVar, Token  # noqa: E402 — 与上方纯函数分区

_ACTIVE: ContextVar[Optional[Recorder]] = ContextVar("clipwright_recorder", default=None)


class recording_scope:
    """`with recording_scope(Recorder(path)):` —— 让 Kernel 自动记录调用。

    用 ContextVar 而不是全局单例：并发跑多条任务时不能互相串录制。
    """

    def __init__(self, recorder: Optional[Recorder]) -> None:
        self.recorder = recorder
        self._token: Optional[Token] = None

    def __enter__(self) -> Optional[Recorder]:
        self._token = _ACTIVE.set(self.recorder)
        return self.recorder

    def __exit__(self, *exc: Any) -> None:
        if self._token is not None:
            _ACTIVE.reset(self._token)


def current_recorder() -> Optional[Recorder]:
    return _ACTIVE.get()


def maybe_record(kind: str, name: str, args: Any, result: Any = None,
                 *, error: Optional[str] = None) -> None:
    """有录制作用域时记一条；没有就什么都不做（生产默认路径零开销）。"""
    recorder = current_recorder()
    if recorder is None:
        return
    try:
        recorder.record(kind, name, args, result, error=error)
    except Exception:  # noqa: BLE001 — 录制失败绝不能影响主流程
        pass


def make_replay_planner(replayer: Replayer):  # noqa: ANN201 — 返回协议对象
    """从录制构造一个"假规划器"（A2 回放用，不联网）。"""
    from clipwright.schema.task import Plan  # 延迟导入避免循环

    async def plan(task):  # noqa: ANN001, ANN202
        payload = replayer.result_of("llm", "plan", {"topic": task.request.topic})
        return Plan.model_validate(payload)

    async def reflect(task, step, outcome):  # noqa: ANN001, ANN202
        return replayer.result_of(
            "llm", "reflect", {"step_id": step.id, "status": step.status},
        )

    class _ReplayPlanner:
        pass

    planner = _ReplayPlanner()
    planner.plan = plan          # type: ignore[attr-defined]
    planner.reflect = reflect    # type: ignore[attr-defined]
    return planner


def make_replay_executor(replayer: Replayer):  # noqa: ANN201
    """从录制构造执行器：同一 Step 直接取录制结果，绝不真跑工具。"""

    async def executor(task, step):  # noqa: ANN001, ANN202
        return replayer.result_of(
            "tool", step.capability, {"step_id": step.id, "args": step.args},
        )

    return executor
