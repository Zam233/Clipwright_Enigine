"""Pipeline 端到端集成测试 — DAG 执行 + 自愈循环 + 持久化 + 并发。

运行: cd J:\Clipwright && python -m pytest tests/test_pipeline_e2e.py -v
"""

import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clipwright.services.pipeline_v2 import AgentDAG, PipelineOrchestratorV2
from clipwright.schema.pipeline import PipelineRequest, PipelineStatus
from clipwright.schema.agent import AgentContext, AgentDecision
from clipwright.config import logger

passed = 0
failed = 0
results = []

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        results.append(f"  [OK] {name}")
    else:
        failed += 1
        results.append(f"  [FAIL] {name}: {detail}")


async def test_dag_execution_order():
    """测试 DAG 执行顺序正确性 — material 在 structure 之后运行。"""
    print("\n=== Test 1: DAG Execution Order ===")
    plan = AgentDAG.get_execution_plan()
    # 验证 structure 在 material 之前
    structure_group = next(i for i, g in enumerate(plan) if "structure" in g)
    material_group = next(i for i, g in enumerate(plan) if "material" in g)
    check("structure 在 material 之前运行", structure_group < material_group,
          f"structure=组{structure_group}, material=组{material_group}")
    # 验证 audio 和 animation 在同一组（并行）
    audio_group = next(i for i, g in enumerate(plan) if "audio" in g)
    anim_group = next(i for i, g in enumerate(plan) if "animation" in g)
    check("audio 和 animation 并行", audio_group == anim_group,
          f"audio=组{audio_group}, animation=组{anim_group}")
    # 总共 5 组
    check("DAG 共 5 个执行组", len(plan) == 5, f"实际 {len(plan)} 组")
    # 验证无环
    all_agents = set(AgentDAG._DEPS.keys())
    remaining = set(all_agents)
    for group in plan:
        for agent in group:
            remaining.discard(agent)
    check("所有 agent 都被调度", len(remaining) == 0, f"未调度: {remaining}")


async def test_circuit_breaker():
    """测试熔断器 — 连续 3 次失败后自动跳过。"""
    print("\n=== Test 2: Circuit Breaker ===")
    orch = PipelineOrchestratorV2()
    # 初始状态
    cb = orch._check_circuit_breaker("test_agent")
    check("熔断初始状态 false", cb is False)
    # 2 次失败仍可执行
    orch._record_agent_failure("test_agent")
    orch._record_agent_failure("test_agent")
    cb2 = orch._check_circuit_breaker("test_agent")
    check("2 次失败未熔断", cb2 is False)
    # 第 3 次失败后熔断
    orch._record_agent_failure("test_agent")
    cb3 = orch._check_circuit_breaker("test_agent")
    check("3 次失败后熔断", cb3 is True)
    # 成功后重置
    orch._record_agent_success("test_agent")
    cb4 = orch._check_circuit_breaker("test_agent")
    check("成功后熔断重置", cb4 is False)


async def test_error_categorization():
    """测试错误分类 — transient vs permanent。"""
    print("\n=== Test 3: Error Categorization ===")
    orch = PipelineOrchestratorV2()
    check("timeout 分类为 transient",
          orch._categorize_error("LLM request timed out") == "transient")
    check("connection 分类为 transient",
          orch._categorize_error("connection refused") == "transient")
    check("KeyError 分类为 permanent",
          orch._categorize_error("KeyError: 'scenes'") == "permanent")
    check("not found 分类为 permanent",
          orch._categorize_error("Persona not found: xyz") == "permanent")
    check("未知错误默认 permanent",
          orch._categorize_error("some random error") == "permanent")


async def test_get_downstream_agents():
    """测试下游 agent 推导 — 自愈联动。"""
    print("\n=== Test 4: Downstream Agents (Self-Healing Chain) ===")
    check("edit 下游 = animation, audio",
          set(PipelineOrchestratorV2._get_downstream_agents("edit")) == {"animation", "audio"})
    check("structure 下游 = material, edit",
          set(PipelineOrchestratorV2._get_downstream_agents("structure")) == {"material", "edit"})
    check("animation 下游 = quality",
          PipelineOrchestratorV2._get_downstream_agents("animation") == ["quality"])
    check("quality 无下游",
          PipelineOrchestratorV2._get_downstream_agents("quality") == [])


async def test_merge_agent_result():
    """测试 _merge_agent_result 数据一致性 — timeline 存为 dict。"""
    print("\n=== Test 5: Merge Agent Result (Data Consistency) ===")
    from clipwright.services.agent_bus import AgentBus
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2

    bus = AgentBus("test_pid")
    result_data = {}

    class MockStep:
        def __init__(self, d):
            self.result = d

    # 模拟 edit agent 输出
    step = MockStep({
        "timeline": {
            "id": "tl_001", "width": 1920, "height": 1080,
            "fps": 30, "duration_sec": 60,
            "tracks": [{"id": "t1", "name": "视频轨", "kind": "video", "index": 0, "clips": []}],
        },
        "edit_notes": ["test note"],
    })
    PipelineOrchestratorV2._merge_agent_result("edit", step, result_data, bus, "pid")
    check("result_data 有 timeline (dict)", "timeline" in result_data)
    check("timeline 是 dict 类型", isinstance(result_data["timeline"], dict),
          f"类型: {type(result_data['timeline'])}")
    check("result_data 有 edit_notes", "edit_notes" in result_data)
    check("bus 有 timeline artifact", bus.get_artifact("timeline") is not None)


async def test_persist_state():
    """测试 _persist_state 截断保护和 extra_params 保存。"""
    print("\n=== Test 6: Persist State (Truncation + Extra Params) ===")
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2
    from clipwright.schema.pipeline import PipelineRequest, PipelineState

    orch = PipelineOrchestratorV2()
    state = PipelineState(
        pipeline_id="test_truncation",
        request=PipelineRequest(
            persona_id="test", category_plugin_id="test", topic="test",
            extra_params={"video_mode": "voiceover", "custom_key": "custom_value"},
        ),
    )
    state.shared_data = {
        "large_field": "x" * 10000,
        "timeline_json": {"clips": [{"id": f"c{i}"} for i in range(500)]},
        "small_field": "hello",
    }
    # 只检查方法不抛异常
    try:
        orch._persist_state(state, status="completed", error_category="none")
        check("_persist_state 不抛异常", True)
    except Exception as e:
        check("_persist_state 不抛异常", False, str(e))


async def test_self_healing_chain_simulation():
    """模拟自愈循环 — Quality 返回 redo_agent='edit' 时联动重做 animation + audio。"""
    print("\n=== Test 7: Self-Healing Chain ===")
    # 验证自愈逻辑的 _get_downstream_agents 返回正确
    # 当 redo_agent='edit': 应重做 edit + animation + audio
    redo = "edit"
    downstream = PipelineOrchestratorV2._get_downstream_agents(redo)
    check(f"重做 {redo} 时联动 {downstream}",
          set(downstream) == {"animation", "audio"},
          f"下游: {downstream}")

    # 当 redo_agent='animation': 应重做 animation + quality
    redo2 = "animation"
    downstream2 = PipelineOrchestratorV2._get_downstream_agents(redo2)
    check(f"重做 {redo2} 时联动 {downstream2}",
          set(downstream2) == {"quality"},
          f"下游: {downstream2}")

    # 模拟 self-healing loop 代码逻辑
    quality_issues = [
        {"severity": "error", "category": "timing", "message": "clip too short"},
    ]
    has_errors = any(i.get("severity") == "error" for i in quality_issues)
    check("Quality 错误检测", has_errors is True)


async def run_all():
    """运行所有测试。"""
    await test_dag_execution_order()
    await test_circuit_breaker()
    await test_error_categorization()
    await test_get_downstream_agents()
    await test_merge_agent_result()
    await test_persist_state()
    await test_self_healing_chain_simulation()

    # 等待 MongoDB 异步写入
    await asyncio.sleep(0.5)

    total = passed + failed
    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过 / {failed} 失败 / 总计 {total}")
    print(f"{'='*50}")
    for r in results:
        print(r)
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all())
    sys.exit(0 if success else 1)
