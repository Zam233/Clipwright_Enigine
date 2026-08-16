import { describe, it, expect } from 'vitest';
import { EasingFunctions, EASING_NAMES, interpolateProperties } from './easing';
import type { KeyframeLike } from './easing';

describe('EasingFunctions', () => {
  it('every easing satisfies f(0)=0 and f(1)=1', () => {
    for (const name of EASING_NAMES) {
      const f = EasingFunctions[name];
      expect(f(0), `${name}(0)`).toBeCloseTo(0, 5);
      expect(f(1), `${name}(1)`).toBeCloseTo(1, 5);
    }
  });

  it('linear is identity', () => {
    expect(EasingFunctions.linear(0.3)).toBeCloseTo(0.3, 10);
    expect(EasingFunctions.linear(0.7)).toBeCloseTo(0.7, 10);
  });

  it('ease-in is below linear in the first half', () => {
    expect(EasingFunctions['ease-in'](0.25)).toBeLessThan(0.25);
  });
});

describe('interpolateProperties', () => {
  it('returns empty for no keyframes', () => {
    expect(interpolateProperties([], 0.5)).toEqual({});
  });

  it('returns the single keyframe properties regardless of progress', () => {
    const kfs: KeyframeLike[] = [{ time: 0.5, properties: { opacity: 0.7 } }];
    expect(interpolateProperties(kfs, 0)).toEqual({ opacity: 0.7 });
    expect(interpolateProperties(kfs, 1)).toEqual({ opacity: 0.7 });
  });

  it('clamps to first/last keyframe outside range', () => {
    const kfs: KeyframeLike[] = [
      { time: 0.2, properties: { opacity: 0 } },
      { time: 0.8, properties: { opacity: 1 } },
    ];
    expect(interpolateProperties(kfs, 0).opacity).toBe(0);
    expect(interpolateProperties(kfs, 1).opacity).toBe(1);
  });

  it('linearly interpolates midpoint with linear easing', () => {
    const kfs: KeyframeLike[] = [
      { time: 0, properties: { opacity: 0 } },
      { time: 1, properties: { opacity: 1 }, easing: 'linear' },
    ];
    expect(interpolateProperties(kfs, 0.5).opacity).toBeCloseTo(0.5, 5);
    expect(interpolateProperties(kfs, 0.25).opacity).toBeCloseTo(0.25, 5);
  });

  it('interpolates multiple properties independently', () => {
    const kfs: KeyframeLike[] = [
      { time: 0, properties: { opacity: 0, scale: 1 } },
      { time: 1, properties: { opacity: 1, scale: 2 }, easing: 'linear' },
    ];
    const mid = interpolateProperties(kfs, 0.5);
    expect(mid.opacity).toBeCloseTo(0.5, 5);
    expect(mid.scale).toBeCloseTo(1.5, 5);
  });

  it('handles unsorted keyframes', () => {
    const kfs: KeyframeLike[] = [
      { time: 1, properties: { opacity: 1 }, easing: 'linear' },
      { time: 0, properties: { opacity: 0 } },
    ];
    expect(interpolateProperties(kfs, 0.5).opacity).toBeCloseTo(0.5, 5);
  });
});
