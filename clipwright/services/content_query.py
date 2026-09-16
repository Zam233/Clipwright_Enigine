"""素材内容检索（R4）——把"索引"变成"能问的问题"。

为什么这一层是关键
------------------
R1–R3 把"素材里有什么"存了下来，但**存下来不等于用得上**：Agent 不会去读 JSON，
它要么需要一段**紧凑上下文**（把素材内容讲给它听），要么需要**一个能问问题的工具**。
本模块提供两者，并且刻意把"为什么命中"一起返回——剪辑决策要能解释，
检索结果也一样（这也让失败可归因：是标签错、还是检索排序错）。

检索是**混合**的（三层信号，按可解释的权重相加）：
1. **结构化过滤**：有没有人脸/景别/时长/黑场/运动/过曝/静音/事件类型——硬条件；
2. **关键词**：标签（精确匹配权重最高）、画面描述、语音文本、语音关键词；
3. **向量**：用项目既有的 RAG 嵌入器（`bge-small-zh-v1.5`）对"镜头文本"做语义相似；
   可选再用 `bge-reranker-v2-m3` 重排。

**为什么向量不放第一**：向量擅长"意思相近"，但对"黑场在哪一秒""最短的镜头"这类
**结构化事实**毫无优势，甚至会给出似是而非的结果。结构化条件当硬过滤、向量只做功不可及
的语义召回，是这个场景的正确分工。

诚实的边界：
- 文本向量召回用的是**镜头文本**（描述+标签+语音），**不含画面**——画面语义要等 R9 的
  CLIP/SigLIP 视觉向量；
- 真实素材上的召回质量未验证（本机无真实素材，且 R2 已实测合成帧上的标签不可信）；
- 向量结果在进程内缓存（键 = 文本哈希），不落盘：换进程会重算（几十个镜头 <1s）。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger, settings
from clipwright.schema.content import ContentIndex, ShotRecord, format_shot_ref

#: 融合权重：语义与关键词各占一半，结构化条件做硬过滤（不计分）
W_EMBED = 0.55
W_KEYWORD = 0.45
#: R9：画面语义分权重。**不压低前两项**——文本路在"用户搜的就是镜头文字里的词"时
#: 是最准的（关键词精确匹配），视觉路的价值在"用户换了说法"。
#: 三项权重之和 > 1 是有意的：分数只用于排序与阈值，不解释成概率。
W_VISUAL = 0.5
#: CLIP 余弦的"零点"（经验值）：不相关的图文对也常在 0.14–0.20。
#:
#: **真实素材实测修正**（2026-09-14，4 段 Pexels 素材）：命中的镜头原始余弦约
#: 0.24–0.27，不相关的约 0.14–0.24——两者**有重叠**，所以单靠"减一个零点"
#: 不足以分辨。因此零点只用于把分数拉到 0 附近，真正的**分辨**交给
#: `VISUAL_FLOOR`（进不进候选）与集合内归一化（排序）。
VISUAL_BASELINE = 0.18
#: 视觉命的**下限**：低于它的镜头不进候选。
#: 真实素材实测：正确镜头 ≥ 0.24，错误镜头 ≤ 0.24（4 段素材、1 镜头/段的小样本，
#: 这个阈值需要在更大素材集上重标）。
VISUAL_FLOOR = 0.22
#: 文本向量（bge）的命中下限。bge 的中文短文本余弦普遍偏高（0.3–0.6），
#: 0.35 以下是"几乎无关"那一档。
EMBED_FLOOR = 0.35
#: 关键词里标签精确命中的权重（标签是人/模型给的强信号）
TAG_WEIGHT = 1.0
TEXT_WEIGHT = 0.6
#: 低于该分不进结果（避免"什么都能召回"）
MIN_SCORE = 0.05

_EMBED_CACHE: dict[str, list[float]] = {}
_EMBED_CACHE_MAX = 4096


# ── 索引加载 ──────────────────────────────────────────────


def load_indexes(
    project_id: str | None = None,
    *,
    sha256: str = "",
    include_library: bool = True,
) -> list[ContentIndex]:
    """加载本地索引（可用 `sha256` 限定单素材；默认项目库 + 公共素材库）。

    读索引是纯文件操作，不做任何模型调用——所以"总结/过滤"这类问答是**零成本**的。
    """
    from clipwright.services import content_index as ci

    out: list[ContentIndex] = []
    seen: set[str] = set()
    targets: list[tuple[str | None, list[Path]]] = []
    if sha256:
        targets.append((project_id, [ci.index_path(sha256, project_id)]))
        if include_library and project_id:
            targets.append((None, [ci.index_path(sha256, None)]))
    else:
        if project_id:
            targets.append((project_id, ci.local_index_files(project_id)))
        if include_library:
            targets.append((None, ci.local_index_files(None)))
    for pid, files in targets:
        for path in files:
            if not path.exists():
                continue
            idx = ci.load_index(path.stem, pid)
            if idx is None or idx.sha256 in seen:
                continue
            seen.add(idx.sha256)
            out.append(idx)
    return out


def assets_overview(indexes: list[ContentIndex]) -> list[dict[str, Any]]:
    """索引总览（一行一个素材，给 Agent 看"手上有什么"）。"""
    rows: list[dict[str, Any]] = []
    for idx in indexes:
        s = idx.summary(max_shots=0)
        rows.append({
            "ref": f"asset:{idx.sha256[:12]}",
            "sha256": idx.sha256[:12],
            "filename": idx.filename,
            "duration_sec": s["duration_sec"],
            "shot_count": s["shot_count"],
            "with_face": s["shots_with_face"],
            "black_shots": s["black_shots"],
            "with_speech": s["shots_with_speech"],
            "top_tags": [t["tag"] for t in s["top_tags"][:5]],
        })
    return rows


# ── 结构化过滤 ────────────────────────────────────────────


def filter_shots(
    index: ContentIndex,
    *,
    has_face: bool | None = None,
    shot_size: str | Iterable[str] | None = None,
    motion: str | Iterable[str] | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
    is_black: bool | None = None,
    is_white: bool | None = None,
    min_brightness: float | None = None,
    max_brightness: float | None = None,
    max_underexposed: float | None = None,
    min_overexposed: float | None = None,
    max_overexposed: float | None = None,
    min_sharpness: float | None = None,
    max_sharpness: float | None = None,
    tags: Iterable[str] | None = None,
    has_speech: bool | None = None,
    event_kinds: Iterable[str] | None = None,
    time_range: tuple[float, float] | None = None,
) -> list[ShotRecord]:
    """按结构化条件筛镜头。**只做能直接从索引字段判定的条件**（不猜、不模糊匹配）。"""

    def _as_set(value: Any) -> set[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return {value}
        return {str(v) for v in value}

    sizes, motions = _as_set(shot_size), _as_set(motion)
    tag_set = _as_set(tags)
    kinds = _as_set(event_kinds)
    out: list[ShotRecord] = []
    for shot in index.shots:
        st, vis = shot.stats, shot.visual
        if has_face is not None and bool(vis.has_face) is not has_face:
            continue
        if sizes and vis.shot_size not in sizes:
            continue
        if motions and vis.camera_motion not in motions:
            continue
        if min_duration is not None and shot.duration_sec < min_duration:
            continue
        if max_duration is not None and shot.duration_sec > max_duration:
            continue
        if is_black is not None and st.is_black is not is_black:
            continue
        if is_white is not None and st.is_white is not is_white:
            continue
        if min_brightness is not None and st.brightness < min_brightness:
            continue
        if max_brightness is not None and st.brightness > max_brightness:
            continue
        if max_underexposed is not None and st.underexposed_ratio > max_underexposed:
            continue
        if min_overexposed is not None and st.overexposed_ratio < min_overexposed:
            continue
        if max_overexposed is not None and st.overexposed_ratio > max_overexposed:
            continue
        if min_sharpness is not None and st.sharpness < min_sharpness:
            continue
        if max_sharpness is not None and st.sharpness > max_sharpness:
            continue
        if tag_set and not (tag_set & set(vis.tags)):
            continue
        if has_speech is not None and bool(shot.speech.text) is not has_speech:
            continue
        if kinds and not (kinds & {str(ev.get("kind")) for ev in shot.audio_events}):
            continue
        if time_range is not None:
            lo, hi = time_range
            if shot.t1 <= lo or shot.t0 >= hi:
                continue
        out.append(shot)
    return out


# ── 关键词与向量打分 ──────────────────────────────────────


def _tokenize(text: str) -> set[str]:
    """查询/文本切词：CJK 二元组 + ASCII 词（与 R3 的关键词抽取同口径）。"""
    text = (text or "").lower()
    tokens: set[str] = set()
    for run in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", text):
        if len(run) == 1:
            tokens.add(run)
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    for word in re.findall(r"[a-z][a-z0-9_'\-]{1,}", text):
        tokens.add(word)
        tokens.add(re.sub(r"['\-]", "", word))
    return tokens


def keyword_score(shot: ShotRecord, query: str) -> tuple[float, list[str]]:
    """标签精确命中 > 文本命中；返回 (0-1 分, 命中说明)。"""
    q = (query or "").strip().lower()
    if not q:
        return (0.0, [])
    hits: list[str] = []
    tag_hits = [t for t in shot.visual.tags if t and t.lower() in q]
    if tag_hits:
        hits.append("标签命中 " + "/".join(tag_hits[:3]))
    q_tokens = _tokenize(q)
    corpus = " ".join([
        shot.visual.description, " ".join(shot.visual.tags),
        " ".join(shot.visual.objects), " ".join(shot.visual.actions),
        shot.speech.text, " ".join(shot.speech.keywords),
    ]).lower()
    text_hits = [tok for tok in q_tokens if len(tok) >= 2 and tok in corpus]
    if text_hits:
        hits.append("关键词命中 " + "/".join(sorted(text_hits)[:4]))
    score = 0.0
    if tag_hits:
        score = max(score, TAG_WEIGHT)
    if text_hits:
        coverage = len(text_hits) / max(1, len(q_tokens))
        score = max(score, TEXT_WEIGHT * min(1.0, coverage * 2))
    return (min(1.0, score), hits)


def _visual_adj(raw: float) -> float:
    """CLIP 原始余弦 → 可用于排序的分数（减零点；负值压到 0）。"""
    return max(0.0, float(raw) - VISUAL_BASELINE) / max(1e-6, 1.0 - VISUAL_BASELINE)


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """用项目 RAG 嵌入器编码（带进程内缓存；不可用返回空）。"""
    if not texts:
        return []
    try:
        from clipwright.rag.embedder import get_embedder

        embedder = get_embedder()
        todo = [t for t in texts if _hash(t) not in _EMBED_CACHE]
        if todo:
            vectors = embedder.embed(todo)
            for text, vec in zip(todo, vectors):
                if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
                    _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)), None)
                _EMBED_CACHE[_hash(text)] = list(vec)
        return [_EMBED_CACHE[_hash(t)] for t in texts]
    except Exception as exc:
        logger.info("嵌入不可用（检索退化为关键词）：%s", exc)
        return []


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    try:
        import numpy as np

        va, vb = np.asarray(a, dtype="float32"), np.asarray(b, dtype="float32")
        na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:
        return 0.0


# ── 检索主入口 ────────────────────────────────────────────


#: 结构化条件 → 自然语言说法的路由表（R4）。
#: 用户不会说 `is_black=true`，他会说"黑场在哪"。没有这张表，"用 NLP 问素材"就只是个口号：
#: 关键词检索对"黑场""有没有人脸"这类**结构化事实**毫无办法（这些词根本不在镜头文本里）。
#: 每条规则都是"说法 → 条件"，可读、可测、可扩展；命中后会把该说法从查询词里剔除，
#: 避免它去污染关键词打分。
_ROUTE_RULES: tuple[tuple[tuple[str, ...], dict[str, Any]], ...] = (
    (("黑场", "全黑", "黑屏", "黑帧"), {"is_black": True}),
    (("白场", "全白"), {"is_white": True}),
    # "过曝"是**局部**过曝（高光溢出），不等于全白画面：用像素占比阈值更贴近用户本意
    # （实测："过曝的镜头"曾因路由成 is_white 而一个都筛不出来）
    (("过曝", "曝光过度", "高光溢出"), {"min_overexposed": 0.3}),
    (("人脸", "露脸", "出镜"), {"has_face": True}),
    (("没有人", "无人", "空镜", "空景"), {"has_face": False}),
    (("特写", "近景"), {"shot_size": ["extreme_close", "close"]}),
    (("中景",), {"shot_size": ["medium"]}),
    (("全景", "远景", "大景"), {"shot_size": ["wide", "full"]}),
    (("掌声", "欢呼", "鼓掌", "大响", "突然变响", "噪音"), {"event_kinds": ["loud"]}),
    (("静音", "没声音", "无声"), {"event_kinds": ["silence"]}),
    (("有语音", "说话", "讲话", "口播", "有人声"), {"has_speech": True}),
    (("静止", "静置", "不动"), {"motion": ["static", "low"]}),
    (("剧烈", "运动大", "晃动", "摇镜"), {"motion": ["medium", "high"]}),
    # "运动/动感"这类**不带程度词**的说法（"有哪些画面是运动的"）。
    # 实测 R4 的路由没覆盖它，于是"运动的"被当成口水词清掉、清单查询把**所有**镜头
    # 都列了出来——看着有输出、其实没过滤。这类问题只有跑真链路才会暴露（R10 demo 发现）。
    (("运动的", "运动幅度", "动感", "有动", "在动"), {"motion": ["medium", "high"]}),
)
#: 排序意图（"最长的镜头"不是过滤条件，而是排序）
_ORDER_RULES: tuple[tuple[tuple[str, ...], tuple[str, bool]], ...] = (
    (("最长", "最久", "时间最长"), ("duration", True)),
    (("最短", "时间最短"), ("duration", False)),
    (("最亮",), ("brightness", True)),
    (("最暗",), ("brightness", False)),
    (("最清晰", "最锐", "最清楚"), ("sharpness", True)),
    (("最模糊", "最糊"), ("sharpness", False)),
    (("运动最", "最剧烈", "动作最大"), ("motion", True)),
)
#: 模糊/锐度类说法 → 直接给个可用的锐度上界（Laplacian 方差经验值）
_BLURRY_HINT_WORDS = ("模糊", "糊", "失焦", "不清楚")
#: "列出来/有几个"这类**清单式**问法：命中后按结构化条件直接给清单，不要求文本命中
_LIST_HINTS = ("列出", "列表", "所有镜头", "全部镜头", "一共", "多少", "几个", "哪些", "都有哪些")
#: 中文里的口水词：路由后要清掉，否则"最长的那一个镜头"会残留"那一个"当查询词，
#: 把本该是排序查询的问题变成一个必然落空的文本匹配
_FILLER_TOKENS = ("那一个", "这一个", "这个", "那个", "一段", "一些", "其中",
                  "里面", "当中", "时候", "是什么", "有没有", "在第几秒",
                  "哪些", "哪个", "哪一个", "一共", "多少个", "列表", "列出",
                  "给我", "帮我", "看看", "的", "在哪")


def route_query(question: str) -> dict[str, Any]:
    """把自然语言问题路由成 `{filters, order_by, list_all, clean_query}`（纯函数）。

    返回的 `clean_query` 已剔除命中的结构化说法——否则"哪些镜头有人脸"里的
    "镜头/人脸"会当成关键词去和镜头文本比，把本该是清单查询的问题变成弱匹配。
    """
    q = (question or "").strip()
    filters: dict[str, Any] = {}
    order: tuple[str, bool] | None = None
    for words, cond in _ROUTE_RULES:
        hit = [w for w in words if w in q]
        if hit:
            for key, value in cond.items():
                filters.setdefault(key, value)
            for w in hit:
                q = q.replace(w, " ")
    for words, order_by in _ORDER_RULES:
        hit = [w for w in words if w in q]
        if hit and order is None:
            order = order_by
            for w in hit:
                q = q.replace(w, " ")
    # "模糊的镜头"是**条件**（锐度低）而不是排序；取一个经验上界（可被设置覆盖）
    if any(w in q for w in _BLURRY_HINT_WORDS):
        filters.setdefault("max_sharpness", settings.content_index_blurry_sharpness)
        q = " ".join(q.replace(w, " ") for w in _BLURRY_HINT_WORDS)
    list_all = any(hint in question for hint in _LIST_HINTS)
    cleaned = q
    for token in _FILLER_TOKENS:
        cleaned = cleaned.replace(token, " ")
    clean = re.sub(r"\s+", " ", cleaned.replace("的镜头", " ").replace("镜头", " ")
                   .replace("素材", " ").replace("这段", " ").replace("那一段", " ")
                   .replace("有没有", " ").replace("是什么", " ").replace("在哪", " ")).strip()
    return {"filters": filters, "order_by": order, "list_all": list_all,
            "clean_query": clean}


def _list_why(filters: dict[str, Any], order: tuple[str, bool] | None) -> list[str]:
    """清单命的"为什么"：条件命中 / 排序依据 / 纯清单——三者可解释地拼出来。"""
    why: list[str] = []
    if filters:
        why.append("结构化条件命中")
    if order:
        why.append(f"按{order[0]}排序")
    return why or ["清单查询"]


def _order_shots(shots: list[ShotRecord], order_by: tuple[str, bool] | None) -> list[ShotRecord]:
    if not order_by:
        return sorted(shots, key=lambda s: s.t0)
    field_name, desc = order_by

    def _key(s: ShotRecord) -> float:
        if field_name == "duration":
            return s.duration_sec
        if field_name == "brightness":
            return s.stats.brightness
        if field_name == "sharpness":
            return s.stats.sharpness
        if field_name == "motion":
            return s.stats.motion
        return s.t0

    return sorted(shots, key=_key, reverse=bool(desc))


async def search_shots(
    query: str,
    *,
    project_id: str | None = None,
    sha256: str = "",
    indexes: list[ContentIndex] | None = None,
    top_k: int = 8,
    use_embeddings: bool = True,
    rerank: bool = True,
    filters: dict[str, Any] | None = None,
    order_by: tuple[str, bool] | None = None,
    route: bool = True,
    min_score: float = MIN_SCORE,
) -> dict[str, Any]:
    """自然语言检索镜头 → 带证据的命中列表（Agent 与工具共用同一入口）。

    三态语义（都实测过）：
    1. **显式条件**（调用方给 `filters`）：条件即答案，文本不匹配也返回（`why=结构化条件命中`）；
    2. **自然语言里的结构化意图**（`route=True`）：由 `route_query` 路由成条件；
       若是清单式问法（"哪些/列出/一共…"）则直接给**符合条件的有序清单**；
    3. 其余按混合打分（结构化过滤 → 关键词 + 向量 → 可选重排）。
    """
    routed = route_query(query) if route else {"filters": {}, "order_by": None,
                                               "list_all": False, "clean_query": query}
    # 显式条件优先：调用方给了 filters 就按它执行（路由只负责排序与文本清理）。
    # 合并会造出"路由条件 + 显式条件"的交集，实测把"黑场在哪"+has_face 变成空集，
    # 与调用方意图相反。
    eff_filters = dict(filters) if filters else dict(routed["filters"])
    eff_order = order_by or routed["order_by"]
    text_query = routed["clean_query"] if route else query
    # "还有没有查询词"用**有效词元**判断（CJK 需 ≥2 字）：口水词清完后残留的
    # 单个"的/里/有"不构成查询意图，若按裸字符串判断会把清单查询挡在门外
    has_content_tokens = bool(_tokenize(text_query))

    idxs = indexes if indexes is not None else load_indexes(project_id, sha256=sha256)
    if not idxs:
        return {"query": query, "hits": [], "total_candidates": 0,
                "note": "没有可用索引（先对素材建索引）", "summary": ""}

    candidates: list[tuple[ContentIndex, ShotRecord]] = []
    for idx in idxs:
        shots = filter_shots(idx, **eff_filters) if eff_filters else list(idx.shots)
        candidates.extend((idx, s) for s in shots)
    if not candidates:
        return {"query": query, "hits": [], "total_candidates": 0,
                "filters": eff_filters,
                "note": "结构化条件没有筛出任何镜头", "summary": ""}

    def _as_hit(idx: ContentIndex, shot: ShotRecord, score: float,
                why: list[str], **extra: Any) -> dict[str, Any]:
        hit = {
            "ref": format_shot_ref(idx.sha256, shot.index),
            "sha256": idx.sha256[:12], "filename": idx.filename,
            "index": shot.index, "t0": shot.t0, "t1": shot.t1,
            "duration_sec": shot.duration_sec,
            "text": shot.text_for_embedding()[:160],
            "score": round(score, 4),
            "why": why or ["结构化条件命中"],
        }
        hit.update(extra)
        return hit

    # ── 清单式查询：条件/排序即答案（不要求文本命中）──
    # 触发条件（任一）：用户明确要清单（"哪些/列出/一共…"）；或剔除结构化说法后
    # **没有剩下任何查询词**——"最长的镜头""按时间列出"都属于后者：
    # 排序诉求本身就是答案，硬要它去和镜头文本打分只会得到空结果（实测踩过）。
    if routed["list_all"] or not has_content_tokens:
        ordered = _order_shots([s for _i, s in candidates], eff_order)
        owners = {id(s): i for i, s in candidates}
        hits = [_as_hit(owners[id(s)], s, 1.0, _list_why(eff_filters, eff_order))
                for s in ordered[: max(1, top_k)]]
        return {"query": query, "hits": hits, "total_candidates": len(candidates),
                "filters": eff_filters, "order_by": eff_order, "list_mode": True,
                "embedding_used": False,
                "summary": render_hits(query, hits, len(candidates))}

    scored: list[dict[str, Any]] = []
    texts = [s.text_for_embedding() or f"镜头{s.index}" for _idx, s in candidates]
    vectors: list[list[float]] = []
    query_vec: list[float] = []
    if use_embeddings and text_query and has_content_tokens:
        query_vec = await asyncio.to_thread(lambda: (_embed_texts([text_query]) or [[]])[0])
        if query_vec:
            vectors = await asyncio.to_thread(_embed_texts, texts)
        if not query_vec or len(vectors) != len(candidates):
            vectors = []

    # ── R9：画面语义分（CLIP 视觉向量 vs 文本查询）──
    # 这一路看的是**像素**：文本描述里没写、但画面确实是那个样子的镜头，
    # 只有它能找出来（R4 的文本向量只覆盖镜头文字，覆盖不到画面）。
    # 没有向量（未建/模型不可用）时整段跳过，行为与 R7 之前完全一致。
    visual_scores: dict[str, float] = {}
    visual_used = False
    if text_query and has_content_tokens:
        try:
            from clipwright.services import content_semantic as sem

            have_vec = [s for _i, s in candidates
                        if (s.embeddings or {}).get(sem.VISUAL_MODALITY)]
            if have_vec:
                raw = await asyncio.to_thread(
                    sem.semantic_scores, text_query,
                    [s for _i, s in candidates])
                if raw:
                    visual_used = True
                    for i, value in raw.items():
                        idx, shot = candidates[i]
                        visual_scores[format_shot_ref(idx.sha256, shot.index)] = float(value)
        except Exception as exc:  # noqa: BLE001 — 视觉路失败不该挡住文本检索
            logger.info("画面语义分不可用，本次检索退化为文本路：%s", exc)

    for i, (idx, shot) in enumerate(candidates):
        kw, why = keyword_score(shot, text_query)
        emb = _cosine(query_vec, vectors[i]) if vectors and i < len(vectors) else 0.0
        emb = max(0.0, emb)
        vis = visual_scores.get(format_shot_ref(idx.sha256, shot.index), 0.0)
        vis_adj = _visual_adj(vis)

        # 三路信号各自出分，融合分只用于**排序**；"进不进候选"由各路自己的下限决定。
        #
        # 为什么要改（真实素材实测）：原先用"融合分 ≥ min_score(0.05)"当门槛，
        # 而 CLIP 视觉分的量级本来就小（真实素材命中约 abt 0.09，乘 W_VISUAL=0.5 后 0.044），
        # 于是**只有视觉命中的正确结果被整体过滤掉**——实测"找海边的镜头"排在第一位的是
        # 海浪镜头，却因为 0.044 < 0.05 返回 0 结果。绝对阈值套在"尺度随信号组合变化"
        # 的融合分上，是这个缺陷的根因。
        score = W_KEYWORD * kw + (W_EMBED * emb if vectors else 0.0) \
            + (W_VISUAL * vis_adj if visual_used else 0.0)
        if emb > 0.2:
            why.append(f"语义相似 {emb:.2f}")
        if visual_used and vis >= VISUAL_FLOOR:
            why.append(f"画面像 {vis:.2f}")

        # 显式条件命中：即使三路都不匹配也要留下（否则"黑场在哪"永远问不出结果）
        if eff_filters:
            scored.append(_as_hit(idx, shot, max(score, min_score),
                                  ["结构化条件命中"] + why,
                                  keyword=round(kw, 4), embedding=round(emb, 4),
                                  visual=round(vis, 4) if visual_used else 0.0))
            continue

        passed = kw > 0 or emb >= EMBED_FLOOR or (visual_used and vis >= VISUAL_FLOOR)
        if not passed:
            continue
        scored.append(_as_hit(idx, shot, score, why,
                              keyword=round(kw, 4), embedding=round(emb, 4),
                              visual=round(vis, 4) if visual_used else 0.0))
    # 排序型问题（"最长/最亮/最糊…"）若一个文本分都没打出来，说明问题里剩下的只是口水词——
    # 此时排序本身就是答案，直接给有序清单（实测："最长的那一个镜头"曾因此返回空）。
    # **必须在 `not scored` 提前返回之前**判断，否则会先掉进"没有得分"的分支。
    if eff_order and not any(h.get("keyword") or h.get("embedding") for h in scored):
        ordered = _order_shots([s for _i, s in candidates], eff_order)
        owners = {id(s): i for i, s in candidates}
        hits = [_as_hit(owners[id(s)], s, 1.0, _list_why(eff_filters, eff_order))
                for s in ordered[: max(1, top_k)]]
        return {"query": query, "hits": hits, "total_candidates": len(candidates),
                "filters": eff_filters, "order_by": eff_order, "list_mode": True,
                "embedding_used": bool(vectors),
                "summary": render_hits(query, hits, len(candidates))}
    if not scored:
        # 「为什么一个都没命中」必须说清楚：三路各自可能缺席，
        # 而"嵌入门面挂了"与"素材里确实没有"是**完全不同的两件事**
        # （实测：本机 embedder 指向一个无效的云端 key，检索静默退化为"只剩视觉分"，
        #  表现就是所有查询都返回 0 命中——不说清原因根本查不出来）。
        reasons: list[str] = []
        if not vectors:
            reasons.append("文本向量不可用（嵌入后端未就绪或调用失败）")
        if not visual_used:
            reasons.append("画面向量不可用（索引未建视觉向量或 CLIP 缺失）")
        note = f"没有任何镜头命中（门槛：关键词>0 / 文本向量≥{EMBED_FLOOR} / 画面≥{VISUAL_FLOOR}）"
        if reasons:
            note += "；降级原因：" + "、".join(reasons)
        return {"query": query, "hits": [], "total_candidates": len(candidates),
                "filters": eff_filters, "embedding_used": bool(vectors),
                "visual_used": visual_used, "degraded": reasons,
                "note": note, "summary": ""}

    scored.sort(key=lambda h: h["score"], reverse=True)
    if eff_order:
        order_field, desc = eff_order
        key_map = {"duration": "duration_sec"}
        field_name = key_map.get(order_field, order_field)
        scored.sort(key=lambda h: h.get(field_name, 0), reverse=bool(desc))
    top = scored[: max(1, top_k)]
    if rerank and len(scored) > 1 and len(top) > 1:
        top = await _maybe_rerank(text_query or query, scored, top_k)
    return {
        "query": query,
        "hits": top,
        "total_candidates": len(candidates),
        "filters": eff_filters,
        "order_by": eff_order,
        "embedding_used": bool(vectors),
        "visual_used": visual_used,
        "summary": render_hits(query, top, len(candidates)),
    }


async def _maybe_rerank(query: str, scored: list[dict[str, Any]],
                        top_k: int) -> list[dict[str, Any]]:
    """可选重排（复用 RAG 的 CrossEncoder）；不可用/失败时保持原序。"""
    try:
        from clipwright.rag.reranker import Reranker

        head = scored[: max(top_k * 3, top_k)]
        cands = [{"text": h["text"], "score": h["score"], "meta": h} for h in head]
        reranked = await Reranker().rerank(query, cands, top_k=top_k)
        out: list[dict[str, Any]] = []
        for item in reranked or []:
            meta = (item.get("meta") if isinstance(item, dict) else None) or {}
            hit = dict(meta) if meta else {}
            if not hit:
                continue
            if isinstance(item, dict) and item.get("score") is not None:
                hit["rerank_score"] = round(float(item["score"]), 4)
                hit["why"] = list(hit.get("why") or []) + ["重排命中"]
            out.append(hit)
        return out or scored[:top_k]
    except Exception as exc:
        logger.debug("重排不可用（保持原序）：%s", exc)
        return scored[:top_k]


def render_hits(query: str, hits: list[dict[str, Any]], total: int) -> str:
    """把命中压成短文本（直接进提示词；不塞 JSON）。"""
    if not hits:
        return ""
    lines = [f"查询“{query}”命中 {len(hits)}/{total} 个镜头："]
    for h in hits:
        lines.append(f"  [{h['t0']:.1f}-{h['t1']:.1f}s] {h['filename']} 镜头{h['index']} "
                     f"（{h['score']:.2f}）{h.get('text', '')[:40]} ← {'；'.join(h.get('why') or [])}")
    return "\n".join(lines)


async def shot_at(
    t: float, *, project_id: str | None = None, sha256: str = "",
) -> dict[str, Any] | None:
    """按时间问"这一刻是什么镜头"（毫秒级事实查询，零模型）。"""
    for idx in load_indexes(project_id, sha256=sha256):
        shot = idx.shot_at(float(t))
        if shot is not None:
            return {
                "ref": format_shot_ref(idx.sha256, shot.index),
                "sha256": idx.sha256[:12], "filename": idx.filename,
                "index": shot.index, "t0": shot.t0, "t1": shot.t1,
                "text": shot.text_for_embedding()[:160],
                "shot_size": shot.visual.shot_size,
                "has_face": shot.visual.has_face,
                "camera_motion": shot.visual.camera_motion,
                "is_black": shot.stats.is_black,
                "audio_events": [ev.get("kind") for ev in shot.audio_events],
                "speech": shot.speech.text[:120],
            }
    return None


async def answer(
    question: str,
    *,
    project_id: str | None = None,
    sha256: str = "",
    top_k: int = 5,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """"问一句"的统一入口：先结构化条件，再混合检索（供工具与上下文注入使用）。"""
    return await search_shots(question, project_id=project_id, sha256=sha256,
                              top_k=top_k, filters=filters)


def index_context_text(
    project_id: str | None = None,
    *,
    max_assets: int = 3,
    max_shots: int = 8,
) -> str:
    """素材内容上下文（给 Agent 读的紧凑段落）；无索引时返回 ""。

    刻意短：**只讲"有什么 + 少量样本镜头"**。把整份索引塞进提示词既贵又没用——
    Agent 需要的是"知道存在什么、能问什么"，细节用它自己的 `content_query` 工具查。
    """
    indexes = load_indexes(project_id)
    if not indexes:
        return ""
    lines: list[str] = []
    for idx in indexes[:max_assets]:
        head = (f"素材 {idx.filename or idx.sha256[:12]}：{len(idx.shots)} 个镜头 / "
                f"{idx.duration_sec:.1f}s / {idx.width}x{idx.height}")
        s = idx.summary(max_shots=0)
        bits = []
        if s["shots_with_face"]:
            bits.append(f"{s['shots_with_face']} 段有人脸")
        if s["black_shots"]:
            bits.append(f"{s['black_shots']} 段黑场")
        if s["shots_with_speech"]:
            bits.append(f"{s['shots_with_speech']} 段有语音")
        if s["shot_sizes"]:
            bits.append("景别 " + "/".join(f"{k}:{v}" for k, v in list(s["shot_sizes"].items())[:3]))
        if s["top_tags"]:
            bits.append("标签 " + "、".join(t["tag"] for t in s["top_tags"][:5]))
        if bits:
            head += "（" + "；".join(bits) + "）"
        lines.append(head)
        for shot in idx.shots[:max_shots]:
            lines.append("    " + shot.brief(idx.sha256))
    if len(indexes) > max_assets:
        lines.append(f"（另有 {len(indexes) - max_assets} 个素材已建索引）")
    return "\n".join(lines)


__all__ = [
    "answer", "assets_overview", "filter_shots", "index_context_text",
    "keyword_score", "load_indexes", "render_hits", "search_shots", "shot_at",
]
