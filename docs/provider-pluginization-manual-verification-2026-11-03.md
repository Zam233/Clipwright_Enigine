# 人工验收剧本（provider 插件化与提示词覆盖，2026-11-03）

> **用途**：把「只有人能做的验收」压成可照着敲的命令 + 期望输出 + 要回报什么。
> 按项目约定，pytest / vitest 由人执行，我只跑 `py_compile`、`tsc --noEmit` 与自检脚本。
> 每跑完一项，把 §7 里对应的一行贴回来，我据此关闭台账条目。
>
> 权威细节：[PRD](provider-pluginization-and-prompt-override-prd-2026-11-02.md)、
> [编码计划](provider-pluginization-coding-plan-2026-11-02.md)、
> [核实台账](provider-pluginization-verification-ledger-2026-11-03.md)、
> [B3-T6 删除清单](provider-pluginization-b3t6-removal-checklist.md)。

## 0. 前置

```powershell
cd J:\Clipwright;  py -3.14 -c "import clipwright.main; print('ok')"
scripts\start.ps1        # 后端 8080 + 前端 5173（+ 账号服务 8090）
```

- 鉴权模式默认 `off`（等价管理员），所以下面的 `/api/plugin/*`、`/api/providers/*` 诊断端点不用登录。
- 后端若已在跑旧进程，**先重启**：Python 改动不会热加载。
- 本节所有命令都在 `J:\Clipwright` 目录下执行（前端命令除外，会标注）。

## 1. 自动化门槛（4 条）

| # | 命令 | 期望 | 失败时 |
|---|---|---|---|
| 1 | `cd J:\Clipwright; py -3.14 -m pytest tests -q` | 全绿（历史基线 1125 passed / 5 skipped） | 贴失败文件名 + 首段断言 |
| 2 | `cd J:\Clipweight-Client; npx tsc --noEmit` | 退出码 0 | 贴 TS 报错 |
| 3 | `cd J:\Clipweight-Client; npm run test` | 全绿 | 贴失败用例名 |
| 4 | `cd J:\Clipweight-Client; npm run build` | 构建成功 | 贴 vite 报错 |

第 2 条我已跑过（0 错）。**1 / 3 / 4 需要你**——我这轮只做了 `py_compile` 与 `tsc --noEmit`。

## 2. 本次改造的运行期契约（一条命令，13 项）

```powershell
cd J:\Clipwright; py -3.14 scripts\verify_provider_pluginization.py
```

期望最后一行：**`共 13 项，通过 13，失败 0`**（我本地是 13/13）。
失败项看栈：`--trace`；单跑一项：`-k prompt`（子串过滤）。
这 13 项不依赖 pytest、不写任何落盘数据，覆盖的是本次改造引入的运行期契约。

## 3. B3-T6 删除前的三项人工确认（删除的**唯一**前置）

依据 [删除清单 §0](provider-pluginization-b3t6-removal-checklist.md)。

### 3.1 插件在时，配音能力由插件提供

```powershell
$body = @{ voice_id = "任一已建音色"; text = "第一句。第二句。" } | ConvertTo-Json
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -X POST http://127.0.0.1:8080/api/voice/dub `
  -H "Content-Type: application/json" -d $body
```

期望：`HTTP 200` + `{"success":true,...}`。
没有 TTS 凭据时会**结构化失败**（`detail.error_code`，例如 `auth_failed` / `provider_not_found`），
这也是可接受的——**不接受 500 或裸异常**。

### 3.2 禁用功能插件后：能力整体消失，但有可读降级（不许 500）

```powershell
curl.exe -s -X POST http://127.0.0.1:8080/api/plugin/tts_feature/disable
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -X POST http://127.0.0.1:8080/api/voice/dub `
  -H "Content-Type: application/json" -d $body
curl.exe -s -X POST http://127.0.0.1:8080/api/plugin/tts_feature/enable
```

期望：`HTTP 503`，`detail` 说的是「配音能力当前不可用（TTS 功能插件未安装或被禁用）」这类可读原因。
再新建一个小任务跑到 audio 阶段：期望 AudioAgent 写「未配音：当前没有可用的配音能力（TTS 功能插件未安装或被禁用）」，
任务照常收尾，**不 500、不崩、不空等**。

### 3.3 三个 TTS provider id 仍在（删除内置注册不会让下拉变短）

```powershell
curl.exe -s http://127.0.0.1:8080/api/providers/available
```

期望返回里 `tts_provider` 数组含 **`cosyvoice` / `qwen-tts` / `minimax`** 三项
（该端点不接 `kind` 参数，一次返回各类型；键名是 kind 值，如 `llm_provider` / `tts_provider`）。
再看 `GET /api/providers/schemas?kind=tts`：三个 id 都应带 `plugin_id`
（`tts_cosyvoice` / `tts_qwen` / `tts_minimax`），而不是空串（空串=内置）。

三项都过 → 回一句「可以删」，我按清单执行删除（**单个 commit**，便于 `git revert`）。

## 4. H-057：TTS 取消与降级端到端

1. 用**长文本**（≥300 字）在「新建制作」里触发配音；运行中在运行条上点停止，
   或直接 `POST /api/requirements/{session_id}/stop`。
2. 期望：任务的 `terminal_reason` 是中止/`capped` 之一；时间线与消息流各留一条
   `metadata.user_stopped=true` 的说明；**没有 500**、没有悬挂协程；再说一句话能续跑。
3. 降级侧（可选，脚本已覆盖错误结构）：用不存在的 provider 调 `/api/voice/synthesize`，
   期望 `detail` 为结构化对象 `{error_code, stage, provider_id, retryable, message}`，
   其中 `error_code="provider_not_found"`。

## 5. H-065：提示词「只有一处」的运行时证据

- **我已经能自证的部分**：
  1. 26 常量 + 8 builder 的文本与搬运前 **sha256 全等**（五批差异 0，记录在台账）；
  2. 自检第 13 项 `prompt_runtime_path`：真正拼 prompt 的函数 `agent_loop._system_prompt()`
     三态（plan_only / 正常 / 收口）必须等于 `prompts.py` 的符号；注册插件覆盖后**运行时立即改变**，
     导入期快照 `_NORMAL_SYSTEM` 不被改写，回滚后恢复默认。
- **只剩一件事**：§1 第 1 条 `pytest` 全绿。仓库里**没有任何用例引用提示词符号**，
  所以提示词的行为侧只能靠全套回归兜。
- 想看符号清单与覆盖来源（不返回正文）：
  `curl.exe -s http://127.0.0.1:8080/api/plugin/prompt-symbols`
- 已知缺口（有意不迁）：`services/requirements_service.py:396` 另有一份「快速草案」提示词
  `CREATIVE_BRIEF_SYSTEM`，与 `prompts.py` 同名符号是**两份不同文本**，台账记为 H-072。

## 6. 前端观感（约 3 分钟）

| # | 去哪里 | 看什么 |
|---|---|---|
| 1 | 模型提供商页 | 源列表显示「插件：`llm_openai_compatible` / 内置实现」；源设置里有能力徽标（流式 / 工具 / 视觉 / JSON / 思考强度 / 思考内容） |
| 2 | 插件管理页 →「提示词符号」 | 26 常量 + 8 builder、已接线数、被覆盖数、每个符号的绿/灰点与覆盖它的 plugin id |
| 3 | 新建制作 →「03 配音与字幕」 | 音色下拉（宿主白名单）+「自动配音」开关 + **上传配音**（`file` 控件）。上传后出现「已上传：xxx.wav · 时长」回执卡；禁用 tts 插件后回落到内置块 |
| 4 | 同上 | 上传一个 `.mp3` → 字幕预览按实测时长对齐；关掉自动配音且不选音色 → 纯画面模式 |

## 7. 回报什么（每项一行就够）

| 门槛 | 回报内容 |
|---|---|
| pytest | 结尾统计行（`N passed, M skipped`）+ 任何 `FAILED` 的文件名 |
| tsc / vitest / build | 「全绿」或贴报错 |
| 13 项自检 | 最后一行「共 13 项，通过 13，失败 0」 |
| B3-T6 三项 | 3.1 的 HTTP 码、3.2 的 `detail` 原文、3.3 的 provider id 列表 |
| H-057 | 中止后的 `terminal_reason`、是否留了 `user_stopped` 说明、有无 500 |
| 前端观感 | 4 条里哪条不对（截图更好） |

拿到这些我就能关掉 **H-065 / H-057 / B3-T6**，把台账的「仍开」清零；
剩下唯一开项会是 H-044（是否新增 STT Tab），那是纯产品决策。
