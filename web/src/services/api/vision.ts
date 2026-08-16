import { getApiClient } from './client';

// ── Types (aligned with backend clipwright/api/vision.py) ──

export interface VisionAnalysis {
  description?: string;
  tags?: string[];
  labels?: string[];
  model?: string;
  width?: number;
  height?: number;
  [key: string]: unknown;
}

export interface VisionImportResponse {
  asset: Record<string, unknown>;
  analysis: VisionAnalysis;
  added_to: string;
}

export const visionApi = {
  /** 分析图片/视频内容，返回自动识别的标签和描述 */
  async analyze(imagePath: string): Promise<VisionAnalysis> {
    const { data } = await getApiClient().post('/api/vision/analyze', { image_path: imagePath });
    return data;
  },

  /** 识别图片内容 → 自动生成标签 → 导入素材库 */
  async importImage(
    imagePath: string,
    options?: { catalog_id?: string; media_type?: 'image' | 'video' | 'audio' | 'text' },
  ): Promise<VisionImportResponse> {
    const { data } = await getApiClient().post('/api/vision/import', {
      image_path: imagePath,
      catalog_id: options?.catalog_id ?? 'vision_catalog',
      media_type: options?.media_type ?? 'image',
    });
    return data;
  },
};
