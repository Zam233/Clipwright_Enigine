"""ChatForge — 对话式 Persona 智能构建引擎。

不同于 stateless 的 PersonaForge，ChatForge 维护完整的对话状态，
让用户通过与 AI 自然对话来逐步构建 Persona。

支持：
- 多轮对话，AI 逐步追问完善各维度
- 上传知识库/参考文档作为风格依据
- 实时预览 Persona 草稿
- 任何时候可保存/提交
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from clipwright.persona.repository import PersonaRepository
from clipwright.schema.persona import (
    AudioConfig,
    ConstraintsConfig,
    IdentityConfig,
    KnowledgeDoc,
    LanguageConfig,
    ParameterLayer,
    PersonaManifest,
    RhythmConfig,
    VisualConfig,
)
from clipwright.services.llm import LLMService
from clipwright.config import logger

# ── ChatForgesystem prompt ──

CHAT_SYSTEM_PROMPT = """你是一个专业的视频创作风格顾问。你的工作是通过自然对话，帮助创作者逐步明确自己的风格，并实时构建结构化的 Persona 配置。

## Persona 的六个维度

每个维度你都需要通过对话逐步收集信息：

1. **identity（身份）**：语调（冷峻/热情/学术/吐槽）、知识领域、立场
2. **language（语言）**：学术密度、网络用语比例、句长偏好、句式模式
3. **rhythm（剪辑节奏）**：整体节奏型、基准镜头时长、快慢段落分布
4. **visual（视觉）**：配色方案、文字动画风格、转场偏好
5. **audio（音频）**：BGM 类型偏好、音量风格、音效使用
6. **constraints（约束）**：最长/最短时长、禁用内容

## 对话策略

- **开局**：问一个开放性问题了解创作者的总体风格
- **推进**：基于已有信息追问最缺失的维度，每次最多追问 2 个问题
- **确认**：当用户给出具体描述时，提取关键参数并让用户确认
- **修正**：用户说"不对"或"改一下"时，精确调整对应参数
- **知识库**：如果用户上传了参考文档/脚本，提取其中的风格特征融入 Persona

## 输出格式

每次回应必须返回以下 JSON 结构（不要包含其他内容）：

```json
{
  "reply": "你对用户的自然语言回应，可以提问、确认、总结",
  "persona_draft": {
    "identity": {"tone": "...", "knowledge_domains": [...]},
    "language": {"academic_density": 0.1, "slang_ratio": 0.05, ...},
    "rhythm": {"cut_profile": "...", "base_shot_duration_ms": 5000, ...},
    "visual": {"palette": "...", "animation_styles": {...}, ...},
    "audio": {"bgm_slots": {...}, "target_loudness_lufs": -16, ...},
    "constraints": {"max_duration_sec": 900, ...}
  },
  "missing_dimensions": ["还未收集信息的维度"],
  "progress": {
    "identity": 0.8,
    "language": 0.5,
    "rhythm": 0.3,
    "visual": 0.2,
    "audio": 0.1,
    "constraints": 0.1
  }
}
```

## 注意事项

- persona_draft 必须包含**完整**的六个维度，未确定的字段用合理默认值
- progress 字段记录每个维度的完成度（0-1），帮助用户了解进展
- missing_dimensions 列出还需要追问的维度
- 当所有维度进度 > 0.7 时，建议用户保存
- 不要一次性问太多问题，每次对话自然推进 1-2 个维度
- 如果用户上传了知识库文档，在 persona_draft 的 description 中标注来源
"""


@dataclass
class ChatForgeSession:
    """一次对话创建 Persona 的完整会话状态。"""
    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    persona_draft: dict[str, Any] = field(default_factory=lambda: _default_draft())
    knowledge_base: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_expired(self) -> bool:
        # 已知限制：1h 内存过期保留（非本需求范围）
        return datetime.now(timezone.utc) - self.updated_at > timedelta(hours=1)


def _default_draft() -> dict[str, Any]:
    """生成 Persona 草稿的默认值。"""
    return {
        "identity": {"tone": "", "knowledge_domains": [], "class_perspective": ""},
        "language": {"academic_density": 0.1, "slang_ratio": 0.05, "max_sentence_len": 30, "variance_target": 0.6},
        "rhythm": {"cut_profile": "even_flow", "base_shot_duration_ms": 5000, "cut_density_tier": "medium"},
        "visual": {"palette": "neutral", "animation_styles": {}, "transition_weights": {}},
        "audio": {"bgm_slots": {}, "target_loudness_lufs": -16},
        "constraints": {"max_duration_sec": 900, "min_duration_sec": 30},
    }


class ChatForge:
    """对话式 Persona 构建引擎。"""

    # 单段知识库内容的最大字符数，超过则按 Markdown H1 分段读入
    MAX_KB_CHARS: int = 8000
    # B5: 会话落盘目录（每个 session 一个 JSON；重启后恢复）
    SESSIONS_DIR = Path("chatforge_sessions")

    def __init__(self) -> None:
        self._llm = LLMService()
        self._sessions: dict[str, ChatForgeSession] = {}
        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._restore_sessions()

    # ── B5: 会话落盘 ──

    def _session_path(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64] or "s"
        return self.SESSIONS_DIR / f"{safe}.json"

    def _persist_session(self, session: ChatForgeSession) -> None:
        """把会话状态写到磁盘（消息/草稿/知识库）。"""
        try:
            data = {
                "session_id": session.session_id,
                "messages": session.messages,
                "persona_draft": session.persona_draft,
                "knowledge_base": session.knowledge_base,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
            }
            path = self._session_path(session.session_id)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            logger.warning("ChatForge 会话落盘失败 %s: %s", session.session_id, e)

    def _restore_sessions(self) -> None:
        """启动时从磁盘恢复未过期会话。"""
        try:
            for f in self.SESSIONS_DIR.glob("*.json"):
                if f.name.endswith(".tmp"):
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    sid = data.get("session_id", "")
                    if not sid:
                        continue
                    session = ChatForgeSession(
                        session_id=sid,
                        messages=data.get("messages", []),
                        persona_draft=data.get("persona_draft", _default_draft()),
                        knowledge_base=data.get("knowledge_base", []),
                        created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
                        updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(timezone.utc),
                    )
                    if not session.is_expired:
                        self._sessions[sid] = session
                except Exception:
                    continue
        except Exception as e:
            logger.warning("ChatForge 会话恢复失败: %s", e)

    # ── 会话管理 ──

    def _cleanup_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [sid for sid, s in self._sessions.items() if now - s.updated_at > timedelta(hours=1)]
        for sid in expired:
            del self._sessions[sid]

    def _get_or_create_session(self, session_id: Optional[str] = None) -> ChatForgeSession:
        self._cleanup_expired()
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        session = ChatForgeSession(session_id=session_id or uuid.uuid4().hex[:12])
        self._sessions[session.session_id] = session
        return session

    # ── 核心对话 ──

    async def start(self, persona_id: str = "") -> dict[str, Any]:
        """开始一个新的对话会话。仅初始化，不触发 AI 回复。"""
        session = self._get_or_create_session()
        logger.info("ChatForge session started: %s", session.session_id)
        # 不调 LLM，等用户发第一条消息再处理
        return {
            "session_id": session.session_id,
            "reply": "",
            "persona_draft": session.persona_draft,
            "missing_dimensions": [
                "identity", "language", "rhythm", "visual", "audio", "constraints",
            ],
            "progress": {k: 0 for k in (
                "identity", "language", "rhythm", "visual", "audio", "constraints",
            )},
        }

    async def message(
        self,
        session_id: str,
        user_message: str,
        persona_id: str = "",
    ) -> dict[str, Any]:
        """发送一条消息并获取 AI 回复。"""
        session = self._get_or_create_session(session_id)
        session.messages.append({"role": "user", "content": user_message})
        session.updated_at = datetime.now(timezone.utc)
        self._persist_session(session)  # B5
        logger.info("ChatForge message: session=%s, len=%d", session_id, len(user_message))
        return await self._process(session, user_message, persona_id)

    async def add_knowledge(
        self,
        session_id: str,
        content: str,
        source: str = "user_upload",
    ) -> dict[str, Any]:
        """添加知识库内容到会话上下文。

        如果内容超过 MAX_KB_CHARS，自动按 Markdown H1（`# 标题`）分段，
        每段作为独立的知识块存入，AI 逐段分析并累积更新 Persona 草稿。
        """
        session = self._get_or_create_session(session_id)
        chunks = self._split_knowledge(content, source)

        for chunk in chunks:
            session.knowledge_base.append(chunk)

        session.updated_at = datetime.now(timezone.utc)
        self._persist_session(session)  # B5
        total_chars = len(content)

        logger.info("ChatForge knowledge added: session=%s, source=%s, chunks=%d, total_chars=%d",
                     session_id, source, len(chunks), total_chars)

        if len(chunks) == 1:
            # 单块：直接分析
            system_msg = (
                f"[系统] 用户上传了参考文档「{source}」（{total_chars} 字）。"
                f"请分析其中的创作风格、语言习惯、论证结构等特征，更新 Persona 草稿。"
            )
            session.messages.append({"role": "user", "content": system_msg})
            return await self._process(session, system_msg, "")

        else:
            # 多块：逐段送入 AI 累积更新
            headings = [
                c["source"].split(" > ", 1)[-1] for c in chunks
            ]
            intro_msg = (
                f"[系统] 用户上传了参考文档「{source}」（{total_chars} 字），"
                f"包含 {len(chunks)} 个章节：\n" + "\n".join(
                    f"  {i+1}. {h}" for i, h in enumerate(headings)
                ) + "\n将逐段读入分析。"
            )
            session.messages.append({"role": "user", "content": intro_msg})
            result = await self._process(session, intro_msg, "")

            for i, chunk in enumerate(chunks):
                heading = chunk["source"].split(" > ", 1)[-1]
                section_msg = (
                    f"[系统] 第 {i+1}/{len(chunks)} 章：{heading}\n\n"
                    f"{chunk['content']}"
                )
                session.messages.append({"role": "user", "content": section_msg})
                result = await self._process(session, section_msg, "")

            # 全部段落后做一次综合总结
            summary_msg = (
                f"[系统] 「{source}」全部 {len(chunks)} 章已读完。"
                f"请综合所有内容，输出完整的 Persona 草稿。"
            )
            session.messages.append({"role": "user", "content": summary_msg})
            result = await self._process(session, summary_msg, "")

            return result

    async def commit(
        self,
        session_id: str,
        persona_id: str = "",
        persona_name: str = "",
    ) -> PersonaManifest:
        """将当前会话保存为完整的 Persona（含 YAML + Prompt + RAG 知识库）。"""
        session = self._get_or_create_session(session_id)
        repo = PersonaRepository.from_settings()

        # 1. 从对话记录提取 Prompt 指令
        prompt = self._build_prompt_from_session(session.messages)

        # 1b. 从对话记录提取视觉需求 Prompt（画面/视觉相关描述）
        vision_prompt = self._build_vision_prompt_from_session(session.messages)

        # 2. 从知识库提取 KnowledgeDoc 列表
        knowledge_docs = [
            KnowledgeDoc(
                id=f"kb_{i}",
                title=kb["source"],
                content=kb["content"],
                source=kb["source"],
            )
            for i, kb in enumerate(session.knowledge_base)
        ]

        # 3. 编译参数层
        manifest = self._draft_to_manifest(
            session.persona_draft,
            persona_id or f"chat_{session.session_id}",
            persona_name or f"对话创建_{session.session_id[:8]}",
        )

        # 4. 填入 Prompt + RAG
        if prompt:
            manifest.prompt = prompt
        if knowledge_docs:
            manifest.knowledge = knowledge_docs

        # 4b. 填入视觉需求 Prompt（no-clobber：不覆盖已存在的 vision_prompt.md）
        if vision_prompt:
            vision_path = repo.root_dir / manifest.persona_id / "vision_prompt.md"
            if vision_path.exists():
                logger.warning("vision_prompt.md 已存在，ChatForge 不覆盖用户创作内容: %s", vision_path)
            else:
                manifest.vision_prompt = vision_prompt

        # 5. 保存到磁盘
        repo.save_manifest(manifest)

        # 6. 保存对话记录
        transcript_path = repo.root_dir / manifest.persona_id / "chat_transcript.json"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(session.messages, f, ensure_ascii=False, indent=2)

        logger.info("ChatForge commit: session=%s, persona_id=%s, name=%s, knowledge_docs=%d",
                     session_id, manifest.persona_id, persona_name, len(knowledge_docs))

        return manifest

    def get_state(self, session_id: str) -> Optional[dict[str, Any]]:
        """获取当前会话状态。"""
        session = self._sessions.get(session_id)
        if not session:
            return None
        return {
            "session_id": session.session_id,
            "message_count": len([m for m in session.messages if m["role"] == "user"]),
            "persona_draft": session.persona_draft,
            "knowledge_base_count": len(session.knowledge_base),
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }

    # ── 内部 ──

    async def _process(
        self,
        session: ChatForgeSession,
        user_msg: Optional[str],
        persona_id: str,
    ) -> dict[str, Any]:
        """处理消息并返回 AI 回复 + 更新后的 Persona。"""
        if not self._has_api_key():
            logger.warning("ChatForge _process: no API key, using fallback")
            return self._fallback_response(session)

        # 构建消息列表
        system = self._build_system_prompt(session)
        messages = [{"role": "user" if i == 0 and m.get("role") != "system" else m["role"], "content": m["content"]}
                    for i, m in enumerate(session.messages)]

        # 如果用户发送了消息，追加当前消息
        if user_msg:
            pass  # 已在 message() 中追加

        # 调用 LLM（取消防 temperature/max_tokens 等可能不兼容的参数）
        full_messages = [{"role": "system", "content": system}] + messages
        system_len = len(system)
        msg_count = len(full_messages)
        logger.info("ChatForge LLM 请求: system=%d chars, messages=%d 条", system_len, msg_count)
        for i, m in enumerate(full_messages):
            logger.debug("  msg[%d]: role=%s, len=%d", i, m.get("role"), len(m.get("content", "")))
        resp = await self._llm.generate(
            messages=full_messages,
        )

        if not resp.success:
            logger.error("ChatForge LLM 调用失败: status=%d, content=%.200s", resp.status_code or -1, resp.content or "")
            err_detail = f"LLM API 返回 {resp.status_code}，请检查控制台日志"
            if resp.status_code == 500 and resp.content:
                err_detail = f"模型返回错误: {resp.content[:200]}"
            return {
                "session_id": session.session_id,
                "reply": f"抱歉，AI 暂时无法回应。{err_detail}",
                "persona_draft": session.persona_draft,
                "missing_dimensions": [],
                "progress": {},
            }

        # 解析 AI 返回的 JSON（含混合文本处理）
        try:
            raw = resp.content.strip()
            result = self._extract_json_from_llm(raw)

            reply = result.get("reply", "")
            draft_update = result.get("persona_draft", {})

            # 合并更新 Persona 草稿（只覆盖非空字段）
            self._merge_draft(session.persona_draft, draft_update)

            session.messages.append({"role": "assistant", "content": reply})
            session.updated_at = datetime.now(timezone.utc)

            return {
                "session_id": session.session_id,
                "reply": reply,
                "persona_draft": session.persona_draft,
                "missing_dimensions": result.get("missing_dimensions", []),
                "progress": result.get("progress", {}),
            }

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("ChatForge JSON parse failed: %s, content=%.200s", e, resp.content)
            # JSON 解析失败，把原始内容作为 reply
            reply = resp.content
            session.messages.append({"role": "assistant", "content": reply})
            session.updated_at = datetime.now(timezone.utc)
            return {
                "session_id": session.session_id,
                "reply": reply,
                "persona_draft": session.persona_draft,
                "missing_dimensions": [],
                "progress": {},
                "_parse_error": str(e),
            }

    @staticmethod
    def _extract_json_from_llm(raw: str) -> dict:
        """从 LLM 回复中提取 JSON，处理混合文本。

        AI 的回复可能有多种格式：
        - 纯 JSON:          {"reply":"...", "persona_draft":{...}}
        - markdown 代码块:  ```json\n{"reply":"..."}\n```
        - 文本+JSON混合:    "好的，已经更新。\n\n```json\n{...}\n```"
        - 多个代码块:        ```json\n{...}\n```\n补充\n```json\n{...}\n```
        """
        # 策略 1：尝试直接解析
        text = raw.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 策略 2：提取 ```json ... ``` 或 ``` ... ``` 代码块
        code_blocks = re.findall(
            r"```(?:json)?\s*\n?(.*?)\n?```",
            text,
            flags=re.DOTALL,
        )
        for block in code_blocks:
            block = block.strip()
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue

        # 策略 3：在纯文本中寻找 JSON，处理尾部文本
        def _extract_balanced_json(s: str, start: int) -> str | None:
            """从 start 位置找到匹配的闭合大括号（跳过字符串内的 {}）。"""
            if s[start] != "{":
                return None
            depth = 0
            in_str = False
            escape = False
            for i in range(start, len(s)):
                ch = s[i]
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_str:
                    escape = True
                    continue
                if ch == '"' and not escape:
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return s[start : i + 1]
            return None

        def _has_expected_keys(obj: dict) -> bool:
            """检查 JSON 对象是否包含 ChatForge 响应应有的顶层键。"""
            return bool(obj.get("reply")) or bool(obj.get("persona_draft"))

        # 按顺序遍历每个顶层 {...}，直到找到含预期键的 JSON
        brace_starts = [m.start() for m in re.finditer(r"\{", text)]
        for start in brace_starts:
            candidate = _extract_balanced_json(text, start)
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
                if _has_expected_keys(parsed):
                    return parsed
            except json.JSONDecodeError:
                continue

        # 策略 4：用正则找 "reply" 等关键字附近的 JSON
        for keyword in ['"reply"', '"persona_draft"', '"identity"']:
            idx = text.find(keyword)
            if idx == -1:
                continue
            left = text.rfind("{", 0, idx)
            if left == -1:
                continue
            candidate = _extract_balanced_json(text, left)
            if candidate:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue

        # 全部失败：抛出原始错误
        raise json.JSONDecodeError(
            f"Cannot extract JSON from LLM response",
            raw[:200],
            0,
        )

    @staticmethod
    def _split_knowledge(content: str, source: str) -> list[dict[str, str]]:
        """按 Markdown H1 分段知识库内容。

        如果内容超过 MAX_KB_CHARS，按 `# ` 一级标题分行读入；
        否则作为单块返回。
        """
        if len(content) <= ChatForge.MAX_KB_CHARS:
            return [{"source": source, "content": content}]

        # 按 H1 分割：匹配行首的 `# `
        sections = re.split(r"^# ", content, flags=re.MULTILINE)
        chunks: list[dict[str, str]] = []

        for section in sections:
            section = section.strip()
            if not section:
                continue
            lines = section.split("\n", 1)
            heading = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            chunk_source = f"{source} > {heading}"
            # 单段超长时截断
            chunk_body = body[:ChatForge.MAX_KB_CHARS] if len(body) > ChatForge.MAX_KB_CHARS else body
            chunks.append({"source": chunk_source, "content": f"# {heading}\n\n{chunk_body}"})

        # 如果 H1 分割失败（没有任何 #），按段落切分
        if not chunks:
            fallback = content[:ChatForge.MAX_KB_CHARS * 2]
            return [{"source": source, "content": fallback}]

        return chunks

    def _build_system_prompt(self, session: ChatForgeSession) -> str:
        """构建包含知识库上下文的系统提示词。"""
        prompt = CHAT_SYSTEM_PROMPT

        if session.knowledge_base:
            # 按 source 分组，展示完整内容
            groups: dict[str, list[str]] = {}
            for doc in session.knowledge_base:
                src = doc["source"]
                # 去掉 " > 标题" 部分以分组
                base_src = src.split(" > ", 1)[0] if " > " in src else src
                groups.setdefault(base_src, []).append(doc["content"])

            kb_text = "\n\n## 用户上传的参考文档\n"
            for src, contents in groups.items():
                kb_text += f"\n### 📄 {src}\n"
                if len(contents) == 1:
                    kb_text += contents[0] + "\n"
                else:
                    kb_text += f"（共 {len(contents)} 个章节，详见对话历史）\n"
            prompt += kb_text

        # 附上当前 Persona 草稿
        prompt += (
            f"\n\n## 当前 Persona 草稿\n"
            f"```json\n{json.dumps(session.persona_draft, ensure_ascii=False, indent=2)}\n```\n"
        )
        return prompt

    @staticmethod
    def _merge_draft(draft: dict, update: dict) -> None:
        """递归合并 Persona 草稿更新。"""
        for key, value in update.items():
            if isinstance(value, dict) and key in draft and isinstance(draft[key], dict):
                draft[key].update({k: v for k, v in value.items() if v})
            elif value:
                draft[key] = value

    @staticmethod
    def _build_prompt_from_session(messages: list[dict]) -> str:
        """从对话记录生成 Prompt 指令。"""
        if not messages:
            return ""
        lines = ["# ChatForge 对话生成的 Persona Prompt", ""]
        # 提取用户的关键描述（保留完整内容，不截断）
        user_statements = [
            m["content"] for m in messages
            if m.get("role") == "user" and not m["content"].startswith("[系统]")
        ]
        if user_statements:
            lines.append("## 用户风格描述")
            for s in user_statements:
                lines.append(f"- {s}")
            lines.append("")
        lines.append("## 说明")
        lines.append("此 Prompt 由 ChatForge 根据对话记录自动生成，")
        lines.append("包含用户在对话中表达的创作风格偏好，")
        lines.append("可在 AI 视频编排时作为系统指令使用。")
        return "\n".join(lines)

    _VISUAL_KEYWORDS = ("画面", "视觉", "风格", "配色", "颜色", "色调", "字体", "动画", "转场", "特效", "镜头", "滤镜")

    @classmethod
    def _build_vision_prompt_from_session(cls, messages: list[dict]) -> str:
        """从对话记录生成视觉需求 Prompt（画面风格/视觉约束）。

        仅提取用户消息中与 画面/视觉/风格/配色/字体/动画/转场 相关的描述，
        完整保留原文（不截断）。无视觉相关描述时返回空串。
        """
        if not messages:
            return ""
        user_statements = [
            m["content"] for m in messages
            if m.get("role") == "user"
            and not m["content"].startswith("[系统]")
            and any(k in m["content"] for k in cls._VISUAL_KEYWORDS)
        ]
        if not user_statements:
            return ""
        lines = ["# ChatForge 对话生成的视觉需求 Prompt", ""]
        lines.append("## 用户视觉/风格描述")
        for s in user_statements:
            lines.append(f"- {s}")
        lines.append("")
        lines.append("## 说明")
        lines.append("此视觉需求 Prompt 由 ChatForge 根据对话记录自动生成，")
        lines.append("包含用户在对话中表达的画面风格与视觉约束，")
        lines.append("可在结构/动画/MG 生成阶段作为视觉约束注入。")
        return "\n".join(lines)

    @staticmethod
    def _draft_to_manifest(
        draft: dict[str, Any],
        persona_id: str,
        persona_name: str,
    ) -> PersonaManifest:
        """将草稿编译为正式的 PersonaManifest（含安全类型转换）。"""
        i = draft.get("identity") or {}
        l = draft.get("language") or {}
        r = draft.get("rhythm") or {}
        v = draft.get("visual") or {}
        a = draft.get("audio") or {}
        c = draft.get("constraints") or {}

        def _safe_float(val: Any, default: float = 0.0) -> float:
            try: return float(val) if val is not None else default
            except (TypeError, ValueError): return default

        def _safe_int(val: Any, default: int = 0) -> int:
            try: return int(val) if val is not None else default
            except (TypeError, ValueError): return default

        def _safe_str(val: Any, default: str = "") -> str:
            return str(val) if val is not None else default

        def _safe_list(val: Any, default: list | None = None) -> list:
            if isinstance(val, list): return val
            return list(default) if default is not None else []

        def _safe_dict(val: Any, default: dict | None = None) -> dict:
            if isinstance(val, dict): return val
            return dict(default) if default is not None else {}

        def _safe_bgm_slots(val: Any) -> dict[str, list[str]]:
            """标准化 bgm_slots：确保值是 list[str]，非 list 的字符串自动包裹。"""
            if not isinstance(val, dict):
                return {}
            result: dict[str, list[str]] = {}
            for k, v in val.items():
                if isinstance(v, list):
                    result[k] = [str(item) for item in v]
                elif isinstance(v, str):
                    result[k] = [v]
                elif v is not None:
                    result[k] = [str(v)]
            return result

        param = ParameterLayer(
            persona_id=persona_id,
            identity=IdentityConfig(
                tone=_safe_str(i.get("tone"), "neutral"),
                knowledge_domains=_safe_list(i.get("knowledge_domains"), []),
                class_perspective=_safe_str(i.get("class_perspective")),
            ),
            language=LanguageConfig(
                academic_density=_safe_float(l.get("academic_density"), 0.1),
                slang_ratio=_safe_float(l.get("slang_ratio"), 0.05),
                max_sentence_len=_safe_int(l.get("max_sentence_len"), 30),
                variance_target=_safe_float(l.get("variance_target"), 0.6),
            ),
            rhythm=RhythmConfig(
                cut_profile=_safe_str(r.get("cut_profile"), "even_flow"),
                base_shot_duration_ms=_safe_int(r.get("base_shot_duration_ms"), 5000),
                cut_density_tier=_safe_str(r.get("cut_density_tier"), "medium"),
            ),
            visual=VisualConfig(
                palette=_safe_str(v.get("palette"), "neutral"),
                animation_styles=_safe_dict(v.get("animation_styles"), {}),
                transition_weights=_safe_dict(v.get("transition_weights"), {}),
            ),
            audio=AudioConfig(
                bgm_slots=_safe_bgm_slots(a.get("bgm_slots")),
                target_loudness_lufs=_safe_float(a.get("target_loudness_lufs"), -16),
            ),
            constraints=ConstraintsConfig(
                max_duration_sec=_safe_int(c.get("max_duration_sec"), 900),
                min_duration_sec=_safe_int(c.get("min_duration_sec"), 30),
            ),
        )

        return PersonaManifest(
            persona_id=persona_id,
            persona_name=persona_name,
            version="1.0.0",
            parameter=param,
            description="由 ChatForge 对话创建",
        )

    @staticmethod
    def _has_api_key() -> bool:
        from clipwright.config import settings
        return bool(settings.llm_api_key)

    @staticmethod
    def _fallback_response(session: ChatForgeSession) -> dict[str, Any]:
        """无 API key 时的回退响应。"""
        reply = (
            "你好！我是帧艺 ClipWright 的创作风格顾问。🎬\n\n"
            "由于没有配置 API key，我将通过预设问题引导你。\n\n"
            "请描述你的视频创作风格，例如：\n"
            "• 你做什么类型的视频？（知识区/数码评测/Vlog/鬼畜）\n"
            "• 你的说话风格是怎样的？（冷峻/热情/学术/吐槽）\n"
            "• 画面有什么偏好？（色调、文字动画、节奏快慢）\n\n"
            "可以一句句告诉我，我会逐步帮你构建 Persona。"
        )
        session.messages.append({"role": "assistant", "content": reply})
        return {
            "session_id": session.session_id,
            "reply": reply,
            "persona_draft": session.persona_draft,
            "missing_dimensions": ["identity", "language", "rhythm", "visual", "audio", "constraints"],
            "progress": {k: 0 for k in ("identity", "language", "rhythm", "visual", "audio", "constraints")},
        }
