import { describe, it, expect, beforeEach } from 'vitest';
import { useTimelineStore } from './timelineStore';
import { createEmptyTimeline } from '@/types/timeline';

describe('timelineStore', () => {
  beforeEach(() => {
    useTimelineStore.getState().setTimeline(createEmptyTimeline());
  });

  it('adds a track', () => {
    const id = useTimelineStore.getState().addTrack('video', 'V1');
    const tracks = useTimelineStore.getState().timeline.tracks;
    expect(tracks).toHaveLength(1);
    expect(tracks[0].id).toBe(id);
    expect(tracks[0].kind).toBe('video');
  });

  it('adds a track at a specific index (dropAssetAt insert position)', () => {
    useTimelineStore.getState().addTrack('video', 'V1');
    useTimelineStore.getState().addTrack('text', 'T1');
    // 在位置 1（两条轨道之间）插入
    const id = useTimelineStore.getState().addTrack('video', 'V2', 1);
    const tracks = useTimelineStore.getState().timeline.tracks;
    expect(tracks.map((t) => t.id)).toEqual([tracks[0].id, id, tracks[2].id]);
    expect(tracks.map((t) => t.index)).toEqual([0, 1, 2]);
    expect(tracks[1].name).toBe('V2');
  });

  it('adds a track with an out-of-range index clamped', () => {
    useTimelineStore.getState().addTrack('video');
    useTimelineStore.getState().addTrack('audio', 'A1', 99);
    const tracks = useTimelineStore.getState().timeline.tracks;
    expect(tracks).toHaveLength(2);
    expect(tracks[1].kind).toBe('audio');
  });

  it('keeps clips sorted by start_sec after addClip/moveClip', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 10, duration_sec: 2 });
    const mid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 2 });
    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    expect(clips.map((c) => c.start_sec)).toEqual([0, 10]);
    // moveClip 也保持有序
    useTimelineStore.getState().moveClip(mid, tid, 5);
    const after = useTimelineStore.getState().timeline.tracks[0].clips;
    expect(after.map((c) => c.start_sec)).toEqual([5, 10]);
  });

  it('adds a clip to a track', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    const cid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 5 });
    const track = useTimelineStore.getState().timeline.tracks[0];
    expect(track.clips).toHaveLength(1);
    expect(track.clips[0].id).toBe(cid);
    expect(track.clips[0].duration_sec).toBe(5);
  });

  it('removes a clip', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    const cid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 5 });
    useTimelineStore.getState().removeClip(cid);
    expect(useTimelineStore.getState().timeline.tracks[0].clips).toHaveLength(0);
  });

  it('splits a clip into two', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    const cid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 10 });
    useTimelineStore.getState().splitClip(cid, 4);
    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    expect(clips).toHaveLength(2);
    expect(clips[0].duration_sec).toBeCloseTo(4, 5);
    expect(clips[1].start_sec).toBeCloseTo(4, 5);
    expect(clips[1].duration_sec).toBeCloseTo(6, 5);
  });

  it('does not split outside clip bounds', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    const cid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 10 });
    useTimelineStore.getState().splitClip(cid, 20);
    expect(useTimelineStore.getState().timeline.tracks[0].clips).toHaveLength(1);
  });

  it('ripple delete closes the gap', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    const a = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 5 });
    useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 5, duration_sec: 5 });
    useTimelineStore.getState().rippleDelete(a);
    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    expect(clips).toHaveLength(1);
    expect(clips[0].start_sec).toBeCloseTo(0, 5); // shifted left to close gap
  });

  it('ripple insert shifts later clips right', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 5, duration_sec: 5 });
    useTimelineStore.getState().rippleInsert(tid, { kind: 'video', duration_sec: 3 }, 0);
    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    expect(clips).toHaveLength(2);
    const shifted = clips.find((c) => c.start_sec > 0);
    expect(shifted?.start_sec).toBeCloseTo(8, 5); // 5 + 3
  });

  it('toggles track lock and mute', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    useTimelineStore.getState().toggleTrackLock(tid);
    expect(useTimelineStore.getState().timeline.tracks[0].locked).toBe(true);
    useTimelineStore.getState().toggleTrackMute(tid);
    expect(useTimelineStore.getState().timeline.tracks[0].muted).toBe(true);
  });

  it('computes total duration from clips', () => {
    const tid = useTimelineStore.getState().addTrack('video');
    useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 12 });
    expect(useTimelineStore.getState().timeline.duration_sec).toBeCloseTo(12, 5);
  });

  it('re-reading getState after addTrack sees the new track (stale-state guard)', () => {
    // Simulate the stale-state pattern: capture snapshot BEFORE mutation
    const staleSnapshot = useTimelineStore.getState();

    // Mutate via live API (which calls set() internally)
    const tid = useTimelineStore.getState().addTrack('audio', 'A1');

    // Stale snapshot is frozen in time — its tracks array is still empty
    expect(staleSnapshot.timeline.tracks).toHaveLength(0);

    // Live re-read sees the new track
    const freshTracks = useTimelineStore.getState().timeline.tracks;
    expect(freshTracks).toHaveLength(1);
    expect(freshTracks[0].id).toBe(tid);
    expect(freshTracks[0].kind).toBe('audio');
  });

  it('finds a track by id via fresh getState (the fix)', () => {
    // This mirrors the actual code pattern after the fix:
    //   const store = useTimelineStore.getState();
    //   const tid = store.addTrack('video');
    //   const track = useTimelineStore.getState().timeline.tracks.find(t => t.id === tid);
    const store = useTimelineStore.getState();
    const tid = store.addTrack('video', 'V1');
    const track = useTimelineStore.getState().timeline.tracks.find((t) => t.id === tid);
    expect(track).toBeDefined();
    expect(track!.kind).toBe('video');
  });
});

describe('timelineStore × selectionStore 联动', () => {
  beforeEach(() => {
    useTimelineStore.getState().resetTimeline();
  });

  it('removeClip 清理对应选择，不遗留悬空引用', async () => {
    const { useSelectionStore } = await import('./selectionStore');
    const tid = useTimelineStore.getState().addTrack('video');
    const cid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 5 });
    useSelectionStore.getState().selectClip(cid);
    expect(useSelectionStore.getState().selectedClipIds).toContain(cid);

    useTimelineStore.getState().removeClip(cid);
    expect(useSelectionStore.getState().selectedClipIds).not.toContain(cid);
  });

  it('removeTrack 清理该轨道全部 clip 的选择与 selectedTrackId', async () => {
    const { useSelectionStore } = await import('./selectionStore');
    const tid = useTimelineStore.getState().addTrack('video');
    const cid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 5 });
    useSelectionStore.getState().selectClip(cid);
    useSelectionStore.getState().selectTrack(tid);

    useTimelineStore.getState().removeTrack(tid);
    expect(useSelectionStore.getState().selectedClipIds).toHaveLength(0);
    expect(useSelectionStore.getState().selectedTrackId).toBeNull();
  });

  it('rippleDelete 清理对应选择', async () => {
    const { useSelectionStore } = await import('./selectionStore');
    const tid = useTimelineStore.getState().addTrack('video');
    const cid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 5 });
    useSelectionStore.getState().selectClip(cid);

    useTimelineStore.getState().rippleDelete(cid);
    expect(useSelectionStore.getState().selectedClipIds).not.toContain(cid);
  });

  it('setTimeline 同步预览时长并清空选择', async () => {
    const { useSelectionStore } = await import('./selectionStore');
    const { usePreviewStore } = await import('./previewStore');
    const tid = useTimelineStore.getState().addTrack('video');
    const cid = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 5 });
    useSelectionStore.getState().selectClip(cid);

    const tl = useTimelineStore.getState().timeline;
    useTimelineStore.getState().setTimeline({ ...tl, duration_sec: 42 });
    expect(usePreviewStore.getState().durationSec).toBe(42);
    expect(useSelectionStore.getState().selectedClipIds).toHaveLength(0);
  });
});

describe('updateTrackClips (字幕样式整层级联)', () => {
  // 状态隔离：每个用例从干净的初始状态开始，防止 store 状态泄漏
  beforeEach(() => {
    useTimelineStore.setState({ timeline: createEmptyTimeline(), isDirty: false });
  });

  it('级联：updates 应用到轨道上的全部 clip', () => {
    const tid = useTimelineStore.getState().addTrack('caption');
    useTimelineStore.getState().addClip(tid, { kind: 'caption', start_sec: 0, duration_sec: 2 });
    useTimelineStore.getState().addClip(tid, { kind: 'caption', start_sec: 2, duration_sec: 3 });

    useTimelineStore.getState().updateTrackClips(tid, { stroke_width: 4, font_weight: 'bold' });

    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    expect(clips).toHaveLength(2);
    for (const c of clips) {
      expect(c.stroke_width).toBe(4);
      expect(c.font_weight).toBe('bold');
    }
  });

  it('无该轨道：幂等且不抛错，其他轨道不受影响', () => {
    const tid = useTimelineStore.getState().addTrack('caption');
    useTimelineStore.getState().addClip(tid, { kind: 'caption', start_sec: 0, duration_sec: 5 });

    expect(() => {
      useTimelineStore.getState().updateTrackClips('no_such_track', { stroke_width: 4 });
    }).not.toThrow();

    const track = useTimelineStore.getState().timeline.tracks[0];
    expect(track.clips[0].stroke_width).toBeNull();
    expect(useTimelineStore.getState().timeline.duration_sec).toBeCloseTo(5, 5);
  });

  it('仅更新目标轨道，不影响其他轨道 clip', () => {
    const captionTid = useTimelineStore.getState().addTrack('caption');
    const textTid = useTimelineStore.getState().addTrack('text');
    useTimelineStore.getState().addClip(captionTid, { kind: 'caption', start_sec: 0, duration_sec: 2 });
    useTimelineStore.getState().addClip(textTid, { kind: 'text', start_sec: 0, duration_sec: 2 });

    useTimelineStore.getState().updateTrackClips(captionTid, { stroke_width: 4 });

    const captionClip = useTimelineStore.getState().timeline.tracks.find((t) => t.id === captionTid)!.clips[0];
    const textClip = useTimelineStore.getState().timeline.tracks.find((t) => t.id === textTid)!.clips[0];
    expect(captionClip.stroke_width).toBe(4);
    expect(textClip.stroke_width).toBeNull();
  });

  it('时长重算：更新 duration_sec 后 timeline.duration_sec 随之更新，且置 isDirty', () => {
    const tid = useTimelineStore.getState().addTrack('caption');
    useTimelineStore.getState().addClip(tid, { kind: 'caption', start_sec: 0, duration_sec: 2 });
    useTimelineStore.getState().addClip(tid, { kind: 'caption', start_sec: 2, duration_sec: 3 });
    expect(useTimelineStore.getState().timeline.duration_sec).toBeCloseTo(5, 5);

    useTimelineStore.getState().updateTrackClips(tid, { duration_sec: 8 });

    // max(0+8, 2+8) = 10
    expect(useTimelineStore.getState().timeline.duration_sec).toBeCloseTo(10, 5);
    expect(useTimelineStore.getState().isDirty).toBe(true);
  });

  it('M8 setTimelineMarkers 写入标记并置 isDirty（引擎变更回调持久化路径）', () => {
    const before = useTimelineStore.getState().isDirty;
    useTimelineStore.getState().setTimelineMarkers([{ time: 1.5, name: '片头' }, { time: 12 }]);
    const st = useTimelineStore.getState();
    expect(st.timeline.markers).toEqual([{ time: 1.5, name: '片头' }, { time: 12 }]);
    expect(st.isDirty).toBe(true);
    // reset 后回到空列表
    useTimelineStore.getState().resetTimeline();
    expect(useTimelineStore.getState().timeline.markers).toEqual([]);
    expect(before).toBe(false);
  });

  it('M8 createEmptyTimeline 默认含空 markers（兼容旧数据加载）', () => {
    const tl = useTimelineStore.getState().timeline;
    expect(tl.markers).toEqual([]);
  });
});

describe('timelineStore M1 (rolling/slip/slide 编辑族)', () => {
  beforeEach(() => {
    useTimelineStore.getState().setTimeline(createEmptyTimeline());
  });

  function threeClips() {
    const tid = useTimelineStore.getState().addTrack('video', 'V1');
    const c1 = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 0, duration_sec: 4 });
    const c2 = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 4, duration_sec: 5 });
    const c3 = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 9, duration_sec: 3 });
    return { tid, c1, c2, c3 };
  }

  it('rollingTrim edge=start：边界此消彼长，总时长不变', () => {
    const { c1, c2 } = threeClips();
    useTimelineStore.getState().rollingTrim(c2, -1, 'start');
    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    const a = clips.find((c) => c.id === c1)!;
    const b = clips.find((c) => c.id === c2)!;
    expect(b.start_sec).toBeCloseTo(3, 5); // 前移 1s
    expect(b.duration_sec).toBeCloseTo(6, 5); // 加长 1s
    expect(a.duration_sec).toBeCloseTo(3, 5); // 前片段缩短 1s
    expect(useTimelineStore.getState().timeline.duration_sec).toBeCloseTo(12, 5); // 总长不变
  });

  it('rollingTrim edge=end：后片段起点对向移动', () => {
    const { c2, c3 } = threeClips();
    useTimelineStore.getState().rollingTrim(c2, 1, 'end');
    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    const b = clips.find((c) => c.id === c2)!;
    const d = clips.find((c) => c.id === c3)!;
    expect(b.duration_sec).toBeCloseTo(6, 5); // 加长 1s
    expect(d.start_sec).toBeCloseTo(10, 5); // 后片段起点后移 1s
    expect(d.duration_sec).toBeCloseTo(2, 5); // 后片段缩短 1s
  });

  it('rollingTrim 边界钳制：不能把片段缩到 <0.1s', () => {
    const { c2 } = threeClips();
    useTimelineStore.getState().rollingTrim(c2, 99, 'end'); // 后片段仅 3s，最多缩 2.9s
    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    const b = clips.find((c) => c.id === c2)!;
    expect(b.duration_sec).toBeCloseTo(5 + 2.9, 5); // c2 原时长 5s
  });

  it('slipClip：位置时长不变，素材偏移平移', () => {
    const { c2 } = threeClips();
    const before = useTimelineStore.getState().getClip(c2)!;
    useTimelineStore.getState().slipClip(c2, 0.5);
    const after = useTimelineStore.getState().getClip(c2)!;
    expect(after.start_sec).toBe(before.start_sec);
    expect(after.duration_sec).toBe(before.duration_sec);
    expect(after.source_offset_sec).toBeCloseTo(before.source_offset_sec + 0.5, 5);
    // 负方向钳制到 0
    useTimelineStore.getState().slipClip(c2, -999);
    expect(useTimelineStore.getState().getClip(c2)!.source_offset_sec).toBe(0);
  });

  it('slideClip：移动片段 + 相邻伸缩补位，总时长不变', () => {
    const { c1, c2, c3 } = threeClips();
    useTimelineStore.getState().slideClip(c2, -1);
    const clips = useTimelineStore.getState().timeline.tracks[0].clips;
    const a = clips.find((c) => c.id === c1)!;
    const b = clips.find((c) => c.id === c2)!;
    const d = clips.find((c) => c.id === c3)!;
    expect(b.start_sec).toBeCloseTo(3, 5); // 自身左移 1s
    expect(b.duration_sec).toBeCloseTo(5, 5); // 自身时长不变
    expect(a.duration_sec).toBeCloseTo(3, 5); // 前片段缩短 1s
    expect(d.start_sec).toBeCloseTo(8, 5); // 后片段整体左移 1s
    expect(d.duration_sec).toBeCloseTo(4, 5); // 后片段加长 1s
    expect(useTimelineStore.getState().timeline.duration_sec).toBeCloseTo(12, 5);
  });

  it('slideClip 边界钳制：首片段无前邻时后移受限', () => {
    const { c1 } = threeClips();
    useTimelineStore.getState().slideClip(c1, 1);
    const b = useTimelineStore.getState().getClip(c1)!;
    // 首片段前移 1s → 被 0 边界钳制
    expect(b.start_sec).toBeCloseTo(0, 5);
  });
});

describe('timelineStore M2 (编组)', () => {
  beforeEach(() => {
    useTimelineStore.getState().setTimeline(createEmptyTimeline());
  });

  function twoClipsOnTracks() {
    const v1 = useTimelineStore.getState().addTrack('video', 'V1');
    const v2 = useTimelineStore.getState().addTrack('video', 'V2');
    const a = useTimelineStore.getState().addClip(v1, { kind: 'video', start_sec: 0, duration_sec: 3 });
    const b = useTimelineStore.getState().addClip(v2, { kind: 'video', start_sec: 0, duration_sec: 3 });
    return { a, b };
  }

  it('groupClips 赋予同一 group_id；getGroupClipIds 返回全组', () => {
    const { a, b } = twoClipsOnTracks();
    const gid = useTimelineStore.getState().groupClips([a, b])!;
    expect(gid).toBeTruthy();
    expect(useTimelineStore.getState().getClip(a)!.group_id).toBe(gid);
    expect(useTimelineStore.getState().getClip(b)!.group_id).toBe(gid);
    expect(useTimelineStore.getState().getGroupClipIds(a).sort()).toEqual([a, b].sort());
    expect(useTimelineStore.getState().getGroupClipIds(a)).toHaveLength(2);
  });

  it('少于 2 个片段不成组', () => {
    const { a } = twoClipsOnTracks();
    expect(useTimelineStore.getState().groupClips([a])).toBeNull();
    expect(useTimelineStore.getState().getClip(a)!.group_id).toBeNull();
  });

  it('ungroupClips 清除指定片段的组', () => {
    const { a, b } = twoClipsOnTracks();
    useTimelineStore.getState().groupClips([a, b]);
    useTimelineStore.getState().ungroupClips([a]);
    expect(useTimelineStore.getState().getClip(a)!.group_id).toBeNull();
    expect(useTimelineStore.getState().getClip(b)!.group_id).toBeTruthy();
    expect(useTimelineStore.getState().getGroupClipIds(b)).toEqual([b]);
  });

  it('合并组：新片段加入已有组时沿用组 id', () => {
    const { a, b } = twoClipsOnTracks();
    const v3 = useTimelineStore.getState().addTrack('video', 'V3');
    const c = useTimelineStore.getState().addClip(v3, { kind: 'video', start_sec: 5, duration_sec: 2 });
    const gid = useTimelineStore.getState().groupClips([a, b])!;
    useTimelineStore.getState().groupClips([b, c]);
    expect(useTimelineStore.getState().getClip(c)!.group_id).toBe(gid);
    expect(useTimelineStore.getState().getGroupClipIds(c)).toHaveLength(3);
  });
});

describe('timelineStore C3 (嵌套序列)', () => {
  beforeEach(() => {
    useTimelineStore.getState().setTimeline(createEmptyTimeline());
  });

  function threeClips() {
    const tid = useTimelineStore.getState().addTrack('video', 'V1');
    const c1 = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 2, duration_sec: 4 });
    const c2 = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 6, duration_sec: 5 });
    const c3 = useTimelineStore.getState().addClip(tid, { kind: 'video', start_sec: 11, duration_sec: 3 });
    return { tid, c1, c2, c3 };
  }

  it('createNestedSequence：折叠 2+ 片段为嵌套片段（保留相对时间）', () => {
    const { tid, c1, c2, c3 } = threeClips();
    const nestId = useTimelineStore.getState().createNestedSequence([c1, c2])!;
    expect(nestId).toBeTruthy();
    const st = useTimelineStore.getState();
    const nest = st.getClip(nestId)!;
    expect(nest.nested_timeline).toBeTruthy();
    // 起点 = 最小起点（2s），时长 = 总跨度（2→11 = 9s）
    expect(nest.start_sec).toBeCloseTo(2, 5);
    expect(nest.duration_sec).toBeCloseTo(9, 5);
    // 原片段被移除
    expect(st.getClip(c1)).toBeUndefined();
    expect(st.getClip(c2)).toBeUndefined();
    // 未选中的 c3 保留
    expect(st.getClip(c3)).toBeTruthy();
    // 子时间线内的相对时间：c2 相对 2s 起点 → 4s
    const nestedClips = nest.nested_timeline!.tracks.flatMap((t) => t.clips);
    expect(nestedClips.some((c) => Math.abs(c.start_sec - 4) < 0.001)).toBe(true);
    expect(nestedClips.some((c) => Math.abs(c.start_sec - 0) < 0.001)).toBe(true);
  });

  it('createNestedSequence：少于 2 个片段返回 null', () => {
    const { c1 } = threeClips();
    expect(useTimelineStore.getState().createNestedSequence([c1])).toBeNull();
  });

  it('expandNestedSequence：子片段平铺回原轨道并删除嵌套片段', () => {
    const { c1, c2 } = threeClips();
    const nestId = useTimelineStore.getState().createNestedSequence([c1, c2])!;
    useTimelineStore.getState().expandNestedSequence(nestId);
    const st = useTimelineStore.getState();
    expect(st.getClip(nestId)).toBeUndefined();
    const clips = st.timeline.tracks.flatMap((t) => t.clips);
    // 平铺回 2 个子片段（相对时间 + 父起点）
    expect(clips.length).toBe(3); // 2 个展开 + 原 c3
    const starts = clips.map((c) => c.start_sec).sort((a, b) => a - b);
    expect(starts[0]).toBeCloseTo(2, 5);
    expect(starts[1]).toBeCloseTo(6, 5);
  });
});
