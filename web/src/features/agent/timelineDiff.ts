/**
 * Timeline Diff — compares the current timeline against an Agent-proposed
 * timeline and produces a structured change list (added / removed / modified).
 * Powers the Agent co-pilot's "review changes before accepting" flow.
 */
import type { Timeline, Clip, Track } from '@/types/timeline';

export interface ModifiedClip {
  current: Clip;
  proposed: Clip;
  /** Field names that differ */
  fields: string[];
}

export interface TimelineDiff {
  addedClips: Clip[];
  removedClips: Clip[];
  modifiedClips: ModifiedClip[];
  /** True when nothing differs */
  isEmpty: boolean;
  /** Human-readable summary counts */
  summary: { added: number; removed: number; modified: number };
}

/** Fields compared when deciding whether a clip was modified. */
const COMPARED_FIELDS: (keyof Clip)[] = [
  'kind', 'asset_id', 'track_id', 'start_sec', 'duration_sec',
  'source_offset_sec', 'speed', 'volume', 'opacity', 'text',
  'font_size', 'font_color', 'transition_in', 'transition_out',
];

const EPS = 0.001;

function fieldDiffers(a: Clip, b: Clip, field: keyof Clip): boolean {
  const va = a[field];
  const vb = b[field];
  if (typeof va === 'number' && typeof vb === 'number') {
    return Math.abs(va - vb) > EPS;
  }
  return va !== vb;
}

/** Index all clips in a timeline by id. */
function indexClips(tl: Timeline): Map<string, Clip> {
  const map = new Map<string, Clip>();
  for (const track of tl.tracks) {
    for (const clip of track.clips) {
      map.set(clip.id, clip);
    }
  }
  return map;
}

/**
 * Compute the diff from `current` to `proposed`.
 * Clips are matched by id; matched clips with differing fields are "modified".
 */
export function computeTimelineDiff(current: Timeline, proposed: Timeline): TimelineDiff {
  const cur = indexClips(current);
  const prop = indexClips(proposed);

  const addedClips: Clip[] = [];
  const removedClips: Clip[] = [];
  const modifiedClips: ModifiedClip[] = [];

  // Added + modified
  for (const [id, pClip] of prop) {
    const cClip = cur.get(id);
    if (!cClip) {
      addedClips.push(pClip);
      continue;
    }
    const fields = COMPARED_FIELDS.filter((f) => fieldDiffers(cClip, pClip, f));
    if (fields.length > 0) {
      modifiedClips.push({ current: cClip, proposed: pClip, fields });
    }
  }

  // Removed
  for (const [id, cClip] of cur) {
    if (!prop.has(id)) {
      removedClips.push(cClip);
    }
  }

  // Sort for stable display
  addedClips.sort((a, b) => a.start_sec - b.start_sec);
  removedClips.sort((a, b) => a.start_sec - b.start_sec);
  modifiedClips.sort((a, b) => a.current.start_sec - b.current.start_sec);

  return {
    addedClips,
    removedClips,
    modifiedClips,
    isEmpty: addedClips.length === 0 && removedClips.length === 0 && modifiedClips.length === 0,
    summary: {
      added: addedClips.length,
      removed: removedClips.length,
      modified: modifiedClips.length,
    },
  };
}

/**
 * Merge only the selected changes from the proposed timeline into the current
 * one. `acceptIds` = clip ids to apply (added or modified); removals are
 * applied only for ids present in `removeIds`.
 */
export function mergeTimeline(
  current: Timeline,
  diff: TimelineDiff,
  acceptIds: Set<string>,
  removeIds: Set<string>,
): Timeline {
  const result: Timeline = structuredClone(current);

  // Apply removals
  if (removeIds.size > 0) {
    for (const track of result.tracks) {
      track.clips = track.clips.filter((c) => !removeIds.has(c.id));
    }
  }

  // Apply modifications
  for (const mod of diff.modifiedClips) {
    if (!acceptIds.has(mod.proposed.id)) continue;
    for (const track of result.tracks) {
      const idx = track.clips.findIndex((c) => c.id === mod.proposed.id);
      if (idx < 0) continue;

      if (track.id === mod.proposed.track_id) {
        // Same track: keep it in place but adopt proposed field values
        track.clips[idx] = { ...mod.proposed, track_id: track.id };
      } else {
        // Track migration: Agent moved the clip to a different track_id
        track.clips.splice(idx, 1);

        let target = result.tracks.find((t) => t.id === mod.proposed.track_id);
        if (!target) {
          // Agent 引入了新轨道 → 按片段类型创建一个最小轨道，避免静默丢弃片段
          target = {
            id: mod.proposed.track_id,
            name: `${mod.proposed.kind} ${result.tracks.length + 1}`,
            kind: mod.proposed.kind,
            index: result.tracks.length,
            clips: [],
            locked: false,
            muted: false,
          } satisfies Track;
          result.tracks.push(target);
        }
        target.clips.push(structuredClone(mod.proposed));
        target.clips.sort((a, b) => a.start_sec - b.start_sec);
      }
      break;
    }
  }

  // Apply additions
  for (const add of diff.addedClips) {
    if (!acceptIds.has(add.id)) continue;
    let track = result.tracks.find((t) => t.id === add.track_id);
    if (!track) {
      // Agent 引入了新轨道 → 按片段类型创建一个最小轨道，避免静默丢弃片段
      track = {
        id: add.track_id,
        name: `${add.kind} ${result.tracks.length + 1}`,
        kind: add.kind,
        index: result.tracks.length,
        clips: [],
        locked: false,
        muted: false,
      } satisfies Track;
      result.tracks.push(track);
    }
    track.clips.push(structuredClone(add));
  }

  // Recompute duration
  let maxEnd = 0;
  for (const track of result.tracks) {
    for (const clip of track.clips) {
      maxEnd = Math.max(maxEnd, clip.start_sec + clip.duration_sec);
    }
  }
  result.duration_sec = maxEnd;
  return result;
}
