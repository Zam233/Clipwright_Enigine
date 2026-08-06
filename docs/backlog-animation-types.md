# 动画类型 Backlog — 广告 vs 实现缺口

> 来源：`442dfc9` 对齐 `test_animation_chain.py` 时发现。所有结论经实证脚本验证
> （`resolve_marker` 对每个名字的真实返回），非推断。
> 日期：2026-08-06
> **状态：✅ 已全部修复**（2026-08-06，P0-P3 全闭环，见各节「修复」标注）

## 结论摘要

`AnimationCatalog.resolve_marker()` 只遍历 `get_text_animations()`（5 个）+ `get_logic_animations()`（24 个）。
**onscreen（10 个）与 transition（10 个）动画虽有完整注册与关键帧，但从不参与 marker 解析**——
因此 `/api/animation/list` 对外广告的 20 个 onscreen/transition 名字，在场景 description 标记中 **100% 兜底为 `text_fade_in`（淡入）**。

三层缺口，按修复成本升序：

| 层 | 缺口 | 影响 | 建议成本 |
|----|------|------|----------|
| P0 | resolve 覆盖范围 | 20 个已实现动画名不可达 | 低 |
| P1 | 命名漂移 | 注册名 vs 广告/prompt 名不一致 | 低 |
| P2 | catalog.py 死代码 | 14 项 fallback 默认列表永不生效 | 极低 |
| P3 | 从未实现的逻辑类型 | 10 个图表类无渲染器 | 高 |

---

## P0 — resolve_marker 覆盖范围缺口（✅ 已修复）

**修复**（`catalog.py` + `builtin.py`）：
- `resolve_marker` 现遍历 `get_text_animations()`（已合并 TEXT+ONSCREEN 共 15 个）+ `get_logic_animations()`（24 个）+ transition 动画。
- 新增 `[转场动画]`/`[过渡动画]` 前缀支持（`parse_marker_from_description`），转场语义保留为片段间过渡。
- onscreen 动画（弹跳/模糊/脉冲等）现可直接经 `[文字动画]` marker 解析命中。
- 未匹配时回退 `text_fade_in` 并 `warn` 日志，区分拼写错误与不支持类型。

**现象**：以下名字注册于 `clipwright/animation/builtin.py`，`/api/animation/list`（`api/animation.py`）会返回给前端/LLM，但写进 description marker 后全部静默兜底为淡入。

### Onscreen（AnimationType.ONSCREEN，10 个）

| 注册 ID | 注册名 | marker 写「…」时 resolve 到 |
|---------|--------|------------------------------|
| fade_in | 淡入 | 淡入（误中 text_fade_in，语义撞名） |
| fade_out | 淡出 | text_fade_in |
| slide_up_in | 上滑进入 | text_fade_in |
| slide_down_out | 下滑退出 | text_fade_in |
| slide_left_in | 左滑进入 | text_fade_in |
| scale_in | 缩放进入 | text_fade_in |
| scale_bounce | 弹跳进入 | text_fade_in |
| blur_in | 模糊进入 | text_fade_in |
| rotate_in | 旋转进入 | text_fade_in |
| pulse | 脉冲强调 | text_fade_in |

### Transition（AnimationType.TRANSITION，10 个）

| 注册 ID | 注册名 | marker 写「…」时 resolve 到 |
|---------|--------|------------------------------|
| cut | 硬切 | text_fade_in |
| crossfade | 淡入淡出 | text_fade_in |
| fade_to_black | 黑场过渡 | text_fade_in |
| push_left | 左推 | text_fade_in |
| push_right | 右推 | text_fade_in |
| wipe_left | 左擦除 | text_fade_in |
| zoom_in | 放大进入 | text_fade_in |
| glitch | 故障干扰 | text_fade_in |
| pixel_dissolve | 像素溶解 | text_fade_in |
| slide_up | 上滑 | text_fade_in |

**广告出处**：
- `requirements_agent.py:45` — `type="text": 文字入场动画（打字机、淡入、弹跳等）` 广告「弹跳」
- `api/animation.py /list` — 返回全部 25 个注册（含上述 20 个）
- `structure_agent.py` — 经 `list_animations` tool 可见全部

**待决策（产品）**：
1. onscreen 动画是否应作为 `[文字动画]` 可解析？（`parse_marker_from_description` 只认 `[文字动画]`/`[逻辑动画]`/`[动画]` 三种前缀）
2. transition 动画是否需要 `[转场动画]` 前缀支持？目前**无此前缀**，且转场语义上是「片段之间」而非「片段入场」，可能需要单独的 timeline 属性而非 description marker。
3. 若上两者均不需要 → `resolve_marker` 应在未匹配时对「已注册但类型不支持」的名字给出**显式警告**（区分「拼写错」与「不支持的类型」），并让 `/api/animation/list` 只返回可解析的类型。

---

## P1 — 命名漂移（✅ 已修复）

**修复**：`resolve_marker` 新增 `_MARKER_ALIASES` 别名表（如 `弹跳 → scale_bounce`、`模糊 → blur_in`、`滑入 → slide_up_in`、`放大 → zoom_in`、`旋转 → rotate_in`、`脉冲 → pulse`），精确匹配 → 别名 → 包含匹配 → id → 兜底，四层解析防歧义（「淡入」先于「淡入淡出」精确命中）。
同时 `builtin.py` 补齐 4 个缺失注册（slide_down/下滑、slide_left/左滑、slide_right/右滑、shake/震动），消除 fallback 名单中不存在的 ID。

注册名与广告/prompt 名不一致，LLM 写的是后者，resolve 按前者匹配：

| 注册 ID | 注册名（builtin.py） | prompt/预期名 | 现状 |
|---------|---------------------|---------------|------|
| scale_bounce | 弹跳进入 | 弹跳 | 兜底淡入 |
| blur_in | 模糊进入 | 模糊 | 兜底淡入 |
| slide_up_in | 上滑进入 | 滑入 / 上滑 | 兜底淡入 |
| zoom_in | 放大进入（transition） | 放大 | 兜底淡入 |
| rotate_in | 旋转进入 | 旋转 | 兜底淡入 |
| pulse | 脉冲强调 | 脉冲 | 兜底淡入 |

**修复方向**：`resolve_marker` 增加别名表（如 `弹跳 → scale_bounce`），或统一命名。
注意：`resolve_marker` 的「包含匹配」逻辑（`a["name"] in marker_text or marker_text in a["name"]`）在加别名后需防歧义（如「淡入」会匹配「淡入淡出」）。

---

## P2 — catalog.py fallback 死代码（✅ 已修复）

**修复**：`get_text_animations()` 默认列表改为完整合并 TEXT（5）+ ONSCREEN（10）共 15 项，与注册表一致；9 个原不存在 ID 中 4 个已在 `builtin.py` 补齐注册（slide_down/slide_left/slide_right/shake），其余为 onscreen 语义映射（scale_bounce/blur_in/rotate_in/pulse/zoom_in 均已有真实注册）。<mark>待办：仍可从注册表生成以彻底去重，非阻塞。</mark>

`catalog.py:66-83` `get_text_animations()` 的默认列表**仅当 AnimationRegistry 为空时生效**。
`register_builtin_animations()` 在 `main.py:122` 启动时总会调用 → 此分支永不执行。

其中 9 个 id 在注册表中**不存在**（误导性广告）：

`slide_down`（下滑）、`slide_left`（左滑）、`slide_right`（右滑）、`zoom_in`（放大）、`shake`（震动）、`scale_bounce`（弹跳，实为 onscreen）、`blur_in`（模糊，实为 onscreen）、`rotate_in`（旋转，实为 onscreen）、`pulse`（脉冲，实为 onscreen）

**修复**：删除 fallback 分支，或改为从注册表生成（保留空表时的兜底文案即可）。

---

## P3 — 从未实现的逻辑动画类型（✅ 已实现）

**修复**：`diagram_svg.py` 新增 11 个渲染器（radar/gantt/venn3/heatmap/sankey/concept/codeblock/datatable/quote/compcard/orgchart），全部注册进 `renderer_map` 与 `get_supported_presets()`，按 `_build_diagram_params` 契约消费 items。

`test_animation_chain.py` 曾期望以下类型可解析（对应旧 catalog），重构后从未实现；
`DiagramRenderer.renderer_map`（`diagram_svg.py:91-105`）无对应 preset：

| 预期名 | 预期 ID | 当前行为 | 备注 |
|--------|---------|----------|------|
| 雷达图 | radar | 兜底 text_fade_in | 需新增渲染器 |
| 甘特图 | gantt | 兜底 text_fade_in | 需新增渲染器 |
| 热力图 | heatmap | 兜底 text_fade_in | 需新增渲染器 |
| 桑基图 | sankey | 兜底 text_fade_in | 需新增渲染器 |
| 概念图 | concept | 兜底 text_fade_in | 需新增渲染器 |
| 代码块 | codeblock | 兜底 text_fade_in | 需新增渲染器 |
| 数据表 | datatable | 兜底 text_fade_in | 需新增渲染器 |
| 引用 | quote | 兜底 text_fade_in | MG 已有 mg_quote_card（金句卡），可考虑映射 |
| 组织架构图 | orgchart | 兜底 text_fade_in | 可复用 tree/hierarchy 渲染器 + 别名 |
| 三维图 | venn3 | 兜底 text_fade_in | 可复用 venn 渲染器 + 别名 |
| 对比卡 | compcard | logic/comparison | ✅ 已可用（包含匹配「对比」） |
| 思维导图 | mindmap | logic/mg_mindmap | ✅ 已实现（MG 模板） |

**修复方向**：新增渲染器为高成本项；其中 3 项（引用/组织架构图/三维图）可用**已有渲染器 + 别名**低成本覆盖。

---

## 实证方法（可复现）

修复后验证脚本（应全部命中预期 ID，不再兜底 text_fade_in）：

```python
python -X utf8 -c "
import sys; sys.path.insert(0, '.')
from clipwright.animation import register_builtin_animations
from clipwright.animation.catalog import AnimationCatalog
register_builtin_animations()
# (name, 期望 anim_id)
expect = {
    '弹跳': 'scale_bounce', '模糊': 'blur_in', '淡入淡出': 'crossfade',
    '左推': 'push_left', '雷达图': 'radar', '思维导图': 'mg_mindmap',
    '对比卡': 'compcard', '下滑': 'slide_down', '震动': 'shake',
}
for p, want in expect.items():
    got = AnimationCatalog.resolve_marker(p)['anim_id']
    print(p, '->', got, 'OK' if got == want else f'FAIL expect {want}')
"
```

回归保障：`tests/test_animation_chain.py`（177 断言）+ `tests/clipwright/test_animation.py`（35 断言）全绿；全量 pytest 698 通过，唯一失败 `test_pipeline.py::test_category_registry` 为 **pre-existing** 顺序依赖（`test_pipeline_diag.py` 模块顶层注册插件导致重复注册，stash 后仍复现，与本次改动无关）。
