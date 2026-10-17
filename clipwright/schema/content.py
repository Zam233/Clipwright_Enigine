"""素材内容索引的数据模型（R1）。

设计要点（与 `docs/content-understanding-plan-2026-09-14.md` §2 一致）：

1. **索引粒度 = 镜头（shot）**，不做帧级全索引——成本随时长线性爆炸，而镜头级足以回答
   绝大多数"素材里有什么/在哪几秒"的问题。
2. **内容寻址**：`ShotRef = asset:{sha256[:12]}#s{shot_index}`。素材会被移动/重命名/重复导入
   （`AssetManager` 本就按 sha256 去重），路径寻址会让会话指代在两次对话之间失效。
3. **字段带来源与置信度**：每个可能由不同手段（规则/本地模型/云端多模态）填充的字段，
   都在 `evidence` 里记明"谁填的、可信度多少"。理由：索引一旦混入幻觉而无法追溯，
   下游的"为什么这么剪"就失去意义；也让 R2/R3 升级模型时能选择性地只重算某几个字段。
4. **诚实优先**：本阶段只填得出来的字段才填，估计不出来的留空/`unknown`，
   **不编造**（例如镜头语言里的景别要等 R2 有人脸/尺寸估计后再填）。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

#: 索引结构版本——结构或语义变化时 +1（缓存键的一部分，天然失效旧索引）
#: v2（R2）：ShotStats 增加质量维度（过曝/欠曝/边缘能量），ShotVisual 增加景别与人脸。
INDEX_VERSION = 2

#: `asset:{sha256[:12]}#s{n}` 的解析正则
_SHOT_REF_RE = re.compile(r"^asset:(?P<sha>[0-9a-fA-F]{8,64})#s(?P<idx>\d+)$")

ShotSize = Literal["extreme_close", "close", "medium", "full", "wide", "unknown"]
CameraMotion = Literal["static", "low", "medium", "high", "unknown"]


# ── 内容寻址 ──────────────────────────────────────────────


def format_shot_ref(sha256: str, index: int) -> str:
    """生成镜头引用（sha 取前 12 位，够用且短）。"""
    sha = (sha256 or "").strip()
    if len(sha) < 8:
        raise ValueError("sha256 太短，无法作为内容地址")
    return f"asset:{sha[:12].lower()}#s{int(index)}"


def parse_shot_ref(ref: str) -> tuple[str, int] | None:
    """解析镜头引用 → `(sha256前缀, 镜头序号)`；非法返回 None（不抛异常）。"""
    m = _SHOT_REF_RE.match((ref or "").strip())
    if not m:
        return None
    return (m.group("sha").lower(), int(m.group("idx")))


def model_fingerprint(**models: str) -> str:
    """模型指纹：索引缓存键的一部分。

    任一模型换掉都应导致重建——旧字段由旧模型产出，混在一起无法解释。
    空值（未声明/未启用）不参与计算：显式写 `vision=""` 与不传应当等价。
    """
    parts = [f"{k}={v}" for k, v in sorted(models.items()) if (v or "").strip()]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def cache_key(sha256: str, index_version: int, fingerprint: str) -> str:
    """索引缓存键：内容 + 结构版本 + 模型指纹。"""
    raw = f"{sha256}|{index_version}|{fingerprint}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ── 镜头级记录 ────────────────────────────────────────────


class ShotStats(BaseModel):
    """廉价的逐镜头统计（R1：无需任何 ML 模型即可算出）。

    这些量本身就是信号：亮度/对比度用于黑场与曝光判断，锐度用于"糊不糊"，
    帧间差（motion）用于镜头语言和"静止/高速运动"的区分。
    """

    brightness: float = Field(default=0.0, description="平均亮度 0-255")
    contrast: float = Field(default=0.0, description="亮度标准差")
    sharpness: float = Field(default=0.0, description="Laplacian 方差（越大越锐）")
    saturation: float = Field(default=0.0, description="平均饱和度 0-255")
    motion: float = Field(default=0.0, description="镜头内帧间平均绝对差 0-255")
    is_black: bool = Field(default=False, description="近全黑（平均亮度 < 阈值）")
    is_white: bool = Field(default=False, description="近全白（平均亮度 > 阈值）")
    frame_count: int = Field(default=0, description="本镜头实际抽到的帧数")
    # ── R2：质量维度（可直接用于"别用糊的/别用过曝的"这类筛选）──
    overexposed_ratio: float = Field(default=0.0, description="过曝像素占比（>245 灰阶）")
    underexposed_ratio: float = Field(default=0.0, description="欠曝像素占比（<10 灰阶）")
    edge_energy: float = Field(default=0.0, description="相邻像素差均值（越低越糊）")


class ShotSpeech(BaseModel):
    """镜头内语音（R3 填充；R1 留空）。"""

    text: str = ""
    phrases: list[dict[str, Any]] = Field(default_factory=list, description="V6 短语视图条目")
    keywords: list[str] = Field(default_factory=list)
    language: str = ""
    speaker: str = ""


class ShotVisual(BaseModel):
    """镜头画面语义（R2 填充；R1 只填 stats 能给出的部分）。"""

    description: str = Field(default="", description="一句话描述")
    tags: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list, description="英文标签（CLIP/分类器）")
    objects: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    scene_type: str = Field(default="", description="indoor/outdoor/abstract/portrait/landscape")
    shot_size: ShotSize = "unknown"
    camera_motion: CameraMotion = "unknown"
    has_face: Optional[bool] = Field(default=None, description="是否检出人脸（R2）")


class ShotRecord(BaseModel):
    """一个镜头的全部已知信息。"""

    index: int = Field(description="镜头序号（0 起）")
    t0: float = Field(description="起始秒（含）")
    t1: float = Field(description="结束秒（不含）")
    duration_sec: float = 0.0
    keyframes: list[str] = Field(default_factory=list, description="抽出的代表帧路径")
    stats: ShotStats = Field(default_factory=ShotStats)
    speech: ShotSpeech = Field(default_factory=ShotSpeech)
    visual: ShotVisual = Field(default_factory=ShotVisual)
    audio_events: list[dict[str, Any]] = Field(default_factory=list, description="音频事件（R3）")
    embeddings: dict[str, list[float]] = Field(default_factory=dict, description="模态→向量（R9）")
    evidence: dict[str, str] = Field(default_factory=dict, description="字段名→产出者（规则/模型名）")
    confidence: dict[str, float] = Field(default_factory=dict, description="字段名→置信度 0-1")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if not self.duration_sec:
            self.duration_sec = round(max(0.0, self.t1 - self.t0), 3)

    def text_for_embedding(self) -> str:
        """拼出用于检索的文本（描述 + 标签 + 语音）——索引与查询必须同源构造。"""
        parts = [self.visual.description, " ".join(self.visual.tags),
                 " ".join(self.visual.objects), " ".join(self.visual.actions),
                 self.speech.text]
        return " ".join(p for p in parts if p).strip()

    def brief(self, sha256: str = "") -> str:
        """一行摘要（喂 LLM 用；刻意短）。"""
        head = f"[{self.t0:6.1f}-{self.t1:6.1f}s] s{self.index}"
        bits = []
        if self.visual.description:
            bits.append(self.visual.description)
        if self.visual.tags:
            bits.append("/".join(self.visual.tags[:4]))
        if self.visual.shot_size != "unknown":
            bits.append(self.visual.shot_size)
        if self.visual.camera_motion not in ("unknown", "static"):
            bits.append(f"motion:{self.visual.camera_motion}")
        if self.speech.text:
            bits.append(f"语音“{self.speech.text[:20]}”")
        if self.stats.is_black:
            bits.append("黑场")
        return f"{head} " + " ".join(bits) if bits else head


class ContentIndex(BaseModel):
    """一段素材的内容索引（一个 sha256 一份，跨项目复用）。"""

    sha256: str = ""
    asset_id: str = ""
    asset_path: str = ""
    media_type: str = "video"
    filename: str = ""
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    index_version: int = INDEX_VERSION
    model_fingerprint: str = ""
    scene_threshold: float = 0.3
    shots: list[ShotRecord] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict, description="维度→产出者（如 visual=vit-base）")
    created_at: str = ""
    updated_at: str = ""
    build_ms: int = 0
    #: 本次调用是否命中磁盘缓存（不落盘：它是调用结果而不是索引内容）
    served_from_cache: bool = Field(default=False, exclude=True)

    # ── 查询辅助 ──

    def shot_at(self, t: float) -> Optional[ShotRecord]:
        """给定时间落在哪个镜头（二分；越界返回 None）。"""
        lo, hi = 0, len(self.shots) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            s = self.shots[mid]
            if t < s.t0:
                hi = mid - 1
            elif t >= s.t1:
                lo = mid + 1
            else:
                return s
        return None

    def summary(self, max_shots: int = 12) -> dict[str, Any]:
        """紧凑摘要（喂 LLM 的形态）——只给"能用得上的统计 + 少量样本"。"""
        black = sum(1 for s in self.shots if s.stats.is_black)
        with_text = sum(1 for s in self.shots if s.speech.text)
        with_desc = sum(1 for s in self.shots if s.visual.description)
        with_tags = sum(1 for s in self.shots if s.visual.tags)
        with_face = sum(1 for s in self.shots if s.visual.has_face)
        sizes: dict[str, int] = {}
        for s in self.shots:
            if s.visual.shot_size != "unknown":
                sizes[s.visual.shot_size] = sizes.get(s.visual.shot_size, 0) + 1
        tag_counts: dict[str, int] = {}
        for s in self.shots:
            for t in s.visual.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        top_tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        return {
            "sha256": self.sha256[:12],
            "filename": self.filename,
            "duration_sec": round(self.duration_sec, 2),
            "resolution": f"{self.width}x{self.height}" if self.width else "",
            "shot_count": len(self.shots),
            "black_shots": black,
            "shots_with_speech": with_text,
            "shots_with_description": with_desc,
            "shots_with_tags": with_tags,
            "shots_with_face": with_face,
            "shot_sizes": sizes,
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
            "avg_shot_sec": round(
                sum(s.duration_sec for s in self.shots) / len(self.shots), 2
            ) if self.shots else 0.0,
            "sample_shots": [s.brief(self.sha256) for s in self.shots[:max(0, max_shots)]],
        }

    def summary_text(self, max_shots: int = 12) -> str:
        """摘要的纯文本形态（直接拼进提示词）。"""
        s = self.summary(max_shots=max_shots)
        lines = [
            f"素材 {s['filename'] or s['sha256']}：{s['shot_count']} 个镜头 / "
            f"{s['duration_sec']}s / {s['resolution']} / 平均镜头 {s['avg_shot_sec']}s",
        ]
        extra = []
        if s["black_shots"]:
            extra.append(f"{s['black_shots']} 段黑场")
        if s["shots_with_speech"]:
            extra.append(f"{s['shots_with_speech']} 段有语音")
        if s["shots_with_face"]:
            extra.append(f"{s['shots_with_face']} 段有人脸")
        if s["shot_sizes"]:
            extra.append("景别 " + "/".join(f"{k}:{v}" for k, v in s["shot_sizes"].items()))
        if s["top_tags"]:
            extra.append("常见标签 " + "、".join(f"{t['tag']}×{t['count']}" for t in s["top_tags"][:6]))
        if extra:
            lines.append("；".join(extra))
        if s["sample_shots"]:
            lines.append("镜头样本：")
            lines.extend(f"  {b}" for b in s["sample_shots"])
        return "\n".join(lines)

    def coverage(self) -> dict[str, float]:
        """各维度填充率（0-1）——衡量索引完整度，避免"以为索引好了其实没填"。"""
        n = max(1, len(self.shots))
        return {
            "shots": float(len(self.shots)),
            "visual_description": sum(1 for s in self.shots if s.visual.description) / n,
            "visual_tags": sum(1 for s in self.shots if s.visual.tags) / n,
            "speech": sum(1 for s in self.shots if s.speech.text) / n,
            "stats": sum(1 for s in self.shots if s.stats.frame_count) / n,
            "embeddings": sum(1 for s in self.shots if s.embeddings) / n,
        }

    def to_mongo_doc(self) -> dict[str, Any]:
        """Mongo 镜像文档（跨项目检索用：一段素材一份，含 shots 数组）。"""
        doc = self.model_dump(exclude={"served_from_cache"})
        doc["_id"] = self.sha256 or cache_key(self.sha256, self.index_version, self.model_fingerprint)
        # 便于 Mongo 侧做关键词检索的扁平字段
        doc["search_text"] = " ".join(
            s.text_for_embedding() for s in self.shots
        )[:20000]
        return doc


def now_iso() -> str:
    """UTC ISO 时间戳（与 ProjectManager 同口径）。"""
    return datetime.now(timezone.utc).isoformat()
