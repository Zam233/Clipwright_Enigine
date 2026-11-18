"""material prompts — 从 prompts.py 按域拆出。"""
from __future__ import annotations

MATERIAL_QUERY_SINGLE_HEAD = (
    "你是一个视频素材搜索关键词生成器。根据以下场景信息，生成 3-5 个具体、可搜索的视觉关键词，"
    "用于在视频素材库中搜索合适的 B-roll 画面。\n\n"
    "场景标题: {scene_title}\n"
    "关键词: {keywords}\n"
    "场景描述: {description}\n\n"
    "要求：\n"
    "- 每个关键词必须是**具体的视觉画面**（如'城市夜景''键盘打字''街头涂鸦'），"
    "不能是抽象概念（如'社会矛盾''心理防御'）\n"
    "- 优先推荐实拍风格的画面\n"
    "- 用中文输出，每行一个关键词\n"
    "- 只输出关键词，不要序号和说明"
)

MATERIAL_QUERY_BATCH_HEAD = (
    "你是一个视频素材搜索关键词生成器。为以下每个场景分别生成 3-5 个具体、"
    "可搜索的视觉关键词，用于在视频素材库搜索 B-roll 画面。\n\n"
)

MATERIAL_QUERY_BATCH_TAIL = (
    "\n\n要求：\n"
    "- 每个关键词必须是**具体的视觉画面**（如'城市夜景''键盘打字'），不能是抽象概念\n"
    "- 优先推荐实拍风格画面\n"
)

MATERIAL_QUERY_BATCH_EN = (
    "- **关键词一律用英文**（图库索引是英文，中文查询命中为 0）；"
    "确有必要可附中文对照，格式：english1/english2/中文对照\n"
    "- 按场景编号输出，每行格式：\"场景N: keyword1/keyword2/keyword3\"\n"
    "- 只输出关键词行，不要额外说明"
)

MATERIAL_ENGLISH_HEAD = (
    "把下面用于视频素材库（Pexels）检索的中文关键词翻成**英文检索词**。\n"
    "要求：只输出英文关键词，用 / 分隔，保持顺序与个数一致，不要解释。\n\n"
)
