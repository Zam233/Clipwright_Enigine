/**
 * Persona types — matching backend Persona 4-layer system
 */

export interface PersonaIdentity {
  persona_id: string;
  persona_name: string;
  version: string;
  tone: string;
  positioning?: string;
  knowledge_domains: string[];
}

export interface PersonaLanguage {
  max_sentence_length?: number;
  sentence_variance_target?: number;
  academic_density?: number;
  slang_ratio?: number;
  forbidden_patterns?: string[];
}

export interface PersonaRhythm {
  cut_density_tier?: 'low' | 'medium' | 'high' | 'extreme';
  base_shot_duration_sec?: number;
  pause_frequency?: number;
}

export interface PersonaVisual {
  color_palette?: {
    primary?: string;
    accent?: string;
    background?: string;
  };
  animation_style?: string;
  transition_weights?: Record<string, number>;
}

export interface PersonaAudio {
  voice_clone_model_id?: string | null;
  bgm_slots?: string[];
  loudness_target_lufs?: number;
}

export interface PersonaConstraints {
  max_duration_sec?: number;
  source_citation_required?: boolean;
}

/** Parameter Layer — human-readable YAML/JSON style constraints */
export interface ParameterLayer {
  persona_id?: string;
  identity: PersonaIdentity;
  language: PersonaLanguage;
  rhythm: PersonaRhythm;
  visual: PersonaVisual;
  audio: PersonaAudio;
  constraints: PersonaConstraints;
}

/** Complete Persona (4-layer composite) */
export interface Persona {
  persona_id: string;
  persona_name: string;
  version: string;
  parameter: ParameterLayer;
  prompt?: string;
  vision_prompt?: string;
  parent_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** Chat Forge state */
export interface ChatForgeState {
  sessionId: string | null;
  messages: ChatForgeMessage[];
  personaDraft: Partial<ParameterLayer> | null;
  progress: {
    identity: number;
    language: number;
    rhythm: number;
    visual: number;
    audio: number;
    constraints: number;
  };
  knowledgeFiles: { name: string; chapters: number }[];
}

export interface ChatForgeMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
}

/** Requirements Agent types */
export type RequirementsStatus =
  | 'idle'
  | 'gathering'
  | 'brief_ready'
  | 'brief_confirmed'
  | 'planning'
  | 'plan_ready'
  | 'plan_confirmed'
  | 'pipeline_running'
  | 'pipeline_done'
  | 'completed'
  | 'error';

export interface CreativeBrief {
  title: string;
  overview: string;
  target_audience: string;
  core_message: string;
  style_direction: string;
  structure_suggestion: string;
  duration_estimate: string;
  key_elements: string[];
  special_requirements: string[];
  production_plan?: string;
  reference_style?: string;
  bgm_requirement?: string;
  era_background?: string;
  material_requirements?: {
    type?: string;
    source?: string;
    preference?: string;
  };
  animation_style?: {
    style?: string;
    tone?: string;
  };
  asset_ratio?: {
    footage: string;
    mg: string;
  };
}

export interface ProductionPlan {
  markdown?: string;
  markdown_content?: string;
  scenes?: unknown[];
  toc?: { level: number; title: string; anchor: string }[];
}

export interface RequirementMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  creative_brief?: CreativeBrief | null;
  production_plan?: ProductionPlan | null;
}
