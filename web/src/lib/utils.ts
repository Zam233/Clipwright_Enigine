import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { ClipKind } from '@/types/timeline';

/** Merge Tailwind classes with clsx */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Normalize a raw kind string to a valid ClipKind, with case-insensitive matching */
export function normalizeClipKind(raw: string | undefined | null): ClipKind {
  if (raw == null) return 'image';
  const k = raw.toLowerCase().trim();
  if (k === 'video' || k.startsWith('video')) return 'video';
  if (k === 'audio' || k === 'music' || k.startsWith('audio')) return 'audio';
  if (k === 'text' || k === 'caption') return 'text';
  if (k === 'image' || k === 'photo' || k.startsWith('image')) return 'image';
  if (k === 'waveform') return 'waveform';
  if (k === 'animation') return 'animation';
  if (k === 'shape') return 'shape';
  console.warn(`[normalizeClipKind] Unknown kind "${raw}", falling back to "image"`);
  return 'image';
}

/** Generate a short unique ID */
export function uid(prefix = ''): string {
  const rand = Math.random().toString(36).slice(2, 10);
  const time = Date.now().toString(36);
  return prefix ? `${prefix}_${time}${rand}` : `${time}${rand}`;
}

/** Format seconds to timecode HH:MM:SS:FF */
export function formatTimecode(seconds: number, fps = 30): string {
  if (!isFinite(seconds) || seconds < 0 || fps <= 0 || !isFinite(fps)) return '00:00:00:00';
  const totalFrames = Math.round(seconds * fps);
  const ff = totalFrames % Math.round(fps);
  const totalSecs = Math.floor(totalFrames / Math.round(fps));
  const ss = totalSecs % 60;
  const mm = Math.floor(totalSecs / 60) % 60;
  const hh = Math.floor(totalSecs / 3600);
  return [hh, mm, ss, ff]
    .map((v) => String(v).padStart(2, '0'))
    .join(':');
}

/** Format seconds to short display (e.g., "1:23.4") */
export function formatTimeShort(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '0s';
  // 先四舍五入到 0.1s 再拆分，避免 59.96 → "60.0s"
  const rounded = Math.round(seconds * 10) / 10;
  const mm = Math.floor(rounded / 60);
  const ss = (rounded % 60).toFixed(1);
  return mm > 0 ? `${mm}:${ss.padStart(4, '0')}` : `${ss}s`;
}

/** Clamp a number between min and max */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** Linear interpolation */
export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Debounce a function */
export function debounce<T extends (...args: unknown[]) => void>(
  fn: T,
  ms: number,
): (...args: Parameters<T>) => void {
  let timer: ReturnType<typeof setTimeout>;
  return (...args: Parameters<T>) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/** Format duration in seconds to MM:SS */
export function fmtDur(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/** Relative time display in Chinese */
export function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} 天前`;
  return new Date(iso).toLocaleDateString('zh-CN');
}

/** Throttle a function */
export function throttle<T extends (...args: unknown[]) => void>(
  fn: T,
  ms: number,
): (...args: Parameters<T>) => void {
  let last = 0;
  return (...args: Parameters<T>) => {
    const now = Date.now();
    if (now - last >= ms) {
      last = now;
      fn(...args);
    }
  };
}
