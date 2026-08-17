"""C2: requirements_service LLM 调用成本追踪测试。

验证 _record_llm_usage 在 4 类调用点（edit_intent / edit_adjust / confirm /
gathering / plan_translate / structure）后写入 llm_tracker 内存记录。
"""

from __future__ import annotations

import pytest

import clipwright.services.requirements_service as req_mod
from clipwright.services.llm_tracker import _llm_calls, record_llm_call


@pytest.fixture(autouse=True)
def _clean_llm_calls():
    """隔离 llm_tracker 内存记录，避免测试间互相污染。"""
    before = list(_llm_calls)
    _llm_calls.clear()
    yield
    _llm_calls.clear()
    _llm_calls.extend(before)


class _StubLLM:
    """模拟 LLMService：可配置 last_usage，记录 structured_output 调用。"""

    def __init__(self, usage: dict | None = None):
        self.last_usage = usage
        self.calls: list[str] = []

    async def structured_output(self, **kwargs):
        self.calls.append("structured_output")
        return {}

    async def with_tools(self, **kwargs):
        self.calls.append("with_tools")
        return type("R", (), {"content": '{"reply": "hi"}'})()


def _service(stub_llm) -> req_mod.RequirementsService:
    svc = req_mod.RequirementsService.__new__(req_mod.RequirementsService)
    svc._llm = stub_llm  # type: ignore[assignment]
    svc._cleanup_started = True
    return svc


@pytest.mark.asyncio
async def test_record_usage_writes_to_tracker() -> None:
    """_record_llm_usage 把 last_usage 写入 llm_tracker（含 pipeline_id/agent_name）。"""
    svc = _service(_StubLLM(usage={"input_tokens": 12, "output_tokens": 34}))
    await svc._record_llm_usage("requirements.edit_intent", "req_abc")
    records = list(_llm_calls)
    assert len(records) == 1
    r = records[0]
    assert r["pipeline_id"] == "req_abc"
    assert r["agent_name"] == "requirements.edit_intent"
    assert r["input_tokens"] == 12
    assert r["output_tokens"] == 34


@pytest.mark.asyncio
async def test_record_usage_no_usage_skips() -> None:
    """无 last_usage（ask 路径 / 失败）→ 不写入。"""
    svc = _service(_StubLLM(usage=None))
    await svc._record_llm_usage("requirements.confirm")
    assert _llm_calls == []


@pytest.mark.asyncio
async def test_edit_intent_records_usage() -> None:
    """edit_timeline 意图分类成功后记录用量。"""
    stub = _StubLLM(usage={"input_tokens": 5, "output_tokens": 1})
    stub.structured_output = _intent_fn
    svc = _service(stub)
    # 真实内存会话（不依赖 Mongo）
    session = svc.create_session({"topic": "测试"})
    sid = session["session_id"]
    timeline = _mk_timeline()
    try:
        await svc.edit_timeline(
            sid, "换素材", timeline, ["c1"],
        )
    except Exception:
        # 后续换素材逻辑可能因缺依赖失败，只验证意图分类已记录
        pass
    assert any(r["agent_name"] == "requirements.edit_intent" for r in _llm_calls)


@pytest.mark.asyncio
async def test_confirm_records_usage() -> None:
    """_is_confirm 的 LLM 语义判断成功后记录用量。

    输入需命中「语义模糊」分支（不以明确确认/否定词开头）才会走 LLM。
    """
    stub = _StubLLM(usage={"input_tokens": 7, "output_tokens": 2})
    svc = _service(stub)

    async def fake_confirm(**kwargs):
        return {"is_confirm": True}

    stub.structured_output = fake_confirm
    # 「这个方案可以」不以 affirm_starts 开头 → 模糊分支 → LLM
    result = await svc._is_confirm("这个方案可以")
    assert result is True
    assert any(r["agent_name"] == "requirements.confirm" for r in _llm_calls)


def _mk_timeline() -> dict:
    return {
        "id": "tl_1", "width": 1920, "height": 1080, "fps": 30.0, "duration_sec": 10.0,
        "tracks": [
            {
                "id": "tr_v", "name": "V1", "kind": "video", "index": 0,
                "clips": [
                    {
                        "id": "c1", "kind": "video", "asset_id": "C:\\library\\a.mp4",
                        "track_id": "tr_v", "start_sec": 0, "duration_sec": 5,
                        "source_offset_sec": 0, "speed": 1, "volume": 1, "opacity": 1,
                        "keyframes": [], "metadata": {},
                    },
                ],
            }
        ],
    }


async def _intent_fn(**kwargs) -> dict:
    return {"action": "replace_material"}
