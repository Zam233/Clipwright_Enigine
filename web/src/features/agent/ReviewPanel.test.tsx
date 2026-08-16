// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { ReviewPanel } from './ReviewPanel';
import { useAgentStore } from '@/stores/agentStore';
import type { CreativeBrief } from '@/types/persona';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    chat: vi.fn(),
    runAsync: vi.fn(),
    getResult: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  requirementsApi: { chat: mocks.chat },
  pipelineApi: { runAsync: mocks.runAsync, getResult: mocks.getResult },
}));
vi.mock('@/components/shared/Markdown', () => ({ Markdown: () => null }));
vi.mock('@/stores/toastStore', () => ({ toast: vi.fn() }));

const brief: CreativeBrief = {
  title: '标题',
  overview: '概述',
  target_audience: '受众',
  core_message: '核心',
  style_direction: '风格',
  structure_suggestion: '结构',
  duration_estimate: '时长',
  key_elements: [],
  special_requirements: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  useAgentStore.setState({
    requirementsSessionId: 'req_1',
    annotations: [{ id: 'a1', type: 'dislike', text: '这段不好' }],
    requirementsMessages: [],
    requirementsStatus: 'plan_ready',
    creativeBrief: null,
    productionPlan: null,
  });
});

afterEach(() => cleanup());

describe('B14: 反馈发送错误分类', () => {
  it('网络错误（ERR_NETWORK）→ 离线演示文案 + onBack', async () => {
    mocks.chat.mockRejectedValue(Object.assign(new Error('network'), { code: 'ERR_NETWORK' }));
    const onBack = vi.fn();
    render(<ReviewPanel brief={brief} onBack={onBack} />);

    fireEvent.click(screen.getByText('发送反馈'));
    await waitFor(() => expect(mocks.chat).toHaveBeenCalled());

    const msgs = useAgentStore.getState().requirementsMessages;
    expect(msgs.some((m) => m.content.includes('离线模式'))).toBe(true);
    expect(onBack).toHaveBeenCalled();
  });

  it('真实 500 响应 → 发送失败提示 + 不 onBack', async () => {
    mocks.chat.mockRejectedValue(
      Object.assign(new Error('500'), { response: { status: 500 }, request: {} }),
    );
    const onBack = vi.fn();
    render(<ReviewPanel brief={brief} onBack={onBack} />);

    fireEvent.click(screen.getByText('发送反馈'));
    await waitFor(() => expect(mocks.chat).toHaveBeenCalled());

    const msgs = useAgentStore.getState().requirementsMessages;
    expect(msgs.some((m) => m.content.includes('发送失败'))).toBe(true);
    expect(onBack).not.toHaveBeenCalled();
  });
});
