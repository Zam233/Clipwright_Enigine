import { describe, it, expect } from 'vitest';
import { nextPlaybackSpeed } from './PreviewPanel';

const SPEEDS = [0.5, 1, 1.5, 2];

describe('nextPlaybackSpeed — play-speed cycle (F6)', () => {
  it('starts from the first speed when current is NOT in the list (bug: 1.25 → 0.5, not 1)', () => {
    expect(nextPlaybackSpeed(1.25, SPEEDS)).toBe(0.5);
  });

  it('advances from 0.5 to 1', () => {
    expect(nextPlaybackSpeed(0.5, SPEEDS)).toBe(1);
  });

  it('wraps from the last speed (2) back to 0.5', () => {
    expect(nextPlaybackSpeed(2, SPEEDS)).toBe(0.5);
  });

  it('advances from 1 to 1.5', () => {
    expect(nextPlaybackSpeed(1, SPEEDS)).toBe(1.5);
  });

  it('returns current unchanged for an empty speeds list', () => {
    expect(nextPlaybackSpeed(1.25, [])).toBe(1.25);
    expect(nextPlaybackSpeed(1, [])).toBe(1);
  });
});
