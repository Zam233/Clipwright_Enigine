"""工作区轮询扫描器（PRD §5.1）。

为什么用轮询而不是文件系统 watch：不引新依赖（watchdog 在 Windows 网络盘上不可靠），
20s 延迟对"用户往 materials/ 丢素材"这个场景完全够用；扫描只在独立线程里 stat，
不阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from clipwright.config import logger, settings
from clipwright.services.workspace import Workspace, workspace_root

_task: asyncio.Task | None = None
_stop = False


def _known_projects() -> list[str]:
    """扫工作区根目录，收集所有已建工作区的 project_id。"""
    out: list[str] = []
    root = workspace_root()
    for meta_file in root.glob("*/.workspace.json"):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏元数据由 Workspace 自己兜底
            continue
        pid = str(data.get("project_id") or "")
        if pid:
            out.append(pid)
    return out


async def scan_once() -> dict[str, Any]:
    """扫一遍所有开启自动扫描的工作区（返回每个工作区的报告）。"""
    from clipwright.services.workspace_index import scan_and_index

    results: dict[str, Any] = {}
    for pid in _known_projects():
        ws = Workspace(pid)
        meta = ws.meta or {}
        if not bool((meta.get("scan") or {}).get("auto", True)):
            continue
        try:
            report = await scan_and_index(ws)
            if report.get("new") or report.get("deduped"):
                results[pid] = report
        except Exception as e:  # noqa: BLE001 — 单个工作区失败不影响其它
            logger.warning("工作区扫描失败 %s: %s", pid, e)
            results[pid] = {"error": str(e)[:200]}
    return results


async def _loop(interval: float) -> None:
    # 启动后先等 3s（让 app 完全起来），再按间隔轮询
    await asyncio.sleep(3)
    tick = 0
    while not _stop:
        try:
            await scan_once()
            tick += 1
            # 每 ~10 分钟按上限清一次 cache/（PRD §4.1：可清空、不参与备份校验）
            if tick % max(1, int(600 / max(5.0, interval))) == 0:
                for pid in _known_projects():
                    try:
                        ws = Workspace(pid)
                        ws.cleanup_cache()
                        # B11：logs/ 也要按 logs_keep_days 轮转，否则只进不出
                        ws.cleanup_logs()
                    except Exception:  # noqa: BLE001 — 单个工作区清理失败不影响轮询
                        continue
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("工作区轮询异常（继续）: %s", e)
        await asyncio.sleep(max(5.0, interval))


def start() -> asyncio.Task | None:
    """启动轮询（幂等）。关闭 ``workspace_scan_enabled`` 时不启动。"""
    global _task, _stop
    if not bool(getattr(settings, "workspace_scan_enabled", True)):
        return None
    if _task is not None and not _task.done():
        return _task
    _stop = False
    interval = float(getattr(settings, "workspace_scan_interval_sec", 20) or 20)
    _task = asyncio.create_task(_loop(interval))
    logger.info("工作区扫描器已启动（间隔 %.0fs）", interval)
    return _task


def stop() -> None:
    global _task, _stop
    _stop = True
    if _task is not None and not _task.done():
        _task.cancel()
    _task = None


__all__ = ["scan_once", "start", "stop"]
