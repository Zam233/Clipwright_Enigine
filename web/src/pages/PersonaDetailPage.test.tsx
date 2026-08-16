// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PersonaDetailPage } from './PersonaDetailPage';
import type { Persona } from '@/types/persona';

const personaId = 'per_test_1';

const persona: Persona = {
  persona_id: personaId,
  persona_name: '测试人格',
  version: '1.0.0',
  prompt: '系统 Prompt 初始值',
  parameter: {
    identity: { persona_id: personaId, persona_name: '测试人格', version: '1.0.0', tone: 'warm_storyteller', positioning: '', knowledge_domains: [] },
    language: { max_sentence_length: 25, academic_density: 0.5, slang_ratio: 0.3 },
    rhythm: { cut_density_tier: 'medium', base_shot_duration_sec: 6 },
    visual: { color_palette: { primary: '#1a1a2e', accent: '#e94560' }, animation_style: '' },
    audio: { loudness_target_lufs: -16 },
    constraints: { max_duration_sec: 900 },
  },
};

const { mocks } = vi.hoisted(() => ({
  mocks: {
    get: vi.fn(),
    getPrompt: vi.fn(),
    getVisionPrompt: vi.fn(),
    updatePrompt: vi.fn(),
    updateVisionPrompt: vi.fn(),
    getKnowledge: vi.fn(),
    list: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  personaApi: {
    get: mocks.get,
    getPrompt: mocks.getPrompt,
    getVisionPrompt: mocks.getVisionPrompt,
    updatePrompt: mocks.updatePrompt,
    updateVisionPrompt: mocks.updateVisionPrompt,
    getKnowledge: mocks.getKnowledge,
  },
  voiceApi: {
    list: mocks.list,
  },
}));

vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ personaId }),
  useNavigate: () => vi.fn(),
}));

describe('PersonaDetailPage vision_prompt', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue(persona);
    mocks.getPrompt.mockResolvedValue({ persona_id: personaId, prompt: '系统 Prompt 初始值' });
    mocks.getVisionPrompt.mockResolvedValue({ persona_id: personaId, vision_prompt: '视觉 Prompt 初始值' });
    mocks.getKnowledge.mockResolvedValue([]);
    mocks.list.mockResolvedValue([]);
    mocks.updatePrompt.mockResolvedValue({ status: 'ok', persona_id: personaId });
    mocks.updateVisionPrompt.mockResolvedValue({ status: 'ok', persona_id: personaId });
  });

  it('loads vision prompt and renders a second textarea in the Prompt tab', async () => {
    render(<PersonaDetailPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Prompt' }));

    await waitFor(() => {
      expect(mocks.getVisionPrompt).toHaveBeenCalledWith(personaId);
    });

    const textareas = screen.getAllByRole('textbox');
    expect(textareas.length).toBe(2);
    const visionTextarea = textareas[1];
    expect((visionTextarea as HTMLTextAreaElement).value).toBe('视觉 Prompt 初始值');
    expect(
      screen.getByText(/视觉需求 Prompt（注入结构\/动画\/MG 生成的画面风格）/),
    ).toBeTruthy();
    expect(screen.getByText(/vision-prompt/)).toBeTruthy();
  });

  it('can type into the vision prompt textarea and save via updateVisionPrompt', async () => {
    render(<PersonaDetailPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Prompt' }));

    const textareas = screen.getAllByRole('textbox');
    const visionTextarea = textareas[1];
    fireEvent.change(visionTextarea, { target: { value: '冷色调科技感画面' } });

    fireEvent.click(screen.getByRole('button', { name: '保存视觉需求 Prompt' }));

    await waitFor(() => {
      expect(mocks.updateVisionPrompt).toHaveBeenCalledWith(personaId, '冷色调科技感画面');
    });
  });
});
