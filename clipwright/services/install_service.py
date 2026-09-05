"""市场包安装服务（P4-4B）— 下载/校验/解包/注册，失败可回滚。"""

from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

from clipwright.config import logger, settings

MAX_TARBALL_BYTES = 200 * 1024 * 1024


def _safe_extract(data: bytes, dest: Path, required_manifest: str) -> None:
    """安全解包：拒绝路径穿越条目；要求存在必需清单文件。"""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"非法路径条目: {member.name}")
        names = tf.getnames()
        if not any(n.endswith(required_manifest) for n in names):
            raise ValueError(f"缺少必需清单文件 {required_manifest}")
        tf.extractall(dest)


def install_plugin_from_bytes(data: bytes, plugin_id: str, expected_sha256: str = "") -> Path:
    """安装插件包：解包到临时目录 → 校验 → 原子移动到 plugins/{id} → 注册。

    P2: 支持 expected_sha256 校验（下载端提供）；load 失败时自动回滚删除 plugin_dir。
    """
    if len(data) > MAX_TARBALL_BYTES:
        raise ValueError("包大小超限")
    # P2: sha256 完整性校验
    if expected_sha256:
        import hashlib
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            raise ValueError(
                f"sha256 校验失败: expected={expected_sha256[:16]}... actual={actual[:16]}...")
    plugin_dir = Path(settings.plugin_dir) / plugin_id
    if plugin_dir.exists():
        raise ValueError(f"插件 {plugin_id} 已安装")

    tmp = Path(tempfile.mkdtemp(prefix="market_plugin_"))
    try:
        _safe_extract(data, tmp, "plugin.yaml")
        if (tmp / "plugin.yaml").exists():
            # 规范结构：包根即插件目录
            shutil.move(str(tmp), str(plugin_dir))
            tmp = plugin_dir  # 已移动，无需清理
        else:
            raise ValueError("plugin.yaml 必须位于包根目录")

        from clipwright.plugins.loader import PluginLoader

        loader = PluginLoader(plugin_dir=Path(settings.plugin_dir), data_dir=settings.plugin_data_dir)
        loaded = loader.load(plugin_id)
        if loaded is None:
            # 加载失败 → P2 回滚（含 loader 内部注册的残留清理）
            shutil.rmtree(plugin_dir, ignore_errors=True)
            raise ValueError(f"插件 {plugin_id} 加载失败")
        logger.info("市场插件安装成功: %s", plugin_id)
        return plugin_dir
    except Exception:
        # P2: 回滚——即使 tmp == plugin_dir（已 move）也删除，不留坏插件卡死启动
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)
        if tmp.exists() and tmp != plugin_dir:
            shutil.rmtree(tmp, ignore_errors=True)
        raise


def install_persona_from_bytes(data: bytes, persona_id: str) -> Path:
    """安装 Persona 包：解包 → 校验 persona.yaml 可解析 → 移动到 personas/{id}。"""
    if len(data) > MAX_TARBALL_BYTES:
        raise ValueError("包大小超限")
    persona_root = Path(settings.persona_dir)
    dest = persona_root / persona_id
    if dest.exists():
        raise ValueError(f"Persona {persona_id} 已存在")

    tmp = Path(tempfile.mkdtemp(prefix="market_persona_"))
    try:
        _safe_extract(data, tmp, "persona.yaml")
        manifest_file = tmp / "persona.yaml"
        if not manifest_file.exists():
            raise ValueError("persona.yaml 必须位于包根目录")

        # 用 schema 校验可解析（失败则拒绝安装）
        from clipwright.persona.loader import load_persona_manifest
        load_persona_manifest(tmp)

        persona_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), str(dest))
        logger.info("市场 Persona 安装成功: %s", persona_id)
        return dest
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


async def install_plugin(package_id: str, version: str = "") -> dict:
    """从市场下载并安装插件。"""
    from clipwright.services import market_client

    data, _ = await market_client.download_plugin(package_id, version)
    path = await _run_in_thread(install_plugin_from_bytes, data, package_id)
    return {"status": "ok", "plugin_id": package_id, "path": str(path)}


async def install_persona(package_id: str, version: str = "") -> dict:
    """从市场下载并安装 Persona。"""
    from clipwright.services import market_client

    data, _ = await market_client.download_persona(package_id, version)
    path = await _run_in_thread(install_persona_from_bytes, data, package_id)
    return {"status": "ok", "persona_id": package_id, "path": str(path)}


async def _run_in_thread(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)
