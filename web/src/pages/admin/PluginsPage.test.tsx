// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { PluginsPage } from './PluginsPage';

const { mocks } = vi.hoisted(() => ({
  mocks: { list: vi.fn(), discover: vi.fn(), disable: vi.fn(), enable: vi.fn(), getUI: vi.fn(), errors: vi.fn(), clearErrors: vi.fn() },
}));

vi.mock('@/services/api', () => ({
  pluginApi: {
    list: mocks.list,
    discover: mocks.discover,
    disable: mocks.disable,
    enable: mocks.enable,
    getUI: mocks.getUI,
    errors: mocks.errors,
    clearErrors: mocks.clearErrors,
  },
  healthApi: {
    check: vi.fn(async () => ({ status: 'ok' })),
  },
}));

vi.mock('@tanstack/react-router', () => ({
  useLocation: () => ({ pathname: '/settings/plugins' }),
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

afterEach(() => cleanup());

const loadedPlugin = {
  manifest: { id: 'plug_a', name: '插件A', kind: 'capability', version: '1.0.0' },
  enabled: true,
};

describe('PluginsPage M8 启停持久化', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue([loadedPlugin]);
    mocks.discover.mockResolvedValue([]);
    mocks.disable.mockResolvedValue({ status: 'ok' });
    mocks.enable.mockResolvedValue({ status: 'ok' });
    mocks.getUI.mockResolvedValue({ widgets: [{ type: 'text', content: '插件界面', size: 'body' }] });
    mocks.errors.mockResolvedValue([{ plugin_id: 'plug_a', phase: 'load', message: '导入失败', details: '', ts: 1700000000 }]);
    mocks.clearErrors.mockResolvedValue({ status: 'ok', removed: 1 });
  });

  it('已加载插件点击开关 → 调用 pluginApi.disable（持久化禁用）', async () => {
    render(<PluginsPage />);
    const toggle = await screen.findByTitle('禁用（持久化）');
    fireEvent.click(toggle);
    await waitFor(() => expect(mocks.disable).toHaveBeenCalledWith('plug_a'));
  });

  it('未加载插件点击开关 → 调用 pluginApi.enable（持久化启用）', async () => {
    mocks.list.mockResolvedValue([]);
    mocks.discover.mockResolvedValue(['plug_b']);
    render(<PluginsPage />);
    const toggle = await screen.findByTitle('启用（持久化）');
    fireEvent.click(toggle);
    await waitFor(() => expect(mocks.enable).toHaveBeenCalledWith('plug_b'));
  });

  it('UI 预览按钮加载 ui.json 并渲染插件界面（M12）', async () => {
    render(<PluginsPage />);
    fireEvent.click(await screen.findByTitle('UI 预览'));
    expect(await screen.findByText('插件界面')).toBeTruthy();
    expect(mocks.getUI).toHaveBeenCalledWith('plug_a');
  });

  it('无 UI 定义的插件预览显示提示', async () => {
    mocks.getUI.mockResolvedValue({ widgets: [] });
    render(<PluginsPage />);
    fireEvent.click(await screen.findByTitle('UI 预览'));
    expect(await screen.findByText(/未定义 UI/)).toBeTruthy();
  });

  it('错误通道按钮加载并展示插件错误（M7）', async () => {
    render(<PluginsPage />);
    fireEvent.click(await screen.findByRole('button', { name: /错误通道/ }));
    expect(await screen.findByText('导入失败')).toBeTruthy();
    expect((await screen.findAllByText('plug_a')).length).toBeGreaterThanOrEqual(2);
    expect(mocks.errors).toHaveBeenCalled();
  });

  it('错误通道清空调用 pluginApi.clearErrors', async () => {
    render(<PluginsPage />);
    fireEvent.click(await screen.findByRole('button', { name: /错误通道/ }));
    fireEvent.click(await screen.findByRole('button', { name: /清空/ }));
    await waitFor(() => expect(mocks.clearErrors).toHaveBeenCalled());
  });
});
