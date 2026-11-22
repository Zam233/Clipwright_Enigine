"""生成产物素材源工厂 — 历史 AI 生成产物的只读兼容层。

原三个生成插件（ai_image_gen / ai_video_gen / ai_music_gen）已于 2026-11-14
下架（image-provider-plugin PRD M6）；本模块由 `plugins/generated_source.py`
迁入 Engine（宿主持有账本与登记）。当前职责：

- 读取旧 ``PluginData/plugins/<id>/generated.json`` 历史（只读，不写回），
  供 :func:`generated_image_entries` 把仍存在的旧产物喂给 AnimationAgent
  的 `image_assets`（无历史时返回 []，零变化）；
- :func:`make_generated_source` 保留给历史调用方；新生成链路（第二期
  `image_generate` 工具）走工作区 `materials/image/`，不再经过本模块。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clipwright.material.base import MaterialSource
from clipwright.schema.material import MaterialAsset, MaterialType

_TOKEN_SPLIT = re.compile(r"[\s,，。;；、/|()（）\[\]]+")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_CJK = re.compile(r"[\u4e00-\u9fff]")
_MAX_HISTORY = 200

_MTYPE_BY_STR = {
    "video": MaterialType.VIDEO,
    "image": MaterialType.IMAGE,
    "audio": MaterialType.AUDIO,
}


def generated_history_path(plugin_id: str) -> Path:
    """生成历史索引文件路径（PluginData/plugins/<id>/generated.json）。"""
    d = Path("PluginData") / "plugins" / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "generated.json"


def record_generated(plugin_id: str, **entry: Any) -> None:
    """生成成功后追加历史索引（id/created_at 自动补全，损坏重建，失败静默）。

    历史索引写失败不影响生成主流程；文件损坏时重建（视为空历史）。
    """
    entry.setdefault("id", Path(str(entry.get("path", ""))).stem or uuid.uuid4().hex[:10])
    entry["created_at"] = datetime.now(timezone.utc).isoformat()
    p = generated_history_path(plugin_id)
    try:
        items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    items.append(entry)
    try:
        p.write_text(json.dumps(items[-_MAX_HISTORY:], ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    # 新产物登记后使 MaterialAgent 检索缓存失效（TTL 1h 会掩蔽新素材）
    try:
        from clipwright.agents.material_agent import _search_cache
        _search_cache.clear()
    except Exception:
        pass


def _tokenize(text: str) -> set[str]:
    """检索分词：按分隔符切词；CJK 词额外产出字符二元组（中文无空格分词的实用近似）。"""
    toks: set[str] = set()
    for word in _TOKEN_SPLIT.split(str(text or "")):
        if len(word) < 2:
            continue
        word = word.lower()
        toks.add(word)
        if _CJK.search(word):
            toks.update(word[i:i + 2] for i in range(len(word) - 1))
    return toks


def load_generated_history(plugin_id: str) -> list[dict[str, Any]]:
    """读取生成历史（供测试与 AnimationAgent 图片索引复用）。"""
    try:
        items = json.loads(generated_history_path(plugin_id).read_text(encoding="utf-8"))
        return [e for e in items if isinstance(e, dict)] if isinstance(items, list) else []
    except Exception:
        return []


def workspace_image_entries(workspace_root: str | Path | None) -> list[dict[str, Any]]:
    """工作区 ``materials/image/`` 里的图片 → 子 Agent image_assets 结构。

    新 ``image_generate`` 工具写工作区，不再写旧 ``PluginData/.../generated.json``；
    只读目录里的真实文件，返回 ``{path, tags, description, asset_id}``。没有工作区
    或目录不存在时返回 []（零变化）。
    """
    if not workspace_root:
        return []
    root = Path(str(workspace_root)).expanduser()
    image_dir = root / "materials" / "image"
    if not image_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(image_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in _IMAGE_EXTS:
            continue
        stem = p.stem.replace("_", " ").strip()
        out.append({
            "path": str(p),
            "asset_id": p.stem,
            "tags": sorted(_tokenize(stem))[:8],
            "description": stem[:120],
        })
    return out


def generated_image_entries(workspace_root: str | Path | None = None) -> list[dict[str, Any]]:
    """AI 生成图片 → 子 Agent image_assets 结构（[{path, tags, description}]）。

    同时覆盖旧 ``PluginData/plugins/ai_image_gen/generated.json`` 历史与工作区
    ``materials/image/`` 新产物；按绝对路径去重，只返回仍存在的本地文件。
    无历史/无工作区时返回 []（零变化）。
    """
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in load_generated_history("ai_image_gen"):
        path = str(e.get("path", ""))
        prompt = str(e.get("prompt", ""))
        if not path or not Path(path).exists():
            continue
        key = str(Path(path).resolve())
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "path": path,
            "tags": sorted(_tokenize(prompt))[:8],
            "description": prompt[:120],
        })
    for entry in workspace_image_entries(workspace_root):
        key = str(Path(str(entry["path"])).resolve())
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries


def make_generated_source(plugin_id: str, source_name: str,
                          material_type: MaterialType | str) -> MaterialSource:
    """构造一个面向生成历史的 MaterialSource 实例。"""

    mtype = _MTYPE_BY_STR.get(str(getattr(material_type, "value", material_type)), MaterialType.VIDEO)
    history_path = generated_history_path(plugin_id)
    src_id, src_name = plugin_id, source_name

    class _GeneratedAssetSource(MaterialSource):
        """按 query 与生成 prompt 的分词重合度检索历史产物。"""

        source_id = src_id
        source_name = src_name

        def _to_asset(self, entry: dict[str, Any]) -> MaterialAsset:
            prompt = str(entry.get("prompt", ""))
            return MaterialAsset(
                id=str(entry.get("id") or uuid.uuid4().hex[:10]),
                title=prompt[:60],
                type=mtype,
                local_path=str(entry.get("path", "")),
                tags=sorted(_tokenize(prompt))[:8],
                duration_sec=entry.get("duration_sec"),
                resolution=entry.get("resolution"),
                source=self.source_id,
                license="AI Generated",
                metadata={"ai_generated": True, "prompt": prompt},
            )

        async def search(self, query: str, top_k: int = 10,
                         **kwargs: Any) -> list[tuple[MaterialAsset, float]]:
            q_toks = _tokenize(query)
            if not q_toks:
                return []
            scored: list[tuple[MaterialAsset, float]] = []
            for entry in load_generated_history(plugin_id):
                path = str(entry.get("path", ""))
                if not path or not Path(path).exists():
                    continue  # 产物已被清理 → 跳过，避免渲染期缺文件
                overlap = len(q_toks & _tokenize(str(entry.get("prompt", "")))) / len(q_toks)
                if overlap <= 0:
                    continue
                scored.append((self._to_asset(entry), min(0.92, 0.70 + 0.22 * overlap)))
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return scored[:max(1, int(top_k))]

        async def count(self) -> int:
            return len(load_generated_history(plugin_id))

        async def list_all(self) -> list[MaterialAsset]:
            return [self._to_asset(e) for e in load_generated_history(plugin_id)]

    return _GeneratedAssetSource()


def legacy_image_history_present() -> bool:
    """是否存在旧版 ai_image_gen 生成历史（只读兼容，未迁移）。"""
    try:
        return bool(load_generated_history("ai_image_gen"))
    except Exception:  # noqa: BLE001
        return False
