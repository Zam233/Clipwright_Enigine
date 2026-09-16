"""意图落位（R6）——把"那一段""有人物的镜头"解析到**具体片段**，歧义时反问。

为什么这一步不能省
------------------
R5 能把"去掉黑场"解析成 `remove + filter={is_black:true}`，但**时间线上没有"黑场"这个东西**：
时间线上是 clip，clip 指向素材；"黑场"是素材内容索引里的事实。中间缺一层映射：
**内容索引的镜头 → 时间线上的 clip**。缺了它，意图就永远停在"语义上正确、执行不了"。

本模块做三件事：
1. **引用解析**：`all/selected/time_range/ref/index` 直接对时间线解析；`filter/query` 走
   R4 的内容检索，再把命中的**镜头**映射回使用该素材的 **clip**；
2. **会话指代**：`"刚才那段"`「"上一个"」「"第三个"」需要在**会话状态**里记录最近的候选集，
   否则每次都得让用户重新说一遍（这是"能用 NL 达到目的"和"每次都要重说"的分界）；
3. **歧义反问**：解析出 0 个或太多候选时**不猜**，给出 2-3 个具体选项让人选。

诚实边界：
- 内容条件依赖**已建索引**；没有索引时降级为"按 clip 文本/标题匹配"，命中率会明显下降
  （并在结果里标明 `degraded=True`，不假装有内容索引）；
- 镜头→clip 的映射依赖 `clip.asset_id` 与索引里的 `asset_path` 对得上（同一文件路径/同一个
  sha256）；素材被移动或改名后映射会失效，此时按"该素材在时间线上被哪个 clip 使用"的
  弱匹配兜底并给出提示；
- 会话指代存在进程内存（跨重启丢失）；持久化留给后续轮次。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.services.edit_intent import (
    DEICTIC_PLURAL_WORDS,
    DEICTIC_RECENT_WORDS,
    EditIntent,
    IntentOp,
    IntentTarget,
)

#: 指代词 → 语义（"刚才/上一个" 取最近候选，"它们/这些" 取最近几轮的并集）。
#: 词表放在 `edit_intent`（规则解析器也要用同一套判断），此处只是取别名。
_DEICTIC_RECENT = DEICTIC_RECENT_WORDS
_DEICTIC_SAME = DEICTIC_PLURAL_WORDS
#: 会话里保留的最近候选集数量上限
_MAX_SHORTLISTS = 5


@dataclass
class SessionRefState:
    """会话级指代状态：记住"最近提到过哪些片段"。"""
    shortlists: list[list[str]] = field(default_factory=list)
    last_updated: float = 0.0

    def push(self, clip_ids: Iterable[str]) -> None:
        ids = [str(c) for c in clip_ids if c]
        if not ids:
            return
        self.shortlists.insert(0, ids)
        del self.shortlists[_MAX_SHORTLISTS:]
        import time

        self.last_updated = time.time()

    def recent(self) -> list[str]:
        return list(self.shortlists[0]) if self.shortlists else []

    def union_of_recent(self) -> list[str]:
        seen: list[str] = []
        for group in self.shortlists[:3]:
            for cid in group:
                if cid not in seen:
                    seen.append(cid)
        return seen

    def as_dict(self) -> dict[str, Any]:
        return {"shortlists": self.shortlists, "last_updated": self.last_updated}


#: 进程内会话状态（R6 先做到"同一次会话内指代可用"）
_SESSIONS: dict[str, SessionRefState] = {}


def session_state(session_id: str) -> SessionRefState:
    """取（或建）会话指代状态。"""
    key = session_id or "default"
    if key not in _SESSIONS:
        _SESSIONS[key] = SessionRefState()
    return _SESSIONS[key]


def reset_session_state(session_id: str = "") -> None:
    """清空会话指代状态（测试/重置用）。"""
    if session_id:
        _SESSIONS.pop(session_id, None)
    else:
        _SESSIONS.clear()


@dataclass
class Question:
    """需要用户确认的问题（带**具体可选项**，不是空问一句"你指哪个"）。"""
    code: str
    text: str
    options: list[dict[str, str]] = field(default_factory=list)
    op_index: int = -1


@dataclass
class ResolvedOp:
    op: IntentOp
    clip_ids: list[str] = field(default_factory=list)
    degraded: bool = False
    evidence: list[str] = field(default_factory=list)


@dataclass
class ResolutionReport:
    resolved: list[ResolvedOp] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.questions and not self.problems

    def affected_clip_ids(self) -> list[str]:
        seen: list[str] = []
        for item in self.resolved:
            for cid in item.clip_ids:
                if cid not in seen:
                    seen.append(cid)
        return seen

    def preview_lines(self, timeline: Any = None) -> list[str]:
        """"将会发生什么"的人话清单（意图确认卡直接渲染）。

        给了 `timeline` 时用**时间线序号**（"第2个片段"）而不是裸 clip id——
        真实素材上的 clip id / 文件名（`pexels_6748729_426p.mp4`）用户根本对不上号，
        而确认卡的全部意义就是"让人在改动前看清改的是哪几个"。
        """
        order: dict[str, int] = {}
        labels: dict[str, str] = {}
        if timeline is not None:
            order = {cid: i + 1 for i, cid in enumerate(clip_ids_in_order(timeline))}
            for _t, clip in iter_clips(timeline):
                text = str(getattr(clip, "text", "") or "").strip()
                asset = Path(str(getattr(clip, "asset_id", "") or "")).name
                labels[str(getattr(clip, "id", ""))] = text or asset
        lines: list[str] = []
        for item in self.resolved:
            op = item.op
            where = "、".join(self._label(cid, order, labels) for cid in item.clip_ids[:4]) \
                or "（无匹配）"
            if len(item.clip_ids) > 4:
                where += f" 等 {len(item.clip_ids)} 个片段"
            lines.append(f"{op.op} → {where}" + ("（降级匹配）" if item.degraded else ""))
        return lines

    @staticmethod
    def _label(cid: str, order: dict[str, int], labels: dict[str, str]) -> str:
        pos = order.get(cid)
        name = labels.get(cid) or ""
        if pos and name:
            return f"第{pos}个片段（{name[:16]}）"
        if pos:
            return f"第{pos}个片段"
        return cid

    def as_dict(self, timeline: Any = None) -> dict[str, Any]:
        return {
            "resolved": [
                {"op": item.op.op, "clip_ids": item.clip_ids,
                 "degraded": item.degraded, "evidence": item.evidence}
                for item in self.resolved
            ],
            "questions": [
                {"code": q.code, "text": q.text, "options": q.options, "op_index": q.op_index}
                for q in self.questions
            ],
            "problems": self.problems,
            "affected_clip_ids": self.affected_clip_ids(),
            "preview": self.preview_lines(timeline),
        }


# ── 时间线辅助 ────────────────────────────────────────────


def iter_clips(timeline: Timeline) -> list[tuple[str, Any]]:
    """`[(track_id, clip), ...]`（按轨道顺序）。"""
    out: list[tuple[str, Any]] = []
    for track in getattr(timeline, "tracks", []) or []:
        for clip in getattr(track, "clips", []) or []:
            out.append((str(getattr(track, "id", "")), clip))
    return out


def clip_ids_in_order(timeline: Timeline) -> list[str]:
    return [str(getattr(c, "id", "")) for _t, c in iter_clips(timeline)]


def _clip_label(clip: Any) -> str:
    text = str(getattr(clip, "text", "") or "")
    kind = str(getattr(getattr(clip, "kind", ""), "value", getattr(clip, "kind", "")))
    asset = Path(str(getattr(clip, "asset_id", "") or "")).name
    label = text.strip() or asset or str(getattr(clip, "id", ""))
    return f"{label[:24]}（{kind}）"


def _normalize_path(value: str) -> str:
    try:
        return os.path.normcase(str(Path(str(value)).resolve()))
    except Exception:
        return os.path.normcase(str(value or ""))


def _clip_index_map(
    timeline: Timeline, indexes: list[Any],
) -> tuple[dict[str, Any], dict[str, bool]]:
    """`clip_id → 该片段所用素材的内容索引` 与 `clip_id → 是否路径精确匹配`。

    两级匹配：① 路径完全相同（`path_exact=True`）；② 同文件名（素材被移动过，标 False）；
    都对不上则 `None`（该片段没有内容索引）。
    把"是否精确"按片段返回，是为了让降级标记能**只针对真正用到的片段**——
    全局标记会把"时间线上恰好有个无关片段没索引"也算成降级（实测误报）。
    """
    by_path: dict[str, Any] = {}
    by_name: dict[str, Any] = {}
    for idx in indexes:
        asset_path = str(getattr(idx, "asset_path", "") or "")
        if asset_path:
            by_path[_normalize_path(asset_path)] = idx
            by_name[Path(asset_path).name.lower()] = idx
    mapping: dict[str, Any] = {}
    exact: dict[str, bool] = {}
    for _track, clip in iter_clips(timeline):
        asset = str(getattr(clip, "asset_id", "") or "")
        cid = str(getattr(clip, "id", ""))
        if not asset:
            mapping[cid] = None
            exact[cid] = False
            continue
        idx = by_path.get(_normalize_path(asset))
        if idx is not None:
            mapping[cid] = idx
            exact[cid] = True
            continue
        idx = by_name.get(Path(asset).name.lower())
        mapping[cid] = idx
        exact[cid] = False
    return mapping, exact


# ── 引用解析 ──────────────────────────────────────────────


async def resolve_target(
    target: IntentTarget,
    *,
    timeline: Timeline,
    selected_clip_ids: Optional[list[str]] = None,
    region: Optional[tuple[float, float]] = None,
    project_id: str | None = None,
    state: SessionRefState | None = None,
    max_candidates: int = 30,
) -> tuple[list[str], list[str], bool]:
    """解析一个 target → `(clip_ids, evidence, degraded)`。

    不猜：解析不出就返回空列表 + 说明，由上层生成反问。
    """
    selected = [str(c) for c in (selected_clip_ids or [])]
    evidence: list[str] = []
    all_ids = clip_ids_in_order(timeline)

    if target.is_empty():
        return ([], ["未指定对象"], False)

    if target.all:
        return (all_ids, ["整条时间线"], False)

    if target.selected:
        if selected:
            return (selected, [f"用户选中的 {len(selected)} 个片段"], False)
        # 没选就用区域；区域也没有 → 交给反问
        if region:
            ids = _clip_ids_in_range(timeline, region)
            return (ids, [f"时间区间 {region[0]:.1f}-{region[1]:.1f}s"], False)
        return ([], ["没有选中片段，也没有时间区间"], False)

    if target.time_range:
        lo, hi = target.time_range
        ids = _clip_ids_in_range(timeline, (float(lo), float(hi)))
        return (ids, [f"时间区间 {lo:g}-{hi:g}s"], False)

    if target.ref:
        known = set(all_ids)
        hits = [r for r in target.ref if r in known]
        missing = [r for r in target.ref if r not in known]
        if missing:
            evidence.append("引用不存在：" + "、".join(missing[:3]))
        return (hits, evidence or ["显式引用"], False)

    if target.index:
        out: list[str] = []
        bad: list[int] = []
        for i in target.index:
            if 0 <= int(i) < len(all_ids):
                out.append(all_ids[int(i)])
            else:
                bad.append(int(i))
        if bad:
            evidence.append(f"序号越界：{bad}（共 {len(all_ids)} 个片段）")
        return (out, evidence or ["按序号"], False)

    if target.filter or target.query:
        return await _resolve_content_target(
            target, timeline=timeline, project_id=project_id, state=state,
            max_candidates=max_candidates)

    return ([], ["无法识别的对象描述"], False)


def _clip_ids_in_range(timeline: Timeline, region: tuple[float, float]) -> list[str]:
    lo, hi = region
    out: list[str] = []
    for _track, clip in iter_clips(timeline):
        t0 = float(getattr(clip, "start_sec", 0.0) or 0.0)
        t1 = t0 + float(getattr(clip, "duration_sec", 0.0) or 0.0)
        if min(t1, hi) - max(t0, lo) > 0.01:
            out.append(str(getattr(clip, "id", "")))
    return out


async def _resolve_content_target(
    target: IntentTarget,
    *,
    timeline: Timeline,
    project_id: str | None,
    state: SessionRefState | None,
    max_candidates: int,
) -> tuple[list[str], list[str], bool]:
    """内容条件 → clip：先用索引把条件落到**镜头**，再把镜头映射回使用该素材的**片段**。

    粒度说明：时间线上是 clip（整段素材的一个时间窗口），R6 只做**预览**，
    因此这里给的是"哪些片段用到了命中内容"的**片段级**结果；
    要精确到"素材里的第 12 秒"需要真正的裁剪执行（R7）。
    """
    from clipwright.services import content_query as cq

    indexes = cq.load_indexes(project_id)
    text_ids = _match_clips_by_text(timeline, target.query)
    if not indexes:
        return (text_ids, ["未建索引：按片段文本弱匹配"], True)

    query = (target.query or "").strip()
    res = await cq.search_shots(query or "匹配条件", project_id=project_id,
                                top_k=max_candidates, filters=target.filter or None,
                                use_embeddings=bool(query))
    hits = res.get("hits") or []
    evidence: list[str] = []
    if target.filter:
        evidence.append("结构化条件：" + "、".join(f"{k}={v}" for k, v in target.filter.items()))
    if hits:
        evidence.append(f"内容命中 {len(hits)} 个镜头：" + "、".join(h["ref"] for h in hits[:3]))

    hit_shas = {h["sha256"] for h in hits}
    clip_map, path_exact = _clip_index_map(timeline, indexes)
    matched: list[str] = []
    degraded = False
    for cid, idx in clip_map.items():
        if idx is None:
            continue
        if str(getattr(idx, "sha256", ""))[:12] in hit_shas:
            matched.append(cid)
            if not path_exact.get(cid):
                degraded = True   # 靠同文件名匹配到的素材，可能不是同一份
    # 片段自身文本/标题也参与匹配：时间线上的片段标题是**用户自己的话**
    # （"产品演示""采访"），索引里查不到这些词，但它们确实描述了内容。
    for cid in text_ids:
        if cid not in matched:
            matched.append(cid)
    if text_ids:
        evidence.append(f"片段文本命中 {len(text_ids)} 个（{('、'.join(text_ids[:3]))}）")
    if hits and not matched:
        evidence.append("命中镜头不在时间线所用素材上（素材可能已移动或未被使用）")
        degraded = True
    if not hits and not text_ids:
        evidence.append("内容条件未命中任何镜头，也没有片段文本命中")
    return (matched[:max_candidates], evidence, degraded)


def _match_clips_by_text(timeline: Timeline, query: str) -> list[str]:
    """无索引时的弱匹配：按 clip 文本/来源标题与查询词元的交集。"""
    from clipwright.services.content_query import _tokenize

    tokens = _tokenize(query)
    if not tokens:
        return []
    out: list[str] = []
    for _track, clip in iter_clips(timeline):
        hay = " ".join([
            str(getattr(clip, "text", "") or ""),
            str((getattr(clip, "metadata", {}) or {}).get("source_title", "")),
            Path(str(getattr(clip, "asset_id", "") or "")).stem,
        ]).lower()
        if any(tok in hay for tok in tokens if len(tok) >= 2):
            out.append(str(getattr(clip, "id", "")))
    return out


# ── 指代消解 ──────────────────────────────────────────────


def resolve_deictic(phrase: str, state: Optional[SessionRefState]) -> Optional[list[str]]:
    """指代词 → 会话里的候选片段（解析不出返回 None，交给反问）。"""
    text = (phrase or "").strip()
    if not text or state is None:
        return None
    # 序号指代："第 3 个"
    import re

    m = re.fullmatch(r"第\s*(\d+)\s*个?", text)
    if m and state.shortlists:
        idx = int(m.group(1)) - 1
        recent = state.recent()
        if 0 <= idx < len(recent):
            return [recent[idx]]
        return None
    if any(word in text for word in _DEICTIC_SAME):
        return state.union_of_recent() or None
    if any(word in text for word in _DEICTIC_RECENT):
        return state.recent() or None
    return None


# ── 主入口 ────────────────────────────────────────────────


async def resolve_intent(
    intent: EditIntent,
    *,
    timeline: Timeline,
    session_id: str = "",
    selected_clip_ids: Optional[list[str]] = None,
    region: Optional[tuple[float, float]] = None,
    project_id: str | None = None,
    max_candidates: int = 30,
    clarify_above: int = 12,
) -> ResolutionReport:
    """把整份意图落位到具体片段；有歧义/空匹配时产出**带选项的**反问。"""
    state = session_state(session_id) if session_id else SessionRefState()
    report = ResolutionReport()

    # 会话里没有选中、但用户在说"刚才那段" → 用会话最近候选兜底
    deictic = resolve_deictic(intent.goal, state)
    effective_selection = list(selected_clip_ids or [])
    if deictic and not effective_selection:
        effective_selection = deictic
        report.problems.append("按会话上下文理解为最近提到的片段")

    for i, op in enumerate(intent.ops):
        ids, evidence, degraded = await resolve_target(
            op.target, timeline=timeline, selected_clip_ids=effective_selection,
            region=region, project_id=project_id, state=state,
            max_candidates=max_candidates)
        item = ResolvedOp(op=op, clip_ids=ids, degraded=degraded, evidence=list(evidence))
        report.resolved.append(item)

        if not ids:
            report.questions.append(Question(
                code="no_match", op_index=i,
                text=f"「{op.op}」没有找到匹配的片段（{'；'.join(evidence) or '对象不明确'}）",
                options=_sample_options(timeline, limit=3),
            ))
        elif len(ids) > clarify_above:
            report.questions.append(Question(
                code="too_many", op_index=i,
                text=f"「{op.op}」匹配到 {len(ids)} 个片段，太多——要不要缩小范围？",
                options=[
                    {"value": "first10", "label": f"只处理前 10 个（{_label_of(timeline, ids[0])} 起）"},
                    {"value": "manual", "label": "我自己在时间线上选中后再试"},
                    {"value": "all", "label": f"就按全部 {len(ids)} 个处理"},
                ],
            ))
    if report.resolved:
        state.push(report.affected_clip_ids())
    logger.info("意图落位：%d 个操作 / %d 个待确认问题", len(report.resolved), len(report.questions))
    return report


def _label_of(timeline: Timeline, clip_id: str) -> str:
    for _track, clip in iter_clips(timeline):
        if str(getattr(clip, "id", "")) == clip_id:
            return _clip_label(clip)
    return clip_id


def _sample_options(timeline: Timeline, limit: int = 3) -> list[dict[str, str]]:
    opts: list[dict[str, str]] = []
    for _track, clip in iter_clips(timeline)[:limit]:
        cid = str(getattr(clip, "id", ""))
        opts.append({"value": cid, "label": f"{_clip_label(clip)} @ "
                                          f"{float(getattr(clip, 'start_sec', 0.0) or 0.0):.1f}s"})
    return opts


async def preview_intent(
    message: str,
    *,
    timeline: Timeline,
    session_id: str = "",
    selected_clip_ids: Optional[list[str]] = None,
    region: Optional[tuple[float, float]] = None,
    project_id: str | None = None,
    llm: Any = None,
) -> dict[str, Any]:
    """一句话 → 意图 → 落位 → **预览**（不修改时间线）。

    返回结构直接喂前端"意图确认卡"：`intent`（人话总结）、`resolution`（影响的片段）、
    `questions`（要用户拍板的问题）、`problems`（校验错误/警告）。
    """
    from clipwright.services.edit_intent import (
        describe_intent, has_errors, parse_intent, validate_intent,
    )

    intent, problems = await parse_intent(message, llm=llm)
    problems_payload = [{"level": p.level, "where": p.where, "message": p.message} for p in problems]
    if has_errors(problems):
        # 有硬错误就不落位：避免"带病的意图"影响片段（幻觉 op 在这里被拦住）
        return {
            "intent": describe_intent(intent),
            "intent_raw": intent.model_dump(),
            "resolution": ResolutionReport().as_dict(),
            "problems": problems_payload,
            "blocked": True,
        }
    report = await resolve_intent(
        intent, timeline=timeline, session_id=session_id,
        selected_clip_ids=selected_clip_ids, region=region, project_id=project_id)
    return {
        "intent": describe_intent(intent),
        "intent_raw": intent.model_dump(),
        "resolution": report.as_dict(timeline),
        "problems": problems_payload,
        "blocked": False,
    }
