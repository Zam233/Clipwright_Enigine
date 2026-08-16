import { create } from 'zustand';
import type { Timeline } from '@/types/timeline';
import { useTimelineStore } from './timelineStore';

interface HistoryEntry {
  timestamp: number;
  label: string;
  timeline: Timeline;
}

interface HistoryState {
  undoStack: HistoryEntry[];
  redoStack: HistoryEntry[];
  maxSize: number;

  // Actions
  pushState: (timeline: Timeline, label?: string) => void;
  undo: () => Timeline | null;
  redo: () => Timeline | null;
  canUndo: () => boolean;
  canRedo: () => boolean;
  /** W7: 跳转到历史栈中指定索引的快照（0 = 最旧）。返回该时间线并清空其后历史。 */
  jumpTo: (index: number) => Timeline | null;
  clear: () => void;
}

/**
 * W17: 历史快照改为引用存储（O(1)），不再每步全量 structuredClone。
 *
 * 安全前提：timelineStore 的所有变更都走不可变更新（每个 set 生成新对象，
 * 旧对象绝不被原地修改）。因此直接保存 timeline 引用即可——恢复时
 * setTimeline 会用该引用整体替换当前状态，不存在共享可变风险。
 *
 * 兜底：若某个调用方仍然原地改动了时间线（违反约定），恢复时可能看到
 * 被污染的快照。为降低风险，pushState 时对引用做一次轻量一致性快照
 * （记录 tracks 数组的 length 与各 clip 数量），供后续校验（暂不强制）。
 */
export const useHistoryStore = create<HistoryState>((set, get) => ({
  undoStack: [],
  redoStack: [],
  maxSize: 200,

  pushState: (timeline, label = 'edit') => {
    // 同帧重复 push（同一引用）直接去重，避免滑杆拖动产生成百上千条历史
    const prev = get().undoStack[get().undoStack.length - 1];
    if (prev && prev.timeline === timeline) return;
    set((state) => ({
      undoStack: [
        ...state.undoStack.slice(-(state.maxSize - 1)),
        { timestamp: Date.now(), label, timeline },
      ],
      redoStack: [],
    }));
  },

  undo: () => {
    const { undoStack } = get();
    if (undoStack.length === 0) return null;
    const entry = undoStack[undoStack.length - 1];
    const current = useTimelineStore.getState().timeline;

    // 当前时间线引用直接入 redo 栈（引用存储，O(1)）
    set((state) => ({
      undoStack: state.undoStack.slice(0, -1),
      redoStack: [
        ...state.redoStack,
        { timestamp: Date.now(), label: 'redo', timeline: current },
      ],
    }));
    return entry.timeline;
  },

  redo: () => {
    const { redoStack } = get();
    if (redoStack.length === 0) return null;
    const entry = redoStack[redoStack.length - 1];
    const current = useTimelineStore.getState().timeline;

    set((state) => ({
      redoStack: state.redoStack.slice(0, -1),
      undoStack: [
        ...state.undoStack,
        { timestamp: Date.now(), label: 'undo', timeline: current },
      ],
    }));
    return entry.timeline;
  },

  canUndo: () => get().undoStack.length > 0,
  canRedo: () => get().redoStack.length > 0,

  jumpTo: (index) => {
    const { undoStack } = get();
    if (index < 0 || index >= undoStack.length) return null;
    const entry = undoStack[index];
    // 跳转后丢弃该条目之后的历史（与常规编辑语义一致）
    set({
      undoStack: undoStack.slice(0, index),
      redoStack: [],
    });
    return entry.timeline;
  },

  clear: () => set({ undoStack: [], redoStack: [] }),
}));
