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
#: 解包后的总字节与成员数上限（防压缩炸弹；压缩包大小上限挡不住高压缩比）。
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_MEMBERS = 5000


def _safe_extract(data: bytes, dest: Path, required_manifest: str) -> None:
    """安全解包：拒绝路径穿越/符号链接条目；要求存在必需清单文件。

    P1-14: 旧实现 (a) startswith 前缀比较可被 `tmp/abc` vs `tmp/abc-evil`
    绕过、(b) 未拒 symlink/hardlink 成员、(c) extractall 未用 filter="data"。

    B1：另按成员数与解包总字节限额（tar header 预检 + 实际写入计数），
    防止高压缩比包把磁盘写满。
    """
    dest_resolved = dest.resolve()
    total_bytes = 0
    members = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        all_members = tf.getmembers()
        if len(all_members) > MAX_MEMBERS:
            raise ValueError(f"压缩包成员过多（>{MAX_MEMBERS}）")
        for member in all_members:
            members += 1
            if member.issym() or member.islnk():
                raise ValueError(f"拒绝符号链接/硬链接条目: {member.name}")
            target = (dest / member.name).resolve()
            if not target.is_relative_to(dest_resolved):
                raise ValueError(f"非法路径条目: {member.name}")
            total_bytes += max(0, int(member.size or 0))
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise ValueError(
                    f"解包后总大小超限（>{MAX_EXTRACTED_BYTES} 字节）",
                )
        names = tf.getnames()
        if not any(n.endswith(required_manifest) for n in names):
            raise ValueError(f"缺少必需清单文件 {required_manifest}")
        # B1：按**实际写出字节**限额（header.size 可被伪造），分块复制而不是
        # 交给 extractall 一把梭。
        written = 0
        for member in all_members:
            target = dest / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = tf.extractfile(member)
            if source is None:  # 设备文件等：前面已拒绝，这里兜底
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    written += len(block)
                    if written > MAX_EXTRACTED_BYTES:
                        raise ValueError(
                            f"解包后总大小超限（>{MAX_EXTRACTED_BYTES} 字节）",
                        )
                    out.write(block)


def _validated_plugin_id(plugin_id: str) -> str:
    """安装路径的插件 id 必须先过统一校验，禁止 ``..``/盘符/绝对路径。

    API 端点的 `_validate_plugin_id` 不能替代离线安装函数自身的校验。
    """
    from clipwright.security import SecurityViolation, validate_id

    try:
        return validate_id(str(plugin_id or ""), "plugin_id")
    except SecurityViolation as e:
        raise ValueError(f"非法 plugin_id: {e}") from e


def install_plugin_from_bytes(data: bytes, plugin_id: str, expected_sha256: str = "") -> Path:
    """安装插件包：解包到临时目录 → 校验 → 原子移动到 plugins/{id} → 注册。

    P2: 支持 expected_sha256 校验（下载端提供）；load 失败时自动回滚删除 plugin_dir。
    """
    plugin_id = _validated_plugin_id(plugin_id)
    if len(data) > MAX_TARBALL_BYTES:
        raise ValueError("包大小超限")
    # sha256 完整性校验
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
        # 回滚——即使 tmp == plugin_dir（已 move）也删除，不留坏插件卡死启动
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)
        if tmp.exists() and tmp != plugin_dir:
            shutil.rmtree(tmp, ignore_errors=True)
        raise


def install_persona_from_bytes(data: bytes, persona_id: str, expected_sha256: str = "") -> Path:
    """安装 Persona 包：解包 → 校验 persona.yaml 可解析 → 移动到 personas/{id}。"""
    if len(data) > MAX_TARBALL_BYTES:
        raise ValueError("包大小超限")
    if expected_sha256:
        import hashlib

        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            raise ValueError(
                f"sha256 校验失败: expected={expected_sha256[:16]}... actual={actual[:16]}...")
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
    """从市场下载并安装插件（账号/市场已剥离，此入口不再可用）。"""
    raise RuntimeError("市场服务已剥离：请使用 install_plugin_from_bytes() 从本地包安装")


async def install_persona(package_id: str, version: str = "") -> dict:
    """从市场下载并安装 Persona（账号/市场已剥离，此入口不再可用）。"""
    raise RuntimeError("市场服务已剥离：请使用 install_persona_from_bytes() 从本地包安装")
