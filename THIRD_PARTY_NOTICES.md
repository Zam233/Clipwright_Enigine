# 第三方软件与来源声明 / Third-Party Notices

> 本项目（Clipwright / 帧艺，含前端 Clipweight-Client）自身以 **MPL-2.0** 授权（见 `LICENSE`）。
> 本文件登记**运行时依赖**与**方法论来源**，供合规审计与再分发时随附。
> 最近更新：2026-09-13

## 0. 现状摘要（诚实声明）

- 本仓库当前**未内联任何第三方源代码**（没有复制 MIT/Apache-2.0 项目的源码文件）。
  对 HyperFrames 的使用是**外部进程调用**（`npx hyperframes ...`），不构成衍生作品。
- 下列「方法论来源」条目指的是**设计思路与规则的提炼**（提示词、校验规则、编排方式），
  不是源码复制；版权不保护思想，但仍在此登记以示尊重与可追溯。
- 一旦将来移植第三方代码（例如 video-use 的 `timeline_view.py`），**必须**：
  1. 在目标文件头部保留原版权与许可声明；
  2. 在下文「2. 已内联的第三方代码」中登记文件、来源、许可全文；
  3. 若为 Apache-2.0 组件，还需随附其 NOTICE 并注明是否修改。

## 1. 运行时依赖

| 组件 | 许可 | 使用方式 | 义务 |
|---|---|---|---|
| [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes)（CLI `hyperframes`，v0.8.36） | Apache-2.0（Copyright 2026 HeyGen, Inc.） | 通过子进程调用 `render/lint/check/snapshot` 渲染 HTML 合成为视频；不修改、不再分发其包 | 若随产品分发该包或其二进制，需附 Apache-2.0 全文与 NOTICE，并遵守商标条款（不得暗示 HeyGen 背书） |
| FFmpeg / ffprobe | LGPL-2.1+ 或 GPL-2.0+（取决于具体构建） | 视频渲染、探测、合成 | 分发可执行体时确认所用构建的许可与对应源码可得性；GPL 构建会带来 GPL 义务 |
| Chrome / chrome-headless-shell | 专有（可自由再分发用于无头渲染） | HyperFrames 抓帧 | 仅作为本机运行时依赖使用，不随产品再分发 |
| Python / Node.js 运行时的第三方库（FastAPI、Pydantic、motor、Pillow、React、Vite 等） | 以各自 `pyproject.toml` / `package.json` 声明为准（绝大多数为 MIT / BSD / Apache-2.0） | 常规库依赖 | 分发时按各自许可保留声明；本文件不逐条复制依赖清单，需要时用 `pip-licenses` / `license-checker` 生成 |

## 2. 已内联的第三方代码

**（当前为空）**

格式约定（将来登记用）：

```
### <文件路径>
来源：<仓库 / 版本 / commit>
许可：<MIT / Apache-2.0 / …>（全文附于本文件末尾）
修改：<无 / 简述改动>
```

## 3. 方法论来源（非源码复制，仅思路与规则提炼）

| 来源 | 许可 | 采纳内容 | 落地位置 |
|---|---|---|---|
| [browser-use/video-use](https://github.com/browser-use/video-use)（Copyright (c) 2026 Browser Use） | MIT | 生产正确性硬规则（字幕最后合成、30ms 切点淡化、overlay PTS 平移、切点落字边界等）；「每动画一个并行单元 + 自包含 brief」的编排范式；渲染后视觉自评闭环的设计；MG 通用质量指导（透明度分层、同屏元素上限、节奏变奏、末帧 hold） | `docs/opencut-video-use-hyperframes-adoption-2026-09-13.md`、`animation/mg/config.yaml`（提示词「通用质量红线」）、`animation/mg/knowledge/motion_recipes.yaml`（引语卡片/时长节奏） |
| [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes) 的 agent skills 文档 | Apache-2.0 | 合成 HTML 契约（`data-*` 时间属性、`class="clip"`、`window.__timelines` 注册、确定性渲染禁则）、WAAPI 可 seek 的 CSS 动画写法、`lint/check/snapshot` 门禁用法；动效配方库的主题与规则名（**结构提炼，非原文**） | `animation/hf_timeline.py`、`animation/hyperframes_renderer.py`、`docs/animation_system.md`、`animation/mg/knowledge/motion_recipes.yaml` |
| [OpenCut](https://github.com/opencut-app/opencut) / [opencut-classic](https://github.com/opencut-app/opencut-classic)（Copyright 2025-2026 OpenCut） | MIT | 数据模型与编辑器工程范式（整数时间 + 有理数帧率、轨道判别联合、命令式撤销、声明式参数/特效/遮罩契约、版本化存储迁移） | 见采纳报告 §1.2 / 深读报告 §1.2（**尚未移植代码**） |

> **动效配方库的合规声明**：`clipwright/animation/mg/knowledge/motion_recipes.yaml` 的 8 条配方
> 由本项目自行撰写，仅借鉴上游文档的**主题与规则名**，不含任何上游 HTML/CSS/JS 片段或整段文字；
> 每条 `source` 字段登记来源仓库/路径/许可，`tests/clipwright/test_mg_knowledge_pack.py`
> 对此做自动校验（许可白名单、distilled 标记、无代码片段特征）。

## 4. 商标

Apache-2.0 与 MIT 均不授予商标权。本项目文档中提及 "HyperFrames"、"OpenCut"、"video-use"
等名称仅用于说明技术依赖与方法论出处，不表示任何形式的隶属、赞助或背书。

---

## 附：许可全文

需要再分发时，请在此处附上对应组件的许可全文（Apache-2.0 / MIT / LGPL 等）。
模板见 `docs/opencut-video-use-hyperframes-adoption-2026-09-13.md` §2.3。
