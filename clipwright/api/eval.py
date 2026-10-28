"""评估报告 API（closeout B10 的可视化数据源）。

`GET /api/eval/reports`        列出 `_cache/eval` 下的报告（新→旧）
`GET /api/eval/reports/{name}` 读一份报告全文
`GET /api/eval/latest`         最近一份（面板首屏用）

为什么单独一个路由而不是塞进 `/api/pipeline`：评估报告属于**主 Agent 任务**，
跟老管线无关；放在 pipeline 下面会让人以为它是历史遗留物。

安全：只允许读报告目录内的文件（名字里带 `/`、`..`、绝对路径一律拒），
不允许把这里变成"任意文件读取"。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from clipwright.config import logger

router = APIRouter(prefix="/api/eval", tags=["eval"])

DEFAULT_DIR = Path("_cache") / "eval"


def _report_dir() -> Path:
    from clipwright.config import settings

    configured = getattr(settings, "eval_report_dir", "") or ""
    return Path(str(configured)) if str(configured).strip() else DEFAULT_DIR


def _safe_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw or "/" in raw or "\\" in raw or raw.startswith(".") or ".." in raw:
        raise HTTPException(status_code=400, detail=f"非法的报告名: {name}")
    if not raw.endswith(".json"):
        raw += ".json"
    return raw


def _summarize(path: Path) -> dict:
    """列表页只要摘要：选题、次数、成功率均值/极差、告警数。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 坏文件不让整个列表挂掉
        logger.warning("评估报告读取失败 %s: %s", path, exc)
        return {"name": path.name, "error": str(exc)[:200]}
    report = payload.get("report") or payload
    topic = report.get("topic") or payload.get("topic") or {}
    metrics = report.get("metrics") or {}
    sr = metrics.get("success_rate") or {}
    cost = metrics.get("cost_avg_usd") or {}
    return {
        "name": path.name,
        "topic_id": str(topic.get("id") or ""),
        "topic": str(topic.get("topic") or "")[:80],
        "runs": int(report.get("runs") or len(payload.get("runs") or []) or 0),
        "success_rate_mean": sr.get("mean"),
        "success_rate_spread": sr.get("spread"),
        "cost_avg_usd": cost.get("mean"),
        "warnings": len(report.get("warnings") or []),
        "mtime": path.stat().st_mtime,
    }


@router.get("/reports")
def list_reports(limit: int = Query(50, ge=1, le=200)) -> dict:
    d = _report_dir()
    if not d.is_dir():
        return {"dir": str(d), "count": 0, "items": []}
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    items = [_summarize(p) for p in files[:limit]]
    return {"dir": str(d), "count": len(items), "items": items}


@router.get("/latest")
def latest_report() -> dict:
    d = _report_dir()
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if d.is_dir() else []
    if not files:
        raise HTTPException(status_code=404, detail="还没有评估报告：先跑一次 golden_eval")
    return {"name": files[0].name,
            "payload": json.loads(files[0].read_text(encoding="utf-8"))}


@router.get("/reports/{name}")
def read_report(name: str) -> dict:
    safe = _safe_name(name)
    path = _report_dir() / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"报告不存在: {safe}")
    try:
        return {"name": safe, "payload": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"报告不是合法 JSON: {exc}") from exc


__all__ = ["DEFAULT_DIR", "router"]
