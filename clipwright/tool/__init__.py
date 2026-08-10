"""原子能力层（Atomic Capabilities）。

封装 FFmpeg、OpenCV、CLIP、Whisper 等底层工具的调用。
所有 API 入参为纯数值或纯路径，不接受风格描述字符串。

入口：register_builtin_tools() 注册所有内置工具到 ToolRegistry。
"""

from clipwright.tool.animation import TrackingTextTool, TypewriterAnimationTool
from clipwright.tool.animation_list import ListAnimationsTool
from clipwright.tool.describe_llm_mg import DescribeLLMMGTool
from clipwright.tool.audio import (
    AudioExtractTool,
    AudioMixTool,
    AudioNormalizeTool,
    AudioReplaceTool,
    BPMDetectTool,
)
from clipwright.tool.base import BaseTool
from clipwright.tool.chroma_key import ChromaKeyTool
from clipwright.tool.color import ColorCorrectTool, LutApplyTool
from clipwright.tool.effects import (
    BackgroundRemoveTool,
    EffectVignetteTool,
    FaceDetectTool,
    TextDiagramTool,
    TransitionApplyTool,
    VideoBlurTool,
    VideoSpeedTool,
    WatermarkTool,
)
from clipwright.tool.material import FrameValidatorTool, MaterialFilterTool
from clipwright.tool.registry import ToolRegistry
from clipwright.tool.speed import SpeedRampTool
from clipwright.tool.stabilize import VideoStabilizeTool
from clipwright.tool.stubs import (
    AudioSilenceDetectTool,
    BlackFrameDetectTool,
    SubtitleOverflowTool,
    TextDesignTool,
    VideoFilterTool,
    VisionLLMTool,
    WhisperTranscribeTool,
)
from clipwright.tool.voice import TextToSpeechTool, VoiceCloneTool
from clipwright.tool.web_search_tool import WebFetchTool, WebSearchTool
from clipwright.tool.subtitle import SubtitleBurnTool
from clipwright.tool.text_video import GenerateTextVideoTool
from clipwright.tool.video import (
    MediaProbeTool,
    VideoConcatTool,
    VideoCropTool,
    VideoDownloadTool,
    VideoOverlayTool,
    VideoThumbnailTool,
    VideoTrimTool,
)
from clipwright.tool.vision import SceneDetectTool, SemanticMatchTool


def register_builtin_tools() -> None:
    """注册所有内置工具到全局 ToolRegistry。"""
    tools: list[BaseTool] = [
        # ── 视频 ──
        VideoTrimTool(),
        VideoConcatTool(),
        VideoOverlayTool(),
        VideoDownloadTool(),
        VideoCropTool(),
        VideoThumbnailTool(),
        VideoSpeedTool(),
        VideoBlurTool(),
        VideoFilterTool(),
        MediaProbeTool(),
        # ── 音频 ──
        AudioExtractTool(),
        AudioNormalizeTool(),
        AudioMixTool(),
        AudioReplaceTool(),
        BPMDetectTool(),
        # ── 视觉 ──
        SceneDetectTool(),
        SemanticMatchTool(),
        VisionLLMTool(),
        FaceDetectTool(),
        BackgroundRemoveTool(),
        # ── 特效 ──
        EffectVignetteTool(),
        WatermarkTool(),
        ChromaKeyTool(),
        VideoStabilizeTool(),
        # ── 文字 ──
        GenerateTextVideoTool(),
        SubtitleBurnTool(),
        TextDesignTool(),
        TypewriterAnimationTool(),
        TrackingTextTool(),
        TextDiagramTool(),
        # ── 素材 ──
        MaterialFilterTool(),
        FrameValidatorTool(),
        # ── 质量 ──
        BlackFrameDetectTool(),
        AudioSilenceDetectTool(),
        SubtitleOverflowTool(),
        # ── 其他 ──
        SpeedRampTool(),
        ColorCorrectTool(),
        LutApplyTool(),
        TransitionApplyTool(),
        WhisperTranscribeTool(),
        TextToSpeechTool(),
        VoiceCloneTool(),
        ListAnimationsTool(),
        DescribeLLMMGTool(),
        WebSearchTool(),
        WebFetchTool(),
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
    "VideoDownloadTool",
    "VideoCropTool",
    "VideoThumbnailTool",
    "VideoSpeedTool",
    "VideoBlurTool",
    "VideoFilterTool",
    "MediaProbeTool",
    # Audio
    "AudioExtractTool",
    "AudioNormalizeTool",
    "AudioMixTool",
    "AudioReplaceTool",
    "BPMDetectTool",
    # Vision
    "SceneDetectTool",
    "SemanticMatchTool",
    "VisionLLMTool",
    "FaceDetectTool",
    "BackgroundRemoveTool",
    # Effects
    "EffectVignetteTool",
    "WatermarkTool",
    "ChromaKeyTool",
    "VideoStabilizeTool",
    # Text
    "GenerateTextVideoTool",
    "SubtitleBurnTool",
    "TextDesignTool",
    "TypewriterAnimationTool",
    "TrackingTextTool",
    "TextDiagramTool",
    # Material
    "MaterialFilterTool",
    "FrameValidatorTool",
    # Quality
    "BlackFrameDetectTool",
    "AudioSilenceDetectTool",
    "SubtitleOverflowTool",
    # Misc
    "SpeedRampTool",
    "ColorCorrectTool",
    "LutApplyTool",
    "TransitionApplyTool",
    "WhisperTranscribeTool",
    "TextToSpeechTool",
    "VoiceCloneTool",
    "ListAnimationsTool",
    "DescribeLLMMGTool",
    "WebSearchTool",
    "WebFetchTool",
]
