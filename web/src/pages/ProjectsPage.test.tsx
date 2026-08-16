// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { ProjectsPage } from './ProjectsPage';
import type { ProjectSummary } from '@/types/api';

afterEach(() => cleanup());

const projects: ProjectSummary[] = [
  { id: 'proj_1', name: '项目A', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z', folder: '', tags: [], track_count: 2, duration_sec: 10 },
  { id: 'proj_2', name: '项目B', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z', folder: '', tags: [], track_count: 3, duration_sec: 20 },
];

const { mocks } = vi.hoisted(() => ({
  mocks: {
    list: vi.fn(),
    remove: vi.fn(),
    trashProject: vi.fn(),
    restoreProject: vi.fn(),
    purgeProject: vi.fn(),
    rename: vi.fn(),
    setFolder: vi.fn(),
    addTag: vi.fn(),
    removeTag: vi.fn(),
    duplicate: vi.fn(),
    getThumbnailUrl: vi.fn(),
    toast: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  projectApi: {
    list: mocks.list,
    remove: mocks.remove,
    trashProject: mocks.trashProject,
    restoreProject: mocks.restoreProject,
    purgeProject: mocks.purgeProject,
    rename: mocks.rename,
    setFolder: mocks.setFolder,
    addTag: mocks.addTag,
    removeTag: mocks.removeTag,
    duplicate: mocks.duplicate,
    getThumbnailUrl: mocks.getThumbnailUrl,
  },
}));

vi.mock('@/stores/toastStore', () => ({
  toast: mocks.toast,
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

/** Open the delete confirm on a card, then click the confirm (trash) button. */
async function confirmDelete(projectName: string) {
  fireEvent.click(screen.getByRole('button', { name: `删除项目 ${projectName}` }));
  const cancel = screen.getByRole('button', { name: '取消' });
  const deleteConfirm = cancel.previousElementSibling as HTMLElement;
  fireEvent.click(deleteConfirm);
}

describe('ProjectsPage handleDelete (A2 → 回收站)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue(projects);
    mocks.getThumbnailUrl.mockReturnValue('http://localhost:8000/api/project/proj_1/thumbnail');
  });

  it('trashes the project (soft delete) and removes it from the list', async () => {
    mocks.trashProject.mockResolvedValue({ ok: true });
    render(<ProjectsPage />);
    await screen.findByText('项目A');

    await confirmDelete('项目A');

    await waitFor(() => {
      expect(mocks.trashProject).toHaveBeenCalledWith('proj_1');
    });
    await waitFor(() => {
      expect(screen.queryByText('项目A')).toBeNull();
    });
    expect(screen.getByText('项目B')).toBeTruthy();
    // 不再调用永久删除
    expect(mocks.remove).not.toHaveBeenCalled();
  });

  it('keeps the project and shows an error toast when the trash API fails', async () => {
    mocks.trashProject.mockRejectedValue(new Error('后端不可达'));
    render(<ProjectsPage />);
    await screen.findByText('项目A');

    await confirmDelete('项目A');

    await waitFor(() => {
      expect(mocks.trashProject).toHaveBeenCalledWith('proj_1');
    });
    await waitFor(() => {
      expect(screen.getByText('项目A')).toBeTruthy();
    });
    expect(mocks.toast).toHaveBeenCalledWith('删除失败：后端不可达，项目已保留', 'error');
  });
});

describe('ProjectsPage 回收站视图 (A2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue(projects);
    mocks.getThumbnailUrl.mockReturnValue('http://localhost:8000/api/project/proj_1/thumbnail');
  });

  it('切换回收站 → 加载 trash 列表并显示恢复/永久删除按钮', async () => {
    mocks.list.mockResolvedValueOnce(projects).mockResolvedValueOnce([
      { id: 'proj_9', name: '已删项目', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z', folder: '', tags: [], track_count: 1, duration_sec: 3 },
    ]);
    render(<ProjectsPage />);
    await screen.findByText('项目A');

    fireEvent.click(screen.getByRole('button', { name: /回收站/ }));
    await screen.findByText('已删项目');
    expect(mocks.list).toHaveBeenLastCalledWith(undefined, undefined, true);

    // 恢复
    mocks.restoreProject.mockResolvedValue({ ok: true });
    fireEvent.click(screen.getByRole('button', { name: '恢复 已删项目' }));
    await waitFor(() => {
      expect(mocks.restoreProject).toHaveBeenCalledWith('proj_9');
    });
    await waitFor(() => {
      expect(screen.queryByText('已删项目')).toBeNull();
    });
  });

  it('永久删除需确认，确认后调用 purgeProject', async () => {
    mocks.list.mockResolvedValueOnce(projects).mockResolvedValueOnce([
      { id: 'proj_9', name: '已删项目', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z', folder: '', tags: [], track_count: 1, duration_sec: 3 },
    ]);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    mocks.purgeProject.mockResolvedValue({ ok: true });
    render(<ProjectsPage />);
    await screen.findByText('项目A');

    fireEvent.click(screen.getByRole('button', { name: /回收站/ }));
    await screen.findByText('已删项目');
    fireEvent.click(screen.getByRole('button', { name: '永久删除 已删项目' }));

    await waitFor(() => {
      expect(mocks.purgeProject).toHaveBeenCalledWith('proj_9');
    });
    confirmSpy.mockRestore();
  });
});

describe('ProjectsPage loadProjects (U10)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('clears loading and shows the error state when list() rejects', async () => {
    mocks.list.mockRejectedValue(new Error('network down'));
    render(<ProjectsPage />);

    // loading spinner is visible first
    expect(screen.getByText('加载中…')).toBeTruthy();

    await waitFor(() => {
      expect(screen.queryByText('加载中…')).toBeNull();
    });
    expect(screen.getByText('后端未连接，无法加载项目')).toBeTruthy();
  });
});

describe('ProjectsPage silent-failure toasts (U4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue(projects);
    mocks.getThumbnailUrl.mockReturnValue('http://localhost:8000/api/project/proj_1/thumbnail');
  });

  /** Open the kebab menu on the first card (项目A). */
  async function openMenu() {
    render(<ProjectsPage />);
    await screen.findByText('项目A');
    fireEvent.click(screen.getAllByLabelText('更多操作')[0]);
  }

  it('toasts when rename fails', async () => {
    mocks.rename.mockRejectedValue(new Error('offline'));
    await openMenu();

    fireEvent.click(screen.getByText('重命名'));
    const input = screen.getByDisplayValue('项目A');
    fireEvent.change(input, { target: { value: '新名字' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(mocks.rename).toHaveBeenCalledWith('proj_1', '新名字');
    });
    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith('重命名失败', 'error');
    });
  });

  it('toasts when setFolder fails', async () => {
    mocks.setFolder.mockRejectedValue(new Error('offline'));
    mocks.list.mockResolvedValue([
      { ...projects[0], folder: '工作' },
      { ...projects[1], folder: '' },
    ]);
    await openMenu();

    const folderBtn = screen.getAllByText('工作').find((el) => el.tagName === 'BUTTON') as HTMLElement;
    fireEvent.click(folderBtn);

    await waitFor(() => {
      expect(mocks.setFolder).toHaveBeenCalledWith('proj_1', '工作');
    });
    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith('移动文件夹失败', 'error');
    });
  });

  it('toasts when addTag fails', async () => {
    mocks.addTag.mockRejectedValue(new Error('offline'));
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('标签1');
    await openMenu();

    fireEvent.click(screen.getByText('添加标签'));

    await waitFor(() => {
      expect(mocks.addTag).toHaveBeenCalledWith('proj_1', '标签1');
    });
    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith('添加标签失败', 'error');
    });
    promptSpy.mockRestore();
  });

  it('toasts when removeTag fails', async () => {
    mocks.removeTag.mockRejectedValue(new Error('offline'));
    mocks.list.mockResolvedValue([
      { ...projects[0], tags: ['旧标签'] },
    ]);
    render(<ProjectsPage />);
    await screen.findByText('项目A');

    const tagChip = screen.getAllByText('旧标签').find((el) => el.tagName === 'SPAN') as HTMLElement;
    fireEvent.click(tagChip.querySelector('button') as HTMLElement);

    await waitFor(() => {
      expect(mocks.removeTag).toHaveBeenCalledWith('proj_1', '旧标签');
    });
    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith('移除标签失败', 'error');
    });
  });

  it('toasts when duplicate fails', async () => {
    mocks.duplicate.mockRejectedValue(new Error('offline'));
    await openMenu();

    fireEvent.click(screen.getByText('创建副本'));

    await waitFor(() => {
      expect(mocks.duplicate).toHaveBeenCalledWith('proj_1');
    });
    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith('复制项目失败', 'error');
    });
  });
});
