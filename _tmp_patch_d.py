"""批D 补丁（修正版）。执行后删除。"""
from pathlib import Path

# ── D1(R10): 下游闭包纳入插件 Agent ──
p = Path("clipwright/services/pipeline_v2.py")
src = p.read_text(encoding="utf-8")
old = '''        rev_deps: dict[str, set[str]] = {a: set() for a in AgentDAG._DEPS}
        for dep, base in AgentDAG._DEPS.items():
            for b in base:
                rev_deps.setdefault(b, set()).add(dep)
        closed: set[str] = set()'''
new = '''        # 批D(R10)：用合并插件 Agent 的完整依赖图（旧实现仅核心 DAG，
        # 插件 Agent 在上游重做后不会被联动）
        full_deps = AgentDAG.get_full_deps()
        rev_deps: dict[str, set[str]] = {a: set() for a in full_deps}
        for dep, base in full_deps.items():
            for b in base:
                rev_deps.setdefault(b, set()).add(dep)
        closed: set[str] = set()'''
assert src.count(old) == 1, f"D1={src.count(old)}"
src = src.replace(old, new)

# ── D2(R11): 持久化串行化 ──
n = src.count("None, self._persist_state, state,")
src = src.replace("None, self._persist_state, state,",
                  "None, self._persist_state_serial, state,")
print(f"D2 persist sites rewritten: {n}")

# 追加串行执行器 + 单例池（挂在 _persist_state 定义后）
i = src.find("def _persist_state(")
assert i > 0
j = src.find("\n    async def", i)
if j < 0:
    j = src.find("\n    def ", i + 10)
helper = '''
    def _persist_state_serial(self, state, status_str: str, error_category: str) -> None:
        """批D(R11)：持久化专用单线程执行器——并行组内各 Agent 同时落全量
        状态时，默认线程池并发 find→update/insert 会竞态双插/互相覆盖；
        单工作线程保证持久化按调用顺序串行。"""
        if getattr(self, "_persist_pool", None) is None:
            from concurrent.futures import ThreadPoolExecutor
            self._persist_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cw-persist")
        self._persist_pool.submit(self._persist_state, state, status_str, error_category).result()
'''
src = src[:j] + helper + src[j:]
p.write_text(src, encoding="utf-8")
print("D2 done")

# ── D3: semantic QA 支持读 script_skeleton.brief ──
p = Path("clipwright/agents/quality_agent.py")
src = p.read_text(encoding="utf-8")
i = src.find("_check_semantic_qa(")
j = src.rfind("async def _check_semantic_qa", 0, i + 40)
seg_start = src.find("creative_brief", j)
print("semantic brief ref context:", repr(src[seg_start-60:seg_start+60]))
PYEOF
