"""内置 Provider 的统一注册入口 —— 「让位必须发生在注册之前」。

每个 provider 家族（`llm` / `embedding` / `rerank` / `stt` / `tts`）原先各自复制了
同一段三行逻辑：

```python
if ContributionRegistry.resolve(KIND, "<首 id>") is not None:
    return
ContributionRegistry.register(BUILTIN_OWNER, [...], dispatch=False)
```

这段顺序**不能颠倒**，也不能只在一处写对：

- `ContributionRegistry._validate`（`plugins/contribution.py:189-193`）对「已存在
  内置」默认 REJECT，只有 `ConflictPolicy.OVERRIDE`（需 manifest
  `override_builtin` + 签名）才放行；
- 因此「插件覆盖内置」唯一可行的路径就是**在内置注册之前先查一次**——查到了就
  整体让位，让冲突判定根本不会触发。

反过来说，一旦有人把顺序改成「先注册内置、插件后注册」，同名插件会被直接拒绝，
插件化当场失效，而报错信息只会说「不得覆盖内置贡献」，很难看出根因。

把这条顺序收成 `register_builtin_once()` 的目的就是：**只有一种写法是对的**，
六处复制粘贴不会各自漂移。

⚠️ 移除内置（裁定 3：engine 默认不带 TTS 能力）**不是**改这个函数，而是不再调用它
——让位与「退场」是两件事：让位是「插件在就让」，退场是「谁都不在也不注册」。
"""
from __future__ import annotations

from typing import Iterable

from clipwright.plugins.contribution import (
    BUILTIN_OWNER,
    Contribution,
    ContributionKind,
    ContributionRegistry,
)

__all__ = [
    "needs_builtin_registration",
    "register_builtin_once",
]


def needs_builtin_registration(kind: ContributionKind, probe_id: str) -> bool:
    """该家族当前是否还需要登记内置实现（True = 注册表里没有实现）。

    ``probe_id`` 传该家族**首个内置 provider 的 id**（与各模块既有探针一致）。
    它只是探针：同一家族的插件只要注册了任意一个内置同名 id，整族就让位。
    """
    return ContributionRegistry.resolve(kind, probe_id) is None


def register_builtin_once(
    kind: ContributionKind,
    probe_id: str,
    items: Iterable[Contribution],
) -> bool:
    """先查后注册；返回 True=已登记内置，False=让位给已存在的实现。

    ``dispatch=False``：内置实现已由各自构造路径落地，门面只做归属与冲突基准，
    转调会二次注册（见 `ContributionRegistry.register` 的 docstring）。
    """
    if not needs_builtin_registration(kind, probe_id):
        return False
    ContributionRegistry.register(BUILTIN_OWNER, list(items), dispatch=False)
    return True
