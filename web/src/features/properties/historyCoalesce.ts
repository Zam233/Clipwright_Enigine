// Per-clip history push coalescing.
// Rapid successive edits to the SAME clip (slider drag / typing) collapse into a
// single undo point, while switching clips always opens a fresh window so editing
// clip A then clip B produces two undo points instead of one.
// 按片段作用域的撤销快照合并：同一片段的连续快速编辑合并为一个撤销点，
// 切换片段时各自独立开窗，保证「编辑 A 再编辑 B」产生两个撤销点。

export const HISTORY_COALESCE_WINDOW_MS = 600;

const lastPushByClip = new Map<string, number>();

/**
 * Returns true when a history push for `clipId` should be recorded now.
 * A push within `windowMs` of the last accepted push for the same clip is
 * coalesced (skipped). Other clips are unaffected.
 */
export function shouldPush(
  clipId: string,
  now: number = Date.now(),
  windowMs: number = HISTORY_COALESCE_WINDOW_MS,
): boolean {
  const last = lastPushByClip.get(clipId) ?? 0;
  if (now - last < windowMs) return false;
  lastPushByClip.set(clipId, now);
  return true;
}

/** Clear the internal per-clip timestamp map (tests / clip selection reset). */
export function clearHistoryCoalesce(): void {
  lastPushByClip.clear();
}
