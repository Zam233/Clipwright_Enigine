/**
 * Pipeline types — matching backend clipwright/schema/pipeline.py
 */

export type PipelineStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type PipelinePhase =
  | 'idle'
  | 'structure'
  | 'material'
  | 'edit'
  | 'animation'
  | 'audio'
  | 'quality'
  | 'self_heal'
  | 'completed'
  | 'failed';

export interface PipelineStep {
  agent_name: string;
  status: PipelineStatus;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
  retry_count: number;
}

export interface PipelineRequest {
  persona_id: string;
  category_plugin_id: string;
  topic: string;
  extra_params?: Record<string, unknown>;
  dry_run?: boolean;
  use_v2?: boolean;
}

export interface PipelineState {
  pipeline_id: string;
  status: PipelineStatus;
  request: PipelineRequest;
  created_at: string;
  updated_at: string;
  current_agent?: string | null;
  steps: PipelineStep[];
  shared_data: Record<string, unknown>;
  output_path?: string | null;
  error?: string | null;
}

/** SSE event types from pipeline trace stream */
export type PipelineSSEEventType =
  | 'agent_start'
  | 'agent_end'
  | 'agent_complete'
  | 'agent_error'
  | 'timeline_snapshot'
  | 'tool'
  | 'llm'
  | 'log'
  | 'info'
  | 'warning'
  | 'done'
  | 'self_heal'
  | 'pipeline_complete'
  | 'cancelled';

export interface AgentSuggestion {
  id: string;
  type: 'clip' | 'pace' | 'style' | 'transition';
  message: string;
  confidence: number;
  data?: Record<string, unknown>;
}

export interface AgentChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
}

/** SSE log event types */
export type LogEventType =
  | 'agent_start'
  | 'agent_end'
  | 'llm'
  | 'tool'
  | 'skill'
  | 'plugin'
  | 'info'
  | 'warning'
  | 'error'
  | 'timeline_snapshot'
  | 'mg_start'
  | 'mg_end';

/** A single log entry from the pipeline execution log */
export interface LogEntry {
  id: string;
  timestamp: number;
  agent: string;
  type: LogEventType;
  summary: string;
  detail?: Record<string, unknown> | null;
  expanded?: boolean;
}

/** Per-agent timing stats */
export interface AgentStat {
  agent: string;
  startTime: number;
  endTime?: number;
  durationMs?: number;
}

/** Pipeline run summary */
export interface PipelineSummary {
  totalTokens: number;
  totalCost: string;
  materialCount: number;
  selfHealCount: number;
  agentDurations: Record<string, number>;
  timelineStats?: { tracks: number; clips: number; durationSec: number };
}
