"""M1 安全基线：worker 不继承宿主密钥、越界读被拒。"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.plugins.runtime import PluginHost

SOURCE = "\n".join([
    "import os",
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "",
    "",
    "class Probe:",
    "    def __init__(self, mode):",
    "        self.mode = mode",
    "",
    "    async def execute(self, **kwargs):",
    "        if self.mode == 'env':",
    "            return {'success': True, 'output': {",
    "                'token': os.environ.get('CLIPWRIGHT_API_TOKEN', 'MISSING'),",
    "                'model_key': os.environ.get('DASHSCOPE_API_KEY', 'MISSING'),",
    "                'home': bool(os.environ.get('HOME') or os.environ.get('USERPROFILE')),",
    "            }}",
    "        path = kwargs.get('path') or ''",
    "        try:",
    "            with open(path, 'r', encoding='utf-8') as fh:",
    "                return {'success': True, 'output': fh.read(64)}",
    "        except Exception as exc:",
    "            return {'success': False,",
    "                    'error': type(exc).__name__ + ': ' + str(exc)}",
    "",
    "",
    "class SecurityProbePlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [",
    "            Contribution(ContributionKind.TOOL, 'probe_env', Probe('env'),",
    "                         meta={'name': 'probe_env'}),",
    "            Contribution(ContributionKind.TOOL, 'probe_read', Probe('read'),",
    "                         meta={'name': 'probe_read'}),",
    "        ]",
    "",
])


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPWRIGHT_API_TOKEN", "super-secret-token")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-should-not-leak")
    root = tmp_path / "plugins" / "sec_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: sec_probe\nname: sec probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n"
        "  - kind: tool\n    id: probe_env\n"
        "  - kind: tool\n    id: probe_read\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(SOURCE, encoding="utf-8")
    instance = PluginHost("sec_probe", root, tmp_path / "PluginData" / "sec_probe")
    instance.start()
    yield instance, tmp_path
    instance.stop()


@pytest.mark.asyncio
async def test_worker_env_excludes_host_secrets(host):
    instance, _tmp = host
    result = await instance.invoke(
        "tool.call", {"id": "probe_env", "input": {}},
    )
    assert result["success"] is True
    out = result["output"]
    assert out["token"] == "MISSING"
    assert out["model_key"] == "MISSING"
    assert out["home"] is True  # 运行必需项仍保留


@pytest.mark.asyncio
async def test_read_outside_roots_denied(host):
    instance, tmp_path = host
    secret = Path(tmp_path) / "secret.txt"
    secret.write_text("do-not-read", encoding="utf-8")
    result = await instance.invoke(
        "tool.call", {"id": "probe_read", "input": {"path": str(secret)}},
    )
    assert result["success"] is False
    assert "读越界" in str(result["error"]) or "blocked" in str(result["error"])
