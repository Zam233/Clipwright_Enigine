"""对话式视频编辑服务 — 通过自然语言对话修改已生成的视频。"""

from __future__ import annotations

import json
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.services.llm import LLMService


# 工具/Skill/Agent 的 LLM 可调用描述
_EDIT_CAPABILITIES = [
    {
        "name": "apply_video_filter",
        "description": "调整视频画面的色调/亮度/对比度/饱和度/裁切/旋转",
        "tool": "video_filter",
        "params": {"brightness": "float -1~1", "contrast": "float 0~3", "saturation": "float 0~3", "hue": "float -180~180"},
    },
    {
        "name": "change_text_style",
        "description": "修改视频中所有文字或指定文字的样式：字体/颜色/大小/位置/描边/发光/阴影",
        "tool": "text_design",
        "params": {"text_description": "string 自然语言描述想要的样式"},
    },
    {
        "name": "apply_transition",
        "description": "修改或添加片段之间的转场效果（淡入淡出/滑动/推动/glitch等）",
        "tool": "transition_apply",
        "params": {"transition_type": "string 转场类型名称"},
    },
    {
        "name": "change_video_speed",
        "description": "调整视频片段播放速度（慢动作或快进）",
        "tool": "video_speed",
        "params": {"speed": "float 速度倍率", "clip_index": "int 片段索引"},
    },
    {
        "name": "add_watermark",
        "description": "给视频添加图片或文字水印",
        "tool": "watermark",
        "params": {"text": "string 水印文字", "position": "string 位置"},
    },
    {
        "name": "apply_effect",
        "description": "应用视觉效果：暗角/胶片颗粒/扫描线/深褐色/老电影",
        "tool": "effect_vignette",
        "params": {"effect": "string 效果类型", "intensity": "float 强度"},
    },
    {
        "name": "remove_background",
        "description": "去除或替换视频背景（绿幕抠像/背景模糊）",
        "tool": "background_remove",
        "params": {"method": "string chroma_key/blur"},
    },
    {
        "name": "text_diagram",
        "description": "生成文字图解动画：箭头/高亮/变色，展示概念之间的逻辑关系（A→B→C）",
        "tool": "text_diagram",
        "params": {"items": "list 概念列表", "relations": "list 关系描述"},
    },
    {
        "name": "add_blur",
        "description": "给视频或指定区域添加模糊效果（高斯/像素化/动感）",
        "tool": "video_blur",
        "params": {"blur_type": "string gaussian/pixelate/motion", "radius": "int"},
    },
    {
        "name": "retime_scene",
        "description": "重新生成指定场景的结构/素材/剪辑（调用管线Agent）",
        "tool": "pipeline",
        "params": {"scene_index": "int", "description": "string 修改描述"},
    },
    {
        "name": "change_audio",
        "description": "替换或调整配音/背景音乐",
        "tool": "audio",
        "params": {"action": "string replace/volume/mute", "path": "string 音频路径"},
    },
]


class EditSession:
    """单个视频的编辑会话。"""

    def __init__(self, session_id: str, timeline: Optional[Timeline] = None):
        self.session_id = session_id
        self.timeline = timeline
        self.history: list[dict] = []
        self.current_video_path: str = ""
        self.pipeline_id: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "timeline": self.timeline.model_dump(mode="json") if self.timeline else None,
            "history_count": len(self.history),
            "video_path": self.current_video_path,
        }


class VideoEditor:
    """对话式视频编辑器 — 管理多个编辑会话。"""

    def __init__(self):
        self._sessions: dict[str, EditSession] = {}
        self._llm = LLMService()

    def create_session(self, session_id: str, timeline: Optional[Timeline] = None) -> EditSession:
        session = EditSession(session_id, timeline)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[EditSession]:
        return self._sessions.get(session_id)

    async def process_edit(
        self,
        session_id: str,
        user_input: str,
    ) -> dict[str, Any]:
        """处理用户的自然语言编辑请求。"""
        session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}

        # 1. LLM 将自然语言映射到具体操作
        action = await self._parse_intent(user_input, session)
        logger.info("VideoEditor 解析意图: %s → %s", user_input[:60], action.get("action"))

        if not action.get("action"):
            return {"success": False, "error": "未能理解您的编辑需求", "llm_reply": action.get("reply", "")}

        # 2. 执行操作
        result = await self._execute_action(session, action)

        # 3. 记录历史
        session.history.append({
            "user_input": user_input,
            "action": action,
            "result": result,
        })

        return result

    async def _parse_intent(self, user_input: str, session: EditSession) -> dict:
        """LLM 解析用户意图，映射到可执行操作。包含思考过程。"""
        caps_json = json.dumps(_EDIT_CAPABILITIES, ensure_ascii=False, indent=2)
        timeline_summary = ""
        if session.timeline:
            t = session.timeline
            timeline_summary = (
                f"当前视频: {t.duration_sec:.0f}s, {len(t.tracks)} 轨道, "
                f"共 {sum(len(track.clips) for track in t.tracks)} 个片段"
            )

        prompt = (
            f"你是一个视频编辑助手。根据用户的编辑需求，从以下能力中选择最匹配的一个或多个操作。\n\n"
            f"{timeline_summary}\n\n"
            f"可用能力:\n{caps_json}\n\n"
            f"用户需求: {user_input}\n\n"
            f"请先思考用户的真实意图，然后选择合适的操作。返回 JSON 格式:\n"
            f"{{\n"
            f'  "thinking": "你的思考过程（对用户需求的分析）",\n'
            f'  "action": "能力名称(如 apply_video_filter/change_text_style 等)",\n'
            f'  "params": {{...能力对应的参数...}},\n'
            f'  "reply": "给用户的中文回复，说明即将执行的操作",\n'
            f'  "confidence": 0.0~1.0\n'
            f"}}\n"
            f"如果无法理解需求，action 设为空字符串，reply 说明原因。"
        )

        try:
            resp = await self._llm.ask(prompt)
            result = {"action": "", "reply": "抱歉，未能解析您的编辑需求", "thinking": ""}
            if resp.success and resp.content:
                content = resp.content.strip()
                if content.startswith("```"):
                    lines = content.splitlines()
                    content = "\n".join(lines[1:-1])
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    result.update(parsed)
                    # 推送思考过程到 trace
                    thinking = result.get("thinking", "")
                    if thinking:
                        logger.info("🤔 编辑思考: %s", thinking[:200])
            return result
        except Exception as e:
            logger.warning("VideoEditor intent parse failed: %s", e)

        return {"action": "", "reply": "抱歉，未能解析您的编辑需求，请重新描述", "thinking": ""}

    async def _execute_action(self, session: EditSession, action: dict) -> dict:
        """执行 LLM 解析出的操作。"""
        action_name = action.get("action", "")
        params = action.get("params", {})

        if action_name == "apply_video_filter":
            return await self._exec_tool("video_filter", session.current_video_path, params)

        elif action_name == "change_text_style":
            return await self._exec_tool("text_design", "", params)

        elif action_name == "change_video_speed":
            return await self._exec_tool("video_speed", session.current_video_path, params)

        elif action_name == "apply_transition":
            return await self._exec_tool("transition_apply", "", params)

        elif action_name == "add_watermark":
            return await self._exec_tool("watermark", session.current_video_path, params)

        elif action_name == "apply_effect":
            return await self._exec_tool("effect_vignette", session.current_video_path, params)

        elif action_name == "remove_background":
            return await self._exec_tool("background_remove", session.current_video_path, params)

        elif action_name == "add_blur":
            return await self._exec_tool("video_blur", session.current_video_path, params)

        elif action_name == "retime_scene":
            # 局部重生成场景端点已移除（B4 处置：原按索引替换、前端零调用；语义匹配重做
            # 请走需求对话 /edit 意图或审阅视图「不满意→重做」入口）。
            return {
                "success": False,
                "error": "局部场景重生成端点已移除",
                "llm_reply": "该操作已不再支持，请用审阅的「不满意→重做」或需求对话提出修改。",
            }

        elif action_name == "change_audio":
            # 重新混音
            return await self._exec_tool("audio_mix", session.current_video_path, params)

        else:
            return {"success": False, "error": f"未知操作: {action_name}", "llm_reply": action.get("reply", "")}

    async def _exec_tool(self, tool_name: str, input_path: str, params: dict) -> dict:
        """调用 ToolRegistry 执行。"""
        from clipwright.tool.registry import ToolRegistry
        kwargs = {**params}
        if input_path and "input_path" not in kwargs:
            kwargs["input_path"] = input_path

        result = await ToolRegistry.execute(tool_name, **kwargs)
        output = result.model_dump(mode="json") if hasattr(result, "model_dump") else {"status": str(result.status)}

        return {
            "success": result.status == "success",
            "action": tool_name,
            "output": output,
            "llm_reply": f"已执行 {tool_name}，状态: {result.status}",
        }

    # ── 本地视频编辑工具 ──
