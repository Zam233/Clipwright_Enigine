"""C7: 代理工作流 — switch_to_full 还原代理路径测试。"""

from __future__ import annotations

import json
from pathlib import Path

from clipwright.services.proxy import ProxyGenerator


class TestProxySwitch:
    def test_switch_to_proxy_and_back(self, tmp_path: Path) -> None:
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"x")
        proxy = tmp_path / "clip_proxy_720p.mp4"
        proxy.write_bytes(b"x")

        tl = {
            "tracks": [{
                "clips": [
                    {"id": "c1", "asset_id": str(src)},
                    {"id": "c2", "asset_id": "/data/other.mp4"},
                ],
            }],
        }
        # 代理化：存在的代理文件被替换
        switched = ProxyGenerator.switch_to_proxy(json.loads(json.dumps(tl)))
        assert switched["tracks"][0]["clips"][0]["asset_id"] == str(proxy)
        # 不存在的代理候选保持原样
        assert switched["tracks"][0]["clips"][1]["asset_id"] == "/data/other.mp4"

        # 还原：代理路径恢复为原片
        restored = ProxyGenerator.switch_to_full(switched)
        assert restored["tracks"][0]["clips"][0]["asset_id"] == str(src)
        # 非代理路径不受影响
        assert restored["tracks"][0]["clips"][1]["asset_id"] == "/data/other.mp4"

    def test_switch_to_full_keeps_non_proxy_paths(self) -> None:
        tl = {"tracks": [{"clips": [{"id": "c1", "asset_id": "/data/normal.mp4"}]}]}
        out = ProxyGenerator.switch_to_full(tl)
        assert out["tracks"][0]["clips"][0]["asset_id"] == "/data/normal.mp4"

    def test_switch_to_full_ignores_proxy_prefix_paths(self) -> None:
        tl = {"tracks": [{"clips": [{"id": "c1", "asset_id": "proxy_virtual/clip.mp4"}]}]}
        out = ProxyGenerator.switch_to_full(tl)
        assert out["tracks"][0]["clips"][0]["asset_id"] == "proxy_virtual/clip.mp4"
