/**
 * Keyframe interpolation engine — Penner easing functions + property interpolation.
 * (Design Plan P4-6)
 */

export type EasingName =
  | 'linear'
  | 'ease-in' | 'ease-out' | 'ease-in-out'
  | 'ease-in-quad' | 'ease-out-quad' | 'ease-in-out-quad'
  | 'ease-in-cubic' | 'ease-out-cubic' | 'ease-in-out-cubic'
  | 'ease-in-quart' | 'ease-out-quart' | 'ease-in-out-quart'
  | 'ease-in-expo' | 'ease-out-expo' | 'ease-in-out-expo'
  | 'ease-in-back' | 'ease-out-back' | 'ease-in-out-back'
  | 'ease-out-elastic' | 'ease-out-bounce';

export const EasingFunctions: Record<EasingName, (t: number) => number> = {
  linear: (t) => t,
  'ease-in': (t) => t * t,
  'ease-out': (t) => t * (2 - t),
  'ease-in-out': (t) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t),
  'ease-in-quad': (t) => t * t,
  'ease-out-quad': (t) => t * (2 - t),
  'ease-in-out-quad': (t) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t),
  'ease-in-cubic': (t) => t * t * t,
  'ease-out-cubic': (t) => (--t) * t * t + 1,
  'ease-in-out-cubic': (t) => (t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1),
  'ease-in-quart': (t) => t * t * t * t,
  'ease-out-quart': (t) => 1 - (--t) * t * t * t,
  'ease-in-out-quart': (t) => (t < 0.5 ? 8 * t * t * t * t : 1 - 8 * (--t) * t * t * t),
  'ease-in-expo': (t) => (t === 0 ? 0 : Math.pow(2, 10 * (t - 1))),
  'ease-out-expo': (t) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t)),
  'ease-in-out-expo': (t) => {
    if (t === 0 || t === 1) return t;
    return t < 0.5
      ? Math.pow(2, 20 * t - 10) / 2
      : (2 - Math.pow(2, -20 * t + 10)) / 2;
  },
  'ease-in-back': (t) => { const c = 1.70158; return (c + 1) * t * t * t - c * t * t; },
  'ease-out-back': (t) => { const c = 1.70158; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); },
  'ease-in-out-back': (t) => {
    const c = 1.70158 * 1.525;
    return t < 0.5
      ? (Math.pow(2 * t, 2) * ((c + 1) * 2 * t - c)) / 2
      : (Math.pow(2 * t - 2, 2) * ((c + 1) * (t * 2 - 2) + c) + 2) / 2;
  },
  'ease-out-elastic': (t) => {
    if (t === 0 || t === 1) return t;
    const c = (2 * Math.PI) / 3;
    return Math.pow(2, -10 * t) * Math.sin((t * 10 - 0.75) * c) + 1;
  },
  'ease-out-bounce': (t) => {
    const n1 = 7.5625, d1 = 2.75;
    if (t < 1 / d1) return n1 * t * t;
    if (t < 2 / d1) return n1 * (t -= 1.5 / d1) * t + 0.75;
    if (t < 2.5 / d1) return n1 * (t -= 2.25 / d1) * t + 0.9375;
    return n1 * (t -= 2.625 / d1) * t + 0.984375;
  },
};

export const EASING_NAMES = Object.keys(EasingFunctions) as EasingName[];

export interface KeyframeLike {
  time: number;
  properties: Record<string, number>;
  easing?: string;
}

/**
 * Interpolate keyframe properties at a given progress (0-1).
 * Uses binary search to find the surrounding keyframes, then applies the
 * segment's easing function per-property.
 */
export function interpolateProperties(
  keyframes: KeyframeLike[],
  progress: number,
  defaultEasing: EasingName = 'linear',
): Record<string, number> {
  if (keyframes.length === 0) return {};
  if (keyframes.length === 1) return { ...keyframes[0].properties };

  const sorted = [...keyframes].sort((a, b) => a.time - b.time);
  if (progress <= sorted[0].time) return { ...sorted[0].properties };
  if (progress >= sorted[sorted.length - 1].time) return { ...sorted[sorted.length - 1].properties };

  let lo = 0, hi = sorted.length - 1;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid].time <= progress) lo = mid; else hi = mid;
  }

  const prev = sorted[lo];
  const next = sorted[hi];
  const segDur = next.time - prev.time;
  const localT = segDur > 0 ? (progress - prev.time) / segDur : 0;
  const easingName = (next.easing as EasingName) || defaultEasing;
  const easedT = (EasingFunctions[easingName] ?? EasingFunctions.linear)(localT);

  const result: Record<string, number> = {};
  const allProps = new Set([
    ...Object.keys(prev.properties),
    ...Object.keys(next.properties),
  ]);
  for (const prop of allProps) {
    const a = prev.properties[prop] ?? next.properties[prop] ?? 0;
    const b = next.properties[prop] ?? prev.properties[prop] ?? 0;
    result[prop] = a + (b - a) * easedT;
  }
  return result;
}
