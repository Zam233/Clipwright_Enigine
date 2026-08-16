// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { HomePage } from './HomePage';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    personaList: vi.fn(),
    projectList: vi.fn(),
    projectRemove: vi.fn(),
    projectDuplicate: vi.fn(),
    getThumbnailUrl: vi.fn(),
    typeMakerList: vi.fn(),
    listSources: vi.fn(),
    healthCheck: vi.fn(),
    predictScript: vi.fn(),
    healthStatus: vi.fn(),
    toast: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  healthApi: { check: mocks.healthCheck },
  personaApi: { list: mocks.personaList },
  projectApi: {
    list: mocks.projectList,
    remove: mocks.projectRemove,
    duplicate: mocks.projectDuplicate,
    getThumbnailUrl: mocks.getThumbnailUrl,
    create: vi.fn(),
  },
  assetApi: { listSources: mocks.listSources },
  typeMakerApi: { list: mocks.typeMakerList },
  pipelineApi: { predictScript: mocks.predictScript },
  getApiClient: vi.fn(),
}));

vi.mock('./useBackendHealth', () => ({
  useBackendHealth: () => mocks.healthStatus(),
}));

vi.mock('@/stores/toastStore', () => ({ toast: mocks.toast }));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

// \u9879\u76eeA = 'Project A', \u9879\u76eeB = 'Project B'
const BACKEND_PROJECTS = [
  { id: 'proj_a', name: '\u9879\u76eeA', plugin_id: 'knowledge_longform', duration_sec: 10, track_count: 2, updated_at: '2024-01-01T00:00:00Z' },
  { id: 'proj_b', name: '\u9879\u76eeB', plugin_id: 'kichiku_fastcut', duration_sec: 20, track_count: 3, updated_at: '2024-01-01T00:00:00Z' },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.personaList.mockResolvedValue([]);
  mocks.projectList.mockResolvedValue(BACKEND_PROJECTS);
  mocks.getThumbnailUrl.mockReturnValue('http://localhost:8000/api/project/proj_a/thumbnail');
  mocks.typeMakerList.mockResolvedValue([]);
  mocks.listSources.mockResolvedValue([]);
  mocks.healthStatus.mockReturnValue('offline');
  mocks.predictScript.mockResolvedValue({});
});

afterEach(() => cleanup());

/** Open the delete confirm on a card, then click the confirm (trash) button. */
async function confirmDelete(projectName: string) {
  fireEvent.click(screen.getByRole('button', { name: `\u5220\u9664\u9879\u76ee ${projectName}` }));
  const cancel = screen.getByRole('button', { name: '\u53d6\u6d88' });
  const deleteConfirm = cancel.previousElementSibling as HTMLElement;
  fireEvent.click(deleteConfirm);
}

describe('HomePage handleDeleteProject (U1)', () => {
  it('removes the project from the list when delete succeeds', async () => {
    mocks.projectRemove.mockResolvedValue({ ok: true });
    render(<HomePage />);
    await screen.findByText('\u9879\u76eeA');

    await confirmDelete('\u9879\u76eeA');

    await waitFor(() => expect(mocks.projectRemove).toHaveBeenCalledWith('proj_a'));
    await waitFor(() => expect(screen.queryByText('\u9879\u76eeA')).toBeNull());
    expect(screen.getByText('\u9879\u76eeB')).toBeTruthy();
    expect(mocks.toast).not.toHaveBeenCalled();
  });

  it('keeps the project in the list and shows an error toast when delete fails', async () => {
    mocks.projectRemove.mockRejectedValue(new Error('\u540e\u7aef\u4e0d\u53ef\u8fbe'));
    render(<HomePage />);
    await screen.findByText('\u9879\u76eeA');

    await confirmDelete('\u9879\u76eeA');

    await waitFor(() => expect(mocks.projectRemove).toHaveBeenCalledWith('proj_a'));
    await waitFor(() => expect(screen.getByText('\u9879\u76eeA')).toBeTruthy());
    expect(mocks.toast).toHaveBeenCalledWith('\u5220\u9664\u5931\u8d25\uff1a\u540e\u7aef\u4e0d\u53ef\u8fbe\uff0c\u9879\u76ee\u5df2\u4fdd\u7559', 'error');
  });
});

describe('HomePage project card keys (U8)', () => {
  it('renders two same-name projects without a duplicate-key warning', async () => {
    mocks.projectList.mockResolvedValue([
      { id: 'proj_1', name: '\u540c\u540d\u9879\u76ee', plugin_id: 'knowledge_longform', duration_sec: 10, track_count: 1, updated_at: '2024-01-01T00:00:00Z' },
      { id: 'proj_2', name: '\u540c\u540d\u9879\u76ee', plugin_id: 'kichiku_fastcut', duration_sec: 20, track_count: 2, updated_at: '2024-01-02T00:00:00Z' },
    ]);
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      render(<HomePage />);
      expect(await screen.findAllByText('\u540c\u540d\u9879\u76ee')).toHaveLength(2);
      const dupWarnings = errorSpy.mock.calls.filter((args) =>
        args.some((a) => typeof a === 'string' && /same key|two children with the same key/i.test(a)),
      );
      expect(dupWarnings).toHaveLength(0);
    } finally {
      errorSpy.mockRestore();
    }
  });
});

describe('HomePage empty-state copy (U12)', () => {
  it('demo mode shows the demo-data empty state when the backend list fails', async () => {
    mocks.personaList.mockRejectedValue(new Error('offline'));
    mocks.projectList.mockRejectedValue(new Error('offline'));
    render(<HomePage />);
    expect(await screen.findByText('\u6f14\u793a\u6570\u636e \u00b7 \u6682\u65e0\u540e\u7aef\u9879\u76ee')).toBeTruthy();
    expect(screen.queryByText('\u8fd8\u6ca1\u6709\u9879\u76ee')).toBeNull();
  });

  it('live mode shows the real empty state when the backend returns no projects', async () => {
    mocks.projectList.mockResolvedValue([]);
    render(<HomePage />);
    expect(await screen.findByText('\u8fd8\u6ca1\u6709\u9879\u76ee')).toBeTruthy();
    expect(screen.queryByText('\u6f14\u793a\u6570\u636e \u00b7 \u6682\u65e0\u540e\u7aef\u9879\u76ee')).toBeNull();
  });
});
describe('G6: script intelligence prediction', () => {
  it('长文稿 + 后端在线 → 渲染推荐卡片', async () => {
    mocks.healthStatus.mockReturnValue('online');
    mocks.predictScript.mockResolvedValue({
      video_type: 'knowledge_longform',
      estimated_duration_sec: 180,
      recommended_persona_tone: '专业',
      summary: '适合做知识区长片',
    });
    render(<HomePage />);

    const textarea = screen.getByPlaceholderText(/粘贴口播文案/);
    fireEvent.change(textarea, { target: { value: 'x'.repeat(60) } });
    await waitFor(() => expect(mocks.predictScript).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText('智能预判')).toBeTruthy());
    expect(screen.getByText(/适合做知识区长片/)).toBeTruthy();
  });

  it('离线/失败 → 无推荐卡片且不影响启动', async () => {
    mocks.healthStatus.mockReturnValue('offline');
    mocks.predictScript.mockRejectedValue(new Error('offline'));
    render(<HomePage />);

    const textarea = screen.getByPlaceholderText(/粘贴口播文案/);
    fireEvent.change(textarea, { target: { value: 'y'.repeat(60) } });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('智能预判')).toBeNull();
    // 启动按钮仍在
    expect(screen.getByText(/开始创作/)).toBeTruthy();
  });
});
