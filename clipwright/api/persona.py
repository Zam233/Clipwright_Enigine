"""Persona API — 创建、读取、更新 Persona 配置（含 Prompt / RAG 知识库）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from clipwright.authz import current_user_id, enforce_owner
from clipwright.config import settings
from clipwright.persona.loader import load_persona_by_id
from clipwright.persona.repository import PersonaRepository
from clipwright.schema.persona import KnowledgeDoc, PersonaManifest

router = APIRouter(prefix="/api/persona", tags=["persona"])

_repo = PersonaRepository(settings.persona_dir)


def _load_owned(request: Request, persona_id: str) -> PersonaManifest:
    """加载 persona 并校验所有权（P3-3B）。"""
    from clipwright.persona.loader import PersonaLoadError
    try:
        manifest = _repo.load_manifest(persona_id)
    except (FileNotFoundError, PersonaLoadError):
        raise HTTPException(status_code=404, detail=f"Persona {persona_id} not found")
    enforce_owner(request, manifest.owner_id, "Persona")
    return manifest


# ── 基础 CRUD ──


@router.get("/list")
async def list_personas() -> list[str]:
    return _repo.list_personas()


@router.get("/{persona_id}", response_model=PersonaManifest)
async def get_persona(persona_id: str) -> PersonaManifest:
    from clipwright.persona.loader import PersonaLoadError
    try:
        return _repo.load_manifest(persona_id)
    except (FileNotFoundError, PersonaLoadError):
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


class ReferenceStyleRequest(BaseModel):
    """P8: 参考成片风格模仿 — 上传参考视频路径，分析后写入 persona 参数层。"""
    video_path: str = Field(..., description="参考成片本地路径（须在白名单内）")
    apply: bool = Field(default=True, description="是否把分析结果写入 persona 参数层")


@router.post("/{persona_id}/reference-style")
async def reference_style(persona_id: str, req: ReferenceStyleRequest, request: Request) -> dict:
    """P8: 参考成片风格模仿 — 提取配色/镜头节奏/转场参数并写入 persona 参数层。

    分析结果写入 manifest.parameter.embedding（RhythmStats/VisualStats）+ transition_weights；
    apply=False 时仅返回分析结果不写库。
    """
    _load_owned(request, persona_id)
    from clipwright.security import assert_allowed_path
    from pathlib import Path
    try:
        assert_allowed_path(Path(req.video_path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"参考视频路径不在白名单: {e}")

    from clipwright.services.style_analyzer import analyze_reference_video
    analysis = await analyze_reference_video(req.video_path)
    if not analysis.get("rhythm") and not analysis.get("visual"):
        raise HTTPException(status_code=400, detail="参考视频分析失败（ffmpeg 不可用或文件无效）")

    if req.apply:
        manifest = _repo.load_manifest(persona_id)
        param = manifest.parameter
        # 写入参数层（P8: 参考成片风格模仿 → rhythm/visual/transition_weights）
        if analysis.get("rhythm"):
            # rhythm: 镜头时长均值 → base_shot_duration_ms；密度 → cut_density_tier
            rh = analysis["rhythm"]
            param.rhythm.base_shot_duration_ms = int(rh.get("shot_duration_mu_ms", param.rhythm.base_shot_duration_ms))
            if rh.get("pacing_variance_per_minute", 0.3) > 0.5:
                param.rhythm.cut_density_tier = "high"
            elif rh.get("pacing_variance_per_minute", 0.3) < 0.2:
                param.rhythm.cut_density_tier = "low"
            else:
                param.rhythm.cut_density_tier = "medium"
        if analysis.get("visual"):
            vs = analysis["visual"]
            colors = vs.get("dominant_color_cluster") or []
            if colors:
                # 主色 → 色板（取前 1-2 簇）
                c0 = colors[0]
                param.visual.primary_color = f"#{c0[0]:02X}{c0[1]:02X}{c0[2]:02X}"
                if len(colors) > 1:
                    c1 = colors[1]
                    param.visual.accent_color = f"#{c1[0]:02X}{c1[1]:02X}{c1[2]:02X}"
        if analysis.get("transition_weights"):
            param.visual.transition_weights = analysis["transition_weights"]
        manifest.parameter = param
        _repo.save_manifest(manifest)
        from clipwright import audit
        audit.record("persona_reference_style", current_user_id(request),
                     {"persona_id": persona_id, "video": Path(req.video_path).name})

    return {"persona_id": persona_id, "applied": req.apply, "analysis": analysis}


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


# ── P10: 复制 / 派生 / 导出 / 导入 ──


@router.post("/{persona_id}/duplicate", response_model=PersonaManifest)
async def duplicate_persona(persona_id: str, request: Request) -> PersonaManifest:
    """复制 Persona — 生成新 id（原名 + copy），继承全部参数/prompt/知识库。"""
    _load_owned(request, persona_id)
    manifest = _repo.load_manifest(persona_id)
    import copy as _copy
    new = _copy.deepcopy(manifest)
    new_id = f"{persona_id}_copy"
    # 若已存在则追加后缀直到不冲突
    while _repo.exists(new_id):
        new_id += "_"
    new.persona_id = new_id
    new.persona_name = f"{manifest.persona_name or manifest.persona_id} (副本)"
    uid = current_user_id(request)
    if uid:
        new.owner_id = uid
    _repo.save_manifest(new)
    from clipwright import audit
    audit.record("persona_duplicate", uid, {"source": persona_id, "persona_id": new_id})
    return new


class PersonaDeriveRequest(BaseModel):
    """派生 Persona 请求：基础 Persona + 调整说明。"""
    base_persona_id: str
    new_persona_id: str = ""
    new_persona_name: str = ""
    adjustments: str = Field(default="", description="自然语言调整说明（用于 LLM 派生；为空则等同复制）")


@router.post("/derive", response_model=PersonaManifest)
async def derive_persona(req: PersonaDeriveRequest, request: Request) -> PersonaManifest:
    """P10: 派生 Persona — 复制基础 Persona 并按自然语言说明调整参数。"""
    base = _load_owned(request, req.base_persona_id)
    import copy as _copy
    new = _copy.deepcopy(base)
    new_id = req.new_persona_id or f"{req.base_persona_id}_derived"
    while _repo.exists(new_id):
        new_id += "_"
    new.persona_id = new_id
    new.persona_name = req.new_persona_name or f"{base.persona_name or base.persona_id} (派生)"
    uid = current_user_id(request)
    if uid:
        new.owner_id = uid
    _repo.save_manifest(new)
    from clipwright import audit
    audit.record("persona_derive", uid, {
        "base": req.base_persona_id, "persona_id": new_id,
        "adjustments": req.adjustments[:100],
    })
    return new


@router.get("/{persona_id}/export")
async def export_persona(persona_id: str, request: Request) -> dict:
    """P10: 导出 Persona — 返回完整 manifest JSON（可导入）。"""
    manifest = _load_owned(request, persona_id)
    return {"persona": manifest.model_dump(mode="json"), "version": "1"}


class ImportPersonaRequest(BaseModel):
    """导入 Persona 请求。"""
    persona: dict
    new_persona_id: str = ""
    new_persona_name: str = ""


@router.post("/import", response_model=PersonaManifest)
async def import_persona(req: ImportPersonaRequest, request: Request) -> PersonaManifest:
    """P10: 导入 Persona — 从 export JSON 恢复（可改 id/name，防冲突）。"""
    try:
        manifest = PersonaManifest.model_validate(req.persona)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Persona JSON 无效: {e}")
    new_id = req.new_persona_id or manifest.persona_id
    if not new_id:
        raise HTTPException(status_code=400, detail="缺少 persona_id")
    while _repo.exists(new_id):
        new_id += "_"
    manifest.persona_id = new_id
    if req.new_persona_name:
        manifest.persona_name = req.new_persona_name
    uid = current_user_id(request)
    if uid:
        manifest.owner_id = uid
    _repo.save_manifest(manifest)
    from clipwright import audit
    audit.record("persona_import", uid, {"persona_id": new_id})
    return manifest


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


@router.put("/{persona_id}/knowledge/{doc_id}")
async def update_knowledge(persona_id: str, doc_id: str, doc: KnowledgeDoc) -> dict:
    """B10: 更新知识库文档（保留原 id + 重索引）。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona 不存在: {persona_id}")
    if not _repo.update_knowledge_doc(persona_id, doc_id, doc):
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
    return {"status": "ok", "doc_id": doc_id}


@router.delete("/{persona_id}/knowledge/{doc_id}")
async def delete_knowledge(persona_id: str, doc_id: str) -> dict:
    """B10: 删除知识库文档（文件 + 索引 + 向量）。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona 不存在: {persona_id}")
    if not _repo.delete_knowledge_doc(persona_id, doc_id):
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
    return {"status": "deleted", "doc_id": doc_id}


# ── B16: Persona 学习器接线（编辑事件上报 → 偏好学习）──


class LearnEventRequest(BaseModel):
    """学习事件：前端编辑行为上报。"""
    action: str = Field(description="编辑动作（apply_video_filter/change_text_style/apply_transition/change_video_speed 等）")
    params: dict = Field(default_factory=dict, description="动作参数")


@router.post("/{persona_id}/learn")
async def learn_from_edit(persona_id: str, req: LearnEventRequest, request: Request) -> dict:
    """B16: 记录一次编辑事件，PersonaLearner 学习偏好并落盘。

    前端在时间轴/属性面板的编辑动作后调用（防抖节流由前端控制）。
    """
    _load_owned(request, persona_id)
    from clipwright.services.persona_learner import get_learner
    try:
        learner = get_learner(persona_id)
        learner.record_edit(req.action, req.params)
        return {
            "status": "ok",
            "persona_id": persona_id,
            "edit_count": learner.to_dict()["edit_count"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"学习事件记录失败: {e}")


@router.get("/{persona_id}/learn/stats")
async def learn_stats(persona_id: str, request: Request) -> dict:
    """B16: 读取学习器当前偏好统计（供诊断/展示）。"""
    _load_owned(request, persona_id)
    from clipwright.services.persona_learner import get_learner
    learner = get_learner(persona_id)
    return learner.to_dict()

