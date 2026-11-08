"""真机冒烟：验证「用户创作提示词」真的传到了模型（最小成本）。

只做一件事：把 `creative_prompt_block()` 拼进 system prompt，让模型用一句话复述它必须遵守的
规范，然后断言回复里出现了提示词里的关键短语。

- **不代表**成片质量，也不代表结构阶段跑通；它证明的是"提示词到达模型且被理解"这一环。
- 需要已配置 LLM（`settings.llm_api_key` / `base_url`），未配置则跳过并不算失败。

用法::

    python scripts/smoke_llm_prompt.py
    python scripts/smoke_llm_prompt.py --prompt "版画质感，三墨套印，硬切为主"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.config import settings  # noqa: E402
from clipwright.services.creative_prompt import creative_prompt_block  # noqa: E402
from clipwright.services.llm import LLMService  # noqa: E402

DEFAULT_PROMPT = "版画质感，三墨套印，硬切为主；每句歌词配一个语义相符的画面。不要花哨转场。"
KEY_PHRASES = ("版画", "三墨", "硬切", "花哨")


async def run(prompt: str) -> dict:
    block = creative_prompt_block(
        {"creative_prompt": prompt, "creative_avoid": ["花哨转场"]}
    )
    system = (
        "你是视频导演。下面这段是用户对本片的规范，必须遵守。\n\n"
        f"{block}\n\n"
        "请用不超过 40 个字复述你必须遵守的要点，不要解释。"
    )
    llm = LLMService()
    reply = await llm.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "开始复述。"},
        ],
        temperature=0.2,
        max_tokens=200,
    )
    text = str(reply or "").strip()
    hits = [phrase for phrase in KEY_PHRASES if phrase in text]
    return {
        "ok": bool(hits),
        "reply": text[:300],
        "matched_phrases": hits,
        "prompt_block_chars": len(block),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创作提示词到达模型的真机冒烟")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not (settings.llm_api_key or settings.llm_base_url):
        print(json.dumps({"ok": False, "skipped": True, "reason": "未配置 LLM"}, ensure_ascii=False))
        return 0  # 未配置不算失败，但也不谎报通过

    try:
        result = asyncio.run(run(args.prompt))
    except Exception as exc:  # noqa: BLE001 - CLI 边界
        print(json.dumps({"ok": False, "error": str(exc)[:300]}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["reply"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
