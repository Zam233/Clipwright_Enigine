import { getApiClient } from './client';
import { apiBase, fetchSseToken } from './sse';
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

  /**
   * W1: 流式消费需求对话（SSE）— 边收边回调 chunk（type/status/result），
   * 返回最终 result payload。长耗时对话不再受 axios 超时影响，且能实时显示「思考中」。
   */
  async streamChat(
    sessionId: string,
    message: string,
    onChunk?: (chunk: { type: string; data: unknown }) => void,
  ): Promise<Record<string, unknown>> {
    const base = apiBase();
    const form = new URLSearchParams();
    form.append('message', message);
    const token = await fetchSseToken();
    const url = `${base}/api/requirements/chat/stream/${sessionId}`;
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: form.toString(),
    });
    if (!resp.ok || !resp.body) {
      throw new Error(`chat stream failed: ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result: Record<string, unknown> = {};
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 以空行分隔事件
      const parts = buffer.split('\n\n');
      buffer = parts.pop() ?? '';
      for (const part of parts) {
        const line = part.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') continue;
        try {
          const chunk = JSON.parse(payload) as { type: string; data: unknown };
          onChunk?.(chunk);
          if (chunk.type === 'result') result = (chunk.data as Record<string, unknown>) ?? result;
        } catch { /* 忽略坏块 */ }
      }
    }
    return result;
  },

  /** Edit timeline by selected clips + natural-language instruction (C6/C7; W12 支持区域) */
  async edit(request: {
    session_id: string;
    message: string;
    timeline: unknown;
    selected_clip_ids: string[];
    region_start_sec?: number;
    region_end_sec?: number;
  }) {
    // 换素材/重做动画/数值调整可能调用 Agent，耗时可长，对齐 chat 的超时
    const { data } = await getApiClient().post('/api/requirements/edit', request, { timeout: 600_000 });
    return data;
  },

  /** Get SSE stream URL for chat（token 由调用方用 withSseToken 附加） */
  getChatStreamUrl(sessionId: string): string {
    return `${apiBase()}/api/requirements/chat/stream/${sessionId}`;
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
