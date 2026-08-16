/**
 * Timeline data types — matching backend clipwright/schema/timeline.py
 * This is the core data contract: Agent output, editor loading, and user
 * modifications all operate on the same data model.
 */

export type ClipKind =
  | 'video'
  | 'audio'
  | 'text'
  | 'image'
  | 'caption'
  | 'shape'
  | 'waveform'
  | 'animation';

export type TransitionType =
  | 'hard_cut'
  | 'fade'
  | 'dissolve'
  | 'glitch'
  | 'pixel_dissolve'
  | 'slide'
  | 'wipe';

export type ImageFit = 'cover' | 'contain' | 'free';

export type TextAlign = 'left' | 'center' | 'right';

/** Keyframe for per-clip property animation */
export interface Keyframe {
  /** Normalized time within clip (0-1) */
  time: number;
  /** Property values at this keyframe */
  properties: Record<string, number>;
  /** Easing function name */
  easing?: string;
}

/** A single clip on the timeline */
export interface Clip {
  id: string;
  kind: ClipKind;
  asset_id: string;
  track_id: string;

  // Time
  start_sec: number;
  duration_sec: number;
  source_offset_sec: number;

  // Speed / Volume
  speed: number;
  volume: number;
  opacity: number;
  blend_mode?: string | null;
  enabled?: boolean;
  eq_preset?: string | null;
  label_color?: string | null;
  notes?: string | null;

  // Video effects (video / image only)
  fx_brightness?: number | null;   // 0-2, default 1
  fx_contrast?: number | null;     // 0-2, default 1
  fx_saturation?: number | null;   // 0-2, default 1
  fx_blur?: number | null;         // 0-10 px, default 0
  fx_hue?: number | null;          // 0-360 deg, default 0

  // Layout (video / image only)
  image_fit?: ImageFit | null;
  image_rect?: { x: number; y: number; w: number; h: number } | null;

  // Text content (text / caption only)
  text?: string | null;
  font?: string | null;
  font_size?: number | null;
  font_color?: string | null;
  text_align?: TextAlign | null;

  // Caption style (text / caption only) — aligned with backend schema/timeline.py
  font_weight?: string | null;
  font_italic?: boolean | null;
  letter_spacing?: number | null;
  stroke_width?: number | null;
  stroke_color?: string | null;
  shadow_x?: number | null;
  shadow_y?: number | null;
  shadow_color?: string | null;
  shadow_blur?: number | null;
  glow_color?: string | null;
  glow_width?: number | null;

  // Transitions
  transition_in?: string | null;
  transition_out?: string | null;
  transition_duration_sec?: number | null;

  // Shape (shape only)
  shape?: string | null;
  fill?: string | null;

  // Waveform (waveform only)
  bar_count?: number | null;
  bar_width?: number | null;

  // Keyframe animation
  keyframes: Keyframe[];

  // Extension metadata
  metadata: Record<string, unknown>;
}

/** A timeline track */
export interface Track {
  id: string;
  name: string;
  kind: ClipKind;
  index: number;
  clips: Clip[];
  locked: boolean;
  muted: boolean;
}

/** Complete timeline — unified format for Agent output and editor */
export interface Timeline {
  id: string;
  width: number;
  height: number;
  fps: number;
  duration_sec: number;
  tracks: Track[];
}

/** Create an empty timeline with defaults */
export function createEmptyTimeline(id = ''): Timeline {
  return {
    id,
    width: 1920,
    height: 1080,
    fps: 30,
    duration_sec: 0,
    tracks: [],
  };
}

/** Create a default clip */
export function createDefaultClip(
  overrides: Partial<Clip> & { id: string; kind: ClipKind; track_id: string },
): Clip {
  return {
    asset_id: '',
    start_sec: 0,
    duration_sec: 5,
    source_offset_sec: 0,
    speed: 1,
    volume: 1,
    opacity: 1,
    enabled: true,
    blend_mode: null,
    label_color: null,
    notes: null,
    eq_preset: null,
    fx_brightness: null,
    fx_contrast: null,
    fx_saturation: null,
    fx_blur: null,
    fx_hue: null,
    image_fit: null,
    image_rect: null,
    text: null,
    font: null,
    font_size: null,
    font_color: null,
    text_align: null,
    font_weight: null,
    font_italic: null,
    letter_spacing: null,
    stroke_width: null,
    stroke_color: null,
    shadow_x: null,
    shadow_y: null,
    shadow_color: null,
    shadow_blur: null,
    glow_color: null,
    glow_width: null,
    transition_in: null,
    transition_out: null,
    transition_duration_sec: null,
    shape: null,
    fill: null,
    bar_count: null,
    bar_width: null,
    keyframes: [],
    metadata: {},
    ...overrides,
  };
}

/** Track color mapping by kind */
export const TRACK_COLORS: Record<ClipKind, string> = {
  video: '#4F8CFF',
  audio: '#34D399',
  text: '#FBBF24',
  image: '#A855F7',
  caption: '#F59E0B',
  shape: '#8B5CF6',
  waveform: '#34D399',
  animation: '#FF6B6B',
};

/**
 * Compute total duration from tracks.
 *
 * This is the TIMELINE length (max end across all clips) and is intentionally
 * NOT clamped. The backend ASS renderer clamps each caption Dialogue end to the
 * actual output duration (Dialogue End = min(start + dur, actual duration)), so
 * a rendered caption tail can never exceed the timeline length. Keep this
 * function as-is: the timeline duration and the ASS-clamped caption end stay
 * consistent by construction.
 */
export function computeTotalDuration(tracks: Track[]): number {
  let maxEnd = 0;
  for (const track of tracks) {
    for (const clip of track.clips) {
      const end = clip.start_sec + clip.duration_sec;
      if (end > maxEnd) maxEnd = end;
    }
  }
  return maxEnd;
}
