import { describe, it, expect, vi } from 'vitest';
import { collectSnapTargets, applySnap } from './snap';
import { makeLayout } from './types';
import type { Track } from '@/types/timeline';

vi.mock('@/stores/settingsStore', () => ({
  useSettingsStore: {
    getState: () => ({ snapToGrid: false, snapGridSec: 1 }),
  },
}));

const layout = makeLayout(1000, 400, 100, 0, 0); // 100 px per second

function mkTrack(clips: { id: string; start: number; dur: number }[]): Track {
  return {
    id: 'tr', name: 'V1', kind: 'video', index: 0, locked: false, muted: false,
    clips: clips.map((c) => ({
      id: c.id, kind: 'video' as const, asset_id: '', track_id: 'tr',
      start_sec: c.start, duration_sec: c.dur, source_offset_sec: 0,
      speed: 1, volume: 1, opacity: 1, keyframes: [], metadata: {},
    })),
  };
}

describe('collectSnapTargets', () => {
  it('includes zero, playhead, markers, and clip edges', () => {
    const tracks = [mkTrack([{ id: 'a', start: 2, dur: 3 }])];
    const targets = collectSnapTargets(tracks, new Set(), 10, [{ time: 5 }]);
    expect(targets).toContain(0);
    expect(targets).toContain(10); // playhead
    expect(targets).toContain(5); // marker
    expect(targets).toContain(2); // clip start
    expect(targets).toContain(5); // clip end
  });

  it('excludes dragged clip edges', () => {
    const tracks = [mkTrack([{ id: 'a', start: 2, dur: 3 }, { id: 'b', start: 8, dur: 2 }])];
    const targets = collectSnapTargets(tracks, new Set(['a']), 0, []);
    expect(targets).not.toContain(2);
    expect(targets).not.toContain(5);
    expect(targets).toContain(8); // other clip still present
  });
});

describe('applySnap', () => {
  it('snaps a candidate edge to a nearby target within threshold', () => {
    // candidate edge at 2.0, moving by rawDelta 0.05 -> 2.05, target at 2.04
    const res = applySnap([2.0], 0.05, [2.04], layout, 8);
    expect(res.deltaTime).toBeCloseTo(0.04, 5);
    expect(res.snapX).not.toBeNull();
  });

  it('does not snap beyond threshold', () => {
    // threshold 8px = 0.08s at 100px/s; target 0.5s away
    const res = applySnap([2.0], 0.0, [2.5], layout, 8);
    expect(res.deltaTime).toBe(0.0);
    expect(res.snapX).toBeNull();
  });

  it('chooses the closest target', () => {
    const res = applySnap([2.0], 0.0, [2.02, 2.05], layout, 20);
    // should snap to 2.02 (closer)
    expect(res.deltaTime).toBeCloseTo(0.02, 5);
  });
});
