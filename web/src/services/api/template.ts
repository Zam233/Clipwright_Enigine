import { getApiClient } from './client';

export interface Template {
  id: string;
  name: string;
  description: string;
  tags: string[];
  track_count: number;
  duration_sec: number;
  created_at: string;
  updated_at?: string;
}

export interface ApplyTemplateResult {
  status: string;
  template_id: string;
  timeline: Record<string, unknown>;
}

export const templateApi = {
  async list(): Promise<Template[]> {
    const { data } = await getApiClient().get('/api/template/list');
    return data;
  },

  async get(id: string): Promise<Template & { timeline?: Record<string, unknown> }> {
    const { data } = await getApiClient().get(`/api/template/${id}`);
    return data;
  },

  async create(template: Partial<Template> & { name: string }): Promise<Template> {
    const { data } = await getApiClient().post('/api/template/create', template);
    return data;
  },

  async update(id: string, template: Partial<Template>): Promise<Template> {
    const { data } = await getApiClient().put(`/api/template/${id}`, template);
    return data;
  },

  async remove(id: string): Promise<void> {
    await getApiClient().delete(`/api/template/${id}`);
  },

  async apply(id: string): Promise<ApplyTemplateResult> {
    const { data } = await getApiClient().post<ApplyTemplateResult>(`/api/template/${id}/apply`);
    return data;
  },
};
