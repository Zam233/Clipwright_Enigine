# 关键词强调（emphasis_keyword）

## Intent

把旁白里最关键的那一个词/短语砸在画面上，其余信息让位。它是"视觉重音"，不是字幕的替代品。

## Semantic constraints

- 只允许 1 个主文本元素，可选 1 条装饰线；元素数 ≤3，装饰不计入"第二主体"
- 文本取自旁白的**摘要**，≤10 字，禁止与字幕逐字重复
- 锚点 center 或 upper；`position=bottom` 一律禁止（会压字幕带）
- 数字/时间/专有名词优先原样保留，不改写、不补造

## Composition

整屏只服务一个词：词居中偏上（画面高度 38–46% 处，y≈420–500px），左右各留 12% 安全边；
可选一条 2px 强调线压在词下 8px；背景保持实拍可见，不铺满不透明底色。

## Motion

阶段表（片段 1.4s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 入场 | 0 | 0.35 | 主文本 | opacity 0→1、scale 0.88→1.0（ease-out-back） |
| 落定 | 0.35 | 0.70 | 主文本 | scale 1.0→1.04→1.0（ease-out-cubic，只弹一次） |
| 静止 | 0.70 | 1.4 | 主文本 | 无变化（读词时间 ≥0.7s） |

弹跳幅度 ≤4% 且只发生一次；easing 只能取 `ease-out-back`（保守可退 `ease-out-cubic`），
不要写 `back-out`／`bounce`——那是 MG 名字，drawtext 的 FFmpeg 映射不认。
关键帧覆盖 ≥60% 片段时长；末段 0.6s 完全静止。

## Anti-patterns

- 把整句旁白原样搬上来（那是字幕的职责）
- 同时出现两个以上强调词
- 持续抖动/无限循环
- 不透明全幅背景遮住实拍
- 写 drawtext 做不到的效果：粒子、模糊、独立卡片层——这些一律标注「MG HTML 路径」或不写

## Example

```json
{
  "animation_id": "emphasis_keyword",
  "width": 1920,
  "height": 1080,
  "duration_sec": 1.4,
  "elements": [
    {
      "id": "kw",
      "type": "text",
      "content": "没有监管要求",
      "position": "center",
      "x": 960,
      "y": 420,
      "props": {
        "font_size": 96,
        "color": "#F5F7FA",
        "font_weight": 700
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "scale": 0.88,
          "easing": "back-out"
        },
        {
          "time": 0.35,
          "opacity": 1,
          "scale": 1.0,
          "easing": "ease-out"
        },
        {
          "time": 0.55,
          "opacity": 1,
          "scale": 1.04,
          "easing": "ease-out"
        },
        {
          "time": 0.7,
          "opacity": 1,
          "scale": 1.0,
          "easing": "linear"
        },
        {
          "time": 1.3,
          "opacity": 1,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "rule",
      "type": "line",
      "content": "",
      "position": "center",
      "x": 960,
      "y": 520,
      "props": {
        "width": 520,
        "height": 2,
        "color": "#E4572E"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_x": -260
        },
        {
          "time": 0.45,
          "opacity": 1,
          "translate_x": 0,
          "easing": "ease-out"
        },
        {
          "time": 1.3,
          "opacity": 1,
          "translate_x": 0
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S3, S12]

主画风 S3 极简几何（`prompts/styles/S3.md`）：单一主体、细线、短促平移、强对比；
备选 S12 电影字幕标题（克制排版）。只用所选模块的配色与线宽，不引入参考图里的文案与数据。

## Constants

| 常量 | 值 |
|---|---|
| 画布 | 1920 x 1080（任何分辨率按 `font_size * width / 1920` 缩放） |
| 字幕带 | 底部 160px；元素底边不得超过 y=920px |
| 单行字数上限 | ≤14 字 |
| 元素数上限 | ≤3（含装饰） |
| 片段时长 | min_duration=0.8，max_duration=8.0；关键帧 ≤ 实际 duration_sec |
| 安全区 | x=8%–92%，y=8%–85% |

## Output

只输出一个 JSON 对象（`animation_id` + `elements`，元素字段 `id/type/content/position/x/y/props/keyframes`）。
关键帧为 `{"time": 秒, <属性>: 值, ...}` 的升序数组，每个元素至少 2 帧；不加围栏、不加注释、不写"其余同上"。

## Self-check

- [ ] 关键帧 time 全部落在 0–duration_sec，且严格升序。
- [ ] 元素数 ≤3，单行 ≤14 字，底边 ≤920px，不压字幕带。
- [ ] 主体至少一个属性真在变（opacity/scale/translate 之一），不是只加装饰。
- [ ] 末段静止 ≥0.6s，动画在 1.2s 内完成。
- [ ] 每个事实值都能追到旁白/原文；同输入两次生成的关键帧时间完全一致。

## Fallback

1. 解析失败按裸 JSON → ```json 围栏 → 首个 `{` 至末个 `}` 依次重试。
2. 结构问题交给 `_normalize`：时长钳制、关键帧夹取到片段内、元素截断到 3 个、文字键收敛到 `content`。
3. 校验（`validate_text_def`）失败最多重试 2 次；仍失败返回失败并走模板/降级 kind。
4. 文本超长先语义摘要，禁止静默截断；与字幕带冲突时先移 upper/center，再减元素。
5. 降级必须记 type_id、prompt_version、失败字段与 fallback kind；不得把回退说成生成成功。
