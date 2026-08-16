import { describe, it, expect } from 'vitest';
import { captionBaselineY, captionFontSize } from './captionLayout';

describe('captionBaselineY — ASS bottom anchor (h-text_h-20)', () => {
  it('anchors the caption baseline 20px above the frame bottom (1080 → 1060)', () => {
    expect(captionBaselineY(1080)).toBe(1060);
  });

  it('is frame-local (before the frame top offset fy is added)', () => {
    expect(captionBaselineY(1080) + 100).toBe(1160);
  });
});

describe('captionFontSize — min-dimension scaling for portrait frames', () => {
  it('scales by the smaller dimension so caption text fits portrait exports', () => {
    // 1080x1920 portrait: height-only scaling would give 48 * (1920/1080) ≈ 85.
    const size = captionFontSize(48, 1080, 1920);
    expect(size).toBe(48);
    expect(size).toBeLessThanOrEqual(1920);
  });

  it('matches the reference scale on a 16:9 1080p frame', () => {
    expect(captionFontSize(48, 1920, 1080)).toBe(48);
  });

  it('respects the transform scale factor', () => {
    expect(captionFontSize(48, 1920, 1080, 1.5)).toBe(72);
  });
});
