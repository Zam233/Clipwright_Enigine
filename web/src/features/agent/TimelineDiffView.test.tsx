// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { TimelineDiffView } from './TimelineDiffView';
import { useTimelineStore } from '@/stores/timelineStore';
import { useAgentStore } from '@/stores/agentStore';
import { useHistoryStore } from '@/stores/historyStore';
import { createDefaultClip, type Timeline } from '@/types/timeline';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    edit: vi.fn(),
    registerTimeline: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  requirementsApi: { edit: mocks.edit },
}));
vi.mock('@/services/media/mediaManager', () => ({
  mediaManager: { registerTimeline: mocks.registerTimeline },
}));
vi.mock('@/stores/toastStore', () => ({ toast: vi.fn() }));

function makeTimeline(clips: Array<{ id: string; text: string; start_sec?: number }>): Timeline {
  return {
    id: 'tl1', width: 1920, height: 1080, fps: 30, duration_sec: 10,
    tracks: [{ id: 'tr1', name: 'V1', kind: 'video' as const, index: 0, locked: false, muted: false,
      clips: clips.map((c) => createDefaultClip({
        id: c.id, kind: 'video' as const, track_id: 'tr1',
        start_sec: c.start_sec ?? 0, duration_sec: 2, text: c.text,
      })) }],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.registerTimeline.mockReturnValue(undefined);
  useAgentStore.setState({
    requirementsSessionId: 'req_1',
    requirementsMessages: [],
    requirementsStatus: 'plan_ready',
    creativeBrief: null,
    productionPlan: null,
    agentTimeline: null,
  });
  useHistoryStore.setState({ undoStack: [], redoStack: [] });
});

afterEach(() => cleanup());

describe('G10: diff review rework entry', () => {
  it('有会话 → 选片段提反馈 → edit 触发新审阅', async () => {
    const current = makeTimeline([]);
    useTimelineStore.setState({ timeline: current });
    const agentTimeline = makeTimeline([{ id: 'c1', text: '测试片段' }]);
    mocks.edit.mockResolvedValue({ reply: 'ok', proposed_timeline: makeTimeline([{ id: 'c1', text: '新版片段' }]) });

    render(<TimelineDiffView agentTimeline={agentTimeline} onDone={vi.fn()} />);

    fireEvent.click(screen.getByText('不满意，让 Agent 重做'));
    expect(screen.getByText('选择不满意的片段并提出修改意见')).toBeTruthy();

    // 选中 c1 片段 + 输入反馈
    const cb = screen.getByRole('checkbox');
    fireEvent.click(cb);
    fireEvent.change(screen.getByPlaceholderText(/节奏放慢/), { target: { value: '放慢节奏' } });
    fireEvent.click(screen.getByText('提交重做'));

    await waitFor(() => expect(mocks.edit).toHaveBeenCalled());
    const arg = mocks.edit.mock.calls[0][0];
    expect(arg.session_id).toBe('req_1');
    expect(arg.message).toBe('放慢节奏');
    expect(arg.selected_clip_ids).toContain('c1');
    expect(arg.timeline).toBe(agentTimeline);

    // 新 proposed_timeline 触发新一轮审阅
    expect(useAgentStore.getState().agentTimeline?.tracks?.[0]?.clips?.[0]?.text).toBe('新版片段');
  });

  it('无会话 → 提示需要需求会话', async () => {
    useAgentStore.setState({ requirementsSessionId: null });
    const current = makeTimeline([]);
    useTimelineStore.setState({ timeline: current });
    const agentTimeline = makeTimeline([{ id: 'c1', text: '测试片段' }]);

    render(<TimelineDiffView agentTimeline={agentTimeline} onDone={vi.fn()} />);
    fireEvent.click(screen.getByText('不满意，让 Agent 重做'));
    fireEvent.click(screen.getByText('提交重做'));

    await waitFor(() => expect(screen.getByText(/需要需求会话/)).toBeTruthy());
    expect(mocks.edit).not.toHaveBeenCalled();
  });

  it('edit 失败 → 显示错误且不关闭', async () => {
    const current = makeTimeline([]);
    useTimelineStore.setState({ timeline: current });
    const agentTimeline = makeTimeline([{ id: 'c1', text: '测试片段' }]);
    mocks.edit.mockRejectedValue(new Error('boom'));

    render(<TimelineDiffView agentTimeline={agentTimeline} onDone={vi.fn()} />);
    fireEvent.click(screen.getByText('不满意，让 Agent 重做'));
    fireEvent.change(screen.getByPlaceholderText(/节奏放慢/), { target: { value: '重做这段' } });
    fireEvent.click(screen.getByText('提交重做'));

    await waitFor(() => expect(screen.getByText(/重做请求失败/)).toBeTruthy());
  });
});
