#!/usr/bin/env python3
"""describe_image.py — 视觉转述 CLI（Agent 工具链，非 ClipWright 产品功能）。

仅使用 Python 标准库（urllib / base64 / json），把本地图片提交给视觉大模型，
返回中文结构化描述。用法与故障排查见 .opencode/README.md。

用法:
    python describe_image.py <image_path> [--prompt "提示词"] [--json]
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "https://api.llm-token.cn/v1/chat/completions"
MODEL = "qwen3.8-max"
# 默认 key（文档化位置：仅此脚本一处）；可通过环境变量 VISION_API_KEY 覆盖。
DEFAULT_API_KEY = "sk-TYPBqk8fLnsUNrfce2cv9zF8EmsMsse3f5mgB1QfdsVf7bHD"
TIMEOUT_SECONDS = 120

# 与 .opencode/scripts/image-prompt.md 内容保持一致的内嵌副本（文件缺失时兜底）。
FALLBACK_SYSTEM_PROMPT = """你是一名专业的图片内容转述员。请用中文对用户提供的图片进行全面、客观、结构化的描述，输出 Markdown 格式：

1. **画面概览**：一句话概括图片主要内容。
2. **主体细节**：主体是什么、状态、动作、显著特征。
3. **背景与环境**：背景内容、场景类型、光线、氛围。
4. **文字内容**：图中出现的任何文字，逐字转写（若无则注明"无文字"）。
5. **色彩与构图**：主色调、构图方式、视觉重点。
6. **推测意图**：图片可能的用途或传达意图。

要求：
- 只描述图片中真实存在的内容，不编造、不臆测缺失的细节。
- 使用简洁的中文，善用列表与标题，便于后续处理。
- 若图片无法读取、内容模糊或为空，请如实说明。
"""

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
}


def load_system_prompt() -> str:
    """优先读取同目录下 image-prompt.md，缺失或为空时用内嵌副本。"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(script_dir, "image-prompt.md")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            return content
    except OSError:
        pass
    return FALLBACK_SYSTEM_PROMPT


def mime_for(image_path: str) -> str:
    ext = os.path.splitext(image_path)[1].lower()
    return MIME_BY_EXT.get(ext, "application/octet-stream")


def build_payload(image_path: str, user_prompt: str, system_prompt: str) -> dict:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    mime = mime_for(image_path)
    data_uri = "data:{};base64,{}".format(mime, b64)
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    }


def call_api(payload: dict, api_key: str) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(api_key),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError("API HTTP {}: {}".format(e.code, detail)) from e
    except urllib.error.URLError as e:
        raise RuntimeError("网络错误：{}".format(e.reason)) from e
    except TimeoutError as e:
        raise RuntimeError("请求超时（>{:d}s）".format(TIMEOUT_SECONDS)) from e


def extract_content(resp: dict) -> str:
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            "响应缺少内容字段：{}".format(json.dumps(resp, ensure_ascii=False))
        ) from None


def main() -> int:
    # 固定 UTF-8 输出，避免 Windows 控制台按 ANSI 代码页（GBK）写 stdout/stderr 造成乱码。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(
        description="使用视觉大模型将本地图片转述为中文描述（Agent 工具链，非产品功能）。"
    )
    parser.add_argument("image_path", help="要分析的图片文件路径")
    parser.add_argument(
        "--prompt",
        default="请详细描述这张图片。",
        help="附加的用户文本提示（默认：请详细描述这张图片。）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 OpenAI 接口的原始 JSON 响应",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.image_path):
        print("错误：图片文件不存在或不可读：{}".format(args.image_path), file=sys.stderr)
        return 2

    api_key = os.environ.get("VISION_API_KEY") or DEFAULT_API_KEY

    try:
        system_prompt = load_system_prompt()
        payload = build_payload(args.image_path, args.prompt, system_prompt)
        resp = call_api(payload, api_key)
        if args.json:
            print(json.dumps(resp, ensure_ascii=False, indent=2))
        else:
            print(extract_content(resp))
        return 0
    except RuntimeError as e:
        print("describe_image 失败：{}".format(e), file=sys.stderr)
        return 1
    except OSError as e:
        print("describe_image 失败：读取图片出错 {}".format(e), file=sys.stderr)
        return 1
    except Exception as e:  # 兜底，保证任何异常都非零退出
        print("describe_image 失败（未预期错误）：{}".format(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
