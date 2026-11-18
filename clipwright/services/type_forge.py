"""TypeForge — 对话式视频类型创建。"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.prompts import TYPE_FORGE_SYSTEM, TYPE_TITLE_SYSTEM
from clipwright.services.llm import LLMService


def _default_draft() -> dict[str, Any]:
    return {
        "id": "", "name": "", "description": "",
        "shot_params": {
            "min_shot_sec": 1.0, "max_shot_sec": 4.0,
            "transition_type": "cut", "transition_duration_sec": 0.5,
            "cut_on_beat": False,
        },
        "prompts": {},
        "param_rules": [],
        "signature_traits": [],
        "declares": {},
        "waivers": [],
    }


@dataclass
class TypeForgeSession:
    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    draft: dict[str, Any] = field(default_factory=_default_draft)
    title: str = ""
    revision: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TypeForge:
    SESSIONS_DIR = Path("typeforge_sessions")

    def __init__(self) -> None:
        self._llm = LLMService()
        self._sessions: dict[str, TypeForgeSession] = {}
        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._restore()

    def _restore(self) -> None:
        for path in self.SESSIONS_DIR.glob("*.json"):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                self._sessions[d["session_id"]] = TypeForgeSession(
                    session_id=d["session_id"],
                    messages=d.get("messages", []),
                    draft=d.get("draft") or _default_draft(),
                    title=d.get("title", ""),
                    revision=int(d.get("revision", 0) or 0),
                )
            except Exception:
                continue

    def _persist(self, s: TypeForgeSession) -> None:
        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = self.SESSIONS_DIR / f"{s.session_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "session_id": s.session_id, "messages": s.messages,
            "draft": s.draft, "title": s.title, "revision": s.revision,
            "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    async def start(self) -> dict[str, Any]:
        s = TypeForgeSession(session_id=uuid.uuid4().hex[:12])
        self._sessions[s.session_id] = s
        self._persist(s)
        return {"session_id": s.session_id, "reply": "", "draft": s.draft, "missing_fields": [], "progress": {}}

    def get(self, session_id: str) -> Optional[TypeForgeSession]:
        return self._sessions.get(session_id)

    def detail(self, session_id: str) -> Optional[dict[str, Any]]:
        s = self.get(session_id)
        if not s:
            return None
        return {
            "session_id": s.session_id, "title": s.title, "revision": s.revision,
            "messages": s.messages, "draft": s.draft,
            "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat(),
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        out = []
        for s in self._sessions.values():
            user = next((m.get("content", "") for m in s.messages if m.get("role") == "user"), "")
            out.append({
                "session_id": s.session_id, "title": s.title or user[:40],
                "message_count": len([m for m in s.messages if m.get("role") == "user"]),
                "updated_at": s.updated_at.isoformat(), "draft_name": s.draft.get("name", ""),
            })
        return sorted(out, key=lambda x: x["updated_at"], reverse=True)

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    value = json.loads(raw[start:end + 1])
                    return value if isinstance(value, dict) else {}
                except json.JSONDecodeError:
                    pass
        return {}

    def _merge(self, draft: dict[str, Any], update: dict[str, Any]) -> None:
        allowed_transitions = {"cut", "fade", "dissolve", "wipe", "glitch", "pixel_dissolve", "slide"}
        sp = update.get("shot_params")
        if isinstance(sp, dict) and sp.get("transition_type") not in allowed_transitions:
            sp["transition_type"] = "cut"
        if isinstance(update.get("signature_traits"), list):
            update["signature_traits"] = [t for t in update["signature_traits"] if isinstance(t, dict)]
        for key, value in update.items():
            if key not in draft:
                continue
            if isinstance(value, dict) and isinstance(draft.get(key), dict):
                draft[key].update(value)
            elif value is not None:
                draft[key] = value

    async def message(self, session_id: str, text: str, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        s = self.get(session_id)
        if not s:
            raise KeyError(session_id)
        content = text
        for a in attachments or []:
            if str(a.get("type")) == "file" and a.get("data_url"):
                try:
                    import base64
                    raw = str(a["data_url"]).split(",", 1)[1]
                    content += "\n\n[附件]\n" + base64.b64decode(raw).decode("utf-8", errors="ignore")[:8000]
                except Exception:
                    pass
        s.messages.append({"role": "user", "content": content})
        s.updated_at = datetime.now(timezone.utc)
        self._persist(s)
        result = await self._process(s)
        self._persist(s)
        await self._ensure_title(s)
        return result

    async def _process(self, s: TypeForgeSession) -> dict[str, Any]:
        msgs = [{"role": "system", "content": TYPE_FORGE_SYSTEM}]
        msgs += [{"role": m["role"], "content": m.get("content", "")} for m in s.messages]
        try:
            resp = await self._llm.generate(messages=msgs)
            data = self._extract_json(getattr(resp, "content", "") or "")
            if data:
                self._merge(s.draft, data.get("type_draft") or {})
                reply = str(data.get("reply") or "已更新类型草稿。")
                s.messages.append({"role": "assistant", "content": reply})
                s.updated_at = datetime.now(timezone.utc)
                return {
                    "reply": reply, "draft": s.draft,
                    "missing_fields": data.get("missing_fields") or [],
                    "progress": data.get("progress") or {},
                }
        except Exception as e:  # noqa: BLE001
            logger.warning("TypeForge LLM failed: %s", e)
        reply = "暂时无法调用模型。已保留你的描述，请重试。"
        s.messages.append({"role": "assistant", "content": reply})
        s.updated_at = datetime.now(timezone.utc)
        return {"reply": reply, "draft": s.draft, "missing_fields": [], "progress": {}}

    async def _ensure_title(self, s: TypeForgeSession) -> None:
        if s.title:
            return
        first = next((m.get("content", "") for m in s.messages if m.get("role") == "user"), "")
        if not first:
            return
        try:
            resp = await asyncio.wait_for(self._llm.generate(messages=[
                {"role": "system", "content": TYPE_TITLE_SYSTEM},
                {"role": "user", "content": first[:1000]},
            ]), timeout=8)
            text = (getattr(resp, "content", "") or "").strip().strip('"“”').strip()
            if text and len(text) <= 40:
                s.title = text
                self._persist(s)
        except Exception as e:  # noqa: BLE001
            logger.debug("TypeForge title failed: %s", e)

    async def commit(self, session_id: str, type_id: str = "", definition: dict[str, Any] | None = None) -> dict[str, Any]:
        s = self.get(session_id)
        if not s:
            raise KeyError(session_id)
        payload = dict(definition or s.draft)
        if type_id:
            payload["id"] = type_id
        if not payload.get("id"):
            raise ValueError("type id is required")
        from clipwright.api.type_maker import TypeDefinition, create_type, spec_issues
        try:
            td = TypeDefinition(**payload)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"类型定义不合法: {exc}") from exc
        errors = [i.message for i in spec_issues(td) if i.severity == "error"]
        if errors:
            raise ValueError("；".join(errors))
        result = await create_type(td)
        s.revision += 1
        s.updated_at = datetime.now(timezone.utc)
        self._persist(s)
        return result
