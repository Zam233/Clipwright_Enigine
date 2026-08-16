/**
 * Caption layout helpers — keep the Canvas preview WYSIWYG-consistent with the
 * backend ASS/libass renderer (see clipwright/schema/timeline.py + design.py).
 *
 * - Bottom anchor: ASS bottom alignment maps to `h-text_h-20` (design.py), i.e.
 *   the caption baseline sits 20px up from the frame bottom edge. The preview
 *   keeps textBaseline='middle' but anchors the text the same distance from the
 *   bottom, so it never drifts like the old 0.85-height heuristic.
 * - Font size: scaled by the SMALLER of the two frame dimensions so caption text
 *   never overflows a portrait frame (e.g. 1080x1920 exports, where height-only
 *   scaling would overshoot the width). The 1080 reference matches the default
 *   1080p timeline.
 */

/** Bottom-anchored caption baseline (frame-local, before the frame top fy is added). */
export function captionBaselineY(fh: number, bottomMargin = 20): number {
  return fh - bottomMargin;
}

/** Caption font size scaled to the frame via the min-dimension (portrait-safe). */
export function captionFontSize(
  fontSize: number,
  fw: number,
  fh: number,
  scale = 1,
): number {
  return fontSize * (Math.min(fw, fh) / 1080) * scale;
}
