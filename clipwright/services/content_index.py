"""素材内容索引服务（R1）——把"这段素材里有什么"变成可查询的结构。

一句话定位：**Agent 不读视频，读索引**。本模块负责把一段素材变成镜头级的结构化记录，
存成内容寻址的一份 JSON（同 sha256 跨项目复用），并可选镜像到 Mongo 供跨项目检索。

成本分层（`docs/content-understanding-plan-2026-09-14.md` §2 决策 2）：
- **L0（本模块）**：ffprobe 元数据 + ffmpeg 场景切分 + 抽帧 + 廉价统计（亮度/对比度/
  锐度/饱和度/帧间差/黑场白场）——**零 ML、零云端调用**，10 分钟 1080p 素材秒级完成。
- **L1（R2）**：本地小模型（ViT/CLIP）画面标签、质量与镜头语言。
- **L2（R3）**：ASR 短语对齐 + 音频事件 + 领域词表。
- **L3（按需）**：云端多模态对**被选中的镜头**做精判（已获准，默认只对候选窗口调用）。

缓存与增量：键 = `sha256 + INDEX_VERSION + model_fingerprint`（见 `schema/content.py`）。
同一份素材重复导入、换项目、重启进程都不会重算；换了模型指纹则自动重建——
避免"旧模型填的字段和新模型混在一份索引里"这种无法解释的状态。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from clipwright.config import logger, settings
from clipwright.schema.content import (
    INDEX_VERSION,
    ContentIndex,
    ShotRecord,
    ShotStats,
    cache_key,
    model_fingerprint,
    now_iso,
)

#: 抽帧时每镜头的默认帧数（2 帧才能算帧间差 → 运动强度）
DEFAULT_FRAMES_PER_SHOT = 2
#: 低于该时长的镜头会被合并进邻居（转场/闪帧造成的碎片）
MIN_SHOT_SEC = 0.3
#: 单段素材最多保留的镜头数（超长素材兜底，避免索引本身变成负担）
MAX_SHOTS = 2000
#: 黑/白场判定阈值（0-255）
BLACK_LUMA = 16.0
WHITE_LUMA = 240.0

ProgressFn = Callable[[float, str], Any]


# ── 路径与缓存 ────────────────────────────────────────────


def asset_base_dir(project_id: str | None = None) -> Path:
    """素材库根目录（与 `AssetManager` 保持同一约定）。"""
    return (settings.project_dir / project_id / "assets") if project_id else settings.library_dir


def index_dir(project_id: str | None = None) -> Path:
    """索引存放目录：`<素材库>/content_index/`。"""
    d = asset_base_dir(project_id) / "content_index"
    d.mkdir(parents=True, exist_ok=True)
    return d


def index_path(sha256: str, project_id: str | None = None) -> Path:
    """某素材的索引文件路径（按内容寻址：同内容跨项目复用同一份）。"""
    return index_dir(project_id) / f"{(sha256 or 'unknown')[:64]}.json"


def sha256_of(path: str | Path, chunk: int = 1 << 20) -> str:
    """分块计算文件 sha256（与 `asset_manager._sha256_of` 同算法，便于复用其去重结果）。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()


def load_index(sha256: str, project_id: str | None = None) -> Optional[ContentIndex]:
    """读索引；不存在/损坏/版本不符都返回 None（调用方据此重建）。"""
    if not sha256:
        return None
    path = index_path(sha256, project_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        idx = ContentIndex.model_validate(raw)
    except Exception as exc:
        logger.warning("内容索引读取失败（将重建）: %s: %s", path.name, exc)
        return None
    if idx.index_version != INDEX_VERSION:
        logger.info("内容索引版本不符（%s != %s），重建: %s",
                    idx.index_version, INDEX_VERSION, path.name)
        return None
    return idx


def save_index(index: ContentIndex, project_id: str | None = None) -> Path:
    """原子写索引（先写临时文件再 replace，避免半截 JSON）。"""
    index.updated_at = now_iso()
    if not index.created_at:
        index.created_at = index.updated_at
    path = index_path(index.sha256, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(index.model_dump(exclude={"served_from_cache"}), f,
                      ensure_ascii=False, indent=1)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def index_is_fresh(index: Optional[ContentIndex], sha256: str, fingerprint: str) -> bool:
    """索引是否可直接复用（同内容 + 同结构版本 + 同模型指纹）。"""
    if index is None:
        return False
    return (
        index.sha256 == sha256
        and index.index_version == INDEX_VERSION
        and index.model_fingerprint == fingerprint
        and bool(index.shots)
    )


def current_fingerprint() -> str:
    """当前索引的模型指纹。

    凡是**产出索引字段**的东西都算进来：视觉分类模型、CLIP 权重、嵌入模型、
    以及是否启用云端多模态描述（换后端 = 字段语义变了 = 必须重建）。
    """
    return model_fingerprint(
        index_version=str(INDEX_VERSION),
        visual=(settings.vision_model if settings.vision_provider == "transformers" else ""),
        clip=(settings.content_index_clip_model if settings.content_index_clip_enabled else ""),
        embed=(settings.rag_embed_model or ""),
        cloud_llm="1" if settings.content_index_visual_llm else "",
        speech="",
    )


# ── L0：切分与统计（纯函数部分可单测） ─────────────────────


def build_shot_ranges(
    cut_times: Iterable[float],
    duration: float,
    *,
    min_shot_sec: float = MIN_SHOT_SEC,
    max_shots: int = MAX_SHOTS,
) -> list[tuple[float, float]]:
    """切点 → 镜头区间列表。

    - 切点做去重、排序、裁剪到 `(0, duration)`；首尾自动补 0 与 duration；
    - 短于 `min_shot_sec` 的镜头合并进**前一个**（没有前一个则并入后一个）——
      碎片通常来自转场/闪帧，单独成镜只会污染索引；
    - 超过 `max_shots` 时按"最长的镜头优先"裁到上限（确定性，不随机）。
    """
    dur = float(duration or 0.0)
    if dur <= 0:
        return []
    cuts = sorted({round(float(c), 3) for c in cut_times if 0 < float(c) < dur})
    bounds = [0.0, *cuts, dur]
    ranges = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]

    merged: list[list[float]] = []
    for t0, t1 in ranges:
        if merged and (t1 - t0) < min_shot_sec:
            # 并入前一个镜头（延长它的结束时间）
            merged[-1][1] = t1
            continue
        merged.append([t0, t1])
    # 第一个镜头本身过短时并入后一个
    while len(merged) >= 2 and (merged[0][1] - merged[0][0]) < min_shot_sec:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    merged = [r for r in merged if r[1] - r[0] > 0.001]

    if len(merged) > max_shots:
        keep = sorted(range(len(merged)),
                      key=lambda i: (merged[i][1] - merged[i][0]), reverse=True)[:max_shots]
        merged = [merged[i] for i in sorted(keep)]
    return [(round(r[0], 3), round(r[1], 3)) for r in merged]


_SCENE_TS_RE = re.compile(r"pts_time:([\d.]+)")


def parse_scene_times(ffmpeg_stderr: str) -> list[float]:
    """从 ffmpeg `showinfo` 输出里解析切点时间戳（与 `SceneDetectTool` 同口径）。"""
    return [float(m.group(1)) for m in _SCENE_TS_RE.finditer(ffmpeg_stderr or "")]


def _hist_intersection(a: Any, b: Any) -> float:
    """归一化直方图交集（1.0 = 完全相同，0.0 = 毫无重叠）。

    **两张图都必须是 L1 归一化（和为 1）**。踩坑记录：第一版用
    `cv2.normalize(hist, hist)` 的默认 L2 归一化，于是"交集"的和可以 **>1**，
    `1 - 交集` 出现**负值**——实测一处交叉溶解的跨度是 -0.92，
    看起来像"完全负相关"，实际是归一化口径错了。
    """
    import numpy as np

    return float(np.minimum(a, b).sum())


def _l1(hist: Any) -> Any:
    """L1 归一化（和为 1）——直方图交集的正确前提。"""
    total = float(hist.sum())
    if total <= 1e-12:
        return hist
    return hist / total


def detect_gradual_transitions(
    video_path: str | Path,
    *,
    sample_fps: float = 10.0,
    window: int = 3,
    gradual_threshold: float = 0.45,
    hard_threshold: float = 0.45,
    sustained_ratio: float = 0.4,
) -> list[float]:
    """检测**渐变转场**（交叉溶解 / 淡入淡出）——ffmpeg 的 `scene` 滤镜测不到这一类。

    为什么需要（R2 就记录了这个方法学盲区，一直没修）：`select=gt(scene,thr)` 看的是
    **相邻两帧**的差异；而交叉溶解是"每帧只差一点点、累计起来画面全换了"，
    于是单帧差异永远低于阈值 → 溶接处被当成同一个镜头。

    做法（经典且零模型、零网络）：
    1. 低分辨率、低帧率采样（默认 10fps / 96×54），算 HSV 直方图（**L1 归一化**）；
    2. `d[i] = 1 - 直方图交集(h[i-1], h[i])` —— 单步变化量；
    3. `S[i] = 1 - 交集(h[i-w], h[i+w])` —— **跨窗口累计变化量**；
    4. 判定渐变：`S[i] >= gradual_threshold`、窗口内没有单步超过 `hard_threshold`
       （否则是硬切，交给 ffmpeg 那条路），**并且窗口内变化是"持续"的**。

    第 4 条的"持续性"是真实素材标定加上的（只靠累计量分不开）：
    实测海浪镜头的自然变化（水花翻涌）累计跨度 0.609，而一处弱交叉溶解只有 0.603——
    **单看累计量两者重叠**。区别在于：溶解是"每一帧都在变"（`d` 中位数 ≈ S/2w），
    浪花是"某一帧突变、其余帧几乎不动"（`d` 中位数 ≈ 0）。
    所以要求 `median(d over window) >= S/(2w) * sustained_ratio`。
    """
    try:
        import cv2
        import numpy as np
    except Exception as exc:  # noqa: BLE001 — 没有 cv2 就跳过这条路
        logger.info("cv2 不可用，渐变转场检测跳过：%s", exc)
        return []

    path = str(video_path)
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return []
        native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
        step = max(1, int(round(native_fps / max(0.5, sample_fps))))
        hists: list[Any] = []
        times: list[float] = []
        idx = 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    small = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
                    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                    hist = cv2.calcHist([hsv], [0, 1], None, [18, 16], [0, 180, 0, 256])
                    hists.append(_l1(hist.flatten().astype("float32")))
                    times.append(idx / native_fps)
            idx += 1
        cap.release()
    except Exception as exc:  # noqa: BLE001
        logger.info("渐变转场检测失败（跳过）：%s", exc)
        return []

    if len(hists) < window * 2 + 2:
        return []
    diffs = [1.0 - _hist_intersection(hists[i - 1], hists[i]) for i in range(1, len(hists))]
    # `diffs[i-1]` 对应"从 i-1 到 i"，与 hists 下标错开 1；这里的窗口按 times 索引对齐
    out: list[float] = []
    for i in range(window, len(hists) - window):
        span = 1.0 - _hist_intersection(hists[i - window], hists[i + window])
        if span < gradual_threshold:
            continue
        local = diffs[max(0, i - window - 1): i + window]
        if not local:
            continue
        if max(local) >= hard_threshold:
            continue        # 窗口里有硬切 → 那是硬切，不重复报
        expected = span / max(1, 2 * window)
        median = float(np.median(local))
        if median < expected * sustained_ratio:
            continue        # 单帧突变（水花/闪光），不是持续渐变 → 不是转场
        out.append(round(float(times[i]), 3))

    # 合并相邻命中（同一个溶接会连续多帧满足条件）
    merged: list[float] = []
    for t in out:
        if merged and t - merged[-1] <= 1.0:
            merged[-1] = round((merged[-1] + t) / 2, 3)     # 取中心
            continue
        merged.append(t)
    return merged


async def detect_cut_times(
    video_path: str | Path,
    threshold: float = 0.3,
    *,
    timeout: float = 600.0,
    detect_gradual: bool = True,
) -> list[float]:
    """ffmpeg 场景检测（硬切）+ 渐变转场检测 → 合并去重后的切点时间戳。

    失败一律返回空表，由调用方兜底成"整段一个镜头"（不猜）。
    `detect_gradual=False` 可关掉渐变检测（老行为；给对比实验用）。
    """
    path = str(video_path)
    cmd = [
        "ffmpeg", "-v", "info", "-i", path,
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    hard: list[float] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        hard = parse_scene_times(err.decode("utf-8", errors="replace"))
    except asyncio.TimeoutError:
        logger.warning("场景检测超时（%ss）: %s", timeout, path)
    except FileNotFoundError:
        logger.warning("ffmpeg 不可用，场景检测跳过: %s", path)
    except Exception as exc:
        logger.warning("场景检测失败: %s", exc)

    if not detect_gradual:
        return hard
    if not getattr(settings, "content_index_gradual_detect", True):
        return hard

    gradual = await asyncio.to_thread(detect_gradual_transitions, path)
    if not gradual:
        return hard
    # 去重：与硬切重合（±0.5s）的不重复报
    merged = list(hard)
    for t in gradual:
        if not any(abs(t - h) <= 0.5 for h in merged):
            merged.append(t)
    if len(merged) > len(hard):
        logger.info("渐变转场补检 %d 处（ffmpeg 硬切 %d 处）: %s",
                    len(merged) - len(hard), len(hard), path)
    return sorted(merged)


_META_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


async def probe_media(path: str | Path) -> dict[str, Any]:
    """ffprobe 元数据（带进程内缓存，键 = 路径 + mtime）。"""
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    key = str(p)
    cached = _META_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(p),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=30)
        data = json.loads(out.decode("utf-8", errors="replace") or "{}")
    except Exception as exc:
        logger.warning("ffprobe 失败: %s: %s", p.name, exc)
        return {}
    meta: dict[str, Any] = {
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
        "width": 0, "height": 0, "fps": 0.0, "has_audio": False,
    }
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not meta["width"]:
            meta["width"] = int(s.get("width") or 0)
            meta["height"] = int(s.get("height") or 0)
            num, _, den = str(s.get("r_frame_rate") or "0/1").partition("/")
            try:
                meta["fps"] = float(num) / float(den) if float(den or 0) else 0.0
            except ValueError:
                meta["fps"] = 0.0
        elif s.get("codec_type") == "audio":
            meta["has_audio"] = True
    _META_CACHE[key] = (mtime, meta)
    return meta


async def extract_shot_frames(
    video_path: str | Path,
    ranges: list[tuple[float, float]],
    out_dir: Path,
    *,
    frames_per_shot: int = DEFAULT_FRAMES_PER_SHOT,
    timeout: float = 30.0,
    progress: ProgressFn | None = None,
    progress_base: float = 0.0,
    progress_span: float = 1.0,
) -> list[list[str]]:
    """每个镜头在内部均匀抽 N 帧（N≥2 才能算帧间差）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = max(1, len(ranges))
    results: list[list[str]] = []
    for i, (t0, t1) in enumerate(ranges):
        n = max(1, int(frames_per_shot))
        shots_frames: list[str] = []
        for k in range(n):
            frac = (k + 0.5) / n
            ts = t0 + (t1 - t0) * frac
            path = out_dir / f"s{i:05d}_{k}.jpg"
            if path.exists():
                shots_frames.append(str(path))
                continue
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, ts):.3f}",
                "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(path),
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL)
                rc = await asyncio.wait_for(proc.wait(), timeout=timeout)
                if rc == 0 and path.exists():
                    shots_frames.append(str(path))
                elif path.exists():
                    path.unlink()
            except Exception as exc:
                logger.debug("抽帧失败 t=%.2f: %s", ts, exc)
        results.append(shots_frames)
        if progress:
            await _maybe_await(progress(progress_base + progress_span * (i + 1) / total,
                                        f"抽帧 {i + 1}/{total}"))
    return results


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value):
        await value


def _load_gray(path: str):
    """读成灰度 numpy 数组（失败返回 None）。"""
    try:
        from PIL import Image
        import numpy as np
    except Exception:  # pragma: no cover - 依赖缺失时降级
        return None
    try:
        with Image.open(path) as img:
            return np.asarray(img.convert("L"), dtype="float32")
    except Exception:
        return None


def _laplacian_var(gray) -> float:
    """Laplacian 方差（锐度代理）：无 cv2 时用 numpy 卷积实现，结果可比。"""
    try:
        import numpy as np
    except Exception:  # pragma: no cover
        return 0.0
    if gray is None or gray.size == 0:
        return 0.0
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype="float32")
    h, w = gray.shape
    if h < 3 or w < 3:
        return 0.0
    # 显式切片卷积（比 scipy 依赖更可控）
    center = gray[1:-1, 1:-1] * k[1, 1]
    lap = (center
           + gray[:-2, 1:-1] * k[0, 1]
           + gray[2:, 1:-1] * k[2, 1]
           + gray[1:-1, :-2] * k[1, 0]
           + gray[1:-1, 2:] * k[1, 2])
    return float(lap.var())


def compute_shot_stats(frame_paths: list[str]) -> ShotStats:
    """从镜头抽帧算廉价统计（无 ML）。

    `motion` = 帧间平均绝对差：单帧素材（frames_per_shot=1）时为 0，
    这是**已知的诚实缺省**，不代表镜头静止（调用方看 `frame_count` 区分）。
    """
    stats = ShotStats(frame_count=len(frame_paths or []))
    grays = [g for g in (_load_gray(p) for p in (frame_paths or [])) if g is not None]
    if not grays:
        return stats
    try:
        import numpy as np
    except Exception:  # pragma: no cover
        return stats
    main = grays[0]
    stats.brightness = round(float(main.mean()), 3)
    stats.contrast = round(float(main.std()), 3)
    stats.sharpness = round(_laplacian_var(main), 3)
    stats.is_black = stats.brightness <= BLACK_LUMA
    stats.is_white = stats.brightness >= WHITE_LUMA
    if len(grays) > 1:
        diffs = [float(np.abs(grays[i] - grays[i - 1]).mean())
                 for i in range(1, len(grays)) if grays[i].shape == grays[i - 1].shape]
        if diffs:
            stats.motion = round(sum(diffs) / len(diffs), 3)
    # 饱和度需要彩色图：单独读一次首帧
    try:
        from PIL import Image
        with Image.open(frame_paths[0]) as img:
            hsv = np.asarray(img.convert("HSV"), dtype="float32")
            stats.saturation = round(float(hsv[:, :, 1].mean()), 3)
    except Exception:
        pass
    return stats


def classify_motion(stats: ShotStats) -> str:
    """帧间差 → 镜头语言粗分类（R1 的启发式；带阈值区间，便于 R2 替换为模型输出）。"""
    if stats.frame_count < 2:
        return "unknown"
    if stats.is_black or stats.is_white:
        return "unknown"
    if stats.motion < 2.0:
        return "static"
    if stats.motion < 8.0:
        return "low"
    if stats.motion < 20.0:
        return "medium"
    return "high"


# ── 构建入口 ──────────────────────────────────────────────


async def build_index(
    video_path: str | Path,
    *,
    sha256: str = "",
    asset_id: str = "",
    filename: str = "",
    project_id: str | None = None,
    scene_threshold: float | None = None,
    frames_per_shot: int = DEFAULT_FRAMES_PER_SHOT,
    min_shot_sec: float = MIN_SHOT_SEC,
    max_shots: int = MAX_SHOTS,
    force: bool = False,
    keep_frames: bool = True,
    progress: ProgressFn | None = None,
    mirror: bool | None = None,
) -> ContentIndex:
    """构建（或复用）一段素材的内容索引。

    流程：缓存命中 → 直接返回；否则 元数据 → 场景切分 → 镜头区间 → 抽帧 → 廉价统计
    → 落盘（+ 可选 Mongo 镜像）。**不做任何 ML/云端调用**（那是 R2/R3 的事）。
    """
    started = time.perf_counter()
    path = Path(video_path)
    exists = path.exists()
    if not sha256:
        sha = sha256_of(path) if exists else ""
    else:
        sha = sha256
    fingerprint = current_fingerprint()

    if not force:
        cached = load_index(sha, project_id)
        if index_is_fresh(cached, sha, fingerprint):
            cached.served_from_cache = True
            logger.info("内容索引命中缓存: %s (%d 镜头)", sha[:12], len(cached.shots))
            return cached

    if not path.exists():
        raise FileNotFoundError(f"素材不存在: {video_path}")

    thr = float(scene_threshold if scene_threshold is not None
                else settings.content_index_scene_threshold)
    if progress:
        await _maybe_await(progress(0.05, "读取元数据"))
    meta = await probe_media(path)

    duration = float(meta.get("duration") or 0.0)
    if progress:
        await _maybe_await(progress(0.15, "场景切分"))
    cuts = await detect_cut_times(path, thr) if duration > 0 else []
    ranges = build_shot_ranges(cuts, duration, min_shot_sec=min_shot_sec, max_shots=max_shots)

    frames_root = index_dir(project_id) / "frames" / (sha[:12] or path.stem)
    if force and frames_root.exists():
        shutil.rmtree(frames_root, ignore_errors=True)
    if progress:
        await _maybe_await(progress(0.25, f"抽帧（{len(ranges)} 镜头）"))
    frames = await extract_shot_frames(
        path, ranges, frames_root, frames_per_shot=frames_per_shot,
        progress=progress, progress_base=0.25, progress_span=0.6)

    shots: list[ShotRecord] = []
    #: 音频/语音层的统计（写在索引 sources 里，便于排查"为什么没语音"）
    _extra_summary: dict[str, Any] = {}
    speech_stats: dict[str, Any] = {}
    sources: dict[str, str] = {
        "shots": "ffmpeg:scene", "stats": "pillow+numpy",
        "visual": "clip_zero_shot" if settings.content_index_clip_enabled else "classifier",
        "cloud_llm": "on" if settings.content_index_visual_llm else "off",
    }
    for i, (t0, t1) in enumerate(ranges):
        shot_frames = frames[i] if i < len(frames) else []
        stats = compute_shot_stats(shot_frames)
        motion = classify_motion(stats)
        shots.append(ShotRecord(
            index=i, t0=t0, t1=t1, duration_sec=round(t1 - t0, 3),
            keyframes=shot_frames if keep_frames else [],
            stats=stats,
            visual={"camera_motion": motion},  # type: ignore[arg-type]
            evidence={"stats": "ffmpeg+pillow+numpy", "camera_motion": "frame_diff_heuristic"},
            confidence={"camera_motion": 0.4 if motion != "unknown" else 0.0},
        ))

    # ── R2：画面维度（本地模型优先 + 云端按需）＋ 质量维度 ──
    if settings.content_index_enabled and frames:
        if progress:
            await _maybe_await(progress(0.88, f"画面标注（{len(shots)} 镜头）"))
        try:
            from clipwright.services import content_vision as cv2mod

            labels = await cv2mod.label_shots(
                frames,
                concurrency=settings.content_index_label_concurrency,
                use_clip=settings.content_index_clip_enabled,
                use_llm=settings.content_index_visual_llm,
                llm_max_shots=settings.content_index_llm_max_shots,
                progress=progress, progress_base=0.88, progress_span=0.05,
            )
            for shot, extra in zip(shots, labels):
                if not extra:
                    continue
                for field, value in (extra.get("visual") or {}).items():
                    setattr(shot.visual, field, value)
                shot.evidence.update(extra.get("evidence") or {})
                shot.confidence.update(extra.get("confidence") or {})
                quality = cv2mod.detect_quality_flags(shot.keyframes or [])
                for field, value in quality.items():
                    setattr(shot.stats, field, value)
                if quality:
                    shot.evidence["stats.quality"] = "pillow+numpy"
        except Exception as exc:  # 标注失败不能拖垮索引
            logger.warning("画面标注失败（索引仍可用）: %s", exc)

    # ── 本地描述合成（真实素材实测暴露的缺口）──
    # `visual.description` 此前只来自云端多模态 / 分类器兜底，而 CLIP 一旦给出标签
    # 分类器分支就被跳过 → 真实素材上描述覆盖率是 **0**，每段素材只剩 1 个标签，
    # 检索可匹配文本极少、方案接地 0 命中。
    # 这里用**已测到的字段**（明暗/景别/运镜/质量/标签/语音/音频事件）拼一句可检索的中文描述：
    # 零模型调用、零网络，且 `evidence` 写明来源是 composed，不与"模型级理解"混为一谈。
    if settings.content_index_enabled and shots:
        try:
            from clipwright.services import content_describe as cd

            filled = cd.fill_descriptions(
                shots, blurry_threshold=float(settings.content_index_blurry_sharpness))
            if filled:
                sources["visual_description"] = "composed:stats+clip+audio"
                logger.info("补写本地合成描述 %d/%d 个镜头", filled, len(shots))
        except Exception as exc:  # 描述合成失败不能拖垮索引
            logger.warning("本地描述合成失败（索引仍可用）: %s", exc)

    # ── R9：画面语义向量（供"换个说法也能搜到"用）──
    # 存进索引随索引落盘，之后检索只是点积（不再碰模型）。
    # 与零样本标注共用 `content_index_clip_enabled` 开关：两者用的是同一个 CLIP 模型，
    # "关掉 CLIP"应当意味着**完全不加载它**（否则 `--no-label` 这类快路径仍会拉 600MB 权重，
    # 实测就是被这个坑到：号称跳过标注、实际还是跑了 30 秒的向量计算）。
    # CLIP 不可用时**静默跳过**并在 sources 里如实标注——不假装有向量。
    #
    # 进度值取 0.93：与"画面标注"的末值同档。**必须落在音频层（0.94）之前或与之相等**，
    # 否则进度回调会倒退（实测：第一版用 0.98，直接把"进度单调"的既有用例打红）。
    if (getattr(settings, "content_index_visual_vectors", True)
            and settings.content_index_clip_enabled and frames):
        try:
            from clipwright.services import content_semantic as sem

            if progress:
                await _maybe_await(progress(0.93, f"视觉向量（{len(shots)} 镜头）"))
            _idx_stub = ContentIndex(sha256=sha, asset_path=str(path), shots=shots)
            _idx_stub, vec_count = await asyncio.to_thread(
                sem.attach_visual_vectors, _idx_stub)
            if vec_count:
                sources["embeddings_visual"] = f"clip:{settings.content_index_clip_model}"
            else:
                sources["embeddings_visual"] = "unavailable"
        except Exception as exc:  # 向量层失败不能拖垮索引
            logger.warning("视觉向量计算失败（索引仍可用，语义检索退化为文本）: %s", exc)
            sources["embeddings_visual"] = "failed"

    # ── R3：音频维度（音频事件 + 语音对齐 + 关键词事件） ──
    if settings.content_index_audio_events and ranges:
        try:
            from clipwright.services import content_audio as camod
            from clipwright.services.event_lexicon import resolve_lexicon

            if progress:
                await _maybe_await(progress(0.94, "音频事件检测"))
            wav = await camod.extract_audio_wav(path, frames_root.parent / "audio" / f"{sha[:12]}.wav")
            if wav is not None:
                signal, sr = camod.read_wav_mono(wav)
                if signal is not None and sr > 0:
                    audio_events = camod.merge_events(camod.detect_audio_events(signal, sr))
                    for shot in shots:
                        shot.audio_events = camod.events_for_shot(audio_events, shot.t0, shot.t1)
                    index_audio_summary = camod.summarize_audio_events(audio_events)
                    sources["audio_events"] = "short_term_rms"
                    _extra_summary = index_audio_summary
                else:
                    logger.info("音频解码失败，跳过音频事件: %s", path.name)
        except Exception as exc:  # 音频层失败不能拖垮索引
            logger.warning("音频事件检测失败（索引仍可用）: %s", exc)

    if settings.content_index_asr and ranges:
        try:
            from clipwright.services import content_audio as camod

            if progress:
                await _maybe_await(progress(0.96, "转写对齐"))
            lex = resolve_lexicon(getattr(settings, "content_index_event_domain", "generic"),
                                  getattr(settings, "content_index_event_lexicon", "") or "")
            speech_rows, stats = await camod.align_transcript_to_shots(
                path, ranges, language=settings.content_index_asr_language, lexicon=lex)
            for shot, row in zip(shots, speech_rows):
                if row.get("text"):
                    shot.speech.text = row["text"]
                    shot.speech.phrases = row.get("phrases") or []
                    shot.speech.keywords = row.get("keywords") or []
                    shot.speech.speaker = row.get("speaker") or ""
                    shot.evidence["speech"] = "stt+overlap_align"
                    shot.confidence["speech"] = 0.6
            sources["speech"] = f"stt/{stats.get('error') or 'ok'}"
            speech_stats = stats
        except Exception as exc:
            logger.warning("转写对齐失败（索引仍可用）: %s", exc)

    index = ContentIndex(
        sha256=sha, asset_id=asset_id, asset_path=str(path),
        filename=filename or path.name,
        duration_sec=round(duration, 3),
        width=int(meta.get("width") or 0), height=int(meta.get("height") or 0),
        fps=round(float(meta.get("fps") or 0.0), 3),
        index_version=INDEX_VERSION, model_fingerprint=fingerprint,
        scene_threshold=thr, shots=shots,
        sources=sources,
        created_at=now_iso(),
    )
    if not keep_frames:
        # 不保留代表帧时把临时抽帧目录清掉（索引只留统计与语义）
        shutil.rmtree(frames_root, ignore_errors=True)
    index.build_ms = int((time.perf_counter() - started) * 1000)
    # 音频/语音层统计落到 sources（不在 schema 里新增易变字段；摘要里已能表达）
    if _extra_summary:
        index.sources["audio_loud"] = str(_extra_summary.get("by_kind", {}).get("loud", 0))
        index.sources["audio_silence"] = str(_extra_summary.get("by_kind", {}).get("silence", 0))
    if speech_stats:
        index.sources["speech_shots"] = str(speech_stats.get("shots_with_speech", 0))
    save_index(index, project_id)
    if progress:
        # 0.97 而不是 0.95：转写层（0.96）之后才写索引，用 0.95 会让进度倒退
        # （转写默认关闭所以一直没暴露；顺手修掉）
        await _maybe_await(progress(0.97, "写入索引"))

    should_mirror = settings.content_index_mirror_mongo if mirror is None else mirror
    if should_mirror:
        try:
            mirrored = await mirror_index(index)
            if mirrored:
                index.sources["mongo"] = "mirrored"
        except Exception as exc:  # 镜像失败不影响索引本身
            logger.info("内容索引 Mongo 镜像失败（忽略）: %s", exc)
    if progress:
        await _maybe_await(progress(1.0, f"完成（{len(shots)} 镜头 / {index.build_ms}ms）"))
    logger.info("内容索引构建完成: %s → %d 镜头 / %dms", sha[:12], len(shots), index.build_ms)
    return index


async def ensure_index(
    asset: Any,
    *,
    project_id: str | None = None,
    force: bool = False,
    **kwargs: Any,
) -> Optional[ContentIndex]:
    """从素材对象（`AssetInfo` / dict）确保索引存在；非视频返回 None。

    这是给 Agent/工具层用的门面：调用方只需要给"素材"，不必关心路径与缓存细节。
    """

    def _val(name: str) -> Any:
        if hasattr(asset, name):
            return getattr(asset, name)
        getter = getattr(asset, "get", None)
        return getter(name) if callable(getter) else None

    media_type = str(_val("media_type") or "")
    file_path = str(_val("file_path") or "")
    if media_type and media_type != "video":
        return None
    if not file_path or not Path(file_path).exists():
        return None
    try:
        return await build_index(
            file_path, sha256=str(_val("sha256") or ""),
            asset_id=str(_val("asset_id") or ""),
            filename=str(_val("filename") or ""),
            project_id=project_id, force=force, **kwargs)
    except Exception as exc:
        logger.warning("内容索引构建失败（忽略）: %s", exc)
        return None


# ── Mongo 镜像与跨项目检索 ────────────────────────────────

COLLECTION = "content_index"


def _collection(create: bool = False):
    """拿集合（未连接返回 None；best-effort，不让 Mongo 成为硬依赖）。"""
    from clipwright.context import mongo
    if not mongo.is_connected:
        return None
    try:
        coll = mongo.db[COLLECTION]
        if create:
            coll.create_index("sha256")
            coll.create_index([("search_text", "text")])
        return coll
    except Exception as exc:  # pragma: no cover - 环境相关
        logger.debug("content_index 集合不可用: %s", exc)
        return None


async def mirror_index(index: ContentIndex) -> bool:
    """把索引 upsert 到 Mongo（跨项目检索的唯一载体；失败静默）。"""
    coll = _collection(create=True)
    if coll is None:
        return False
    doc = index.to_mongo_doc()
    try:
        await asyncio.to_thread(
            coll.replace_one, {"_id": doc["_id"]}, doc, True)
        return True
    except Exception as exc:
        logger.info("内容索引镜像写入失败: %s", exc)
        return False


async def find_indexes(
    *,
    sha256: str = "",
    keyword: str = "",
    project_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """跨项目检索索引（R1 提供基础能力：按内容 sha / 关键词）。

    返回裁剪过的文档（去掉 shots 明细，避免把整个索引塞进 Agent 上下文；
    需要明细时再用 `load_index(sha)` 取本地文件）。
    """
    coll = _collection()
    if coll is None:
        return []
    query: dict[str, Any] = {}
    if sha256:
        query["sha256"] = sha256
    if keyword:
        pattern = re.escape(keyword)
        query["$or"] = [
            {"search_text": {"$regex": pattern, "$options": "i"}},
            {"filename": {"$regex": pattern, "$options": "i"}},
        ]
    if project_id:
        query["project_id"] = project_id
    projection = {"shots": 0, "search_text": 0}
    try:
        docs = await asyncio.to_thread(
            lambda: list(coll.find(query, projection).limit(max(1, int(limit)))))
    except Exception as exc:
        logger.info("内容索引检索失败: %s", exc)
        return []
    for d in docs:
        d.pop("_id", None)
    return docs


async def count_indexes() -> int:
    """已镜像的索引数量（健康检查/统计用）。"""
    coll = _collection()
    if coll is None:
        return 0
    try:
        counter = coll.estimated_document_count
        # pymongo 上是方法（会真的查一次）；替身/异种实现可能是属性
        result = await asyncio.to_thread(counter) if callable(counter) else counter
        return int(result)
    except Exception:
        return 0


def local_index_files(project_id: str | None = None) -> list[Path]:
    """本地已有的索引文件列表（离线清点用）。"""
    d = index_dir(project_id)
    return sorted(d.glob("*.json"))


def summarize_index_files(project_id: str | None = None) -> dict[str, Any]:
    """本地索引总览（不读全量 JSON，只统计文件数/大小）。"""
    files = local_index_files(project_id)
    return {
        "dir": str(index_dir(project_id)),
        "count": len(files),
        "total_bytes": sum(f.stat().st_size for f in files if f.exists()),
        "frames_dir": str(index_dir(project_id) / "frames"),
    }
