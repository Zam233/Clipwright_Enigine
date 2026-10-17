"""执行网关（PRD F11 / 开发计划 B9）。

流程（与 PRD 表一致）：

1. 硬底线（确定性）→ 读凭据 / 不可逆系统破坏：直接拒绝并记账；
2. 危险种子棘轮 → 给出最低等级（只升不降）；
3. 独立 LLM 判风险 → 失败 fail-safe 到 high；
4. `final = max(LLM, 棘轮)`；
5. 分流：low 直接执行；med 在自动模式下放行，否则等授权；
   high/critical 一律需要授权（critical 还需二次确认）；
6. 执行：`asyncio.create_subprocess_shell`，cwd=workspace，环境变量剥离密钥，
   墙钟超时，输出截断；
7. 全程入账（`ExecRecord`）并写 trace。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from clipwright.config import logger
from clipwright.supervisor import review as rv
from clipwright.supervisor.shell_policy import hard_floor, max_level, ratchet_level

_MAX_RECORDS = 500
_SECRET_HINTS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")


# ── 内存状态（按 pipeline）────────────────────────────────────

_RECORDS: dict[str, list[dict[str, Any]]] = {}
_PENDING: dict[str, dict[str, dict[str, Any]]] = {}
_AUTO: dict[str, bool] = {}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#: 待授权窗口有效期：过期后授权会被拒绝并记审计（防止「三天前的高危命令」被顺手点过）
_APPROVAL_TTL_SEC = 600.0


def _expiry_iso(ttl_sec: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)).isoformat()


def _expired(item: dict[str, Any]) -> bool:
    raw = str(item.get("expiresAt") or "")
    if not raw:
        return False
    try:
        return datetime.now(timezone.utc) > datetime.fromisoformat(raw)
    except Exception:  # noqa: BLE001 — 时间解析失败按「未过期」处理，避免误放行也避免误拒
        return False


def records(pipeline_id: str) -> list[dict[str, Any]]:
    return list(_RECORDS.get(pipeline_id) or [])


def pending_approvals(pipeline_id: str) -> list[dict[str, Any]]:
    return list((_PENDING.get(pipeline_id) or {}).values())


def auto_mode(pipeline_id: str) -> bool:
    return bool(_AUTO.get(pipeline_id))


def set_auto_mode(pipeline_id: str, enabled: bool) -> bool:
    _AUTO[pipeline_id] = bool(enabled)
    _trace(pipeline_id, "warning" if enabled else "info",
           f"自动模式{'开启' if enabled else '关闭'}")
    return _AUTO[pipeline_id]


def clear_gateway(pipeline_id: str = "") -> None:
    if pipeline_id:
        _RECORDS.pop(pipeline_id, None)
        _PENDING.pop(pipeline_id, None)
        _AUTO.pop(pipeline_id, None)
    else:
        _RECORDS.clear()
        _PENDING.clear()
        _AUTO.clear()


def _trace(pid: str, level: str, summary: str, detail: Any = None) -> None:
    try:
        from clipwright.services.trace import add_event

        add_event(pid, "supervisor", "exec", summary, detail)
    except Exception:  # noqa: BLE001
        logger.debug("exec 事件写入失败（忽略）")


def _record(pid: str, record: dict[str, Any]) -> dict[str, Any]:
    bucket = _RECORDS.setdefault(pid, [])
    bucket.append(record)
    if len(bucket) > _MAX_RECORDS:
        del bucket[: len(bucket) - _MAX_RECORDS]
    return record


def _sanitized_env(allow_network: bool = False) -> dict[str, str]:
    """剥离密钥类环境变量（PRD F11 沙盒要求）；禁网时再去掉代理变量。"""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(h in upper for hint in _SECRET_HINTS for h in (hint,)):
            continue
        if not allow_network and upper in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            continue
        env[key] = value
    return env


def _truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + f"…[截断 {len(text) - limit} 字符]"


async def _execute(pid: str, command: str, *, cwd: str, timeout_sec: float,
                   risk: str, reasons: list[str], kind: str,
                   allow_network: bool = False) -> dict[str, Any]:
    record = {
        "id": f"ex_{uuid4().hex[:8]}",
        "ts": _utcnow_iso(),
        "kind": kind,
        "command": command,
        "cwd": cwd,
        "riskLevel": risk,
        "riskReasons": reasons,
        "autoMode": auto_mode(pid),
        "exitCode": None,
        "durationMs": 0,
        "outputTail": "",
    }
    started = asyncio.get_event_loop().time()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd or None,
            env=_sanitized_env(allow_network),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=float(timeout_sec))
            record["exitCode"] = proc.returncode
            record["outputTail"] = _truncate((out or b"").decode("utf-8", "replace"))
        except asyncio.TimeoutError:
            proc.kill()
            record["outputTail"] = f"命令超时（>{timeout_sec}s），已终止"
            record["exitCode"] = -1
    except Exception as e:  # noqa: BLE001
        record["outputTail"] = f"执行失败: {type(e).__name__}: {e}"
        record["exitCode"] = -1
    record["durationMs"] = int((asyncio.get_event_loop().time() - started) * 1000)
    record["status"] = "done" if record.get("exitCode") == 0 else "failed"
    _record(pid, record)
    _trace(pid, "info", f"执行 {kind}: {command[:60]} → exit={record['exitCode']}", record)
    return record


async def run_command(
    pipeline_id: str,
    command: str,
    *,
    cwd: str = "",
    timeout_sec: float = 60.0,
    kind: str = "shell",
    timeout_override: Optional[float] = None,
    risk_text: Optional[str] = None,
) -> dict[str, Any]:
    """网关入口：判定 → 分流 → （必要时）等待授权 → 执行。

    `risk_text`：用 Python 脚本时把**代码正文**喂给判定器（命令里只有临时文件路径）。
    """
    from clipwright.config import settings as _settings

    cmd = str(command or "").strip()
    if not cmd:
        return {"status": "rejected", "reason": "命令为空"}
    allow_network = bool(getattr(_settings, "exec_allow_network", False))
    default_timeout = float(getattr(_settings, "exec_timeout_sec", 60.0) or 60.0)
    timeout_sec = timeout_sec or default_timeout
    judged_text = (risk_text or cmd)

    # 1) 硬底线（命令与代码正文都要查）
    #
    # **策略由用户拍板（2026-10-17）**：硬底线不再自己一票否决，而是与其他风险一道
    # 交给**独立 LLM** 判定；命中硬底线只做两件事——把等级钉在 `critical`（只升不降），
    # 并要求**两级人工确认**（授权窗口 + 再次确认窗口）。
    # 想回到「规则直接拒绝、不可授权」的旧语义：设置 `exec_hard_floor_never_authorizable=true`。
    floor = hard_floor(cmd) or hard_floor(judged_text)
    floor_locked = bool(floor) and bool(
        getattr(_settings, "exec_hard_floor_never_authorizable", False)
    )
    if floor and floor_locked:
        rec = _record(pipeline_id, {
            "id": f"ex_{uuid4().hex[:8]}",
            "ts": _utcnow_iso(), "kind": kind, "command": cmd, "cwd": cwd,
            "riskLevel": "critical", "riskReasons": [floor], "autoMode": auto_mode(pipeline_id),
            "exitCode": None, "durationMs": 0,
            "rejected": {"reason": f"硬底线（配置为不可授权）：{floor}"},
        })
        _trace(pipeline_id, "error", f"拒绝执行（硬底线，配置为不可授权）：{floor}", rec)
        return {"status": "rejected", "reason": floor, "record": rec}

    # 2) 棘轮 + 3) 独立判定（硬底线命中时仍要走这一步：让 LLM 给出它自己的理由）
    ratchet = ratchet_level(judged_text)
    assessment = await rv.assess(
        {"command": judged_text[:4000], "cwd": cwd, "workspace": "管线 workspace"},
        timeout=timeout_override,
    )
    reasons = list(assessment.get("reasons") or [])
    final = max_level(str(assessment.get("risk") or "high"), ratchet)
    if floor:
        # 规则只升不降：命中硬底线一律钉到 critical，并把它写进理由里（可审计）
        final = max_level(final, "critical")
        reasons.append(f"硬底线命中（{floor}）：无论如何都要两级确认")
    elif final != assessment.get("risk"):
        reasons.append(f"危险种子规则上调为 {final}")
    needs_double_confirm = final == "critical"

    # 5) 分流
    if final != "low":
        needs_confirm = rv.requires_confirm({"risk": final}) or needs_double_confirm
        if not (auto_mode(pipeline_id) and final == "med"):
            approval_id = f"ap_{uuid4().hex[:8]}"
            item = {
                "id": approval_id,
                "pipeline_id": pipeline_id,
                "command": cmd,
                "cwd": cwd,
                "kind": kind,
                "riskLevel": final,
                "riskReasons": reasons,
                "dangerExplanation": "；".join(reasons)[:300],
                "needsConfirm": needs_confirm,
                "needsDoubleConfirm": needs_double_confirm,
                "hardFloor": bool(floor),
                "requestedBy": "supervisor",
                # 授权窗口 10 分钟过期：一条高危命令挂三天后再点，风险判断早已失context
                "expiresAt": _expiry_iso(_APPROVAL_TTL_SEC),
            }
            _PENDING.setdefault(pipeline_id, {})[approval_id] = item
            _trace(pipeline_id, "warning", f"等待授权（{final}）：{cmd[:60]}", item)
            return {"status": "pending_approval", "approval": item}

    return await _execute(
        pipeline_id, cmd, cwd=cwd, timeout_sec=timeout_sec,
        risk=final, reasons=reasons, kind=kind, allow_network=allow_network,
    )


async def run_python(
    pipeline_id: str,
    code: str,
    *,
    cwd: str = "",
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    """执行 Python 脚本（E5）：落临时文件 → `python -I` 子进程 → 走同一网关。

    `-I` 隔离用户 site-packages；是否联网由 `exec_allow_network` 决定（默认禁）。
    脚本落盘位置：给了 `cwd` 就落在该工作目录下（便于脚本读取相对路径素材），
    否则落系统临时目录——**不要**落进程 cwd，那会把脚本写进仓库/安装目录。
    """
    import sys
    import tempfile

    body = str(code or "")
    if not body.strip():
        return {"status": "rejected", "reason": "脚本为空"}
    root = cwd or os.path.join(tempfile.gettempdir(), "clipwright-supervisor")
    base = os.path.join(root, ".supervisor", "scripts")
    try:
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, f"{uuid4().hex}.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    except Exception as e:  # noqa: BLE001
        return {"status": "rejected", "reason": f"脚本落盘失败: {e}"}

    cmd = f'"{sys.executable}" -I "{path}"'
    return await run_command(
        pipeline_id, cmd, cwd=cwd, timeout_sec=timeout_sec, kind="python",
        risk_text=body,
    )


async def approve(
    pipeline_id: str, approval_id: str, *,
    confirm_high_risk: bool = False,
    second_confirm: bool = False,
) -> dict[str, Any]:
    """用户授权一条待执行命令。

    两级确认的语义（critical / 命中硬底线时）：
    1. `confirm_high_risk=true` —— 第一级：你在授权窗口里点了「允许执行」；
    2. `second_confirm=true` —— 第二级：你再过一遍**再次确认窗口**（默认焦点仍在拒绝）。
    缺任何一级都放回队列并报错，不放行。
    """
    item = (_PENDING.get(pipeline_id) or {}).get(approval_id)
    if item is None:
        raise KeyError(f"待授权记录不存在: {approval_id}")
    if _expired(item):
        (_PENDING.get(pipeline_id) or {}).pop(approval_id, None)
        rec = _record(pipeline_id, {
            "id": f"ex_{uuid4().hex[:8]}",
            "ts": _utcnow_iso(), "kind": str(item.get("kind") or "shell"),
            "command": item.get("command", ""), "cwd": item.get("cwd", ""),
            "riskLevel": item.get("riskLevel", "high"),
            "riskReasons": item.get("riskReasons") or [],
            "autoMode": auto_mode(pipeline_id), "exitCode": None, "durationMs": 0,
            "rejected": {"reason": "授权窗口已过期（超过 10 分钟）"},
        })
        _trace(pipeline_id, "warning", "授权窗口已过期，命令未执行", rec)
        raise ValueError("授权窗口已过期（>10 分钟），请重新发起执行")

    if item.get("needsConfirm") and not confirm_high_risk:
        raise ValueError("风险等级 high/critical，需显式二次确认（confirm_high_risk=true）")
    if item.get("needsDoubleConfirm") and not second_confirm:
        raise ValueError(
            "该命令被判定为极高风险（含不可逆/凭据类），需在再次确认窗口里再确认一次"
            "（second_confirm=true）"
        )

    (_PENDING.get(pipeline_id) or {}).pop(approval_id, None)
    return await _execute(
        pipeline_id, item["command"], cwd=item.get("cwd", ""),
        timeout_sec=60.0, risk=str(item.get("riskLevel") or "high"),
        reasons=list(item.get("riskReasons") or []), kind=str(item.get("kind") or "shell"),
    )


def reject(pipeline_id: str, approval_id: str) -> dict[str, Any]:
    item = (_PENDING.get(pipeline_id) or {}).pop(approval_id, None)
    if item is None:
        raise KeyError(f"待授权记录不存在: {approval_id}")
    rec = _record(pipeline_id, {
        "id": f"ex_{uuid4().hex[:8]}",
        "ts": _utcnow_iso(), "kind": str(item.get("kind") or "shell"),
        "command": item.get("command", ""), "cwd": item.get("cwd", ""),
        "riskLevel": item.get("riskLevel", "high"), "riskReasons": item.get("riskReasons") or [],
        "autoMode": auto_mode(pipeline_id), "exitCode": None, "durationMs": 0,
        "rejected": {"reason": "用户拒绝授权"},
    })
    _trace(pipeline_id, "warning", "用户拒绝执行授权")
    return {"status": "rejected", "record": rec}
