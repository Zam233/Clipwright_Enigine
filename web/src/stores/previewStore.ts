import { create } from 'zustand';

interface PreviewState {
  isPlaying: boolean;
  currentTimeSec: number;
  durationSec: number;
  fps: number;
  volume: number;
  isMuted: boolean;
  isFullscreen: boolean;
  zoomLevel: number;
  showSafeArea: boolean;
  loopRegion: { start: number; end: number } | null;
  isLooping: boolean;
  shuttleSpeed: number;
  playbackSpeed: number;

  // Actions
  setPlaying: (playing: boolean) => void;
  togglePlay: () => void;
  setCurrentTime: (timeSec: number) => void;
  setDuration: (durationSec: number) => void;
  setFps: (fps: number) => void;
  setVolume: (volume: number) => void;
  toggleMute: () => void;
  setFullscreen: (fullscreen: boolean) => void;
  setZoomLevel: (zoom: number) => void;
  toggleSafeArea: () => void;
  setLoopRegion: (region: { start: number; end: number } | null) => void;
  toggleLoop: () => void;
  stepForward: () => void;
  stepBackward: () => void;
  seekToStart: () => void;
  seekToEnd: () => void;
  shuttleReverse: () => void;
  shuttleForward: () => void;
  shuttleStop: () => void;
  setMarkerIn: () => void;
  setMarkerOut: () => void;
  setPlaybackSpeed: (speed: number) => void;
  resetPreview: () => void;
}

export const usePreviewStore = create<PreviewState>((set, get) => ({
  isPlaying: false,
  currentTimeSec: 0,
  durationSec: 0,
  fps: 30,
  volume: 1,
  isMuted: false,
  isFullscreen: false,
  zoomLevel: 1,
  showSafeArea: false,
  loopRegion: null,
  isLooping: false,
  shuttleSpeed: 0,
  playbackSpeed: 1,

  setPlaying: (playing) => set({ isPlaying: playing }),
  togglePlay: () =>
    set((state) => {
      // Restart from beginning if playhead is at/past the end; clear stale shuttle state
      const atEnd = state.durationSec > 0 && state.currentTimeSec >= state.durationSec - 1e-3;
      return {
        isPlaying: !state.isPlaying,
        shuttleSpeed: 0,
        currentTimeSec: !state.isPlaying && atEnd ? 0 : state.currentTimeSec,
      };
    }),
  setCurrentTime: (timeSec) =>
    set((state) => ({
      // duration 尚未同步时不做上限钳位，避免 playhead 卡在 0
      currentTimeSec:
        state.durationSec > 0
          ? Math.max(0, Math.min(timeSec, state.durationSec))
          : Math.max(0, timeSec),
    })),
  setDuration: (durationSec) =>
    set((state) => ({
      durationSec,
      // Keep playhead within the new (possibly shrunk) duration
      currentTimeSec: Math.min(state.currentTimeSec, Math.max(0, durationSec)),
    })),
  setFps: (fps) => set({ fps }),
  setVolume: (volume) => set({ volume }),
  toggleMute: () => set((state) => ({ isMuted: !state.isMuted })),
  setFullscreen: (fullscreen) => set({ isFullscreen: fullscreen }),
  setZoomLevel: (zoom) => set({ zoomLevel: zoom }),
  toggleSafeArea: () => set((state) => ({ showSafeArea: !state.showSafeArea })),
  setLoopRegion: (region) => set({ loopRegion: region }),

  stepForward: () =>
    set((state) => {
      if (state.fps <= 0) return {};
      const frame = Math.round(state.currentTimeSec * state.fps) + 1;
      const time = frame / state.fps;
      return {
        currentTimeSec: Math.min(state.durationSec, Math.max(0, time)),
      };
    }),

  stepBackward: () =>
    set((state) => {
      if (state.fps <= 0) return {};
      const frame = Math.round(state.currentTimeSec * state.fps) - 1;
      const time = frame / state.fps;
      return { currentTimeSec: Math.max(0, time) };
    }),

  seekToStart: () => set({ currentTimeSec: 0 }),
  seekToEnd: () =>
    set((state) => ({
      // One frame before the end so the last frame stays visible
      currentTimeSec: state.fps > 0 ? Math.max(0, state.durationSec - 1 / state.fps) : state.durationSec,
    })),
  toggleLoop: () => set((state) => ({ isLooping: !state.isLooping })),
  shuttleReverse: () => set({ isPlaying: true, shuttleSpeed: -1 }),
  shuttleForward: () => set({ isPlaying: true, shuttleSpeed: 1 }),
  shuttleStop: () => set({ isPlaying: false, shuttleSpeed: 0 }),
  setMarkerIn: () => {
    const { currentTimeSec, loopRegion, durationSec } = get();
    const end = loopRegion?.end ?? durationSec;
    // Ensure start < end to avoid an inverted (jamming) region; clamp to >= 0
    set({ loopRegion: { start: Math.min(Math.max(currentTimeSec, 0), Math.max(end - 0.01, 0)), end } });
  },
  setMarkerOut: () => {
    const { currentTimeSec, loopRegion, durationSec } = get();
    const start = loopRegion?.start ?? 0;
    set({ loopRegion: { start, end: Math.min(Math.max(currentTimeSec, start + 0.01), Math.max(durationSec, start + 0.01)) } });
  },
  setPlaybackSpeed: (speed) => set({ playbackSpeed: Math.max(0.25, Math.min(4, speed)) }),
  resetPreview: () =>
    set({
      isPlaying: false, currentTimeSec: 0, durationSec: 0, fps: 30,
      volume: 1, isMuted: false, isFullscreen: false, zoomLevel: 1,
      showSafeArea: false, loopRegion: null, isLooping: false,
      shuttleSpeed: 0, playbackSpeed: 1,
    }),
}));
