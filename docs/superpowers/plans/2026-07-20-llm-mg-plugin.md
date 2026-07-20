# LLM Motion Graphics Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `llm_mg` plugin that dynamically generates MG animations via LLM, plus fix the broken `mg_animations` pseudo-plugin.

**Architecture:** New CapabilityPlugin integrates with AnimationAgent via `mg_dynamic` marker routing. LLM generates complete MG JSON (elements + keyframes + params), validated and rendered through existing MGRenderer → Hyperframes pipeline. Fallback chain: LLM → template match → drawtext.

**Tech Stack:** Python 3.12+, Pydantic, IsoBase LLM client, existing PluginLoader/HookRegistry/MGRenderer/HyperframesRenderer

**Spec:** `docs/superpowers/specs/2026-07-20-llm-mg-plugin-design.md`

## Global Constraints

- Follow existing code patterns: CapabilityPlugin, AnimationRegistry, AgentBus
- MG JSON schema MUST match MGRenderer.render() expected format
- All new files under `plugins/llm_mg/`
- Backward compatible: old `mg_` prefixed templates still work via updated MGRenderer path
- LLM calls use `clipwright.services.llm.LLMService`

---

### Task 1: Schema — AnimationIntent + Output Extensions

**Files:**
- Modify: `clipwright/schema/agent.py:95-316`

**Interfaces:**
- Produces: `AnimationIntent(BaseModel)` — scene_index, type, description, text_content, style_hint, suggested_template
- Produces: `RequirementsOutput.animation_intents: list[AnimationIntent]`
- Produces: `AnimationOutput.generated_mg_count: int`

- [ ] **Step 1: Add AnimationIntent model**

At the end of `clipwright/schema/agent.py` (before last line), add:

```python
class AnimationIntent(BaseModel):
    """动画需求意图 — RequirementsAgent → StructureAgent → AnimationAgent。"""
    scene_index: Optional[int] = Field(default=None, description="目标场景索引，未确定时 null")
    type: str = Field(default="mg", description="动画类型: mg / text / logic")
    description: str = Field(default="", description="自然语言动画需求描述")
    text_content: str = Field(default="", description="动画中要显示的文字，用 | 分隔多个内容")
    style_hint: str = Field(default="", description="风格提示: tech_dark / minimal_clean / bold_vibrant / retro")
    suggested_template: str = Field(default="", description="建议的已有模板 ID，不确定则留空")
```

- [ ] **Step 2: Extend RequirementsOutput**

In `RequirementsOutput` (line ~157), add `animation_intents` field after `error`:

```python
animation_intents: list[AnimationIntent] = Field(
    default_factory=list,
    description="RequirementsAgent 识别的动画需求意图",
)
```

- [ ] **Step 3: Extend AnimationOutput**

In `AnimationOutput` (line ~102), add `generated_mg_count` field after `animation_plan`:

```python
generated_mg_count: int = Field(default=0, description="LLM 本次生成的 MG 动画数量")
```

- [ ] **Step 4: Verify import works**

```bash
python -c "from clipwright.schema.agent import AnimationIntent, RequirementsOutput, AnimationOutput; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add clipwright/schema/agent.py
git commit -m "feat(schema): add AnimationIntent model and agent output extensions"
```

---

### Task 2: Plugin Scaffolding

**Files:**
- Create: `plugins/llm_mg/plugin.yaml`
- Create: `plugins/llm_mg/__init__.py`
- Create: `plugins/llm_mg/config.yaml`

**Interfaces:**
- Produces: `LLMMGPlugin` discoverable by PluginLoader
- Produces: config with LLM prompt templates

- [ ] **Step 1: Create plugin directory**

```bash
mkdir -p plugins/llm_mg/templates
```

- [ ] **Step 2: Write plugin.yaml**

```yaml
id: "llm_mg"
name: "LLM Motion Graphics Generator"
version: "1.0.0"
kind: "capability"
description: "LLM 驱动的动态 MG 动画生成 — 从自然语言需求生成 HTML/CSS 动画并渲染为视频覆盖层"
author: "Clipwright Team"
entry_point: "llm_mg.main"
```

- [ ] **Step 3: Write __init__.py**

```python
"""LLM Motion Graphics Generator Plugin."""
```

- [ ] **Step 4: Write config.yaml**

```yaml
# LLM MG Generator 配置

llm:
  model: ""                       # 空 = 使用全局 LLM 配置
  temperature: 0.3                # 低温度保证 JSON 结构稳定
  max_tokens: 4096
  timeout: 60

generation:
  max_retries: 2                  # LLM 生成失败时的重试次数
  default_duration_sec: 3.0
  default_width: 1920
  default_height: 1080

prompt:
  system_template: |
    你是一个 MG 动画生成器。根据用户需求生成符合规范的 MG 动画 JSON。

    ## 输出格式
    严格输出合法 JSON，不要包含解释性文字：

    {
      "animation_id": "mg_generated_xxx",
      "name": "动画名称",
      "description": "描述",
      "duration_sec": 3.0,
      "width": 1920,
      "height": 1080,
      "elements": [
        {
          "type": "text|shape",
          "content": "文字内容（text 类型）",
          "x": "center|left|right|数字px",
          "y": "center|top|bottom|数字px",
          "y_offset": 0,
          "font_size": 48,
          "font_color": "#ffffff",
          "font_weight": "normal|bold",
          "shape": "rect|ellipse",
          "color": "#4f8cff",
          "width": 200,
          "height": 3,
          "keyframes": [
            {"time": 0, "opacity": 0, "scale": 0.3},
            {"time": 0.6, "opacity": 1, "scale": 1.0}
          ]
        }
      ],
      "params": {
        "text": {"type": "string", "default": ""}
      },
      "style": {
        "background": "transparent",
        "font_family": "sans-serif"
      }
    }

    ## 可用动画属性
    - opacity (0~1): 透明度
    - scale: 缩放比例
    - translate_x / translate_y: 位移 (px)
    - rotate: 旋转角度 (deg)
    - width: 宽度 (px, shape 元素)

    ## 位置定义
    - x: "center" / "left" / "right" / 数字像素值
    - y: "center" / "top" / "bottom" / 数字像素值
    - y_offset: 额外偏移 (px)，微调位置

    ## 约束
    - 动画至少 2 个 keyframes，time 从 0 开始
    - 最后一个 keyframe 的 time 不超过 duration_sec
    - 所有坐标以 1920x1080 为基准
    - typescript animation_id 命名: mg_generated_<简短英文>
    - 使用 {text} {value} {unit} {subtitle} {accent} 作为 params 占位符

    ## 风格提示对应配色
    - tech_dark: 主色 #4f8cff, 辅色 #7c3aed, 背景深色
    - minimal_clean: 主色 #333333, 辅色 #666666, 大量留白
    - bold_vibrant: 主色 #ff6b6b, 辅色 #fbbf24, 高饱和
    - retro: 主色 #e07a5f, 辅色 #f4a261, 暖色调

templates_search:
  similarity_threshold: 0.6       # 语义匹配最低相似度
  max_candidates: 3               # 最多候选项
```

- [ ] **Step 5: Verify PluginLoader discovers the plugin**

```bash
python -c "from clipwright.plugins import PluginLoader; pl = PluginLoader(); print(pl.discover())"
```
Expected: `llm_mg` appears in the list.

- [ ] **Step 6: Commit**

```bash
git add plugins/llm_mg/plugin.yaml plugins/llm_mg/__init__.py plugins/llm_mg/config.yaml
git commit -m "feat(llm_mg): plugin scaffolding with config"
```

---

### Task 3: Storage Layer

**Files:**
- Create: `plugins/llm_mg/storage.py`

**Interfaces:**
- Produces: `save_generation(generation_id, mg_def) -> Path` — persist generated JSON
- Produces: `load_generation(generation_id) -> dict | None` — load persisted JSON
- Produces: `save_as_template(generation_id, custom_name) -> str` — promote to template
- Produces: `get_templates() -> list[dict]` — list available templates

- [ ] **Step 1: Write storage.py**

```python
"""MG 动画存储层 — 持久化生成结果和管理模板。"""

from __future__ import annotations

import json
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


class MGStorage:
    """管理 LLM 生成的 MG 动画的持久化和模板化。"""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent
        self._base = base_dir
        self._generations_dir = base_dir / "generations"
        self._templates_dir = base_dir / "templates"
        self._generations_dir.mkdir(parents=True, exist_ok=True)
        self._templates_dir.mkdir(parents=True, exist_ok=True)

    def save_generation(self, mg_def: dict, generation_id: str = "") -> dict:
        """保存一次生成结果。返回 {generation_id, path}。"""
        if not generation_id:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            short_uid = uuid.uuid4().hex[:8]
            generation_id = f"gen_{ts}_{short_uid}"

        record = {
            "generation_id": generation_id,
            "mg_def": mg_def,
            "created_at": datetime.now().isoformat(),
        }
        path = self._generations_dir / f"{generation_id}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"generation_id": generation_id, "path": str(path)}

    def load_generation(self, generation_id: str) -> dict | None:
        """加载一次生成结果。"""
        path = self._generations_dir / f"{generation_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_as_template(self, generation_id: str, custom_name: str = "") -> str:
        """将生成结果保存为可复用模板。返回模板文件路径。"""
        record = self.load_generation(generation_id)
        if record is None:
            raise FileNotFoundError(f"Generation {generation_id} not found")

        mg_def = record["mg_def"]
        anim_id = mg_def.get("animation_id", "")

        # 避免 ID 冲突
        existing = self.get_template_ids()
        if anim_id in existing or not anim_id:
            short_uid = uuid.uuid4().hex[:6]
            base_name = anim_id or "mg_custom"
            anim_id = f"{base_name}_{short_uid}"

        mg_def["animation_id"] = anim_id
        if custom_name:
            mg_def["name"] = custom_name

        path = self._templates_dir / f"{anim_id}.json"
        path.write_text(json.dumps(mg_def, ensure_ascii=False, indent=2), encoding="utf-8")

        # 清理生成记录
        gen_path = self._generations_dir / f"{generation_id}.json"
        if gen_path.exists():
            gen_path.unlink()

        return str(path)

    def get_template_ids(self) -> list[str]:
        """获取所有模板 ID。"""
        ids = []
        for f in self._templates_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                aid = data.get("animation_id", "")
                if aid:
                    ids.append(aid)
            except Exception:
                pass
        return ids

    def get_templates(self) -> list[dict]:
        """获取所有模板元信息。"""
        templates = []
        for f in sorted(self._templates_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                templates.append({
                    "animation_id": data.get("animation_id", ""),
                    "name": data.get("name", ""),
                    "description": data.get("description", ""),
                    "duration_sec": data.get("duration_sec", 3.0),
                    "params": list(data.get("params", {}).keys()),
                })
            except Exception:
                pass
        return templates

    def load_template(self, anim_id: str) -> dict | None:
        """按 ID 加载模板。"""
        path = self._templates_dir / f"{anim_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_generations(self) -> list[dict]:
        """列出所有未保存的生成记录。"""
        gens = []
        for f in sorted(self._generations_dir.glob("*.json"), reverse=True):
            try:
                record = json.loads(f.read_text(encoding="utf-8"))
                mg_def = record.get("mg_def", {})
                gens.append({
                    "generation_id": record.get("generation_id", ""),
                    "name": mg_def.get("name", ""),
                    "created_at": record.get("created_at", ""),
                })
            except Exception:
                pass
        return gens
```

- [ ] **Step 2: Verify storage works**

```bash
python -c "
from plugins.llm_mg.storage import MGStorage
s = MGStorage()
r = s.save_generation({'animation_id': 'test', 'elements': []})
print('Saved:', r)
t = s.load_generation(r['generation_id'])
print('Loaded:', t['mg_def']['animation_id'])
"
```
Expected: prints Saved and Loaded with test animation_id.

- [ ] **Step 3: Commit**

```bash
git add plugins/llm_mg/storage.py
git commit -m "feat(llm_mg): storage layer for generations and templates"
```

---

### Task 4: Validator

**Files:**
- Create: `plugins/llm_mg/validator.py`

**Interfaces:**
- Produces: `validate_mg_json(mg_def: dict) -> tuple[bool, list[str]]` — (valid, errors)
- Produces: `repair_mg_json(mg_def: dict) -> tuple[dict, list[str]]` — repaired JSON + fix notes

- [ ] **Step 1: Write validator.py**

```python
"""MG JSON Schema 验证器 — 校验和修复 LLM 生成的 MG JSON。"""

from __future__ import annotations

from typing import Any


# 有效的 MG JSON schema 约束
REQUIRED_TOP_KEYS = {"animation_id", "elements"}
VALID_ELEMENT_TYPES = {"text", "shape"}
VALID_SHAPES = {"rect", "ellipse"}
VALID_POSITIONS = {"center", "left", "right", "top", "bottom"}
ANIMATABLE_PROPS = {"opacity", "scale", "translate_x", "translate_y", "rotate", "width"}


def validate_mg_json(mg_def: dict[str, Any]) -> tuple[bool, list[str]]:
    """验证 MG JSON 定义是否符合规范。

    Returns:
        (is_valid, error_messages)
    """
    errors: list[str] = []

    if not isinstance(mg_def, dict):
        return False, ["mg_def is not a dict"]

    # 顶层必需字段
    for key in REQUIRED_TOP_KEYS:
        if key not in mg_def:
            errors.append(f"Missing required top-level key: {key}")

    # animation_id
    anim_id = mg_def.get("animation_id", "")
    if not anim_id or not isinstance(anim_id, str):
        errors.append("animation_id must be a non-empty string")

    # duration_sec
    dur = mg_def.get("duration_sec", 0)
    if not isinstance(dur, (int, float)) or dur <= 0:
        errors.append("duration_sec must be a positive number")

    # width / height
    for dim in ("width", "height"):
        v = mg_def.get(dim, 0)
        if not isinstance(v, (int, float)) or v <= 0:
            errors.append(f"{dim} must be a positive number")

    # elements
    elements = mg_def.get("elements", [])
    if not isinstance(elements, list) or len(elements) == 0:
        errors.append("elements must be a non-empty list")
    else:
        for i, elem in enumerate(elements):
            elem_errors = _validate_element(elem, i, mg_def.get("duration_sec", 3.0))
            errors.extend(elem_errors)

    return len(errors) == 0, errors


def _validate_element(elem: dict, index: int, total_dur: float) -> list[str]:
    """验证单个元素。"""
    errors: list[str] = []
    prefix = f"elements[{index}]"

    elem_type = elem.get("type", "")
    if elem_type not in VALID_ELEMENT_TYPES:
        errors.append(f"{prefix}: type must be one of {VALID_ELEMENT_TYPES}, got '{elem_type}'")

    # text 元素必须有 content
    if elem_type == "text" and "content" not in elem:
        errors.append(f"{prefix}: text element missing 'content'")

    # shape 元素必须有 shape
    if elem_type == "shape":
        if "shape" not in elem:
            errors.append(f"{prefix}: shape element missing 'shape' field")
        elif elem.get("shape") not in VALID_SHAPES:
            errors.append(f"{prefix}: shape must be one of {VALID_SHAPES}")

    # keyframes
    kfs = elem.get("keyframes", [])
    if not isinstance(kfs, list) or len(kfs) < 2:
        errors.append(f"{prefix}: must have at least 2 keyframes")
    else:
        for j, kf in enumerate(kfs):
            kf_errors = _validate_keyframe(kf, j, total_dur, prefix)
            errors.extend(kf_errors)

    # 位置检查
    for pos in ("x", "y"):
        val = elem.get(pos)
        if val is None:
            continue
        if isinstance(val, str) and val not in VALID_POSITIONS:
            try:
                float(val)
            except (ValueError, TypeError):
                errors.append(f"{prefix}: {pos} must be center/left/right/top/bottom or a number")

    return errors


def _validate_keyframe(kf: dict, index: int, total_dur: float, parent_prefix: str) -> list[str]:
    """验证单个关键帧。"""
    errors: list[str] = []
    pfx = f"{parent_prefix}.keyframes[{index}]"

    t = kf.get("time", -1)
    if not isinstance(t, (int, float)) or t < 0 or t > total_dur:
        errors.append(f"{pfx}: time must be between 0 and {total_dur}")

    props = kf.get("properties", kf)  # 兼容 properties 字段或顶层属性
    if not isinstance(props, dict):
        errors.append(f"{pfx}: must have properties dict")
        return errors

    # 去掉 time 字段后检查是否有动画属性
    anim_props = {k: v for k, v in props.items() if k != "time"}
    if not anim_props:
        errors.append(f"{pfx}: no animatable properties found")
    else:
        for prop_name in anim_props:
            if prop_name not in ANIMATABLE_PROPS:
                errors.append(f"{pfx}: unknown property '{prop_name}'")

    return errors


def repair_mg_json(mg_def: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """尝试修复常见的 MG JSON 错误。

    Returns:
        (repaired_dict, fix_messages)
    """
    fixes: list[str] = []
    repaired = dict(mg_def)

    # 修复缺失的顶层字段
    if "animation_id" not in repaired or not repaired["animation_id"]:
        import uuid
        repaired["animation_id"] = f"mg_generated_{uuid.uuid4().hex[:8]}"
        fixes.append("Added missing animation_id")

    if "duration_sec" not in repaired or not isinstance(repaired.get("duration_sec"), (int, float)):
        repaired["duration_sec"] = 3.0
        fixes.append("Set default duration_sec=3.0")

    for dim, default in (("width", 1920), ("height", 1080)):
        if dim not in repaired or not isinstance(repaired.get(dim), (int, float)):
            repaired[dim] = default
            fixes.append(f"Set default {dim}={default}")

    if "elements" not in repaired or not repaired["elements"]:
        fixes.append("No elements — cannot repair")
        return repaired, fixes

    # 修复 style
    if "style" not in repaired:
        repaired["style"] = {"background": "transparent", "font_family": "sans-serif"}

    # 修复 params
    if "params" not in repaired:
        params = {}
        for elem in repaired.get("elements", []):
            content = elem.get("content", "")
            if "{text}" in content:
                params.setdefault("text", {"type": "string", "default": ""})
            if "{value}" in content:
                params.setdefault("value", {"type": "string", "default": ""})
            if "{accent}" in content:
                params.setdefault("accent", {"type": "string", "default": "#4f8cff"})
        if params:
            repaired["params"] = params

    # 修复元素 keyframes
    for i, elem in enumerate(repaired.get("elements", [])):
        kfs = elem.get("keyframes", [])
        if not kfs:
            elem["keyframes"] = [
                {"time": 0, "opacity": 0},
                {"time": 0.5, "opacity": 1},
            ]
            fixes.append(f"elements[{i}]: added default keyframes")
            continue

        # 确保有 time=0 的关键帧
        first_time = kfs[0].get("time", -1)
        if first_time > 0:
            kfs.insert(0, {"time": 0, "opacity": 0})
            fixes.append(f"elements[{i}]: added time=0 keyframe")

        # 规范 keyframe 结构 (嵌套 properties → 扁平)
        for kf in kfs:
            if "properties" in kf and isinstance(kf["properties"], dict):
                for pk, pv in kf["properties"].items():
                    if pk not in kf:
                        kf[pk] = pv

    return repaired, fixes
```

- [ ] **Step 2: Verify validator**

```bash
python -c "
from plugins.llm_mg.validator import validate_mg_json, repair_mg_json

# Valid
ok, errs = validate_mg_json({
    'animation_id': 'mg_test',
    'duration_sec': 3.0,
    'width': 1920, 'height': 1080,
    'elements': [{'type': 'text', 'content': 'Hello', 'keyframes': [{'time': 0, 'opacity': 0}, {'time': 1, 'opacity': 1}]}]
})
print('Valid:', ok, errs)

# Invalid → repair
bad = {'animation_id': '', 'elements': [{'type': 'text', 'content': 'X', 'keyframes': []}]}
repaired, fixes = repair_mg_json(bad)
print('Repaired:', repaired['animation_id'], fixes)
"
```
Expected: Valid=True, Repair adds animation_id and default keyframes.

- [ ] **Step 3: Commit**

```bash
git add plugins/llm_mg/validator.py
git commit -m "feat(llm_mg): MG JSON schema validator and auto-repair"
```

---

### Task 5: Fallback Strategy

**Files:**
- Create: `plugins/llm_mg/fallback.py`

**Interfaces:**
- Produces: `FallbackEngine.find_best_template(description, templates) -> dict | None`
- Produces: `FallbackEngine.extract_keywords(text) -> list[str]`
- Produces: `FallbackEngine.fallback_generate(description, text_content, templates) -> dict`

- [ ] **Step 1: Write fallback.py**

```python
"""MG 动画降级策略 — 当 LLM 生成失败时匹配已有模板。"""

from __future__ import annotations

import re
from typing import Any


class FallbackEngine:
    """降级引擎：语义描述 → 已有模板匹配 → 参数填充。"""

    # 关键词 → 模板映射
    KEYWORD_TEMPLATE_MAP: dict[str, str] = {
        "对比|vs|比较|pk|差异": "mg_comparison_split",
        "标题|title|reveal|揭示|开头": "mg_title_reveal",
        "进度|progress|完成|percent|百分比": "mg_progress_bar",
        "数字|count|计数|增长|统计|counter": "mg_counter_up",
        "标签|badge|徽章|标注|callout|提示": "mg_callout_badge",
    }

    @classmethod
    def find_best_template(
        cls, description: str, templates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """根据描述匹配最合适的模板。

        Args:
            description: 用户自然语言描述
            templates: 可用模板列表 [{animation_id, name, ...}]

        Returns:
            匹配的模板，或 None
        """
        if not templates:
            return None

        desc_lower = description.lower()
        scores: dict[str, float] = {}

        for t in templates:
            tid = t.get("animation_id", "")
            tname = t.get("name", "")
            tdesc = t.get("description", "")
            score = 0.0

            # 关键词精确匹配
            for pattern, template_id in cls.KEYWORD_TEMPLATE_MAP.items():
                if tid == template_id:
                    for kw in pattern.split("|"):
                        if kw.lower() in desc_lower:
                            score += 2.0
                        if kw.lower() in tname.lower():
                            score += 1.0
                        if kw.lower() in tdesc.lower():
                            score += 0.5

            scores[tid] = score

        # 返回最高分模板
        if scores:
            best_id = max(scores, key=scores.get)
            if scores[best_id] > 0:
                for t in templates:
                    if t.get("animation_id") == best_id:
                        return t

        # 无匹配 → 返回 comparison_split（最通用）
        for t in templates:
            if t.get("animation_id") == "mg_comparison_split":
                return t

        return templates[0] if templates else None

    @classmethod
    def extract_keywords(cls, text: str) -> list[str]:
        """从文本中提取关键信息。"""
        # 提取 | 分隔的内容段
        parts = [p.strip() for p in text.replace("→", "|").split("|") if p.strip()]
        return parts

    @classmethod
    def fill_template_params(
        cls, template: dict[str, Any], text_content: str, persona_style: dict | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """用提取的关键词填充模板参数。

        Returns:
            (filled_template, params_dict)
        """
        parts = cls.extract_keywords(text_content)
        params: dict[str, str] = {}

        param_defs = template.get("params", {})
        param_keys = list(param_defs.keys())

        # 按顺序分配
        for i, key in enumerate(param_keys):
            if i < len(parts):
                params[key] = parts[i]
            else:
                params[key] = param_defs[key].get("default", "") if isinstance(param_defs[key], dict) else ""

        # 如果只有一个值，同时填到 text
        if len(params) == 0 and parts:
            params["text"] = parts[0]

        # 应用 Persona 风格
        style = persona_style or {}
        if "primary_color" in style:
            params.setdefault("accent", style["primary_color"])

        return template, params
```

- [ ] **Step 2: Verify fallback matching**

```bash
python -c "
from plugins.llm_mg.fallback import FallbackEngine
templates = [
    {'animation_id': 'mg_comparison_split', 'name': '左右对比', 'description': 'A vs B 左右分屏'},
    {'animation_id': 'mg_title_reveal', 'name': '标题揭示', 'description': '大标题动画'},
]
best = FallbackEngine.find_best_template('产品A和B的性能对比分析', templates)
print('Matched:', best['animation_id'])
"
```
Expected: Matched: mg_comparison_split

- [ ] **Step 3: Commit**

```bash
git add plugins/llm_mg/fallback.py
git commit -m "feat(llm_mg): fallback strategy with template matching"
```

---

### Task 6: Generator (LLM Integration)

**Files:**
- Create: `plugins/llm_mg/generator.py`

**Interfaces:**
- Consumes: `LLMService` from `clipwright.services.llm`
- Consumes: `config.yaml` for prompt templates
- Consumes: `MGStorage` for template loading
- Consumes: `FallbackEngine` for fallback
- Consumes: `validator` for validation/repair
- Produces: `MGGenerator.generate(description, text_content, persona_style, scene_context) -> dict`

- [ ] **Step 1: Write generator.py**

```python
"""MG 动画 LLM 生成器 — LLM 生成 → 验证 → 修复 → 降级。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import yaml

from clipwright.services.llm import LLMService
from clipwright.config import logger

from .validator import validate_mg_json, repair_mg_json
from .fallback import FallbackEngine
from .storage import MGStorage


class MGGenerator:
    """LLM 驱动的 MG 动画生成器。"""

    def __init__(self) -> None:
        self._llm = LLMService()
        self._storage = MGStorage()
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        config_path = Path(__file__).resolve().parent / "config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    async def generate(
        self,
        description: str,
        text_content: str,
        persona_style: dict[str, Any] | None = None,
        scene_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成 MG 动画 JSON。

        LLM 生成 → 验证 → 失败则修复 → 仍失败则降级到已有模板。

        Returns:
            {
                "success": bool,
                "html": str,
                "mg_def": dict,
                "method": str,           # "llm" | "fallback"
                "fallback_template": str | None,
                "generation_id": str,
            }
        """
        persona_style = persona_style or {}
        scene_context = scene_context or {}

        gen_config = self._config.get("generation", {})
        max_retries = gen_config.get("max_retries", 2)

        # ── ① LLM 生成 ──
        mg_def = None
        for attempt in range(max_retries + 1):
            try:
                mg_def = await self._call_llm(description, text_content, persona_style, scene_context)
                if mg_def:
                    break
            except Exception as e:
                logger.warning("MGGenerator LLM attempt %d failed: %s", attempt + 1, e)

        # ── ② 验证 + 修复 ──
        if mg_def:
            ok, errors = validate_mg_json(mg_def)
            if not ok:
                logger.warning("MGGenerator validation errors: %s", errors)
                mg_def, fixes = repair_mg_json(mg_def)
                logger.info("MGGenerator repair fixes: %s", fixes)

            # 再次验证
            ok2, errors2 = validate_mg_json(mg_def)
            if ok2:
                return self._build_success(mg_def, "llm")

        # ── ③ 降级到已有模板 ──
        return await self._fallback_generate(description, text_content, persona_style)

    async def _call_llm(
        self,
        description: str,
        text_content: str,
        persona_style: dict,
        scene_context: dict,
    ) -> dict[str, Any] | None:
        """调用 LLM 生成 MG JSON。"""
        prompt_config = self._config.get("prompt", {})
        system_template = prompt_config.get("system_template", "")

        # 构建上下文
        user_parts = [f"## 动画需求\n{description}"]
        if text_content:
            user_parts.append(f"## 文字内容\n{text_content}")
        if persona_style:
            style_desc = persona_style.get("style_description", "")
            primary = persona_style.get("primary_color", "")
            if style_desc or primary:
                user_parts.append(f"## 风格要求\n{style_desc}\n主色: {primary}")
        if scene_context:
            title = scene_context.get("title", "")
            keywords = scene_context.get("keywords", [])
            if title or keywords:
                user_parts.append(f"## 场景上下文\n标题: {title}\n关键词: {keywords}")

        user_prompt = "\n\n".join(user_parts)

        llm_config = self._config.get("llm", {})
        temperature = llm_config.get("temperature", 0.3)

        try:
            response = await self._llm.generate(
                messages=[
                    {"role": "system", "content": system_template},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                timeout=llm_config.get("timeout", 60),
            )

            content = response.content if hasattr(response, "content") else str(response)
            return self._parse_llm_response(content)
        except Exception as e:
            logger.warning("MGGenerator LLM call failed: %s", e)
            return None

    def _parse_llm_response(self, content: str) -> dict[str, Any] | None:
        """从 LLM 响应中提取 JSON。"""
        if not content:
            return None

        # 尝试直接解析
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown code block 提取
        import re
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试从大括号中提取
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("MGGenerator: could not parse JSON from LLM response: %.200s", content)
        return None

    async def _fallback_generate(
        self,
        description: str,
        text_content: str,
        persona_style: dict,
    ) -> dict[str, Any]:
        """降级生成 — 匹配已有模板。"""
        templates = self._storage.get_templates()
        best = FallbackEngine.find_best_template(description, templates)

        if best:
            template, params = FallbackEngine.fill_template_params(best, text_content, persona_style)
            return self._build_success(template, "fallback", fallback_template=best.get("animation_id"))

        # 最终降级：无可用模板
        logger.warning("MGGenerator: no fallback template available")
        return {
            "success": False,
            "html": "",
            "mg_def": {},
            "method": "fallback",
            "fallback_template": None,
            "generation_id": "",
        }

    def _build_success(
        self, mg_def: dict, method: str, fallback_template: str | None = None,
    ) -> dict[str, Any]:
        """构建成功响应。"""
        result = self._storage.save_generation(mg_def)
        generation_id = result["generation_id"]

        # 渲染 HTML
        from clipwright.animation.mg_renderer import MGRenderer
        try:
            html = MGRenderer.render(mg_def)
        except Exception as e:
            logger.warning("MGGenerator: MGRenderer.render() failed: %s", e)
            html = ""

        return {
            "success": bool(html),
            "html": html,
            "mg_def": mg_def,
            "method": method,
            "fallback_template": fallback_template,
            "generation_id": generation_id,
        }
```

- [ ] **Step 2: Verify import**

```bash
python -c "from plugins.llm_mg.generator import MGGenerator; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add plugins/llm_mg/generator.py
git commit -m "feat(llm_mg): LLM-driven MG JSON generator with validation and fallback"
```

---

### Task 7: Plugin Main Class

**Files:**
- Create: `plugins/llm_mg/main.py`

**Interfaces:**
- Consumes: `MGGenerator`, `MGStorage`
- Produces: `LLMMGPlugin(CapabilityPlugin)` with `generate_mg()` and `save_as_template()`

- [ ] **Step 1: Write main.py**

```python
"""LLM Motion Graphics Generator Plugin — 主入口。"""

from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger

from .generator import MGGenerator
from .storage import MGStorage


class LLMMGPlugin(CapabilityPlugin):
    """LLM 驱动的 MG 动画生成插件。

    从自然语言需求动态生成完整的 MG 动画 JSON，
    通过现有 MGRenderer → Hyperframes 管线渲染为视频覆盖层。
    """

    manifest = PluginManifest(
        id="llm_mg",
        name="LLM Motion Graphics Generator",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="LLM 驱动的动态 MG 动画生成 — 从自然语言需求生成 HTML/CSS 动画",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        super().__init__()
        self._generator: MGGenerator | None = None
        self._storage: MGStorage | None = None

    def initialize(self) -> None:
        """初始化插件：加载生成器和存储。"""
        self._generator = MGGenerator()
        self._storage = MGStorage()
        logger.info("LLMMGPlugin initialized, templates=%d", len(self._storage.get_templates()))

    def shutdown(self) -> None:
        """插件卸载。"""
        self._generator = None
        self._storage = None

    async def generate_mg(
        self,
        description: str,
        text_content: str,
        persona_style: dict[str, Any] | None = None,
        scene_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成 MG 动画。

        Args:
            description: 自然语言动画需求描述
            text_content: 动画中的文本内容（| 分隔多段）
            persona_style: Persona visual_config 风格参数
            scene_context: 当前场景上下文 {title, keywords, prev_scene, next_scene}

        Returns:
            {
                "success": bool,
                "html": str,
                "mg_def": dict,
                "method": str,
                "fallback_template": str | None,
                "generation_id": str,
            }
        """
        if self._generator is None:
            self._generator = MGGenerator()
        return await self._generator.generate(
            description=description,
            text_content=text_content,
            persona_style=persona_style or {},
            scene_context=scene_context or {},
        )

    def save_as_template(self, generation_id: str, custom_name: str = "") -> str:
        """将生成的 MG 动画保存为可复用模板。

        Args:
            generation_id: generate_mg() 返回的 generation_id
            custom_name: 自定义名称

        Returns:
            模板文件路径
        """
        if self._storage is None:
            self._storage = MGStorage()
        return self._storage.save_as_template(generation_id, custom_name)

    def list_templates(self) -> list[dict]:
        """列出所有可用模板。"""
        if self._storage is None:
            self._storage = MGStorage()
        return self._storage.get_templates()

    def list_generations(self) -> list[dict]:
        """列出未保存的生成记录。"""
        if self._storage is None:
            self._storage = MGStorage()
        return self._storage.list_generations()
```

- [ ] **Step 2: Verify plugin class is discoverable**

```bash
python -c "
from clipwright.plugins import PluginLoader
pl = PluginLoader()
pids = pl.discover()
print('Discovered:', pids)
if 'llm_mg' in pids:
    plugin = pl.load('llm_mg')
    print('Type:', type(plugin).__name__)
    print('Manifest:', plugin.manifest.id)
"
```
Expected: `llm_mg` in discovered list, type=LLMMGPlugin.

- [ ] **Step 3: Commit**

```bash
git add plugins/llm_mg/main.py
git commit -m "feat(llm_mg): plugin main class with generate_mg and save_as_template"
```

---

### Task 8: Template Migration + MGRenderer Path Update

**Files:**
- Modify: `clipwright/animation/mg_renderer.py:233-248` (load_animation, list_animations)
- Migrate: `plugins/mg_animations/animations/*.json` → `plugins/llm_mg/templates/`
- Delete: `plugins/mg_animations/` (after migration)

- [ ] **Step 1: Copy template files**

```bash
Copy-Item "plugins/mg_animations/animations/*.json" -Destination "plugins/llm_mg/templates/"
```

- [ ] **Step 2: Update MGRenderer.load_animation()**

In `clipwright/animation/mg_renderer.py`, replace the `load_animation` method (lines 234-248):

```python
    @staticmethod
    def load_animation(anim_id: str) -> dict | None:
        """按 animation_id 加载 MG 动画定义。

        搜索顺序:
        1. plugins/llm_mg/templates/ (正式插件路径)
        2. plugins/mg_animations/animations/ (向后兼容, deprecated)
        """
        import json

        search_paths = [
            Path(__file__).resolve().parent.parent.parent / "plugins" / "llm_mg" / "templates",
            Path(__file__).resolve().parent.parent.parent / "plugins" / "mg_animations" / "animations",
        ]

        for base_dir in search_paths:
            if not base_dir.exists():
                continue
            for f in base_dir.iterdir():
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        if data.get("animation_id") == anim_id:
                            return data
                    except Exception:
                        continue
        return None
```

- [ ] **Step 3: Update MGRenderer.list_animations()**

In `clipwright/animation/mg_renderer.py`, replace the `list_animations` method (lines 251-270):

```python
    @staticmethod
    def list_animations() -> list[dict]:
        """列出所有可用的 MG 动画。"""
        import json

        search_paths = [
            Path(__file__).resolve().parent.parent.parent / "plugins" / "llm_mg" / "templates",
            Path(__file__).resolve().parent.parent.parent / "plugins" / "mg_animations" / "animations",
        ]

        seen_ids: set[str] = set()
        anims: list[dict] = []

        for base_dir in search_paths:
            if not base_dir.exists():
                continue
            for f in sorted(base_dir.iterdir()):
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        aid = data.get("animation_id", "")
                        if aid and aid not in seen_ids:
                            seen_ids.add(aid)
                            anims.append({
                                "id": aid,
                                "name": data.get("name", ""),
                                "description": data.get("description", ""),
                                "duration_sec": data.get("duration_sec", 3.0),
                                "params": list(data.get("params", {}).keys()),
                            })
                    except Exception:
                        continue
        return anims
```

- [ ] **Step 4: Verify templates still loadable**

```bash
python -c "
from clipwright.animation.mg_renderer import MGRenderer
anims = MGRenderer.list_animations()
print('Found', len(anims), 'animations')
for a in anims:
    print(f'  {a[\"id\"]}: {a[\"name\"]}')
"
```
Expected: 5 animations from both paths.

- [ ] **Step 5: Remove old mg_animations pseudo-plugin**

```bash
Remove-Item -Recurse -Force "plugins/mg_animations"
```

- [ ] **Step 6: Commit**

```bash
git add plugins/llm_mg/templates/ clipwright/animation/mg_renderer.py
git rm -r plugins/mg_animations/
git commit -m "feat(llm_mg): migrate templates, update MGRenderer search paths, remove mg_animations pseudo-plugin"
```

---

### Task 9: AnimationAgent — mg_dynamic Routing

**Files:**
- Modify: `clipwright/agents/animation_agent.py:246-261` (_handle_logic_animation)
- Add: `_handle_llm_mg()` method

- [ ] **Step 1: Add mg_dynamic routing in _handle_logic_animation()**

In `_handle_logic_animation()` (right before the existing `mg_` check at line 256), add a check for `mg_dynamic`:

```python
        # ── LLM 动态 MG 动画路由 ──
        if anim_id == "mg_dynamic":
            await self._handle_llm_mg(
                anim_track, vid_clip, anim_id, anim_name,
                text_content, duration, marker, persona_style,
            )
            return
```

- [ ] **Step 2: Add _handle_llm_mg() method**

After the existing `_handle_mg_animation()` method (around line 414), add:

```python
    async def _handle_llm_mg(
        self,
        anim_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        text_content: str,
        duration: float,
        marker: dict[str, Any],
        persona_style: dict[str, Any] | None = None,
    ) -> None:
        """处理 mg_dynamic 标记 — 通过 llm_mg 插件动态生成 MG 动画。"""
        # 获取插件
        from clipwright.plugins import PluginLoader
        loader = PluginLoader()
        plugin = loader.get("llm_mg")

        if plugin is None:
            logger.warning("AnimationAgent: llm_mg 插件未加载，mg_dynamic 降级为 drawtext")
            self._add_trace_warning("animation", f"LLM MG 插件未加载，动画 {anim_name} 降级为文字显示")
            self._create_fallback_text_clip(anim_track, vid_clip, anim_name, text_content, duration)
            return

        # 构建场景上下文
        scene_meta = vid_clip.metadata or {}
        scene_context = {
            "title": scene_meta.get("title", ""),
            "keywords": scene_meta.get("keywords", []),
            "description": scene_meta.get("description", ""),
        }

        # 调用插件生成
        persona = persona_style or {}
        try:
            result = await plugin.generate_mg(
                description=text_content or marker.get("description", ""),
                text_content=text_content,
                persona_style=persona,
                scene_context=scene_context,
            )
        except Exception as e:
            logger.exception("AnimationAgent: llm_mg.generate_mg() 异常: %s", e)
            self._create_fallback_text_clip(anim_track, vid_clip, anim_name, text_content, duration)
            return

        if not result.get("success"):
            logger.warning("AnimationAgent: llm_mg 生成失败 (method=%s)", result.get("method"))
            self._add_trace_warning("animation",
                f"LLM MG 生成失败，使用降级方案: {result.get('fallback_template', 'none')}")

        # 获取 HTML
        html = result.get("html", "")
        mg_def = result.get("mg_def", {})
        method = result.get("method", "unknown")
        generation_id = result.get("generation_id", "")

        if not html:
            self._create_fallback_text_clip(anim_track, vid_clip, anim_name, text_content, duration)
            return

        clip_dur = min(mg_def.get("duration_sec", duration), vid_clip.duration_sec)

        anim_clip = Clip(
            id=_uid("mgd"),
            kind=ClipKind.ANIMATION,
            asset_id="",
            track_id=anim_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_dur,
            text=text_content,
            metadata={
                "anim_type": anim_id,
                "anim_name": anim_name,
                "category": "mg_dynamic",
                "renderer": "mg_hyperframes",
                "mg_html": html,
                "mg_def": mg_def,
                "mg_method": method,
                "mg_generation_id": generation_id,
                "mg_fallback_template": result.get("fallback_template"),
                "position": "center",
            },
        )
        anim_track.clips.append(anim_clip)
        anim_track.clips.sort(key=lambda c: c.start_sec)
        logger.info("AnimationAgent: [LLM MG]%s → method=%s, html=%d chars",
                     anim_name, method, len(html))

    def _create_fallback_text_clip(
        self,
        anim_track: Track,
        vid_clip: Clip,
        anim_name: str,
        text_content: str,
        duration: float,
    ) -> None:
        """创建降级文字 clip。"""
        text_clip = Clip(
            id=_uid("fl"),
            kind=ClipKind.TEXT,
            asset_id="",
            track_id=anim_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=min(duration, 5.0),
            text=f"{anim_name}: {text_content[:50]}",
            font_size=36,
            font_color="#ffffff",
            metadata={
                "anim_type": "fallback_text",
                "renderer": "drawtext",
                "position": "center",
            },
        )
        anim_track.clips.append(text_clip)
        anim_track.clips.sort(key=lambda c: c.start_sec)

    @staticmethod
    def _add_trace_warning(category: str, message: str) -> None:
        """添加 trace 警告事件。"""
        try:
            from clipwright.services.trace import add_event as _evt
            _evt("", "animation", "warning", message)
        except Exception:
            pass
```

- [ ] **Step 3: Verify import**

```bash
python -c "from clipwright.agents.animation_agent import AnimationAgent; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add clipwright/agents/animation_agent.py
git commit -m "feat(animation): add mg_dynamic routing and LLM MG handler"
```

---

### Task 10: RequirementsAgent — animation_intent Prompt

**Files:**
- Modify: `clipwright/agents/requirements_agent.py:20-46` (CREATIVE_BRIEF_SYSTEM)

- [ ] **Step 1: Extend CREATIVE_BRIEF_SYSTEM prompt**

In `requirements_agent.py`, replace the `CREATIVE_BRIEF_SYSTEM` string (lines 20-46):

```python
CREATIVE_BRIEF_SYSTEM = """你是一位专业的视频创作顾问。你的任务是与用户对话，收集创作需求，逐步完善创作方案。

## 对话策略
- 每次回复都要推进对话：回复用户 + 追问关键缺失信息
- 当收集到足够信息时，设置 is_ready=true
- 信息不足的标准：至少需要了解 主题/目标受众/风格方向

## 动画需求识别
如果用户的创作需求中提到了视觉效果、数据展示、对比、流程、图表等信息呈现需求，
在 brief_draft 中设置 animation_intents 数组，每个元素描述一个场景的动画需求。

animation_intents 格式:
[
  {
    "scene_index": null,
    "type": "mg",
    "description": "自然语言描述该动画应呈现的效果",
    "text_content": "动画中要显示的文字内容，多段内容用 | 分隔",
    "style_hint": "样式提示: tech_dark / minimal_clean / bold_vibrant / retro",
    "suggested_template": "最接近的现有模板 ID，不确定则留空"
  }
]

类型说明:
- type="mg": 动态图形动画（数据图表、标题揭示、进度条、对比图等）
- type="text": 文字入场动画（打字机、淡入、弹跳等）
- type="logic": 逻辑关系图解（箭头、流程图、因果关系等）

只在用户明确需要视觉信息呈现（图表/对比/数据可视化/标题动画）时填写 animation_intents。

## 输出格式（纯 JSON）
{
  "reply": "对用户的自然语言回复",
  "brief_draft": {
    "title": "视频标题",
    "overview": "概述",
    "target_audience": "目标受众",
    "core_message": "核心信息",
    "style_direction": "风格方向",
    "structure_suggestion": "结构建议",
    "duration_estimate": "预估时长",
    "key_elements": ["元素1"],
    "special_requirements": [],
    "animation_intents": []
  },
  "is_ready": false,
  "missing_info": ["还未了解的信息"]
}

当 is_ready=true 时，brief_draft 必须完整填写。
"""
```

- [ ] **Step 2: Verify prompt is parseable**

```bash
python -c "from clipwright.agents.requirements_agent import CREATIVE_BRIEF_SYSTEM; print(len(CREATIVE_BRIEF_SYSTEM))"
```

- [ ] **Step 3: Commit**

```bash
git add clipwright/agents/requirements_agent.py
git commit -m "feat(requirements): add animation_intents detection to creative brief prompt"
```

---

### Task 11: Documentation Update

**Files:**
- Modify: `docs/api_reference.md`
- Modify: `docs/development.md`

- [ ] **Step 1: Add plugin API to api_reference.md**

Append after the existing Plugin section:

```markdown
### LLM MG Plugin API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/plugin/llm_mg/generate` | 调用 LLM 生成 MG 动画 JSON |
| POST | `/api/plugin/llm_mg/save-template` | 将生成结果保存为可复用模板 |
| GET | `/api/plugin/llm_mg/templates` | 列出所有可用 MG 模板 |
| GET | `/api/plugin/llm_mg/generations` | 列出未保存的生成记录 |
```

- [ ] **Step 2: Add plugin dev guide to development.md**

Append:

```markdown
## MG 动画插件开发

`plugins/llm_mg/` 提供 LLM 驱动的 MG 动画生成能力。

### 添加新模板

在 `plugins/llm_mg/templates/` 创建 JSON 文件，格式见 `mg_title_reveal.json`。

### 自定义 LLM prompt

编辑 `plugins/llm_mg/config.yaml` 中的 `prompt.system_template`。

### 插件接口

```python
plugin = PluginLoader().get("llm_mg")
result = await plugin.generate_mg(
    description="产品对比动画",
    text_content="A产品|B产品|A胜出",
    persona_style={"primary_color": "#4f8cff"},
    scene_context={"title": "性能对比", "keywords": ["CPU", "GPU"]},
)
# result: {success, html, mg_def, method, generation_id}
```
```

- [ ] **Step 3: Commit**

```bash
git add docs/api_reference.md docs/development.md
git commit -m "docs: add llm_mg plugin API reference and development guide"
```

---

## Verification

After all tasks complete, run the full verification:

```bash
# 1. Plugin discovery
python -c "from clipwright.plugins import PluginLoader; p=PluginLoader(); print('llm_mg' in p.discover())"

# 2. Schema imports
python -c "from clipwright.schema.agent import AnimationIntent, RequirementsOutput, AnimationOutput; print('Schema OK')"

# 3. Plugin load
python -c "from clipwright.plugins import PluginLoader; p=PluginLoader(); plugin=p.load('llm_mg'); print(type(plugin).__name__)"

# 4. Templates accessible
python -c "from clipwright.animation.mg_renderer import MGRenderer; print(len(MGRenderer.list_animations()), 'templates')"

# 5. Agent imports
python -c "from clipwright.agents.animation_agent import AnimationAgent; from clipwright.agents.requirements_agent import RequirementsAgent; print('Agents OK')"
```
