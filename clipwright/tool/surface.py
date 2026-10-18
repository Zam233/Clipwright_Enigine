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
    return sorted(out, key=lambda e: (e.tier, e.name))


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
    # 分母取"允许直调的档位中的可用工具"，不把 dangerous 算进覆盖率（它本就不该直调）
    pool = [
        e for e in build_matrix_cached()
        if e.kind == "tool" and e.tier in SURFACE_TIERS and e.available
    ]
    covered = [e for e in entries if e.available]
    return {
        "total_capabilities": stat["total"],
        "surface_count": len(entries),
        "surface_available": len(covered),
        "by_tier": by_tier,
        "pool": len(pool),
        "coverage_ratio": round(len(covered) / len(pool), 4) if pool else 0.0,
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


async def execute(
    name: str,
    args: Optional[dict[str, Any]] = None,
    *,
    pipeline_id: str = "",
    idem_key: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """执行一次 Kernel 工具调用（唯一入口）。

    返回统一形状：`{status, output?, error?, replayed?, dry_run?, record}`。
    `status` 取值：ok / error / refused / dry_run。
    """
    from clipwright.tool.registry import ToolRegistry

    payload = dict(args or {})
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
        result = await ToolRegistry.execute(name, **payload)
        duration_ms = (time.monotonic() - started) * 1000
        status = str(getattr(result, "status", "ok") or "ok")
        # ToolStatus 是 str 枚举（success/error/...），统一成 ok/error 便于前端判断
        ok = status in ("success", "ok", "ToolStatus.SUCCESS")
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
