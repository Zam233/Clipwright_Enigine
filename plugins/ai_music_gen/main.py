"""AI 文生音乐插件 — 从情绪/风格描述生成免版税 BGM / 人声歌曲。
默认接入火山引擎豆包音乐大模型（「音视频理解与处理」服务，AK/SK V4 签名）：
纯音乐 GenBGM / 人声歌曲 GenSongV4 提交任务 + QuerySong 轮询；
保留 Suno 分支可切回（Suno 无官方公开 API，默认不再使用）。

任务成功的音频 URL 有效期有限，产物统一下载落地到 PluginData/assets/
（下载前经 assert_public_url 防 SSRF），返回本地路径 + 远端 URL。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from clipwright.plugins import CapabilityPlugin
from clipwright.security import assert_public_url
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger
from clipwright.material.registry import MaterialRegistry
from clipwright.plugins.generated_source import make_generated_source, record_generated

# 火山引擎 OpenAPI（音视频理解与处理 / 豆包音乐大模型）
# 生成纯音乐：GenBGM（预付费）/ GenBGMForTime（后付费）
# 生成人声歌曲：GenSongV4（预付费）/ GenSongForTime（后付费）
# 查询任务：QuerySong（Body {"TaskID"}，Result.Status: 0 等待/1 处理/2 成功/3 失败）
_MUSIC_HOST = "open.volcengineapi.com"
_MUSIC_REGION = "cn-beijing"
_MUSIC_SERVICE = "imagination"
_MUSIC_VERSION = "2024-08-12"

# 工具参数名 → GenSongV4 请求字段名
_SONG_PASSTHROUGH = {
    "model_version": "ModelVersion",
    "genre": "Genre",
    "mood": "Mood",
    "gender": "Gender",
    "lang": "Lang",
}


def _music_url(action: str) -> str:
    return "https://{}/?Action={}&Version={}".format(_MUSIC_HOST, action, _MUSIC_VERSION)


def _sign_v4(
    ak: str,
    sk: str,
    action: str,
    body_bytes: bytes,
    now: datetime,
    region: str = _MUSIC_REGION,
    service: str = _MUSIC_SERVICE,
    host: str = _MUSIC_HOST,
) -> dict[str, str]:
    """火山引擎 OpenAPI V4 签名（HMAC-SHA256），返回 POST 请求所需 headers。

    规范请求 = "POST /" + Action/Version 查询串 + 排序签名头 + body SHA256；
    派生密钥链 = HMAC(SK, 日期) → region → service → "request"。
    """
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    canonical_headers = (
        "content-type:application/json; charset=utf-8\n"
        "host:" + host + "\n"
        "x-content-sha256:" + body_hash + "\n"
        "x-date:" + x_date + "\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    # 注意：canonical_headers 每行自带 \n，与 signed_headers 之间还需一个空行（V4 规范）
    canonical_request = "POST\n/\nAction={}&Version={}\n{}\n{}\n{}".format(
        action, _MUSIC_VERSION, canonical_headers, signed_headers, body_hash,
    )
    scope = "{}/{}/{}/request".format(x_date[:8], region, service)
    string_to_sign = "HMAC-SHA256\n{}\n{}\n{}".format(
        x_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    )
    key = hmac.new(sk.encode("utf-8"), x_date[:8].encode("utf-8"), hashlib.sha256).digest()
    key = hmac.new(key, region.encode("utf-8"), hashlib.sha256).digest()
    key = hmac.new(key, service.encode("utf-8"), hashlib.sha256).digest()
    key = hmac.new(key, b"request", hashlib.sha256).digest()
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-Content-Sha256": body_hash,
        "X-Date": x_date,
        "Authorization": "HMAC-SHA256 Credential={}/{}, SignedHeaders={}, Signature={}".format(
            ak, scope, signed_headers, signature,
        ),
    }


class AIMusicGenTool(BaseTool):
    name = "ai_music_generate"
    agent_callable = True
    description = "从情绪/风格描述生成免版税背景音乐（纯音乐）或人声歌曲"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "音乐描述（纯音乐仅支持中文）"},
            "instrumental": {"type": "boolean", "default": True, "description": "True=纯音乐 BGM，False=人声歌曲"},
            "lyrics": {"type": "string", "description": "歌词（人声歌曲可选，与 prompt 二选一，优先级更高）"},
            "duration_sec": {"type": "number", "default": 60, "description": "时长秒：纯音乐 [30,120]，人声歌曲 [30,240]"},
            "genre": {"type": "string", "description": "曲风（人声歌曲可选）"},
            "mood": {"type": "string", "description": "情绪（人声歌曲可选）"},
            "model_version": {"type": "string", "description": "歌曲模型版本：v4.0 / v4.3 / v5.0（可选）"},
        },
        "required": ["prompt"],
    }

    def __init__(
        self,
        provider: str = "volcengine",
        api_key: str = "",
        access_key: str = "",
        secret_key: str = "",
        billing_mode: str = "prepaid",
        poll_interval: float = 5.0,
        poll_timeout: float = 480.0,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._access_key = access_key
        self._secret_key = secret_key
        self._billing_mode = billing_mode
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    def is_available(self) -> bool:
        """按当前 provider 检查凭据是否配置（配置优先、env 兜底）。

        volcengine 需要 AK/SK 成对；兼容 VOLC_ 与 VOLCENGINE_ 两套环境变量前缀。
        """
        if self._provider == "volcengine":
            ak = self._access_key or os.environ.get("VOLC_ACCESS_KEY", "")                 or os.environ.get("VOLCENGINE_ACCESS_KEY", "")
            sk = self._secret_key or os.environ.get("VOLC_SECRET_KEY", "")                 or os.environ.get("VOLCENGINE_SECRET_KEY", "")
            return bool(ak and sk)
        if self._provider == "suno":
            return bool(self._api_key or os.environ.get("SUNO_API_KEY", ""))
        return False

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少 prompt"}
        try:
            if self._provider == "volcengine":
                return await self._gen_volcengine(kwargs)
            elif self._provider == "suno":
                return await self._gen_suno(prompt, kwargs)
            return {"success": False, "error": f"未知 provider: {self._provider}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 火山引擎豆包音乐大模型（提交任务 + QuerySong 轮询） ──

    async def _gen_volcengine(self, params: dict) -> dict:
        ak = (self._access_key or os.environ.get("VOLC_ACCESS_KEY", "")
              or os.environ.get("VOLCENGINE_ACCESS_KEY", ""))
        sk = (self._secret_key or os.environ.get("VOLC_SECRET_KEY", "")
              or os.environ.get("VOLCENGINE_SECRET_KEY", ""))
        if not ak or not sk:
            return {"success": False,
                    "error": "VOLC_ACCESS_KEY / VOLC_SECRET_KEY 未配置（或经插件配置 access_key/secret_key）"}
        prompt = str(params.get("prompt", ""))
        instrumental = bool(params.get("instrumental", True))
        if instrumental:
            # 纯音乐：Text 必填（官方仅支持中文）；v5.0 时长有效区间 [30,120]
            action = "GenBGMForTime" if self._billing_mode == "postpaid" else "GenBGM"
            payload: dict[str, Any] = {
                "Text": prompt,
                "Duration": max(30, min(120, int(params.get("duration_sec", 60) or 60))),
                "Version": "v5.0",
            }
        else:
            # 人声歌曲：Lyrics 与 Prompt 二选一（Lyrics 优先）；时长 [30,240]
            action = "GenSongForTime" if self._billing_mode == "postpaid" else "GenSongV4"
            payload = {}
            lyrics = str(params.get("lyrics", "") or "")
            if lyrics:
                payload["Lyrics"] = lyrics
            else:
                payload["Prompt"] = prompt
            for param_key, field in _SONG_PASSTHROUGH.items():
                if params.get(param_key):
                    payload[field] = str(params[param_key])
            duration = int(params.get("duration_sec", 0) or 0)
            if duration > 0:
                payload["Duration"] = max(30, min(240, duration))
            payload["VodFormat"] = "mp3"
        return await self._submit_and_poll(ak, sk, action, payload, instrumental)

    async def _submit_and_poll(self, ak: str, sk: str, action: str, payload: dict,
                               instrumental: bool) -> dict:
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._poll_timeout
        async with httpx.AsyncClient(timeout=60) as c:
            headers = _sign_v4(ak, sk, action, body_bytes, datetime.now(timezone.utc))
            resp = await c.post(_music_url(action), headers=headers, content=body_bytes)
            resp.raise_for_status()
            rdata = resp.json()
            task_id = self._extract(rdata, "TaskID")
            if not task_id:
                return {"success": False,
                        "error": "创建任务未返回 TaskID：" + json.dumps(rdata, ensure_ascii=False)[:200]}
            while loop.time() < deadline:
                await asyncio.sleep(self._poll_interval)
                qbody = json.dumps({"TaskID": task_id}, ensure_ascii=False).encode("utf-8")
                qheaders = _sign_v4(ak, sk, "QuerySong", qbody, datetime.now(timezone.utc))
                # 批5：瞬态错误不再废弃已付费任务（连续失败有上限兜底）
                try:
                    q = await c.post(_music_url("QuerySong"), headers=qheaders, content=qbody)
                    q.raise_for_status()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (429, 500, 502, 503, 504):
                        continue
                    return {"success": False, "error": f"查询任务 HTTP {e.response.status_code}",
                            "task_id": task_id}
                except httpx.HTTPError:
                    continue
                qdata = q.json()
                result = (qdata.get("Result") or {})
                if not result:
                    err = (qdata.get("ResponseMetadata") or {}).get("Error") or {}
                    if not err:
                        continue  # 偶发空 Result（最终一致）→ 视为未就绪继续轮询
                    reason = " ".join(str(err.get(k, "")) for k in ("Code", "Message")).strip()
                    return {"success": False, "error": "查询任务失败: " + reason, "task_id": task_id}
                status = result.get("Status")
                if status == 2:
                    detail = result.get("SongDetail") or {}
                    audio_url = str(detail.get("AudioUrl", ""))
                    if not audio_url:
                        return {"success": False, "error": "任务成功但未返回 AudioUrl", "task_id": task_id}
                    local_path = await self._download(c, audio_url)
                    record_generated("ai_music_gen",
                                     prompt=str(payload.get("Text") or payload.get("Prompt")
                                                or payload.get("Lyrics") or ""),
                                     type="audio", path=local_path, duration_sec=detail.get("Duration"))
                    return {
                        "success": True, "url": local_path, "path": local_path,
                        "remote_url": audio_url, "task_id": task_id,
                        "duration_sec": detail.get("Duration"),
                        "instrumental": instrumental,
                        "provider": "volcengine",
                    }
                if status == 3:
                    fr = result.get("FailureReason") or {}
                    reason = " ".join(str(fr.get(k, "")) for k in ("Code", "Msg")).strip()
                    return {"success": False, "error": "生成失败: " + reason, "task_id": task_id}
            return {"success": False, "error": "生成超时", "task_id": task_id}

    @staticmethod
    def _extract(rdata: dict, field: str) -> str:
        """从成功响应取 Result.TaskID；带 ResponseMetadata.Error 或 Code!=0 时返回空串。"""
        err = (rdata.get("ResponseMetadata") or {}).get("Error")
        if err:
            return ""
        if rdata.get("Code") not in (0, None):
            return ""
        return str((rdata.get("Result") or {}).get(field, "") or "")

    @staticmethod
    async def _download(client: httpx.AsyncClient, audio_url: str) -> str:
        """下载生成音频到 PluginData/assets/（先做 SSRF 校验）。"""
        assert_public_url(audio_url)
        r = await client.get(audio_url)
        r.raise_for_status()
        out_dir = Path("PluginData/assets")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "ai_music_{}.mp3".format(uuid.uuid4().hex[:12])
        out.write_bytes(r.content)
        return str(out)

    async def _gen_suno(self, prompt: str, params: dict) -> dict:
        key = self._api_key or os.environ.get("SUNO_API_KEY", "")
        if not key:
            return {"success": False, "error": "SUNO_API_KEY 未配置"}
        duration = int(params.get("duration_sec", 30))
        async with httpx.AsyncClient(timeout=120) as c:
            resp = await c.post("https://api.suno.ai/v1/generate",
                headers={"Authorization": f"Bearer {key}"},
                json={"prompt": prompt, "duration": duration, "make_instrumental": True})
            resp.raise_for_status()
            data = resp.json()
            audio_url = data.get("audio_url", "")
            if audio_url:
                # 批5：统一落地 + 登记（与 volcengine 分支行为一致）
                assert_public_url(audio_url)
                r = await c.get(audio_url)
                r.raise_for_status()
                from clipwright.plugins.generated_source import record_generated
                out_dir = Path("PluginData/assets")
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / ("ai_music_" + uuid.uuid4().hex[:12] + ".mp3")
                out.write_bytes(r.content)
                record_generated("ai_music_gen", prompt=prompt, type="audio",
                                 path=str(out), duration_sec=duration)
                return {"success": True, "url": str(out), "path": str(out),
                        "remote_url": audio_url, "provider": "suno", "duration_sec": duration}
            return {"success": False, "error": "Suno 未返回音频"}


class AIMusicGenPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="ai_music_gen", name="AI Music Generation", version="1.1.0",
        kind=PluginKind.CAPABILITY,
        description="Generate royalty-free BGM/songs via Volcengine Doubao Music (default) / Suno",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        cfg = self.config or {}
        tool = AIMusicGenTool(
            provider=cfg.get("provider", "volcengine"),
            api_key=cfg.get("api_key", ""),
            access_key=cfg.get("access_key", ""),
            secret_key=cfg.get("secret_key", ""),
            billing_mode=cfg.get("billing_mode", "prepaid"),
            poll_interval=float(cfg.get("poll_interval_sec", 5)),
            poll_timeout=float(cfg.get("poll_timeout_sec", 480)),
        )
        ToolRegistry.register(tool, plugin_id=self.manifest.id)
        MaterialRegistry.register(
            make_generated_source("ai_music_gen", "AI 生成音乐", "audio"),
            plugin_id=self.manifest.id)
        logger.info("[AIMusicGen] Tool + MaterialSource 已注册 (provider=%s, billing=%s)",
                    tool._provider, tool._billing_mode)

    def shutdown(self) -> None:
        # 批7：disable 时真正移除工具与素材源（旧实现 pass → 禁用后
        # 工具仍可用、能力概览仍播报）
        try:
            ToolRegistry.unregister("ai_music_generate")
        except Exception:
            pass
        try:
            from clipwright.material.registry import MaterialRegistry
            MaterialRegistry.unregister("ai_music_gen")
        except Exception:
            pass


__all__ = ["AIMusicGenPlugin"]
