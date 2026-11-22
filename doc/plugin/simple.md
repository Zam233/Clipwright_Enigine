# 最小实例：一个可加载、可调用的工具插件

本页只做一件事：让 Clipwright 加载你的插件，并让 Agent 能调用其中一个工具。
扩展点的完整列表见 [扩展点](contributions.md)。

## 1. 目录

```
plugins/my_echo/
├── plugin.yaml
└── main.py
```

## 2. 清单 `plugin.yaml`

```yaml
id: my_echo
name: 回声示例
version: 0.1.0
kind: capability
api_version: "2"
entry_point: my_echo.main
contributions:
  - { kind: tool, id: my_echo }
```

`id` 必须与目录名一致（小写字母/数字/下划线/连字符）。`contributions` 里的
`(kind, id)` 是运行时注册白名单：**没声明的 id 注册会被拒绝**。

## 3. 代码 `main.py`

```python
from typing import Any

from clipwright.plugins.base import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.tool.base import BaseTool


class MyEchoTool(BaseTool):
    name = "my_echo"
    description = "把输入原样返回；用于验证插件链路"
    capability = "my_echo"          # 语义能力名：Agent 只认能力，不认工具名
    permission_tier = "read"        # read / write_local / costly / dangerous
    agent_callable = True           # 允许 Agent 的 tool-use 调用

    async def execute(self, text: str = "", **_: Any) -> dict[str, Any]:
        return {"ok": True, "text": text}


class MyEchoPlugin(CapabilityPlugin):
    def setup(self) -> None:
        # 可选副作用：建目录、读配置、预热；这里不要注册贡献。
        pass

    def contributions(self):
        return [Contribution(ContributionKind.TOOL, "my_echo", MyEchoTool())]
```

要点：

- 插件类必须定义在本插件包内，且是 `BasePlugin` 的非抽象子类；
- 工具名不必等于贡献 id，但贡献 id 必须在 manifest 里声明；
- `capability` 让 Agent 通过能力名解析工具，插件可以“接管”一个已有能力；
- `permission_tier` 由工具自声明，引擎按它做预算/收口期工具面裁剪。

## 4. 加载与验证

```bash
# 加载（已加载会重载）
curl -X POST http://127.0.0.1:8080/api/plugin/load/my_echo

# 看清单、贡献与错误
curl http://127.0.0.1:8080/api/plugin/list
curl http://127.0.0.1:8080/api/plugin/errors
```

`GET /api/plugin/list` 返回的 `contributions` 应包含 `tool: my_echo`；若 manifest 声明了但
运行时没注册，会出现在 `declared_only`。加载失败先看 `errors`。

也可以在「管理控制台 → 插件」页点重载。改代码后必须重载才会生效。

## 5. 常见失败

| 现象 | 原因 |
|---|---|
| 加载失败：`entry_point 必须属于 <id>` | `entry_point` 写了顶层模块名（如 `requests`） |
| 已加载但零贡献 | 没实现 `contributions()`，或贡献 id 与 manifest 不一致 |
| `declared_only` 非空 | manifest 声明了但 `contributions()` 没返回对应条目 |
| 注册被拒 | id 未在 manifest 声明，或与内置冲突（见 [扩展点](contributions.md)） |

下一步：给插件加配置 → [插件配置](config.md)；加设置面板 → [可视化视图](ui.md)。
