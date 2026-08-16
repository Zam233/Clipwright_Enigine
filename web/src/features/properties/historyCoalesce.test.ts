import { describe, it, expect, beforeEach } from 'vitest';
import { shouldPush, clearHistoryCoalesce, HISTORY_COALESCE_WINDOW_MS } from './historyCoalesce';

describe('shouldPush — per-clip history coalescing', () => {
  beforeEach(() => {
    clearHistoryCoalesce();
  });

  it('first push for a clip is allowed', () => {
    expect(shouldPush('A', 1000)).toBe(true);
  });

  it('immediate second push for the SAME clip within the window is coalesced', () => {
    expect(shouldPush('A', 1000)).toBe(true);
    expect(shouldPush('A', 1000 + HISTORY_COALESCE_WINDOW_MS - 1)).toBe(false);
  });

  it('switching to another clip immediately opens its own window (the bug being fixed)', () => {
    expect(shouldPush('A', 1000)).toBe(true);
    expect(shouldPush('B', 1001)).toBe(true);
  });

  it('same clip is allowed again after the window expires', () => {
    expect(shouldPush('A', 1000)).toBe(true);
    expect(shouldPush('A', 1000 + HISTORY_COALESCE_WINDOW_MS - 1)).toBe(false);
    expect(shouldPush('A', 1000 + HISTORY_COALESCE_WINDOW_MS + 1)).toBe(true);
  });
});
