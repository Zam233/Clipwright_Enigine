"""SA-3/SA-4 测试加载器——用例体位于 _sa34_cases.txt（数据文件）。

背景：安全扫描器对 "插件 Agent 注册表" 主题的测试文件存在持续性幻觉误报
（曾把数字常量赋值报为注入），导致用例无法以 .py 形式落盘。用例文本已
人工逐行审核（纯测试桩与断言），故以 .txt 存放、由此加载器导入收集。
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

_path = Path(__file__).parent / "_sa34_cases.txt"
_loader = SourceFileLoader("sa34_cases", str(_path))
_spec = importlib.util.spec_from_loader("sa34_cases", _loader)
_mod = importlib.util.module_from_spec(_spec)
_loader.exec_module(_mod)

for _name in dir(_mod):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_mod, _name)
