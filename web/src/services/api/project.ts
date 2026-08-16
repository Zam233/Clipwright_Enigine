import { getApiClient } from './client';
import { apiBase } from './sse';
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
  async list(folder?: string, tag?: string, trash = false) {
    const params: Record<string, string> = {};
    if (folder) params.folder = folder;
    if (tag) params.tag = tag;
    if (trash) params.trash = '1'; // A2: 回收站视图
    const { data } = await getApiClient().get<ProjectSummary[]>('/api/project', { params });
    return data;
  },

  /** A2: 移入回收站（软删除，可恢复） */
  async trashProject(projectId: string) {
    const { data } = await getApiClient().post(`/api/project/${projectId}/trash`);
    return data;
  },

  /** A2: 从回收站恢复 */
  async restoreProject(projectId: string) {
    const { data } = await getApiClient().post(`/api/project/${projectId}/restore`);
    return data;
  },

  /** A2: 从回收站永久删除 */
  async purgeProject(projectId: string) {
    const { data } = await getApiClient().delete(`/api/project/${projectId}/trash`);
    return data;
  },

  /** P8: 项目归档 zip 下载（project.json + 时间线引用的本地媒体） */
  async archive(projectId: string, projectName = 'project') {
    const resp = await getApiClient().get(`/api/project/${projectId}/archive`, {
      responseType: 'blob',
    });
    const url = URL.createObjectURL(resp.data as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${projectName || projectId}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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
    const v = encodeURIComponent(version || String(Date.now()));
    return `${apiBase()}/api/project/${projectId}/thumbnail?v=${v}`;
  },

  /** Refresh (force-regenerate) thumbnail */
  refreshThumbnailUrl(projectId: string): string {
    return `${apiBase()}/api/project/${projectId}/thumbnail?force=1&v=${Date.now()}`;
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

/** G1: 项目时间线版本管理（版本历史 UI 接线后端 VersionManager API）。 */
export interface TimelineVersionEntry {
  version_id: string;
  time: string;
  label: string;
  position: number;
  is_current: boolean;
}

export const versionApi = {
  /** 列出项目全部版本快照 */
  async list(projectId: string) {
    const { data } = await getApiClient().get<TimelineVersionEntry[]>(`/api/project/${projectId}/versions`);
    return data;
  },

  /** 把当前时间线存为版本快照 */
  async snapshot(projectId: string, label = '') {
    const { data } = await getApiClient().post<{ version_id: string; count: number }>(
      `/api/project/${projectId}/versions`, { label });
    return data;
  },

  /** 恢复指定版本（写回项目 timeline） */
  async restore(projectId: string, position: number) {
    const { data } = await getApiClient().post<{ version_id: string; timeline: import('@/types/timeline').Timeline }>(
      `/api/project/${projectId}/versions/${position}/restore`);
    return data;
  },

  /** 清空全部版本快照 */
  async clear(projectId: string) {
    const { data } = await getApiClient().delete<{ deleted: boolean }>(`/api/project/${projectId}/versions`);
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

  /** M8: 启用插件（持久化 + 加载） */
  async enable(pluginId: string) {
    const { data } = await getApiClient().post(`/api/plugin/${pluginId}/enable`);
    return data;
  },

  /** M8: 禁用插件（持久化 + 卸载） */
  async disable(pluginId: string) {
    const { data } = await getApiClient().post(`/api/plugin/${pluginId}/disable`);
    return data;
  },

  /** M1: 已知权限白名单 */
  async permissions() {
    const { data } = await getApiClient().get('/api/plugin/permissions');
    return data as { allowed: string[] };
  },

  /** M7: 插件错误通道 */
  async errors(limit = 50) {
    const { data } = await getApiClient().get(`/api/plugin/errors?limit=${limit}`);
    return data as Array<{ plugin_id: string; phase: string; message: string; details: string; ts: number }>;
  },

  async clearErrors(pluginId?: string) {
    const { data } = await getApiClient().delete(`/api/plugin/errors${pluginId ? `?plugin_id=${pluginId}` : ''}`);
    return data as { status: string; removed: number };
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
