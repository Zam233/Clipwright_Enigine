import { getApiClient } from './client';

export const edlApi = {
  async importEDL(content: string): Promise<{ clips: Record<string, unknown>[]; count: number }> {
    const { data } = await getApiClient().post('/api/edl/import/edl', content,
      { headers: { 'Content-Type': 'text/plain' } });
    return data;
  },

  async importFCPXML(content: string): Promise<{ clips: Record<string, unknown>[]; count: number }> {
    const { data } = await getApiClient().post('/api/edl/import/fcpxml', content,
      { headers: { 'Content-Type': 'text/plain' } });
    return data;
  },

  async exportEDL(clips: Record<string, unknown>[], fps?: number): Promise<{ edl: string; format: string }> {
    const { data } = await getApiClient().post('/api/edl/export/edl', { clips, fps: fps ?? 30 });
    return data;
  },

  async exportFCPXML(clips: Record<string, unknown>[], timeline?: Record<string, unknown>): Promise<{ fcpxml: string; format: string }> {
    const { data } = await getApiClient().post('/api/edl/export/fcpxml', { clips, timeline: timeline ?? {} });
    return data;
  },
};
