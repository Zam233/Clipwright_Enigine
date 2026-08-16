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

  it('undo with a non-cloneable current timeline does not throw and still returns the prior entry', () => {
    const tl1 = makeTimeline('tl-1');
    const tl2 = makeTimeline('tl-2');
    const bad = makeNonCloneableTimeline('tl-bad');

    useTimelineStore.getState().setTimeline(tl1);
    useHistoryStore.getState().pushState(tl1);
    useTimelineStore.getState().setTimeline(tl2);
    useHistoryStore.getState().pushState(tl2);

    // pushState 已加防护：遇到不可克隆时间线不抛错、不入栈
    expect(() => useHistoryStore.getState().pushState(bad)).not.toThrow();
    expect(useHistoryStore.getState().undoStack).toHaveLength(2);

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

    // 仍出栈并返回栈顶条目…
    expect(result?.id).toBe('tl-2');
    expect(useHistoryStore.getState().undoStack).toHaveLength(1);
    expect(useHistoryStore.getState().canUndo()).toBe(true);
    // …但跳过失败的 redo 快照（redoStack 不增长）
    expect(useHistoryStore.getState().redoStack).toHaveLength(0);
    expect(useHistoryStore.getState().canRedo()).toBe(false);
  });

  it('redo with a non-cloneable current timeline does not throw, returns the entry, and pops redoStack', () => {
    const tl1 = makeTimeline('tl-1');
    const tl2 = makeTimeline('tl-2');
    const tl3 = makeTimeline('tl-3');
    const bad = makeNonCloneableTimeline('tl-bad');

    useTimelineStore.getState().setTimeline(tl1);
    useHistoryStore.getState().pushState(tl1);
    useTimelineStore.getState().setTimeline(tl2);
    useHistoryStore.getState().pushState(tl2);
    useTimelineStore.getState().setTimeline(tl3);

    // 两次撤销灌入 redoStack（模拟真实用法：每次 undo 后把返回的时间线写回）
    expect(useHistoryStore.getState().undo()?.id).toBe('tl-2');
    useTimelineStore.setState({ timeline: tl2 });
    expect(useHistoryStore.getState().undo()?.id).toBe('tl-1');
    expect(useHistoryStore.getState().redoStack).toHaveLength(2);
    expect(useHistoryStore.getState().canRedo()).toBe(true);

    // 把“当前”时间线换成不可克隆值后再重做
    useTimelineStore.setState({ timeline: bad });
    let result: Timeline | null = null;
    let threw = false;
    try {
      result = useHistoryStore.getState().redo();
    } catch {
      threw = true;
    }
    expect(threw).toBe(false);

    // 仍出栈并返回栈顶条目…
    expect(result?.id).toBe('tl-2');
    expect(useHistoryStore.getState().redoStack).toHaveLength(1);
    expect(useHistoryStore.getState().canRedo()).toBe(true);
    // …但跳过失败的 undo 快照（undoStack 不增长）
    expect(useHistoryStore.getState().undoStack).toHaveLength(0);
    expect(useHistoryStore.getState().canUndo()).toBe(false);
  });
});
