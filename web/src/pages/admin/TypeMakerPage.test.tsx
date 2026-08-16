// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { TypeMakerPage } from './TypeMakerPage';

const { mocks } = vi.hoisted(() => ({
  mocks: { list: vi.fn(), get: vi.fn(), remove: vi.fn(), preview: vi.fn() },
}));

vi.mock('@/services/api', () => ({
  typeMakerApi: {
    list: mocks.list,
    get: mocks.get,
    remove: mocks.remove,
    preview: mocks.preview,
    create: vi.fn(),
    update: vi.fn(),
  },
  healthApi: { check: vi.fn(async () => ({ status: 'ok' })) },
}));

vi.mock('@tanstack/react-router', () => ({
  useLocation: () => ({ pathname: '/settings/type-maker' }),
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

afterEach(() => cleanup());

const customType = {
  id: 'custom_t1',
  name: '自定义类型',
  shot_duration: '5-15s',
  transition: 'cut',
  animation_density: 'medium',
  cut_interval_ms: 8000,
  color: '#4F8CFF',
  builtin: false,
};

const builtinType = {
  id: 'builtin_doc',
  name: '纪录片',
  shot_duration: '8-20s',
  transition: 'cut',
  animation_density: 'low',
  cut_interval_ms: 12000,
  color: '#34D399',
  builtin: true,
};

describe('TypeMakerPage P10 预览 + 删除确认', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue([customType, builtinType]);
    mocks.get.mockResolvedValue({ id: 'custom_t1', name: '自定义类型', shot_params: { min_shot_sec: 2, max_shot_sec: 8, transition_type: 'cut', transition_duration_sec: 0.5, cut_on_beat: false } });
    mocks.preview.mockResolvedValue({
      valid: true,
      errors: [],
      shot_params: { min_shot_sec: 2, max_shot_sec: 8, transition_type: 'cut', transition_duration_sec: 0.5, cut_on_beat: false },
      sample_translation: { beat: 1, shot: 2 },
    });
    mocks.remove.mockResolvedValue(undefined);
  });

  it('预览按钮调用 typeMakerApi.preview 并显示校验结果', async () => {
    render(<TypeMakerPage />);
    const previewBtn = await screen.findAllByTitle('预览');
    fireEvent.click(previewBtn[0]);

    await waitFor(() => expect(mocks.preview).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/定义有效/)).toBeTruthy();
  });

  it('删除按钮先弹确认，确认后调用 remove', async () => {
    render(<TypeMakerPage />);
    const delBtn = await screen.findAllByTitle('删除');
    fireEvent.click(delBtn[0]);

    // 确认弹层出现
    expect(await screen.findByText('删除视频类型？')).toBeTruthy();
    expect(mocks.remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /确认删除/ }));
    await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith('custom_t1'));
  });

  it('内置类型没有删除按钮', async () => {
    render(<TypeMakerPage />);
    const delBtns = await screen.findAllByTitle('删除');
    expect(delBtns.length).toBe(1); // 仅自定义类型
  });
});
