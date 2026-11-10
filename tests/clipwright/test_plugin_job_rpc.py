"""M3 e2e：插件经 call_host 提交 job、轮询状态并登记产物。"""
from __future__ import annotations

import sys

import pytest

from clipwright.plugins.runtime import PluginHost
from clipwright.services.plugin_rpc import install_default_handlers, register_runtime
from clipwright.services.plugin_jobs import RuntimeSpec

PLUGIN_SOURCE = "\n".join([
    "import asyncio",
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import call_host",
    "",
    "",
    "class JobTool:",
    "    async def execute(self, **kwargs):",
    "        sub = await call_host('job.submit', {",
    "            'runtime_id': 'py',",
    "            'argv': ['-c', \"open('art.txt','w').write('x')\"],",
    "            'timeout_ms': 10000,",
    "        })",
    "        job_id = sub['job_id']",
    "        status = sub",
    "        for _ in range(100):",
    "            status = await call_host('job.status', {'job_id': job_id})",
    "            if status['status'] in ('succeeded', 'failed', 'cancelled',",
    "                                    'timed_out', 'interrupted'):",
    "                break",
    "            await asyncio.sleep(0.05)",
    "        art = await call_host('artifact.register',",
    "                              {'path': 'art.txt', 'role': 'render'})",
    "        return {'success': True, 'output': {'job': status, 'artifact': art}}",
    "",
    "",
    "class JobPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(ContributionKind.TOOL, 'probe_job', JobTool(),",
    "                             meta={'name': 'probe_job'})]",
    "",
])


@pytest.fixture
def env(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    register_runtime(RuntimeSpec(runtime_id="py", executable=sys.executable, approved=True))
    root = tmp_path / "plugins" / "job_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: job_probe\nname: job probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    host = PluginHost(
        "job_probe", root,
        tmp_path / "PluginData" / "plugins" / "job_probe",
        call_timeout=20.0,
    )
    install_default_handlers(host)
    host.start()
    scope = {"project_id": "proj-1", "task_id": "t1"}
    yield host, scope
    host.stop()


@pytest.mark.asyncio
async def test_job_submit_and_artifact_over_rpc(env):
    host, scope = env
    result = await host.invoke(
        "tool.call", {"id": "probe_job", "input": {}, "scope": scope}, scope=scope,
    )
    assert result["success"] is True, result
    out = result["output"]
    assert out["job"]["status"] == "succeeded", out["job"]
    assert out["artifact"]["role"] == "render"
    assert out["artifact"]["path"] == "art.txt"
