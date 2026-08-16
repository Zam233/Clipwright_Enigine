import { useEffect } from 'react';
import { keybindingEngine } from './KeybindingEngine';
import { useTimelineStore } from '@/stores/timelineStore';
import { useHistoryStore } from '@/stores/historyStore';
import { usePreviewStore } from '@/stores/previewStore';
import { useSelectionStore } from '@/stores/selectionStore';
import { useProjectStore } from '@/stores/projectStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { uid } from '@/lib/utils';
import { clipClipboard } from '@/features/timeline/components/EditorToolbar';
import {
  extractCopyableAttributes, filterFieldsForKind,
  useClipAttributeClipboard,
} from '@/features/properties/clipAttributeClipboard';
import { toast } from '@/stores/toastStore';
import type { Clip } from '@/types/timeline';

export function useGlobalKeybindings() {

  useEffect(() => {
    const undo = () => {
      const tl = useHistoryStore.getState().undo();
      if (tl) useTimelineStore.getState().setTimeline(tl);
    };
    const redo = () => {
      const tl = useHistoryStore.getState().redo();
      if (tl) useTimelineStore.getState().setTimeline(tl);
    };

    const splitSelected = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      const store = useTimelineStore.getState();
      const t = usePreviewStore.getState().currentTimeSec;
      // 一次手势只推一个撤销点：先快照再批量切分
      let pushed = false;
      for (const cid of sel) {
        for (const tr of store.timeline.tracks) {
          const clip = tr.clips.find((c) => c.id === cid);
          if (clip && t > clip.start_sec && t < clip.start_sec + clip.duration_sec) {
            if (!pushed) {
              useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'split');
              pushed = true;
            }
            store.splitClip(clip.id, t);
            break;
          }
        }
      }
    };

    const toggleLoop = () => usePreviewStore.getState().toggleLoop();

    const deleteSelected = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'delete');
      const store = useTimelineStore.getState();
      sel.forEach((id) => store.removeClip(id));
      useSelectionStore.getState().deselectAll();
    };

    const toggleMuteSelectedTrack = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const cid = sel[0];
      for (const tr of store.timeline.tracks) {
        if (tr.clips.some((c) => c.id === cid)) {
          store.toggleTrackMute(tr.id);
          break;
        }
      }
    };

    const toggleLockSelectedTrack = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const cid = sel[0];
      for (const tr of store.timeline.tracks) {
        if (tr.clips.some((c) => c.id === cid)) {
          store.toggleTrackLock(tr.id);
          break;
        }
      }
    };

    const saveProject = () => {
      useProjectStore.getState().requestSave();
    };

    const copyClips = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const found: typeof clipClipboard.clips = [];
      for (const tr of store.timeline.tracks) {
        for (const c of tr.clips) {
          if (sel.includes(c.id)) found.push(c);
        }
      }
      if (found.length > 0) {
        // 按起始时间排序：粘贴偏移以最早的片段为锚点
        clipClipboard.clips = [...found].sort((a, b) => a.start_sec - b.start_sec);
      }
    };

    const pasteClips = () => {
      if (clipClipboard.clips.length === 0) return;
      const store = useTimelineStore.getState();
      const t = usePreviewStore.getState().currentTimeSec;
      useHistoryStore.getState().pushState(store.timeline, 'paste');
      for (const src of clipClipboard.clips) {
        const newId = uid('clip');
        const track = store.timeline.tracks.find((tr) => tr.id === src.track_id || tr.kind === src.kind);
        if (!track || track.locked) continue;
        store.addClip(track.id, {
          ...src,
          id: newId,
          start_sec: t + (src.start_sec - clipClipboard.clips[0].start_sec),
          asset_id: src.asset_id,
          kind: src.kind,
          keyframes: src.keyframes?.map((kf) => ({ ...kf })),
        });
      }
    };

    // M3: 复制/粘贴属性（跨项目）
    const copyAttributes = () => {
      const store = useTimelineStore.getState();
      const firstId = useSelectionStore.getState().selectedClipIds[0];
      if (!firstId) return;
      for (const tr of store.timeline.tracks) {
        const c = tr.clips.find((cc) => cc.id === firstId);
        if (c) {
          useClipAttributeClipboard.getState().set(extractCopyableAttributes(c), c.kind as Clip['kind']);
          toast('属性已复制', 'success');
          return;
        }
      }
    };

    const pasteAttributes = () => {
      const fields = useClipAttributeClipboard.getState().fields;
      if (!fields) return;
      const store = useTimelineStore.getState();
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      useHistoryStore.getState().pushState(store.timeline, 'paste-attrs');
      for (const tr of store.timeline.tracks) {
        for (const c of tr.clips) {
          if (sel.includes(c.id)) {
            const filtered = filterFieldsForKind(fields, c.kind as Clip['kind']);
            const entries = Object.entries(filtered);
            if (entries.length > 0) {
              store.updateClip(c.id, Object.fromEntries(entries) as Partial<Clip>);
            }
          }
        }
      }
      toast('属性已粘贴', 'success');
    };

    const cutClips = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;      const store = useTimelineStore.getState();
      const found: typeof clipClipboard.clips = [];
      for (const tr of store.timeline.tracks) {
        for (const c of tr.clips) {
          if (sel.includes(c.id)) found.push(c);
        }
      }
      if (found.length > 0) {
        clipClipboard.clips = [...found].sort((a, b) => a.start_sec - b.start_sec);
        useHistoryStore.getState().pushState(store.timeline, 'cut');
        sel.forEach((id) => store.removeClip(id));
        useSelectionStore.getState().deselectAll();
      }
    };

    const duplicateClips = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const found: typeof clipClipboard.clips = [];
      for (const tr of store.timeline.tracks) {
        for (const c of tr.clips) {
          if (sel.includes(c.id)) found.push(c);
        }
      }
      if (found.length === 0) return;
      // Sort by start time to preserve relative positions
      found.sort((a, b) => a.start_sec - b.start_sec);
      const offset = 1; // 1 second offset for pasted duplicates
      useHistoryStore.getState().pushState(store.timeline, 'duplicate');
      for (const src of found) {
        const newId = uid('clip');
        const track = store.timeline.tracks.find((tr) => tr.id === src.track_id || tr.kind === src.kind);
        if (!track || track.locked) continue;
        store.addClip(track.id, {
          ...src,
          id: newId,
          start_sec: src.start_sec + offset,
          asset_id: src.asset_id,
          kind: src.kind,
          keyframes: src.keyframes?.map((kf) => ({ ...kf })),
        });
      }
    };

    const selectAll = () => {
      const store = useTimelineStore.getState();
      const sel = useSelectionStore.getState();
      sel.deselectAll();
      for (const tr of store.timeline.tracks) {
        if (tr.locked) continue;
        for (const c of tr.clips) {
          sel.selectClip(c.id, true);
        }
      }
    };

    const deselectAll = () => {
      useSelectionStore.getState().deselectAll();
    };

    const toolSelect = () => useSelectionStore.getState().setToolMode('select');
    const toolRazor = () => useSelectionStore.getState().setToolMode('razor');
    const toolRange = () => useSelectionStore.getState().setToolMode('range');

    const nudgeLeft = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const fps = store.timeline.fps || 30;
      const frame = 1 / fps;
      useHistoryStore.getState().pushState(store.timeline, 'nudge');
      for (const cid of sel) {
        const clip = store.getClip(cid);
        if (!clip) continue;
        store.moveClip(cid, clip.track_id, Math.max(0, clip.start_sec - frame));
      }
    };

    const nudgeRight = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const fps = store.timeline.fps || 30;
      const frame = 1 / fps;
      useHistoryStore.getState().pushState(store.timeline, 'nudge');
      for (const cid of sel) {
        const clip = store.getClip(cid);
        if (!clip) continue;
        store.moveClip(cid, clip.track_id, clip.start_sec + frame);
      }
    };

    const trimStartIn = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const fps = store.timeline.fps || 30;
      const frame = 1 / fps;
      useHistoryStore.getState().pushState(store.timeline, 'trim');
      for (const cid of sel) {
        const clip = store.getClip(cid);
        if (!clip) continue;
        store.trimClipStart(cid, Math.max(0, clip.start_sec + frame));
      }
    };

    const trimEndOut = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const fps = store.timeline.fps || 30;
      const frame = 1 / fps;
      useHistoryStore.getState().pushState(store.timeline, 'trim');
      for (const cid of sel) {
        const clip = store.getClip(cid);
        if (!clip) continue;
        const newEnd = clip.start_sec + clip.duration_sec - frame;
        if (newEnd > clip.start_sec + 0.1) {
          store.trimClipEnd(cid, newEnd);
        }
      }
    };

    // M1: slip（Alt+←/→ 平移素材窗口）/ slide（Shift+Alt+←/→ 移动片段并补位）
    const slipBy = (dir: 1 | -1) => {
      const sel = useSelectionStore.getState().selectedClipIds;
      const first = sel[0];
      if (!first) return;
      const store = useTimelineStore.getState();
      const fps = store.timeline.fps || 30;
      const frame = 1 / fps;
      useHistoryStore.getState().pushState(store.timeline, 'slip');
      sel.forEach((cid) => store.slipClip(cid, dir * frame));
    };
    const slideBy = (dir: 1 | -1) => {
      const sel = useSelectionStore.getState().selectedClipIds;
      const first = sel[0];
      if (!first) return;
      const store = useTimelineStore.getState();
      const fps = store.timeline.fps || 30;
      const frame = 1 / fps;
      useHistoryStore.getState().pushState(store.timeline, 'slide');
      sel.forEach((cid) => store.slideClip(cid, dir * frame));
    };

    // M2: 编组 / 解组
    const groupSelected = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length < 2) return;
      const store = useTimelineStore.getState();
      useHistoryStore.getState().pushState(store.timeline, 'group');
      store.groupClips(sel);
      toast('已编组', 'success');
    };
    const ungroupSelected = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      useHistoryStore.getState().pushState(store.timeline, 'ungroup');
      store.ungroupClips(sel);
      toast('已解组', 'success');
    };

    const moveClipUp = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      useHistoryStore.getState().pushState(store.timeline, 'move-track');
      for (const cid of sel) {
        const clip = store.getClip(cid);
        if (!clip) continue;
        const tracks = store.timeline.tracks;
        const idx = tracks.findIndex((t) => t.id === clip.track_id);
        if (idx <= 0) continue;
        const targetTrack = tracks[idx - 1];
        if (targetTrack.locked) continue;
        if (targetTrack.kind !== clip.kind) continue;
        store.moveClip(cid, targetTrack.id, clip.start_sec);
      }
    };

    const moveClipDown = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      useHistoryStore.getState().pushState(store.timeline, 'move-track');
      for (const cid of sel) {
        const clip = store.getClip(cid);
        if (!clip) continue;
        const tracks = store.timeline.tracks;
        const idx = tracks.findIndex((t) => t.id === clip.track_id);
        if (idx < 0 || idx >= tracks.length - 1) continue;
        const targetTrack = tracks[idx + 1];
        if (targetTrack.locked) continue;
        if (targetTrack.kind !== clip.kind) continue;
        store.moveClip(cid, targetTrack.id, clip.start_sec);
      }
    };

    const addKeyframeAtPlayhead = () => {
      const sel = useSelectionStore.getState().selectedClipIds;
      if (sel.length === 0) return;
      const store = useTimelineStore.getState();
      const t = usePreviewStore.getState().currentTimeSec;
      // 一次手势只推一个撤销点
      useHistoryStore.getState().pushState(store.timeline, 'add-keyframe');
      for (const cid of sel) {
        const clip = store.getClip(cid);
        if (!clip) continue;
        const relT = clip.duration_sec > 0 ? (t - clip.start_sec) / clip.duration_sec : 0;
        if (relT < 0 || relT > 1) continue;
        const existing = clip.keyframes?.find((k) => Math.abs(k.time - relT) < 0.005);
        if (existing) {
          store.updateClip(cid, {
            keyframes: clip.keyframes?.map((k) =>
              Math.abs(k.time - relT) < 0.005
                ? { ...k, properties: { ...k.properties, opacity: clip.opacity, scale: 1 } }
                : k,
            ),
          });
        } else {
          store.addKeyframe(cid, relT, { opacity: clip.opacity, scale: 1 });
        }
      }
    };

    const unsub = keybindingEngine.registerMany([
      { id: 'undo', combo: 'ctrl+z', label: '撤销', category: '通用',
        when: () => useHistoryStore.getState().undoStack.length > 0, handler: undo },
      { id: 'redo', combo: 'ctrl+shift+z', label: '重做', category: '通用',
        when: () => useHistoryStore.getState().redoStack.length > 0, handler: redo },
      { id: 'play', combo: 'space', label: '播放 / 暂停', category: '传输控制',
        handler: () => usePreviewStore.getState().togglePlay() },
      { id: 'step-back', combo: 'arrowleft', label: '上一帧', category: '传输控制',
        handler: () => { usePreviewStore.getState().setPlaying(false); usePreviewStore.getState().stepBackward(); } },
      { id: 'step-fwd', combo: 'arrowright', label: '下一帧', category: '传输控制',
        handler: () => { usePreviewStore.getState().setPlaying(false); usePreviewStore.getState().stepForward(); } },
      { id: 'seek-start', combo: 'home', label: '跳到开头', category: '传输控制',
        handler: () => usePreviewStore.getState().seekToStart() },
      { id: 'seek-end', combo: 'end', label: '跳到结尾', category: '传输控制',
        handler: () => usePreviewStore.getState().seekToEnd() },
      { id: 'shuttle-rev', combo: 'j', label: '倒放 (J)', category: '传输控制',
        when: () => !usePreviewStore.getState().isPlaying || usePreviewStore.getState().shuttleSpeed !== -1,
        handler: () => usePreviewStore.getState().shuttleReverse() },
      { id: 'shuttle-stop', combo: 'k', label: '暂停 (K)', category: '传输控制',
        handler: () => usePreviewStore.getState().shuttleStop() },
      { id: 'shuttle-fwd', combo: 'l', label: '播放 (L)', category: '传输控制',
        when: () => !usePreviewStore.getState().isPlaying || usePreviewStore.getState().shuttleSpeed !== 1,
        handler: () => usePreviewStore.getState().shuttleForward() },
      { id: 'loop', combo: '/', label: '循环播放开关', category: '传输控制',
        handler: toggleLoop },
      { id: 'marker-in', combo: 'i', label: '设置入点 (I)', category: '标记',
        handler: () => usePreviewStore.getState().setMarkerIn() },
      { id: 'marker-out', combo: 'o', label: '设置出点 (O)', category: '标记',
        handler: () => usePreviewStore.getState().setMarkerOut() },
      { id: 'split', combo: 's', label: '分割片段 (S)', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: splitSelected },
      { id: 'delete', combo: 'delete', label: '删除片段', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: deleteSelected },
      { id: 'delete-bs', combo: 'backspace', label: '删除片段 (退格)', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: deleteSelected },
      { id: 'mute-track', combo: 'ctrl+m', label: '静音轨道 (Ctrl+M)', category: '轨道',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: toggleMuteSelectedTrack },
      { id: 'lock-track', combo: 'shift+l', label: '锁定轨道', category: '轨道',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: toggleLockSelectedTrack },
      { id: 'cheatsheet', combo: 'ctrl+/', label: '快捷键速查表', category: '通用',
        handler: () => {
          const s = useSettingsStore.getState();
          s.setCheatSheetOpen(!s.cheatSheetOpen);
        } },
      { id: 'save', combo: 'ctrl+s', label: '保存项目', category: '通用',
        handler: saveProject },
      { id: 'copy', combo: 'ctrl+c', label: '复制片段', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: copyClips },
      { id: 'paste', combo: 'ctrl+v', label: '粘贴片段', category: '编辑',
        when: () => clipClipboard.clips.length > 0,
        handler: pasteClips },
      { id: 'cut', combo: 'ctrl+x', label: '剪切片段', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: cutClips },
      // M3: 复制/粘贴属性（跨项目，localStorage 持久化）
      { id: 'copy-attrs', combo: 'ctrl+shift+c', label: '复制属性', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: copyAttributes },
      { id: 'paste-attrs', combo: 'ctrl+shift+v', label: '粘贴属性', category: '编辑',
        when: () => useClipAttributeClipboard.getState().fields !== null,
        handler: pasteAttributes },
      { id: 'duplicate', combo: 'ctrl+d', label: '复制片段到后方', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: duplicateClips },
      { id: 'select-all', combo: 'ctrl+a', label: '全选片段', category: '编辑',
        handler: selectAll },
      { id: 'deselect', combo: 'escape', label: '取消选择', category: '编辑',
        handler: deselectAll },
      { id: 'tool-select', combo: 'v', label: '选择工具 (V)', category: '工具',
        handler: toolSelect },
      { id: 'tool-razor', combo: 'c', label: '剃刀工具 (C)', category: '工具',
        handler: toolRazor },
      { id: 'tool-range', combo: 'r', label: '范围选择 (R)', category: '工具',
        handler: toolRange },
      // M10: 吸附切换快捷键（Alt+S，与 Ctrl+S 保存不冲突）
      { id: 'toggle-snap', combo: 'alt+s', label: '切换吸附 (Alt+S)', category: '时间轴',
        handler: () => {
          const s = useSettingsStore.getState();
          s.setSnapEnabled(!s.snapEnabled);
        },
      },
      { id: 'zoom-fit', combo: 'f', label: '跳至选中片段', category: '时间轴',
        handler: () => {
          const sel = useSelectionStore.getState().selectedClipIds;
          if (sel.length > 0) {
            const store = useTimelineStore.getState();
            for (const tr of store.timeline.tracks) {
              for (const c of tr.clips) {
                if (sel.includes(c.id)) {
                  usePreviewStore.getState().setCurrentTime(c.start_sec);
                  return;
                }
              }
            }
          }
        },
      },
      { id: 'nudge-left', combo: 'shift+[', label: '左移一帧', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: nudgeLeft },
      { id: 'nudge-right', combo: 'shift+]', label: '右移一帧', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: nudgeRight },
      { id: 'trim-start', combo: '[', label: '修剪入点 (左剪一帧)', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: trimStartIn },
      { id: 'trim-end', combo: ']', label: '修剪出点 (右剪一帧)', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: trimEndOut },
      // M1: slip / slide（避免与默认浏览器 Alt+←→ 前进后退冲突，用 Shift 组合区分）
      { id: 'slip-left', combo: 'shift+alt+arrowleft', label: 'Slip 左移一帧', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: () => slipBy(-1) },
      { id: 'slip-right', combo: 'shift+alt+arrowright', label: 'Slip 右移一帧', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: () => slipBy(1) },
      { id: 'slide-left', combo: 'ctrl+alt+arrowleft', label: 'Slide 左移一帧', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: () => slideBy(-1) },
      { id: 'slide-right', combo: 'ctrl+alt+arrowright', label: 'Slide 右移一帧', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: () => slideBy(1) },
      // M2: 编组 / 解组
      { id: 'group-clips', combo: 'ctrl+g', label: '编组 (Ctrl+G)', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length >= 2,
        handler: groupSelected },
      { id: 'ungroup-clips', combo: 'ctrl+shift+g', label: '解组 (Ctrl+Shift+G)', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: ungroupSelected },
      { id: 'move-clip-up', combo: 'ctrl+arrowup', label: '上移轨道', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: moveClipUp },
      { id: 'move-clip-down', combo: 'ctrl+arrowdown', label: '下移轨道', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: moveClipDown },
      { id: 'add-keyframe', combo: 'ctrl+shift+k', label: '在播放头添加关键帧', category: '编辑',
        when: () => useSelectionStore.getState().selectedClipIds.length > 0,
        handler: addKeyframeAtPlayhead },
      { id: 'export-page', combo: 'ctrl+e', label: '打开导出页面', category: '通用',
        handler: () => {
          const pid = useProjectStore.getState().projectId;
          if (!pid) return;
          import('@/router').then((m) => m.router.navigate({ to: '/export/$projectId', params: { projectId: pid } }));
        } },
    ]);

    keybindingEngine.attach();
    return () => { unsub(); keybindingEngine.detach(); };
  }, []);

  const cheatSheetOpen = useSettingsStore((s) => s.cheatSheetOpen);

  return { cheatSheetOpen, setCheatSheetOpen: (v: boolean) => useSettingsStore.getState().setCheatSheetOpen(v) };
}
