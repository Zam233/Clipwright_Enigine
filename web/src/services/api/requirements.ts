import { getApiClient } from './client';
import { useSettingsStore } from '@/stores/settingsStore';
import type { RequirementsInitRequest, RequirementsChatRequest } from '@/types/api';

export const requirementsApi = {
  /** Initialize a requirements session */
  async init(request: RequirementsInitRequest) {
    const { data } = await getApiClient().post('/api/requirements/init', request);
    return data as { session_id: string; status: string };
  },

  /** Send a chat message */
  async chat(request: RequirementsChatRequest) {
    // 规划书生成需要结构 Agent + 翻译，耗时可达 2-5 分钟，
    // 覆盖 axios 默认 60s 超时。
    const { data } = await getApiClient().post('/api/requirements/chat', request, { timeout: 600_000 });
    return data;
  },

  /** Edit timeline by selected clips + natural-language instruction (C6/C7) */
  async edit(request: { session_id: string; message: string; timeline: unknown; selected_clip_ids: string[] }) {
    // 换素材/重做动画/数值调整可能调用 Agent，耗时可长，对齐 chat 的超时
    const { data } = await getApiClient().post('/api/requirements/edit', request, { timeout: 600_000 });
    return data;
  },

  /** Get SSE stream URL for chat */
  getChatStreamUrl(sessionId: string): string {
    const base = getApiClient().defaults.baseURL || 'http://localhost:8000';
    const token = useSettingsStore.getState().authToken;
    const q = token ? `?token=${encodeURIComponent(token)}` : '';
    return `${base}/api/requirements/chat/stream/${sessionId}${q}`;
  },

  /** Get session state */
  async getSession(sessionId: string) {
    const { data } = await getApiClient().get(`/api/requirements/session/${sessionId}`);
    return data;
  },

  /** Get production plan */
  async getPlan(sessionId: string) {
    const { data } = await getApiClient().get(`/api/requirements/plan/${sessionId}`);
    return data;
  },

  /** Upload reference file */
  async upload(sessionId: string, file: File) {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await getApiClient().post(
      `/api/requirements/upload/${sessionId}`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
    return data;
  },

  /** Proceed to pipeline */
  async proceed(sessionId: string, personaId: string, pluginId: string, extraParams?: Record<string, unknown>) {
    const { data } = await getApiClient().post('/api/requirements/proceed', {
      session_id: sessionId,
      persona_id: personaId,
      category_plugin_id: pluginId,
      extra_params: extraParams,
    }, { timeout: 300_000 });
    return data;
  },
};
