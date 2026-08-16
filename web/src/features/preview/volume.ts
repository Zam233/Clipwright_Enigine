import { clamp } from '@/lib/utils';

/** 主音量 × 片段音量，各自钳位到 [0,1]。Master volume × clip volume, each clamped to [0,1]. */
export function applyMasterVolume(clipVolume: number, master: number): number {
  return clamp(clipVolume, 0, 1) * clamp(master, 0, 1);
}
