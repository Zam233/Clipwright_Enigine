import { create } from 'zustand';
import type { Persona } from '@/types/persona';

interface ProjectState {
  projectId: string | null;
  projectName: string;
  personaId: string | null;
  pluginId: string | null;
  personas: Persona[];
  isSaving: boolean;
  lastSavedAt: string | null;
  saveError: boolean;
  saveNonce: number;

  voiceId: string | null;
  autoDub: boolean;
  scriptText: string;
  videoMode: string;
  splitMode: string;
  audioPath: string;
  audioDurationSec: number;

  requirementsTopic: string;
  requirementsScript: string;
  requirementsAudioDuration: number;
  materialSourceIds: string[];
  dubSegments: Array<{ start: number; end: number; text: string }> | null;

  // Actions
  setProjectId: (id: string | null) => void;
  setProjectName: (name: string) => void;
  setPersonaId: (id: string | null) => void;
  setPluginId: (id: string | null) => void;
  setPersonas: (personas: Persona[]) => void;
  setSaving: (saving: boolean) => void;
  setLastSaved: (at: string | null) => void;
  setSaveError: (error: boolean) => void;
  requestSave: () => void;
  setVoiceId: (id: string | null) => void;
  setAutoDub: (v: boolean) => void;
  setScriptText: (v: string) => void;
  setVideoMode: (v: string) => void;
  setSplitMode: (v: string) => void;
  setAudioPath: (v: string) => void;
  setAudioDurationSec: (v: number) => void;
  setRequirementsTopic: (v: string) => void;
  setRequirementsScript: (v: string) => void;
  setRequirementsAudioDuration: (v: number) => void;
  setMaterialSourceIds: (ids: string[]) => void;
  setDubSegments: (segments: Array<{ start: number; end: number; text: string }> | null) => void;
  resetProject: () => void;
}

export const useProjectStore = create<ProjectState>((set) => ({
  projectId: null,
  projectName: 'Untitled Project',
  personaId: null,
  pluginId: null,
  personas: [],
  isSaving: false,
  lastSavedAt: null,
  saveError: false,
  saveNonce: 0,

  voiceId: null,
  autoDub: true,
  scriptText: '',
  videoMode: 'voiceover',
  splitMode: 'period',
  audioPath: '',
  audioDurationSec: 0,

  requirementsTopic: '',
  requirementsScript: '',
  requirementsAudioDuration: 0,
  materialSourceIds: [],
  dubSegments: null,

  setProjectId: (id) => set({ projectId: id }),
  setProjectName: (name) => set({ projectName: name }),
  setPersonaId: (id) => set({ personaId: id }),
  setPluginId: (id) => set({ pluginId: id }),
  setPersonas: (personas) => set({ personas }),
  setSaving: (saving) => set({ isSaving: saving }),
  setLastSaved: (at) => set({ lastSavedAt: at }),
  setSaveError: (error) => set({ saveError: error }),
  requestSave: () => set((s) => ({ saveNonce: s.saveNonce + 1 })),
  setVoiceId: (id) => set({ voiceId: id }),
  setAutoDub: (v) => set({ autoDub: v }),
  setScriptText: (v) => set({ scriptText: v }),
  setVideoMode: (v) => set({ videoMode: v }),
  setSplitMode: (v) => set({ splitMode: v }),
  setAudioPath: (v) => set({ audioPath: v }),
  setAudioDurationSec: (v) => set({ audioDurationSec: v }),
  setRequirementsTopic: (v) => set({ requirementsTopic: v }),
  setRequirementsScript: (v) => set({ requirementsScript: v }),
  setRequirementsAudioDuration: (v) => set({ requirementsAudioDuration: v }),
  setMaterialSourceIds: (ids) => set({ materialSourceIds: ids }),
  setDubSegments: (segments) => set({ dubSegments: segments }),
  resetProject: () =>
    set({
      projectId: null,
      projectName: 'Untitled Project',
      personaId: null,
      pluginId: null,
      isSaving: false,
      lastSavedAt: null,
      saveError: false,
      saveNonce: 0,
      voiceId: null,
      autoDub: true,
      scriptText: '',
      videoMode: 'voiceover',
      splitMode: 'period',
      audioPath: '',
      audioDurationSec: 0,
      requirementsTopic: '',
      requirementsScript: '',
      requirementsAudioDuration: 0,
      materialSourceIds: [],
      dubSegments: null,
    }),
}));
