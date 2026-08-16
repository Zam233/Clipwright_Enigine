// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { PluginLayoutRenderer } from './PluginLayoutRenderer';
import type { UILayout } from './types';

afterEach(() => cleanup());

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  getApiClient: () => ({
    get: mocks.get,
    post: mocks.post,
  }),
}));

describe('PluginLayoutRenderer M11 控件集', () => {
  it('渲染 input / select / checkbox / slider 并写入状态', () => {
    const layout: UILayout = {
      widgets: [
        { type: 'input', key: 'name', label: '名称', defaultValue: '张三' },
        { type: 'select', key: 'tone', label: '语气', options: ['专业', '幽默'], defaultValue: '幽默' },
        { type: 'checkbox', key: 'enabled', label: '启用', defaultValue: true },
        { type: 'slider', key: 'speed', label: '速度', min: 0, max: 10, step: 1, defaultValue: 5 },
        {
          type: 'button', label: '提交',
          action: { endpoint: '/api/test', method: 'POST', body: { name: '${name}', tone: '${tone}', enabled: '${enabled}', speed: '${speed}' } },
        },
      ],
    };

    mocks.post.mockResolvedValue({ data: { ok: true } });
    render(<PluginLayoutRenderer layout={layout} />);

    // input defaultValue
    expect((screen.getByLabelText('名称') as HTMLInputElement).value).toBe('张三');
    // select defaultValue
    expect((screen.getByLabelText('语气') as HTMLSelectElement).value).toBe('幽默');
    // checkbox defaultValue
    expect((screen.getByLabelText('启用') as HTMLInputElement).checked).toBe(true);
    // slider defaultValue
    expect((screen.getByLabelText(/速度/) as HTMLInputElement).value).toBe('5');

    // 修改控件后按钮提交携带插值状态
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '李四' } });
    fireEvent.change(screen.getByLabelText('语气'), { target: { value: '专业' } });
    fireEvent.click(screen.getByLabelText('启用'));
    fireEvent.change(screen.getByLabelText(/速度/), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: '提交' }));

    expect(mocks.post).toHaveBeenCalledWith('/api/test',
      { name: '李四', tone: '专业', enabled: false, speed: 8 },
      { headers: { 'Content-Type': 'application/json' } });
  });

  it('visibleWhen 隐藏条件生效', () => {
    const layout: UILayout = {
      widgets: [
        { type: 'checkbox', key: 'advanced', label: '高级模式', defaultValue: false },
        { type: 'input', key: 'secret', label: '密钥', visibleWhen: 'advanced', defaultValue: '' },
      ],
    };
    render(<PluginLayoutRenderer layout={layout} />);
    expect(screen.queryByLabelText('密钥')).toBeNull();
    fireEvent.click(screen.getByLabelText('高级模式'));
    expect(screen.getByLabelText('密钥')).toBeTruthy();
  });
});
