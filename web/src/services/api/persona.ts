import { getApiClient } from './client';
import type { Persona } from '@/types/persona';

export const personaApi = {
  /** List all persona IDs */
  async listIds() {
    const { data } = await getApiClient().get<string[]>('/api/persona/list');
    return data;
  },

  /** List all personas with full details (batch fetch) */
  async list(): Promise<Persona[]> {
    const ids = await personaApi.listIds();
    if (ids.length === 0) return [];
    const results = await Promise.allSettled(ids.map((id) => personaApi.get(id)));
    return results
      .filter((r): r is PromiseFulfilledResult<Persona> => r.status === 'fulfilled')
      .map((r) => r.value);
  },

  /** Get a single persona */
  async get(personaId: string) {
    const { data } = await getApiClient().get(`/api/persona/${personaId}`);
    return data as Persona;
  },

  /** Create a new persona */
  async create(persona: Partial<Persona>) {
    const { data } = await getApiClient().post('/api/persona/create', persona);
    return data;
  },

  /** Update a persona */
  async update(personaId: string, updates: Partial<Persona>) {
    const { data } = await getApiClient().put(`/api/persona/${personaId}`, updates);
    return data;
  },

  /** Delete a persona */
  async remove(personaId: string) {
    const { data } = await getApiClient().delete(`/api/persona/${personaId}`);
    return data;
  },

  // ── Knowledge / RAG ──

  /** Upload knowledge document for a persona (auto-indexed by backend) */
  async addKnowledge(personaId: string, doc: { title: string; content: string; source?: string }) {
    const { data } = await getApiClient().post(
      `/api/persona/${personaId}/knowledge`,
      { title: doc.title, content: doc.content, source: doc.source || 'upload' },
    );
    return data;
  },

  /** List knowledge documents for a persona */
  async getKnowledge(personaId: string) {
    const { data } = await getApiClient().get(`/api/persona/${personaId}/knowledge`);
    return data as Array<{ id: string; title?: string; content: string; source?: string; created_at?: string }>;
  },

  /** Run RAG query against a persona's knowledge base */
  async ragQuery(personaId: string, query: string) {
    const { data } = await getApiClient().post(
      `/api/persona/${personaId}/rag/query`,
      { query },
    );
    return data as { answer?: string; chunks?: Array<{ content: string; score: number }> };
  },

  /** Build/refresh a persona's vector index */
  async ragIndex(personaId: string, forceRebuild = false) {
    const { data } = await getApiClient().post(
      `/api/persona/${personaId}/rag/index`,
      { force_rebuild: forceRebuild },
    );
    return data as { status: string; persona_id: string; total_docs?: number; total_chunks?: number };
  },

  /** Query a persona's vector index status */
  async ragStatus(personaId: string) {
    const { data } = await getApiClient().get(`/api/persona/${personaId}/rag/status`);
    return data as { persona_id: string; has_index: boolean; knowledge_doc_count: number; indexed: boolean };
  },

  /** Delete a persona's vector index */
  async ragDelete(personaId: string) {
    const { data } = await getApiClient().delete(`/api/persona/${personaId}/rag/index`);
    return data as { status: string; persona_id: string };
  },

  // ── Prompt 管理 ──

  /** Get a persona's prompt instruction */
  async getPrompt(personaId: string) {
    const { data } = await getApiClient().get(`/api/persona/${personaId}/prompt`);
    return data as { persona_id: string; prompt: string };
  },

  /** Save/update a persona's prompt instruction */
  async updatePrompt(personaId: string, prompt: string) {
    const { data } = await getApiClient().put(`/api/persona/${personaId}/prompt`, { prompt });
    return data as { status: string; persona_id: string };
  },

  /** Get a persona's vision prompt instruction */
  async getVisionPrompt(personaId: string) {
    const { data } = await getApiClient().get(`/api/persona/${personaId}/vision-prompt`);
    return data as { persona_id: string; vision_prompt: string };
  },

  /** Save/update a persona's vision prompt instruction */
  async updateVisionPrompt(personaId: string, visionPrompt: string) {
    const { data } = await getApiClient().put(`/api/persona/${personaId}/vision-prompt`, { vision_prompt: visionPrompt });
    return data as { status: string; persona_id: string };
  },

  // ── Chat Forge ──

  /** Start a chat forge session */
  async chatForgeStart(personaId?: string) {
    const { data } = await getApiClient().post('/api/persona/forge/chat/start', {
      persona_id: personaId || '',
    });
    return data as { session_id: string; persona_draft?: unknown; progress?: Record<string, number>; reply?: string };
  },

  /** Send a chat forge message */
  async chatForgeMessage(sessionId: string, message: string) {
    const { data } = await getApiClient().post('/api/persona/forge/chat/message', {
      session_id: sessionId,
      message,
    });
    return data as { reply?: string; persona_draft?: unknown; progress?: Record<string, number>; missing_dimensions?: string[] };
  },

  /** Upload knowledge content for chat forge (sends text content, chunked) */
  async chatForgeKnowledge(sessionId: string, content: string, source?: string) {
    const { data } = await getApiClient().post(
      '/api/persona/forge/chat/knowledge',
      { session_id: sessionId, content, source: source || 'user_upload' },
    );
    return data as { reply?: string; persona_draft?: unknown; progress?: Record<string, number> };
  },

  /** Commit/finalize the persona from chat forge */
  async chatForgeCommit(sessionId: string, personaName: string) {
    const { data } = await getApiClient().post('/api/persona/forge/chat/commit', {
      session_id: sessionId,
      persona_name: personaName,
    });
    return data;
  },

  /** Get a chat forge session's current state */
  async chatForgeState(sessionId: string) {
    const { data } = await getApiClient().get(`/api/persona/forge/chat/state/${sessionId}`);
    return data as Record<string, unknown>;
  },

  // ── PersonaForge (from prompt) ──

  /** Generate persona from description */
  async forgeFromPrompt(description: string, personaId: string, personaName?: string) {
    const { data } = await getApiClient().post('/api/persona/forge/from-prompt', {
      description,
      persona_id: personaId,
      persona_name: personaName ?? '',
    });
    return data;
  },

  // ── PersonaForge (extended modes) ──

  /** Generate persona from a script/口播文本 (POST /from-script) */
  async forgeFromScript(script: string, personaId: string, personaName?: string, scriptFormat = 'txt') {
    const { data } = await getApiClient().post('/api/persona/forge/from-script', {
      script,
      persona_id: personaId,
      persona_name: personaName ?? '',
      script_format: scriptFormat,
    });
    return data;
  },

  /** Dialogue guidance: generate next guiding questions (POST /dialogue/generate-questions) */
  async forgeDialogueQuestions(personaId: string, existingAnswers?: Record<string, unknown>) {
    const { data } = await getApiClient().post('/api/persona/forge/dialogue/generate-questions', {
      persona_id: personaId,
      existing_answers: existingAnswers,
    });
    return data as Array<{ question?: string; category?: string; field?: string }>;
  },

  /** Dialogue guidance: build persona from Q&A answers (POST /dialogue/build) */
  async forgeDialogueBuild(personaId: string, answers: Record<string, unknown>, personaName?: string) {
    const { data } = await getApiClient().post('/api/persona/forge/dialogue/build', {
      persona_id: personaId,
      persona_name: personaName ?? '',
      answers,
    });
    return data;
  },
};
