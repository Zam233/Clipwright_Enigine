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
  clear: () => void;
}

export const useHistoryStore = create<HistoryState>((set, get) => ({
  undoStack: [],
  redoStack: [],
  maxSize: 200,

  pushState: (timeline, label = 'edit') => {
    let snapshot: Timeline;
    try {
      snapshot = structuredClone(timeline);
    } catch {
      return;
    }
    set((state) => ({
      undoStack: [
        ...state.undoStack.slice(-(state.maxSize - 1)),
        { timestamp: Date.now(), label, timeline: snapshot },
      ],
      redoStack: [],
    }));
  },

  undo: () => {
    const { undoStack } = get();
    if (undoStack.length === 0) return null;
    const entry = undoStack[undoStack.length - 1];
    const current = useTimelineStore.getState().timeline;

    // 当前时间线含不可克隆值（函数/DOM/Symbol 等）时跳过 redo 快照，但仍出栈
    let snapshot: Timeline | null = null;
    try {
      snapshot = structuredClone(current);
    } catch {
      snapshot = null;
    }

    set((state) => ({
      undoStack: state.undoStack.slice(0, -1),
      redoStack: snapshot
        ? [...state.redoStack, { timestamp: Date.now(), label: 'redo', timeline: snapshot }]
        : state.redoStack,
    }));
    return entry.timeline;
  },

  redo: () => {
    const { redoStack } = get();
    if (redoStack.length === 0) return null;
    const entry = redoStack[redoStack.length - 1];
    const current = useTimelineStore.getState().timeline;

    // 当前时间线含不可克隆值时跳过 undo 快照，但仍出栈
    let snapshot: Timeline | null = null;
    try {
      snapshot = structuredClone(current);
    } catch {
      snapshot = null;
    }

    set((state) => ({
      redoStack: state.redoStack.slice(0, -1),
      undoStack: snapshot
        ? [...state.undoStack, { timestamp: Date.now(), label: 'undo', timeline: snapshot }]
        : state.undoStack,
    }));
    return entry.timeline;
  },

  canUndo: () => get().undoStack.length > 0,
  canRedo: () => get().redoStack.length > 0,
  clear: () => set({ undoStack: [], redoStack: [] }),
}));
