"""WP0：批量工具统一契约。"""

from __future__ import annotations

from clipwright.schema.tool import (
    BatchItem, BatchResult, BatchSummary, JobRef, ToolError,
)


def test_batch_result_from_items_partial() -> None:
    result = BatchResult.from_items([
        BatchItem(index=0, status="ok", data={"path": "a"}),
        BatchItem(index=1, status="failed",
                  error="bad", error_info=ToolError(category="input_invalid")),
    ], job=JobRef(job_id="job_x", status="partial", total=2,
                  progress={"completed": 1, "failed": 1}))
    assert result.status == "partial"
    assert result.summary == BatchSummary(total=2, ok=1, failed=1, skipped=0)
    assert result.failures[0].index == 1
    assert result.job is not None and result.job.job_id == "job_x"


def test_batch_result_all_failed_and_skipped() -> None:
    result = BatchResult.from_items([
        BatchItem(index=0, status="failed", error="x"),
        BatchItem(index=1, status="skipped", error="cancelled"),
    ])
    assert result.status == "failed"
    assert result.summary.failed == 1 and result.summary.skipped == 1
