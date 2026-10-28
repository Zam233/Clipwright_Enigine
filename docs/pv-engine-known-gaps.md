# PV 引擎已知缺陷与契约陷阱（2026-10-23）

> 配套文档：[pv-engine-plugin.md](pv-engine-plugin.md)（插件能力）、[subtitle-bug-report-2026-10-23.md](subtitle-bug-report-2026-10-23.md)（完整 24 条）。
> 本文件是 `pv_engine_doctor` 返回的 `known_gaps` 的人工版——**插件不修引擎**，只如实披露。

## 一、文档承诺、代码没有（5 条）

| 编号 | 文档怎么说 | 代码事实 | 影响 |
|---|---|---|---|
| D1 | `layout: fill_low` 合法（README:101、SKILL:60） | `LAYOUTS` 无此键（`engine/block.py:16-32`），静默回退 `center` | 竖屏字幕写 `fill_low` → 跑到画面正中间 |
| D2 | `kind: vertical` 是文字动效之一（SKILL:61） | 竖排由 `cue.vertical: true` 触发（`block.py:101`），写 `kind` 无效 | 竖排字幕不出竖排 |
| D3 | `glyph: "你×1.2↑4"` 字符串（README:107） | `_chars` 只吃 **dict 列表**；`typespec.parse_glyphs` 未接线 | 写字符串抛异常。**本插件已在落盘前代做转换** |
| D4 | `trans_in` 支持 flash/streak/whip/tint | `expand_shot` 不拷贝 `trans_in`/`tr_out` → 项目文件不可达 | 文档承诺的转场永不出现 |
| D5 | 30+ 条审计规则（README:158-172） | `engine/` 版只有 **R01–R11**（`audit.py:34-118`） | 「30 条全过」的预期不成立 |

## 二、三个"看着能用、实际是坑"的写法

| 写法 | 为什么是坑 | 判定依据 |
|---|---|---|
| 写 `raw cam dict`（带 `keys`/`beats`）但不写 `handheld/aperture/max_blur` | 落到 `Cam.__init__` 默认 `1.0/3.2/6.0`，**不是预设的 `0.35/1.6/4.0`** → 抖到不能看、景深糊一倍 | `camera.py:55-57` vs `project.py:54-60` |
| `post: {"glitch": 0.8}` 与 `shot.glitch: [0.2,0.9]` | 同名不同义：前者是后期 0–1 倍率，后者是两元素故障起止 | `scene.py:335` vs `scene.py:337` |
| `layers` 直写时用 `map`/`texture` | 代码支持 **7 种** `t`（README 只写 5 种），字段与 `figure/prop` 完全不同 | `puppet.py:208-265,390-429` |

## 三、字幕/文字层缺陷（与字幕报告同源）

| 编号 | 一句话 | 位置 |
|---|---|---|
| S01 | 标点让逐字时间（`word_times`）整体错位一个字 | `block.py:213` vs `project.py:81-88` |
| S02 | 英文空格被 `strip_punct` 删掉（`hello world` → `helloworld`） | `block.py:186-192` |
| S03 | 没有按像素换行，长句只缩到 36px 下限 | `block.py:248-257` |
| S04 | 审计的 px 与真实渲染 px 不是同一个数（假 WARN/漏报） | `audit.py:26-31` vs `block.py:217-228` |
| S06 | 竖排行距不与字宽取 max，宽字重叠 | `block.py:588-600` |
| S08 | 仅 WARN 时退出码 0；插件已改为 `pass_with_warnings` | `audit.py:113-118` |

## 四、死代码 / 不可达字段

- `trans_in`（`trans.py`）与 `tr_out`（`frames.py:160`）：`expand_shot` 不拷贝 → 项目文件不可达；
- `motion` 字段（`motion.py:25` 的 11 个程序）只在 legacy 无 layer 路径读取；
- legacy shot 字段 `beam/ghost/fg/cues/credits/plate_alpha/tear/grain` 同样不可达；
- `block._band_scrim`（`block.py:195-209`）全仓无调用；
- `squash` 在 `block.py:260-269` 已 return，后续 `476/503/521-530` 分支永不执行；
- `typespec.ACTIONS` 26 个动作码里只有 8 个在 `_chars` 有实现
  （slam/stamp/land/lift/wipe/tremble/cut/burn）。

## 五、插件侧的处理方式

| 缺陷 | 插件怎么应对 |
|---|---|
| D1/D2/D3 | `doctor()` 只反射代码；`glyph` 字符串在 `timeline_to_project` 里先转 dict 列表 |
| D4/死字段 | `doctor()` 的 `dead_fields` 区块显式标 `unreachable`，避免 Agent 反复尝试 |
| S01/S03/S04 | `timeline_to_project` 返回 `warnings`；`audit` 输出标 `px_source: "declared"`；**长句/竖屏仍建议走 ASS** |
| S08 | `verdict` 增加 `pass_with_warnings`，不再把"只有警告"显示成通过 |
