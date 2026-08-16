import { getApiClient } from './client';
import type { Project, ProjectSummary, ProjectSaveRequest, HealthResponse, AnimationDef } from '@/types/api';

export const projectApi = {
  /** Create a new project (backend assigns id) */
  async create(request: ProjectSaveRequest) {
    const { data } = await getApiClient().post<Project>('/api/project', request);
    return data;
  },

  /** Save/update an existing project (PUT) */
  async save(projectId: string, request: ProjectSaveRequest) {
    const { data } = await getApiClient().put<Project>(`/api/project/${projectId}`, request);
    return data;
  },

  /** Load a project by id */
  async load(projectId: string) {
    const { data } = await getApiClient().get<Project>(`/api/project/${projectId}`);
    return data;
  },

  /** List all projects (returns summaries, not full timeline) */
  async list(folder?: string, tag?: string) {
    const params: Record<string, string> = {};
    if (folder) params.folder = folder;
    if (tag) params.tag = tag;
    const { data } = await getApiClient().get<ProjectSummary[]>('/api/project', { params });
    return data;
  },

  /** Delete a project */
  async remove(projectId: string) {
    const { data } = await getApiClient().delete(`/api/project/${projectId}`);
    return data;
  },

  /** Rename a project */
  async rename(projectId: string, name: string) {
    const { data } = await getApiClient().patch<Project>(`/api/project/${projectId}/rename`, { name });
    return data;
  },

  /** Set project folder */
  async setFolder(projectId: string, folder: string) {
    const { data } = await getApiClient().patch<Project>(`/api/project/${projectId}/folder`, { folder });
    return data;
  },

  /** Add a tag to a project */
  async addTag(projectId: string, tag: string) {
    const { data } = await getApiClient().post<Project>(`/api/project/${projectId}/tags`, { tag });
    return data;
  },

  /** Remove a tag from a project */
  async removeTag(projectId: string, tag: string) {
    const { data } = await getApiClient().delete<Project>(`/api/project/${projectId}/tags/${encodeURIComponent(tag)}`);
    return data;
  },

  /** Get thumbnail URL for a project */
  getThumbnailUrl(projectId: string, version?: string): string {
    const base = getApiClient().defaults.baseURL || 'http://localhost:8000';
    const v = encodeURIComponent(version || String(Date.now()));
    return `${base}/api/project/${projectId}/thumbnail?v=${v}`;
  },

  /** Refresh (force-regenerate) thumbnail */
  refreshThumbnailUrl(projectId: string): string {
    const base = getApiClient().defaults.baseURL || 'http://localhost:8000';
    return `${base}/api/project/${projectId}/thumbnail?force=1&v=${Date.now()}`;
  },

  /** Duplicate a project */
  async duplicate(projectId: string) {
    const { data } = await getApiClient().post<Project>(`/api/project/${projectId}/duplicate`);
    return data;
  },

  /** Rename a folder across all projects */
  async renameFolder(oldName: string, newName: string) {
    const { data } = await getApiClient().post<{ updated: number }>('/api/project/folders/rename', { old: oldName, new: newName });
    return data;
  },

  /** Delete a folder (unfile all its projects) */
  async deleteFolder(name: string) {
    const { data } = await getApiClient().post<{ updated: number }>('/api/project/folders/delete', { name });
    return data;
  },
};

export const healthApi = {
  async check() {
    const { data } = await getApiClient().get<HealthResponse>('/health');
    return data;
  },
};

export const pluginApi = {
  async discover() {
    const { data } = await getApiClient().get<string[]>('/api/plugin/discover');
    return data;
  },

  async list() {
    const { data } = await getApiClient().get('/api/plugin/list');
    return data as { manifest: { id: string; name: string; description?: string; version?: string; kind?: string }; enabled: boolean }[];
  },

  async loadAll() {
    const { data } = await getApiClient().post<string[]>('/api/plugin/load-all');
    return data;
  },

  async load(pluginId: string) {
    const { data } = await getApiClient().post(`/api/plugin/load/${pluginId}`);
    return data;
  },

  async unload(pluginId: string) {
    const { data } = await getApiClient().post(`/api/plugin/unload/${pluginId}`);
    return data;
  },

  async capabilities() {
    const { data } = await getApiClient().get('/api/plugin/capabilities');
    return data;
  },

  async getConfig(pluginId: string) {
    const { data } = await getApiClient().get(`/api/plugin/${pluginId}/config`);
    return data as { fields: Record<string, { type: string; value: unknown; label: string; description?: string }> };
  },

  async saveConfig(pluginId: string, yaml: string) {
    const { data } = await getApiClient().put(`/api/plugin/${pluginId}/config`, yaml, {
      headers: { 'Content-Type': 'text/plain' },
    });
    return data;
  },

  async deleteConfig(pluginId: string) {
    const { data } = await getApiClient().delete(`/api/plugin/${pluginId}/config`);
    return data;
  },

  /** 获取插件的 UI 布局定义 (ui.json) */
  async getUI(pluginId: string) {
    const { data } = await getApiClient().get(`/api/plugin/${pluginId}/ui`);
    return data as { title?: string; widgets: unknown[] } | null;
  },
};

export const animationApi = {
  /** 列出所有（或指定类型的）动画定义 */
  async list() {
    const { data } = await getApiClient().get<AnimationDef[]>('/api/animation/list');
    return data;
  },

  /** 列出所有屏幕动画 */
  async onscreen() {
    const { data } = await getApiClient().get<AnimationDef[]>('/api/animation/onscreen');
    return data;
  },

  /** 列出所有转场动画 */
  async transitions() {
    const { data } = await getApiClient().get<AnimationDef[]>('/api/animation/transitions');
    return data;
  },

  /** 获取单个动画定义的详细信息 */
  async get(animationId: string) {
    const { data } = await getApiClient().get<AnimationDef>(`/api/animation/get/${animationId}`);
    return data;
  },
};

export const skillApi = {
  async list() {
    const { data } = await getApiClient().get('/api/skill/list');
    return data;
  },

  async execute(skillName: string, params: Record<string, unknown>) {
    const { data } = await getApiClient().post('/api/skill/execute', {
      name: skillName,
      params,
    });
    return data;
  },
};
