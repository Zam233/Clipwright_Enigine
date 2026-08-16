import { getApiClient } from './client';

// ── Types (aligned with backend clipwright/api/learning.py) ──

export type TrainingStatus =
  | 'pending'
  | 'preparing'
  | 'training'
  | 'evaluating'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface TrainingJob {
  job_id: string;
  name: string;
  status: TrainingStatus;
  base_model: string;
  dataset_id: string;
  config: Record<string, unknown>;
  progress: number;
  current_epoch: number;
  total_epochs: number;
  metrics: Record<string, number>;
  output_model_id: string;
  error: string;
  created_at: string;
  started_at: string;
  completed_at: string;
}

export interface DatasetInfo {
  dataset_id: string;
  name: string;
  description: string;
  sample_count: number;
  total_duration_sec: number;
  created_at: string;
  status: string;
}

export interface CreateJobRequest {
  name: string;
  base_model?: string;
  dataset_id: string;
  epochs?: number;
  learning_rate?: number;
  batch_size?: number;
  lora_rank?: number;
  extra_config?: Record<string, unknown>;
}

export interface CreateDatasetRequest {
  name: string;
  description?: string;
  video_paths?: string[];
  annotations?: Array<Record<string, unknown>>;
}

export interface LearningStatus {
  status: 'training' | 'idle';
  active_jobs: number;
  total_jobs: number;
  gpu_available: boolean;
}

export const learningApi = {
  /** 学习管线整体状态（GPU 可用性 / 任务统计） */
  async status(): Promise<LearningStatus> {
    const { data } = await getApiClient().get('/api/learning/status');
    return data;
  },

  /** 列出所有数据集 */
  async listDatasets(): Promise<DatasetInfo[]> {
    const { data } = await getApiClient().get('/api/learning/datasets');
    return data;
  },

  /** 创建训练数据集 */
  async createDataset(req: CreateDatasetRequest): Promise<DatasetInfo> {
    const { data } = await getApiClient().post('/api/learning/datasets/create', req);
    return data;
  },

  /** 删除数据集 */
  async deleteDataset(datasetId: string): Promise<{ status: string; dataset_id: string }> {
    const { data } = await getApiClient().delete(`/api/learning/datasets/${datasetId}`);
    return data;
  },

  /** 列出训练任务（可按状态过滤） */
  async listJobs(status?: string): Promise<TrainingJob[]> {
    const params: Record<string, string> = {};
    if (status) params.status = status;
    const { data } = await getApiClient().get('/api/learning/jobs', { params });
    return data;
  },

  /** 获取训练任务详情 */
  async getJob(jobId: string): Promise<TrainingJob> {
    const { data } = await getApiClient().get(`/api/learning/jobs/${jobId}`);
    return data;
  },

  /** 创建训练任务（排队等待执行） */
  async createJob(req: CreateJobRequest): Promise<TrainingJob> {
    const { data } = await getApiClient().post('/api/learning/jobs/create', req);
    return data;
  },

  /** 启动训练任务 */
  async startJob(jobId: string): Promise<{ status: string; job_id: string; message?: string }> {
    const { data } = await getApiClient().post(`/api/learning/jobs/${jobId}/start`);
    return data;
  },

  /** 取消训练任务 */
  async cancelJob(jobId: string): Promise<{ status: string; job_id: string }> {
    const { data } = await getApiClient().post(`/api/learning/jobs/${jobId}/cancel`);
    return data;
  },

  /** 删除训练任务记录 */
  async deleteJob(jobId: string): Promise<{ status: string; job_id: string }> {
    const { data } = await getApiClient().delete(`/api/learning/jobs/${jobId}`);
    return data;
  },

  /** 列出已训练的模型 */
  async listModels(): Promise<Array<Record<string, unknown>>> {
    const { data } = await getApiClient().get('/api/learning/models');
    return data;
  },
};
