"""M4：host→worker 事件（取消/超时）与 bridge.on_event 订阅。"""
from __future__ import annotations

import asyncio

import pytest

from clipwright.plugins.runtime import PluginHost

SOURCE = "\n".join([
    "import asyncio",
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import on_event",
    "",
    "RECEIVED = []",
    "",
    "",
    "class WaitTool:",
    "    async def execute(self, **kwargs):",
    "        got = asyncio.Event()",
    "",
    "        def _handler(name, payload):",
    "            RECEIVED.append((name, payload))",
    "            got.set()",
    "",
    "        on_event(_handler)",
    "        try:",
    "            await asyncio.wait_for(got.wait(), timeout=float(kwargs.get('timeout', 2.0)))",
    "        except asyncio.TimeoutError:",
    "            return {'success': False, 'error': 'timeout'}",
    "        return {'success': True, 'output': {'events': RECEIVED}}",
    "",
    "",
    "class EventPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(ContributionKind.TOOL, 'probe_wait', WaitTool(),",
    "                             meta={'name': 'probe_wait'})]",
    "",
])


@pytest.fixture
def host(tmp_path):
    root = tmp_path / "plugins" / "evt_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: evt_probe\nname: evt probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n  - kind: tool\n    id: probe_wait\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(SOURCE, encoding="utf-8")
    instance = PluginHost("evt_probe", root, tmp_path / "PluginData" / "evt_probe")
    instance.start()
    yield instance
    instance.stop()


@pytest.mark.asyncio
async def test_host_event_reaches_plugin(host):
    task = asyncio.create_task(
        host.invoke("tool.call", {"id": "probe_wait", "input": {"timeout": 3}})
    )
    await asyncio.sleep(0.5)  # 等插件注册 handler
    assert host.notify("job.cancelled", {"job_id": "job_1", "reason": "test"})
    result = await asyncio.wait_for(task, timeout=5)
    assert result["success"] is True
    events = result["output"]["events"]
    assert events[0][0] == "job.cancelled"
    assert events[0][1]["job_id"] == "job_1"


@pytest.mark.asyncio
async def test_notify_without_worker_returns_false(host):
    host.stop()
    assert host.notify("job.cancelled", {}) is False
