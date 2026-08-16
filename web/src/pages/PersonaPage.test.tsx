// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { PersonaPage } from './PersonaPage';
import type { Persona } from '@/types/persona';

const { mocks } = vi.hoisted(() => ({
  mocks: { list: vi.fn(), duplicate: vi.fn(), export: vi.fn(), importPersona: vi.fn() },
}));

vi.mock('@/services/api', () => ({
  personaApi: { list: mocks.list, duplicate: mocks.duplicate, export: mocks.export, importPersona: mocks.importPersona },
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

afterEach(() => cleanup());

/** Persona with neither positioning nor tone in identity. */
const barePersona = {
  persona_id: 'per_bare',
  persona_name: '空白人格',
  version: '0.1.0',
  parameter: {
    identity: {},
    language: {},
    rhythm: {},
    visual: {},
    audio: {},
    constraints: {},
  },
} as unknown as Persona;

describe('PersonaPage tone fallback (U19)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue([barePersona]);
  });

  it("shows '未设置' subtitle when both positioning and tone are missing", async () => {
    render(<PersonaPage />);
    expect(await screen.findByText('未设置')).toBeTruthy();
    expect(screen.getByText('空白人格')).toBeTruthy();
  });
});

describe('PersonaPage P10 复制/导出/导入', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue([barePersona]);
    mocks.duplicate.mockResolvedValue(barePersona);
    mocks.export.mockResolvedValue({ persona: barePersona, version: '0.1.0' });
    mocks.importPersona.mockResolvedValue(barePersona);
  });

  it('card 复制按钮调用 personaApi.duplicate 并刷新列表', async () => {
    render(<PersonaPage />);
    fireEvent.click(await screen.findByTitle('复制人格'));
    await waitFor(() => expect(mocks.duplicate).toHaveBeenCalledWith('per_bare'));
    expect(mocks.list).toHaveBeenCalled();
  });

  it('card 导出按钮触发 personaApi.export 并下载 JSON 文件', async () => {
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:mock'), revokeObjectURL: vi.fn() });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<PersonaPage />);
    fireEvent.click(await screen.findByTitle('导出入格'));
    await waitFor(() => expect(mocks.export).toHaveBeenCalledWith('per_bare'));
    expect(URL.createObjectURL).toHaveBeenCalled();
    clickSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  it('导入输入框提交调用 personaApi.importPersona', async () => {
    render(<PersonaPage />);
    fireEvent.click(screen.getAllByRole('button', { name: '导入' })[0]); // header toggle
    const input = screen.getByPlaceholderText(/粘贴 Persona JSON/);
    fireEvent.change(input, { target: { value: JSON.stringify({ persona: barePersona }) } });
    fireEvent.click(screen.getAllByRole('button', { name: '导入' })[1]); // inline submit
    await waitFor(() => expect(mocks.importPersona).toHaveBeenCalledTimes(1));
  });
});
