"""通用「声明式展示契约」引擎侧机制（第 18 类扩展点 `display_spec` 的消费者）。

设计边界（见 `docs/genui-plugin-migration-prd-2026-11-07.md` §3）：

- **能力归插件**：组件白名单、上限、色调/字号、卡片配方、教模型的规范全在
  `plugins/<id>/`；engine 只提供「读契约 → 清洗 → 装配 → 交给宿主渲染」这套机制。
- **零信任**：插件只能交付**数据**（`Contribution.object is None`），engine 从不
  `callable()` 插件的任何对象。所以清洗器必须在 engine，且由契约驱动（本模块只
  读契约，不含任何产品名/组件名单字面量）。
- **不做 I/O**：契约来自 `ContributionRegistry`（内存表），不读文件、不 import
  插件模块，避免加载顺序耦合。
"""

from .contract import Contract, get_contract, list_contracts, normalize_contract
from .sanitizer import sanitize_display_spec

__all__ = [
    "Contract",
    "get_contract",
    "list_contracts",
    "normalize_contract",
    "sanitize_display_spec",
]
