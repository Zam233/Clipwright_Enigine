"""PersonaForge API — 智能 Persona 构建端点。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clipwright.schema.persona import PersonaManifest
from clipwright.services.persona_forge import PersonaForge

router = APIRouter(prefix="/api/persona/forge", tags=["persona-forge"])

_forge = PersonaForge()


class ForgeFromPromptRequest(BaseModel):
    description: str
    persona_id: str
    persona_name: str = ""


class ForgeFromScriptRequest(BaseModel):
    script: str
    persona_id: str
    persona_name: str = ""
    script_format: str = "txt"


class ForgeRefineRequest(BaseModel):
    persona_id: str
    feedback: str


class DialogueQuestionsRequest(BaseModel):
    persona_id: str
    existing_answers: Optional[dict[str, Any]] = None


class DialogueBuildRequest(BaseModel):
    persona_id: str
    persona_name: str = ""
    answers: dict[str, Any]


@router.post("/from-prompt", response_model=PersonaManifest)
async def forge_from_prompt(req: ForgeFromPromptRequest) -> PersonaManifest:
    """自然语言描述 → Persona。

    用户用自然语言描述创作风格，系统自动映射为结构化 Persona 配置。
    """
    try:
        manifest = await _forge.from_prompt(
            description=req.description,
            persona_id=req.persona_id,
            persona_name=req.persona_name,
        )
        await _forge.save_persona(manifest)
        return manifest
    except Exception as e:
        logger.exception("forge from prompt failed")
        raise HTTPException(status_code=500, detail="Persona 生成失败，请稍后重试")


@router.post("/from-script", response_model=PersonaManifest)
async def forge_from_script(req: ForgeFromScriptRequest) -> PersonaManifest:
    """脚本/口播文本 → Persona。

    上传脚本或口播文本，系统通过语言分析和 LLM 提取创作风格。
    支持 .txt / .srt / .md 格式。
    """
    try:
        manifest = await _forge.from_script(
            script=req.script,
            persona_id=req.persona_id,
            persona_name=req.persona_name,
            script_format=req.script_format,
        )
        await _forge.save_persona(manifest)
        return manifest
    except Exception as e:
        logger.exception("forge from prompt failed")
        raise HTTPException(status_code=500, detail="Persona 生成失败，请稍后重试")


@router.post("/refine", response_model=PersonaManifest)
async def forge_refine(req: ForgeRefineRequest) -> PersonaManifest:
    """迭代优化 Persona。

    给出 Persona ID 和自然语言反馈，系统自动调整对应参数。
    """
    try:
        from clipwright.config import settings
        from clipwright.persona.loader import load_persona_by_id

        manifest = load_persona_by_id(req.persona_id)
        updated = await _forge.refine(manifest, req.feedback)
        await _forge.save_persona(updated)
        return updated
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Persona {req.persona_id} not found"
        )
    except Exception as e:
        logger.exception("forge from prompt failed")
        raise HTTPException(status_code=500, detail="Persona 生成失败，请稍后重试")


@router.post("/dialogue/generate-questions")
async def dialogue_generate_questions(
    req: DialogueQuestionsRequest,
) -> list[dict[str, str]]:
    """对话引导：生成下一步引导问题。"""
    try:
        questions = await _forge.dialogue_generate_questions(
            persona_id=req.persona_id,
            existing_answers=req.existing_answers,
        )
        return questions
    except Exception as e:
        logger.exception("forge from prompt failed")
        raise HTTPException(status_code=500, detail="Persona 生成失败，请稍后重试")


@router.post("/dialogue/build", response_model=PersonaManifest)
async def dialogue_build(req: DialogueBuildRequest) -> PersonaManifest:
    """对话引导：将问答记录编译为 Persona。"""
    try:
        manifest = await _forge.dialogue_build(
            persona_id=req.persona_id,
            persona_name=req.persona_name,
            answers=req.answers,
        )
        await _forge.save_persona(manifest)
        return manifest
    except Exception as e:
        logger.exception("forge from prompt failed")
        raise HTTPException(status_code=500, detail="Persona 生成失败，请稍后重试")
