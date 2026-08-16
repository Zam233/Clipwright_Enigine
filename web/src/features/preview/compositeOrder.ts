import type { Track } from '@/types/timeline';

/** 升序轨道合成顺序：index 最小的轨道最先绘制（最底层），最大的最后绘制（最顶层）。 */
/** Ascending track-index composite order: lowest index drawn first (bottom layer), highest last (top layer). */
export function orderTracksForComposite(tracks: Track[]): Track[] {
  return [...tracks].sort((a, b) => a.index - b.index);
}
