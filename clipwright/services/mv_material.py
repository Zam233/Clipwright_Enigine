"""MV 素材识别与落位（PRD C13 / S-01…S-04）。

三条轨道在这里收口成**纯函数**（真正的索引/检索仍由 ``content_index`` / ``content_query``
负责，本模块只做"面向 MV 的那层翻译"）：

- **S-01 标签集**：把一句歌词/一个场景映射成 MV 标签（手部特写、舞台、纸纹理…）；
- **S-02 乐句级 query**：由歌词行 + 标签生成检索 query；
- **S-03 落位**：候选镜头打分、按拍点吸附、**记录依据**（``matched_by`` + 分数）；
  没有任何候选达标时报 ``material_gap``，**不许用无关素材凑数**；
- **S-04 授权**：授权不明的素材不进成片（可进"待确认"清单）。

全部确定性：同输入同输出，无随机、无时钟。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: MV 标签集（S-01）。键是人话，值是同义词（用于匹配素材描述）。
MV_TAGS: dict[str, tuple[str, ...]] = {
    "hand_closeup": ("手", "手部", "手指", "手掌", "伸手", "手心", "hand", "fingers", "grip"),
    "stage": ("舞台", "现场", "演出", "stage", "concert", "live"),
    "paper_texture": ("纸", "纸张", "纹理", "paper", "texture", "grain"),
    "product_closeup": ("产品", "特写", "开箱", "product", "closeup", "unbox"),
    "data_chart": ("图表", "数据", "跑分", "chart", "graph", "data"),
    "face_eye": ("眼睛", "眼神", "脸部", "eye", "face", "gaze"),
    "city": ("城市", "街道", "夜景", "city", "street", "town"),
    "nature": ("自然", "海", "山", "天空", "nature", "sea", "sky"),
    "silhouette": ("剪影", "人影", "背影", "silhouette", "shadow", "figure"),
    "text_overlay": ("文字", "字幕", "排版", "text", "caption", "type"),
    "light_glow": ("光", "光斑", "发光", "light", "glow", "flare"),
    "water_rain": ("雨", "水", "湿润", "rain", "water", "wet"),
}

#: 落位的最低分（低于此分视为"没有合适素材"）。
MIN_MATCH_SCORE = 0.35


@dataclass(frozen=True)
class MaterialCandidate:
    """一条候选素材（来自素材库/内容索引）。"""

    asset_id: str
    description: str = ""
    duration_sec: float = 0.0
    tags: tuple[str, ...] = ()
    license_ok: bool = True

    def haystack(self) -> str:
        return f"{self.description} {' '.join(self.tags)}".lower()


@dataclass
class Placement:
    """一条落位结果（含依据，进 ``report.json``）。"""

    line_index: int
    asset_id: str
    start_sec: float
    score: float
    matched_by: str
    tags: tuple[str, ...] = ()
    note: str = ""


@dataclass
class PlacementResult:
    placements: list[Placement] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)

    @property
    def covered(self) -> int:
        return len(self.placements)


def tag_text(text: str) -> list[str]:
    """把一段文本映射成 MV 标签（按词表命中，顺序固定）。"""
    haystack = (text or "").lower()
    hits = [
        tag
        for tag, words in MV_TAGS.items()
        if any(word.lower() in haystack for word in words)
    ]
    return hits


def build_lyric_queries(lines: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """S-02：每行歌词 → 一条检索 query（带标签，便于检索端精确召回）。"""
    queries: list[dict[str, Any]] = []
    for index, line in enumerate(lines or []):
        text = str(line.get("text") or line.get("band_text") or "").strip()
        tags = tag_text(text)
        keywords = [
            word for word in text.replace("，", " ").replace(",", " ").split() if len(word) > 2
        ][:8]
        queries.append(
            {
                "line_index": int(line.get("i", index)),
                "query": text,
                "keywords": keywords,
                "tags": tags,
                "t0": float(line.get("t0") or 0.0),
                "t1": float(line.get("t1") or 0.0),
            }
        )
    return queries


def score_candidate(query_tags: Iterable[str], query_text: str, candidate: MaterialCandidate) -> tuple[float, str]:
    """打分并给出依据：**显式标签 > 描述同义词 > 关键词命中**（分档，不含糊）。"""
    q_tags = [tag for tag in (query_tags or []) if tag]
    haystack = candidate.haystack()

    explicit = [tag for tag in q_tags if tag in candidate.tags]
    if explicit:
        return min(1.0, 0.70 + 0.10 * (len(explicit) - 1)), f"tag:{','.join(explicit)}"

    synonym = [
        tag
        for tag in q_tags
        if any(word.lower() in haystack for word in MV_TAGS.get(tag, ()))
    ]
    if synonym:
        return min(0.85, 0.50 + 0.08 * (len(synonym) - 1)), f"tag_text:{','.join(synonym)}"

    words = [w.lower() for w in (query_text or "").split() if len(w) > 1]
    hits = [w for w in words if w in haystack]
    if hits:
        return min(0.45, 0.30 + 0.05 * (len(hits) - 1)), f"keyword:{','.join(hits[:3])}"
    return 0.0, "none"


def snap_to_grid(t: float, grid: Any = None, unit: str = "half") -> float:
    """落位起点吸附到拍网格（无网格时原样返回）。"""
    if grid is None or getattr(grid, "beat_sec", 0) <= 0:
        return float(t)
    return float(grid.snap(t, unit))


def place_materials(
    lines: Iterable[dict[str, Any]],
    candidates: Iterable[MaterialCandidate],
    *,
    grid: Any = None,
    min_score: float = MIN_MATCH_SCORE,
    unit: str = "half",
) -> PlacementResult:
    """S-03：逐行挑候选并落位；没达标就记 gap，不硬凑。

    规则：
    - 跳过授权不明的候选（记 ``rejected``，S-04）；
    - 同分时按 ``asset_id`` 字典序取第一个（确定性，避免随机贴图）；
    - 落位起点吸附拍网格，但**不越过该行起点**.
    - 每个候选只用一次（避免同一镜头反复填满全片），用尽后从缺口处报 gap。
    """
    result = PlacementResult()
    pool = list(candidates or [])
    used: set[str] = set()

    for query in build_lyric_queries(lines):
        best: Optional[tuple[float, str, MaterialCandidate]] = None
        for candidate in pool:
            if candidate.asset_id in used:
                continue
            if not candidate.license_ok:
                result.rejected.append(
                    {"asset_id": candidate.asset_id, "reason": "license_unclear", "line": query["line_index"]}
                )
                continue
            score, matched_by = score_candidate(query["tags"], query["query"], candidate)
            if score < min_score:
                continue
            key = (score, matched_by, candidate)
            if best is None or (score, matched_by) > (best[0], best[1]):
                best = key

        if best is None:
            result.gaps.append(
                {
                    "line_index": query["line_index"],
                    "query": query["query"],
                    "tags": query["tags"],
                    "reason": "no_candidate_above_threshold",
                    "threshold": min_score,
                }
            )
            continue

        score, matched_by, candidate = best
        start = snap_to_grid(query["t0"], grid, unit)
        if start > query["t0"]:
            start = query["t0"]  # 吸到后面的拍点会与本行开唱错位
        used.add(candidate.asset_id)
        result.placements.append(
            Placement(
                line_index=query["line_index"],
                asset_id=candidate.asset_id,
                start_sec=round(start, 6),
                score=round(score, 3),
                matched_by=matched_by,
                tags=tuple(query["tags"]),
            )
        )
    return result


def placement_report(result: PlacementResult) -> dict[str, Any]:
    """把落位结果收口成可进 ``report.json`` 的结构（含依据与缺口）。"""
    return {
        "covered": result.covered,
        "total": result.covered + len(result.gaps),
        "placements": [p.__dict__ for p in result.placements],
        "gaps": result.gaps,
        "rejected": result.rejected,
    }


def annotate_scenes_with_placement(
    scenes: Iterable[dict[str, Any]],
    candidates: Iterable[dict[str, Any]],
    *,
    grid: Any = None,
    min_score: float = MIN_MATCH_SCORE,
) -> dict[str, Any]:
    """把落位结果**写回场景**（S 轨接进管线的接口，纯函数）。

    ``candidates`` 是素材候选的字典形式（来自素材库/内容索引），至少含
    ``asset_id``；可选 ``description`` / ``duration_sec`` / ``tags`` / ``license_ok``。

    每个匹配到的场景会得到：
    - ``material_refs``：命中的素材 id（供 EditAgent 直接用）；
    - ``placement``：``{score, matched_by, start_sec, tags}``——**依据必须留痕**。

    返回 ``placement_report`` 同构的统计（含 gaps/rejected），供 material_notes 汇总。
    """
    scene_list = list(scenes or [])
    pool: list[MaterialCandidate] = []
    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        asset_id = str(raw.get("asset_id") or raw.get("id") or "")
        if not asset_id:
            continue
        pool.append(
            MaterialCandidate(
                asset_id=asset_id,
                description=str(raw.get("description") or ""),
                duration_sec=float(raw.get("duration_sec") or 0.0),
                tags=tuple(str(t) for t in (raw.get("tags") or ())),
                license_ok=bool(raw.get("license_ok", True)),
            )
        )

    lines = [
        {
            "i": index,
            "text": f"{scene.get('title', '')} {scene.get('description', '')}".strip(),
            "t0": float(scene.get("start_sec") or 0.0),
            "t1": float(scene.get("start_sec") or 0.0) + float(scene.get("duration_sec") or 0.0),
        }
        for index, scene in enumerate(scene_list)
    ]
    result = place_materials(lines, pool, grid=grid, min_score=min_score)

    by_index = {p.line_index: p for p in result.placements}
    for index, scene in enumerate(scene_list):
        placement = by_index.get(index)
        if placement is None:
            continue
        scene["material_refs"] = [placement.asset_id]
        scene["placement"] = {
            "score": placement.score,
            "matched_by": placement.matched_by,
            "start_sec": placement.start_sec,
            "tags": list(placement.tags),
        }
    return placement_report(result)


__all__ = [
    "MIN_MATCH_SCORE",
    "MV_TAGS",
    "MaterialCandidate",
    "Placement",
    "PlacementResult",
    "annotate_scenes_with_placement",
    "build_lyric_queries",
    "place_materials",
    "placement_report",
    "score_candidate",
    "snap_to_grid",
    "tag_text",
]
