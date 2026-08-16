import { create } from 'zustand';
import type { Timeline, Track, Clip, ClipKind } from '@/types/timeline';
import { createEmptyTimeline, createDefaultClip, computeTotalDuration } from '@/types/timeline';
import { uid } from '@/lib/utils';
import { useSelectionStore } from './selectionStore';
import { usePreviewStore } from './previewStore';

interface TimelineState {
  timeline: Timeline;
  isDirty: boolean;

  // Timeline-level actions
  setTimeline: (timeline: Timeline) => void;
  resetTimeline: () => void;
  updateTimelineMeta: (meta: Partial<Pick<Timeline, 'width' | 'height' | 'fps' | 'duration_sec'>>) => void;

  // Track actions
  addTrack: (kind: ClipKind, name?: string, index?: number) => string;
  removeTrack: (trackId: string) => void;
  reorderTrack: (trackId: string, newIndex: number) => void;
  toggleTrackLock: (trackId: string) => void;
  toggleTrackMute: (trackId: string) => void;
  renameTrack: (trackId: string, name: string) => void;

  // Clip actions
  addClip: (trackId: string, clip: Partial<Clip> & { kind: ClipKind }) => string;
  removeClip: (clipId: string) => void;
  updateClip: (clipId: string, updates: Partial<Clip>) => void;
  /** Apply updates to every clip on a track (caption style cascade). */
  updateTrackClips: (trackId: string, updates: Partial<Clip>) => void;
  moveClip: (clipId: string, targetTrackId: string, newStartSec: number) => void;
  splitClip: (clipId: string, splitTimeSec: number) => void;
  trimClipStart: (clipId: string, newStartSec: number) => void;
  trimClipEnd: (clipId: string, newEndSec: number) => void;
  /** Ripple delete: remove clip and close the gap by shifting later clips left. */
  rippleDelete: (clipId: string) => void;
  /** Ripple insert: add a clip at a time, shifting later clips right to make room. */
  rippleInsert: (trackId: string, clip: Partial<Clip> & { kind: ClipKind }, atSec: number) => string;

  // Keyframe actions
  addKeyframe: (clipId: string, time: number, properties: Record<string, number>) => void;
  removeKeyframe: (clipId: string, time: number) => void;
  updateKeyframe: (clipId: string, time: number, properties: Record<string, number>) => void;

  // Query helpers
  getTrack: (trackId: string) => Track | undefined;
  getClip: (clipId: string) => Clip | undefined;
  findClipAtTime: (trackId: string, timeSec: number) => Clip | undefined;
  exportTimeline: () => Timeline;
}

export const useTimelineStore = create<TimelineState>((set, get) => ({
  timeline: createEmptyTimeline(),
  isDirty: false,

  setTimeline: (timeline) => {
    set({ timeline, isDirty: false });
    // 同步预览/选择状态，避免 playhead 钳位失效与悬空选择
    usePreviewStore.getState().setDuration(timeline.duration_sec);
    usePreviewStore.getState().setFps(timeline.fps);
    useSelectionStore.getState().deselectAll();
  },

  resetTimeline: () => {
    set({ timeline: createEmptyTimeline(), isDirty: false });
    usePreviewStore.getState().setDuration(0);
    useSelectionStore.getState().deselectAll();
  },

  updateTimelineMeta: (meta) =>
    set((state) => {
      const timeline = { ...state.timeline, ...meta };
      if (meta.duration_sec !== undefined) {
        usePreviewStore.getState().setDuration(meta.duration_sec);
      }
      if (meta.fps !== undefined) {
        usePreviewStore.getState().setFps(meta.fps);
      }
      return { timeline, isDirty: true };
    }),

  addTrack: (kind, name, index) => {
    const id = uid('track');
    set((state) => {
      const insertAt = index === undefined
        ? state.timeline.tracks.length
        : Math.max(0, Math.min(index, state.timeline.tracks.length));
      const track: Track = {
        id,
        name: name || `${kind.toUpperCase()} ${insertAt + 1}`,
        kind,
        index: insertAt,
        clips: [],
        locked: false,
        muted: false,
      };
      const tracks = [...state.timeline.tracks];
      tracks.splice(insertAt, 0, track);
      return {
        timeline: {
          ...state.timeline,
          tracks: tracks.map((t, i) => (t.index === i ? t : { ...t, index: i })),
        },
        isDirty: true,
      };
    });
    return id;
  },

  removeTrack: (trackId) => {
    const track = get().timeline.tracks.find((t) => t.id === trackId);
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks
          .filter((t) => t.id !== trackId)
          .map((t, i) => ({ ...t, index: i })),
      },
      isDirty: true,
    }));
    // 清理该轨道及其 clip 的选择，避免悬空引用
    if (track) {
      const removedIds = new Set(track.clips.map((c) => c.id));
      useSelectionStore.setState((s) => ({
        selectedClipIds: s.selectedClipIds.filter((id) => !removedIds.has(id)),
        selectedTrackId: s.selectedTrackId === trackId ? null : s.selectedTrackId,
      }));
    }
  },

  reorderTrack: (trackId, newIndex) =>
    set((state) => {
      const tracks = [...state.timeline.tracks];
      const oldIndex = tracks.findIndex((t) => t.id === trackId);
      if (oldIndex === -1 || oldIndex === newIndex) return state;
      const [moved] = tracks.splice(oldIndex, 1);
      tracks.splice(newIndex, 0, moved);
      return {
        timeline: {
          ...state.timeline,
          tracks: tracks.map((t, i) => ({ ...t, index: i })),
        },
        isDirty: true,
      };
    }),

  toggleTrackLock: (trackId) =>
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) =>
          t.id === trackId ? { ...t, locked: !t.locked } : t,
        ),
      },
      isDirty: true,
    })),

  toggleTrackMute: (trackId) =>
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) =>
          t.id === trackId ? { ...t, muted: !t.muted } : t,
        ),
      },
      isDirty: true,
    })),

  renameTrack: (trackId, name) =>
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) =>
          t.id === trackId ? { ...t, name } : t,
        ),
      },
      isDirty: true,
    })),

  addClip: (trackId, clipData) => {
    const id = uid('clip');
    set((state) => {
      const clip = createDefaultClip({ ...clipData, id, track_id: trackId });
      const tracks = state.timeline.tracks.map((t) =>
        t.id === trackId
          ? { ...t, clips: [...t.clips, clip].sort((a, b) => a.start_sec - b.start_sec) }
          : t,
      );
      return {
        timeline: {
          ...state.timeline,
          tracks,
          duration_sec: computeTotalDuration(tracks),
        },
        isDirty: true,
      };
    });
    return id;
  },

  removeClip: (clipId) => {
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => ({
        ...t,
        clips: t.clips.filter((c) => c.id !== clipId),
      }));
      return {
        timeline: {
          ...state.timeline,
          tracks,
          duration_sec: computeTotalDuration(tracks),
        },
        isDirty: true,
      };
    });
    useSelectionStore.setState((s) => ({
      selectedClipIds: s.selectedClipIds.filter((id) => id !== clipId),
    }));
  },

  updateClip: (clipId, updates) =>
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => ({
        ...t,
        clips: t.clips.map((c) =>
          c.id === clipId ? { ...c, ...updates } : c,
        ),
      }));
      return {
        timeline: {
          ...state.timeline,
          tracks,
          duration_sec: computeTotalDuration(tracks),
        },
        isDirty: true,
      };
    }),

  updateTrackClips: (trackId, updates) =>
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => ({
        ...t,
        clips: t.id === trackId
          ? t.clips.map((c) => ({ ...c, ...updates }))
          : t.clips,
      }));
      return {
        timeline: {
          ...state.timeline,
          tracks,
          duration_sec: computeTotalDuration(tracks),
        },
        isDirty: true,
      };
    }),

  moveClip: (clipId, targetTrackId, newStartSec) =>
    set((state) => {
      let movedClip: Clip | undefined;
      // Remove from source
      const tracksWithout = state.timeline.tracks.map((t) => {
        const clip = t.clips.find((c) => c.id === clipId);
        if (clip) movedClip = clip;
        return { ...t, clips: t.clips.filter((c) => c.id !== clipId) };
      });
      if (!movedClip) return state;
      // Add to target
      const updatedClip = { ...movedClip, track_id: targetTrackId, start_sec: newStartSec };
      const tracks = tracksWithout.map((t) =>
        t.id === targetTrackId
          ? { ...t, clips: [...t.clips, updatedClip].sort((a, b) => a.start_sec - b.start_sec) }
          : t,
      );
      return {
        timeline: {
          ...state.timeline,
          tracks,
          duration_sec: computeTotalDuration(tracks),
        },
        isDirty: true,
      };
    }),

  splitClip: (clipId, splitTimeSec) =>
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => {
        const clipIndex = t.clips.findIndex((c) => c.id === clipId);
        if (clipIndex === -1) return t;
        const clip = t.clips[clipIndex];
        const relSplit = splitTimeSec - clip.start_sec;
        if (relSplit <= 0 || relSplit >= clip.duration_sec) return t;

        // Remap keyframes: keyframes use normalized time [0,1]
        const kfs = clip.keyframes ?? [];
        const leftKfs = kfs
          .filter((k) => k.time * clip.duration_sec <= relSplit)
          .map((k) => ({ ...k, time: (k.time * clip.duration_sec) / relSplit }));
        const rightKfs = kfs
          .filter((k) => k.time * clip.duration_sec > relSplit)
          .map((k) => ({
            ...k,
            time: (k.time * clip.duration_sec - relSplit) / (clip.duration_sec - relSplit),
          }));

        const left: Clip = {
          ...clip,
          duration_sec: relSplit,
          keyframes: leftKfs,
        };
        const right: Clip = {
          ...clip,
          id: uid('clip'),
          start_sec: splitTimeSec,
          duration_sec: clip.duration_sec - relSplit,
          source_offset_sec: clip.source_offset_sec + relSplit * clip.speed,
          keyframes: rightKfs,
        };
        const clips = [...t.clips];
        clips.splice(clipIndex, 1, left, right);
        return { ...t, clips };
      });
      return {
        timeline: { ...state.timeline, tracks, duration_sec: computeTotalDuration(tracks) },
        isDirty: true,
      };
    }),

  trimClipStart: (clipId, newStartSec) =>
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => ({
        ...t,
        clips: t.clips.map((c) => {
          if (c.id !== clipId) return c;
          const newStart = Math.max(0, Math.min(newStartSec, c.start_sec + c.duration_sec - 0.1));
          const delta = newStart - c.start_sec;
          return {
            ...c,
            start_sec: newStart,
            duration_sec: Math.max(0.1, c.duration_sec - delta),
            source_offset_sec: Math.max(0, c.source_offset_sec + delta * c.speed),
    };
        }),
      }));
      return {
        timeline: { ...state.timeline, tracks, duration_sec: computeTotalDuration(tracks) },
        isDirty: true,
      };
    }),

  trimClipEnd: (clipId, newEndSec) =>
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => ({
        ...t,
        clips: t.clips.map((c) => {
          if (c.id !== clipId) return c;
          const newDuration = Math.max(0.1, newEndSec - c.start_sec);
          return { ...c, duration_sec: newDuration };
        }),
      }));
      return {
        timeline: { ...state.timeline, tracks, duration_sec: computeTotalDuration(tracks) },
        isDirty: true,
      };
    }),

  rippleDelete: (clipId) => {
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => {
        const idx = t.clips.findIndex((c) => c.id === clipId);
        if (idx === -1) return t;
        const removed = t.clips[idx];
        const gap = removed.duration_sec;
        const removedStart = removed.start_sec;
        // Drop the clip, then shift every later clip left by the gap
        const clips = t.clips
          .filter((c) => c.id !== clipId)
          .map((c) =>
            c.start_sec >= removedStart + gap - 0.0001
              ? { ...c, start_sec: Math.max(0, c.start_sec - gap) }
              : c,
          );
        return { ...t, clips };
      });
      return {
        timeline: { ...state.timeline, tracks, duration_sec: computeTotalDuration(tracks) },
        isDirty: true,
      };
    });
    useSelectionStore.setState((s) => ({
      selectedClipIds: s.selectedClipIds.filter((id) => id !== clipId),
    }));
  },

  rippleInsert: (trackId, clipData, atSec) => {
    const id = uid('clip');
    set((state) => {
      const newClip = createDefaultClip({ ...clipData, id, track_id: trackId, start_sec: atSec });
      const insertedDur = newClip.duration_sec;
      const tracks = state.timeline.tracks.map((t) => {
        if (t.id !== trackId) return t;
        const clips = t.clips
          .map((c) =>
            c.start_sec >= atSec - 0.0001
              ? { ...c, start_sec: c.start_sec + insertedDur }
              : c,
          );
        return { ...t, clips: [...clips, newClip].sort((a, b) => a.start_sec - b.start_sec) };
      });
      return {
        timeline: { ...state.timeline, tracks, duration_sec: computeTotalDuration(tracks) },
        isDirty: true,
      };
    });
    return id;
  },

  addKeyframe: (clipId, time, properties) =>
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) => ({
          ...t,
          clips: t.clips.map((c) => {
            if (c.id !== clipId) return c;
            const existing = c.keyframes.findIndex((k) => Math.abs(k.time - time) < 0.001);
            const keyframes = [...c.keyframes];
            if (existing >= 0) {
              keyframes[existing] = { ...keyframes[existing], properties: { ...keyframes[existing].properties, ...properties } };
            } else {
              keyframes.push({ time, properties });
              keyframes.sort((a, b) => a.time - b.time);
            }
            return { ...c, keyframes };
          }),
        })),
      },
      isDirty: true,
    })),

  removeKeyframe: (clipId, time) =>
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) => ({
          ...t,
          clips: t.clips.map((c) =>
            c.id === clipId
              ? { ...c, keyframes: c.keyframes.filter((k) => Math.abs(k.time - time) >= 0.001) }
              : c,
          ),
        })),
      },
      isDirty: true,
    })),

  updateKeyframe: (clipId, time, properties) =>
    get().addKeyframe(clipId, time, properties),

  getTrack: (trackId) =>
    get().timeline.tracks.find((t) => t.id === trackId),

  getClip: (clipId) => {
    for (const track of get().timeline.tracks) {
      const clip = track.clips.find((c) => c.id === clipId);
      if (clip) return clip;
    }
    return undefined;
  },

  findClipAtTime: (trackId, timeSec) => {
    const track = get().timeline.tracks.find((t) => t.id === trackId);
    if (!track) return undefined;
    return track.clips.find(
      (c) => timeSec >= c.start_sec && timeSec < c.start_sec + c.duration_sec,
    );
  },

  exportTimeline: () => {
    const { timeline } = get();
    return structuredClone(timeline);
  },
}));
