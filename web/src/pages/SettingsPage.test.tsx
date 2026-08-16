// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SettingsPage } from './SettingsPage';

vi.mock('@/services/api', () => ({
  healthApi: { check: vi.fn().mockResolvedValue({ status: 'ok' }) },
  assetApi: { listSources: vi.fn().mockResolvedValue([]) },
  resetApiClient: vi.fn(),
}));

vi.mock('@/stores/settingsStore', () => ({
  useSettingsStore: () => ({
    apiBaseUrl: 'http://localhost:8000',
    setApiBaseUrl: vi.fn(),
    theme: 'dark',
    setTheme: vi.fn(),
    language: 'zh',
    setLanguage: vi.fn(),
    defaultFps: 30,
    setDefaultFps: vi.fn(),
    snapThresholdPx: 8,
    setSnapThreshold: vi.fn(),
    defaultResolution: { width: 1920, height: 1080 },
    setDefaultResolution: vi.fn(),
    autoSave: true,
    setAutoSave: vi.fn(),
  }),
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

describe('SettingsPage (U2 / U16a)', () => {
  it('does not render the removed WebSocket address field', () => {
    render(<SettingsPage />);
    expect(screen.queryByText('WebSocket 地址')).toBeNull();
    // API field still present
    expect(screen.getByText('API 地址')).toBeTruthy();
  });

  it('renders the content container centered (mx-auto)', () => {
    const { container } = render(<SettingsPage />);
    const wrapper = container.querySelector('.max-w-2xl');
    expect(wrapper).toBeTruthy();
    expect(wrapper!.className).toContain('mx-auto');
  });
});
