// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PersonaPage } from './PersonaPage';
import type { Persona } from '@/types/persona';

const { mocks } = vi.hoisted(() => ({
  mocks: { list: vi.fn() },
}));

vi.mock('@/services/api', () => ({
  personaApi: { list: mocks.list },
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

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
