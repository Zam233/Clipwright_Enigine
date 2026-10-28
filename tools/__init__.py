"""开发期验收工具集（对应开发计划 R-02 / R-03 / R-16）。

模块说明：
- ``lint_determinism``：扫描场景脚本里的墙钟/随机/网络依赖；
- ``hash_frames``：逐帧 sha256 比对（确定性验收）；
- ``check_flash``：闪光安全检测（每秒 ≤3 次大面积往返）。

这些工具**不进生产包**，只被 CLI 与验收脚本调用；因此不在
``clipwright.tool.registry`` 注册，也不计入能力矩阵。
"""

from __future__ import annotations

__all__ = ["check_flash", "hash_frames", "lint_determinism"]
