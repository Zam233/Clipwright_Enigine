import { getApiClient } from './client';

export interface FontInfo {
  name: string;
  path: string;
  family: string;
  style: string;
}

export const fontApi = {
  async list(): Promise<{ fonts: FontInfo[]; count: number }> {
    const { data } = await getApiClient().get('/api/fonts/list');
    return data;
  },

  async getDefault(): Promise<{ path: string; fontspec: string; available: boolean }> {
    const { data } = await getApiClient().get('/api/fonts/default');
    return data;
  },

  async resolve(name: string): Promise<{ name: string; path: string; fontspec: string; found: boolean }> {
    const { data } = await getApiClient().get('/api/fonts/resolve', { params: { name } });
    return data;
  },

  async clearCache(): Promise<{ status: string }> {
    const { data } = await getApiClient().post('/api/fonts/clear-cache');
    return data;
  },
};
