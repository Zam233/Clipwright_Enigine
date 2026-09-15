"""R9：真语义检索——用 CLIP 视觉向量做"文搜图/文搜镜头"。

R4 的检索层有一个**如实标注的缺口**：向量只用于**文本**语义召回（镜头描述/关键词的
bge 向量），**画面本身没有向量**——于是"找一段有海边的镜头"只能命中
`visual.description` 里恰好写了"海边"的镜头，而"画面是海边、没人描述过"的镜头
一条都搜不到。本模块补的就是这一块：**从画面像素算向量**。

设计要点：

- **向量存在索引里**，不每次重算（`ShotRecord.embeddings["clip"]`）。10 分钟素材
  建一次索引约几百毫秒的 CLIP 前向，之后检索是纯 numpy 点积。
- **和 R4 的文本检索是互补的两路，不是替换**：结构化条件（黑场/时长/事件）仍是硬过滤，
  关键词精确匹配仍是强信号（用户搜"代码"时字面命中就是对的），视觉向量负责
  "换一种说法也能找到"。三条路各自出分，加权融合。
- **CLIP 不可用时如实降级**：返回空向量 + 检索退化为原来的文本路，
  并在结果里标注 `visual_vectors: 0`——不假装"语义匹配已经生效"
  （`tool/vision.py` 的 `semantic_match` 之前就是靠假 0.5 分撑着的，这里把它换掉）。
- **中文查询**：CLIP 的文本塔是英文语料训的，直接喂中文效果差。所以走
  **中→英查询改写**（`QUERY_GLOSS` 词表 + 可选 LLM），这一点必须显式写出来，
  否则"中文搜得好"会被误读成"CLIP 懂中文"。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable, Optional

from clipwright.schema.content import ContentIndex, ShotRecord

logger = logging.getLogger(__name__)

#: 视觉向量的模态键（存 `ShotRecord.embeddings` 里；换模型时旧向量会失效）
VISUAL_MODALITY = "clip"
#: 一次前向多少张图
BATCH_SIZE = 16

#: 中文 → CLIP 文本塔能用的英文提示词。
#:
#: **这不是"CLIP 懂中文"**：CLIP 的文本塔在英文语料上训练，中文提示词得分明显更差
#: （`scripts/verify_semantic_search.py` 里有"直送中文"的对照）。做法是先把中文画面词
#: 映射成英文再编码。**表外的词原样保留**（可能不准），但不假装它一定准。
#:
#: 顺序有意义：多字词在前、单字颜色词在后（"青色"要先于"青"匹配到）。
QUERY_GLOSS: dict[str, str] = {
    # 颜色（单字颜色词在中文里几乎只作颜色用，所以敢用单字匹配；
    # "青"是唯一有歧义的——"青年"也会命中，实测这类歧义在本域可接受）
    "红色": "red", "大红": "bright red", "通红": "bright red", "偏红": "reddish",
    "蓝色": "blue", "天蓝": "sky blue", "偏蓝": "bluish",
    "绿色": "green", "偏绿": "greenish",
    "黄色": "yellow", "偏黄": "yellowish",
    "紫色": "purple", "橙色": "orange", "灰色": "gray", "青色": "cyan",
    "红": "red", "蓝": "blue", "绿": "green", "黄": "yellow",
    "紫": "purple", "橙": "orange", "灰": "gray", "青": "cyan",
    # 明暗
    "暗": "dark", "亮": "bright", "明亮": "bright", "昏暗": "dim",
    # 场景
    "海边": "seaside beach ocean", "大海": "ocean sea waves", "海滩": "beach sand sea",
    "城市": "city skyline street", "夜景": "city at night neon lights",
    "街道": "city street road", "车流": "traffic cars on road",
    "球场": "sports field stadium", "足球": "soccer football field",
    "篮球": "basketball court game", "进球": "goal celebration sports",
    "观众": "crowd audience cheering", "庆祝": "celebration cheering people",
    "跑步": "person running outdoors", "运动": "sports action person",
    "美食": "food dish meal", "食物": "food meal plate", "面条": "noodles bowl food",
    "咖啡": "coffee cup cafe", "厨房": "kitchen cooking", "烹饪": "cooking in kitchen",
    "人脸": "close up of a person face", "人物": "person portrait",
    "特写": "extreme close up detail", "全景": "wide shot landscape",
    "风景": "landscape nature scenery", "山": "mountains landscape",
    "森林": "forest trees nature", "天空": "sky clouds", "日落": "sunset sky",
    "室内": "indoor room interior", "办公室": "office desk workspace",
    "电脑": "computer laptop desk", "手机": "smartphone in hand",
    "屏幕": "screen display monitor", "图表": "chart graph data",
    "文字": "text on screen document", "书本": "book reading pages",
    "宠物": "pet cat dog animal", "孩子": "child kid playing",
    "产品": "product on white background", "开箱": "unboxing product box",
    "机械": "machine gears mechanism", "工厂": "factory industrial machine",
    "夜晚": "night dark scene", "白天": "daylight bright scene",
    "雨": "rain raindrops", "雪": "snow winter", "火": "fire flames",
    "水": "water flowing", "花": "flowers blooming",
    "条纹": "stripes pattern", "棋盘": "checkerboard pattern",
    "渐变": "gradient colors", "噪点": "noise texture",
}

#: 提示词模板（CLIP 零样本的标准做法——多模板平均比单模板稳）。
#: 颜色/材质这类"形容词式"概念尤其明显：单用"a photo of red" 会偏，加几个说法能拉回来。
PROMPT_TEMPLATES: tuple[str, ...] = (
    "a photo of {}",
    "an image of {}",
    "a picture with {}",
    "{}",
)


def gloss_terms(text: str) -> list[str]:
    """命中的英文描述词（去重，保持词表顺序）。

    只返回**命中的英文词**，不把剩下的中文一起拼进去：CLIP 的 tokenizer 会把未登录的
    中文字符编成未知词，混进去只会稀释信号（实测"找红色的画面"里的"找/的/画面"
    会让颜色查询明显变差）。一个词都没命中时才回退到原文。
    """
    out: list[str] = []
    raw = str(text or "")
    for zh, en in QUERY_GLOSS.items():
        if zh in raw and en not in out:
            out.append(en)
    return out


def gloss_query(text: str) -> str:
    """中文画面词 → 英文提示词串（表外原样返回）。"""
    terms = gloss_terms(text)
    if terms:
        return " ".join(terms)
    return " ".join(str(text or "").split())


def text_prompts(text: str, *, gloss: bool = True) -> list[str]:
    """查询 → 一组待编码的提示词（多模板集成，提升 CLIP 零样本稳健性）。"""
    if not gloss:
        return [str(text or "").strip()] if str(text or "").strip() else []
    terms = gloss_terms(text)
    if not terms:
        single = " ".join(str(text or "").split())
        return [single] if single else []
    prompts: list[str] = []
    for term in terms:
        for tpl in PROMPT_TEMPLATES:
            candidate = tpl.format(term)
            if candidate not in prompts:
                prompts.append(candidate)
    return prompts


# ── 向量计算 ──────────────────────────────────────────────


def _clip_loaded() -> bool:
    from clipwright.services.content_vision import clip_available

    return clip_available()


def _feature_tensor(output: Any) -> Any:
    """从 transformers 的输出里取出**投影后**的向量张量。

    踩坑记录：transformers 5.x 起 `get_image_features()` / `get_text_features()`
    返回的是 `BaseModelOutputWithPooling` 而不是张量（旧版是张量），直接 `.norm()`
    会抛 `'BaseModelOutputWithPooling' object has no attribute 'norm'`。
    这里兼容三种形态：张量 / `pooler_output`（5.x 的投影结果）/ `*_embeds`（部分版本）。
    """
    if hasattr(output, "image_embeds") or hasattr(output, "text_embeds"):
        for attr in ("image_embeds", "text_embeds"):
            value = getattr(output, attr, None)
            if value is not None:
                return value
    pooler = getattr(output, "pooler_output", None)
    if pooler is not None:
        return pooler
    return output


def _normalize(feats: Any) -> Any:
    return feats / feats.norm(dim=-1, keepdim=True).clamp(min=1e-8)


def embed_images(image_paths: list[str]) -> list[list[float]]:
    """图片 → CLIP 图像向量（L2 归一化）；CLIP 不可用返回空列表。

    失败**不抛异常**：索引构建不该因为少一个模型的权重就整条挂掉。
    """
    if not image_paths:
        return []
    from clipwright.services.content_vision import _load_clip  # noqa: PLC0415

    loaded = _load_clip()
    if not loaded:
        return []
    model, proc = loaded
    try:
        import torch
        from PIL import Image

        out: list[list[float]] = []
        for start in range(0, len(image_paths), BATCH_SIZE):
            batch = image_paths[start:start + BATCH_SIZE]
            images = []
            for path in batch:
                with Image.open(path) as img:
                    images.append(img.convert("RGB"))
            if not images:
                continue
            inputs = proc(images=images, return_tensors="pt")
            with torch.no_grad():
                feats = _feature_tensor(model.get_image_features(pixel_values=inputs["pixel_values"]))
            feats = _normalize(feats)
            out.extend([[round(float(v), 6) for v in row] for row in feats.tolist()])
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("CLIP 图像向量计算失败，语义检索将退化为文本路：%s", exc)
        return []


def embed_texts(texts: list[str]) -> list[list[float]]:
    """文本 → CLIP 文本向量（L2 归一化）；CLIP 不可用返回空列表。

    调用方应先过 `gloss_query()`——CLIP 的文本塔不认识中文。
    """
    if not texts:
        return []
    from clipwright.services.content_vision import _load_clip  # noqa: PLC0415

    loaded = _load_clip()
    if not loaded:
        return []
    model, proc = loaded
    try:
        import torch

        inputs = proc(text=list(texts), return_tensors="pt", padding=True, truncation=True)
        with torch.no_grad():
            feats = _feature_tensor(model.get_text_features(
                input_ids=inputs["input_ids"],
                attention_mask=inputs.get("attention_mask")))
        feats = _normalize(feats)
        return [[round(float(v), 6) for v in row] for row in feats.tolist()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("CLIP 文本向量计算失败：%s", exc)
        return []


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    """余弦相似度（向量已归一化时就是点积；这里仍做除法以容忍未归一的输入）。"""
    av, bv = list(a), list(b)
    if not av or not bv or len(av) != len(bv):
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv))
    na = math.sqrt(sum(x * x for x in av))
    nb = math.sqrt(sum(y * y for y in bv))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return dot / (na * nb)


# ── 索引里的视觉向量 ──────────────────────────────────────


def representative_frame(shot: ShotRecord) -> str:
    """镜头的代表帧：优先取中间那帧（首帧常是转场残影，末帧常是黑场起手）。"""
    frames = [f for f in (shot.keyframes or []) if f]
    if not frames:
        return ""
    return frames[len(frames) // 2]


def index_has_visual_vectors(index: ContentIndex) -> bool:
    return any((shot.embeddings or {}).get(VISUAL_MODALITY) for shot in (index.shots or []))


def attach_visual_vectors(
    index: ContentIndex, *, force: bool = False, max_shots: int = 0,
) -> tuple[ContentIndex, int]:
    """给索引里的镜头补视觉向量，返回 `(索引, 本次新算的镜头数)`。

    向量存进 `ShotRecord.embeddings[VISUAL_MODALITY]`，随索引一起落盘——
    检索时就只是点积，不再碰模型。`force=True` 重算（换模型后需要）。
    """
    shots = [s for s in (index.shots or []) if force or not (s.embeddings or {}).get(VISUAL_MODALITY)]
    if max_shots:
        shots = shots[:max_shots]
    pairs = [(s, representative_frame(s)) for s in shots]
    pairs = [(s, f) for s, f in pairs if f]
    if not pairs:
        return index, 0
    vectors = embed_images([f for _s, f in pairs])
    if not vectors or len(vectors) != len(pairs):
        logger.info("视觉向量不可用（CLIP 缺失或计算失败），索引保持纯文本形态")
        return index, 0
    for (shot, _frame), vec in zip(pairs, vectors):
        if not shot.embeddings:
            shot.embeddings = {}
        shot.embeddings[VISUAL_MODALITY] = vec
    return index, len(pairs)


def attach_all(indexes: list[ContentIndex], *, force: bool = False) -> tuple[int, int]:
    """给多个索引补向量，返回 `(处理索引数, 新算镜头数)`。"""
    done = 0
    total = 0
    for idx in indexes:
        idx2, count = attach_visual_vectors(idx, force=force)
        if idx2 is idx:
            done += 1
        total += count
    return done, total


# ── 语义检索 ──────────────────────────────────────────────


def semantic_scores(
    query: str, shots: list[ShotRecord], *, gloss: bool = True,
) -> dict[int, float]:
    """查询 → `{镜头下标: 相似度}`（只含真正有向量的镜头）。

    `gloss=False` 可用于验证"中文直接送 CLIP"有多差（验收脚本用它做对照）。
    """
    if not query or not shots:
        return {}
    prompts = text_prompts(query, gloss=gloss)
    if not prompts:
        return {}
    vecs = embed_texts(prompts)
    if not vecs:
        return {}
    # 多模板平均（先归一化再平均再归一化）——CLIP 零样本的标准做法
    dim = len(vecs[0])
    if any(len(v) != dim for v in vecs):
        return {}
    avg = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in avg))
    qvec = [x / norm for x in avg] if norm > 1e-12 else avg
    out: dict[int, float] = {}
    dim_bad = 0
    for i, shot in enumerate(shots):
        vec = (shot.embeddings or {}).get(VISUAL_MODALITY)
        if not vec:
            continue
        if len(vec) != len(qvec):
            # 维度不匹配（索引里的向量是另一个模型/另一版本算的）。
            # **必须吵出来**：静默按 0 分处理会让人以为"语义检索没命中"，
            # 实际是向量根本不可比——这两者的排查方向完全不同。
            dim_bad += 1
            continue
        out[i] = cosine(qvec, vec)
    if dim_bad:
        logger.warning("视觉向量维度与当前模型不一致（%d 个镜头，期望 %d 维）——"
                       "这些镜头被跳过；请用 --force 重建索引向量",
                       dim_bad, len(qvec))
    return out


def rank_shots(
    query: str,
    indexes: list[ContentIndex],
    *,
    top_k: int = 10,
    min_score: float = 0.15,
) -> list[dict[str, Any]]:
    """跨索引语义排序，返回 `[{ref, sha256, shot_index, score, description}]`。

    `min_score` 的作用与 R8 的"得分为 0 不算命中"一致：CLIP 的余弦相似度**总有正值**，
    不设下限的话再不相干的画面也会被排进结果里。下限只是"不要明显无关的"，
    不代表分数高的就一定相关（真实效果见验收脚本）。
    """
    from clipwright.schema.content import format_shot_ref

    hits: list[dict[str, Any]] = []
    for idx in indexes:
        shots = list(idx.shots or [])
        scored = semantic_scores(query, shots)
        for i, score in scored.items():
            if score < min_score:
                continue
            shot = shots[i]
            hits.append({
                "ref": format_shot_ref(str(idx.sha256), i),
                "sha256": str(idx.sha256),
                "asset_path": str(idx.asset_path),
                "shot_index": i,
                "t0": float(shot.t0),
                "t1": float(shot.t1),
                "score": round(float(score), 4),
                "description": str((shot.visual.description if shot.visual else "") or ""),
                "tags": list((shot.visual.tags if shot.visual else []) or []),
            })
    hits.sort(key=lambda h: (-h["score"], h["ref"]))
    return hits[:top_k]


def search_available(indexes: list[ContentIndex]) -> tuple[bool, int]:
    """`(CLIP 是否可用, 已带视觉向量的镜头数)`——调用方据此如实标注检索能力。"""
    vectors = sum(1 for idx in indexes for s in (idx.shots or [])
                  if (s.embeddings or {}).get(VISUAL_MODALITY))
    return _clip_loaded(), vectors


def visual_coverage(indexes: list[ContentIndex]) -> float:
    """视觉向量覆盖率（0-1）——"这一刻能不能做语义检索"的诚实答案。"""
    total = sum(len(idx.shots or []) for idx in indexes)
    if not total:
        return 0.0
    have = sum(1 for idx in indexes for s in (idx.shots or [])
               if (s.embeddings or {}).get(VISUAL_MODALITY))
    return round(have / total, 4)


def ensure_vectors_for_query(
    indexes: list[ContentIndex], *, project_id: Optional[str] = None,
) -> int:
    """检索前兜底补向量（老索引里没有向量时才动手，算完回写落盘）。"""
    missing = [idx for idx in indexes if not index_has_visual_vectors(idx)]
    if not missing:
        return 0
    _done, count = attach_all(missing)
    if count:
        try:
            from clipwright.services import content_index as ci

            for idx in missing:
                if index_has_visual_vectors(idx):
                    ci.save_index(idx, project_id)
        except Exception as exc:  # noqa: BLE001 — 回写失败不影响本次检索
            logger.info("视觉向量回写索引失败（本次检索仍可用）：%s", exc)
    return count
