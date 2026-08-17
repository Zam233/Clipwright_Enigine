import { getApiClient } from './client';
import { apiBase } from './sse';
import type { PipelineRequest } from '@/types/pipeline';

/** Single Agent span inside a pipeline run record (aligned with backend pipeline_v2.get_run_records). */
export interface PipelineSpan {
  agent: string;
  start: number;
  dur: number;
  status: 'ok' | 'fail' | 'retry';
}

/** Pipeline run history record — shape of GET /api/pipeline/runs. */
export interface PipelineRunRecord {
  id: string;
  topic: string;
  status: 'completed' | 'running' | 'failed';
  duration_ms: number;
  started_at: string;
  agents: PipelineSpan[];
}

export const pipelineApi = {
  /** Run pipeline async, returns pipeline_id immediately */
  async runAsync(request: PipelineRequest) {
    const { data } = await getApiClient().post('/api/pipeline/run-async', request);
    return data as { pipeline_id: string; status: string };
  },

  /** Get pipeline result */
  async getResult(pipelineId: string) {
    const { data } = await getApiClient().get(`/api/pipeline/result/${pipelineId}`);
    return data;
  },

  /** Get pipeline status */
  async getStatus(pipelineId: string) {
    const { data } = await getApiClient().get(`/api/pipeline/status/${pipelineId}`);
    return data as { pipeline_id: string; status: string; has_result: boolean };
  },

  /** Get pipeline run history (real execution records for the admin console) */
  async getRunRecords(limit = 50): Promise<PipelineRunRecord[]> {
    const { data } = await getApiClient().get('/api/pipeline/runs', {
      params: { limit },
    });
    if (!Array.isArray(data)) return [];
    return data as PipelineRunRecord[];
  },

  /** A10: 队列任务概览（含重启恢复项 recovered/interrupted） */
  async getTasks(limit = 50) {
    const { data } = await getApiClient().get('/api/pipeline/tasks', {
      params: { limit },
    });
    return data as {
      tasks: Array<{
        task_id: string;
        task_type: string;
        priority: number;
        status: string;
        progress: number;
        progress_text: string;
        error: string;
        duration_sec: number;
        created_at: string;
      }>;
      recovered: Array<Record<string, unknown> & { task_id: string; recovered: boolean; status: string }>;
      running: number;
      pending: number;
    };
  },

  /** Retry from failed agent */
  async retry(pipelineId: string, agentName: string) {
    const { data } = await getApiClient().post(
      `/api/pipeline/retry/${pipelineId}/${agentName}`,
    );
    return data;
  },

  /** Cancel a running pipeline (cooperative cancel, backend marks step CANCELLED) */
  async cancel(pipelineId: string) {
    const { data } = await getApiClient().post(
      `/api/pipeline/cancel/${pipelineId}`,
    );
    return data;
  },

  /** Predict script configuration */
  async predictScript(scriptText: string) {
    const { data } = await getApiClient().post(
      '/api/pipeline/predict-script',
      { script_text: scriptText },
    );
    return data;
  },

  /** Predict material usage (analyze a material file) */
  async predictMaterial(filePath: string, fileSize = 0) {
    const { data } = await getApiClient().post(
      '/api/pipeline/predict-material',
      { file_path: filePath, file_size: fileSize },
    );
    return data;
  },

  /** Get pipeline trace events as a JSON array */
  async getTraceJson(pipelineId: string) {
    const { data } = await getApiClient().get(`/api/pipeline/trace/${pipelineId}`);
    return data as Array<{
      type: string;
      time: number;
      agent?: string;
      message?: string;
      [key: string]: unknown;
    }>;
  },

  /** Create SSE stream URL for pipeline trace（token 由调用方用 withSseToken 附加） */
  getTraceStreamUrl(pipelineId: string): string {
    return `${apiBase()}/api/pipeline/trace/stream/${pipelineId}`;
  },
};
