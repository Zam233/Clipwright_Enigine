// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { AgentPanel } from './AgentPanel';
import { useAgentStore } from '@/stores/agentStore';
import { pipelineApi } from '@/services/api';
import type { CreativeBrief } from '@/types/persona';

vi.mock('@/pages/useBackendHealth', () => ({
  useBackendHealth: () => 'online',
}));

vi.mock('@/services/api', () => ({
  healthApi: { check: vi.fn().mockResolvedValue({ status: 'ok' }) },
  pipelineApi: {
    getTraceStreamUrl: (pid: string) => `http://localhost:8000/api/pipeline/${pid}/events`,
    getResult: vi.fn().mockRejectedValue(new Error('not found')),
    cancel: vi.fn().mockResolvedValue({}),
  },
  requirementsApi: {
    init: vi.fn(),
    chat: vi.fn(),
    edit: vi.fn(),
    proceed: vi.fn(),
    upload: vi.fn().mockResolvedValue({ file_name: 'ref.txt', content_preview: 'x' }),
  },
}));

class MockEventSource {
  static instances: MockEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  url: string;
  closed = false;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
}

const brief: CreativeBrief = {
  title: '独特简报标题XYZ',
  overview: '概述',
  target_audience: '受众',
  core_message: '核心信息',
  style_direction: '风格',
  structure_suggestion: '结构',
  duration_estimate: '时长',
  key_elements: [],
  special_requirements: [],
};

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal('EventSource', MockEventSource);
  // jsdom 未实现 Element.scrollTo
  Element.prototype.scrollTo = vi.fn();
  localStorage.clear();
  sessionStorage.clear();
  useAgentStore.setState({
    pipelineId: null,
    phase: 'idle',
    progress: 0,
    error: null,
    cancelling: false,
    logEntries: [],
    pipelineSummary: null,
    mgTotal: 0,
    mgDone: 0,
    agentTimeline: null,
    requirementsMessages: [],
    requirementsStatus: 'idle',
    requirementsSessionId: null,
    requirementsBusy: false,
    creativeBrief: null,
    productionPlan: null,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('U5: SSE reconnect cap', () => {
  it('stops reconnecting after 5 consecutive failures and shows disconnect banner', async () => {
    vi.useFakeTimers();
    try {
      useAgentStore.setState({ pipelineId: 'p1', phase: 'structure' });
      render(<AgentPanel />);

      // 初始挂接
      expect(MockEventSource.instances.length).toBe(1);

      // 连续 5 次断线：前 4 次会 3s 后重连，第 5 次判定断线
      for (let i = 0; i < 5; i++) {
        const es = MockEventSource.instances[MockEventSource.instances.length - 1];
        act(() => {
          es.onerror?.();
        });
        if (i < 4) {
          act(() => {
            vi.advanceTimersByTime(3000);
          });
        }
      }

      // 第 5 次失败后不再重连
      expect(MockEventSource.instances.length).toBe(5);
      act(() => {
        vi.advanceTimersByTime(30000);
      });
      expect(MockEventSource.instances.length).toBe(5);
      // B2: 最后一个实例已显式 close（不依赖原生自动重连）
      expect(MockEventSource.instances[4].closed).toBe(true);

      // 红色断线横幅
      expect(screen.getByText('连接已断开')).toBeTruthy();

      // 写入 error 日志
      const logs = useAgentStore.getState().logEntries;
      expect(
        logs.some((e) => e.type === 'error' && e.summary.includes('SSE 连接已断开')),
      ).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('resets retry counter on a successful onmessage', async () => {
    vi.useFakeTimers();
    try {
      useAgentStore.setState({ pipelineId: 'p1', phase: 'structure' });
      render(<AgentPanel />);

      // 4 次失败（逼近上限）
      for (let i = 0; i < 4; i++) {
        const es = MockEventSource.instances[MockEventSource.instances.length - 1];
        act(() => {
          es.onerror?.();
        });
        act(() => {
          vi.advanceTimersByTime(3000);
        });
      }
      expect(MockEventSource.instances.length).toBe(5);

      // 收到一条成功事件 → 计数清零
      const es = MockEventSource.instances[MockEventSource.instances.length - 1];
      act(() => {
        es.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ type: 'info', summary: 'ok' }) }));
      });

      // 再次失败 4 次：仍应继续重连（未达到 5 次连续失败）
      for (let i = 0; i < 4; i++) {
        const cur = MockEventSource.instances[MockEventSource.instances.length - 1];
        act(() => {
          cur.onerror?.();
        });
        act(() => {
          vi.advanceTimersByTime(3000);
        });
      }
      expect(MockEventSource.instances.length).toBe(9);
      expect(screen.queryByText('连接已断开')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('U11: pipeline auto-reconnect after refresh', () => {
  it('restores pipelineId from sessionStorage and opens SSE on mount', () => {
    sessionStorage.setItem('cw_pipeline_id', 'pid-xyz');
    // 刷新后 store 重置：无 pipelineId、phase 回落 idle
    useAgentStore.setState({ pipelineId: null, phase: 'idle' });

    render(<AgentPanel />);

    expect(useAgentStore.getState().pipelineId).toBe('pid-xyz');
    expect(MockEventSource.instances.length).toBe(1);
    expect(MockEventSource.instances[0].url).toContain('pid-xyz');
  });

  it('does not open SSE when no persisted pipelineId', () => {
    render(<AgentPanel />);
    expect(MockEventSource.instances.length).toBe(0);
  });
});

describe('B17: reset requirements status on pipeline finish', () => {
  it('SSE done 事件后 store.status 复位为 pipeline_done 且输入框恢复', async () => {
    useAgentStore.setState({
      pipelineId: 'p1',
      phase: 'structure',
      requirementsStatus: 'pipeline_running',
      requirementsSessionId: 'req_1',
      requirementsMessages: [
        { id: 'm1', role: 'user', content: '选题', timestamp: new Date().toISOString() },
        { id: 'm2', role: 'assistant', content: '方案已确认', timestamp: new Date().toISOString() },
      ],
    });
    render(<AgentPanel />);
    expect(MockEventSource.instances.length).toBe(1);

    // 模拟管线完成事件
    const es = MockEventSource.instances[0];
    act(() => {
      es.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ type: 'done' }) }));
    });

    await act(async () => { await Promise.resolve(); });

    expect(useAgentStore.getState().requirementsStatus).toBe('pipeline_done');
    // 输入区恢复：发送按钮/输入框存在
    expect(screen.getByPlaceholderText(/继续与需求 Agent 对话/)).toBeTruthy();
  });

  it('SSE error 事件后 store.status 复位为 error 且输入框恢复', async () => {
    useAgentStore.setState({
      pipelineId: 'p2',
      phase: 'structure',
      requirementsStatus: 'pipeline_running',
      requirementsMessages: [
        { id: 'm1', role: 'user', content: '选题', timestamp: new Date().toISOString() },
      ],
    });
    render(<AgentPanel />);
    const es = MockEventSource.instances[0];
    act(() => {
      es.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ type: 'error', error: 'boom' }) }));
    });

    await act(async () => { await Promise.resolve(); });

    expect(useAgentStore.getState().requirementsStatus).toBe('error');
    expect(useAgentStore.getState().phase).toBe('failed');
    expect(screen.getByPlaceholderText(/继续与需求 Agent 对话/)).toBeTruthy();
  });
});

describe('G7: reference file upload', () => {
  it('上传成功后添加"已上传"系统消息', async () => {
    useAgentStore.setState({
      requirementsSessionId: 'req_1',
      requirementsMessages: [
        { id: 'm1', role: 'user', content: '选题', timestamp: new Date().toISOString() },
        { id: 'm2', role: 'assistant', content: '回复', timestamp: new Date().toISOString() },
      ],
    });
    render(<AgentPanel />);

    const file = new File(['内容'], 'ref.txt', { type: 'text/plain' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      const msgs = useAgentStore.getState().requirementsMessages;
      expect(msgs.some((m) => m.content.includes('已上传 ref.txt'))).toBe(true);
    });
  });

  it('无会话时提示先开始会话', async () => {
    useAgentStore.setState({
      requirementsSessionId: null,
      requirementsMessages: [
        { id: 'm1', role: 'user', content: '选题', timestamp: new Date().toISOString() },
        { id: 'm2', role: 'assistant', content: '回复', timestamp: new Date().toISOString() },
      ],
    });
    render(<AgentPanel />);

    const file = new File(['内容'], 'ref.txt', { type: 'text/plain' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      const msgs = useAgentStore.getState().requirementsMessages;
      expect(msgs.some((m) => m.content.includes('请先开始需求会话'))).toBe(true);
    });
  });
});

describe('G4: current agent activity in bottom bar', () => {
  it('运行时显示最新非 system 日志条目活动', async () => {
    useAgentStore.setState({
      pipelineId: 'p1',
      phase: 'animation',
      logEntries: [
        { id: 'l1', timestamp: Date.now(), agent: 'system', type: 'info', summary: '执行组' },
        { id: 'l2', timestamp: Date.now(), agent: 'animation', type: 'llm', summary: 'LLM MG 生成…' },
      ],
    });
    render(<AgentPanel />);
    expect(screen.getByText(/animation · LLM MG 生成/)).toBeTruthy();
  });

  it('无日志时回退相位名', async () => {
    useAgentStore.setState({
      pipelineId: 'p1',
      phase: 'structure',
      logEntries: [{ id: 'l1', timestamp: Date.now(), agent: 'system', type: 'info', summary: 'start' }],
    });
    render(<AgentPanel />);
    // 仅 system 日志 → 回退相位标签
    expect(screen.getByText(/当前：/)).toBeTruthy();
  });
});

describe('U13: brief/plan card dedupe', () => {
  it('skips markdown body when message carries a creative_brief attachment', () => {
    useAgentStore.setState({
      requirementsStatus: 'brief_ready',
      requirementsMessages: [
        {
          id: 'm1',
          role: 'assistant',
          content: '✅ 创作方案已完成\n\n标题：独特简报标题XYZ（正文内嵌重复内容）',
          timestamp: new Date().toISOString(),
          creative_brief: brief,
        },
      ],
    });

    render(<AgentPanel />);

    // 正文 markdown 不再渲染（内嵌的重复段落消失）
    expect(screen.queryByText(/创作方案已完成/)).toBeNull();
    expect(screen.queryByText(/正文内嵌重复内容/)).toBeNull();
    // 卡片仍然渲染
    expect(screen.getAllByText('独特简报标题XYZ').length).toBeGreaterThan(0);
    expect(screen.getByText('创意简报')).toBeTruthy();
  });

  it('still renders markdown body for assistant messages without attachments', () => {
    useAgentStore.setState({
      requirementsMessages: [
        {
          id: 'm1',
          role: 'assistant',
          content: '这是普通回复内容ABC',
          timestamp: new Date().toISOString(),
        },
      ],
    });

    render(<AgentPanel />);
    expect(screen.getByText(/这是普通回复内容ABC/)).toBeTruthy();
  });
});

describe('G2: pipeline cancel', () => {
  it('renders stop button when running, disabled while cancelling', () => {
    useAgentStore.setState({ pipelineId: 'p1', phase: 'structure', cancelling: false });
    render(<AgentPanel />);

    expect(screen.getByText('停止')).toBeTruthy();

    // cancelling: true → 按钮禁用且文案切换
    act(() => {
      useAgentStore.setState({ cancelling: true });
    });
    const btn = screen.getByText('取消中…') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('clicking stop calls pipelineApi.cancel with the pipelineId', async () => {
    useAgentStore.setState({ pipelineId: 'p1', phase: 'structure', cancelling: false });
    const cancel = vi.mocked(pipelineApi.cancel).mockResolvedValue({});
    render(<AgentPanel />);

    fireEvent.click(screen.getByText('停止'));

    expect(cancel).toHaveBeenCalledWith('p1');
  });

  it('cancel failure logs error and keeps SSE open', async () => {
    useAgentStore.setState({ pipelineId: 'p1', phase: 'structure', cancelling: false });
    vi.mocked(pipelineApi.cancel).mockRejectedValue(new Error('boom'));
    render(<AgentPanel />);
    expect(MockEventSource.instances.length).toBe(1);

    fireEvent.click(screen.getByText('停止'));
    await act(async () => { await Promise.resolve(); });

    const logs = useAgentStore.getState().logEntries;
    expect(logs.some((e) => e.type === 'error' && e.summary.includes('取消失败'))).toBe(true);
    // 取消请求失败 → 不关闭 SSE，继续追踪管线
    expect(MockEventSource.instances[0].closed).toBe(false);
  });

  it('SSE cancelled event → phase failed, requirements error, SSE closed, sessionStorage cleared', async () => {
    useAgentStore.setState({ pipelineId: 'p1', phase: 'structure', cancelling: false });
    sessionStorage.setItem('cw_pipeline_id', 'p1');
    render(<AgentPanel />);
    expect(MockEventSource.instances.length).toBe(1);

    const es = MockEventSource.instances[0];
    act(() => {
      es.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ type: 'cancelled', summary: '管线已取消' }) }));
    });
    await act(async () => { await Promise.resolve(); });

    expect(useAgentStore.getState().phase).toBe('failed');
    expect(useAgentStore.getState().requirementsStatus).toBe('error');
    expect(es.closed).toBe(true);
    expect(sessionStorage.getItem('cw_pipeline_id')).toBeNull();
    const logs = useAgentStore.getState().logEntries;
    expect(logs.some((e) => e.type === 'error' && e.summary === '管线已取消')).toBe(true);
  });
});
