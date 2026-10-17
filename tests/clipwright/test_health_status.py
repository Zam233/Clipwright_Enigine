"""`/health` 聚合判定回归测试。

背景（真 bug，非我引入，但由"检查一键启动脚本"实测暴露）：

`/health` 把 `components` 里**所有**值都拿去判 `v == "ok"`，而 `components["service"]`
是服务**名字**（"clipwright-engine"）、`queue_running`/`queue_pending` 是**计数**。
它们恒不等于 `"ok"` → `all_ok` 永远为 `False` → **接口永远返回 `degraded`**。

为什么这很重要：`/health` 是就绪探针（start.ps1 等它就绪、监控/容器编排也读它）。
一个"永远 degraded"的健康检查等于没有健康检查——真出故障时也分辨不出来，
而且我新加在 start.ps1 里的"后端已就绪"提示会与实际语义不符。

为什么测试难写：端点内部会真去探 ffmpeg / Hyperframes（带缓存 + 后台线程）。
这里用 `monkeypatch` 把两个探针的底层函数换掉，并轮询等后台线程回填缓存，
避免依赖机器上装没装 ffmpeg。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.services import async_util


async def _wait_cached(probe, expected: bool, timeout: float = 5.0) -> None:
    """轮询等后台线程把缓存刷成 expected（探针命中缓存后不再重复 spawn）。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if probe.get_sync() is expected or probe.get_sync() == expected:
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"探针缓存未在 {timeout}s 内变成 {expected!r}")


@pytest.fixture
def healthy_probes(monkeypatch):
    """把 ffmpeg / Hyperframes 探针强制为"可用"，测完恢复真实探测函数。"""
    import clipwright.main as m
    from clipwright.animation import hyperframes_renderer as hf

    ff_probe = async_util._PROBES["ffmpeg"]
    hf_probe = async_util._PROBES["hyperframes"]
    real_ff, real_hf = ff_probe._fn, hf_probe._fn

    monkeypatch.setattr(ff_probe, "_fn", lambda: True, raising=False)
    monkeypatch.setattr(hf_probe, "_fn", lambda: True, raising=False)
    ff_probe._expiry = 0.0
    hf_probe._expiry = 0.0
    yield m
    # 还回真实函数并让缓存失效，避免影响其它测试对真实环境的判断
    ff_probe._fn, hf_probe._fn = real_ff, real_hf
    ff_probe._expiry = 0.0
    hf_probe._expiry = 0.0


@pytest.fixture
def mongo_ok(monkeypatch):
    """Mongo 已连接 + LLM 有 key —— 这两项在 .env 里可直接判定，不需真连库。"""
    from clipwright.context import mongo as mongo_ctx
    from clipwright.config import settings
    monkeypatch.setattr(type(mongo_ctx), "is_connected", property(lambda _self: True), raising=False)
    monkeypatch.setattr(settings, "llm_api_key", "test-key", raising=False)


async def test_health_is_ok_when_every_component_is_ok(healthy_probes, mongo_ok):
    """全绿时必须返回 ok —— 这条在修 `service` 误判前是失败的。"""
    m = healthy_probes
    await _wait_cached(async_util._PROBES["ffmpeg"], True)
    await _wait_cached(async_util._PROBES["hyperframes"], True)

    body = await m.health()
    assert body["status"] == "ok", f"所有组件都 ok 却报 degraded：{body}"
    # 非状态字段仍然如实回传，只是不参与判定
    assert body["service"] == "clipwright-engine"
    assert "queue_running" in body and "queue_pending" in body


async def test_health_is_degraded_when_mongo_is_down(healthy_probes, monkeypatch):
    """Mongo 断连是**真**不健康，不能被聚合逻辑放过（作为就绪探针时不许假绿）。"""
    m = healthy_probes
    from clipwright.context import mongo as mongo_ctx
    monkeypatch.setattr(type(mongo_ctx), "is_connected", property(lambda _self: False), raising=False)
    await _wait_cached(async_util._PROBES["ffmpeg"], True)
    await _wait_cached(async_util._PROBES["hyperframes"], True)

    body = await m.health()
    assert body["mongodb"] == "disconnected"
    assert body["status"] == "degraded"


async def test_health_reports_ffmpeg_missing(healthy_probes, mongo_ok):
    """ffmpeg 不可用也必须 degraded（渲染/抽帧/建索引全依赖它）。"""
    m = healthy_probes
    probe = async_util._PROBES["ffmpeg"]
    probe._fn = lambda: False
    probe._expiry = 0.0
    await _wait_cached(probe, False)

    body = await m.health()
    assert body["ffmpeg"] == "not_found"
    assert body["status"] == "degraded"
