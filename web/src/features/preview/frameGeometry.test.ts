import { describe, expect, it } from 'vitest';
import { computePreviewFrameRect, PREVIEW_MARGIN, xToScrubTime } from './frameGeometry';

describe('computePreviewFrameRect', () => {
  it('16:9 (1920×1080) 在 685×471 面板中宽度优先，不触发高度封顶', () => {
    // (685-32)=653；aspect=16/9≈1.7778 → fh≈367.3；(471-32)=439 ≥ 367.3 → 不封顶
    const rect = computePreviewFrameRect(685, 471, 1920, 1080, 1);
    expect(Math.round(rect.fw)).toBe(653);
    expect(Math.round(rect.fh)).toBe(367);
    // 居中：两侧/上下留白相等且为正
    expect(rect.fx).toBeCloseTo((685 - 653) / 2, 5);
    expect(rect.fy).toBeCloseTo((471 - 367.3125) / 2, 5);
    expect(rect.fx).toBeGreaterThan(0);
    expect(rect.fy).toBeGreaterThan(0);
  });

  it('竖版/高度封顶：800×400 面板下 fh 先触顶，fw 按宽高比回算', () => {
    // fh=(400-32)=368 先封顶；fw=368*16/9≈654.2
    const rect = computePreviewFrameRect(800, 400, 1920, 1080, 1);
    expect(rect.fh).toBeLessThanOrEqual(400 - PREVIEW_MARGIN);
    expect(rect.fh).toBeCloseTo(400 - PREVIEW_MARGIN, 5);
    expect(Math.round(rect.fw)).toBe(654);
  });

  it('tlH=0 时回退到 16:9，不产生除零', () => {
    // 与第一例（1920×1080）数值一致
    const rect = computePreviewFrameRect(685, 471, 1920, 0, 1);
    expect(rect.fw).toBeCloseTo(653, 5);
    expect(rect.fh).toBeCloseTo(653 / (16 / 9), 5);
  });

  it('PREVIEW_MARGIN 已导出且等于 32', () => {
    expect(PREVIEW_MARGIN).toBe(32);
  });
});

describe('xToScrubTime', () => {
  const rect = computePreviewFrameRect(685, 471, 1920, 1080, 1);

  it('帧左边缘 → 0', () => {
    expect(xToScrubTime(rect.fx, rect, 10)).toBe(0);
  });

  it('帧右边缘 → durationSec', () => {
    expect(xToScrubTime(rect.fx + rect.fw, rect, 10)).toBe(10);
  });

  it('帧中点 → durationSec / 2', () => {
    expect(xToScrubTime(rect.fx + rect.fw / 2, rect, 10)).toBeCloseTo(5, 5);
  });

  it('越界钳制：panelX < fx → 0，panelX > fx+fw → durationSec', () => {
    expect(xToScrubTime(rect.fx - 100, rect, 10)).toBe(0);
    expect(xToScrubTime(rect.fx + rect.fw + 100, rect, 10)).toBe(10);
  });

  it('durationSec = 0 → 0', () => {
    expect(xToScrubTime(rect.fx + 10, rect, 0)).toBe(0);
    expect(xToScrubTime(rect.fx + 10, rect, -5)).toBe(0);
  });
});
