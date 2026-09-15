"""R8：方案接地——让规划书里的每个场景**指名道姓地**引用真实素材镜头。

要解决的问题很具体：R4 之后 Agent 已经"知道素材里有什么"，但规划书里的场景仍然
只有一句自然语言描述（`description` / `keywords`），下游（素材 Agent / 剪辑 Agent）
拿到的还是**文字**。于是"方案里引用的素材"和"时间线上真正用的素材"是两条独立的
链路——方案写得漂亮、剪出来却是另一回事，用户核对不上。

本模块把这条链路接起来：**场景 → 具体镜头引用**（`asset:{sha}#{n}` 形式，来自 R1–R3
建的内容索引）。关键取舍：

- **引用由索引确定性地产生，不由 LLM 编**。LLM 编素材路径是幻觉重灾区
  （R5 已经实测过它编 op 的能力），所以 `material_refs` 一律走检索层算出来；
  LLM 只负责写"这一场要什么画面"（`description`/`keywords`），不负责说"用哪个文件"。
- **命中不足就如实留白**：某个场景一条都没匹配到时写 `material_gap` 说明"库里没有
  这类画面"，而不是硬塞一个最接近的凑数（凑数的引用比没有引用更坏——它会让
  下游以为有素材，最后剪出一个不相干的镜头）。
- **校验是一条不变量**：`validate_material_refs()` 保证"方案里引用的素材 100% 存在"，
  这也是本轮验收指标（引用不存在的素材占比 = 0）。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from clipwright.schema.content import ContentIndex
from clipwright.services.content_query import filter_shots, route_query

logger = logging.getLogger(__name__)

#: 每个场景最多钉几个镜头引用（太多就等于没有重点，也让下游无从取舍）
MAX_REFS_PER_SCENE = 3
#: 场景文本里参与检索的字段
_TEXT_FIELDS = ("visual_description", "description", "title", "shot_intent", "keywords")


def scene_query_text(scene: dict[str, Any]) -> str:
    """把场景里"要什么画面"的字段拼成一句检索词。

    `visual_description` 优先（R4 之后 StructureAgent 会写它），其次是描述/标题/关键词。
    刻意把 `keywords` 放最后：它是给素材搜索用的短词，语义密度低但噪音少。
    """
    parts: list[str] = []
    for field in _TEXT_FIELDS:
        value = scene.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            parts.extend(str(v).strip() for v in value if str(v).strip())
    return " ".join(parts)[:400]


def _ref_of(idx: ContentIndex, shot: Any) -> str:
    from clipwright.schema.content import format_shot_ref

    index = int(getattr(shot, "index", 0) or 0)
    return format_shot_ref(str(getattr(idx, "sha256", "")), index)


def _shot_index_map(indexes: Iterable[ContentIndex]) -> dict[str, tuple[ContentIndex, Any]]:
    """`ref → (索引, 镜头)`，用于校验引用是否真实存在。"""
    out: dict[str, tuple[ContentIndex, Any]] = {}
    for idx in indexes:
        for shot in getattr(idx, "shots", []) or []:
            out[_ref_of(idx, shot)] = (idx, shot)
    return out


def valid_refs(indexes: Iterable[ContentIndex]) -> set[str]:
    return set(_shot_index_map(indexes))


def ground_scene(
    scene: dict[str, Any],
    indexes: list[ContentIndex],
    *,
    max_refs: int = MAX_REFS_PER_SCENE,
    keep_existing: bool = True,
) -> dict[str, Any]:
    """给单个场景钉上 `material_refs`（就地生成副本，不改原对象）。

    `keep_existing=True` 时保留场景自带的合法引用（人工指定或上一轮已接地的结果），
    只对"没有引用 / 引用全是假的"的场景重新检索。
    """
    out = dict(scene)
    known = valid_refs(indexes)
    existing = [str(r) for r in (scene.get("material_refs") or []) if str(r) in known]
    if existing and keep_existing:
        out["material_refs"] = existing[:max_refs]
        out.pop("material_gap", None)
        return out

    query = scene_query_text(scene)
    if not query or not indexes:
        out["material_refs"] = []
        out["material_gap"] = "没有素材索引或场景无文本描述，无法接地"
        return out

    routed = route_query(query)
    filters = routed.get("filters") or {}
    clean = (routed.get("clean_query") or "").strip() or query

    # 两遍检索：**先按结构化条件硬过滤，零命中就退化为纯文本匹配**。
    #
    # 为什么必须退化：场景描述里天然会出现"全景/特写"这类词，`route_query` 会把它们
    # 变成 `shot_size=wide` 之类的硬条件；但 R2 明确对看不出景别的镜头写 `unknown`
    # （不编造），于是"整库只有一个 `wide`"这种真实情形会让硬过滤把素材全滤掉——
    # 明明库里有关键词高度匹配的画面，却报"没有可用素材"。退化的成本只是"匹配没那么精准"，
    # 而错误地说"没有素材"会让 Agent 去编一个无关镜头，代价大得多。
    passes: list[tuple[dict[str, Any], str]] = []
    if filters:
        passes.append((filters, "filtered"))
    passes.append(({}, "degraded" if filters else "text"))
    hits: list[tuple[ContentIndex, Any]] = []
    used_filters: dict[str, Any] = {}
    level = "text"
    for flt, tag in passes:
        collected: list[tuple[ContentIndex, Any]] = []
        for idx in indexes:
            shots = filter_shots(idx, **flt) if flt else list(getattr(idx, "shots", []) or [])
            for shot in shots:
                collected.append((idx, shot))
        if collected:
            hits, used_filters, level = collected, flt, tag
            break
    if not hits:
        out["material_refs"] = []
        out["material_gap"] = (
            f"素材库里没有满足条件的画面（检索词「{clean}」"
            + (f" + 结构化条件 {filters}" if filters else "") + "）"
        )
        return out

    # 关键词/标签打分（纯规则、零成本、可解释），取前 N 个
    from clipwright.services.content_query import keyword_score

    scored: list[tuple[float, ContentIndex, Any]] = []
    for idx, shot in hits:
        score, _why = keyword_score(shot, clean)
        scored.append((float(score), idx, shot))
    scored.sort(key=lambda item: (-item[0], _ref_of(item[1], item[2])))

    # **得分为 0 的不算命中**：退化到"全库"之后，如果连一个关键词/标签都对不上，
    # 随便挑前 3 个镜头就是"凑数引用"——下游会以为这一场有素材，剪出来却完全不相关。
    # 宁可如实说"库里没有这类画面"，让上层去补素材或改写方案。
    positive = [item for item in scored if item[0] > 0]
    if not positive:
        out["material_refs"] = []
        out["material_gap"] = f"素材库里没有与「{clean}」相关的画面（关键词与标签均未命中）"
        return out

    refs: list[str] = []
    scores: dict[str, float] = {}
    for score, idx, shot in positive:
        ref = _ref_of(idx, shot)
        if ref not in refs:
            refs.append(ref)
            scores[ref] = round(score, 3)
        if len(refs) >= max_refs:
            break
    out["material_refs"] = refs
    out["material_scores"] = scores
    out["material_match"] = level
    if level == "degraded":
        out["material_note"] = "结构化条件没有命中（可能是索引缺少景别/人脸等字段），已退化为按文本匹配"
    else:
        out.pop("material_note", None)
    if not refs:
        out["material_gap"] = "检索没有命中任何镜头"
    else:
        out.pop("material_gap", None)
    return out


def ground_scenes(
    scenes: list[dict[str, Any]],
    indexes: list[ContentIndex],
    *,
    max_refs: int = MAX_REFS_PER_SCENE,
) -> tuple[list[dict[str, Any]], list[str]]:
    """批量接地，返回 `(新场景列表, 说明)`。"""
    if not scenes:
        return [], []
    notes: list[str] = []
    if not indexes:
        notes.append("没有内容索引，规划书无法引用真实素材（material_refs 为空）")
    out: list[dict[str, Any]] = []
    hits = 0
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        grounded = ground_scene(scene, indexes, max_refs=max_refs)
        if grounded.get("material_refs"):
            hits += 1
        elif grounded.get("material_gap"):
            notes.append(f"场景[{i}] 未接上素材：{grounded['material_gap']}")
        out.append(grounded)
    if indexes:
        notes.insert(0, f"规划书接地：{hits}/{len(out)} 个场景引用了真实素材镜头")
    return out, notes


def validate_material_refs(
    scenes: Iterable[dict[str, Any]], indexes: list[ContentIndex],
) -> list[str]:
    """校验"方案里引用的素材是否都存在"，返回问题清单（空 = 全部有效）。

    这是 R8 的验收指标所对应的函数：**引用不存在的素材占比必须为 0**。
    只认"看起来像引用但不是合法 ref"和"ref 在索引里查不到"两种情况；
    场景本来就没引用（`material_refs` 为空 + 有 `material_gap` 说明）不算错——
    库里确实没有就如实留白，硬编一个才是错。
    """
    known = valid_refs(indexes)
    problems: list[str] = []
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            problems.append(f"场景[{i}] 不是对象")
            continue
        refs = scene.get("material_refs")
        if refs is None:
            continue
        if not isinstance(refs, list):
            problems.append(f"场景[{i}] material_refs 不是列表：{refs!r}")
            continue
        for ref in refs:
            text = str(ref)
            if text not in known:
                problems.append(f"场景[{i}] 引用了不存在的素材：{text}")
        if refs and scene.get("material_gap"):
            problems.append(f"场景[{i}] 既引用了素材又声明缺口，语义矛盾")
    return problems


def refs_to_clip_metadata(scene: dict[str, Any]) -> dict[str, Any]:
    """场景 → 剪辑片段 metadata（EditAgent 落时间线时带上，便于回查"这一刀用了哪个镜头"）。"""
    meta: dict[str, Any] = {}
    refs = scene.get("material_refs") or []
    if refs:
        meta["material_refs"] = [str(r) for r in refs][:MAX_REFS_PER_SCENE]
    gap = scene.get("material_gap")
    if gap:
        meta["material_gap"] = str(gap)
    return meta


def summary_text(scenes: Iterable[dict[str, Any]], limit: int = 8) -> str:
    """一行一场景的接地摘要（给人看，也可塞进提示词让下游知道"这一场用哪些镜头"）。"""
    lines: list[str] = []
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        title = str(scene.get("title") or f"场景{i}")
        refs = scene.get("material_refs") or []
        if refs:
            lines.append(f"- {title}：{'、'.join(str(r) for r in refs)}")
        elif scene.get("material_gap"):
            lines.append(f"- {title}：无可用素材（{scene['material_gap']}）")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def load_project_indexes(project_id: Optional[str]) -> list[ContentIndex]:
    """读项目（+公共库）索引；任何异常都退化为空列表——接地失败不该让管线挂掉。"""
    try:
        from clipwright.services.content_query import load_indexes

        return load_indexes(project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取内容索引失败，规划书接地跳过：%s", exc)
        return []
