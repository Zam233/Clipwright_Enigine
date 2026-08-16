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

/**
 * P0-5: 后端 schema（规范字段名）→ 前端 UI 模型映射。
 * 消除「保存时字段名错位导致参数静默重置」的 B1 缺陷：
 * 加载时把 max_sentence_len→max_sentence_length、base_shot_duration_ms→sec、
 * palette/primary_color→color_palette、inherits→parent_id 等反向映射回来。
 */
export function personaFromBackend(raw: Record<string, unknown>): Persona {
  const p = (raw.parameter ?? {}) as Record<string, any>;
  const identity = (p.identity ?? {}) as Record<string, any>;
  const language = (p.language ?? {}) as Record<string, any>;
  const rhythm = (p.rhythm ?? {}) as Record<string, any>;
  const visual = (p.visual ?? {}) as Record<string, any>;
  const audio = (p.audio ?? {}) as Record<string, any>;
  const constraints = (p.constraints ?? {}) as Record<string, any>;

  return {
    persona_id: String(raw.persona_id ?? ''),
    persona_name: String(raw.persona_name ?? raw.persona_id ?? ''),
    version: String(raw.version ?? '1.0.0'),
    parameter: {
      persona_id: p.persona_id != null ? String(p.persona_id) : undefined,
      identity: {
        persona_id: identity.persona_id != null ? String(identity.persona_id) : '',
        persona_name: identity.persona_name != null ? String(identity.persona_name) : '',
        version: identity.version != null ? String(identity.version) : '1.0.0',
        tone: identity.tone != null ? String(identity.tone) : 'neutral',
        positioning: identity.positioning != null ? String(identity.positioning) : '',
        knowledge_domains: Array.isArray(identity.knowledge_domains)
          ? identity.knowledge_domains.map(String)
          : [],
      },
      language: {
        max_sentence_length: typeof language.max_sentence_len === 'number' ? language.max_sentence_len : undefined,
        sentence_variance_target: typeof language.variance_target === 'number' ? language.variance_target : undefined,
        academic_density: typeof language.academic_density === 'number' ? language.academic_density : undefined,
        slang_ratio: typeof language.slang_ratio === 'number' ? language.slang_ratio : undefined,
        forbidden_patterns: Array.isArray(language.forbidden_patterns)
          ? language.forbidden_patterns.map(String)
          : undefined,
      },
      rhythm: {
        cut_density_tier:
          rhythm.cut_density_tier != null
            ? (String(rhythm.cut_density_tier) as PersonaRhythm['cut_density_tier'])
            : undefined,
        base_shot_duration_sec:
          typeof rhythm.base_shot_duration_ms === 'number' ? rhythm.base_shot_duration_ms / 1000 : undefined,
        pause_frequency: typeof rhythm.pause_frequency === 'number' ? rhythm.pause_frequency : undefined,
      },
      visual: {
        color_palette: {
          primary: typeof visual.primary_color === 'string' ? visual.primary_color : undefined,
          accent: typeof visual.accent_color === 'string' ? visual.accent_color : undefined,
          background: typeof visual.background_color === 'string' ? visual.background_color : undefined,
        },
        animation_style: visual.animation_style != null ? String(visual.animation_style) : undefined,
        transition_weights:
          visual.transition_weights && typeof visual.transition_weights === 'object'
            ? (visual.transition_weights as Record<string, number>)
            : undefined,
      },
      audio: {
        voice_clone_model_id: audio.voice_model != null ? String(audio.voice_model) : null,
        bgm_slots:
          audio.bgm_slots && typeof audio.bgm_slots === 'object'
            ? Object.values(audio.bgm_slots as Record<string, unknown>).flat().map(String)
            : undefined,
        loudness_target_lufs: typeof audio.target_loudness_lufs === 'number' ? audio.target_loudness_lufs : undefined,
      },
      constraints: {
        max_duration_sec: typeof constraints.max_duration_sec === 'number' ? constraints.max_duration_sec : undefined,
        source_citation_required:
          typeof constraints.require_source_citation === 'boolean' ? constraints.require_source_citation : undefined,
      },
    },
    prompt: typeof raw.prompt === 'string' ? raw.prompt : undefined,
    vision_prompt: typeof raw.vision_prompt === 'string' ? raw.vision_prompt : undefined,
    parent_id: raw.inherits != null ? String(raw.inherits) : null,
    created_at: typeof raw.created_at === 'string' ? raw.created_at : undefined,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : undefined,
  };
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
