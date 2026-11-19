# 动画 Prompt Skill 化 MVP 验证记录

- 分支：`feat_zam_animation_improvements`
- 日期：2026-11-18
- 对照 PRD：`docs/animation-agent-prompt-skill-prd.md`

## 1. 提示词体积

| 项 | 字符数 |
|---|---:|
| 旧 `MG_DESIGNER_SYSTEM` | 13,702 |
| 新模块化 prompt（同一示例） | 3,522 |
| 降幅 | 约 74% |

新 prompt 包含：

- 角色/输出契约；
- 全片 motion contract；
- 本镜头 plan（category/shot_type/current/carrier/budget/rule_ids）；
- 2–3 条 atomic rules；
- Persona/category 视觉令牌；
- renderer 能力边界；
- 10 条自检；
- 一个最小正例；
- 既有编排约束 + 严格 JSON 输出。

## 2. 计划与规则

- `AnimationPlan` 包含 category/intent/energy/current/budget/rule_ids/carrier/shots/seams；
- `build_timeline_plan` 从 clips 的起始时间构建多镜头 shots/seams；
- `AnimationAgent` 在动画作业 gather 前构建全局 plan，并按 `clip_id` 传入各镜头；
- `validate_animation_plan` 校验 current、budget、规则数量与未知 rule_id；
- 每次镜头 prompt 只选 2–3 条规则。

## 3. Critique

模块化 critique 注入：

- seam；
- carrier；
- idle wobble；
- hold；
- transition budget；
- 一镜一主角；
- 无因方向翻转。

## 4. 测试

```text
MG/Prompt 定向：151 passed
AnimationAgent 定向：68 passed
计划/模块化专项：9 passed
CI scoped（MotionSpec/TextMotion API）：20 passed
ruff 错误类：通过
prompt lint：0 error
```

## 5. 回退

`prompt.modular: false` 时 `_design_system_template` 回到旧 `MG_DESIGNER_SYSTEM`；
`MG_DESIGNER_SYSTEM` 常量未删除，现有覆盖/插件机制保持。

## 6. 后续

- 视觉抽帧评审；
- A/B 指标与灰度；
- 规则库按 category 扩展；
- 把 seam 计划进一步映射到时间线 transition 字段。
