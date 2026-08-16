// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { PipelineAdminPage } from './PipelineAdminPage';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    getRunRecords: vi.fn(),
    getTraceJson: vi.fn(),
    retry: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  healthApi: { check: vi.fn().mockResolvedValue({ status: 'ok' }) },
  pipelineApi: {
    getRunRecords: mocks.getRunRecords,
    getTraceJson: mocks.getTraceJson,
    retry: mocks.retry,
  },
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ pathname: '/pipeline-admin' }),
}));

const RUNS = [
  {
    id: 'pl_a', topic: '话题A', status: 'completed',
    duration_ms: 10000, started_at: '2026-08-10T00:00:00Z',
    agents: [{ agent: 'structure', start: 0, dur: 1000, status: 'ok' }],
    llm_cost: 1.23,
  },
  {
    id: 'pl_b', topic: '话题B', status: 'failed',
    duration_ms: 20000, started_at: '2026-08-10T00:00:01Z',
    agents: [
      { agent: 'structure', start: 0, dur: 1000, status: 'ok' },
      { agent: 'material', start: 1000, dur: 2000, status: 'fail' },
    ],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getRunRecords.mockResolvedValue(RUNS);
  mocks.getTraceJson.mockResolvedValue([]);
  mocks.retry.mockResolvedValue({ status: 'retrying' });
});

afterEach(() => cleanup());

describe('G9: PipelineAdminPage 真实成本 + 重试', () => {
  it('有 llm_cost 记录 → 显示真实成本（求和）', async () => {
    render(<PipelineAdminPage />);
    await screen.findByText(/¥1.23/);
  });

  it('无 llm_cost → 成本显示 —', async () => {
    mocks.getRunRecords.mockResolvedValue([RUNS[1]]);
    render(<PipelineAdminPage />);
    await screen.findByText('—');
  });

  it('失败 run 显示重试按钮 → 点击调用 retry(id, agent) 并刷新', async () => {
    render(<PipelineAdminPage />);
    const btn = await screen.findByText('重试');
    expect(btn).toBeTruthy();

    fireEvent.click(btn);
    expect(mocks.retry).toHaveBeenCalledWith('pl_b', 'material');

    // 3s 后重新拉取记录（真实定时器 + 长等待）
    await waitFor(() => expect(mocks.getRunRecords).toHaveBeenCalledTimes(2), { timeout: 6000 });
  });

  it('重试失败 → 显示错误提示', async () => {
    mocks.retry.mockRejectedValue(new Error('backend down'));
    render(<PipelineAdminPage />);
    const btn = await screen.findByText('重试');
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByText(/重试失败/)).toBeTruthy());
  });
});
