import axios from 'axios';
import { getApiClient } from './client';
import { session } from './session';

/** 市场条目（与 Server market 集合字段对齐）。 */
export interface MarketItem {
  package_id: string;
  name: string;
  description: string;
  tags: string[];
  version: string;
  download_count: number;
  license?: string;
  author?: string;
  rating?: { count: number; avg: number; comments?: string[] };
}

export type MarketKind = 'plugin' | 'persona';

const _srv = axios.create({ baseURL: '/srv', withCredentials: true, timeout: 30_000 });

function _authHeaders(): Record<string, string> {
  const token = session.token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export const marketApi = {
  /** 搜索插件（主项目 /api/market 代理 Server） */
  async plugins(q = '', tag = '', page = 1) {
    const { data } = await getApiClient().get('/api/market/plugins', { params: { q, tag, page } });
    return data as { items: MarketItem[]; total: number; page: number };
  },

  async personas(q = '', tag = '', page = 1) {
    const { data } = await getApiClient().get('/api/market/personas', { params: { q, tag, page } });
    return data as { items: MarketItem[]; total: number; page: number };
  },

  async pluginDetail(packageId: string) {
    const { data } = await getApiClient().get(`/api/market/plugins/${packageId}`);
    return data as MarketItem;
  },

  async personaDetail(packageId: string) {
    const { data } = await getApiClient().get(`/api/market/personas/${packageId}`);
    return data as MarketItem;
  },

  /** 一键安装（下载→校验→解包→注册，后端完成） */
  async installPlugin(packageId: string, version = '') {
    const { data } = await getApiClient().post(
      `/api/market/plugins/${packageId}/install${version ? `?version=${encodeURIComponent(version)}` : ''}`,
    );
    return data as { status: string; plugin_id: string };
  },

  async installPersona(packageId: string, version = '') {
    const { data } = await getApiClient().post(
      `/api/market/personas/${packageId}/install${version ? `?version=${encodeURIComponent(version)}` : ''}`,
    );
    return data as { status: string; persona_id: string };
  },

  /** 评分（直连 Server，需账号登录） */
  async rate(kind: MarketKind, packageId: string, score: number, comment = '') {
    await _srv.post(`/api/market/${kind}s/${packageId}/rating`, { score, comment }, { headers: _authHeaders() });
  },

  /** 发布（直连 Server，需账号登录；multipart 包上传） */
  async publish(
    kind: MarketKind,
    meta: { package_id: string; version: string; name: string; description: string; tags: string; license: string },
    file: File,
  ) {
    const fd = new FormData();
    fd.append('package_id', meta.package_id);
    fd.append('version', meta.version);
    fd.append('name', meta.name);
    fd.append('description', meta.description);
    fd.append('tags', meta.tags);
    fd.append('license', meta.license);
    fd.append('file', file);
    await _srv.post(`/api/market/${kind}s`, fd, { headers: _authHeaders() });
  },
};
