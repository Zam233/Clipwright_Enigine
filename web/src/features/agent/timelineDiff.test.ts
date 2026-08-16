import { describe, it, expect } from 'vitest';
import { computeTimelineDiff, mergeTimeline } from './timelineDiff';
import type { Timeline, Clip, Track, ClipKind } from '@/types/timeline';

function mkClip(id: string, over: Partial<Clip> = {}): Clip {
  return {
    id, kind: 'video', asset_id: `asset_${id}`, track_id: 'tr',
    start_sec: 0, duration_sec: 5, source_offset_sec: 0,
    speed: 1, volume: 1, opacity: 1, keyframes: [], metadata: {}, ...over,
  };
}

function mkTimeline(clips: Clip[]): Timeline {
  const track: Track = { id: 'tr', name: 'V1', kind: 'video', index: 0, locked: false, muted: false, clips };
  return { id: 'tl', width: 1920, height: 1080, fps: 30, duration_sec: 10, tracks: [track] };
}

function mkTrack(id: string, kind: ClipKind = 'video', index = 0, clips: Clip[] = []): Track {
  return { id, name: id, kind, index, locked: false, muted: false, clips };
}

function mkTracks(tracks: Track[]): Timeline {
  return { id: 'tl', width: 1920, height: 1080, fps: 30, duration_sec: 10, tracks };
}

describe('computeTimelineDiff', () => {
  it('detects added clips', () => {
    const current = mkTimeline([mkClip('a')]);
    const proposed = mkTimeline([mkClip('a'), mkClip('b', { start_sec: 5 })]);
    const diff = computeTimelineDiff(current, proposed);
    expect(diff.summary.added).toBe(1);
    expect(diff.addedClips[0].id).toBe('b');
  });

  it('detects removed clips', () => {
    const current = mkTimeline([mkClip('a'), mkClip('b')]);
    const proposed = mkTimeline([mkClip('a')]);
    const diff = computeTimelineDiff(current, proposed);
    expect(diff.summary.removed).toBe(1);
    expect(diff.removedClips[0].id).toBe('b');
  });

  it('detects modified clips with changed fields', () => {
    const current = mkTimeline([mkClip('a', { duration_sec: 5 })]);
    const proposed = mkTimeline([mkClip('a', { duration_sec: 8 })]);
    const diff = computeTimelineDiff(current, proposed);
    expect(diff.summary.modified).toBe(1);
    expect(diff.modifiedClips[0].fields).toContain('duration_sec');
  });

  it('ignores numerically-equal fields within epsilon', () => {
    const current = mkTimeline([mkClip('a', { start_sec: 1.0 })]);
    const proposed = mkTimeline([mkClip('a', { start_sec: 1.0001 })]);
    const diff = computeTimelineDiff(current, proposed);
    expect(diff.isEmpty).toBe(true);
  });

  it('reports empty when identical', () => {
    const tl = mkTimeline([mkClip('a'), mkClip('b', { start_sec: 5 })]);
    const diff = computeTimelineDiff(tl, JSON.parse(JSON.stringify(tl)));
    expect(diff.isEmpty).toBe(true);
  });
});

describe('mergeTimeline', () => {
  it('applies only accepted additions', () => {
    const current = mkTimeline([mkClip('a')]);
    const proposed = mkTimeline([mkClip('a'), mkClip('b', { start_sec: 5 })]);
    const diff = computeTimelineDiff(current, proposed);
    const merged = mergeTimeline(current, diff, new Set(['b']), new Set());
    const ids = merged.tracks[0].clips.map((c) => c.id);
    expect(ids).toContain('b');
  });

  it('does not apply unaccepted additions', () => {
    const current = mkTimeline([mkClip('a')]);
    const proposed = mkTimeline([mkClip('a'), mkClip('b', { start_sec: 5 })]);
    const diff = computeTimelineDiff(current, proposed);
    const merged = mergeTimeline(current, diff, new Set(), new Set());
    expect(merged.tracks[0].clips.map((c) => c.id)).not.toContain('b');
  });

  it('applies accepted removals', () => {
    const current = mkTimeline([mkClip('a'), mkClip('b')]);
    const proposed = mkTimeline([mkClip('a')]);
    const diff = computeTimelineDiff(current, proposed);
    const merged = mergeTimeline(current, diff, new Set(), new Set(['b']));
    expect(merged.tracks[0].clips.map((c) => c.id)).not.toContain('b');
  });

  it('moves a modified clip to the target track when track_id changes', () => {
    const current = mkTracks([
      mkTrack('t0', 'video', 0, [mkClip('c1', { track_id: 't0', start_sec: 0 })]),
      mkTrack('t1', 'video', 1, []),
    ]);
    const proposed = mkTracks([
      mkTrack('t0', 'video', 0, []),
      mkTrack('t1', 'video', 1, [mkClip('c1', { track_id: 't1', start_sec: 0, duration_sec: 7 })]),
    ]);
    const diff = computeTimelineDiff(current, proposed);
    const merged = mergeTimeline(current, diff, new Set(['c1']), new Set());
    expect(merged.tracks.find((t) => t.id === 't0')?.clips.map((c) => c.id)).not.toContain('c1');
    const t1 = merged.tracks.find((t) => t.id === 't1');
    expect(t1?.clips.map((c) => c.id)).toContain('c1');
    expect(t1?.clips.find((c) => c.id === 'c1')?.duration_sec).toBe(7);
  });

  it('creates the target track when the move targets a missing track', () => {
    const current = mkTracks([
      mkTrack('t0', 'video', 0, [mkClip('c1', { track_id: 't0', start_sec: 0 })]),
    ]);
    const proposed = mkTracks([
      mkTrack('t0', 'video', 0, []),
      mkTrack('t_new', 'video', 1, [mkClip('c1', { track_id: 't_new', start_sec: 0 })]),
    ]);
    const diff = computeTimelineDiff(current, proposed);
    const merged = mergeTimeline(current, diff, new Set(['c1']), new Set());
    const tNew = merged.tracks.find((t) => t.id === 't_new');
    expect(tNew).toBeDefined();
    expect(tNew?.kind).toBe('video');
    expect(tNew?.clips.map((c) => c.id)).toContain('c1');
    expect(merged.tracks.find((t) => t.id === 't0')?.clips.map((c) => c.id)).not.toContain('c1');
  });

  it('keeps a same-track modified clip on its track with updated fields', () => {
    const current = mkTracks([
      mkTrack('t0', 'video', 0, [mkClip('c1', { track_id: 't0', start_sec: 0, duration_sec: 5 })]),
      mkTrack('t1', 'video', 1, []),
    ]);
    const proposed = mkTracks([
      mkTrack('t0', 'video', 0, [mkClip('c1', { track_id: 't0', start_sec: 0, duration_sec: 8 })]),
      mkTrack('t1', 'video', 1, []),
    ]);
    const diff = computeTimelineDiff(current, proposed);
    const merged = mergeTimeline(current, diff, new Set(['c1']), new Set());
    const t0 = merged.tracks.find((t) => t.id === 't0');
    expect(t0?.clips.map((c) => c.id)).toContain('c1');
    expect(t0?.clips.find((c) => c.id === 'c1')?.duration_sec).toBe(8);
    expect(merged.tracks.find((t) => t.id === 't1')?.clips.length).toBe(0);
  });

  it('preserves the moved clip start_sec and inserts it in track order', () => {
    const current = mkTracks([
      mkTrack('t0', 'video', 0, [mkClip('c1', { track_id: 't0', start_sec: 0, duration_sec: 2 })]),
      mkTrack('t1', 'video', 1, [
        mkClip('c2', { track_id: 't1', start_sec: 0 }),
        mkClip('c3', { track_id: 't1', start_sec: 10 }),
      ]),
    ]);
    const proposed = mkTracks([
      mkTrack('t0', 'video', 0, []),
      mkTrack('t1', 'video', 1, [
        mkClip('c2', { track_id: 't1', start_sec: 0 }),
        mkClip('c3', { track_id: 't1', start_sec: 10 }),
        mkClip('c1', { track_id: 't1', start_sec: 5, duration_sec: 2 }),
      ]),
    ]);
    const diff = computeTimelineDiff(current, proposed);
    const merged = mergeTimeline(current, diff, new Set(['c1']), new Set());
    const t1 = merged.tracks.find((t) => t.id === 't1');
    expect(t1?.clips.find((c) => c.id === 'c1')?.start_sec).toBe(5);
    expect(t1?.clips.map((c) => c.id)).toEqual(['c2', 'c1', 'c3']);
  });
});
