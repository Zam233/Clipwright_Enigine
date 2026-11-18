"""Animation context / blackboard / calls API (engine-owned)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clipwright.animation.context_store import ContextConflict, get_context_store

router = APIRouter(prefix="/api/animation", tags=["animation-context"])


class HistoryIn(BaseModel):
    role: str = "user"
    content: str = Field(min_length=1, max_length=8000)
    meta: dict = Field(default_factory=dict)


class BlackboardPatch(BaseModel):
    patch: dict = Field(default_factory=dict)
    expected_version: int | None = None


class ArtifactIn(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=200)
    content: str


class CallIn(BaseModel):
    kind: str
    name: str
    status: str = "success"
    input_summary: str = ""
    output_summary: str = ""
    artifact_ref: str = ""
    error: str = ""
    idempotency_key: str = ""
    children: list[str] = Field(default_factory=list)


@router.get("/context/{session_id}/{task_id}")
async def get_context(session_id: str, task_id: str) -> dict:
    return get_context_store().get(session_id, task_id)


@router.post("/context/{session_id}/{task_id}/history")
async def append_history(session_id: str, task_id: str, body: HistoryIn) -> dict:
    get_context_store().append_history(session_id, task_id, body.role, body.content, **body.meta)
    return get_context_store().get(session_id, task_id)


@router.get("/context/{session_id}/{task_id}/history")
async def get_history(session_id: str, task_id: str, cursor: int = 0) -> dict:
    ctx = get_context_store().get(session_id, task_id)
    return {"history": ctx["history"][cursor:], "summary": ctx["history_summary"]}


@router.get("/blackboard/{session_id}/{task_id}")
async def get_blackboard(session_id: str, task_id: str) -> dict:
    return get_context_store().get(session_id, task_id)


@router.patch("/blackboard/{session_id}/{task_id}")
async def patch_blackboard(session_id: str, task_id: str, body: BlackboardPatch) -> dict:
    try:
        return get_context_store().patch_blackboard(session_id, task_id, body.patch, body.expected_version)
    except ContextConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/blackboard/{session_id}/{task_id}/artifact")
async def put_artifact(session_id: str, task_id: str, body: ArtifactIn) -> dict:
    ref = get_context_store().put_artifact(session_id, task_id, body.artifact_id, body.content)
    return {"artifact_ref": ref}


@router.get("/calls/{session_id}/{task_id}")
async def list_calls(session_id: str, task_id: str, kind: str = "") -> dict:
    calls = get_context_store().get(session_id, task_id)["calls"]
    if kind:
        calls = [c for c in calls if c.get("kind") == kind]
    return {"calls": calls}


@router.post("/calls/{session_id}/{task_id}")
async def record_call(session_id: str, task_id: str, body: CallIn) -> dict:
    rec = get_context_store().record_call(
        session_id, task_id, body.kind, body.name, body.status,
        input_summary=body.input_summary, output_summary=body.output_summary,
        artifact_ref=body.artifact_ref, error=body.error,
        idempotency_key=body.idempotency_key, children=body.children,
    )
    return rec.as_dict()


class QAIn(BaseModel):
    shot_index: int = Field(default=1, ge=1)
    report: dict = Field(default_factory=dict)


@router.post("/blackboard/{session_id}/{task_id}/qa")
async def record_qa(session_id: str, task_id: str, body: QAIn) -> dict:
    return get_context_store().record_qa(session_id, task_id, body.shot_index, body.report)
