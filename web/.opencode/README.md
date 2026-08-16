# ClipWright `.opencode` Agent 工具链自述

> **⚠️ 重要声明：本目录中的一切内容属于 Agent 工具链（AGENT TOOLING），
> 不是 ClipWright（帧艺）产品功能。**
> 它只服务于 AI Agent 在开发过程中的辅助需求（例如查看截图/错误图），
> **禁止**在 AGENTS.md、README.md、docs/ 或任何产品文档/审计报告中引用或混入本工具链。
> 唯一允许的自述文档就是本文件。

## 目录

```
.opencode/
├── scripts/
│   ├── describe_image.py   # 视觉转述 CLI（仅 Python 标准库）
│   └── image-prompt.md     # 中文图像转述 system prompt（可编辑）
├── plugin/
│   └── describe-image.ts   # opencode 自定义工具 describe_image（注册入口）
├── commands/
│   └── describe-image.md   # 斜杠命令兜底 /describe-image
├── package.json            # 本地插件依赖（@opencode-ai/plugin、zod）
└── README.md               # 本文件
```

## describe_image：视觉转述工具

把本地图片（png/jpg/jpeg/gif/webp…）提交给视觉大模型，返回**中文结构化 Markdown 描述**。

### 直接使用（CLI）

```bash
python .opencode/scripts/describe_image.py e2e-error.png
python .opencode/scripts/describe_image.py e2e-error.png --prompt "重点描述错误弹窗里的文字"
python .opencode/scripts/describe_image.py e2e-error.png --json     # 输出原始 JSON 响应
```

参数：
- `<image_path>`（必填）— 图片路径，不存在/不可读时非零退出（退出码 2）。
- `--prompt`（可选）— 用户文本提示，默认「请详细描述这张图片。」。
- `--json`（可选）— 输出 OpenAI 兼容接口的原始 JSON（可用 `python -c "import json,sys; json.loads(sys.stdin.read())"` 校验）。
- 超时 120s；任何失败均以非零退出码 + stderr 中的明确错误结束。

### 通过 opencode 工具调用

`describe-image.ts` 插件注册了名为 `describe_image` 的自定义工具，Agent 可直接调用
（`image_path` 必填，`prompt` 可选）。

**重要：改动 `.opencode/plugin/` 或 `.opencode/commands/` 后必须重启 opencode 才会生效。**
（opencode 在启动时扫描并加载插件目录与命令；不同版本对插件的发现目录以 `opencode.ai/docs/plugins` 为准，
若当前版本不扫描 `.opencode/plugin/`（单数），请把 `describe-image.ts` 放到它实际扫描的插件目录，
再重启验证。）

### API 与鉴权

- 端点：`https://api.llm-token.cn/v1/chat/completions`（OpenAI 兼容）
- 模型：`qwen3.8-max`
- 鉴权：`Authorization: Bearer <key>`
  - 优先读取环境变量 `VISION_API_KEY` 覆盖默认 key；
  - 未设置时使用脚本内嵌的默认 key（唯一文档化位置）。
- system prompt = `.opencode/scripts/image-prompt.md` 内容；运行时文件缺失则用脚本内嵌副本兜底。

### 已知风险

- 网关账户余额有限：若 API 返回 quota/balance/鉴权类错误，脚本会以非零退出并打印精确错误——
  说明代码路径正确，运行时验证被外部额度限制阻断。
- 图片较大时 base64 请求体随之增大，请保持网关/模型可接受的尺寸。
