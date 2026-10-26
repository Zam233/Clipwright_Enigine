# 字幕链路 BUG 探查报告（2026-10-23）

- 范围：`K:\video engine`（逐帧渲染引擎的文字层）+ `J:\Clipwright`（后端字幕链路）+ `J:\Clipweight-Client`（前端字幕/预览）
- 方法：读码 → 构造最小用例 → **真机执行**（ffmpeg 8.1.2 / Python 3.14）→ 只保留能复现的结论
- 结论：共确认 **27 条**（P1×12、P2×12、P3×3）。其中 S25–S27 为 2026-10-24 追加（上传配音链路）。**没有"字幕整体不可用"级故障，但有 4 条会直接产废片/废文件**（S12、S13、S15、S11）。

---

## 0. 最该先修的 5 条

| # | 一句话 | 后果 |
|---|---|---|
| S12 | ASS 多行字幕不转 `\N` | SRT 里只要有一句折行，`.ass` 文件结构就坏，**整批字幕失效** |
| S13 | Dialogue 行把 MarginV 写死成 10 | 竖屏 12% 安全区被覆盖，字幕贴底被平台 UI 挡（`ass_safe_margins` 白写了） |
| S15 | `subtitle_burn` 自写转义 | 文案带单引号 → **ffmpeg 进程崩溃**（0xC0000005）；带 `%` → 文本扩展报错 |
| S11 | SRT 毫秒不进位 | 导出 `00:00:59,1000`，第三方播放器/剪辑软件读成非法时间码 |
| S01 | 引擎标点导致逐字时间错位 | 歌里有任何标点，后半句的逐字卡点整体提前一个字 |

---

## A. 引擎侧（`K:\video engine`）：文字/字幕

### S01（P1）标点让 `word_times` 与真正渲染的字错位

**证据**：`project._cue` 用**原始文本长度**铺 `words`（`project.py:81-88`：`n = len(text)`），
而 `block._chars` 先 `strip_punct` 再按索引取时间（`block.py:213` + `block.py:390`：
`on = float(words[i]["t0"]) if i < len(words)`）。

**后果**：`"你好，世界"` 有 5 个字符、去标点后 4 个字，`words` 仍是 5 项 →
最后一个真正渲染的字取到 `words[3]`（标点的时间），`words[4]` **永不使用**。
即：**句中每出现一个标点，其后所有字的卡点整体前移一格**。竖排 `_vertical:605` 同病。
同时 `audit._last_word`（`audit.py:21-23`）取的是那个永不渲染的末项，R06「末字停留」量的是空气。

**复现**（实测输出）：
```
raw len: 5  stripped: 4  words: 5
words t0: [1.0, 1.4, 1.8, 2.2, 2.6]
最后一个真正渲染的字会用 words[3]=2.2 ；设计值 2.6；words[4] 永不使用
```
**修复**：`_cue` 里先 `strip_punct(text)` 再按去标点后的长度铺 `words`（标点不参与逐字卡点）；
或让 `_chars` 维护"原始索引 → 去标点索引"映射。

---

### S02（P1）空格与间隔号被删，英文/中英混排没有词距

**证据**：`strip_punct` 的正则包含 `\s`（含半角空格）与 `\u00b7`（`block.py:186-192`），
且 `_chars` 默认走它（`block.py:213`）。

**后果**：实测 `strip_punct("hello world") == "helloworld"` —— 英文歌词/双语字幕**词间空白消失**；
`·`（常见于「某某·某某」）也被吞掉。只有显式写 `raw_text: true` 才绕得开，而文档没提这个开关。

**修复**：CJK 之外的空白保留（只压连续空白为单空格）；`·` 从标点表移除，或在 `raw_text` 缺省时按"含拉丁字符"自动判定。

---

### S03（P1）没有按像素换行；长句只能一路缩到 36px 下限

**证据**：横向路径只有"整行夹进安全区就缩字号"（`block.py:248-257`，`k = usable/total`，下限 `max(36, ...)`），
`_fit_whole`（`block.py:818-822`）只对整句单 sprite 生效；全文无换行/折行逻辑。

**后果**：竖屏 1080 宽、30 字的字幕会被压到 36px（约画高 1.9%），远低于引擎自己 R04 的 5% 门槛，
而 R04 用的是**折行前**的字号，审计抓不到（见 S04）。字幕"看得见但读不了"。

**修复**：按 `px` 与 `SAFE` 计算每行容量，先折行再缩字号；折行结果参与 R04 校验。

---

### S04（P1）审计的"字号"和真正渲染的字号不是一个数

**证据**：`audit._px_of` 用 `len(c.get("text"))`（含标点）与 `band*W/n*0.66`（`audit.py:26-31`），
不看 `kind`，也不看 `block._chars` 里的字号下限（`block.py:226-228`：n≤5→128、n≤8→116、否则 104）
与 `kind=band` 的 `min(px, step*0.66)`（`block.py:222`）。

**后果**（实测 `examples/overlay`）：审计算出 **64px**（< min_px 96 → WARN），
实际渲染是 **116px**（≥96）——**假 WARN**；
反向也成立：长句被缩到 36px 时，审计仍按未缩的值算，**漏报**。

**修复**：把"算最终字号"抽成一个函数，渲染与审计共用；或在 cue 归一化时就把 `px` 落定写回。

---

### S05（P2）逐字时间被静默夹到 `t1-0.22`

**证据**：`block.py:393` `on = min(on, t1 - 0.22)`。

**后果**：`word_times` 排到片尾附近时，多个字被压成同一时刻同时出现（逐字卡点效果消失），
且没有任何审计项提示"你的时间被改写了"。短片（`t1-t0 < 0.22`）时 `on` 甚至早于 `t0`。

**修复**：夹取时记 WARN（新增 R12「逐字时间被裁剪」），或把夹取量写进 `doctor` 的契约说明。

---

### S06（P2）竖排行距不与字宽取 max，宽字会重叠

**证据**：横向路径有 `steps = [max(adv, sp.w) ...]`（`block.py:243`），
竖排路径直接用 `adv = int(px * (1.0 + track))`（`block.py:588-600`），从不与 `sp.w`/`sp.h` 取 max。

**后果**：竖排字号大、字面宽（如「國」「體」）或 `tracking` 为 0 时，相邻字竖直方向压字。
竖排字幕（`vertical: true`）的最常见形态。

**修复**：竖排 `adv = max(int(px*(1+track)), sp.h + 2)`。

---

### S07（P2）两条审计规则名不副实

**证据**：
- R07 叫「同镜 cue 不重叠」，判定却是 `c["t0"] < cs[j-1]["t1"] - 0.5`（`audit.py:85`）——**重叠 0.5s 以内一律放行**；
- R05 叫「文字不出画」，实际只查 `c["t0"]/c["t1"]` 是否越出镜头、`band>1`、post 越界（`audit.py:77-94`），
  **完全不查像素几何**；而 `_radial`/`_scan`/`_watermark`/`_echo` 都不做安全区夹取（`block.py:619-799`）。

**后果**：文字真出画（径向/残影/水印）时审计说 OK；cue 重叠 0.4s 时审计说 OK，而画面上是两行叠字。

**修复**：R07 去掉 0.5s 宽容（或把宽容写进规则名）；R05 增加"按布局算包围盒"的几何检查。

---

### S08（P2）只有 WARN 时退出码 0，插件一律判 `pass`

**证据**：`audit.check` 只在有 FAIL 时返回非 0（`audit.py:113-118`）；插件把退出码映射为 `verdict`（`plugins/pv_engine/main.py:134-139`）。
11 条规则里 **6 条是 WARN 级**（R03/R04/R06/R08/R09/R10）。

**后果**：用户看到"审计通过"，实际上"字号过小 + 末字停留不足 + 镜头不动"可以同时存在。

**修复**：插件侧按 `counts.WARN` 给出 `verdict=pass_with_warnings`（PRD F9 已同步）。

---

### S09（P3）`kind=band` 时 `px` 被覆盖两次

`block.py:222` `px = int(min(cue.get("px") or step*0.66, step*0.66))` —— 写大了被压；
紧接着 `block.py:226-228` 的下限又把它抬到 128/116/104。
于是"`px` 直接指定字号"（README）只对非 `band` 成立，且最终值由三个常量共同决定。

---

### S10（P3）死代码与未实现动作

- `_band_scrim`（`block.py:195-209`）**全仓无调用**（真正用的是 `scene.band_scrim`）；
- `squash` 在 `block.py:260-269` 已 `return`，后面 `476/503/521-530` 的分支永不执行；
- `typespec.ACTIONS` 26 个动作码里，`_chars` 只实现 8 个（slam/stamp/land/lift/wipe/tremble/cut/burn），
  `copy/behind/early/late/slow` 等解析后无效果。

---

## B. Clipwright 后端

### S11（P1）SRT 毫秒不进位 → `,1000`

**证据**：`services/subtitle.py:139` `ms = int(round((sec % 1) * 1000))`，与秒字段各自计算、不联动。

**实测**：
```
1.9996    -> 00:00:01,1000
59.9999   -> 00:00:59,1000
3599.9996 -> 00:59:59,1000
```
**后果**：`to_srt` 被 `/api/subtitle/export`、`/transcribe`、`/align` 三处共用（`api/subtitle.py:45,86,127`），
导出的 SRT 在 Premiere/播放器里时间码非法，常见表现是整条被丢弃或错位。

**修复**：`ass_time`（`design.py:139-145`）是同一系统里的正确写法——先 `int(round(sec*100))` 再 `divmod`。照抄即可。

---

### S12（P1）ASS 多行文本不转 `\N`，`.ass` 结构损坏

**证据**：`build_ass_dialogue` 只转义 `{`/`}`（`design.py:297`），`wrap_cjk_lines` 不处理换行（`design.py:102-136`）；
而 `parse_srt` 明确保留多行（`services/subtitle.py:63` `text = '\n'.join(lines[2:])`）。

**实测**：
```
build_ass_dialogue("第一行\n第二行", 1.0, 3.0)
-> '...\\an2}第一行\n第二行'          # Dialogue 行内裸换行
```
**后果**：ASS 是逐行解析的，`Dialogue:` 行被换行截断后，后半句变成一条无法解析的孤立行 →
libass 报错、该条字幕整条失效，严重时后续事件全乱。

**修复**：`build_ass_dialogue` 入口 `text.replace("\r\n","\n").replace("\n", r"\N")`；
`wrap_cjk_lines` 也应把已有 `\n` 当作强制断点。

---

### S13（P1）Dialogue 行写死 MarginV=10，竖屏安全边距被覆盖

**证据**：`build_ass_style` 按画幅算 `(MarginL, MarginR, MarginV)`（竖屏 `h*0.12`，`design.py:88-99,253`）；
但 `build_ass_dialogue` 每条都写 `Default,,0,0,{10+offset_y},,`（`design.py:306-308`），ASS 里 **Dialogue 级 margin 覆盖 Style 级**。

**实测**（1080×1920）：
```
Style    : ...,2,54,54,230,1        # MarginV=230（12% 安全区）
Dialogue : 0,0:00:01.00,0:00:03.00,Default,,0,0,10,,
```
**后果**：`ass_safe_margins` 专门为"竖屏字幕被平台 UI 遮挡"写的修复**全部失效**，字幕恒定贴在离底 10px。

**附加证据**：`tests/.../test_ass_style.py:104` 把 `Default,,0,0,10,` 写成了断言 —— 修这个 bug 必须同步改测试，
否则测试会"保护"缺陷。

**修复**：Dialogue 行不写 margin（留空继承 Style）；`offset_y` 改用 `\pos` 或 `\an` 锚点表达。

---

### S14（P1）`shadow_x/shadow_y` 在 ASS 路径被丢弃

**证据**：`design.py:284-285` 只要有任一非零就 `\shad1`，**从不写 `\xshad/\yshad`**；
对照 drawtext 路径 `design.py:343-344` 是写了 `shadowx/shadowy` 的。

**实测**：`TextStyle(shadow_x=5, shadow_y=5).build_ass_dialogue(...)` 的 override tags 只有 `['shad1']`。

**后果**：属性面板调阴影偏移，预览（canvas `shadowOffsetX/Y`）有位移，**成片阴影永远贴在字上**。
默认渲染器是 ASS（`render.py:665-677`），所以这是默认路径的问题。

**修复**：`\shad1` 旁补 `\xshad{n}\yshad{n}`（注意 ASS 的 `\shad` 与 `\xshad/\yshad` 不能同时表达的坑，建议只写 xshad/yshad）。

---

### S15（P1）`subtitle_burn` 自写转义：单引号让 ffmpeg 崩溃，`%` 截断文本

**证据**：`tool/subtitle.py:70` `safe_txt = txt.replace("'", "'\\''").replace(":", "\\:")`
—— 这是 **shell** 的转义习惯，不是 ffmpeg filtergraph 的；且未复用已校准的 `escape_drawtext_text`（`design.py:31`）。

**实测**（ffmpeg 8.1.2，带正确 `fontfile`）：
| 文案 | 结果 |
|---|---|
| `it's`（原始单引号） | 滤镜解析失败（`No option name near ...`） |
| `it'\''s`（本工具生成的串） | **ffmpeg 崩溃，退出码 0xC0000005**，stderr 只有 fontconfig 噪音 |
| `it’s`（`escape_drawtext_text` 的做法） | 正常 |
| `100% done` | `Stray % near ' done'` —— 文本扩展报错，`%` 之后不可预期 |
| `100\\% done`（`escape_drawtext_text` 的做法） | 正常 |
| `fontcolor=#ffffff` | **正常**（此条我最初怀疑，实测排除——不要按"hex 不合法"去改） |

**后果**：任何带英文撇号（`don't`）或百分号的字幕，走 `subtitle_burn` 就会失败或吞字；
且失败信息没有任何"原文含单引号"的提示，排查成本极高。

**修复**：改用 `escape_drawtext_text` + `color_to_drawtext`；补一条测试（`test_drawtext_escape.py` 已有对照样本）。

---

### S16（P1）时长四舍五入到 0 / 倒序时间码，整条导入报废

**证据**：`services/subtitle.py:100` `"duration_sec": round(seg.end_sec - seg.start_sec, 2)`；
`parse_srt` 不校验 `end >= start`（`services/subtitle.py:55-64`）。

**实测**：
```
segments_to_timeline_clips([SubtitleSegment(1, 1.000, 1.004, "x")])[0]["duration_sec"] -> 0.0
Clip(...) -> ValidationError: duration_sec Input should be greater than 0
```
倒序时间码（`00:00:02,000 --> 00:00:01,000`）→ `duration_sec = -1.0`，同样被 schema 拒绝。

**后果**：`/api/subtitle/import` 返回的是裸 dict（不校验），前端把它塞进时间线时**整条时间线 422**，
用户看到的是"保存失败"，而不是"第 3 条字幕时间码有问题"。

**修复**：`max(0.01, round(...,2))` 或保留 3 位小数；`parse_srt` 丢弃/钳制 `end<=start` 并返回告警列表。

---

### S17（P2）`text_align` 声明、可编辑、预览生效——成片零消费

**证据**：`schema/timeline.py:142` 声明；`services/subtitle.py:17,29` 写默认；前端 `PropertiesPanel` 有控件、
`captionLayout.ts:83-87` 预览按它偏 5%；但全仓 grep `text_align` **只有 schema/subtitle/edit_intent/intent_apply**，
`services/render.py` 与 `tool/design.py` 零引用。

**后果**：左/右对齐的字幕在预览里靠边，导出后**恒居中**。这是"预览/成片不一致"里最容易被告的一条。

**修复**：把 `text_align` 映射进 `position`（left→`\an4`+左 margin，right→`\an6`）或在 `doctor` 里标明"仅预览"。

---

### S18（P2）混合字重时 `bold` 丢失

**证据**：整条时间线只写一个 `Style:`（取第一条 overlay 的 `TextStyle`，`render.py:2466-2467`）；
`build_ass_dialogue` 的 override tags 里**没有 `\b`**（`design.py:268-296`）。
粗体只体现在 Style 行的 `Bold` 字段（`design.py:251-252`）。

**后果**：一条 normal + 一条 bold 的时间线，bold 那条**不粗**（除非它恰好是第一条）。

**修复**：`font_weight=="bold"` 时补 `\b1`（与已有 `\i1` 并列）。

---

### S19（P2）自动生成的字幕不做重叠消解

**证据**：`animation_agent._handle_caption`（`agents/animation_agent.py:401-429`）生成 caption 后直接 append；
同文件 text 路径（387-393）却调了 `_find_overlapping_clip` 去重。
另有 `stt.py:266-268` 把 start/end `round(...,2)`，相邻段可被舍入成同一时刻。

**后果**：旁白密集时两条 caption 同轨同时可见 → 屏幕上叠两行字。

**修复**：`_handle_caption` 复用 `_find_overlapping_clip`（或按 start 排序后裁剪前一条的 `t1`）。

---

### S20（P2）断行与打字机宽度：按字数、按硬编码 1920

**证据**：
- `wrap_cjk_lines` 固定 `_MAX_CJK_LINE_CHARS = 16`（`design.py:85`），与字号/画幅无关；
- `_split_long_text` 在 `≥ limit//2 = 50` 字遇到标点就切（`render.py:187-208`）；
- 打字机路径 `sum(cw) <= 1920 * 0.9`（`render.py:2869-2870`）硬编码 1920，而 `width` 参数就在手里没用。

**后果**：72px 标题 × 16 字 = 1152px > 1080 → 竖屏/小画幅直接溢出；竖屏上打字机的宽度判断恒为真。

**修复**：改成按像素测量（`font.getlength` / `measureText`）；阈值用 `width`。

---

### S21（P2）clip id 不唯一 + 导出把标题当字幕

**证据**：`segments_to_timeline_clips` 的 id 固定为 `f"cap_{i}"`（`services/subtitle.py:95`）——
两次导入到同一时间线就撞 id；`timeline_clips_to_segments` 接受 `kind in ("caption","text")`（`services/subtitle.py:118`），
导出 SRT 时把标题文字也当字幕。

**修复**：id 用 `uuid4`（或 `cap_{track}_{sha1(text+start)}`）；导出只看 `category=="caption"`。

---

### S22（P3）`TextStyle.from_dict` 遇到 `None` 会崩

**证据**：`design.py:282` `if self.shadow_blur > 0:`；`from_dict` 直接 `d.get("shadow_blur", 0)`
—— 若 dict 里显式是 `null`，得到 `None`，比较抛 `TypeError: '>' not supported between 'NoneType' and 'int'`。

**可达性（已核对，不是 P0）**：正常链路里 `_extract_text_overlay` 用 `if v is not None` 过滤了 clip 字段
（`render.py:1454-1457`），前端 `null` 也在 clip 顶层而非 `metadata.style`。只有**手写 `metadata.style` 带 null**
（API/Agent 生成）才会踩到。

**修复**：`from_dict` 对 `None` 一律回默认值（`d.get(k) if d.get(k) is not None else default`）。

---

## C. 前端

### S23（P2）`end_sec` 契约漂移：时间列恒为 `--:--`

**证据**：后端只发 `start_sec` + `duration_sec`（`services/subtitle.py:94-110`）；
前端 `SubtitleClip` 把 `end_sec` 声明为必填（`services/api/subtitle.ts:6-12`），
`ClipsTable` 直接用 `fmtTime(c.end_sec)`（`SubtitleToolsPage.tsx:394`），而 `fmtTime(undefined)` 返回 `'--:--'`。

**后果**：SRT 导入结果表永远显示 `0.000 → --:--`；
`:268` 的导出占位示例还教用户写 `{start_sec:0, end_sec:2.5}`，而后端只读 `duration_sec`（缺省 2）→ 按示例导出得到 **2s** 而不是 2.5s。

**修复**：前端类型改为 `duration_sec`（或两端都发 `end_sec`），同步占位示例。

---

### S24（P2）默认样式三处漂移

| 字段 | 前端 `PropertiesPanel` | `TextStyle` | `DEFAULT_CAPTION_STYLE` |
|---|---|---|---|
| `shadow_color` | `#000000` | `#00000080` | `None` |
| `glow_color` | `#FFFFFF` | `""`（关） | `None` |

**后果**：不显式设颜色就开阴影时，预览是纯黑，成片是 50% 透明黑；发光同理。

**修复**：统一到一处常量并让前端从后端拉默认值。

---

## D. 测试盲区（用现有用例举证）

| 缺口 | 证据 |
|---|---|
| SRT 时间格式**零覆盖** | `tests/` 里 grep `parse_srt`/`to_srt`/`_srt_time`/`_to_srt_time` 无命中（只有 `test_subtitle_style_defaults.py` 测样式）。S11/S16 因此全部漏网 |
| ASS 覆盖标签只测了 5 个 | `test_ass_style.py:108-116` 只断言 `\an2/\i1/\fsp5/\blur8/\bord3`，**没有 `\b`、`\xshad/\yshad`、`\N`** —— 恰好对应 S12/S14/S18 |
| 边距 bug 被断言固化 | `test_ass_style.py:104` 断言 `Default,,0,0,10,`，把 S13 写成了"期望行为" |
| `subtitle_burn` 无测试 | `test_drawtext_escape.py` 测的是 `escape_drawtext_text`，而 `tool/subtitle.py` 走的是另一条自己写的转义 → S15 漏网 |
| 引擎侧无"渲染像素 ↔ 审计数字"一致性测试 | S01/S04 都是"两个函数各算一遍"的产物，无交叉断言 |

**建议新增 4 条最小测试**（都可直接照抄本报告用例）：
1. `to_srt` 在 `.9996` 边界不进位（S11）；
2. `build_ass_dialogue` 对 `\n` 输出 `\N` 且 `splitlines()` 长度为 1（S12）；
3. Dialogue 行不含 margin 覆盖（S13）；
4. 引擎：`len(cue["words"]) == len(strip_punct(text))`（S01）。

---

## E. 不确定项（需更多证据，未计入上面 24 条）

1. **`_build_drawtext_filter` 的 `fontfile` 路径未加引号**（`render.py:2864`）：只有当 `clip.font` 落在含空格/中文的路径时才出事；`_resolve_system_font` 返回的 `_fonts/msyh.ttc` 无空格，概率低。
2. **`\blur{value:g}` 的科学计数法**（`design.py:283`）：`shadow_blur ≥ 1e6` 会输出 `1e+06`；常规值无碍。
3. **`SubtitleBurnTool` 是否真的进生产链路**：它注册了、有权档，但主渲染走 ASS/drawtext；若只被 LLM 偶然选中，S15 的暴露面有限（但仍是"选中即崩"）。
4. **前端 `captionFontSize` 用 `min(fw,fh)/1080` 缩放 vs 后端 ASS PlayRes**：两边都随画幅缩放，理论一致；未做像素级对照，暂不列为缺陷。

---

## F. 修复优先级建议

| 批次 | 内容 | 理由 |
|---|---|---|
| 第一批（当天） | S12 `\N`、S13 margin、S11 毫秒、S16 时长守卫 | 四条都产"废文件/整批失效"，改动都是几行 |
| 第二批（本周） | S15 转义复用、S01 逐字错位、S04 字号口径、S19 重叠 | 影响正常歌词/口播字幕的可读性 |
| 第三批 | S14 阴影、S17 text_align、S18 bold、S20 断行像素化、S23 前端契约 | 预览/成片不一致类，用户最常投诉 |
| 第四批 | S05–S10、S21、S22、S24 | 体验与一致性打磨 |

---

## G. 追加（2026-10-24）：上传配音链路

### S25（P1）上传配音的字幕对齐被缩进在 SFX 分支里，几乎永不执行

**证据**：`_align` 逻辑原本位于 `agents/audio_agent.py` 的
`if isinstance(_sfx_list, list) and _sfx_list:` 体内（AST 确认：`try` 的父节点是 110 行的
sfx 判断）。也就是说**只有传了 `audio_config.sfx` 才会对齐字幕**；普通上传配音场景下
字幕永远停留在 EditAgent 的字数比例估算，音画/字幕随时间漂移。

**后果**：用户上传配音后，画面与字幕「越到后面越对不上」。

**修复**：抽成 `AudioAgent._align_captions_to_uploaded_dub()`，在 `audio_path` 存在时
（插入配音 clip 之后）**无条件调用**，与 SFX 无关。

### S26（P1）前端上传入口自我隐藏：没有音频就永远看不到上传按钮

**证据**：`HomePage.tsx` 的 `mode` 由 `setMode(audio ? 'voiceover' : 'visual')` 派生，
而上传控件整块包在 `{mode === 'voiceover' && (...)}` 里 →
首次进入（无音频）→ `visual` → 上传按钮不渲染；而按钮是**唯一**能产生 audio 的入口。

**后果**：用户根本无法上传配音，只能落在「画面优先」模式。

**修复**：① 上传控件移出 mode 门控，始终可见；② 派生规则改为
`audio || autoDubPref ? 'voiceover' : 'visual'`（不上传也能用自动配音生成旁白）。

### S27（P2）无标点长文 → 对齐只切出 1 段，字幕塌成一段

**证据**：`STTService._split_sentences` 只按 `。！？.!?\n` 切；整段口播无这些标点时
`align` 返回 1 个 segment，`_realign_captions_to_narration` 会把 N 条字幕替换成 1 条。

**修复**：段数与字幕段数不一致时，改用 `_proportional_segments()`——按配音总时长
等比分配给**原有每一段字幕**，保证"切几段就几段"，且文案不被转写改字。

**回归**：`tests/clipwright/test_audio_uploaded_dub_alignment.py`（5 条）。
