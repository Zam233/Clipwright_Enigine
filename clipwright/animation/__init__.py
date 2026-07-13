"""Animation 系统 — 基于 JSON 规范的动画引擎。

## 架构

```
AnimationRegistry          ← 存所有 AnimationDef（onscreen + transition）
  ├── builtin.py            ← 内置动画（12 onscreen + 10 transition）
  ├── plugins/              ← 第三方插件注册新动画
  └── spec.json             ← JSON 格式定义

AnimationRenderer          ← 将 AnimationDef 渲染为时间线操作
  ├── render_sequence()     ← 编排序列 → 操作列表
  ├── render_onscreen()     ← 屏幕动画 → overlay
  └── render_transition()   ← 转场动画 → FFmpeg filter

AnimationAgent             ← 解析 Persona 配置 → 选择动画 → 编排序列
```

## 用法

```python
from clipwright.animation import AnimationRegistry, AnimationRenderer

# 注册动画
AnimationRegistry.register(my_anim_def)

# 渲染动画
sequence = AnimationSequence(...)
ops = AnimationRenderer.render_sequence(AnimationRegistry._animations, sequence)
```
"""

from clipwright.animation.builtin import register_builtin_animations
from clipwright.animation.catalog import AnimationCatalog
from clipwright.animation.registry import AnimationRegistry
from clipwright.animation.renderer import AnimationRenderer, TimelineAnimationOp

__all__ = [
    "AnimationCatalog",
    "AnimationRegistry",
    "AnimationRenderer",
    "TimelineAnimationOp",
    "register_builtin_animations",
]
