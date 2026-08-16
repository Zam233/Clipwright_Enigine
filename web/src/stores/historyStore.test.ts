import { describe, it, expect, beforeEach } from 'vitest';
import { useHistoryStore } from './historyStore';
import { useTimelineStore } from './timelineStore';
import { createEmptyTimeline, createDefaultClip } from '@/types/timeline';
import type { Timeline } from '@/types/timeline';

function makeTimeline(id: string): Timeline {
  return { ...createEmptyTimeline(id), duration_sec: 5 };
}

/** Timeline with a non-cloneable value (function) inside a clip's metadata. */
function makeNonCloneableTimeline(id: string): Timeline {
  const tl = makeTimeline(id);
  return {
    ...tl,
    tracks: [
      {
        id: 'track-1',
        name: 'V1',
        kind: 'video' as const,
        index: 0,
        locked: false,
        muted: false,
        clips: [
          {
            ...createDefaultClip({ id: 'clip-1', kind: 'video', track_id: 'track-1' }),
            metadata: { bad: () => {} },
          },
        ],
      },
    ],
  };
}

describe('historyStore', () => {
  beforeEach(() => {
    useHistoryStore.setState({ undoStack: [], redoStack: [] });
    useTimelineStore.setState({ timeline: createEmptyTimeline(), isDirty: false });
  });

  it('pushState then undo returns the previous timeline and pops', () => {
    const tl1 = makeTimeline('tl-1');
    const tl2 = makeTimeline('tl-2');

    // 真实用法：编辑前把当前时间线压栈
    useTimelineStore.getState().setTimeline(tl1);
    useHistoryStore.getState().pushState(tl1);
    useTimelineStore.getState().setTimeline(tl2);
    useHistoryStore.getState().pushState(tl2);
    expect(useHistoryStore.getState().undoStack).toHaveLength(2);
    expect(useHistoryStore.getState().canUndo()).toBe(true);
    expect(useHistoryStore.getState().canRedo()).toBe(false);

    const result = useHistoryStore.getState().undo();
    expect(result?.id).toBe('tl-2'); // 返回栈顶条目（最近一次编辑前的快照）
    expect(useHistoryStore.getState().undoStack).toHaveLength(1);
    expect(useHistoryStore.getState().canUndo()).toBe(true);

    const result2 = useHistoryStore.getState().undo();
    expect(result2?.id).toBe('tl-1');
    expect(useHistoryStore.getState().undoStack).toHaveLength(0);
    expect(useHistoryStore.getState().canUndo()).toBe(false);
    expect(useHistoryStore.getState().canRedo()).toBe(true); // 撤销产生的 redo 快照
  });

  it('W17: 引用存储 — 含函数等不可克隆值的时间线也能正常入栈/撤销（不再需要 clone 防护）', () => {
    const tl1 = makeTimeline('tl-1');
    const tl2 = makeTimeline('tl-2');
    const bad = makeNonCloneableTimeline('tl-bad');

    useTimelineStore.getState().setTimeline(tl1);
    useHistoryStore.getState().pushState(tl1);
    useTimelineStore.getState().setTimeline(tl2);
    useHistoryStore.getState().pushState(tl2);

    // 引用存储：不可克隆时间线照常入栈
    expect(() => useHistoryStore.getState().pushState(bad)).not.toThrow();
    expect(useHistoryStore.getState().undoStack).toHaveLength(3);

    // 把“当前”时间线换成不可克隆值后再撤销
    useTimelineStore.setState({ timeline: bad });
    let result: Timeline | null = null;
    let threw = false;
    try {
      result = useHistoryStore.getState().undo();
    } catch {
      threw = true;
    }
    expect(threw).toBe(false);

    // 出栈返回栈顶条目（bad 本身），redo 快照 = 当前引用（bad）也可存储
    expect(result?.id).toBe('tl-bad');
    expect(useHistoryStore.getState().undoStack).toHaveLength(2);
    expect(useHistoryStore.getState().canUndo()).toBe(true);
    expect(useHistoryStore.getState().redoStack).toHaveLength(1);
    expect(useHistoryStore.getState().canRedo()).toBe(true);
  });

  it('W17: 引用存储 — redo 同样处理不可克隆值', () => {
    const tl1 = makeTimeline('tl-1');
    const tl2 = makeTimeline('tl-2');
    const tl3 = makeTimeline('tl-3');
    const bad = makeNonCloneableTimeline('tl-bad');

    useTimelineStore.getState().setTimeline(tl1);
    useHistoryStore.getState().pushState(tl1);
    useTimelineStore.getState().setTimeline(tl2);
    useHistoryStore.getState().pushState(tl2);
    useTimelineStore.getState().setTimeline(tl3);

    expect(useHistoryStore.getState().undo()?.id).toBe('tl-2');
    useTimelineStore.setState({ timeline: tl2 });
    expect(useHistoryStore.getState().undo()?.id).toBe('tl-1');
    expect(useHistoryStore.getState().redoStack).toHaveLength(2);
    expect(useHistoryStore.getState().canRedo()).toBe(true);

    useTimelineStore.setState({ timeline: bad });
    let result: Timeline | null = null;
    let threw = false;
    try {
      result = useHistoryStore.getState().redo();
    } catch {
      threw = true;
    }
    expect(threw).toBe(false);

    expect(result?.id).toBe('tl-2');
    expect(useHistoryStore.getState().redoStack).toHaveLength(1);
    expect(useHistoryStore.getState().canRedo()).toBe(true);
    expect(useHistoryStore.getState().undoStack).toHaveLength(1);
    expect(useHistoryStore.getState().canUndo()).toBe(true);
  });

  it('W17: 同引用重复 push 去重', () => {
    const tl1 = makeTimeline('tl-1');
    useHistoryStore.getState().pushState(tl1);
    useHistoryStore.getState().pushState(tl1);
    useHistoryStore.getState().pushState(tl1);
    expect(useHistoryStore.getState().undoStack).toHaveLength(1);
  });

  it('W7: jumpTo 返回目标快照并丢弃其后历史', () => {
    const tl1 = makeTimeline('tl-1');
    const tl2 = makeTimeline('tl-2');
    const tl3 = makeTimeline('tl-3');
    useHistoryStore.getState().pushState(tl1);
    useHistoryStore.getState().pushState(tl2);
    useHistoryStore.getState().pushState(tl3);
    expect(useHistoryStore.getState().undoStack).toHaveLength(3);

    const result = useHistoryStore.getState().jumpTo(0); // 跳到最旧
    expect(result?.id).toBe('tl-1');
    expect(useHistoryStore.getState().undoStack).toHaveLength(0);
    expect(useHistoryStore.getState().canUndo()).toBe(false);

    // 越界返回 null
    useHistoryStore.getState().pushState(tl1);
    expect(useHistoryStore.getState().jumpTo(5)).toBeNull();
    expect(useHistoryStore.getState().jumpTo(-1)).toBeNull();
  });
});
