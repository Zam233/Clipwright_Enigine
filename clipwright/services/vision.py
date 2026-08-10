"""视觉识别服务 — 识图 + 自动打标签 + 生成素材。

策略（按优先级）：
1. LLM 多模态（CLIPWRIGHT_VISION_PROVIDER=llm）— 调用已配置的 LLM
   支持 Qwen-VL / Claude 3 Vision / GPT-4V 等
2. transformers 图像分类（默认）— ViT / ResNet 等 HF 模型
3. 保底：文件名 + FFmpeg/ffprobe 元数据分析
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger

# E8/E9: 视觉识别结果缓存 — {str(path):mtime_ns → result}，TTL 1h，上限 512。
# 多 Agent 重复分析同一图片（AnimationAgent 图片索引 / Quality 帧检查）共享缓存。
_VISION_CACHE: dict[str, tuple[dict[str, Any], float]] = {}
_VISION_CACHE_MAX = 512
_VISION_CACHE_TTL = 3600.0


def clear_vision_cache() -> None:
    """清空视觉识别缓存（测试/重置用）。"""
    _VISION_CACHE.clear()


def _vision_cache_key(image_path: str) -> str:
    """缓存键 = 规范化路径 + 文件 mtime_ns（文件变更后自动失效）。"""
    try:
        st = Path(image_path).stat()
        mtime = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
    except OSError:
        mtime = 0
    return f"{str(Path(image_path).resolve())}:{mtime}"

# 常见视频/图片内容的标签映射（保底方案）
_FALLBACK_TAGS: dict[str, list[str]] = {
    "nature": ["自然", "风光", "风景", "户外"],
    "city": ["城市", "建筑", "街道", "都市"],
    "people": ["人物", "人像", "人群"],
    "tech": ["科技", "数码", "电子", "编程"],
    "food": ["食物", "美食", "餐饮"],
    "animal": ["动物", "宠物"],
    "car": ["汽车", "车辆", "交通"],
    "water": ["水", "海洋", "河流", "湖泊"],
    "sky": ["天空", "云", "日落", "日出"],
    "abstract": ["抽象", "图案", "纹理"],
    "dark": ["暗调", "夜景", "暗色"],
    "bright": ["亮调", "明亮", "高调"],
    "warm": ["暖色", "温暖"],
    "cold": ["冷色", "冷调"],
}

# LLM 识别提示词
_LLM_PROMPT = """请分析这张图片的内容，用中文输出 JSON：
{
  "description": "一句话描述图片内容（20字以内）",
  "tags": ["标签1", "标签2", "标签3"],
  "labels": ["英文标签1", "english_label_2"],
  "scene_type": "indoor/outdoor/abstract/portrait/landscape"
}
只输出 JSON，不要其他内容。"""


class VisionService:
    """图像识别 + 自动标注。"""

    def __init__(self) -> None:
        self._classifier = None
        self._classifier_name = ""

    # ── 公开接口 ──

    async def analyze_image(self, image_path: str) -> dict[str, Any]:
        """分析图片内容，生成标签和描述（E9：path+mtime 缓存，TTL 1h，上限 512）。

        Returns:
            {"tags": [...], "description": str, "labels": [...], "model": str}
        """
        path = Path(image_path)
        if not path.exists():
            return {"tags": [], "description": "", "labels": [], "model": "", "error": "文件不存在"}

        key = _vision_cache_key(image_path)
        cached = _VISION_CACHE.get(key)
        if cached and (time.time() - cached[1]) < _VISION_CACHE_TTL:
            return dict(cached[0])

        from clipwright.config import settings

        # 1. LLM 多模态（Qwen-VL / Claude Vision / GPT-4V）
        if settings.vision_provider == "llm":
            try:
                result = await self._classify_llm(image_path)
                self._cache_store(key, result)
                return result
            except Exception as e:
                logger.debug("LLM 图片识别失败: %s", e)

        # 2. transformers 图像分类
        if settings.vision_provider == "transformers":
            try:
                result = await self._classify_transformers(image_path)
                self._cache_store(key, result)
                return result
            except Exception as e:
                logger.debug("transformers 分类失败: %s", e)

        # 3. 保底：文件名 + 文件信息
        result = self._fallback_analyze(image_path)
        self._cache_store(key, result)
        return result

    @staticmethod
    def _cache_store(key: str, result: dict[str, Any]) -> None:
        """写入缓存并裁剪超限（淘汰最旧条目）。"""
        if len(_VISION_CACHE) >= _VISION_CACHE_MAX and key not in _VISION_CACHE:
            _VISION_CACHE.pop(next(iter(_VISION_CACHE)), None)
        _VISION_CACHE[key] = (result, time.time())

    # ── LLM 多模态 ──

    async def _classify_llm(self, image_path: str) -> dict[str, Any]:
        """使用 LLM 多模态模型（Qwen-VL/Claude/GPT-4V）识别图片。

        优先使用 vision_llm_* 独立配置；未设置时复用主 LLM 参数。
        """
        from clipwright.config import settings
        from clipwright.services.llm import LLMService

        # 使用独立的视觉 LLM 配置（或退回到主 LLM）
        client = LLMService.build_client(
            provider=settings.vision_llm_provider or settings.llm_provider,
            api_key=settings.vision_llm_api_key or settings.llm_api_key,
            model=settings.vision_llm_model or settings.llm_model,
            base_url=settings.vision_llm_base_url or settings.llm_base_url or "",
        )
        model_name = settings.vision_llm_model or settings.llm_model

        # 获取图片 base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        # 检测 MIME 类型
        ext = Path(image_path).suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".gif": "image/gif",
                    ".webp": "image/webp"}
        media_type = mime_map.get(ext, "image/jpeg")

        # 按 LLM provider 构建消息
        vision_prov = settings.vision_llm_provider or settings.llm_provider
        if vision_prov == "anthropic":
            import asyncio
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _LLM_PROMPT},
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": img_b64,
                    }},
                ],
            }]
        else:
            # OpenAI / Ollama / 兼容格式
            data_uri = f"data:{media_type};base64,{img_b64}"
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _LLM_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }]

        import asyncio
        from functools import partial
        resp = await asyncio.to_thread(
            partial(client.generate, messages=messages),
        )
        if not resp.success:
            raise RuntimeError(f"LLM 识别失败: {resp.content}")

        # 解析 JSON 响应
        content = resp.content.strip()
        # 去除可能的 markdown 代码块包装
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(l for l in lines if not l.startswith("```"))

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # 退化为纯文本
            parsed = {"description": content, "tags": [], "labels": []}

        return {
            "tags": parsed.get("tags", []),
            "description": parsed.get("description", content[:100]),
            "labels": parsed.get("labels", []),
            "scene_type": parsed.get("scene_type", ""),
            "model": f"llm/{model_name}",
        }

    # ── Transformers 分类 ──

    async def _classify_transformers(self, image_path: str) -> dict[str, Any]:
        """使用 transformers 图像分类模型。"""
        if self._classifier is None:
            from clipwright.config import settings
            from transformers import pipeline
            kwargs: dict[str, Any] = {
                "task": "image-classification",
                "model": settings.vision_model,
                "top_k": settings.vision_top_k,
            }
            if settings.vision_device:
                kwargs["device"] = settings.vision_device
            self._classifier = pipeline(**kwargs)
            self._classifier_name = settings.vision_model.split("/")[-1]

        result = self._classifier(image_path)
        labels = [r["label"] for r in result]
        scores = [round(r["score"], 4) for r in result]

        tags = self._map_labels_to_tags(labels)
        top_label = labels[0] if labels else ""

        return {
            "tags": tags,
            "description": f"内容: {top_label}",
            "labels": labels[:3],
            "scores": scores[:3],
            "model": self._classifier_name,
        }

    # ── 保底分析 ──

    def _fallback_analyze(self, image_path: str) -> dict[str, Any]:
        """基于文件名 + 文件元信息的保底分析。"""
        path = Path(image_path)
        stem = path.stem.lower()
        tags: list[str] = []
        name_tags = self._extract_name_tags(stem)
        tags.extend(name_tags)
        info = self._get_image_info(image_path)
        w, h = info.get("width", 0), info.get("height", 0)
        if w > h:
            tags.append("横版")
        elif h > w:
            tags.append("竖版")
        else:
            tags.append("方形")
        return {
            "tags": list(set(tags)),
            "description": f"文件: {path.name} ({w}x{h})",
            "labels": [stem],
            "model": "filename",
            "width": w,
            "height": h,
        }

    # ── 辅助 ──

    def _map_labels_to_tags(self, labels: list[str]) -> list[str]:
        """将模型输出的英文标签映射到中文。"""
        tags: list[str] = []
        label_text = " ".join(labels).lower()
        for keyword, chinese_tags in _FALLBACK_TAGS.items():
            if keyword in label_text:
                tags.extend(chinese_tags)
        if not tags:
            tags.append(labels[0] if labels else "未知")
        return tags[:6]

    @staticmethod
    def _extract_name_tags(stem: str) -> list[str]:
        """从文件名提取关键词标签。"""
        import re
        parts = re.split(r'[_\-\s]+', stem)
        return [p for p in parts if len(p) > 2 and not p.isdigit()]

    @staticmethod
    def _get_image_info(path: str) -> dict[str, Any]:
        """用 ffprobe 获取图片信息。"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(result.stdout)
            info: dict[str, Any] = {"width": 0, "height": 0}
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    info["width"] = int(stream.get("width", 0))
                    info["height"] = int(stream.get("height", 0))
                    break
            return info
        except Exception as e:
            logger.debug("ffprobe 失败: %s", e)
            return {"width": 0, "height": 0}
