"""素材内容查询工具（R4）——让 Agent 自己问"素材里有什么"。

M1 里程碑的落地件。Agent 拿到的上下文只有**几十行摘要**（`content_query` 的
`index_context_text`），细节靠这个工具现查：它比"把整份索引塞进提示词"便宜得多，
也比"让模型凭想象说素材里有什么"可靠得多。

四种模式：
- `summary`：手上有哪些素材、各自什么构成（零模型调用）；
- `search`：自然语言检索镜头（混合：结构化过滤 + 关键词 + 向量 + 可选重排）；
- `filter`：只按结构化条件筛（"找黑场""找有人脸的镜头""找静止 3 秒以上的空镜"）；
- `shot_at`：按时间问"这一刻是什么"（毫秒级事实，零模型）。

每条命中都带 `why`（哪几个信号命中的）与 `ref`（`asset:{sha}#s{n}`）——
下游剪辑可以据此落位，质检可以据此复核"你说的素材里真的有这个吗"。
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class ContentQueryTool(BaseTool):
    """素材内容查询（摘要 / 检索 / 结构化筛选 / 时间定位）。"""

    name = "content_query"
    description = (
        "查询素材内容索引：summary 列出已索引素材的构成，search 用自然语言检索镜头"
        "（自动把“黑场/有人脸/特写/掌声/静止/最长/最亮”这类说法路由成结构化条件或排序，"
        "并给出为什么命中的证据），filter 按结构化条件筛"
        "（人脸/景别/黑场/过曝/运动/语音/音频事件），"
        "shot_at 按时间问这一刻是什么镜头。返回带 ref（asset:sha#sN）与时间区间的命中"
    )
    dependencies = []

    async def execute(
        self,
        mode: str = "summary",
        *,
        query: str = "",
        project_id: str = "",
        sha256: str = "",
        t: float = -1.0,
        order: str = "",
        order_desc: bool = True,
        top_k: int = 8,
        use_embeddings: bool = True,
        rerank: bool = True,
        filters: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        from clipwright.services import content_query as cq

        act = (mode or "summary").strip().lower()
        pid = project_id or None
        try:
            if act == "summary":
                indexes = cq.load_indexes(pid, sha256=sha256)
                if not indexes:
                    return ToolExecResult(
                        status=ToolStatus.SUCCESS, tool_name=self.name,
                        output={"mode": "summary", "assets": [], "count": 0, "text": "",
                                "note": "没有可用索引（先对素材建索引）"})
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={
                    "mode": "summary",
                    "assets": cq.assets_overview(indexes),
                    "count": len(indexes),
                    "text": cq.index_context_text(pid),
                })

            if act == "search":
                if not (query or "").strip():
                    return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                          error="search 模式需要 query")
                res = await cq.search_shots(
                    query, project_id=pid, sha256=sha256, top_k=max(1, int(top_k)),
                    use_embeddings=bool(use_embeddings), rerank=bool(rerank),
                    filters=filters or None,
                    order_by=(order.strip(), bool(order_desc)) if order else None)
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                      output={"mode": "search", **res})
            if act == "filter":
                if not filters:
                    return ToolExecResult(
                        status=ToolStatus.ERROR, tool_name=self.name,
                        error="filter 模式需要 filters（如 {\"is_black\": true}）")
                indexes = cq.load_indexes(pid, sha256=sha256)
                if not indexes:
                    return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                          output={"mode": "filter", "hits": [], "count": 0,
                                                  "note": "没有可用索引"})
                hits: list[dict[str, Any]] = []
                for idx in indexes:
                    for shot in cq.filter_shots(idx, **filters):
                        hits.append({
                            "ref": f"asset:{idx.sha256[:12]}#s{shot.index}",
                            "sha256": idx.sha256[:12], "filename": idx.filename,
                            "index": shot.index, "t0": shot.t0, "t1": shot.t1,
                            "duration_sec": shot.duration_sec,
                            "shot_size": shot.visual.shot_size,
                            "has_face": shot.visual.has_face,
                            "camera_motion": shot.visual.camera_motion,
                            "brightness": round(shot.stats.brightness, 1),
                            "is_black": shot.stats.is_black,
                            "tags": shot.visual.tags[:5],
                            "audio_events": [ev.get("kind") for ev in shot.audio_events],
                            "speech": shot.speech.text[:80],
                        })
                hits.sort(key=lambda h: (h["sha256"], h["index"]))
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                      output={"mode": "filter", "hits": hits[:max(1, int(top_k)) * 5],
                                              "count": len(hits), "filters": filters})

            if act == "shot_at":
                if t < 0:
                    return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                          error="shot_at 模式需要 t（秒）")
                hit = await cq.shot_at(float(t), project_id=pid, sha256=sha256)
                if hit is None:
                    return ToolExecResult(
                        status=ToolStatus.SUCCESS, tool_name=self.name,
                        output={"mode": "shot_at", "t": float(t), "hit": None,
                                "note": "该时间不在任何已索引素材的时间范围内"})
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                      output={"mode": "shot_at", "t": float(t), "hit": hit})

            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"未知 mode: {mode!r}（可用：summary / search / filter / shot_at）")
        except Exception as exc:
            logger.warning("content_query 工具失败: %s", exc)
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))
