import { getApiClient } from './client';

/** Normalized result of a webhook test call. */
export interface WebhookTestResult {
  success: boolean;
  status_code?: number;
  body?: string;
}

/**
 * Normalize the backend /api/webhook/{id}/test response
 * `{status: 'sent'|'failed', response_code?, error?}` into the frontend shape
 * `{success, status_code, body}`.
 */
export function normalizeWebhookTest(raw: unknown): WebhookTestResult {
  const o = (raw ?? {}) as Record<string, unknown>;
  const success = o.status === 'sent';
  const result: WebhookTestResult = { success };
  if (typeof o.response_code === 'number') result.status_code = o.response_code;
  if (!success && typeof o.error === 'string') result.body = o.error;
  return result;
}

export interface WebhookSubscription {
  id: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: string;
  last_delivery_at?: string;
}

export const webhookApi = {
  async listEvents(): Promise<{ events: string[] }> {
    const { data } = await getApiClient().get('/api/webhook/events');
    return data;
  },

  async list(): Promise<WebhookSubscription[]> {
    const { data } = await getApiClient().get('/api/webhook/list');
    return data;
  },

  async register(sub: { url: string; events: string[]; secret?: string }): Promise<WebhookSubscription> {
    const { data } = await getApiClient().post('/api/webhook/register', sub);
    return data;
  },

  async remove(id: string): Promise<void> {
    await getApiClient().delete(`/api/webhook/${id}`);
  },

  async toggle(id: string): Promise<WebhookSubscription> {
    const { data } = await getApiClient().put(`/api/webhook/${id}/toggle`);
    return data;
  },

  async test(id: string): Promise<WebhookTestResult> {
    const { data } = await getApiClient().post(`/api/webhook/${id}/test`);
    return normalizeWebhookTest(data);
  },

  async notify(event: string, payload?: Record<string, unknown>): Promise<{ delivered: number; failed: number }> {
    const { data } = await getApiClient().post('/api/webhook/notify', { event, payload: payload ?? {} });
    return data;
  },

  async deliveries(limit?: number): Promise<Record<string, unknown>[]> {
    const { data } = await getApiClient().get('/api/webhook/deliveries', {
      params: limit ? { limit } : {},
    });
    return data;
  },
};
