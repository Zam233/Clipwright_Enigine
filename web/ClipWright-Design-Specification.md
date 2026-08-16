> 🎨 **Design Authority** — this specification defines the INTENDED design system for ClipWright. It is the source of truth for visual decisions. Implementation conformance should be verified against this document, not the reverse. Version 1.0.0 · 2026-07-20.

# ClipWright 前端设计规范

> **Version**: 1.0.0 | **Date**: 2026-07-20
> **Design System**: Material You (Material Design 3) + Premiere Pro Professional Aesthetic
> **Target**: Full-stack AI-assisted video creation web application

---

## 目录

1. [设计理念](#1-设计理念)
2. [色彩系统](#2-色彩系统)
3. [字体系统](#3-字体系统)
4. [间距与网格](#4-间距与网格)
5. [形状与圆角](#5-形状与圆角)
6. [阴影与层级](#6-阴影与层级)
7. [图标系统](#7-图标系统)
8. [动效规范](#8-动效规范)
9. [布局系统](#9-布局系统)
10. [组件规范](#10-组件规范)
11. [时间轴设计规范](#11-时间轴设计规范)
12. [暗色主题规范](#12-暗色主题规范)
13. [响应式与适配](#13-响应式与适配)
14. [无障碍设计](#14-无障碍设计)
15. [附录](#15-附录)

---

## 1 设计理念

### 1.1 核心原则

ClipWright 的设计融合两个看似矛盾的基因：

| 基因 | 来源 | 特征 |
|------|------|------|
| **Material You** | Google Material Design 3 | 动态色彩、有机形状、流畅动效、自适应对比度 |
| **Premiere Pro** | Adobe 专业视频编辑 | 暗色界面、精确控件、面板式布局、帧级精度 |

> **一句话定义**: "Material You 的专业视频编辑皮肤" — 保留 Material 的动态色彩科学和交互流畅感，但穿上 Premiere 的暗色高密度工作界面。

### 1.2 设计目标

1. **专业感**: 创作者坐下去就知道这是生产力工具，不是玩具
2. **沉浸感**: 暗色界面减少视觉疲劳，长时间编辑不刺眼
3. **精确感**: 每个像素、每帧都有意义，控件粒度支持帧级操作
4. **现代感**: Material You 的有机动效让"重型"视频编辑工具显得轻盈灵动
5. **可访问性**: WCAG 2.1 AA 标准，键盘优先级等同于鼠标

### 1.3 视觉情绪板

```
Premiere Pro 基因 (60%)           Material You 基因 (40%)
┌──────────────────────────┐    ┌──────────────────────────┐
│ ▪ 全暗色界面              │    │ ▪ 动态取色 (Monet)        │
│ ▪ 高密度面板布局           │    │ ▪ 圆角卡片 + 微妙阴影     │
│ ▪ 亮色时间轴轨道           │    │ ▪ 弹性缓动动画            │
│ ▪ 精确的拖拽手柄           │    │ ▪ 涟漪反馈 (Ripple)       │
│ ▪ 帧级刻度尺              │    │ ▪ 自适应 contrast ratio   │
│ ▪ 灰底黑字的代码感         │    │ ▪ Material 状态层          │
└──────────────────────────┘    └──────────────────────────┘
```

---

## 2 色彩系统

### 2.1 Material You 动态色彩架构

采用 Material You 的 **Tonal Palette** 系统，以源色 (Source Color) 衍生出 13 级色调色板。源色设定为 Premiere 风格的蓝紫色。

```
源色 (Primary Source): #4F6BED (ClipWright Blue)
                                    
  Primary 色阶 (0→1000)            Secondary 色阶          Tertiary 色阶
  ┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
  │   0: #000000         │   │   0: #000000         │   │   0: #000000         │
  │  10: #001036         │   │  10: #0E1022         │   │  10: #1A0C10         │
  │  20: #001C55         │   │  20: #242538         │   │  20: #31101E         │
  │  30: #1A3074         │   │  30: #3B3C50         │   │  30: #491B2E         │
  │  40: #34448D         │   │  40: #535468         │   │  40: #622B40         │
  │  50: #4F6BED  ← 源色 │   │  50: #6C6D81         │   │  50: #7D3B53         │
  │  60: #6A86FA         │   │  60: #86879B         │   │  60: #984C66         │
  │  70: #87A2FF         │   │  70: #A0A1B5         │   │  70: #B45D7A         │
  │  80: #B4C5FF         │   │  80: #BBBCD0         │   │  80: #D1708E         │
  │  90: #DBE2FF         │   │  90: #D7D8EC         │   │  90: #EF84A3         │
  │  95: #EDF0FF         │   │  95: #E6E6FB         │   │  95: #FF95B2         │
  │  99: #FDFBFF         │   │  99: #FDFBFF         │   │  99: #FDFBFF         │
  │ 100: #FFFFFF         │   │ 100: #FFFFFF         │   │ 100: #FFFFFF         │
  └──────────────────────┘   └──────────────────────┘   └──────────────────────┘
```

### 2.2 语义色彩令牌 (Design Tokens)

#### 亮色主题 (Light Theme) — 仅用于文档/设置页面

| Token | Light Value | Dark Value | 用途 |
|-------|------------|------------|------|
| `--color-surface` | `#FDFBFF` | `#0E101A` | 面板/卡片背景 |
| `--color-surface-dim` | `#F0F1F5` | `#11131D` | 次级面板背景 |
| `--color-surface-bright` | `#FFFFFF` | `#1A1D29` | 高亮面板 |
| `--color-surface-container` | `#EBECF0` | `#161824` | 容器背景（卡片内） |
| `--color-surface-container-low` | `#F5F6FA` | `#12141E` | 低层级容器 |
| `--color-surface-container-high` | `#E0E2E8` | `#1C1F2C` | 高层级容器 |
| `--color-on-surface` | `#1B1B21` | `#E2E2EC` | 面板上主要文字 |
| `--color-on-surface-variant` | `#46464F` | `#C4C4D0` | 面板上次要文字 |
| `--color-primary` | `#34448D` | `#B4C5FF` | 主色调（按钮、选中） |
| `--color-on-primary` | `#FFFFFF` | `#001C55` | 主色调上的文字 |
| `--color-primary-container` | `#DBE2FF` | `#1A3074` | 主色容器背景 |
| `--color-on-primary-container` | `#001C55` | `#DBE2FF` | 主色容器文字 |
| `--color-secondary` | `#535468` | `#BBBCD0` | 次要色调 |
| `--color-secondary-container` | `#D7D8EC` | `#3B3C50` | 次要容器 |
| `--color-tertiary` | `#622B40` | `#D1708E` | 强调色（警告、特殊） |
| `--color-error` | `#BA1A1A` | `#FFB4AB` | 错误/危险 |
| `--color-outline` | `#73737D` | `#8D8D99` | 边框线 |
| `--color-outline-variant` | `#C4C4D0` | `#46464F` | 次级边框 |

#### Premiere 风格专用色（覆盖 Material 语义）

Material You 不原生支持"视频轨道颜色"这种语义。以下为 ClipWright **扩展令牌**：

| Token | Value | 用途 |
|-------|-------|------|
| `--cw-track-video` | `#4F8CFF` (蓝) | 视频轨道及片段 |
| `--cw-track-audio` | `#34D399` (绿) | 音频轨道及片段 |
| `--cw-track-text` | `#FBBF24` (琥珀) | 文字轨道及片段 |
| `--cw-track-image` | `#A855F7` (紫) | 图片轨道及片段 |
| `--cw-track-caption` | `#F59E0B` (橙) | 字幕轨道及片段 |
| `--cw-track-animation` | `#FF6B6B` (红) | 动画轨道及片段 |
| `--cw-playhead` | `#FF4444` (红) | 播放头 |
| `--cw-marker` | `#FFD700` (金) | 标记点 |
| `--cw-snap-guide` | `#00E5FF` (青) | 吸附辅助线 |
| `--cw-selection` | `#4F8CFF55` (蓝 33%) | 选中高亮 |
| `--cw-ruler-bg` | `#1A1D29` (深灰) | 时间刻度尺背景 |
| `--cw-ruler-tick` | `#46464F` (边框灰) | 时间刻度 |
| `--cw-grid` | `#2A2A3A` (暗灰) | Canvas 网格线 |
| `--cw-keyframe-dot` | `#FBBF24` (琥珀) | 关键帧圆点 |
| `--cw-keyframe-selected` | `#4F8CFF` (蓝) | 选中关键帧 |
| `--cw-agent-bubble` | `#1A3074` (主色容器) | Agent 消息气泡 |

### 2.3 对比度策略

所有 UI 元素必须满足 WCAG 2.1 AA 标准：

| 元素 | 要求 | 检查方式 |
|------|------|---------|
| 正文文字 (14px+) | ≥ 4.5:1 | `on-surface` vs `surface` |
| 大文字 (18px+) | ≥ 3:1 | 标题文字 |
| 轨道标签 (10px) | ≥ 3:1（豁免小字） | 使用 `on-surface-variant` |
| 选中态边框 | 明显可见即可 | 颜色突变 > 行为检测 |

---

## 3 字体系统

### 3.1 字体族

```css
/* 编辑器 UI 字体 — 等宽优先，保证刻度尺/时间码对齐 */
--cw-font-mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace;

/* 面板 UI 字体 — Material You 标准 */
--cw-font-sans: 'Inter', 'Noto Sans SC', -apple-system, 'Segoe UI', sans-serif;

/* 预览窗口文字 — 使用系统字体确保中文渲染 */
--cw-font-display: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;

/* 代码/JSON 显示 */
--cw-font-code: 'JetBrains Mono', 'Fira Code', monospace;
```

### 3.2 字体大小阶梯 (Type Scale)

采用 Material 3 Type Scale，针对密集编辑器微调。

| Token | Size | Line Height | Weight | 用途 |
|-------|------|-------------|--------|------|
| `--cw-text-display` | 36px | 44px | 400 | 页面大标题（极少使用） |
| `--cw-text-headline` | 24px | 32px | 400 | 面板标题 |
| `--cw-text-title` | 20px | 28px | 500 | 卡片标题 |
| `--cw-text-title-sm` | 16px | 24px | 500 | 子标题 |
| `--cw-text-body` | 14px | 20px | 400 | 正文 |
| `--cw-text-body-sm` | 12px | 16px | 400 | 次要文字 |
| `--cw-text-label` | 11px | 16px | 500 | 标签、表单标签 |
| `--cw-text-label-sm` | 10px | 14px | 500 | 轨道标签、刻度 |
| `--cw-text-caption` | 9px | 12px | 400 | 时间码、帧计数 |
| `--cw-text-mono` | 12px | 18px | 400 | 代码、JSON |

### 3.3 字体使用规则

| 上下文 | 字体族 | 大小 | 粗细 |
|--------|--------|------|------|
| 时间刻度尺数字 | `--cw-font-mono` | `--cw-text-caption` | 400 |
| 时间码显示 | `--cw-font-mono` | `--cw-text-body` | 500 |
| 轨道名称标签 | `--cw-font-sans` | `--cw-text-label-sm` | 500 |
| 片段内文字 | `--cw-font-sans` | `--cw-text-caption` | 400 |
| 面板标题 | `--cw-font-sans` | `--cw-text-title-sm` | 500 |
| 属性标签 | `--cw-font-sans` | `--cw-text-label` | 500 |
| Agent 对话 | `--cw-font-sans` | `--cw-text-body-sm` | 400 |
| 代码/JSON 输出 | `--cw-font-code` | `--cw-text-mono` | 400 |
| 预览窗口文字（用户视频内） | `--cw-font-display` | 由用户设定 | — |

---

## 4 间距与网格

### 4.1 基础间距单位

基于 4px 网格系统（Material 3 标准）：

```
0  4  8  12  16  20  24  28  32  36  40  44  48  56  64  72  80  96
```

### 4.2 编辑器专用间距令牌

视频编辑器比普通 Web 应用需要**更紧凑**的间距。以下令牌在 `gap` 和 `padding` 全局使用：

```css
--cw-space-2xs: 2px;    /* 轨道间分隔 */
--cw-space-xs:  4px;    /* 紧凑元素间距 */
--cw-space-sm:  8px;    /* 面板内元素间距 */
--cw-space-md:  12px;   /* 面板内 Section 间距 */
--cw-space-lg:  16px;   /* 面板 padding */
--cw-space-xl:  24px;   /* 面板间 gutter */
--cw-space-2xl: 32px;   /* 大区块分隔 */
```

### 4.3 面板布局网格

```
┌──────────────────────────────────────────────────────────────────┐ 8px
│                        TopToolbar (48px)                          │
├────────────┬───────────────────────────────────┬──────────────────┤ 4px
│ 8px        │ 8px                               │ 8px              │
│            │                                   │                  │
│ Left Panel │          Center Panel             │  Right Panels    │
│ (240px     │    Preview Canvas + Timeline      │  (280px+300px    │
│  default)  │                                   │   stack)         │
│            │                                   │                  │
│ 8px        │ 8px                               │ 8px              │
├────────────┴───────────────────────────────────┴──────────────────┤ 4px
│                     StatusBar (28px)                               │
└──────────────────────────────────────────────────────────────────┘ 8px
```

面板分隔线 (Resizer) 宽度: **4px**, hover 时高亮 `--color-primary`

### 4.4 Canvas 内网格

时间轴 Canvas 内部使用独立坐标系统：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 轨道高度 | 48px | 单轨道行高 |
| 轨道间距 | 2px | 轨道间分隔 |
| 轨道头部宽度 | 120px | 左侧轨道名称/图标区 |
| 刻度尺高度 | 24px | 顶部时间刻度 |
| 片段最小可见宽度 | 4px | 小于此宽度的片段合并为指示线 |
| 片段圆角 | 3px | 片段矩形圆角 |
| 关键帧圆点直径 | 10px | 菱形内切圆 |
| 播放头宽度 | 2px | 红色竖线 |

---

## 5 形状与圆角

### 5.1 Material You Shape Scale

Material 3 定义了 7 级圆角，ClipWright 选取其中 4 级：

| Token | Radius | 用途 |
|-------|--------|------|
| `--cw-shape-none` | 0px | Canvas 元素、片段矩形、表格 |
| `--cw-shape-xs` | 4px | 标签、徽章、小按钮 |
| `--cw-shape-sm` | 8px | 卡片、面板、输入框 |
| `--cw-shape-md` | 12px | 对话框、弹出面板、大卡片 |
| `--cw-shape-lg` | 16px | 模态框、主要 CTA 按钮 |
| `--cw-shape-full` | 9999px | 圆形头像、药片按钮 |

### 5.2 Premiere 风格专用形状规则

- **片段 (Clip)**：`border-radius: 3px`，不对齐 Material scale。这是 Premiere 传统
- **轨道背景**：无圆角，全宽矩形
- **播放头**：纯竖线，无圆角
- **时间刻度尺**：无圆角
- **面板卡片**：`--cw-shape-sm` (8px)
- **Agent 消息气泡**：`--cw-shape-md` (12px)，带方向三角

---

## 6 阴影与层级

### 6.1 Material 3 Elevation（暗色主题适配）

暗色模式下阴影通过**表面亮度提升**表达层级，而非传统 box-shadow：

| Level | Surface Token | Shadow | 用途 |
|:-----:|---------------|--------|------|
| 0 | `surface` | `none` | 时间轴背景、Canvas |
| 1 | `surface-container-low` | `0 1px 2px rgba(0,0,0,0.3)` | 内嵌卡片 |
| 2 | `surface-container` | `0 1px 2px rgba(0,0,0,0.3), 0 2px 6px rgba(0,0,0,0.15)` | 面板、工具栏 |
| 3 | `surface-container-high` | `0 4px 8px rgba(0,0,0,0.15), 0 1px 3px rgba(0,0,0,0.3)` | 弹出面板 |
| 4 | `surface-dim` (反转) | `0 6px 10px rgba(0,0,0,0.15), 0 1px 18px rgba(0,0,0,0.12)` | 模态框 |
| 5 | — | `0 8px 10px rgba(0,0,0,0.15), 0 3px 14px rgba(0,0,0,0.12)` | 对话框、抽屉 |

### 6.2 Premiere 风格阴影规则

Premiere 不使用 Material 的多层阴影。编辑器内面板使用**扁平设计 + 细边框**：

```css
/* 面板卡片 — Premiere 风格 */
.panel-card {
  background: var(--color-surface-container);
  border: 1px solid var(--color-outline-variant);
  border-radius: var(--cw-shape-sm);
  box-shadow: none; /* Premiere 不使用阴影 */
}

/* 工具栏 — 悬浮时有轻微阴影 */
.toolbar {
  background: var(--color-surface-container-high);
  border-bottom: 1px solid var(--color-outline-variant);
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
```

---

## 7 图标系统

### 7.1 图标库

使用 **Material Symbols**（Google 官方图标库），填充风格 (Filled) 为主要，轮廓风格 (Outlined) 为次要：

```html
<!-- 主要动作 → Fill -->
<span class="material-symbols-filled">play_arrow</span>

<!-- 次要/禁用 → Outlined -->
<span class="material-symbols-outlined">more_horiz</span>
```

### 7.2 图标尺寸

| 上下文 | 尺寸 | 光学尺寸 |
|--------|------|:--------:|
| 工具栏按钮 | 20px | 20 |
| 面板内按钮 | 18px | 20 |
| 轨道头部按钮 | 14px | 20 |
| 播放控制 | 24px | 24 |
| 状态指示点 | 8px | — |

### 7.3 关键图标映射

| 功能 | Material Symbol | 替代方案 |
|------|----------------|---------|
| 播放 | `play_arrow` | — |
| 暂停 | `pause` | — |
| 停止 | `stop` | — |
| 逐帧前进 | `skip_next` | — |
| 逐帧后退 | `skip_previous` | — |
| 全屏 | `fullscreen` | — |
| 分割片段 | `content_cut` | `scissors` |
| 选择工具 (V) | `near_me` | — |
| 剃刀工具 (B) | `content_cut` | — |
| 缩放 | `zoom_in` / `zoom_out` | — |
| 添加轨道 | `playlist_add` | — |
| 锁定 | `lock` | — |
| 静音 | `volume_off` | — |
| 撤销 | `undo` | — |
| 重做 | `redo` | — |
| 保存 | `save` | — |
| 导出 | `file_export` | `ios_share` |
| 设置 | `settings` | — |
| Agent | `psychology` | `smart_toy` |
| Persona | `person` | `face` |
| 素材库 | `folder` | `video_library` |
| 关键帧 | `diamond` | `circle` (小圆点) |

---

## 8 动效规范

### 8.1 Material 3 缓动曲线

```css
/* 标准缓动 — 强调进入/退出 */
--cw-easing-standard: cubic-bezier(0.2, 0.0, 0.0, 1.0);
--cw-easing-standard-decelerate: cubic-bezier(0.0, 0.0, 0.0, 1.0);
--cw-easing-standard-accelerate: cubic-bezier(0.3, 0.0, 1.0, 1.0);

/* 强调缓动 — 入场动画 */
--cw-easing-emphasized: cubic-bezier(0.05, 0.7, 0.1, 1.0);

/* 弹性缓动 — 抽屉、面板 */
--cw-easing-legacy: cubic-bezier(0.4, 0.0, 0.2, 1.0);
```

### 8.2 动效时长令牌

| Token | 时长 | 用途 |
|-------|------|------|
| `--cw-duration-short1` | 50ms | 微交互：hover、focus |
| `--cw-duration-short2` | 100ms | 小元素出现：tooltip、badge |
| `--cw-duration-short3` | 150ms | 涟漪反馈 (Ripple) |
| `--cw-duration-short4` | 200ms | 小面板切换、tab 切换 |
| `--cw-duration-medium1` | 250ms | 属性值变化、滑块移动 |
| `--cw-duration-medium2` | 300ms | 面板折叠/展开、侧栏 |
| `--cw-duration-medium3` | 350ms | 中等面板出现 |
| `--cw-duration-long1` | 400ms | 对话框/模态框出现 |
| `--cw-duration-long2` | 450ms | 大型面板展开 |
| `--cw-duration-long3` | 500ms | 全屏过渡 |

### 8.3 关键交互动效规范

| 交互 | 动效 | 时长 | 缓动 |
|------|------|:---:|------|
| 按钮涟漪 (Ripple) | 圆形扩散 + opacity 衰减 | 150ms | decelerate |
| 面板折叠/展开 | 宽度/高度过渡 | 300ms | emphasized |
| 选项卡切换 | 指示器滑动 + 内容淡入 | 200ms | standard |
| 对话框出现 | scale(0.95→1) + opacity(0→1) | 400ms | emphasized |
| 对话框消失 | scale(1→0.95) + opacity(1→0) | 200ms | accelerate |
| Tooltip | opacity(0→1) + translateY(-4→0) | 100ms | decelerate |
| 下拉菜单 | scaleY(0→1) + opacity(0→1) | 200ms | standard |
| Snackbar/Toast | translateY(100%→0) + opacity | 300ms | decelerate |
| 面板 Resize 拖拽 | 即时跟随（无动画） | — | — |
| 播放头拖拽 | 即时跟随 | — | — |
| 片段拖拽移动 | 即时跟随 + 吸附弹性 | — | — |
| 关键帧插值预览 | 即时计算 | — | — |

### 8.4 动效禁用策略

当用户启用系统 "减少动效" (`prefers-reduced-motion: reduce`)，所有动画时长降为 **0ms**。

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

## 9 布局系统

### 9.1 顶级布局模式

ClipWright 使用 **Premiere 式固定面板 + 可拖拽调整宽度**布局：

```
┌─────────────────────────────────────────────────────────────┐
│ TopToolbar (48px fixed)                                      │
│ [项目名称] [Persona选择] [类型选择] | [←] [▶] [→] | [设置]  │
├──────────┬────────────────────────────────┬──────────────────┤
│          │                                │                  │
│ ASSETS   │         CENTER                 │   RIGHT STACK    │
│ PANEL    │                                │                  │
│          │   ┌──────────────────────┐     │  ┌────────────┐  │
│ 240px    │   │   Preview Canvas     │     │  │   AGENT    │  │
│ (折叠→   │   │   (16:9, min 320px)  │     │  │   PANEL    │  │
│  40px)   │   │                      │     │  │   300px    │  │
│          │   └──────────────────────┘     │  │  (可折叠)  │  │
│ ┌──────┐ │                                │  ├────────────┤  │
│ │AI匹配│ │   ┌──────────────────────┐     │  │ PROPERTIES │  │
│ ├──────┤ │   │   Timeline Canvas    │     │  │   PANEL    │  │
│ │素材库│ │   │   (多轨, 可滚动)     │     │  │   280px    │  │
│ ├──────┤ │   │                      │     │  │  (可折叠)  │  │
│ │历史  │ │   │                      │     │  └────────────┘  │
│ └──────┘ │   └──────────────────────┘     │                  │
│          │                                │                  │
├──────────┴────────────────────────────────┴──────────────────┤
│ StatusBar (28px fixed)                                        │
│ 帧率:30 | 1920×1080 | 时长:10:32 | 渲染: idle                │
└─────────────────────────────────────────────────────────────┘
```

### 9.2 面板行为

| 面板 | 默认宽 | 最小宽 | 最大宽 | 可折叠 | 双击标题栏 |
|------|:------:|:------:|:------:|:------:|-----------|
| Left (Assets) | 240px | 180px | 400px | Yes (→40px) | 恢复默认 |
| Right-Agent | 300px | 220px | 500px | Yes | 恢复默认 |
| Right-Properties | 280px | 200px | 450px | Yes | 恢复默认 |
| Center (Preview+Timeline) | flex:1 | 400px | — | No | — |

### 9.3 面板分隔线 (Resizer)

- 宽度: 4px
- 默认色: `transparent`
- Hover: `var(--color-primary)` 50% 不透明度
- Active (拖拽中): `var(--color-primary)`
- 光标: `col-resize`
- 拖拽时显示半透明预览线指示新位置

### 9.4 响应式断点

| 断点 | 宽度 | 行为 |
|------|------|------|
| **Desktop** | ≥ 1440px | 全功能布局 |
| **Laptop** | 1024-1439px | 可折叠面板，默认折叠 Agent 面板 |
| **Tablet** | 768-1023px | 单面板模式（Tab 切换 Left/Center/Right） |
| **Mobile** | < 768px | 仅供预览查看，不支持编辑 |

> **注意**: ClipWright 是专业桌面工具，不以移动端为目标。Tablet/Mobile 断点仅用于紧急查看。

---

## 10 组件规范

### 10.1 按钮 (Button)

遵循 Material 3 按钮规范。ClipWright 扩展 Premiere 风格的"紧凑工具按钮"。

| 变体 | 高度 | Padding | 字号 | 圆角 | 用途 |
|------|:----:|---------|------|------|------|
| **Filled** | 40px | 24px | 14px/500 | `--cw-shape-full` | 主要 CTA |
| **Outlined** | 40px | 24px | 14px/500 | `--cw-shape-full` | 次要动作 |
| **Text** | 40px | 12px | 14px/500 | `--cw-shape-full` | 低强调动作 |
| **Toolbar Icon** | 32px | — | 20px icon | `--cw-shape-xs` | 工具栏图标按钮 |
| **Track Header** | 20px | — | 14px icon | `--cw-shape-xs` | 轨道头部按钮 |
| **Compact** | 28px | 12px | 12px/500 | `--cw-shape-xs` | 面板内小按钮 |

```css
/* Filled Button — Material You */
.btn-primary {
  height: 40px;
  padding: 0 24px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  border-radius: var(--cw-shape-full);
  font: var(--cw-text-label);
  transition: box-shadow var(--cw-duration-short3) var(--cw-easing-standard);
}
.btn-primary:hover {
  box-shadow: var(--cw-elevation-1);
}
/* State layer (Material You) */
.btn-primary::after {
  content: '';
  position: absolute; inset: 0;
  background: transparent;
  transition: background var(--cw-duration-short3);
}
.btn-primary:hover::after { background: rgba(255,255,255,0.08); }
.btn-primary:active::after { background: rgba(255,255,255,0.12); }
```

### 10.2 输入框 (Text Field)

Material 3 Filled Text Field，Premiere 紧凑间距。

```
┌─────────────────────────────────┐
│ Label (12px)          ↑ 8px    │
│ ┌─────────────────────────────┐ │
│ │ Input text (14px)    8px   │ │
│ └─────────────────────────────┘ │
│ Supporting text (12px)   ↓ 4px │
└─────────────────────────────────┘
```

| 属性 | 值 |
|------|-----|
| 高度 | 56px (含 label) |
| 输入区高度 | 40px |
| 背景 | `var(--color-surface-container-high)` |
| 边框 | `1px solid var(--color-outline)` |
| Focus 边框 | `2px solid var(--color-primary)` |
| 圆角 | `--cw-shape-xs` (顶部) |
| 内边距 | 8px 12px |

### 10.3 滑块 (Slider)

Material 3 Slider，带精确数值显示（Premiere 风格）。

```
   0%  ───●──────────────── 100%   [ 75% ]  ← 数值标签
        ▲  ──  active track (primary)
   inactive track (outline-variant)
        ●  ──  thumb (primary, 20px)
```

| 属性 | 值 |
|------|-----|
| Track 高度 | 4px |
| Thumb 直径 | 20px |
| Thumb 状态层 | hover: +8px 光环, active: +12px |
| 数值标签 | 右侧 40px 固定宽度, 等宽字体 |

### 10.4 选项卡 (Tabs)

Material 3 Secondary Tabs（带指示器），Premiere 式紧凑间距。

```
┌────────────────────────────────────┐
│ [ AI Match ] [ Library ] [ History ]│ ← Tab
│ ████████                            │ ← Indicator (2px)
└────────────────────────────────────┘
```

| 属性 | 值 |
|------|-----|
| 高度 | 40px |
| Tab padding | 12px 16px |
| 字号 | `--cw-text-label` (11px) |
| Indicator 高度 | 2px |
| Indicator 色 | `--color-primary` |
| 切换动效 | 200ms `--cw-easing-standard` |

### 10.5 对话框 (Dialog)

Material 3 Basic Dialog，暗色主题适配。

```
┌─────────────────────────────────┐
│                                 │
│  [Icon]  Title (20px)           │ ← 标题区
│         Supporting text (14px)  │
│                                 │
│  ┌─────────────────────────┐    │
│  │ Content area            │    │ ← 内容区（可滚动）
│  └─────────────────────────┘    │
│                                 │
│        [Cancel]  [Confirm]      │ ← 动作区
└─────────────────────────────────┘
```

| 属性 | 值 |
|------|-----|
| 最小宽度 | 280px |
| 最大宽度 | 560px |
| 圆角 | `--cw-shape-lg` (16px) |
| 背景 | `var(--color-surface-container-high)` |
| 背景遮罩 | `rgba(0,0,0,0.5)` |
| 出现动效 | 400ms emphasized |

### 10.6 Tooltip

Rich Tooltip（Material 3），支持标题+正文+快捷键。

```
┌─────────────────────────────┐
│ Split Clip (S)              │ ← 标题 (11px, 500)
│ Split the selected clip     │ ← 正文 (12px, 400)
│ at the playhead position    │
└─────────────────────────────┘
```

| 属性 | 值 |
|------|-----|
| 背景 | `var(--color-surface-container-high)` 或 `inverse-surface` |
| 圆角 | `--cw-shape-xs` (4px) |
| 最大宽度 | 280px |
| 出现延迟 | 500ms（Premiere 传统：长延迟避免误触） |
| 出现动效 | 100ms decelerate |
| 间距 (与目标) | 8px |

### 10.7 Toast / Snackbar

Material 3 Snackbar，底部居中。

```
┌────────────────────────────────────────────┐
│ [icon]  Pipeline completed successfully   │ ← 消息 (14px)
│                                 [Dismiss]  │ ← 动作按钮
└────────────────────────────────────────────┘
```

| 属性 | 值 |
|------|-----|
| 位置 | 底部居中，距底 24px |
| 最小宽度 | 320px，最大 560px |
| 持续时间 | 4s（错误类 8s） |
| 圆角 | `--cw-shape-xs` |
| 出现动效 | 300ms decelerate |

### 10.8 上下文菜单 (Context Menu)

右键菜单，Premiere 风格紧凑列表。

```
┌──────────────────────┐
│  Cut        Ctrl+X   │ ← 12px, 高度 32px
│  Copy       Ctrl+C   │
│  Paste      Ctrl+V   │
│ ──────────────────── │ ← 分隔线
│  Delete     Del      │
│  Duplicate  Ctrl+D   │
└──────────────────────┘
```

| 属性 | 值 |
|------|-----|
| 项高度 | 32px |
| 最小宽度 | 180px |
| 圆角 | `--cw-shape-sm` |
| 背景 | `var(--color-surface-container)` |
| 快捷键文字 | 右对齐, `--cw-font-mono`, `--color-on-surface-variant` |

---

## 11 时间轴设计规范

### 11.1 整体视觉

时间轴是 ClipWright 的视觉重心。采用 **深色 Canvas + 亮色轨道片段** 的 Premiere 经典配色。

```
┌─────────────────────────────────────────────────────────────┐
│ Time Ruler (24px, 深灰背景, 浅灰刻度)                       │
├────┬────────────────────────────────────────────────────────┤
│ V1 │  [══════ Clip A ═══════][════ Clip B ════]            │ ← 蓝
├────┼────────────────────────────────────────────────────────┤
│ V2 │       [═══ Text Anim ════]                             │ ← 琥珀
├────┼────────────────────────────────────────────────────────┤
│ A1 │  [═════════════════ BGM ═══════════════════════]       │ ← 绿
├────┼────────────────────────────────────────────────────────┤
│ A2 │         [════ Voice Over ════════]                     │ ← 绿(浅)
└────┴────────────────────────────────────────────────────────┘
│←─── 轨道头部 (120px) ───→│←─────── 片段区域 (可滚动) ──────→│
```

### 11.2 Canvas 颜色规范

| 元素 | 颜色令牌 | 值 (暗色) |
|------|---------|----------|
| Canvas 背景 | `--cw-ruler-bg` | `#1A1D29` |
| 轨道背景 (偶数) | `--color-surface-dim` | `#11131D` |
| 轨道背景 (奇数) | `--color-surface` | `#0E101A` |
| 网格线 | `--cw-grid` | `#2A2A3A` |
| 刻度尺背景 | `--cw-ruler-bg` | `#1A1D29` |
| 刻度尺文字 | `--color-on-surface-variant` | `#C4C4D0` |
| 刻度线 (主) | `--color-outline` | `#8D8D99` |
| 刻度线 (次) | `--color-outline-variant` | `#46464F` |
| 播放头 | `--cw-playhead` | `#FF4444` |
| 播放头三角 | 同上，等边三角形 (宽10px 高8px) | — |
| 标记点 | `--cw-marker` | `#FFD700` |
| 吸附辅助线 | `--cw-snap-guide` | `#00E5FF` |
| 框选矩形 | `--cw-selection` | `#4F8CFF33` + `1px solid #4F8CFF` |

### 11.3 片段 (Clip) 视觉规范

```
┌────────────────────────────────────────────┐
│ ← 入场转场图标  │  缩略图条带  │  出场转场 →│  ← 3px 圆角
│                 │              │            │
│ ┌─────────────┐ │ ┌──────────┐ │ ┌────────┐ │
│ │ 关键帧点     │ │ │ 文字标签  │ │ │ 关键帧 │ │  ← 10px 菱形
│ └─────────────┘ │ └──────────┘ │ └────────┘ │
│ ← 左拖拽手柄    │              │  右拖拽 →  │  ← 6px 宽区域
└────────────────────────────────────────────┘
```

| 元素 | 规范 |
|------|------|
| 片段主体 | 半透明轨道色 + 1px 实色边框 |
| 缩略图条带 | 从 `source_offset_sec` 起，按轨道高度平铺正方形帧 |
| 波形图 | 仅音频轨，顶部/底部对称波形，填充轨道色 |
| 文字片段标签 | 白色 9px，居中对齐，1px 黑色描边 |
| 选中态 | 2px `--color-primary` 边框 + 半透明填充 |
| 裁剪手柄 | 左右边缘 6px 宽不可见热区，hover 显示 `col-resize` 光标 + 2px 白色竖线 |
| 关键帧圆点 | 10px 菱形 (旋转45°正方形), 填充 `--cw-keyframe-dot`, 选中 `--cw-keyframe-selected` |
| 转场图标 | 16x16px, 圆角矩形, 半透明轨道色, 显示转场类型简写 |

### 11.4 片段颜色映射

| ClipKind | 背景色 (25% opacity) | 边框色 (100%) | 
|----------|:---------------------:|:-------------:|
| `video` | `#4F8CFF44` | `#4F8CFF` |
| `audio` | `#34D39944` | `#34D399` |
| `text` | `#FBBF2444` | `#FBBF24` |
| `image` | `#A855F744` | `#A855F7` |
| `caption` | `#F59E0B44` | `#F59E0B` |
| `shape` | `#F472B644` | `#F472B6` |
| `animation` | `#FF6B6B44` | `#FF6B6B` |

### 11.5 轨道头部规范

```
┌────┐
│ 🎬 │ ← 16px 图标 (Material Symbol)
│ V1 │ ← 轨道名称 (10px, 500)
│ 🔒 │ ← 14px 锁定图标
│ 🔇 │ ← 14px 静音图标
└────┘
```

| 属性 | 值 |
|------|-----|
| 宽度 | 120px |
| 背景 | `var(--color-surface-dim)` |
| 边框 (右) | `1px solid var(--color-outline-variant)` |
| 图标 | Material Symbol, 18px |
| 轨道名 | 可双击编辑 |

---

## 12 暗色主题规范

### 12.1 暗色主题基准

ClipWright **仅支持暗色主题**。这是 Premiere 和所有专业视频编辑工具的行业标准——亮色界面会在长时间编辑中造成视觉疲劳，并干扰对视频色彩的精确判断。

基准暗色表面: `#0E101A`

```css
:root[data-theme="dark"] {
  color-scheme: dark;
}
```

### 12.2 暗色模式下特殊规则

| 场景 | 规范 |
|------|------|
| 预览窗口 | **始终纯黑** (#000000)，不受主题影响 |
| 取色器 | 浮动取色器面板使用亮色表面 (`#FDFBFF`) 以便精确分辨颜色 |
| 波形图 | 使用 `--cw-track-audio` 通道色，不受表面色影响 |
| 代码块 | `#0A0A10` 背景，保持代码高对比度 |
| 滚动条 | 始终 `rgba(255,255,255,0.15)`，hover `rgba(255,255,255,0.25)` |
| 选中文字 | `--color-primary` 50% opacity 背景 |

### 12.3 滚动条规范

```css
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.25);
}
::-webkit-scrollbar-corner {
  background: transparent;
}
```

---

## 13 响应式与适配

### 13.1 断点策略

如 9.4 所述，ClipWright 以 Desktop 为第一优先级。

| 断点 | 策略 |
|------|------|
| ≥1440px | **完整功能**。所有面板可见，可拖拽调整 |
| 1024-1439px | **压缩模式**。Agent 面板默认折叠，时间轴缩放级别自动降低 |
| 768-1023px | **单面板模式**。一次只显示一个主面板，Tab 切换 |
| <768px | **只读模式**。可预览时间轴和视频，不支持编辑 |

### 13.2 高 DPI 适配

- 所有图标使用 SVG/Material Symbols（矢量，自适应 DPI）
- Canvas 缩略图使用 `devicePixelRatio` 动态调整分辨率
- 1px 边框在 Retina 屏幕上使用 `0.5px` 或 `transform: scaleY(0.5)`

---

## 14 无障碍设计

### 14.1 标准

| 标准 | 级别 |
|------|:----:|
| WCAG 2.1 | AA |
| 键盘导航 | 完整支持 |
| 屏幕阅读器 | 关键路径支持（非实时编辑操作） |

### 14.2 键盘优先原则

ClipWright 继承 Premiere 的键盘优先设计哲学：
- 所有编辑操作必须有键盘快捷键
- 快捷键可自定义（`Ctrl+/` 查看速查表）
- 所有交互元素可键盘聚焦（`:focus-visible` 样式）

### 14.3 焦点指示器

```css
:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
  border-radius: 2px;
}
```

### 14.4 对比度

详见 2.3 节。所有文字-背景组合必须在 CI 中通过自动对比度检查。

---

## 15 附录

### A. CSS 变量完整清单

```css
:root {
  /* ═══════════════════════════════════════════════
     Material You 色彩 (由 Monet 动态生成)
     ═══════════════════════════════════════════════ */
  --color-surface: #0E101A;
  --color-surface-dim: #11131D;
  --color-surface-bright: #1A1D29;
  --color-surface-container: #161824;
  --color-surface-container-low: #12141E;
  --color-surface-container-high: #1C1F2C;
  --color-on-surface: #E2E2EC;
  --color-on-surface-variant: #C4C4D0;
  --color-primary: #B4C5FF;
  --color-on-primary: #001C55;
  --color-primary-container: #1A3074;
  --color-on-primary-container: #DBE2FF;
  --color-secondary: #BBBCD0;
  --color-secondary-container: #3B3C50;
  --color-tertiary: #D1708E;
  --color-error: #FFB4AB;
  --color-outline: #8D8D99;
  --color-outline-variant: #46464F;

  /* ═══════════════════════════════════════════════
     ClipWright 扩展色彩
     ═══════════════════════════════════════════════ */
  --cw-track-video: #4F8CFF;
  --cw-track-audio: #34D399;
  --cw-track-text: #FBBF24;
  --cw-track-image: #A855F7;
  --cw-track-caption: #F59E0B;
  --cw-track-animation: #FF6B6B;
  --cw-playhead: #FF4444;
  --cw-marker: #FFD700;
  --cw-snap-guide: #00E5FF;
  --cw-selection: #4F8CFF55;
  --cw-ruler-bg: #1A1D29;
  --cw-ruler-tick: #46464F;
  --cw-grid: #2A2A3A;
  --cw-keyframe-dot: #FBBF24;
  --cw-keyframe-selected: #4F8CFF;
  --cw-agent-bubble: #1A3074;

  /* ═══════════════════════════════════════════════
     字体
     ═══════════════════════════════════════════════ */
  --cw-font-mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  --cw-font-sans: 'Inter', 'Noto Sans SC', -apple-system, 'Segoe UI', sans-serif;
  --cw-font-display: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  --cw-font-code: 'JetBrains Mono', 'Fira Code', monospace;

  /* ═══════════════════════════════════════════════
     字体大小
     ═══════════════════════════════════════════════ */
  --cw-text-display: 36px;
  --cw-text-headline: 24px;
  --cw-text-title: 20px;
  --cw-text-title-sm: 16px;
  --cw-text-body: 14px;
  --cw-text-body-sm: 12px;
  --cw-text-label: 11px;
  --cw-text-label-sm: 10px;
  --cw-text-caption: 9px;
  --cw-text-mono: 12px;

  /* ═══════════════════════════════════════════════
     间距
     ═══════════════════════════════════════════════ */
  --cw-space-2xs: 2px;
  --cw-space-xs: 4px;
  --cw-space-sm: 8px;
  --cw-space-md: 12px;
  --cw-space-lg: 16px;
  --cw-space-xl: 24px;
  --cw-space-2xl: 32px;

  /* ═══════════════════════════════════════════════
     形状
     ═══════════════════════════════════════════════ */
  --cw-shape-none: 0px;
  --cw-shape-xs: 4px;
  --cw-shape-sm: 8px;
  --cw-shape-md: 12px;
  --cw-shape-lg: 16px;
  --cw-shape-full: 9999px;

  /* ═══════════════════════════════════════════════
     阴影 / 层级
     ═══════════════════════════════════════════════ */
  --cw-elevation-1: 0 1px 2px rgba(0,0,0,0.3), 0 1px 3px 1px rgba(0,0,0,0.15);
  --cw-elevation-2: 0 1px 2px rgba(0,0,0,0.3), 0 2px 6px 2px rgba(0,0,0,0.15);
  --cw-elevation-3: 0 4px 8px 3px rgba(0,0,0,0.15), 0 1px 3px rgba(0,0,0,0.3);
  --cw-elevation-4: 0 6px 10px 4px rgba(0,0,0,0.15), 0 2px 3px rgba(0,0,0,0.3);
  --cw-elevation-5: 0 8px 12px 6px rgba(0,0,0,0.15), 0 4px 4px rgba(0,0,0,0.3);

  /* ═══════════════════════════════════════════════
     动效
     ═══════════════════════════════════════════════ */
  --cw-easing-standard: cubic-bezier(0.2, 0.0, 0.0, 1.0);
  --cw-easing-standard-decelerate: cubic-bezier(0.0, 0.0, 0.0, 1.0);
  --cw-easing-standard-accelerate: cubic-bezier(0.3, 0.0, 1.0, 1.0);
  --cw-easing-emphasized: cubic-bezier(0.05, 0.7, 0.1, 1.0);
  --cw-easing-legacy: cubic-bezier(0.4, 0.0, 0.2, 1.0);

  --cw-duration-short1: 50ms;
  --cw-duration-short2: 100ms;
  --cw-duration-short3: 150ms;
  --cw-duration-short4: 200ms;
  --cw-duration-medium1: 250ms;
  --cw-duration-medium2: 300ms;
  --cw-duration-medium3: 350ms;
  --cw-duration-long1: 400ms;
  --cw-duration-long2: 450ms;
  --cw-duration-long3: 500ms;

  /* ═══════════════════════════════════════════════
     Canvas 布局
     ═══════════════════════════════════════════════ */
  --cw-track-height: 48px;
  --cw-track-gap: 2px;
  --cw-track-header-width: 120px;
  --cw-ruler-height: 24px;
  --cw-clip-min-width: 4px;
  --cw-clip-radius: 3px;
  --cw-keyframe-dot-size: 10px;
  --cw-playhead-width: 2px;
}
```

### B. Tailwind 配置映射

```javascript
// tailwind.config.ts
export default {
  theme: {
    extend: {
      colors: {
        surface: 'var(--color-surface)',
        'surface-dim': 'var(--color-surface-dim)',
        'surface-container': 'var(--color-surface-container)',
        'surface-container-high': 'var(--color-surface-container-high)',
        'on-surface': 'var(--color-on-surface)',
        'on-surface-variant': 'var(--color-on-surface-variant)',
        primary: 'var(--color-primary)',
        'on-primary': 'var(--color-on-primary)',
        'primary-container': 'var(--color-primary-container)',
        secondary: 'var(--color-secondary)',
        tertiary: 'var(--color-tertiary)',
        error: 'var(--color-error)',
        outline: 'var(--color-outline)',
        'outline-variant': 'var(--color-outline-variant)',
        // ClipWright tracks
        'track-video': '#4F8CFF',
        'track-audio': '#34D399',
        'track-text': '#FBBF24',
        'track-image': '#A855F7',
        'track-caption': '#F59E0B',
        'track-animation': '#FF6B6B',
        playhead: '#FF4444',
        marker: '#FFD700',
      },
      borderRadius: {
        'cw-xs': '4px',
        'cw-sm': '8px',
        'cw-md': '12px',
        'cw-lg': '16px',
        'cw-full': '9999px',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'SF Mono', 'Consolas', 'monospace'],
        sans: ['Inter', 'Noto Sans SC', 'system-ui', 'sans-serif'],
        display: ['Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
      },
      fontSize: {
        'cw-body': ['14px', '20px'],
        'cw-body-sm': ['12px', '16px'],
        'cw-label': ['11px', '16px'],
        'cw-label-sm': ['10px', '14px'],
        'cw-caption': ['9px', '12px'],
        'cw-mono': ['12px', '18px'],
      },
      spacing: {
        'cw-2xs': '2px',
        'cw-xs': '4px',
        'cw-sm': '8px',
        'cw-md': '12px',
        'cw-lg': '16px',
        'cw-xl': '24px',
        'cw-2xl': '32px',
      },
      transitionDuration: {
        'cw-short1': '50ms',
        'cw-short3': '150ms',
        'cw-short4': '200ms',
        'cw-medium1': '250ms',
        'cw-medium2': '300ms',
        'cw-medium3': '350ms',
        'cw-long1': '400ms',
        'cw-long2': '450ms',
        'cw-long3': '500ms',
      },
    },
  },
};
```

---

> **Design Authority**: This specification overrides any framework defaults. All components must reference CSS custom properties (design tokens) rather than hardcoded values.
>
> **Reference**:
> - Material Design 3: https://m3.material.io/
> - Adobe Premiere Pro UI patterns (proprietary, observed)
> - WCAG 2.1: https://www.w3.org/TR/WCAG21/
