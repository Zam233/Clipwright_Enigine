# 帧艺 ClipWright 文档

## 架构设计（内容视频编排引擎）

| 文档 | 说明 |
|------|------|
| [架构总览](structure.md) | 五层架构设计：原子能力层 → Agent 编排层 → 类型插件层 → Persona 配置层 → 用户接口层 |
| [Persona 系统](Persona.md) | 四层复合数字人格体系：参数层 / 示例层 / 嵌入层 / 模型层 |
| [Agent 工作流](workflow.md) | 7 Agent LangGraph 管线：Persona 解码 → 结构 → 素材 → 剪辑 → 动画 → 音效 → 质检 |

## 使用指南

| 文档 | 说明 |
|------|------|
| [快速开始](quickstart.md) | 安装、配置、启动、快速验证 |
| [API 参考](api_reference.md) | 全部 REST API 端点说明和示例 |
| [PersonaForge 使用说明](persona_forge.md) | Persona 智能构建器的四种模式和用法 |

## 开发

| 文档 | 说明 |
|------|------|
| [开发指南](development.md) | 技术栈、开发规范、设计约束 |
| [插件作者指南](plugin_authoring.md) | 新契约 `contributions()`：16 类扩展点、UI 槽位、Provider 源与迁移清单 |
| [安全部署指南](security.md) | API 令牌认证、CORS、文件白名单与生产部署清单 |

## 新增模块

| 文档 | 说明 |
|------|------|
| [素材系统](material_system.md) | 多源素材搜索与检索系统 |
| [语音与 TTS](voice_tts.md) | 声音克隆、语音合成与配音 |
| [动画系统](animation_system.md) | 动画编目、渲染管线与 MG 动画 |
| [服务概览](services_overview.md) | 全部后端服务层模块说明 |
| [需求分析 Agent](requirements_agent.md) | Requirements Agent 设计与职责 |
| [整数时间基座](timebase.md) | 整数 tick + 有理数帧率：换算纪律、时间码、漂移校正与切点的接线点 |
| [素材内容理解与 NL 落位计划](content-understanding-plan-2026-09-14.md) | 内容索引（镜头级）→ 检索 → NL 意图 → 落位校验：让 Agent 知道在剪什么、用户说人话就能达到目的 |
| [调度中枢方案（v1.4/v1.5）](supervisor-dev-plan-2026-10-17.md) | 确定性骨架 + 调度层：账本 / 归因 / 策略阶梯 / 子代理 / 执行网关 |
| [**全 Agent 化 PRD（路线 C，草案）**](agent-native-prd-2026-10-17.md) | 主 agent 直接编排工具与技能，6 阶段管线降级为它可调用的能力；含代价、分期 C0–C7、验收 A1–A13、8 个待决问题 |
| [**MV/PV 制作能力与类型规范 PRD（合并版 v1.2）**](mv-pv-prd-2026-10-18.md) | 单一真相：Part I 类型插件提示词化（10 槽位 + `param_rules` + `signature_traits` + `plan_schema`，提示词定义类型、参数只调剂量，含决策权矩阵与 D1–D22）；Part II MV/PV 能力（场景脚本 + 三构件库 + 版画库 + 歌词卡点 + 三道闸 + 素材识别落位 + 需求 agent 动态规划 + 7 子 agent，M0–M8，V1–V60）；**Part III 覆盖审计：20 类 MV/PV 逐类判定（能 18 / 不能 2）+ 三条边界 + 可选能力线成本** |
| [**MV/PV 能力开发计划**](mv-pv-dev-plan-2026-10-18.md) | 据 PRD v1.2 拆的 WBS：3 个 Spike → 轨道 R（渲染 26.5）/ A（Agent 与规划 20）/ S（素材 5.5–6）/ F（前端 8）+ 内容 6 人日；四道门与 Kill 规则、10–11 周周计划、单写者规则、DoD 与新增 CI 门 |
| [**验收覆盖表（V1–V60）**](coverage.md) | 逐条列出 60 项验收的当前状态（通过/部分/未做/人工）、证据位置与差距；含复跑命令清单与"只能由人判定"的清单 |
| [**全量验收报告（V1–V60）**](acceptance-report-2026-10-18.md) | 计划 W9 的交付物：一页纸结论（49 通过 / 4 部分 / 3 未做 / 4 人工）、四项"部分"卡在哪与谁能解、复跑命令全集、历史修复记录、下一步建议 |

| [内容理解基准集与指标](content-understanding-benchmark.md) | R1–R10 的基准集构成、量化指标（含门槛 vs 实测）、**每个数字的验证边界**；一键复跑 `scripts/verify_all_metrics.py` |
