"""通用 batch job 状态。"""

from __future__ import annotations

from clipwright.services import tool_jobs


def test_job_lifecycle() -> None:
    tool_jobs.clear()
    job = tool_jobs.create("vision_llm_batch", 3)
    assert job.status == "running"
    tool_jobs.update(job.job_id, completed=1)
    assert tool_jobs.get(job.job_id)["status"] == "running"
    tool_jobs.update(job.job_id, completed=2, failed=1)
    assert tool_jobs.get(job.job_id)["status"] == "partial"
    done = tool_jobs.finish(job.job_id, failed=1)
    assert done["status"] == "partial"
    assert done["completed"] == 3


def test_job_cancel_and_bounded() -> None:
    tool_jobs.clear()
    job = tool_jobs.create("x", 2)
    assert tool_jobs.cancel(job.job_id, "stop")["status"] == "cancelled"
    jobs = [tool_jobs.create("x", 1) for _ in range(305)]
    assert len(tool_jobs.list_jobs(limit=500)) <= 300
    assert tool_jobs.get(jobs[-1].job_id) is not None
