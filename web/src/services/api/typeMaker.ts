import { getApiClient } from './client';

export interface ShotParams {
  min_shot_sec: number;
  max_shot_sec: number;
  transition_type: string;
  transition_duration_sec: number;
  cut_on_beat: boolean;
}

export interface PersonaMappingItem {
  source: string;
  transform: string;
  scale_factor: number;
}

export interface TypeDefinition {
  id: string;
  name: string;
  description: string;
  shot_params: ShotParams;
  persona_mapping: Record<string, PersonaMappingItem>;
  post_process?: Record<string, unknown>;
  tags?: string[];
}

export interface TypeListItem {
  id: string;
  name: string;
  description: string;
  builtin: boolean;
  tags: string[];
}

export interface TypePreviewResult {
  valid: boolean;
  errors: string[];
  shot_params: ShotParams;
  sample_translation: Record<string, unknown>;
}

export interface TypeMutationResult {
  status: string;
  id: string;
  file?: string;
}

export const typeMakerApi = {
  async list(): Promise<TypeListItem[]> {
    const { data } = await getApiClient().get<TypeListItem[]>('/api/type-maker/list');
    return data;
  },

  async get(id: string): Promise<TypeDefinition> {
    const { data } = await getApiClient().get(`/api/type-maker/${id}`);
    return data as TypeDefinition;
  },

  async create(definition: Partial<TypeDefinition> & { name: string }): Promise<TypeMutationResult> {
    const { data } = await getApiClient().post<TypeMutationResult>('/api/type-maker/create', definition);
    return data;
  },

  async update(id: string, definition: Partial<TypeDefinition>): Promise<TypeMutationResult> {
    const { data } = await getApiClient().put<TypeMutationResult>(`/api/type-maker/${id}`, definition);
    return data;
  },

  async remove(id: string): Promise<void> {
    await getApiClient().delete(`/api/type-maker/${id}`);
  },

  async preview(definition: TypeDefinition): Promise<TypePreviewResult> {
    const { data } = await getApiClient().post<TypePreviewResult>('/api/type-maker/preview', definition);
    return data;
  },
};
