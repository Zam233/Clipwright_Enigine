# 左右对照（comparison）

## Intent

把两个对象放在**同一个维度**上比，用位置和明度表达差异，而不是用形容词。

## Semantic constraints

- 必须声明 `comparison_dimension`（比如"启动耗时"）；没有共同维度就不标 VS，改并列卡片
- 左右各含 `label`、`value`、`source_ref`；数值必须同单位
- 每侧补充词 ≤1 个，且来自原文
- 两侧维度不一致（如"价格 vs 手感"）→ 不允许输出比较结论

## Composition

左右同构：左区 x=8%–46%、右区 x=54%–92%；中缝分隔线 2px；两侧标题同在 y≈24%；
数值同字号；底边 ≤920px；配色 2 色 + 1 强调，不要用第三种主色。

## Motion

阶段表（片段 3.0s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 左侧 | 0 | 0.4 | left | opacity 0→1、translate_x -12→0 |
| 右侧 | 0.2 | 0.6 | right | opacity 0→1、translate_x 12→0 |
| 分隔线 | 0.4 | 0.7 | divider | scale 纵向 0→1 |
| 静止 | 0.7 | 3.0 | 全部 | 无变化 |

两侧入场差 0.2–0.4s；不使用弹跳；渐变背景属于 MG HTML 能力（mg_fastpath 不支持时降级纯色）。

## Anti-patterns

- 把「对比 A vs B，渐变背景 + 逐元素入场」当数据输入（那是设计指令）
- 跨维度比较后写"明显更好"
- 只换颜色不表意
- 一侧三个补充词、另一侧一个（不对等）

## Example

```json
{
  "animation_id": "comparison",
  "width": 1920,
  "height": 1080,
  "duration_sec": 3.0,
  "elements": [
    {
      "id": "l",
      "type": "text",
      "content": "A 方案\\n启动 1.2s",
      "position": "center",
      "x": 460,
      "y": 420,
      "props": {
        "font_size": 64,
        "color": "#4CC9F0"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_x": -12,
          "easing": "ease-out"
        },
        {
          "time": 0.4,
          "opacity": 1,
          "translate_x": 0,
          "easing": "linear"
        },
        {
          "time": 2.8,
          "opacity": 1,
          "translate_x": 0
        }
      ]
    },
    {
      "id": "d",
      "type": "line",
      "content": "",
      "position": "center",
      "x": 960,
      "y": 460,
      "props": {
        "width": 2,
        "height": 420,
        "color": "#F4A261"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0
        },
        {
          "time": 0.4,
          "opacity": 0
        },
        {
          "time": 0.7,
          "opacity": 1
        },
        {
          "time": 2.8,
          "opacity": 1
        }
      ]
    },
    {
      "id": "r",
      "type": "text",
      "content": "B 方案\\n启动 0.8s",
      "position": "center",
      "x": 1460,
      "y": 420,
      "props": {
        "font_size": 64,
        "color": "#F4A261"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_x": 12,
          "easing": "ease-out"
        },
        {
          "time": 0.2,
          "opacity": 0,
          "translate_x": 12,
          "easing": "ease-out"
        },
        {
          "time": 0.6,
          "opacity": 1,
          "translate_x": 0,
          "easing": "linear"
        },
        {
          "time": 2.8,
          "opacity": 1,
          "translate_x": 0
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S5, S3]

主画风 S5 分屏对照（`prompts/styles/S5.md`）：左右同构、分隔线、明度表意；
备选 S3 极简几何。渐变需 MG HTML，mg_fastpath 降级纯色。

## Constants

| 常量 | 值 |
|---|---|
| 画布 | 1920 x 1080（默认；其他分辨率按比例换算） |
| 字幕带 | 底部 160px；所有图元底边 ≤920px |
| 节点/柱/点上限 | ≤6（可读性优先，超过就拆片段） |
| 片段时长 | min_duration=1.5，max_duration=12.0 |
| 安全区 | x=8%–92%，y=8%–85% |

## Output

只输出一个 JSON 对象：`animation_id` + `elements`（每个元素 `id/type/content/position/x/y/props/keyframes`）；
关键帧为升序 `{"time": 秒, <属性>: 值, ...}`，每元素至少 2 帧。禁止注释、围栏、"其余同上"。

## Self-check

- [ ] 每个数值/标签都能追到 `source_ref`；没有来源的不画。
- [ ] 元素数、坐标都在安全区内，底边 ≤920px，不压字幕带。
- [ ] 关键帧 time 在 0–duration_sec 内且升序；末段静止 ≥0.6s。
- [ ] 同输入两次生成的结构与时间点一致（无随机）。
- [ ] 只用了 MG validator 支持的属性与 easing（linear / ease / ease-in / ease-out / ease-in-out / back-out / elastic-out / bounce）。
## Fallback

1. 解析失败按裸 JSON → ```json 围栏 → 首个 `{` 至末个 `}` 依次重试。
2. `_normalize` 负责结构收敛（时长钳制、关键帧夹取、元素截断到 6）；归一化不等于语义修复。
3. `validate_mg_json` 失败最多重试 2 次；仍失败返回失败并降级到模板 kind。
4. 数据点不足（<3 点 / 缺单位 / 缺来源）：不画数值图，改强调文字或并列卡片，并在结果里说明原因。
5. 降级必须记 type_id、prompt_version、失败字段、fallback kind；不得把回退描述成生成成功。
