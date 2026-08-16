import { getApiClient } from './client';

// ── Types (aligned with backend clipwright/api/video_editor.py) ──

export interface EditorProject {
  project_id: string;
  name: string;
  timeline: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  version: number;
}

/** Lightweight project row returned by GET /projects */
export interface EditorProjectSummary {
  project_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface SaveEditorProjectRequest {
  name?: string;
  timeline: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface ClipOperation {
  track_index: number;
  clip_index?: number;
  clip_data?: Record<string, unknown>;
  position_sec?: number;
}

export interface SplitClipRequest {
  track_index: number;
  clip_index: number;
  split_at_sec: number;
}

export interface EditorExportRequest {
  format?: 'json' | 'edl' | 'fcpxml';
  fps?: number;
}

export interface UndoRedoResponse {
  status: string;
  timeline?: Record<string, unknown>;
  remaining_undo?: number;
  remaining_redo?: number;
  message?: string;
}

export const videoEditorApi = {
  /** 编辑器服务状态 */
  async status(): Promise<{ status: string; projects: number; storage_dir: string }> {
    const { data } = await getApiClient().get('/api/video-editor/status');
    return data;
  },

  /** 列出所有编辑器项目（概要） */
  async listProjects(): Promise<EditorProjectSummary[]> {
    const { data } = await getApiClient().get('/api/video-editor/projects');
    return data;
  },

  /** 创建编辑器项目 */
  async createProject(req: SaveEditorProjectRequest): Promise<EditorProject> {
    const { data } = await getApiClient().post('/api/video-editor/projects/create', req);
    return data;
  },

  /** 加载编辑器项目 */
  async getProject(projectId: string): Promise<EditorProject> {
    const { data } = await getApiClient().get(`/api/video-editor/projects/${projectId}`);
    return data;
  },

  /** 保存编辑器项目（手动保存） */
  async saveProject(projectId: string, req: SaveEditorProjectRequest): Promise<EditorProject> {
    const { data } = await getApiClient().put(`/api/video-editor/projects/${projectId}`, req);
    return data;
  },

  /** 删除编辑器项目 */
  async deleteProject(projectId: string): Promise<{ status: string; project_id: string }> {
    const { data } = await getApiClient().delete(`/api/video-editor/projects/${projectId}`);
    return data;
  },

  /** 撤销上一步操作 */
  async undo(projectId: string): Promise<UndoRedoResponse> {
    const { data } = await getApiClient().post(`/api/video-editor/projects/${projectId}/undo`);
    return data;
  },

  /** 重做上一步撤销的操作 */
  async redo(projectId: string): Promise<UndoRedoResponse> {
    const { data } = await getApiClient().post(`/api/video-editor/projects/${projectId}/redo`);
    return data;
  },

  /** 向指定轨道添加片段 */
  async addClip(
    projectId: string,
    op: ClipOperation,
  ): Promise<{ status: string; action: string; track_index: number }> {
    const { data } = await getApiClient().post(`/api/video-editor/projects/${projectId}/clips/add`, op);
    return data;
  },

  /** 从指定轨道删除片段 */
  async removeClip(
    projectId: string,
    op: ClipOperation,
  ): Promise<{ status: string; action: string; removed_clip?: Record<string, unknown> }> {
    const { data } = await getApiClient().post(`/api/video-editor/projects/${projectId}/clips/remove`, op);
    return data;
  },

  /** 移动片段到新的时间位置 */
  async moveClip(
    projectId: string,
    op: ClipOperation,
  ): Promise<{ status: string; action: string; new_position_sec?: number }> {
    const { data } = await getApiClient().post(`/api/video-editor/projects/${projectId}/clips/move`, op);
    return data;
  },

  /** 在指定时间点分割片段为两段 */
  async splitClip(
    projectId: string,
    req: SplitClipRequest,
  ): Promise<{ status: string; action: string; left?: Record<string, unknown>; right?: Record<string, unknown> }> {
    const { data } = await getApiClient().post(`/api/video-editor/projects/${projectId}/clips/split`, req);
    return data;
  },

  /** 导出项目时间线（json / edl / fcpxml） */
  async export(
    projectId: string,
    req: EditorExportRequest = {},
  ): Promise<{ format: string; timeline?: Record<string, unknown>; content?: string }> {
    const { data } = await getApiClient().post(`/api/video-editor/projects/${projectId}/export`, {
      format: req.format ?? 'json',
      fps: req.fps ?? 30,
    });
    return data;
  },
};
