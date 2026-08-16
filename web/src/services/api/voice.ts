import { getApiClient } from './client';
import type {
  VoiceRecord,
  VoiceUploadResponse,
  VoiceCloneRequest,
  VoiceSynthesizeRequest,
  VoiceSynthesizeResponse,
  VoiceDubRequest,
  VoiceDubResponse,
} from '@/types/voice';

export const voiceApi = {
  async upload(file: File): Promise<VoiceUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await getApiClient().post('/api/voice/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120_000,
    });
    return data;
  },

  async clone(req: VoiceCloneRequest): Promise<VoiceRecord> {
    const { data } = await getApiClient().post('/api/voice/clone', req, {
      timeout: 120_000,
    });
    return data;
  },

  async list(): Promise<VoiceRecord[]> {
    const { data } = await getApiClient().get('/api/voice/list');
    return data;
  },

  async remove(dbId: string): Promise<void> {
    await getApiClient().delete(`/api/voice/${dbId}`);
  },

  async synthesize(req: VoiceSynthesizeRequest): Promise<VoiceSynthesizeResponse> {
    const { data } = await getApiClient().post('/api/voice/synthesize', req, {
      timeout: 120_000,
    });
    return data;
  },

  async dub(req: VoiceDubRequest): Promise<VoiceDubResponse> {
    const { data } = await getApiClient().post('/api/voice/dub', req, {
      timeout: 300_000,
    });
    return data;
  },

  getAudioUrl(path: string): string {
    // Absolute URLs (e.g. CDN links) are returned as-is
    if (/^https?:\/\//i.test(path)) return path;
    const base = getApiClient().defaults.baseURL || 'http://localhost:8000';
    return `${base}${path}`;
  },
};
