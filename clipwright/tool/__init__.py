"""原子能力层（Atomic Capabilities）。

封装 FFmpeg、OpenCV、CLIP、Whisper 等底层工具的调用。
所有 API 入参为纯数值或纯路径，不接受风格描述字符串。

入口：register_builtin_tools() 注册所有内置工具到 ToolRegistry。
"""

from clipwright.tool.animation import TrackingTextTool, TypewriterAnimationTool
from clipwright.tool.audio import AudioExtractTool, AudioReplaceTool, BPMDetectTool
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.tool.video import VideoConcatTool, VideoOverlayTool, VideoTrimTool
from clipwright.tool.vision import SceneDetectTool, SemanticMatchTool


def register_builtin_tools() -> None:
    """注册所有内置工具到全局 ToolRegistry。"""
    tools: list[BaseTool] = [
        # Video
        VideoTrimTool(),
        VideoConcatTool(),
        VideoOverlayTool(),
        # Audio
        AudioExtractTool(),
        BPMDetectTool(),
        AudioReplaceTool(),
        # Vision
        SceneDetectTool(),
        SemanticMatchTool(),
        # Animation
        TypewriterAnimationTool(),
        TrackingTextTool(),
    ]
    for tool in tools:
        ToolRegistry.register(tool)


__all__ = [
    "BaseTool",
    "ToolRegistry",
    "register_builtin_tools",
    # Video
    "VideoTrimTool",
    "VideoConcatTool",
    "VideoOverlayTool",
    # Audio
    "AudioExtractTool",
    "BPMDetectTool",
    "AudioReplaceTool",
    # Vision
    "SceneDetectTool",
    "SemanticMatchTool",
    # Animation
    "TypewriterAnimationTool",
    "TrackingTextTool",
]
