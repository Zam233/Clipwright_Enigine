// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { TemplateGallery } from './TemplateGallery';

afterEach(() => cleanup());

const { mocks } = vi.hoisted(() => ({
  mocks: {
    list: vi.fn(),
    apply: vi.fn(),
    create: vi.fn(),
    toast: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  templateApi: { list: mocks.list, apply: mocks.apply },
  projectApi: { create: mocks.create },
}));

vi.mock('@/stores/toastStore', () => ({ toast: mocks.toast }));

const TEMPLATES = [
  { template_id: 'tpl_1', name: '口播模板', description: '三段式', category: '口播', tags: ['知识'], track_count: 3, duration_sec: 120 },
  { template_id: 'tpl_2', name: '开箱模板', category: '开箱', tags: [], track_count: 2, duration_sec: 90 },
];

describe('TemplateGallery (A3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue(TEMPLATES);
    mocks.create.mockResolvedValue({ id: 'proj_new' });
  });

  it('open=false 不渲染', () => {
    render(<TemplateGallery open={false} onClose={vi.fn()} />);
    expect(screen.queryByText('从模板开始')).toBeNull();
  });

  it('列出模板并应用 → 创建项目 + 回调', async () => {
    const onApply = vi.fn();
    mocks.apply.mockResolvedValue({ status: 'ok', template_id: 'tpl_1', timeline: { duration_sec: 120 } });
    render(<TemplateGallery open onClose={vi.fn()} onApplyProject={onApply} />);

    await screen.findByText('口播模板');
    expect(screen.getByText('3 轨')).toBeTruthy();

    fireEvent.click(screen.getAllByText('应用为新项目')[0]);
    await waitFor(() => {
      expect(mocks.apply).toHaveBeenCalledWith('tpl_1');
    });
    await waitFor(() => {
      expect(mocks.create).toHaveBeenCalled();
    });
    expect(onApply).toHaveBeenCalledWith('proj_new');
  });

  it('加载失败 → toast', async () => {
    mocks.list.mockRejectedValue(new Error('offline'));
    render(<TemplateGallery open onClose={vi.fn()} />);
    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith('模板加载失败（后端离线？）', 'error');
    });
  });
});
