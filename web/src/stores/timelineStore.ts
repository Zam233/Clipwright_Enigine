import { create } from 'zustand';
import type { Timeline, Track, Clip, ClipKind, TimelineMarker } from '@/types/timeline';
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
  /** M8: 批量设置时间轴标记（引擎变更回调写回 store，随项目保存持久化） */
  setTimelineMarkers: (markers: TimelineMarker[]) => void;

  // Track actions
  addTrack: (kind: ClipKind, name?: string, index?: number) => string;
  removeTrack: (trackId: string) => void;
  reorderTrack: (trackId: string, newIndex: number) => void;
  toggleTrackLock: (trackId: string) => void;
  toggleTrackMute: (trackId: string) => void;
  toggleTrackHidden: (trackId: string) => void; // M7
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
  /** M1: rolling 编辑 — 拖动相邻两片段共享边界，此消彼长，总时长不变 */
  rollingTrim: (clipId: string, deltaSec: number, edge: 'start' | 'end') => void;
  /** M1: slip 编辑 — 保持片段在时间轴位置不变，仅平移素材内容窗口 */
  slipClip: (clipId: string, deltaSec: number) => void;
  /** M1: slide 编辑 — 移动片段，同时让相邻片段伸缩补位，总时长不变 */
  slideClip: (clipId: string, deltaSec: number) => void;
  /** Ripple delete: remove clip and close the gap by shifting later clips left. */
  rippleDelete: (clipId: string) => void;
  /** Ripple insert: add a clip at a time, shifting later clips right to make room. */
  rippleInsert: (trackId: string, clip: Partial<Clip> & { kind: ClipKind }, atSec: number) => string;

  // Keyframe actions
  addKeyframe: (clipId: string, time: number, properties: Record<string, number>) => void;
  removeKeyframe: (clipId: string, time: number) => void;
  updateKeyframe: (clipId: string, time: number, properties: Record<string, number>) => void;

  // M2: 编组
  groupClips: (clipIds: string[]) => string | null;
  ungroupClips: (clipIds: string[]) => void;
  getGroupClipIds: (clipId: string) => string[];

  // C3: 嵌套序列
  createNestedSequence: (clipIds: string[]) => string | null;
  /** 展开嵌套序列：把内嵌子时间线的片段平铺回原轨道（按时间窗口重定位） */
  expandNestedSequence: (clipId: string) => void;

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

  setTimelineMarkers: (markers) =>
    set((state) => ({
      timeline: { ...state.timeline, markers },
      isDirty: true,
    })),

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

  toggleTrackHidden: (trackId) => // M7: 轨道隐藏/独显
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) =>
          t.id === trackId ? { ...t, hidden: !t.hidden } : t,
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

  // M1: rolling 编辑 — 共享边界此消彼长，总时长不变。
  // edge='start'：调整 clipId 的起点，同时把同轨道紧邻的前一片段终点对向移动；
  // edge='end'：调整 clipId 的终点，同时把紧邻的后一片段起点对向移动。
  rollingTrim: (clipId, deltaSec, edge) =>
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => {
        const sorted = [...t.clips].sort((a, b) => a.start_sec - b.start_sec);
        const idx = sorted.findIndex((c) => c.id === clipId);
        if (idx === -1) return t;
        const clip = sorted[idx];
        const updated = new Map<string, { start_sec: number; duration_sec: number }>();
        if (edge === 'start') {
          const prev = sorted[idx - 1];
          if (!prev) return t; // 没有前一片段 → rolling 无从谈起
          const maxShrink = clip.duration_sec - 0.1;
          const maxGrow = prev.duration_sec - 0.1;
          const d = Math.max(-maxShrink, Math.min(maxGrow, deltaSec));
          updated.set(clip.id, { start_sec: clip.start_sec + d, duration_sec: clip.duration_sec - d });
          updated.set(prev.id, { start_sec: prev.start_sec, duration_sec: prev.duration_sec + d });
        } else {
          const next = sorted[idx + 1];
          if (!next) return t;
          const maxGrow = clip.duration_sec - 0.1;
          const maxShrink = next.duration_sec - 0.1;
          const d = Math.max(-maxGrow, Math.min(maxShrink, deltaSec));
          updated.set(clip.id, { start_sec: clip.start_sec, duration_sec: clip.duration_sec + d });
          updated.set(next.id, { start_sec: next.start_sec + d, duration_sec: next.duration_sec - d });
        }
        return {
          ...t,
          clips: t.clips.map((c) => {
            const u = updated.get(c.id);
            return u ? { ...c, ...u } : c;
          }),
        };
      });
      return {
        timeline: { ...state.timeline, tracks, duration_sec: computeTotalDuration(tracks) },
        isDirty: true,
      };
    }),

  // M1: slip 编辑 — 素材窗口平移，时间轴位置与时长不变。
  slipClip: (clipId, deltaSec) =>
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) => ({
          ...t,
          clips: t.clips.map((c) => {
            if (c.id !== clipId) return c;
            return { ...c, source_offset_sec: Math.max(0, c.source_offset_sec + deltaSec * c.speed) };
          }),
        })),
      },
      isDirty: true,
    })),

  // M1: slide 编辑 — 移动片段，相邻片段伸缩补位，总时长不变。
  // 前移：前一片段终点前移、后一片段起点前移（若存在）；后移同理反向。
  slideClip: (clipId, deltaSec) =>
    set((state) => {
      const tracks = state.timeline.tracks.map((t) => {
        const sorted = [...t.clips].sort((a, b) => a.start_sec - b.start_sec);
        const idx = sorted.findIndex((c) => c.id === clipId);
        if (idx === -1) return t;
        const clip = sorted[idx];
        const prev = sorted[idx - 1];
        const next = sorted[idx + 1];
        // 前移上限 = 前一片段可压缩量（且不能越过 0）；后移上限 = 后一片段可压缩量
        const maxFwd = Math.min(prev ? prev.duration_sec - 0.1 : Infinity, clip.start_sec);
        const maxBack = next ? next.duration_sec - 0.1 : Infinity;
        const d = Math.max(
          maxBack === Infinity ? -(clip.duration_sec - 0.1) : -maxBack,
          Math.min(maxFwd === Infinity ? clip.duration_sec - 0.1 : maxFwd, deltaSec),
        );
        const updated = new Map<string, { start_sec: number; duration_sec: number }>();
        updated.set(clip.id, { start_sec: clip.start_sec + d, duration_sec: clip.duration_sec });
        if (prev) {
          updated.set(prev.id, { start_sec: prev.start_sec, duration_sec: prev.duration_sec + d });
        }
        if (next) {
          updated.set(next.id, { start_sec: next.start_sec + d, duration_sec: next.duration_sec - d });
        }
        return {
          ...t,
          clips: t.clips.map((c) => {
            const u = updated.get(c.id);
            return u ? { ...c, ...u } : c;
          }),
        };
      });
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

  // M2: 编组 — 为指定片段分配同一 group_id（≥2 个才成组）
  groupClips: (clipIds) => {
    const ids = [...new Set(clipIds)].filter((id) => get().getClip(id));
    if (ids.length < 2) return null;
    // 合并已有组：若任一片段已属于某组，沿用该组 id（其余组归并进来）
    let groupId: string | null = null;
    for (const id of ids) {
      const c = get().getClip(id);
      if (c?.group_id) { groupId = c.group_id; break; }
    }
    if (!groupId) groupId = uid('grp');
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) => ({
          ...t,
          clips: t.clips.map((c) => (ids.includes(c.id) ? { ...c, group_id: groupId } : c)),
        })),
      },
      isDirty: true,
    }));
    return groupId;
  },

  ungroupClips: (clipIds) => {
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((t) => ({
          ...t,
          clips: t.clips.map((c) => (clipIds.includes(c.id) ? { ...c, group_id: null } : c)),
        })),
      },
      isDirty: true,
    }));
  },

  getGroupClipIds: (clipId) => {
    const clip = get().getClip(clipId);
    if (!clip?.group_id) return [];
    const gid = clip.group_id;
    const out: string[] = [];
    for (const t of get().timeline.tracks) {
      for (const c of t.clips) {
        if (c.group_id === gid) out.push(c.id);
      }
    }
    return out;
  },

  // C3: 把选中片段折叠为一个嵌套序列片段（保留相对时间布局，替换为一个引用子时间线的 clip）
  createNestedSequence: (clipIds) => {
    const ids = [...new Set(clipIds)];
    if (ids.length < 2) return null;
    // 收集片段及其所在轨道
    const state = get().timeline;
    const selected: { clip: Clip; track: Track }[] = [];
    for (const t of state.tracks) {
      for (const c of t.clips) {
        if (ids.includes(c.id)) selected.push({ clip: c, track: t });
      }
    }
    if (selected.length < 2) return null;
    const minStart = Math.min(...selected.map((s) => s.clip.start_sec));
    const maxEnd = Math.max(...selected.map((s) => s.clip.start_sec + s.clip.duration_sec));
    const duration = Math.max(0.1, maxEnd - minStart);
    // 构建子时间线：每个来源轨道映射为一条子轨道，片段时间相对 minStart
    const nestedTracks: Track[] = selected.map((s, i) => ({
      id: uid('track'),
      name: s.track.name,
      kind: s.track.kind,
      index: i,
      locked: false,
      muted: false,
      clips: [{
        ...s.clip,
        id: uid('clip'),
        track_id: '',
        start_sec: s.clip.start_sec - minStart,
      }],
    }));
    const nested: Timeline = {
      id: uid('nest'),
      width: state.width,
      height: state.height,
      fps: state.fps,
      duration_sec: duration,
      tracks: nestedTracks,
      markers: [],
    };
    // 新建一个 video 轨道放置嵌套片段，移除原片段
    const nestClipId = uid('clip');
    const nestTrackId = uid('track');
    const tracks = state.tracks.map((t) => ({
      ...t,
      clips: t.clips.filter((c) => !ids.includes(c.id)),
    }));
    tracks.push({
      id: nestTrackId,
      name: `嵌套序列 ${ids.length}`,
      kind: 'video',
      index: tracks.length,
      locked: false,
      muted: false,
      clips: [{
        ...createDefaultClip({
          id: nestClipId,
          kind: 'video',
          track_id: nestTrackId,
          start_sec: minStart,
          duration_sec: duration,
          nested_timeline: nested,
        }),
        id: nestClipId,
        track_id: nestTrackId,
        start_sec: minStart,
        duration_sec: duration,
        nested_timeline: nested,
      }],
    });
    set({
      timeline: { ...state, tracks, duration_sec: computeTotalDuration(tracks) },
      isDirty: true,
    });
    return nestClipId;
  },

  // C3: 展开嵌套序列 — 子时间线片段平铺回（相对窗口 + 父片段起点），删除嵌套片段
  expandNestedSequence: (clipId) => {
    const state = get().timeline;
    let parent: Clip | null = null;
    let parentTrack: Track | null = null;
    for (const t of state.tracks) {
      for (const c of t.clips) {
        if (c.id === clipId) { parent = c; parentTrack = t; }
      }
    }
    if (!parent || !parent.nested_timeline) return;
    const nt = parent.nested_timeline;
    const flat: { clip: Clip; track: Track }[] = [];
    for (const t of nt.tracks) {
      for (const c of t.clips) {
        flat.push({ clip: c, track: t });
      }
    }
    if (flat.length === 0) return;
    // 平铺到原轨道上（时间 = 父起点 + 相对时间）
    const tracks = state.tracks.map((t) => ({
      ...t,
      clips: t.clips.filter((c) => c.id !== clipId),
    }));
    for (const { clip, track } of flat) {
      const target = tracks.find((t) => t.id === parentTrack!.id) ?? tracks[0];
      if (!target) continue;
      target.clips.push({
        ...clip,
        id: uid('clip'),
        track_id: target.id,
        start_sec: parent!.start_sec + clip.start_sec,
      });
      target.clips.sort((a, b) => a.start_sec - b.start_sec);
    }
    set({
      timeline: { ...state, tracks, duration_sec: computeTotalDuration(tracks) },
      isDirty: true,
    });
  },

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
