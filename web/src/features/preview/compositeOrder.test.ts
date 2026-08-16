import { describe, it, expect } from 'vitest';
import type { Track } from '@/types/timeline';
import { orderTracksForComposite } from './compositeOrder';

/** Build a minimal Track with only the fields the helper touches. */
function makeTrack(index: number, kind: Track['kind'] = 'video', id = `track-${index}-${kind}`): Track {
  return { id, name: `Track ${index}`, kind, index, clips: [], locked: false, muted: false };
}

describe('orderTracksForComposite — ascending track-index stacking', () => {
  it('sorts ascending so the lowest index is drawn first (bottom layer)', () => {
    const tracks = [makeTrack(2, 'video'), makeTrack(0, 'image'), makeTrack(1, 'audio')];
    expect(orderTracksForComposite(tracks).map((t) => t.index)).toEqual([0, 1, 2]);
  });

  it('places a bottom video track under a higher caption track (regression guard for reversed stacking)', () => {
    // Captions sit on a high index and must composite ON TOP of the video layer.
    const tracks = [makeTrack(2, 'caption'), makeTrack(0, 'video')];
    const result = orderTracksForComposite(tracks);
    expect(result[0].index).toBe(0);
    expect(result[1].index).toBe(2);
  });

  it('returns an empty array for empty input', () => {
    expect(orderTracksForComposite([])).toEqual([]);
  });

  it('keeps relative order of tracks sharing the same index (stable sort)', () => {
    const first = makeTrack(1, 'video', 'first');
    const second = makeTrack(1, 'text', 'second');
    const result = orderTracksForComposite([second, first]);
    expect(result[0].id).toBe('second');
    expect(result[1].id).toBe('first');
  });

  it('does not mutate the input array (returns a copy)', () => {
    const tracks = [makeTrack(2), makeTrack(0), makeTrack(1)];
    const original = [...tracks];
    const result = orderTracksForComposite(tracks);
    expect(result).not.toBe(tracks);
    expect(tracks.map((t) => t.index)).toEqual(original.map((t) => t.index));
  });
});
