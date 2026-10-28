"""镜头画面维度标注（R2）——本地模型优先，云端多模态按需。

分层策略（与计划一致）：

| 层 | 手段 | 依赖 | 成本 |
|----|------|------|------|
| **L1-a 标签** | 本地 transformers 图像分类（`settings.vision_model`，ImageNet 族） | 离线，权重已在 HF 缓存 | CPU ~40ms/帧 |
| **L1-b 零样本** | CLIP 零样本打分（可配置标签词表，R9 的召回复用同一套编码） | 需 CLIP 权重 | CPU ~80ms/帧 |
| **L1-c 镜头语言** | OpenCV 内置人脸/人体检测 → 主体占比 → 景别粗分类 | 离线（`cv2` 自带级联 + HOG） | CPU ~30ms/帧 |
| **L1-d 质量** | 模糊/过曝/欠曝/锐度（统计层已算一部分） | 离线 | 近零 |
| **L3 云端** | 多模态 LLM 生成一句话描述（`content_index_visual_llm`） | 网络 + 计费 | 只对候选镜头，受 `budget.py` 约束 |

**为什么把标签做成"可插拔标注器"**：ImageNet-1k 的分类标签很粗（且对合成/陌生画面基本是噪声，
实测纯红方块会被判成"双簧管"），它只能给出低置信度的粗标签；真正能回答"找有咖啡杯的镜头"
的是 CLIP 零样本。把两者放在同一个接口后面，R9 换模型/加词表时不必碰索引逻辑。

诚实的边界：本模块**不做**动作识别（"进球""开箱"需要 R3 的事件词表或专用模型），
也不做 OCR（比分牌在后续轮次）。没检出主体时景别给 `unknown`，**不猜**。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger, settings

#: 主体占比 → 景别阈值（人脸/人体框面积 ÷ 画面面积）。
#: 阈值来自"人脸占画面比例"的通用经验区间（占满 → 特写；极小 → 远景），
#: 属于**可调启发式**，真实素材上应按题材重新标定（已在文档标注）。
SHOT_SIZE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.35, "extreme_close"),
    (0.12, "close"),
    (0.04, "medium"),
    (0.012, "full"),
    (0.0, "wide"),
)

#: 默认零样本词表（中文标签 → 英文提示）。按"剪辑时真的会问的东西"组织：
#: 主体、场景、镜头性质。用户查询里出现的词命中率越高，R4 的问答就越准。
DEFAULT_LABEL_VOCAB: dict[str, str] = {
    "人物": "a person",
    "多人": "a group of people",
    "人脸特写": "a close-up of a human face",
    "儿童": "a child",
    "动物": "an animal",
    "食物": "food",
    "咖啡杯": "a coffee cup",
    "手机": "a smartphone",
    "电脑": "a laptop computer",
    "汽车": "a car",
    "建筑": "a building",
    "天空": "the sky",
    "水面": "water",
    "植物": "plants",
    "室内": "an indoor scene",
    "室外": "an outdoor scene",
    "夜景": "a night scene",
    "屏幕录制": "a screen recording of a computer interface",
    "字幕板": "a title card with text",
    "空镜": "an empty scene without people",
    "运动": "sports in action",
}
#: 可能被 CLIP 判为"在场"的负面提示（提高对比度，减少"什么都像"）
_NEGATIVE_HINTS = ["a blurry abstract image", "a black frame", "a solid color background"]

#: 检测前把图缩放到的最长边（景别只看占比，缩放不影响比例；4K 原图直接跑检测太慢）
_DETECT_MAX_SIDE = 640
#: HOG 行人检测的窗口尺寸（OpenCV 默认）；图小于它时必须跳过，否则会崩进程
_HOG_WIN = (64, 128)


def imread_unicode(path: str | Path) -> Any:
    """读图（**支持中文/空格路径**）。

    为什么需要：`cv2.imread` 在 Windows 上按本地代码页解析路径，中文名一律返回 None
    （日志里是 `can't open/read file`），于是"视觉解读"对中文素材静默失效——
    实测过工作区 `封面图.png` 就是这么被跳过的。`np.fromfile + imdecode` 绕开路径编码。
    """
    try:
        import cv2
        import numpy as np

        raw = np.fromfile(str(path), dtype=np.uint8)
        if raw.size == 0:
            return None
        return cv2.imdecode(raw, cv2.IMREAD_COLOR)
    except Exception as exc:  # noqa: BLE001 — 读图失败由上层降级
        logger.debug("读图失败 %s: %s", path, exc)
        return None


def classify_shot_size(area_ratio: float) -> str:
    """主体占比 → 景别（纯函数，便于单测与后续替换为模型输出）。"""
    for threshold, label in SHOT_SIZE_THRESHOLDS:
        if area_ratio >= threshold:
            return label
    return "wide"


class _Detectors:
    """OpenCV 检测器（懒加载 + 进程内复用）。

    用 OpenCV **自带**的人脸级联与 HOG 行人检测：无需下载权重、无需 GPU，
    作为"有没有人 / 人是大是小"的第一近似足够；精度不如专用模型，
    因此只用来决定景别与 `has_face` 这两个粗字段。
    """

    def __init__(self) -> None:
        self._face = None
        self._hog = None
        self._failed = False

    def _ensure(self) -> bool:
        if self._failed:
            return False
        if self._face is not None:
            return True
        try:
            import cv2

            cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            cascade = cv2.CascadeClassifier(str(cascade_path))
            if cascade.empty():
                raise RuntimeError(f"级联加载失败: {cascade_path}")
            self._face = cascade
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            return True
        except Exception as exc:
            logger.info("OpenCV 检测器不可用（景别将保持 unknown）: %s", exc)
            self._failed = True
            return False

    def detect(self, image_path: str) -> dict[str, Any]:
        """返回 {face_ratio, person_ratio, has_face}；失败返回空 dict。

        两个必须的防护（实测踩过）：
        1. **先按最长边缩放到 ≤`_DETECT_MAX_SIDE`**：1080p/4K 帧上直接跑 HOG 会慢一个数量级，
           而景别只关心"主体占比"，缩放后比例不变；
        2. **HOG 前检查窗口能否装下**：HOG 默认 `winSize=64x128`，在比它还小的图上调用
           `detectMultiScale` 会构造病态金字塔——在本机实测会导致**进程级崩溃**（不是异常，
           捕获不到）。因此小于窗口的图直接跳过人体检测，只跑人脸级联。
        """
        if not self._ensure():
            return {}
        try:
            import cv2

            img = imread_unicode(image_path)
            if img is None:
                return {}
            h, w = img.shape[:2]
            scale = 1.0
            longest = max(h, w)
            if longest > _DETECT_MAX_SIDE:
                scale = _DETECT_MAX_SIDE / float(longest)
                img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                                 interpolation=cv2.INTER_AREA)
                h, w = img.shape[:2]
            total = float(max(1, h * w))
            out: dict[str, Any] = {"has_face": False, "face_ratio": 0.0, "person_ratio": 0.0}
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            min_face = max(12, h // 20)
            faces = self._face.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                                minSize=(min_face, min_face))
            if len(faces):
                # 取最大人脸：剪辑关心"主体有多大"
                biggest = max(faces, key=lambda f: int(f[2]) * int(f[3]))
                out["has_face"] = True
                out["face_ratio"] = round((int(biggest[2]) * int(biggest[3])) / total, 5)
            if w >= _HOG_WIN[0] and h >= _HOG_WIN[1]:
                try:
                    rects, weights = self._hog.detectMultiScale(
                        img, winStride=(8, 8), padding=(8, 8), scale=1.05)
                    keep = [r for r, wt in zip(rects, weights) if float(wt) > 0.5]
                    if len(keep):
                        area = sum(int(r[2]) * int(r[3]) for r in keep)
                        out["person_ratio"] = round(min(1.0, area / total), 5)
                except Exception:
                    pass  # HOG 在边缘尺寸上可能报错：忽略，人脸结果仍然可用
            return out
        except Exception as exc:
            logger.debug("OpenCV 检测失败: %s", exc)
            return {}


_detectors = _Detectors()
_clip_cache: dict[str, Any] = {}
_clip_failed = False


def _load_clip() -> Optional[tuple[Any, Any]]:
    """懒加载 CLIP（失败后不再重试，避免每次抽帧都去碰网络）。"""
    global _clip_failed
    if _clip_failed:
        return None
    if "model" in _clip_cache:
        return _clip_cache["model"]
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        # 离线优先：配了本地目录就只读本地（内网/无网环境必须可用）；
        # 没配则按 HF 缓存加载（`HF_HUB_OFFLINE=1` 时也不会联网）。
        model_name = str(getattr(settings, "content_index_clip_model_path", "") or "").strip() \
            or settings.content_index_clip_model
        model = CLIPModel.from_pretrained(model_name, local_files_only=bool(
            getattr(settings, "content_index_clip_model_path", "")))
        proc = CLIPProcessor.from_pretrained(model_name, local_files_only=bool(
            getattr(settings, "content_index_clip_model_path", "")))
        model.eval()
        _clip_cache["model"] = (model, proc)
        _clip_cache["torch"] = torch
        logger.info("CLIP 已加载: %s", model_name)
        return (model, proc)
    except Exception as exc:
        logger.info("CLIP 不可用（将只用分类标签）: %s", exc)
        _clip_failed = True
        return None


def _clip_text_inputs(prompts: tuple[str, ...]) -> Any:
    """文本侧输入只编码一次（词表固定时跨镜头复用）——实测可省掉约一半耗时。"""
    key = ("text", prompts)
    if key in _clip_cache:
        return _clip_cache[key]
    _model, proc = _clip_cache["model"]
    inputs = proc(text=list(prompts), return_tensors="pt", padding=True)
    _clip_cache[key] = inputs
    return inputs


def clip_scores_for_paths(
    image_paths: list[str],
    vocab: dict[str, str] | None = None,
) -> list[dict[str, float]]:
    """批量零样本打分：返回每个镜头的 `{标签: 概率}`（保持输入顺序）。

    比"逐张调用"省一半时间：文本编码只做一次，且图片按批前向。
    """
    loaded = _load_clip()
    if not loaded or not image_paths:
        return [{} for _ in image_paths]
    model, proc = loaded
    torch = _clip_cache["torch"]
    vocab = vocab or DEFAULT_LABEL_VOCAB
    prompts = tuple(list(vocab.values()) + _NEGATIVE_HINTS)
    names = list(vocab.keys()) + ["" for _ in _NEGATIVE_HINTS]
    try:
        from PIL import Image

        images = []
        for p in image_paths:
            with Image.open(p) as img:
                images.append(img.convert("RGB"))
        text_inputs = _clip_text_inputs(prompts)
        img_inputs = proc(images=images, return_tensors="pt")
        with torch.no_grad():
            logits = model(input_ids=text_inputs["input_ids"],
                           attention_mask=text_inputs.get("attention_mask"),
                           pixel_values=img_inputs["pixel_values"]).logits_per_image
            probs = logits.softmax(dim=1)
        out: list[dict[str, float]] = []
        for row in probs:
            scores = {n: round(float(v), 4) for n, v in zip(names, row.tolist()) if n}
            out.append(scores)
        return out
    except Exception as exc:
        logger.debug("CLIP 批量打分失败: %s", exc)
        return [{} for _ in image_paths]


def clip_zero_shot(image_path: str, top_k: int = 5,
                   vocab: dict[str, str] | None = None) -> list[tuple[str, float]]:
    """CLIP 零样本：返回 [(中文标签, 概率)] 降序；不可用返回空表。"""
    scored = clip_scores_for_paths([image_path], vocab=vocab)
    if not scored or not scored[0]:
        return []
    items = sorted(scored[0].items(), key=lambda kv: kv[1], reverse=True)
    return items[:max(1, top_k)]


async def label_shot(
    frame_paths: list[str],
    *,
    use_clip: bool = True,
    use_llm: bool = False,
    want_description: bool = False,
    clip_top_k: int = 5,
    clip_min_score: float = 0.15,
    clip_scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    """给单个镜头的关键帧打标：返回 `{visual 字段, evidence, confidence}`。

    设计要点：
    - 只对**首帧**做语义标注（多帧语义几乎相同，成本翻倍收益极小；运动由 stats 覆盖）；
    - 分类标签与 CLIP 标签**都保留并标出来源**，不互相覆盖；
    - `clip_scores` 可由批处理预先算好传入（见 `label_shots`），避免逐张重复前向；
    - `want_description=True` 时，即使 CLIP/分类器已经给了标签，也额外向多模态模型要
      一句画面描述（工作区面板与检索需要"话"，CLIP 只给词）；批量抽帧默认关闭以省成本；
    - 云端 LLM 失败静默降级（索引不能因为云挂了而失败），但**绝不把降级报成成功**：
      只有 `source=="llm"` 才算真看过图。
    """
    frames = [p for p in (frame_paths or []) if p and Path(p).exists()]
    if not frames:
        return {}
    first = frames[0]
    visual: dict[str, Any] = {}
    evidence: dict[str, str] = {}
    confidence: dict[str, float] = {}

    # ── L1-a/L1-b：语义标签 ──
    tags: list[str] = []
    labels: list[str] = []
    description = ""
    scene_type = ""
    source = ""
    if use_clip:
        scores = clip_scores if clip_scores is not None else dict(
            clip_zero_shot(first, top_k=clip_top_k))
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:max(1, clip_top_k)]
        for name, score in ranked:
            if score < clip_min_score:
                continue
            if name not in tags:
                tags.append(name)
            labels.append(f"clip:{name}")
            confidence[f"clip.{name}"] = round(float(score), 4)
        if tags:
            evidence["visual.tags"] = "clip_zero_shot"
            source = "clip"
    # 只有 clip/classifier 命中时也去要一句"画面描述"——工作区面板和检索都靠描述，
    # 而 CLIP 只给标签不给话。`want_description` 由调用方决定（索引路径要，批量抽帧不要）。
    if (not tags or want_description) and use_llm and settings.content_index_visual_llm:
        try:
            from clipwright.services.vision import VisionService

            res = await VisionService().analyze_image(first, provider="llm")
            # **只认"真看过图"的结果**：LLM 失败时 vision.py 会内部回退成
            # `_fallback_analyze`（source=filename，model=filename，
            # description="文件: x.png (WxH)"）。旧实现只判 description 非空，
            # 于是把文件名兜底当成模型看懂了，上层随之报 status=ok —— A11 的假阳性。
            # source 缺失时按 model 推断（老调用方/替身不带 source）。
            _src = str(res.get("source") or "").strip()
            _model = str(res.get("model") or "")
            if not _src:
                _src = "filename" if _model.startswith("filename") else "llm"
            if _src == "llm" and res.get("description"):
                description = str(res["description"])
                # vision.py 的 model 可能是 "llm/<name>"，也可能只是 "llm"；统一前缀
                _model_name = _model or "vision"
                if _model_name.startswith("llm/"):
                    _model_name = _model_name[len("llm/"):]
                evidence["visual.description"] = f"llm/{_model_name}"
                confidence["visual.description"] = 0.6
                source = "llm"
                for t in (res.get("tags") or []):
                    if t and t not in tags:
                        tags.append(str(t))
                if res.get("scene_type") and not scene_type:
                    scene_type = str(res["scene_type"])
        except Exception as exc:
            logger.info("云端多模态描述失败（已降级）: %s", exc)

    if not tags:
        # 本地分类器（离线保底；ImageNet 标签较粗，仅给低置信度粗标签）
        try:
            from clipwright.services.vision import VisionService

            res = await VisionService().analyze_image(first, provider="transformers")
            tags = [t for t in (res.get("tags") or []) if t]
            labels = list(res.get("labels") or [])
            if not description:
                description = str(res.get("description") or "")
            if not scene_type:
                scene_type = str(res.get("scene_type") or "")
            source = source or str(res.get("source") or "")
            if tags:
                evidence["visual.tags"] = f"classifier/{res.get('model') or 'transformers'}"
                confidence["visual.tags"] = 0.3  # ImageNet 粗标签：低置信度，如实标注
        except Exception as exc:
            logger.debug("镜头分类标签失败: %s", exc)

    if tags:
        visual["tags"] = tags[:8]
    if description:
        visual["description"] = description
    if labels:
        visual["labels"] = labels[:8]
    if scene_type:
        visual["scene_type"] = scene_type
    # 来源要显式透出去：上层据此区分"真看了"（llm/clip/classifier）与"只看了文件名"
    if source:
        visual["source"] = source
    else:
        visual["source"] = "filename"
    if evidence:
        visual["evidence"] = dict(evidence)

    # ── L1-c：镜头语言（人脸/人体占比 → 景别）──
    det = _detectors.detect(first)
    if det:
        ratio = max(float(det.get("face_ratio") or 0.0), float(det.get("person_ratio") or 0.0))
        visual["shot_size"] = classify_shot_size(ratio)
        visual["has_face"] = bool(det.get("has_face"))
        evidence["visual.shot_size"] = "cv2:haar+hog"
        confidence["visual.shot_size"] = 0.5
        confidence["visual.has_face"] = 0.6
    return {"visual": visual, "evidence": evidence, "confidence": confidence}


async def label_shots(
    shot_frames: list[list[str]],
    *,
    concurrency: int = 4,
    use_clip: bool = True,
    use_llm: bool = False,
    clip_min_score: float = 0.15,
    llm_max_shots: int = 0,
    progress: Any = None,
    progress_base: float = 0.0,
    progress_span: float = 0.0,
) -> list[dict[str, Any]]:
    """批量标注（有界并发；单镜头失败不影响其它镜头）。

    CLIP 走**整批一次前向**（文本只编码一次，实测省约一半时间）；
    其余维度（分类标签 / 镜头语言 / 云端描述）逐镜头并发。
    `llm_max_shots > 0` 时只对前 N 个镜头调用云端多模态（预算闸门）。
    """
    total = max(1, len(shot_frames))
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    done = 0

    # ── 第一遍：CLIP 整批打分（首帧）──
    firsts = [(f[0] if f else "") for f in shot_frames]
    clip_scores: list[dict[str, float]] = [{} for _ in shot_frames]
    if use_clip:
        valid_idx = [i for i, p in enumerate(firsts) if p and Path(p).exists()]
        if valid_idx:
            scored = await asyncio.to_thread(
                clip_scores_for_paths, [firsts[i] for i in valid_idx],
                vocab_for())
            for i, scores in zip(valid_idx, scored):
                clip_scores[i] = scores

    async def _one(i: int, frames: list[str]) -> dict[str, Any]:
        nonlocal done
        async with sem:
            allow_llm = use_llm and (llm_max_shots <= 0 or i < llm_max_shots)
            try:
                result = await label_shot(
                    frames, use_clip=bool(clip_scores[i]), use_llm=allow_llm,
                    clip_min_score=clip_min_score,
                    clip_scores=clip_scores[i],
                )
            except Exception as exc:  # 单个镜头失败 → 空结果
                logger.debug("镜头标注失败: %s", exc)
                result = {}
        done += 1
        if progress is not None:
            value = progress(progress_base + progress_span * done / total,
                             f"标注 {done}/{total}")
            if asyncio.iscoroutine(value):
                await value
        return result

    return await asyncio.gather(*(_one(i, f) for i, f in enumerate(shot_frames)))


def clip_available() -> bool:
    """CLIP 是否可用（供调用方决定要不要按零样本结果建索引）。"""
    return _load_clip() is not None


def vocab_for(content_index_clip_vocab: str | None = None) -> dict[str, str]:
    """词表：设置项 JSON 覆盖 > 默认词表。格式 `{"中文标签": "english prompt"}`。"""
    raw = (content_index_clip_vocab if content_index_clip_vocab is not None
           else getattr(settings, "content_index_clip_vocab", "") or "")
    if not raw.strip():
        return dict(DEFAULT_LABEL_VOCAB)
    try:
        import json

        data = json.loads(raw)
        if isinstance(data, dict) and data:
            return {str(k): str(v) for k, v in data.items()}
    except Exception as exc:
        logger.warning("内容索引 CLIP 词表 JSON 解析失败，用默认词表: %s", exc)
    return dict(DEFAULT_LABEL_VOCAB)


def detect_quality_flags(frame_paths: list[str]) -> dict[str, Any]:
    """质量维度（模糊/过曝/欠曝）：在统计层之外补三个可直接用于筛选的布尔量。"""
    frames = [p for p in (frame_paths or []) if p and Path(p).exists()]
    if not frames:
        return {}
    try:
        import numpy as np
        from PIL import Image

        with Image.open(frames[0]) as img:
            arr = np.asarray(img.convert("L"), dtype="float32")
        total = float(arr.size or 1)
        over = float((arr > 245).sum()) / total
        under = float((arr < 10).sum()) / total
        # 边缘能量代理：Sobel 幅度均值（越低越糊）。用 numpy 手算避免引入依赖。
        gx = np.abs(np.diff(arr, axis=1)).mean() if arr.shape[1] > 1 else 0.0
        gy = np.abs(np.diff(arr, axis=0)).mean() if arr.shape[0] > 1 else 0.0
        return {
            "overexposed_ratio": round(over, 4),
            "underexposed_ratio": round(under, 4),
            "edge_energy": round(float(gx + gy), 3),
        }
    except Exception:
        return {}
