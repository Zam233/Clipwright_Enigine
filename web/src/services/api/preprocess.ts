import { getApiClient } from './client';

export interface PreprocessTask {
  task_id: string;
  file_path: string;
  file_name: string;
  operations: string[];
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress: number;
  results: Record<string, unknown>;
  error: string;
}

export interface PreprocessSubmitRequest {
  file_path: string;
  operations: string[];
}

export interface PreprocessBatchSubmitRequest {
  file_paths: string[];
  operations: string[];
}

export interface PreprocessTaskResults {
  task_id: string;
  status: PreprocessTask['status'];
  results?: Record<string, unknown>;
  message?: string;
}

export const preprocessApi = {
  async listOperations(): Promise<{ operations: string[]; descriptions: Record<string, string> }> {
    const { data } = await getApiClient().get('/api/preprocess/operations');
    return data;
  },

  async listQueue(status?: PreprocessTask['status'] | ''): Promise<PreprocessTask[]> {
    const { data } = await getApiClient().get<PreprocessTask[]>('/api/preprocess/queue', {
      params: status ? { status } : undefined,
    });
    return data;
  },

  async submit(filePath: string, operations: string[]): Promise<PreprocessTask> {
    const { data } = await getApiClient().post<PreprocessTask>('/api/preprocess/submit', {
      file_path: filePath,
      operations,
    } satisfies PreprocessSubmitRequest);
    return data;
  },

  async batchSubmit(filePaths: string[], operations: string[]): Promise<PreprocessTask[]> {
    const { data } = await getApiClient().post<PreprocessTask[]>('/api/preprocess/batch-submit', {
      file_paths: filePaths,
      operations,
    } satisfies PreprocessBatchSubmitRequest);
    return data;
  },

  async getTask(taskId: string): Promise<PreprocessTask> {
    const { data } = await getApiClient().get<PreprocessTask>(`/api/preprocess/task/${taskId}`);
    return data;
  },

  async listResults(taskId: string): Promise<PreprocessTaskResults> {
    const { data } = await getApiClient().get<PreprocessTaskResults>(`/api/preprocess/task/${taskId}/results`);
    return data;
  },

  async removeTask(taskId: string): Promise<void> {
    await getApiClient().delete(`/api/preprocess/task/${taskId}`);
  },
};
