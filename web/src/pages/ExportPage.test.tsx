// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, cleanup } from '@testing-library/react';
import { ExportPage } from './ExportPage';

const { mocks, timelineState, projectState } = vi.hoisted(() => ({
  mocks: {
    getPresets: vi.fn(),
    listQueue: vi.fn(),
    submitQueue: vi.fn(),
    getQueueStreamUrl: vi.fn(),
    getDownloadUrl: vi.fn(),
    load: vi.fn(),
    toast: vi.fn(),
  },
  timelineState: {
    timeline: { duration_sec: 0, fps: 30, tracks: [] as unknown[], width: 1920, height: 1080 },
  },
  projectState: { projectId: '', projectName: '测试项目' },
}));

vi.mock('@/services/api', () => ({
  renderApi: {
    getPresets: mocks.getPresets,
    listQueue: mocks.listQueue,
    submitQueue: mocks.submitQueue,
    getQueueStreamUrl: mocks.getQueueStreamUrl,
    getDownloadUrl: mocks.getDownloadUrl,
  },
  projectApi: { load: mocks.load },
}));

vi.mock('@/stores/toastStore', () => ({ toast: mocks.toast }));

vi.mock('@/stores/timelineStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useTimelineStore: any = (selector: (s: unknown) => unknown) => selector(timelineState);
  useTimelineStore.getState = () => ({
    timeline: timelineState.timeline,
    setTimeline: (t: unknown) => { timelineState.timeline = t as typeof timelineState.timeline; },
    exportTimeline: () => timelineState.timeline,
  });
  return { useTimelineStore };
});

vi.mock('@/stores/projectStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useProjectStore: any = (selector: (s: unknown) => unknown) => selector(projectState);
  useProjectStore.getState = () => ({
    projectId: projectState.projectId,
    projectName: projectState.projectName,
    setProjectId: (id: string) => { projectState.projectId = id; },
    setProjectName: (n: string) => { projectState.projectName = n; },
  });
  return { useProjectStore };
});

vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ projectId: 'proj_1' }),
  useNavigate: () => vi.fn(),
}));

/** Minimal EventSource stub — captures instances so tests can emit SSE messages. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() { this.closed = true; }
  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}
vi.stubGlobal('EventSource', FakeEventSource);

const EMPTY_TIMELINE = { duration_sec: 0, fps: 30, tracks: [] as unknown[], width: 1920, height: 1080 };
const NONEMPTY_TIMELINE = { duration_sec: 12, fps: 30, tracks: [{ id: 't1', clips: [] }], width: 1920, height: 1080 };

beforeEach(() => {
  vi.clearAllMocks();
  FakeEventSource.instances = [];
  timelineState.timeline = { ...EMPTY_TIMELINE };
  projectState.projectId = '';
  projectState.projectName = '测试项目';
  mocks.getPresets.mockResolvedValue({});
  mocks.listQueue.mockResolvedValue([]);
  mocks.load.mockRejectedValue(new Error('offline'));
  mocks.getQueueStreamUrl.mockImplementation((id: string) => `http://localhost:8000/stream/${id}`);
  mocks.getDownloadUrl.mockReturnValue('http://localhost:8000/api/render/download/x.mp4');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('U3: simulated renders are clearly marked', () => {
  it('toasts offline warning and shows 演示模式 badge without download link', async () => {
    vi.useFakeTimers();
    timelineState.timeline = { ...NONEMPTY_TIMELINE };
    mocks.submitQueue.mockRejectedValue(new Error('network down'));

    render(<ExportPage />);
    await act(async () => {}); // flush mount effects

    fireEvent.click(screen.getByRole('button', { name: /加入渲染队列/ }));
    await act(async () => {}); // flush submitQueue rejection → simulateRender

    expect(mocks.toast).toHaveBeenCalledWith('后端离线，无法真实渲染，已进入演示模式', 'error');

    // run the simulated progress to completion (180ms/tick, +2..5% per tick)
    await act(async () => { vi.advanceTimersByTime(12000); });

    const badge = screen.getByText('演示模式');
    expect(badge.getAttribute('title')).toBe('演示模式 — 未真实渲染，仅本地模拟进度');
    expect(screen.queryByTitle('下载')).toBeNull();
  });
});

describe('U14: failure detail + retry', () => {
  it('shows SSE failure detail and retries with the same settings', async () => {
    timelineState.timeline = { ...NONEMPTY_TIMELINE };
    mocks.submitQueue.mockResolvedValue({ task_id: 'render_real_1' });

    render(<ExportPage />);
    await act(async () => {});

    fireEvent.click(screen.getByRole('button', { name: /加入渲染队列/ }));
    await waitFor(() => expect(mocks.submitQueue).toHaveBeenCalledTimes(1));

    const es = FakeEventSource.instances.find((i) => i.url.includes('render_real_1'));
    expect(es).toBeTruthy();
    act(() => { es!.emit({ type: 'failed', detail: '编码器崩溃' }); });

    expect(screen.getByText('编码器崩溃')).toBeTruthy();
    const retryBtn = screen.getByRole('button', { name: /重试/ });

    mocks.submitQueue.mockResolvedValue({ task_id: 'render_real_2' });
    fireEvent.click(retryBtn);

    await waitFor(() => expect(mocks.submitQueue).toHaveBeenCalledTimes(2));
    const secondCall = mocks.submitQueue.mock.calls[1][0];
    expect(secondCall.settings).toEqual(mocks.submitQueue.mock.calls[0][0].settings);
    expect(secondCall.output_path).toBe(mocks.submitQueue.mock.calls[0][0].output_path);
    await waitFor(() => {
      expect(FakeEventSource.instances.some((i) => i.url.includes('render_real_2'))).toBe(true);
    });
  });

  it('toasts and stays failed when retry submit fails', async () => {
    timelineState.timeline = { ...NONEMPTY_TIMELINE };
    mocks.submitQueue.mockResolvedValue({ task_id: 'render_real_1' });

    render(<ExportPage />);
    await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: /加入渲染队列/ }));
    await waitFor(() => expect(mocks.submitQueue).toHaveBeenCalledTimes(1));

    const es = FakeEventSource.instances.find((i) => i.url.includes('render_real_1'))!;
    act(() => { es.emit({ type: 'failed', detail: '磁盘已满' }); });

    mocks.submitQueue.mockRejectedValue(new Error('offline'));
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith('重试失败 — 后端不可达', 'error');
    });
    expect(screen.getByText('重试失败 — 后端不可达')).toBeTruthy();
  });
});

describe('U15: preset merge + empty-timeline size', () => {
  it('keeps internal icon when a colliding backend preset omits it, without duplicates', async () => {
    mocks.getPresets.mockResolvedValue({
      bilibili: { name: 'Bilibili 1080p', width: 2560, height: 1440, fps: 60, bitrate: '12M' },
    });

    render(<ExportPage />);
    await act(async () => {});

    expect(screen.getAllByText('Bilibili 1080p')).toHaveLength(1);
    expect(screen.getByText('📺')).toBeTruthy(); // internal icon retained
    expect(screen.queryByText('📦')).toBeNull();
  });

  it("shows '—' instead of a misleading size for an empty timeline", async () => {
    render(<ExportPage />);
    await act(async () => {});

    expect(screen.getByText('—')).toBeTruthy();
    expect(screen.queryByText('0 MB')).toBeNull();
  });
});

describe('U18: restored queue tasks carry original label', () => {
  it('derives label and preset from the backend filename', async () => {
    mocks.listQueue.mockResolvedValue([
      { task_id: 'render_9', status: 'rendering', progress: 10, filename: 'renders/我的视频_1920x1080.mp4' },
    ]);

    render(<ExportPage />);

    const label = await screen.findByText('我的视频');
    const card = label.closest('div[class*="bg-surface-container"]') as HTMLElement;
    expect(card.textContent).toContain('1080p');
    expect(screen.queryByText('恢复的任务')).toBeNull();
  });

  it('falls back to 恢复的任务 when no filename is available', async () => {
    mocks.listQueue.mockResolvedValue([
      { task_id: 'render_10', status: 'queued', progress: 0 },
    ]);

    render(<ExportPage />);

    expect(await screen.findByText('恢复的任务')).toBeTruthy();
  });
});
