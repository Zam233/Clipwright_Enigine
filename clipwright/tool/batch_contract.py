"""WP0：把各批量工具已有的 items 归一成 BatchResult 附带契约。"""

from __future__ import annotations

from typing import Any

from clipwright.schema.tool import BatchItem, BatchResult, JobRef


def attach_batch_contract(output: dict[str, Any], *, job_id: str = "") -> dict[str, Any]:
    """给工具 output 增加 `batch` 字段；不改变既有 items/summary 兼容形状。"""
    raw = list(output.get("items") or [])
    items: list[BatchItem] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or ("ok" if row.get("ok") else "failed"))
        if status == "queued":
            status = "ok"
        if status not in ("ok", "failed", "skipped"):
            status = "failed" if not row.get("ok") else "ok"
        items.append(BatchItem(
            index=int(row.get("index", i)), status=status,  # type: ignore[arg-type]
            error=str(row.get("error") or "") or None,
            data={k: v for k, v in row.items()
                  if k not in ("index", "status", "error", "error_info")},
        ))
    result = BatchResult.from_items(
        items, job=JobRef(job_id=job_id or str(output.get("job_id") or ""),
                          status="running"),
    )
    output["batch"] = result.model_dump()
    return output
