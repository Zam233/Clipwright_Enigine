// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { buildRenderDownloadUrl } from './render';

describe('buildRenderDownloadUrl', () => {
  it('prefers the SSE output_path basename and URL-encodes it', () => {
    expect(buildRenderDownloadUrl('renders/渲染完成-发布会.mp4')).toBe(
      `/api/render/download/${encodeURIComponent('渲染完成-发布会.mp4')}`,
    );
  });

  it('prefers the output_path basename over the fallback filename', () => {
    expect(buildRenderDownloadUrl('renders/渲染完成-发布会.mp4', 'fallback.mp4')).toBe(
      `/api/render/download/${encodeURIComponent('渲染完成-发布会.mp4')}`,
    );
  });

  it('falls back to the filename when output_path is absent', () => {
    expect(buildRenderDownloadUrl(undefined, 'fallback.mp4')).toBe('/api/render/download/fallback.mp4');
    expect(buildRenderDownloadUrl('', 'fallback.mp4')).toBe('/api/render/download/fallback.mp4');
  });

  it('uses the whole output_path when it has no directory part', () => {
    expect(buildRenderDownloadUrl('渲染完成-发布会.mp4')).toBe(
      `/api/render/download/${encodeURIComponent('渲染完成-发布会.mp4')}`,
    );
  });

  it('handles Windows-style separators in output_path', () => {
    expect(buildRenderDownloadUrl('renders\\渲染完成-发布会.mp4')).toBe(
      `/api/render/download/${encodeURIComponent('渲染完成-发布会.mp4')}`,
    );
  });

  it('returns an empty string when no filename is available', () => {
    expect(buildRenderDownloadUrl()).toBe('');
    expect(buildRenderDownloadUrl(undefined, '')).toBe('');
  });
});
