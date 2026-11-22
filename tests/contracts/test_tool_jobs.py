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


def test_heartbeat_and_cancel() -> None:
    import asyncio
    from clipwright.services import task_events

    tool_jobs.clear()
    job = tool_jobs.create("long_batch", 3)

    async def _run():
        hb = tool_jobs.start_heartbeat("task_hb", job, interval=0.01)
        assert hb is not None
        try:
            await asyncio.sleep(0.03)
            assert tool_jobs.cancel(job.job_id)["status"] == "cancelled"
        finally:
            tool_jobs.stop_heartbeat(hb)

    asyncio.run(_run())
    assert tool_jobs.is_cancelled(job.job_id)
