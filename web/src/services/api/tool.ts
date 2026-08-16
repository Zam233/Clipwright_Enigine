import { getApiClient } from './client';

// ── Types (aligned with backend clipwright/api/tool.py) ──

export interface ToolInfo {
  name: string;
  description?: string;
  params_schema?: Record<string, unknown>;
  enabled?: boolean;
}

export interface ToolExecResult {
  name: string;
  status: string;
  output?: unknown;
  error?: string;
  duration_ms?: number;
}

export interface ToolCall {
  name: string;
  params?: Record<string, unknown>;
}

export const toolApi = {
  /** 列出所有已注册的原子能力工具及其可用状态 */
  async list(): Promise<ToolInfo[]> {
    const { data } = await getApiClient().get('/api/tool/list');
    return data;
  },

  /** 按名称执行工具 */
  async execute(toolName: string, params: Record<string, unknown>): Promise<ToolExecResult> {
    const { data } = await getApiClient().post('/api/tool/execute', {
      name: toolName,
      params,
    });
    return data;
  },

  /** 批量执行多个工具调用（顺序执行，互不影响） */
  async batch(calls: ToolCall[]): Promise<ToolExecResult[]> {
    const { data } = await getApiClient().post('/api/tool/batch', calls);
    return data;
  },
};
