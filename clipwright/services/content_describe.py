"""本地画面描述合成：用**已测到的字段**拼出一句可检索的中文描述。

为什么需要它（真实素材实测暴露）：`visual.description` 此前**只**来自云端多模态或
ImageNet 分类器兜底；而一旦 CLIP 给出标签，分类器分支就被跳过——于是真实素材上
`description` 覆盖率是 **0**，每段素材只剩 1 个标签。后果很直接：

- 检索的可匹配文本极少（关键词路几乎无从下手）；
- 方案接地（R8）在真实素材上 **0/2 命中**——场景的"交代环境的开场画面"这种描述，
  跟"夜景"这种单标签对不上。

本模块把索引里**已经量出来的东西**拼成描述：景别、运镜、明暗、清晰度/曝光异常、
画面标签、语音关键词、音频事件。关键约束：

- **只写测到的东西**。没有测到的维度（比如"这是足球比赛"）**不写**——那是模型做不了的事，
  编出来就是幻觉。所以这是"拼接式描述"（composed），不是 caption；
- **可溯源**：写入索引时 `evidence["visual.description"] = "composed:stats+clip+audio"`，
  一眼能看出它不是模型生成的散文；
- **零成本**：不调模型、不发网络请求，纯字符串组装。
"""

from __future__ import annotations

from typing import Any

#: 景别 → 中文（与 `classify_shot_size` 的输出对应）
_SHOT_SIZE_CN: dict[str, str] = {
    "extreme_close": "大特写", "close": "特写", "medium": "中景",
    "wide": "全景", "full": "远景",
}

#: 运镜 → 中文
_MOTION_CN: dict[str, str] = {
    "static": "画面静止", "low": "缓慢摇动", "medium": "有运镜",
    "high": "镜头运动剧烈",
}

#: 音频事件 → 中文
_AUDIO_CN: dict[str, str] = {
    "loud": "有较大声响", "silence": "有一段安静", "speech": "有人说话",
}


def _stats_measured(stats: Any) -> bool:
    """统计字段是否**真的被测过**。

    踩坑记录：`ShotStats` 的默认值是 0，于是"没测过"和"测到 0"看起来一样——
    对一条裸 `ShotRecord` 合成描述会得到"偏暗的画面"（亮度 0），纯属凭空捏造。
    判据用 `frame_count > 0`（`compute_shot_stats` 只有真跑过才会写它）。
    """
    try:
        return int(getattr(stats, "frame_count", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _brightness_words(stats: Any) -> str:
    if getattr(stats, "is_black", False):
        return "近乎全黑的画面"
    if getattr(stats, "is_white", False):
        return "近乎全白的画面"
    if not _stats_measured(stats):
        return ""
    try:
        value = float(getattr(stats, "brightness", 0.0) or 0.0)
    except (TypeError, ValueError):
        return ""
    if value < 45:
        return "偏暗的画面"
    if value < 95:
        return "中等亮度的画面"
    if value < 190:
        return "明亮的画面"
    return "非常亮的画面"


def _quality_words(stats: Any, blurry_threshold: float = 120.0) -> list[str]:
    out: list[str] = []
    if not _stats_measured(stats):
        return out
    try:
        sharpness = float(getattr(stats, "sharpness", 0.0) or 0.0)
    except (TypeError, ValueError):
        sharpness = 0.0
    if 0 < sharpness < blurry_threshold:
        out.append("画面偏模糊")
    elif sharpness >= blurry_threshold * 4:
        out.append("画面清晰锐利")
    try:
        if float(getattr(stats, "overexposed_ratio", 0.0) or 0.0) > 0.3:
            out.append("有过曝区域")
        if float(getattr(stats, "underexposed_ratio", 0.0) or 0.0) > 0.3:
            out.append("有欠曝区域")
    except (TypeError, ValueError):
        pass
    return out


def compose_description(shot: Any, *, max_tags: int = 4, max_keywords: int = 4) -> str:
    """`ShotRecord` → 一句中文描述（只含已测到的字段）。

    组装顺序刻意"从整体到细节"：画面整体（明暗/景别/运镜）→ 内容标签 → 声音。
    这样即使下游只截前 20 字，拿到的也是最有信息量的部分。
    """
    stats = getattr(shot, "stats", None)
    visual = getattr(shot, "visual", None)
    speech = getattr(shot, "speech", None)
    parts: list[str] = []

    bright = _brightness_words(stats) if stats is not None else ""
    if bright:
        parts.append(bright)
    size = _SHOT_SIZE_CN.get(str(getattr(visual, "shot_size", "") or ""), "")
    if size:
        parts.append(size + "镜头")

    motion = _MOTION_CN.get(str(getattr(visual, "camera_motion", "") or ""), "")
    if motion:
        parts.append(motion)

    if getattr(visual, "has_face", None) is True:
        parts.append("画面里有人脸")
    elif getattr(visual, "has_face", None) is False:
        parts.append("没有检出人脸")

    parts.extend(_quality_words(stats) if stats is not None else [])

    tags = [str(t) for t in (getattr(visual, "tags", None) or []) if str(t).strip()]
    if tags:
        parts.append("画面内容偏向" + "、".join(tags[:max_tags]))

    keywords = [str(k) for k in (getattr(speech, "keywords", None) or []) if str(k).strip()]
    if keywords:
        parts.append("说到" + "、".join(keywords[:max_keywords]))
    elif str(getattr(speech, "text", "") or "").strip():
        parts.append("有旁白或对话")

    events = getattr(shot, "audio_events", None) or []
    kinds: list[str] = []
    for ev in events:
        kind = str((ev or {}).get("kind") or "")
        cn = _AUDIO_CN.get(kind, "")
        if cn and cn not in kinds:
            kinds.append(cn)
    if kinds:
        parts.append("、".join(kinds[:2]))

    if not parts:
        return ""
    return "；".join(parts) + "。"


def should_compose(shot: Any) -> bool:
    """是否需要补描述：**已有描述就不动**（云端/分类器给的更接近人话，优先保留）。"""
    visual = getattr(shot, "visual", None)
    if visual is None:
        return False
    return not str(getattr(visual, "description", "") or "").strip()


def fill_descriptions(
    shots: list[Any], *, blurry_threshold: float = 120.0,
) -> int:
    """给缺少描述的镜头补上合成描述，返回补了几个。"""
    count = 0
    for shot in shots:
        if not should_compose(shot):
            continue
        text = compose_description(shot)
        if not text:
            continue
        shot.visual.description = text
        if not shot.evidence:
            shot.evidence = {}
        shot.evidence["visual.description"] = "composed:stats+clip+audio"
        # 置信度 0.5：景别/明暗/标签是**实测**的，但"拼成一句话"读起来像描述，
        # 容易被误当成模型级理解——置信度刻意压低，且 evidence 里写明来源。
        if not shot.confidence:
            shot.confidence = {}
        shot.confidence["visual.description"] = 0.5
        count += 1
    return count
