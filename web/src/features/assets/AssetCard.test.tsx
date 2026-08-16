// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { AssetCard } from './AssetPanel';
import type { Asset } from '@/types/api';

vi.mock('@/services/media/mediaManager', () => ({
  mediaManager: {
    hasRealMedia: vi.fn(() => true),
    captureThumbnail: vi.fn(() => Promise.resolve('data:image/png;base64,abc')),
    onChange: vi.fn(() => () => {}),
    getDuration: vi.fn(() => 5.0),
  },
}));

vi.mock('@/stores/assetStore', () => ({
  useAssetStore: Object.assign(vi.fn((sel: any) => sel({ activeTab: 'library', setActiveTab: vi.fn() })), {
    getState: () => ({ activeTab: 'library', setActiveTab: vi.fn() }),
  }),
}));

const mockAsset: Asset = {
  id: 'test-1', kind: 'video', filename: 'test.mp4', duration_sec: 5.0,
  source: 'local', source_path: '', metadata: {},
  path: '', tags: [], created_at: '',
} as Asset;

describe('AssetCard overlay restructure', () => {
  it('overlay div: pointer-events-none, DIV tag, no onClick', () => {
    const onAdd = vi.fn();
    render(<AssetCard asset={mockAsset} onAdd={onAdd} />);
    const overlay = document.querySelector('.pointer-events-none');
    expect(overlay).toBeTruthy();
    expect(overlay!.tagName).toBe('DIV');
    expect((overlay as HTMLElement).onclick).toBeNull();
  });

  it('badge button: pointer-events-auto, BUTTON tag, click triggers onAdd', async () => {
    const onAdd = vi.fn();
    const { container } = render(<AssetCard asset={mockAsset} onAdd={onAdd} />);
    // Wait for async thumbnail load so badge is rendered in full
    await waitFor(() => {
      expect(container.querySelector('img')).toBeTruthy();
    });
    const badge = within(container).getByRole('button');
    expect(badge).toBeTruthy();
    expect(badge.tagName).toBe('BUTTON');
    expect(badge.className).toContain('pointer-events-auto');
    fireEvent.click(badge);
    expect(onAdd).toHaveBeenCalledTimes(1);
  });

  it('clicking overlay div does NOT trigger onAdd', () => {
    const onAdd = vi.fn();
    render(<AssetCard asset={mockAsset} onAdd={onAdd} />);
    const overlay = document.querySelector('.pointer-events-none')!;
    fireEvent.click(overlay);
    expect(onAdd).not.toHaveBeenCalled();
  });

  it('thumbnail img has draggable=false', async () => {
    const onAdd = vi.fn();
    render(<AssetCard asset={mockAsset} onAdd={onAdd} />);
    await waitFor(() => {
      expect(document.querySelector('img')).toBeTruthy();
    });
    const img = document.querySelector('img')!;
    expect(img.getAttribute('draggable')).toBe('false');
  });

  it('card root div has draggable=true', () => {
    const onAdd = vi.fn();
    const { container } = render(<AssetCard asset={mockAsset} onAdd={onAdd} />);
    const root = container.firstElementChild!;
    expect(root.getAttribute('draggable')).toBe('true');
  });
});
