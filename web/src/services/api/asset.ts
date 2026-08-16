import { getApiClient } from './client';
import { apiBase } from './sse';
import type {
  Asset,
  AssetUploadResponse,
  MaterialSearchRequest,
  MaterialSearchResult,
} from '@/types/api';

/** Material asset shape returned by GET /api/material/asset/{source_id}/{asset_id} */
export interface MaterialAsset {
  id: string;
  title: string;
  type: 'video' | 'audio' | 'image' | 'text';
  url?: string;
  local_path?: string;
  thumbnail_url?: string;
  tags: string[];
  duration_sec?: number;
  file_size_bytes?: number;
  resolution?: string;
  source: string;
  metadata: Record<string, unknown>;
  created_at?: string;
}

/** Transform backend asset shape (asset_id/media_type/file_path) to frontend Asset type */
function mapAsset(raw: Record<string, unknown>): Asset {
  const assetId = (raw.asset_id as string) || (raw.id as string) || '';
  const thumbPath = (raw.thumbnail_path as string) || '';
  // Convert filesystem thumbnail path to API URL
  const thumbnailUrl = thumbPath
    ? `/api/asset/${assetId}/thumbnail${raw.project_id ? `?project_id=${raw.project_id}` : ''}`
    : ((raw.thumbnail_url as string) || undefined);
  return {
    id: assetId,
    filename: (raw.filename as string) || '',
    path: (raw.file_path as string) || (raw.path as string) || '',
    kind: (raw.media_type as Asset['kind']) || (raw.kind as Asset['kind']) || 'image',
    duration_sec: raw.duration_sec as number | undefined,
    width: raw.width as number | undefined,
    height: raw.height as number | undefined,
    thumbnail_url: thumbnailUrl,
    tags: (Array.isArray(raw.tags) ? raw.tags : []) as string[],
    created_at: (raw.created_at as string) || new Date().toISOString(),
  };
}

export const assetApi = {
  /** List all assets for a project */
  async list(projectId?: string): Promise<Asset[]> {
    const params: Record<string, string> = {};
    if (projectId) params.project_id = projectId;
    const { data } = await getApiClient().get('/api/asset/list', { params });
    if (!Array.isArray(data)) return [];
    return data.map(mapAsset);
  },

  /** Upload a single asset to a project */
  async upload(file: File, onProgress?: (pct: number) => void, projectId?: string) {
    const formData = new FormData();
    formData.append('file', file);
    const params: Record<string, string> = {};
    if (projectId) params.project_id = projectId;
    const { data } = await getApiClient().post<AssetUploadResponse>(
      '/api/asset/upload',
      formData,
      {
        headers: { 'Content-Type': 'multipart/form-data' },
        params,
        onUploadProgress: (e) => {
          if (e.total && onProgress) {
            onProgress(Math.round((e.loaded / e.total) * 100));
          }
        },
      },
    );
    return data;
  },

  /** Import a file by path (symlink, no upload) */
  async importPath(path: string, projectId?: string) {
    const { data } = await getApiClient().post('/api/asset/import-path', {
      path,
      project_id: projectId || '',
    });
    return data;
  },

  /** Import a web resource by URL (download + symlink to asset library) */
  async importUrl(url: string, filename: string, projectId?: string) {
    const { data } = await getApiClient().post('/api/asset/import-url', {
      url,
      filename,
      project_id: projectId || '',
    });
    return data;
  },

  /** M9: 从素材库移除（仅移除条目与软链接，保留原始文件） */
  async remove(assetId: string, projectId?: string) {
    const { data } = await getApiClient().delete(`/api/asset/${assetId}`, {
      params: { project_id: projectId || '' },
    });
    return data;
  },

  /** W14: 获取单个素材详情 */
  async get(assetId: string, projectId?: string): Promise<Asset> {
    const params: Record<string, string> = {};
    if (projectId) params.project_id = projectId;
    const { data } = await getApiClient().get(`/api/asset/${assetId}`, { params });
    return mapAsset(data as Record<string, unknown>);
  },

  /** W14: 素材文件 URL（列表/预览用；thumbnail 同理走 /{id}/thumbnail） */
  fileUrl(assetId: string, projectId?: string): string {
    const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return `${apiBase()}/api/asset/${assetId}/file${q}`;
  },

  /** W14: by-path 白名单媒体代理 URL（resolveMediaUrl 内部已使用，客户端补全封装） */
  byPathUrl(path: string): string {
    return `${apiBase()}/api/asset/by-path?path=${encodeURIComponent(path)}`;
  },

  /** Search materials (semantic) */
  async searchMaterials(request: MaterialSearchRequest): Promise<MaterialSearchResult[]> {
    const params: Record<string, string> = { query: request.query };
    if (request.limit) params.top_k = String(request.limit);
    if (request.source) params.sources = Array.isArray(request.source) ? request.source.join(',') : request.source;
    const { data } = await getApiClient().post('/api/material/search', null, { params });
    if (!Array.isArray(data)) return [];
    // Backend nests fields under `asset`; flatten to the frontend shape
    return data.map((r: Record<string, unknown>) => {
      const a = (r.asset as Record<string, unknown>) || {};
      return {
        id: (a.id as string) || (r.id as string) || '',
        title: (a.title as string) || (r.title as string) || '',
        url: (a.url as string) || (a.local_path as string) || (r.url as string) || '',
        thumbnail: (a.thumbnail_url as string) || (r.thumbnail as string) || undefined,
        duration_sec: (a.duration_sec as number) ?? (r.duration_sec as number) ?? undefined,
        score: (r.score as number) ?? 0,
        source: (r.source_name as string) || (a.source as string) || (r.source as string) || '',
        reason: (r.reason as string) || undefined,
      } as MaterialSearchResult;
    });
  },

  /** List material sources */
  async listSources() {
    const { data } = await getApiClient().get('/api/material/sources');
    return data as { id: string; name: string }[];
  },

  /** Get a single material asset detail by source + asset id */
  async getMaterialAsset(sourceId: string, assetId: string): Promise<MaterialAsset> {
    const { data } = await getApiClient().get(`/api/material/asset/${sourceId}/${assetId}`);
    return data;
  },
};
