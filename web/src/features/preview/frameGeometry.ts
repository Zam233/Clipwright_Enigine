/**
 * 预览帧几何计算
 * Preview frame geometry helpers.
 *
 * 这些工具封装了预览画布中「视频帧矩形」的布局计算，以及把面板内的
 * 鼠标 X 坐标映射为时间（用于拖拽刷洗 / drag-scrubbing）。
 * These utilities encapsulate the layout math of the video frame rectangle
 * inside the preview canvas, plus mapping a panel X coordinate to time
 * (used for drag-scrubbing).
 *
 * 数值必须与 PreviewPanel.tsx draw() 中原有的内联计算保持完全一致。
 * The numbers MUST stay numerically identical to the original inline
 * computation in PreviewPanel.tsx draw().
 */

/** 帧与面板边缘的间距（像素），先于 zoom 缩放应用。Margin between the frame and the panel edges (px), applied BEFORE zoom. */
export const PREVIEW_MARGIN = 32;

/** 视频帧矩形（面板坐标）。Video frame rectangle (panel coordinates). */
export interface FrameRect {
  fx: number;
  fy: number;
  fw: number;
  fh: number;
}

/**
 * 计算视频帧矩形：等比缩放（fit）+ 居中，缺省 16:9，支持缩放级别。
 * Compute the video frame rectangle: aspect-fit + centered, 16:9 fallback, zoom-aware.
 *
 * @param panelW 面板宽度 / panel width in px
 * @param panelH 面板高度 / panel height in px
 * @param tlW    时间线视频宽 / timeline video width
 * @param tlH    时间线视频高 / timeline video height
 * @param zoom   预览缩放级别 / preview zoom level
 */
export function computePreviewFrameRect(
  panelW: number,
  panelH: number,
  tlW: number,
  tlH: number,
  zoom: number,
): FrameRect {
  // 宽高比：时间线尺寸无效时回退到 16:9（避免除零）。
  // Aspect ratio: fall back to 16:9 when timeline size is invalid (avoid div-by-zero).
  const aspect = tlH > 0 ? tlW / tlH : 16 / 9;
  let fw = (panelW - PREVIEW_MARGIN) * zoom;
  let fh = fw / aspect;
  if (fh > (panelH - PREVIEW_MARGIN) * zoom) {
    // 高度先触顶：以高度为基准重新计算宽度。
    // Height caps first: recompute width from the capped height.
    fh = (panelH - PREVIEW_MARGIN) * zoom;
    fw = fh * aspect;
  }
  // 水平 / 垂直居中。
  // Center horizontally / vertically.
  const fx = (panelW - fw) / 2;
  const fy = (panelH - fh) / 2;
  return { fx, fy, fw, fh };
}

/**
 * 将面板内的 X 坐标映射为时间（秒），越界时钳制到 [0, durationSec]。
 * Map a panel X coordinate to a time (seconds), clamped to [0, durationSec].
 *
 * @param panelX      面板内的鼠标 X / pointer X inside the panel
 * @param rect        视频帧矩形 / the video frame rectangle
 * @param durationSec 时间线总时长（秒）/ total timeline duration (seconds)
 */
export function xToScrubTime(panelX: number, rect: FrameRect, durationSec: number): number {
  if (durationSec <= 0) return 0;
  if (panelX <= rect.fx) return 0;
  if (panelX >= rect.fx + rect.fw) return durationSec;
  return ((panelX - rect.fx) / rect.fw) * durationSec;
}
