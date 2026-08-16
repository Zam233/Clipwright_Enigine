---
description: Describe a local image with the vision LLM (returns a Chinese description). Fallback slash-command for the describe_image toolchain.
---

Use the vision transcription script to describe the image, then relay the result.

1. Resolve the repo root (current working directory) and verify the image path exists.
2. Run:

   ```
   python .opencode/scripts/describe_image.py <image_path> [--prompt "中文提示"] [--json]
   ```

   - `<image_path>` is required; must be a readable image (png/jpg/jpeg/gif/webp).
   - `--prompt` optionally overrides the default "请详细描述这张图片。".
   - `--json` prints the raw OpenAI-compatible JSON response instead of the extracted text.
   - The script uses a 120s timeout and exits non-zero on any failure.

3. Return the script's stdout to the user verbatim (it is already a Chinese structured Markdown description).
4. If the script fails, relay the exact stderr message — it is the source of truth for the failure (missing file, quota/balance, network, timeout, etc.).

Environment override: set `VISION_API_KEY` to a custom gateway key before running (default key is embedded in the script).

Note: this is Agent toolchain, not a ClipWright product feature — do not reference it in product docs.
