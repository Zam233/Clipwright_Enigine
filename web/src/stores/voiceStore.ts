import { create } from 'zustand';
import { voiceApi } from '@/services/api/voice';
import type { VoiceRecord } from '@/types/voice';

type CloneStep = 'idle' | 'uploading' | 'cloning' | 'done' | 'error';

interface VoiceState {
  voices: VoiceRecord[];
  loading: boolean;
  error: string | null;
  cloneStep: CloneStep;
  previewUrl: string | null;
  previewLoading: boolean;
  /** 合成请求序号，用于丢弃过期响应 */
  previewSeq: number;

  fetchVoices: () => Promise<void>;
  cloneVoice: (file: File, name: string, provider?: string) => Promise<boolean>;
  deleteVoice: (id: string) => Promise<void>;
  synthesizePreview: (voiceId: string, text: string) => Promise<void>;
  clearPreview: () => void;
  clearError: () => void;
  resetVoices: () => void;
}

export const useVoiceStore = create<VoiceState>((set, get) => ({
  voices: [],
  loading: false,
  error: null,
  cloneStep: 'idle',
  previewUrl: null,
  previewLoading: false,
  previewSeq: 0,

  fetchVoices: async () => {
    set({ loading: true, error: null });
    try {
      const voices = await voiceApi.list();
      set({ voices, loading: false });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '加载音色列表失败';
      set({ error: msg, loading: false });
    }
  },

  cloneVoice: async (file, name, provider = 'qwen_tts') => {
    set({ cloneStep: 'uploading', error: null });
    try {
      const uploaded = await voiceApi.upload(file);
      set({ cloneStep: 'cloning' });
      await voiceApi.clone({
        voice_name: name,
        data_uri: uploaded.data_uri,
        provider,
      });
      set({ cloneStep: 'done' });
      await get().fetchVoices();
      return true;
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (e instanceof Error ? e.message : '克隆失败');
      set({ cloneStep: 'error', error: detail });
      return false;
    }
  },

  deleteVoice: async (id) => {
    try {
      await voiceApi.remove(id);
      set((s) => ({ voices: s.voices.filter((v) => v.id !== id) }));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '删除失败';
      set({ error: msg });
    }
  },

  synthesizePreview: async (voiceId, text) => {
    const seq = ++get().previewSeq;
    set({ previewLoading: true, previewUrl: null, error: null });
    try {
      const res = await voiceApi.synthesize({ voice_id: voiceId, text });
      if (seq !== get().previewSeq) return; // 过期响应（用户已切换音色）不覆盖新状态
      set({ previewUrl: voiceApi.getAudioUrl(res.audio_url), previewLoading: false });
    } catch (e: unknown) {
      if (seq !== get().previewSeq) return;
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (e instanceof Error ? e.message : '合成失败');
      set({ error: detail, previewLoading: false });
    }
  },

  clearPreview: () => set({ previewUrl: null }),
  clearError: () => set({ error: null }),
  resetVoices: () => set({ voices: [], loading: false, error: null, cloneStep: 'idle', previewUrl: null, previewLoading: false }),
}));
