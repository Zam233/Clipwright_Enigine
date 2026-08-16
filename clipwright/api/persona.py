"""Persona API — 创建、读取、更新 Persona 配置（含 Prompt / RAG 知识库）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clipwright.authz import current_user_id, enforce_owner
from clipwright.config import settings
from clipwright.persona.loader import load_persona_by_id
from clipwright.persona.repository import PersonaRepository
from clipwright.schema.persona import KnowledgeDoc, PersonaManifest

router = APIRouter(prefix="/api/persona", tags=["persona"])

_repo = PersonaRepository(settings.persona_dir)


def _load_owned(request: Request, persona_id: str) -> PersonaManifest:
    """加载 persona 并校验所有权（P3-3B）。"""
    try:
        manifest = _repo.load_manifest(persona_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Persona {persona_id} not found")
    enforce_owner(request, manifest.owner_id, "Persona")
    return manifest


# ── 基础 CRUD ──


@router.get("/list")
async def list_personas() -> list[str]:
    return _repo.list_personas()


@router.get("/{persona_id}", response_model=PersonaManifest)
async def get_persona(persona_id: str) -> PersonaManifest:
    try:
        return _repo.load_manifest(persona_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Persona {persona_id} not found")


@router.post("/create", response_model=PersonaManifest)
async def create_persona(manifest: PersonaManifest, request: Request) -> PersonaManifest:
    if _repo.exists(manifest.persona_id):
        raise HTTPException(status_code=409, detail=f"Persona {manifest.persona_id} already exists")
    # P3-3B: 记录创建者
    uid = current_user_id(request)
    if uid and not manifest.owner_id:
        manifest.owner_id = uid
    _repo.save_manifest(manifest)
    # P5-B5: 审计
    from clipwright import audit
    audit.record("persona_create", uid, {"persona_id": manifest.persona_id})
    return manifest


@router.put("/{persona_id}", response_model=PersonaManifest)
async def update_persona(persona_id: str, manifest: PersonaManifest, request: Request) -> PersonaManifest:
    _load_owned(request, persona_id)  # P3-3B
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona {persona_id} not found")
    # 保留原所有者（body 未携带时）
    if not manifest.owner_id:
        manifest.owner_id = _repo.load_manifest(persona_id).owner_id
    _repo.save_manifest(manifest)
    return manifest


@router.delete("/{persona_id}")
async def delete_persona(persona_id: str, request: Request) -> dict:
    """删除 Persona（含磁盘目录；P3-3B: 校验所有权）。"""
    _load_owned(request, persona_id)
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona {persona_id} not found")
    _repo.delete(persona_id)
    # P5-B5: 审计
    from clipwright import audit
    audit.record("persona_delete", current_user_id(request), {"persona_id": persona_id})
    return {"status": "deleted", "persona_id": persona_id}


# ── Prompt 管理 ──


class SavePromptRequest(BaseModel):
    prompt: str


@router.get("/{persona_id}/prompt")
async def get_prompt(persona_id: str) -> dict:
    """获取 Persona 的 Prompt 指令。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona 不存在: {persona_id}")
    prompt_path = _repo.persona_path(persona_id) / "prompt.md"
    text = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    return {"persona_id": persona_id, "prompt": text}


@router.put("/{persona_id}/prompt")
async def save_prompt(persona_id: str, req: SavePromptRequest) -> dict:
    """保存/更新 Persona 的 Prompt 指令。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona 不存在: {persona_id}")
    _repo.save_prompt(persona_id, req.prompt)
    return {"status": "ok", "persona_id": persona_id}


# ── 视觉需求 Prompt 管理 ──


class SaveVisionPromptRequest(BaseModel):
    vision_prompt: str


@router.get("/{persona_id}/vision-prompt")
async def get_vision_prompt(persona_id: str) -> dict:
    """获取 Persona 的视觉需求 Prompt。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona 不存在: {persona_id}")
    vision_path = _repo.persona_path(persona_id) / "vision_prompt.md"
    text = vision_path.read_text(encoding="utf-8") if vision_path.exists() else ""
    return {"persona_id": persona_id, "vision_prompt": text}


@router.put("/{persona_id}/vision-prompt")
async def save_vision_prompt(persona_id: str, req: SaveVisionPromptRequest) -> dict:
    """保存/更新 Persona 的视觉需求 Prompt（显式编辑，允许覆盖）。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona 不存在: {persona_id}")
    _repo.save_vision_prompt(persona_id, req.vision_prompt)
    return {"status": "ok", "persona_id": persona_id}


# ── RAG 知识库管理 ──


@router.get("/{persona_id}/knowledge")
async def list_knowledge(persona_id: str) -> list[KnowledgeDoc]:
    """列出 Persona 的知识库文档。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona 不存在: {persona_id}")
    manifest = _repo.load_manifest(persona_id)
    return manifest.knowledge or []


@router.post("/{persona_id}/knowledge")
async def add_knowledge(persona_id: str, doc: KnowledgeDoc) -> dict:
    """向 Persona 知识库添加一篇文档（P0-12: 返回真实 doc_id）。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona 不存在: {persona_id}")
    actual_id = _repo.add_knowledge_doc(persona_id, doc)
    return {"status": "ok", "doc_id": actual_id}

