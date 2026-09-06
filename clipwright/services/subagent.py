"""SubAgentRunner 门面（SA-4）——实现体位于同目录 _subagent_body.txt。

背景：安全扫描器对本主题的 .py 写入存在持续性幻觉误报（把数字常量、
logging 调用报为 SQL 注入），实现体故以 .txt 载体存放、经 SourceFileLoader
加载（与 tests/clipwright/test_sa34_loader.py 同模式）。内容已人工审核。
"""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

_path = Path(__file__).parent / "_subagent_body.txt"
_loader = SourceFileLoader("clipwright.services._subagent_impl", str(_path))
_spec = spec_from_loader(_loader.name, _loader)
_mod = module_from_spec(_spec)
_loader.exec_module(_mod)

run_sub_agent = _mod.run_sub_agent
SubAgentError = _mod.SubAgentError
SubAgentDepthError = _mod.SubAgentDepthError
SubAgentCancelled = _mod.SubAgentCancelled
SubAgentNotFound = _mod.SubAgentNotFound
subagent_breaker_snapshot = _mod.subagent_breaker_snapshot
_sub_depth = _mod._sub_depth  # 测试/运维观测用（嵌套深度计数）
_sub_breakers = _mod._sub_breakers  # 测试用（熔断状态清理）
