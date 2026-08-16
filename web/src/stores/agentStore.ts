import { create } from 'zustand';
import { uid } from '@/lib/utils';
import type { Timeline } from '@/types/timeline';
import type {
  PipelinePhase,
  AgentSuggestion,
  AgentChatMessage,
  LogEntry,
  AgentStat,
  PipelineSummary,
} from '@/types/pipeline';
import type {
  RequirementsStatus,
  RequirementMessage,
  CreativeBrief,
  ProductionPlan,
} from '@/types/persona';

/** 各管线相位对应的进度百分比（未显式给定进度时按此推导）。 */
// E7: 日志上限——防止长管线日志无界增长导致 DOM 卡顿
export const MAX_LOG_ENTRIES = 500;

const PHASE_PROGRESS: Record<string, number> = {
  idle: 0,
  structure: 15,
  material: 35,
  edit: 55,
  animation: 70,
  audio: 85,
  quality: 95,
  self_heal: 90,
  completed: 100,
  failed: 100,
};

interface Annotation {
  id: string;
  type: 'comment' | 'dislike' | 'like';
  text: string;
  note?: string;
}

interface AgentState {
  // Pipeline state
  pipelineId: string | null;
  phase: PipelinePhase;
  progress: number;
  agentTimeline: Timeline | null;
  suggestions: AgentSuggestion[];
  chatMessages: AgentChatMessage[];
  error: string | null;
  isStreaming: boolean;
  cancelling: boolean;

  // Log entries
  logEntries: LogEntry[];
  agentStats: AgentStat[];
  pipelineSummary: PipelineSummary | null;

  // MG 动画进度（动画阶段逐片段生成）
  mgTotal: number;
  mgDone: number;

  // Requirements Agent state
  requirementsSessionId: string | null;
  requirementsStatus: RequirementsStatus;
  requirementsMessages: RequirementMessage[];
  requirementsBusy: boolean;
  creativeBrief: CreativeBrief | null;
  productionPlan: ProductionPlan | null;

  // Annotations for review mode
  annotations: Annotation[];

  // Full-screen review mode
  reviewMode: 'brief' | 'plan' | null;

  // Pipeline actions
  setPipelineId: (id: string | null) => void;
  updatePhase: (phase: PipelinePhase, progress?: number) => void;
  setAgentTimeline: (timeline: Timeline | null) => void;
  addSuggestion: (suggestion: AgentSuggestion) => void;
  clearSuggestions: () => void;
  addChatMessage: (message: AgentChatMessage) => void;
  setError: (error: string | null) => void;
  setStreaming: (streaming: boolean) => void;
  setCancelling: (busy: boolean) => void;
  resetPipeline: () => void;

  // Log actions
  addLogEntry: (entry: Omit<LogEntry, 'id'>) => void;
  addLogEntries: (entries: Omit<LogEntry, 'id'>[]) => void;
  clearLogs: () => void;
  toggleLogExpand: (id: string) => void;
  setAgentStats: (stats: AgentStat[]) => void;
  setPipelineSummary: (summary: PipelineSummary | null) => void;

  // MG 进度 actions
  mgStarted: () => void;
  mgFinished: () => void;
  resetMgProgress: () => void;

  // Requirements actions
  setRequirementsSession: (sessionId: string | null) => void;
  setRequirementsStatus: (status: RequirementsStatus) => void;
  addRequirementsMessage: (message: RequirementMessage) => void;
  setRequirementsBusy: (busy: boolean) => void;
  setCreativeBrief: (brief: CreativeBrief | null) => void;
  setProductionPlan: (plan: ProductionPlan | null) => void;
  resetRequirements: () => void;
  restoreAgentState: (snapshot: {
    requirementsSessionId?: string | null;
    requirementsStatus?: string;
    requirementsMessages?: unknown[];
    creativeBrief?: unknown;
    productionPlan?: unknown;
    logEntries?: unknown[];
  }) => void;

  // Annotation actions
  addAnnotation: (ann: Omit<Annotation, 'id'>) => void;
  removeAnnotation: (id: string) => void;
  clearAnnotations: () => void;
  setReviewMode: (mode: 'brief' | 'plan' | null) => void;
}

export const useAgentStore = create<AgentState>((set) => ({
  pipelineId: null,
  phase: 'idle',
  progress: 0,
  agentTimeline: null,
  suggestions: [],
  chatMessages: [],
  error: null,
  isStreaming: false,
  cancelling: false,

  logEntries: [],
  agentStats: [],
  pipelineSummary: null,
  mgTotal: 0,
  mgDone: 0,

  requirementsSessionId: null,
  requirementsStatus: 'idle',
  requirementsMessages: [],
  requirementsBusy: false,
  creativeBrief: null,
  productionPlan: null,

  annotations: [],
  reviewMode: null,

  setPipelineId: (id) => set({ pipelineId: id }),

  updatePhase: (phase, progress) =>
    set((state) => ({
      phase,
      // 未显式给定进度时，按相位在管线中的位置推导，避免进度条全程卡在初始值
      progress: progress ?? PHASE_PROGRESS[phase] ?? state.progress,
    })),

  setAgentTimeline: (timeline) => set({ agentTimeline: timeline }),

  addSuggestion: (suggestion) =>
    set((state) => ({
      suggestions: [...state.suggestions, suggestion],
    })),

  clearSuggestions: () => set({ suggestions: [] }),

  addChatMessage: (message) =>
    set((state) => ({
      chatMessages: [...state.chatMessages, message],
    })),

  setError: (error) => set({ error }),

  setStreaming: (streaming) => set({ isStreaming: streaming }),

  setCancelling: (busy) => set({ cancelling: busy }),

  resetPipeline: () =>
    set({
      pipelineId: null,
      phase: 'idle',
      progress: 0,
      agentTimeline: null,
      suggestions: [],
      error: null,
      isStreaming: false,
      cancelling: false,
      logEntries: [],
      agentStats: [],
      pipelineSummary: null,
      chatMessages: [],
      mgTotal: 0,
      mgDone: 0,
    }),

  addLogEntry: (entry) =>
    set((state) => ({
      logEntries: [...state.logEntries, { ...entry, id: uid('log') }].slice(-MAX_LOG_ENTRIES),
    })),

  addLogEntries: (entries) =>
    set((state) => ({
      logEntries: [
        ...state.logEntries,
        ...entries.map((e) => ({ ...e, id: uid('log') })),
      ].slice(-MAX_LOG_ENTRIES),
    })),

  clearLogs: () => set({ logEntries: [], agentStats: [], pipelineSummary: null, mgTotal: 0, mgDone: 0 }),
  toggleLogExpand: (id) =>
    set((state) => ({
      logEntries: state.logEntries.map((e) =>
        e.id === id ? { ...e, expanded: !e.expanded } : e,
      ),
    })),

  setAgentStats: (stats) => set({ agentStats: stats }),
  setPipelineSummary: (summary) => set({ pipelineSummary: summary }),

  mgStarted: () =>
    set((state) => ({ mgTotal: state.mgTotal + 1 })),
  mgFinished: () =>
    set((state) => ({ mgDone: state.mgDone + 1 })),
  resetMgProgress: () => set({ mgTotal: 0, mgDone: 0 }),

  setRequirementsSession: (sessionId) =>
    set({ requirementsSessionId: sessionId }),

  setRequirementsStatus: (status) =>
    set({ requirementsStatus: status }),

  setRequirementsBusy: (busy) => set({ requirementsBusy: busy }),

  addRequirementsMessage: (message) =>
    set((state) => {
      const msgs = [...state.requirementsMessages, message];
      saveDraft({ messages: msgs, brief: state.creativeBrief, plan: state.productionPlan, sessionId: state.requirementsSessionId, status: state.requirementsStatus });
      return { requirementsMessages: msgs };
    }),

  setCreativeBrief: (brief) =>
    set((state) => { saveDraft({ brief, plan: state.productionPlan }); return { creativeBrief: brief }; }),
  setProductionPlan: (plan) =>
    set((state) => { saveDraft({ plan, brief: state.creativeBrief }); return { productionPlan: plan }; }),

  resetRequirements: () =>
    set({
      requirementsSessionId: null,
      requirementsStatus: 'idle',
      requirementsMessages: [],
      requirementsBusy: false,
      creativeBrief: null,
      productionPlan: null,
      annotations: [],
      reviewMode: null,
    }),

  restoreAgentState: (snapshot) =>
    set({
      requirementsSessionId: snapshot.requirementsSessionId ?? null,
      requirementsStatus: (snapshot.requirementsStatus as RequirementsStatus) ?? 'idle',
      requirementsMessages: (snapshot.requirementsMessages as RequirementMessage[]) ?? [],
      creativeBrief: (snapshot.creativeBrief as CreativeBrief) ?? null,
      productionPlan: (snapshot.productionPlan as ProductionPlan) ?? null,
      logEntries: (snapshot.logEntries as LogEntry[]) ?? [],
    }),

  addAnnotation: (ann) =>
    set((state) => ({ annotations: [...state.annotations, { ...ann, id: uid('ann') }] })),
  removeAnnotation: (id) =>
    set((state) => ({ annotations: state.annotations.filter((a) => a.id !== id) })),
  clearAnnotations: () => set({ annotations: [] }),
  setReviewMode: (mode) => set({ reviewMode: mode }),
}));

const DRAFT_KEY = 'clipwright_requirements_draft';

function saveDraft(partial: {
  messages?: RequirementMessage[];
  brief?: CreativeBrief | null;
  plan?: ProductionPlan | null;
  sessionId?: string | null;
  status?: RequirementsStatus;
}) {
  try {
    const current = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
    const merged = { ...current, ...partial, ts: Date.now() };
    if (partial.messages) merged.messages = partial.messages.slice(-50);
    localStorage.setItem(DRAFT_KEY, JSON.stringify(merged));
  } catch { /* ignore */ }
}

export function loadRequirementsDraft(): {
  messages: RequirementMessage[];
  brief: CreativeBrief | null;
  plan: ProductionPlan | null;
  sessionId: string | null;
  status: RequirementsStatus;
} | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const draft = JSON.parse(raw);
    // Guard against missing/invalid timestamp (NaN comparison would never expire)
    if (typeof draft.ts !== 'number' || Date.now() - draft.ts > 86400000) {
      localStorage.removeItem(DRAFT_KEY);
      return null;
    }
    return draft;
  } catch { return null; }
}

export function clearRequirementsDraft() {
  localStorage.removeItem(DRAFT_KEY);
}
