> ⚠️ **Document type**: Architecture & Design Plan. The actual implementation may differ in structural details from the planned design described below. Sections marked [VERIFIED] have been cross-checked against current code.

# ClipWright - Frontend Client Design & Implementation Plan

> **Date**: 2026-07-20
> **Project Phase**: Phase 5 - Full Timeline Editor
> **Backend Reference**: `D:\Clipweight` - Content Video Orchestration Engine v0.1.0
> **Version**: v1.0.0

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture Design](#2-architecture-design)
3. [Pages & Routes](#3-pages--routes)
4. [State Management](#4-state-management)
5. [Component Tree](#5-component-tree)
6. [Multi-track Timeline Engine](#6-multi-track-timeline-engine)
7. [Video Preview Engine](#7-video-preview-engine)
8. [Asset Library System](#8-asset-library-system)
9. [Property Panel Design](#9-property-panel-design)
10. [Agent Co-pilot Panel](#10-agent-co-pilot-panel)
11. [Persona Management Module](#11-persona-management-module)
12. [Animation & Keyframe Editor](#12-animation--keyframe-editor)
13. [Render & Export System](#13-render--export-system)
14. [API Client & Data Flow](#14-api-client--data-flow)
15. [Plugin System (Frontend)](#15-plugin-system-frontend)
16. [Technology Decisions](#16-technology-decisions)
17. [Phased Implementation Plan](#17-phased-implementation-plan)
18. [Testing Strategy](#18-testing-strategy)
19. [Performance Optimization](#19-performance-optimization)
20. [Security & Permissions](#20-security--permissions)
21. [Appendix](#21-appendix)

---

## 1 Overview

### 1.1 Project Positioning

The ClipWright frontend is a **standalone, full-featured web video editor**, not merely an Agent output window. Its baseline capabilities target Adobe Premiere Pro - multi-track timeline, asset drag-and-drop replacement, animation parameter panels, keyframe editing. On top of this, AI Agents are embedded as "co-pilots": users can accept Agent-generated rough-cut timelines, then manually fine-tune every frame, and can invoke Agent intervention at any point.

> **Core Product Logic**: Agent generates a rough draft, human reviews on the timeline, local dissatisfaction can be re-processed by Agent, final export on satisfaction.

### 1.2 Frontend-Backend Responsibility Boundary

| Responsibility | Frontend | Backend |
|---------------|:--------:|:-------:|
| Timeline interaction (drag, trim, track management) | Yes | |
| Asset library browsing, manual import, tag management | Yes | |
| Animation parameter panel (keyframe editing, style switching) | Yes | |
| Video preview (Canvas + WebCodecs) | Yes | |
| Persona visual configuration | Yes | |
| Export parameter settings | Yes | |
| Agent invocation trigger | Yes | |
| Persona parsing & validation | | Yes |
| Agent Pipeline orchestration & execution | | Yes |
| Asset semantic retrieval & matching | | Yes |
| Timeline generation (rough cut) | | Yes |
| Animation composition, audio matching | | Yes |
| Render transcoding | | Yes |
| Asset library indexing & storage | | Yes |

### 1.3 Tech Stack

| Layer | Choice | Version | Rationale |
|-------|--------|---------|-----------|
| Framework | React | 19+ | Mature ecosystem, concurrent rendering |
| Language | TypeScript | 5.5+ | Type safety, IDE intelligence |
| Build | Vite | 6+ | Fast HMR, ESBuild pre-bundling |
| State | Zustand | 5+ | Lightweight, immutable, middleware, time-travel |
| Router | TanStack Router | 1+ | Type-safe, file-system routing |
| UI | Radix UI + shadcn/ui | latest | Unstyled, accessible, customizable |
| CSS | Tailwind CSS | 4+ | Utility-first, design system consistency |
| Timeline | Self-built Canvas 2D engine | - | Frame-level precision needed |
| Drag/Drop | DnD Kit | 6+ | Lightweight, accessible, Canvas hybrid |
| Video Preview | WebCodecs API + Canvas | - | Real-time preview without backend transcode |
| HTTP | TanStack Query (React Query) | 5+ | Caching, retry, optimistic updates |
| Real-time | WebSocket (SSE fallback) | - | Agent status streaming, render progress |
| Animation | Self-built keyframe engine | - | Deep Timeline integration |
| Waveform | Self-built / WaveSurfer.js | - | Audio track visualization |
| Virtual Scroll | @tanstack/react-virtual | 3+ | Large timeline performance |
| Testing | Vitest + Playwright | latest | Unit + Component + E2E |
| Linting | ESLint + Prettier + Biome | latest | Unified code style |
| Package Manager | pnpm | 9+ | Fast, disk-efficient |

---

## 2 Architecture Design

### 2.1 Layered Architecture

```
User Interface Layer (Pages)
    EditorPage   PersonaPage   ExportPage   ProjectPage   SettingsPage
         |             |            |            |             |
Layout Layer
    EditorLayout (4-panel: Assets | Preview+Timeline | Properties | Agent)
    StandardLayout
         |             |            |            |             |
Feature Module Layer
    Timeline Engine  Preview Engine  Asset Library  Agent Panel
    Property Panel   Animation Editor  Persona Mgr  Export/Render
    Keyboard System  History (Undo/Redo)  Plugin System  Project Mgr
         |             |            |            |             |
State Management Layer (Zustand Stores) [VERIFIED]
    timelineStore  selectionStore  agentStore  assetStore
    previewStore   historyStore   workspaceStore  settingsStore
    projectStore   voiceStore
         |             |            |            |             |
Data Access Layer (API & Services)
    TanStack Query (cache)  WebSocket Client  REST API
    SSE Client (Agent Stream)  IndexedDB (local cache)
         |             |            |            |             |
Core Infrastructure
    Canvas2D Renderer  Keyframe Interpolator  WebCodecs Pipeline
    DnD Core  Keybinding Engine  i18n  Telemetry (Sentry)
```

### 2.2 Monorepo Directory Structure [VERIFIED]

```
clipwright-web/
鈹溾攢鈹€ public/
鈹?  鈹溾攢鈹€ favicon.ico
鈹?  鈹斺攢鈹€ fonts/
鈹溾攢鈹€ src/
鈹?  鈹溾攢鈹€ main.tsx                    # App entry
鈹?  鈹溾攢鈹€ App.tsx                     # Root + Router
鈹?  鈹溾攢鈹€ providers.tsx               # Global Provider composition
鈹?  鈹?鈹?  鈹溾攢鈹€ router.tsx                  # Flat createRoute() API (TanStack Router)
鈹?  鈹?  鈹?  鈹溾攢鈹€ (HomePage at `/`, lazy pages for all other routes)
鈹?  鈹?鈹?  鈹溾攢鈹€ layouts/                    # Layout components
鈹?  鈹?  鈹溾攢鈹€ EditorLayout.tsx        # 4-panel editor layout
鈹?  鈹?  鈹斺攢鈹€ StandardLayout.tsx      # Standard page layout
鈹?  鈹?鈹?  鈹溾攢鈹€ features/                   # Feature modules (independent directories)
鈹?  鈹?  鈹溾攢鈹€ timeline/               # Timeline core
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ TimelineRoot.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ Ruler.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ TrackList.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ TrackItem.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ClipItem.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ Playhead.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ Markers.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ SelectionRect.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ SnapGuides.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ WaveformOverlay.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ KeyframeOverlay.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ engine/             # Canvas rendering engine
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ TimelineCanvas.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ renderers/
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ clipRenderer.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ rulerRenderer.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ playheadRenderer.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ gridRenderer.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ waveformRenderer.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ selectionRenderer.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ interaction/
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ DragHandler.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ResizeHandler.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ScrollZoomHandler.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ RangeSelectHandler.ts
鈹?  鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ KeyboardHandler.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ layout/
鈹?  鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ timelineLayout.ts
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ cache/
鈹?  鈹?  鈹?  鈹?      鈹斺攢鈹€ frameCache.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ store/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ timelineStore.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ selectionStore.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ viewportStore.ts
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ keyboardStore.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ hooks/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ useTimelineZoom.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ useTimelineScroll.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ useClipDrag.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ useClipResize.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ usePlayheadDrag.ts
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ useKeyboardShortcuts.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ utils/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ timeUtils.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ snapUtils.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ rippleUtils.ts
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ clipValidation.ts
鈹?  鈹?  鈹?  鈹斺攢鈹€ timeline.types.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ preview/                # Video preview
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PreviewCanvas.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PreviewControls.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ SafeFrameOverlay.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ ResolutionSwitcher.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ engine/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ WebCodecsPipeline.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ CanvasRenderer.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ WebGLRenderer.ts
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ AudioContext.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ store/
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ previewStore.ts
鈹?  鈹?  鈹?  鈹斺攢鈹€ hooks/
鈹?  鈹?  鈹?      鈹溾攢鈹€ usePreviewPlayer.ts
鈹?  鈹?  鈹?      鈹斺攢鈹€ useVideoDecoder.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ assets/                 # Asset library
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AssetPanel.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AssetGrid.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AssetCard.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AssetSearch.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AssetFilter.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AssetUpload.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AiMatchPanel.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ HistoryPanel.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ store/
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ assetStore.ts
鈹?  鈹?  鈹?  鈹斺攢鈹€ hooks/
鈹?  鈹?  鈹?      鈹溾攢鈹€ useAssetSearch.ts
鈹?  鈹?  鈹?      鈹斺攢鈹€ useAiMatch.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ properties/             # Property panel
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PropertyPanel.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ VideoProperties.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ TextProperties.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AudioProperties.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ TransitionProperties.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ EffectProperties.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ TransformControls.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ColorPicker.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ Slider.tsx
鈹?  鈹?  鈹?  鈹斺攢鈹€ store/
鈹?  鈹?  鈹?      鈹斺攢鈹€ propertyStore.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ animation/              # Animation & keyframe editor
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ KeyframeEditor.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ KeyframeTrack.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ KeyframeDiamond.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AnimationPresetPanel.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ EasingSelector.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AnimationTimeline.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ MgAnimationPreview.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ engine/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ KeyframeInterpolator.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ EasingFunctions.ts
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ AnimationResolver.ts
鈹?  鈹?  鈹?  鈹斺攢鈹€ store/
鈹?  鈹?  鈹?      鈹斺攢鈹€ animationStore.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ agent/                  # Agent co-pilot
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AgentPanel.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AgentChat.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AgentSuggestion.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AgentProgress.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ AgentTimelineDiff.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ QuickActions.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ store/
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ agentStore.ts
鈹?  鈹?  鈹?  鈹斺攢鈹€ hooks/
鈹?  鈹?  鈹?      鈹溾攢鈹€ useAgentPipeline.ts
鈹?  鈹?  鈹?      鈹溾攢鈹€ useAgentStream.ts
鈹?  鈹?  鈹?      鈹斺攢鈹€ useAgentSuggestion.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ persona/                # Persona management
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PersonaList.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PersonaCard.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PersonaEditor.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ParameterEditor.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PromptEditor.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ KnowledgeEditor.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ExemplarUploader.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PersonaCompare.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PersonaInheritance.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ PersonaForgeWizard.tsx
鈹?  鈹?  鈹?  鈹斺攢鈹€ store/
鈹?  鈹?  鈹?      鈹斺攢鈹€ personaStore.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ export/                 # Export & render
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ExportPanel.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ExportPresetCard.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ExportSettings.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ RenderQueue.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ RenderProgress.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ ExportHistory.tsx
鈹?  鈹?  鈹?  鈹斺攢鈹€ store/
鈹?  鈹?  鈹?      鈹斺攢鈹€ exportStore.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ project/                # Project management
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ProjectList.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ProjectCard.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ ProjectCreate.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ ProjectSettings.tsx
鈹?  鈹?  鈹?  鈹斺攢鈹€ store/
鈹?  鈹?  鈹?      鈹斺攢鈹€ projectStore.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ plugins/                # Frontend plugin system
鈹?  鈹?  鈹?  鈹溾攢鈹€ core/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PluginManager.ts
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PluginSdk.ts
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ HookRegistry.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PluginPanel.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ PluginSettings.tsx
鈹?  鈹?  鈹?  鈹斺攢鈹€ store/
鈹?  鈹?  鈹?      鈹斺攢鈹€ pluginStore.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ workspace/              # Workspace management
鈹?  鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PanelContainer.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ PanelResizer.tsx
鈹?  鈹?  鈹?  鈹?  鈹溾攢鈹€ WorkspaceTabs.tsx
鈹?  鈹?  鈹?  鈹?  鈹斺攢鈹€ WorkspacePresets.tsx
鈹?  鈹?  鈹?  鈹斺攢鈹€ store/
鈹?  鈹?  鈹?      鈹斺攢鈹€ workspaceStore.ts
鈹?  鈹?  鈹?鈹?  鈹?  鈹溾攢鈹€ keyboard/               # Keyboard shortcuts
鈹?  鈹?  鈹?  鈹溾攢鈹€ shortcuts.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ KeybindingEngine.ts
鈹?  鈹?  鈹?  鈹斺攢鈹€ ShortcutCheatSheet.tsx
鈹?  鈹?  鈹?鈹?  鈹?  鈹斺攢鈹€ history/                # Undo/Redo
鈹?  鈹?      鈹溾攢鈹€ HistoryManager.ts
鈹?  鈹?      鈹斺攢鈹€ store/
鈹?  鈹?          鈹斺攢鈹€ historyStore.ts
鈹?  鈹?鈹?  鈹溾攢鈹€ requirements/           # Requirements Agent (Brief->Plan->Pipeline)
鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹溾攢鈹€ RequirementsPanel.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ RequirementsInitForm.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ RequirementsChat.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ RequirementsBrief.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ RequirementsPlan.tsx
鈹?  鈹?  鈹?  鈹斺攢鈹€ StatusBadge.tsx
鈹?  鈹?  鈹溾攢鈹€ store/
鈹?  鈹?  鈹?  鈹斺攢鈹€ requirementsStore.ts
鈹?  鈹?  鈹斺攢鈹€ hooks/
鈹?  鈹?      鈹斺攢鈹€ useRequirements.ts
鈹?  鈹?鈹?  鈹溾攢鈹€ pipeline-admin/         # Pipeline monitoring dashboard
鈹?  鈹?  鈹溾攢鈹€ components/
鈹?  鈹?  鈹?  鈹溾攢鈹€ QueuePanel.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ BatchPanel.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ StatsPanel.tsx
鈹?  鈹?  鈹?  鈹溾攢鈹€ LlmCostPanel.tsx
鈹?  鈹?  鈹?  鈹斺攢鈹€ TraceViewer.tsx
鈹?  鈹?  鈹斺攢鈹€ store/
鈹?  鈹?      鈹斺攢鈹€ pipelineAdminStore.ts
鈹?  鈹?鈹?  鈹溾攢鈹€ services/                   # API service layer
鈹?  鈹?  鈹溾攢鈹€ api/
鈹?  鈹?  鈹?  鈹溾攢鈹€ client.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ pipelineApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ renderApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ materialApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ personaApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ assetApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ animationApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ toolApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ pluginApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ projectApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ templateApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ typeMakerApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ editApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ sttApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ requirementsApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ fontApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ visionApi.ts
鈹?  鈹?  鈹?  鈹溾攢鈹€ modelTestApi.ts
鈹?  鈹?  鈹?  鈹斺攢鈹€ webhookApi.ts
鈹?  鈹?  鈹溾攢鈹€ websocket/
鈹?  鈹?  鈹?  鈹溾攢鈹€ wsClient.ts
鈹?  鈹?  鈹?  鈹斺攢鈹€ sseClient.ts
鈹?  鈹?  鈹斺攢鈹€ localStorage/
鈹?  鈹?      鈹斺攢鈹€ projectCache.ts
鈹?  鈹?鈹?  鈹溾攢鈹€ hooks/
鈹?  鈹?  鈹溾攢鈹€ useMediaFile.ts
鈹?  鈹?  鈹溾攢鈹€ useFileDrop.ts
鈹?  鈹?  鈹溾攢鈹€ useDebounce.ts
鈹?  鈹?  鈹溾攢鈹€ useThrottle.ts
鈹?  鈹?  鈹斺攢鈹€ useKeyboardShortcut.ts
鈹?  鈹?鈹?  鈹溾攢鈹€ lib/
鈹?  鈹?  鈹溾攢鈹€ time.ts
鈹?  鈹?  鈹溾攢鈹€ math.ts
鈹?  鈹?  鈹溾攢鈹€ color.ts
鈹?  鈹?  鈹溾攢鈹€ uuid.ts
鈹?  鈹?  鈹斺攢鈹€ format.ts
鈹?  鈹?鈹?  鈹溾攢鈹€ types/
鈹?  鈹?  鈹溾攢鈹€ timeline.ts
鈹?  鈹?  鈹溾攢鈹€ asset.ts
鈹?  鈹?  鈹溾攢鈹€ animation.ts
鈹?  鈹?  鈹溾攢鈹€ persona.ts
鈹?  鈹?  鈹溾攢鈹€ pipeline.ts
鈹?  鈹?  鈹斺攢鈹€ plugin.ts
鈹?  鈹?鈹?  鈹溾攢鈹€ constants/
鈹?  鈹?  鈹溾攢鈹€ theme.ts
鈹?  鈹?  鈹溾攢鈹€ keyboard.ts
鈹?  鈹?  鈹溾攢鈹€ presets.ts
鈹?  鈹?  鈹斺攢鈹€ limits.ts
鈹?  鈹?鈹?  鈹斺攢鈹€ i18n/
鈹?      鈹溾攢鈹€ zh-CN/translation.json
鈹?      鈹溾攢鈹€ en/translation.json
鈹?      鈹斺攢鈹€ config.ts
鈹?鈹溾攢鈹€ plugins/                        # Third-party frontend plugins
鈹溾攢鈹€ e2e/
鈹?  鈹溾攢鈹€ fixtures/
鈹?  鈹斺攢鈹€ tests/
鈹溾攢鈹€ index.html
鈹溾攢鈹€ package.json
鈹溾攢鈹€ tsconfig.json
鈹溾攢鈹€ vite.config.ts
鈹溾攢鈹€ tailwind.config.ts
鈹斺攢鈹€ README.md
```

### 2.3 Core Data Model (Frontend TypeScript)

These types **exactly mirror** the backend Python Pydantic models in `clipwright/schema/timeline.py`:

```typescript
// types/timeline.ts

export enum ClipKind {
  VIDEO = 'video',
  AUDIO = 'audio',
  TEXT = 'text',
  IMAGE = 'image',
  CAPTION = 'caption',
  SHAPE = 'shape',
  WAVEFORM = 'waveform',
  ANIMATION = 'animation',
}

export enum TransitionType {
  HARD_CUT = 'hard_cut',
  FADE = 'fade',
  DISSOLVE = 'dissolve',
  GLITCH = 'glitch',
  PIXEL_DISSOLVE = 'pixel_dissolve',
  SLIDE = 'slide',
  WIPE = 'wipe',
}

export enum ImageFit {
  COVER = 'cover',
  CONTAIN = 'contain',
  FREE = 'free',
}

export enum TextAlign {
  LEFT = 'left',
  CENTER = 'center',
  RIGHT = 'right',
}

export interface Keyframe {
  time: number;
  properties: Record<string, any>;
}

export interface Clip {
  id: string;
  kind: ClipKind;
  asset_id: string;
  track_id: string;
  start_sec: number;
  duration_sec: number;
  source_offset_sec: number;
  speed: number;
  volume: number;
  opacity: number;
  image_fit?: ImageFit;
  image_rect?: { x: number; y: number; w: number; h: number };
  text?: string;
  font?: string;
  font_size?: number;
  font_color?: string;
  text_align?: TextAlign;
  transition_in?: string;
  transition_out?: string;
  transition_duration_sec?: number;
  shape?: 'rect' | 'ellipse';
  fill?: string;
  bar_count?: number;
  bar_width?: number;
  keyframes: Keyframe[];
  metadata: Record<string, any>;
}

export interface Track {
  id: string;
  name: string;
  kind: ClipKind;
  index: number;
  clips: Clip[];
  locked: boolean;
  muted: boolean;
}

export interface Timeline {
  id: string;
  width: number;
  height: number;
  fps: number;
  duration_sec: number;
  tracks: Track[];
}
```

---

## 3 Pages & Routes [VERIFIED]

### 3.1 Route Table

All pages except the landing page are lazy-loaded via `React.lazy()` for route-level code splitting (see `src/router.tsx` which uses flat `createRoute()` API).

| Route | Page | Layout | Description |
|-------|------|--------|-------------|
| Route | Page | Layout | Lazy | Description |
|-------|------|--------|:----:|-------------|
| `/` | HomePage | StandardLayout | | All projects, create/open |
| `/editor/$projectId` | EditorPage | EditorLayout | ✓ | 4-panel editor, app core; embeds RequirementsAgent (Brief→Plan→Pipeline) and post-pipeline dialogue editing |
| `/projects` | ProjectsPage | StandardLayout | ✓ | Project list |
| `/export` | ExportPage | StandardLayout | ✓ | Render params, render queue with SSE progress |
| `/persona` | PersonaPage | StandardLayout | ✓ | List, create, version mgmt |
| `/persona/$personaId` | PersonaDetailPage | StandardLayout | ✓ | Single persona editing (YAML/Prompt/RAG/Exemplar tabs) |
| `/persona/forge` | PersonaForgePage | StandardLayout | ✓ | 3-step wizard (describe→questions→review) |
| `/voice` | VoicePage | StandardLayout | ✓ | Voice cloning & records |
| `/settings` | SettingsPage | StandardLayout | ✓ | Shortcuts, appearance, i18n, account |
| `/settings/models` | ModelsPage | StandardLayout | ✓ | LLM/Embed/Rerank test panel |
| `/settings/fonts` | FontsPage | StandardLayout | ✓ | System font list, resolve, default |
| `/settings/tools` | ToolsPage | StandardLayout | ✓ | Tool & Skill Admin |
| `/settings/plugins` | PluginsPage | StandardLayout | ✓ | Plugin Admin |
| `/settings/type-maker` | TypeMakerPage | StandardLayout | ✓ | Create/edit/duplicate video types |
| `/settings/templates` | TemplatesPage | StandardLayout | ✓ | Template Manager |
| `/settings/webhooks` | WebhooksPage | StandardLayout | ✓ | Webhook Settings |
| `/pipeline-admin` | PipelineAdminPage | StandardLayout | ✓ | Pipeline Monitor |
| `/help` | HelpPage | StandardLayout | ✓ | Usage guide, video tutorials |

### 3.2 EditorLayout Wireframe

```
+-----------------------------------------------------------------------------+
| Logo | Project: xxx v | Persona: Zam v | Type: Knowledge v | Cog icon      |
+----------+------------------------------+-----------+----------------------+
|          |                              |           |                      |
|          |      Preview Canvas          |  Agent    |   Properties         |
|          |       (16:9 ratio)           |  Co-pilot |   Panel              |
| Asset    |                              |  Panel    |                      |
| Panel    |  [|<] [>] [||]  00:12:34    |           |  Transform:          |
|          |  1080p v  Fullscreen [ ]    |  Messages |   position, scale    |
| [AI     ]|                              |  ...      |   rotation, opacity  |
| [Match  ]|                              |           |                      |
| [-------]|                              |  Suggest  |  Text Style:         |
| [Library]|                              |  [Accept] |   font, size, color  |
| [-------]|                              |  [Replace]|                      |
| [History]|                              |  [Ignore] |  Transition:         |
|          |                              |           |   type, duration     |
| Search.. |                              |  Global:  |                      |
|          |                              |  [Generate]|  Keyframes:          |
| [Img1]  |------------------------------|  [Analyze]|   property list      |
| [Img2]  |     Multi-track Timeline      |           |                      |
| [Img3]  |  +--+-------------------------|           |                      |
| [Img4]  |  |V1|[====A====][=====B=====] |           |                      |
|          |  |V2|  [===Text Anim===]      |           |                      |
|          |  |A1|[========BGM===========]|           |                      |
|          |  |A2|  [====VO TTS======]     |           |                      |
|          |  +--+-------------------------|           |                      |
|          |  |<-playhead                   |           |                      |
|          |  [00:00]---[00:30]--[01:00]---|           |                      |
|   drag/  |        |==zoom slider==|       |           |                      |
|   dbl-clk|                              |           |                      |
+----------+------------------------------+-----------+----------------------+
| Status: 30fps | 1920x1080 | Duration: 10:32 | Render: idle            |
+-----------------------------------------------------------------------------+
```

**Default panel widths (resizable):**
- Asset Panel: 240px (collapsible)
- Properties Panel: 280px (collapsible)
- Agent Panel: 300px (collapsible)
- Center (Preview + Timeline): remaining space

---

## 4 State Management [VERIFIED]

### 4.1 Zustand Store Overview

10 stores defined in `src/stores/`, all using `create<T>()`:

| Store | Responsibility | Key State |
|-------|---------------|-----------|
| `timelineStore` | Timeline core data | tracks[], clips[], duration, fps |
| `selectionStore` | Current selection | selectedClipIds[], playheadSec |
| `agentStore` | Agent co-pilot state | pipelineStatus, phase, suggestions[], agentTimeline |
| `assetStore` | Asset library data | assets[], searchQuery, filters |
| `previewStore` | Preview playback | isPlaying, currentTime, volume, loop, shuttle |
| `workspaceStore` | Panel layout (localStorage persisted) | panelWidths, visiblePanels |
| `settingsStore` | Global settings (localStorage) | theme, language, shortcuts, apiBaseUrl |
| `projectStore` | Project metadata | projectId, name, personaId, pluginId |
| `historyStore` | Undo/redo (deep-clone based) | undoStack[], redoStack[] |
| `voiceStore` | Voice cloning | upload state, clone progress, records[] |

### 4.2 timelineStore Detailed Design

```typescript
interface TimelineState {
  id: string;
  width: number;       // 1920
  height: number;      // 1080
  fps: number;         // 30
  duration_sec: number;
  tracks: Track[];
  markers: Marker[];

  // Track operations
  addTrack(kind: ClipKind, name?: string, index?: number): string;
  removeTrack(trackId: string): void;
  reorderTrack(trackId: string, newIndex: number): void;
  lockTrack(trackId: string): void;
  muteTrack(trackId: string): void;

  // Clip operations
  addClip(trackId: string, clip: Clip, atSec?: number): string;
  addClips(trackId: string, clips: Clip[]): string[];
  removeClip(clipId: string): void;
  moveClip(clipId: string, toTrackId: string, newStartSec: number): void;
  resizeClip(clipId: string, newDurationSec: number, edge: 'start' | 'end'): void;
  splitClip(clipId: string, atSec: number): [string, string];
  duplicateClip(clipId: string): string;
  replaceClipAsset(clipId: string, newAssetId: string): void;

  // Property updates
  updateClipProperty<K extends keyof Clip>(clipId: string, property: K, value: Clip[K]): void;
  updateClipKeyframes(clipId: string, keyframes: Keyframe[]): void;

  // Batch operations (undoable)
  batchUpdate(operations: TimelineOperation[]): void;

  // Ripple Edit
  rippleDelete(clipId: string): void;
  rippleInsert(trackId: string, clip: Clip, atSec: number): void;

  // Import/Export
  importFromAgent(timeline: Timeline): void;
  exportTimeline(): Timeline;
  mergeAgentTimeline(agentTimeline: Timeline): void;

  // Validation
  validateTimeline(): ValidationResult[];
}

// Command pattern for undo/redo
interface TimelineOperation {
  type: 'ADD_CLIP' | 'REMOVE_CLIP' | 'MOVE_CLIP' | 'RESIZE_CLIP'
      | 'UPDATE_PROPERTY' | 'SPLIT_CLIP' | 'ADD_TRACK' | 'REMOVE_TRACK'
      | 'BATCH';
  payload: any;
  inverse: () => TimelineOperation;
  timestamp: number;
}
```

### 4.3 selectionStore Detailed Design

```typescript
interface SelectionState {
  selectedClipIds: Set<string>;
  selectedTrackId: string | null;
  playheadSec: number;
  rangeSelection: { startSec: number; endSec: number } | null;
  focusTarget: 'timeline' | 'preview' | 'properties' | null;

  selectClip(clipId: string, additive?: boolean): void;
  deselectClip(clipId: string): void;
  selectAllClips(): void;
  clearSelection(): void;
  selectClipsInRange(startSec: number, endSec: number, trackId?: string): void;
  setPlayhead(sec: number): void;
  stepPlayhead(frames: number, direction: 1 | -1): void;
}
```

### 4.4 historyStore (Undo/Redo)

```typescript
interface HistoryState {
  undoStack: TimelineOperation[];
  redoStack: TimelineOperation[];
  maxSize: number;

  push(operation: TimelineOperation): void;
  undo(): void;
  redo(): void;
  canUndo: boolean;
  canRedo: boolean;
  clear(): void;
  snapshot(label?: string): void;
  jumpToSnapshot(label: string): void;
}
```

---

## 5 Component Tree

### 5.1 EditorPage Full Component Tree

```
EditorPage
  EditorLayout
    TopToolbar
      ProjectSwitcher
      PersonaSelector
      CategoryPluginSelector
      QuickActionButtons [Save, Undo, Redo, Export]

    LeftPanel (collapsible, resizable)
      AssetPanel
        TabBar [AI Match | Library | History]
        SearchBar + FilterBar
        AiMatchPanel
          MatchResultCard[] [Thumbnail, Score, Reason, AddButton]
        AssetGrid
          AssetCard[] [Thumbnail, Name, Duration, Tags, ContextMenu]
        HistoryPanel
        UploadArea (drag-and-drop)

    CenterPanel (flex: 1)
      PreviewContainer
        PreviewCanvas
          VideoLayer (WebCodecs decoded frame)
          TextOverlay
          ShapeOverlay
          SafeFrameOverlay
        PreviewControls
          PlayPauseButton, StepBackward, StepForward
          TimecodeDisplay, VolumeSlider
          ResolutionSwitcher, FullscreenButton
        PreviewTransformControls

      TimelineRoot
        TimelineToolbar
          ZoomSlider, SnapToggle, AutoRippleToggle
          AddTrackButton, TimelineActions
        TimelineCanvas (Canvas 2D)
          GridLayer
          TrackLayer[] -> ClipLayer[]
            ThumbnailStrip, WaveformStrip, TextLabel
            TransitionIndicator, KeyframeDot[]
            ResizeHandle, SelectionHighlight
          PlayheadLayer
          MarkerLayer
          SelectionRectLayer
          SnapGuideLayer
        TrackHeaderList
          TrackHeader[] [Icon, Name, Lock, Mute, Collapse]
        TimelineScrollbar

    RightTopPanel (collapsible)
      AgentPanel
        AgentChat [MessageList, ChatInput, TypingIndicator]
        AgentSuggestion[] [Title, Detail, TimelineDiff, Accept/Replace/Ignore]
        AgentProgress [StepIndicator[], OverallProgress]
        QuickActions

    RightBottomPanel (collapsible)
      PropertyPanel
        EmptyState ("Select an element on the timeline")
        VideoProperties [Transform, Trim, Speed, Transition]
        TextProperties [Content, Font, Size, Color, Align, Animation]
        AudioProperties [Volume, FadeInOut, Ducking]
        KeyframeSection [PropertyList, KeyframeEditorPopup]
        EffectsSection

    StatusBar
      ProjectInfo (fps/resolution)
      DurationDisplay
      ZoomLevel
      RenderProgressIndicator
```

---

## 6 Multi-track Timeline Engine

This is the most complex module in the entire frontend.

### 6.1 Canvas Architecture

```
TimelineCanvas (main Canvas element)
    |
    +-- ViewportTransform { zoom, scrollX, scrollY }
    |     screenX = (timelineX - scrollX_ms * zoom_pxPerMs)
    |     timelineX_ms = screenX / zoom_pxPerMs + scrollX_ms
    |
    +-- Layer Stack (render order)
        1. GridLayer        - background grid
        2. TrackBgLayer     - track background colors
        3. ClipLayer        - clip bodies (thumbnails/waveforms/labels/keyframes)
        4. TransitionLayer  - transition icons
        5. SelectionLayer   - selection highlights
        6. ResizeHandleLayer - trim handles
        7. PlayheadLayer    - playhead indicator
        8. RulerLayer       - time ruler (fixed, doesn't scroll)
        9. MarkerLayer      - markers
       10. SnapGuideLayer   - snap alignment guides
       11. SelectionRectLayer - range selection rectangle

    +-- OffscreenCanvas (background rendering)
        +-- ThumbnailCache - pre-rendered thumbnail strips
```

### 6.2 Coordinate System & Zoom Levels

```typescript
interface ViewportState {
  zoom: number;        // px per ms
  zoomLevel: number;   // -5..+20
  scrollX: number;     // horizontal scroll offset (ms)
  scrollY: number;     // vertical scroll offset (px)
  trackHeight: number; // 48px default
  trackGap: number;    // 2px default
  trackHeaderWidth: number; // 120px default
  rulerHeight: number; // 24px default
}

const ZOOM_LEVELS = [
  { level: -5, pxPerSec: 2 },
  { level: -4, pxPerSec: 5 },
  { level: -3, pxPerSec: 10 },
  { level: -2, pxPerSec: 25 },
  { level: -1, pxPerSec: 50 },
  { level:  0, pxPerSec: 100 },   // default
  { level:  1, pxPerSec: 200 },
  { level:  2, pxPerSec: 400 },
  { level:  3, pxPerSec: 800 },
  { level:  4, pxPerSec: 1600 },
  { level:  5, pxPerSec: 3200 },
  { level:  6, pxPerSec: 6400 },
  { level:  7, pxPerSec: 10000 },  // frame-level: 3ms/px
];
```

### 6.3 Rendering Pipeline (Core)

```typescript
class TimelineCanvasEngine {
  private ctx: CanvasRenderingContext2D;
  private offscreen: OffscreenCanvas;
  private rafId: number;
  private dirty: boolean = true;
  private dirtyRects: Rect[] = [];

  renderLoop() {
    if (!this.dirty) return;
    this.ctx.save();
    // Clear dirty rects only
    for (const rect of this.dirtyRects) {
      this.ctx.clearRect(rect.x, rect.y, rect.w, rect.h);
    }
    // Apply viewport transform
    this.ctx.translate(
      this.trackHeaderWidth - this.viewport.scrollX * this.viewport.zoom, 0
    );
    const visibleRange = this.getVisibleRange();
    // Layer-by-layer rendering (viewport-culled)
    this.renderGrid(visibleRange);
    this.renderTracks(visibleRange);
    this.renderClips(visibleRange);
    this.renderTransitions(visibleRange);
    this.renderSelection(visibleRange);
    // Fixed layers (no scroll)
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.renderPlayhead();
    this.renderRuler();
    this.renderSnapGuides();
    this.ctx.restore();
    this.dirty = false;
    this.dirtyRects = [];
  }

  markDirty(rect?: Rect) {
    this.dirty = true;
    if (rect) this.dirtyRects.push(rect);
  }
}
```

### 6.4 Drag & Interaction Handler

```typescript
class DragHandler {
  private dragState: DragState | null = null;

  onMouseDown(e: MouseEvent, hitTarget: HitTestResult) {
    switch (hitTarget.type) {
      case 'clip-body':
        this.dragState = {
          type: 'move-clip', clipId: hitTarget.clipId,
          originSec: hitTarget.clipStartSec, originTrack: hitTarget.trackId,
        };
        break;
      case 'clip-resize-start':
      case 'clip-resize-end':
        this.dragState = {
          type: 'resize', clipId: hitTarget.clipId,
          edge: hitTarget.type === 'clip-resize-start' ? 'start' : 'end',
        };
        break;
      case 'playhead':
        this.dragState = { type: 'scrub' };
        break;
      case 'empty-area':
        this.dragState = { type: 'range-select', startSec: this.xToSec(e.clientX) };
        break;
      case 'keyframe':
        this.dragState = {
          type: 'move-keyframe', clipId: hitTarget.clipId,
          keyframeIndex: hitTarget.keyframeIndex,
        };
        break;
    }
    document.addEventListener('mousemove', this.onMouseMove);
    document.addEventListener('mouseup', this.onMouseUp);
  }

  onMouseMove(e: MouseEvent) {
    if (!this.dragState) return;
    const dx = e.clientX - this.dragState.originX;
    const dtSec = dx / this.viewport.zoom / 1000;

    switch (this.dragState.type) {
      case 'move-clip': {
        const snapResult = snapUtils.snapMove(
          this.dragState.originSec + dtSec,
          this.getAllSnapTargets(), this.viewport.zoom,
        );
        this.timelineStore.moveClip(
          this.dragState.clipId,
          this.findDropTrack(e.clientY)?.id || this.dragState.originTrack,
          snapResult.snappedSec,
        );
        break;
      }
      // ... other cases
    }
  }
}
```

### 6.5 Snapping System

```typescript
interface SnapTarget {
  type: 'clip-edge' | 'playhead' | 'marker' | 'grid';
  positionSec: number;
  priority: number; // Clip edges=2, Markers=1.5, Playhead=1, Grid=0
}

const SNAP_THRESHOLD_PX = 8;

function snapMove(proposedSec: number, targets: SnapTarget[], zoom: number): SnapResult {
  let best = { snappedSec: proposedSec, snapped: false, distancePx: Infinity };
  for (const target of targets) {
    const distancePx = Math.abs(proposedSec - target.positionSec) * 1000 * zoom;
    if (distancePx < SNAP_THRESHOLD_PX && distancePx < best.distancePx) {
      best = { snappedSec: target.positionSec, snapped: true, distancePx };
    }
  }
  return best;
}
```

### 6.6 Performance Optimizations

1. **Virtualized rendering**: Only render tracks/clips within visible viewport
2. **Dirty rects**: `markDirty(rect)` for partial redraws only
3. **OffscreenCanvas**: Thumbnail strip pre-rendering in Worker thread
4. **Frame cache**: LRU cache of rendered thumbnail frames (max 500)
5. **requestAnimationFrame batching**: Merge consecutive operations into single rAF
6. **Web Worker for waveforms**: Audio waveform computation off main thread
7. **Layered Canvases**: Separate static layers from dynamic ones
8. **Event throttling**: mousemove limited to 16ms (60fps)

---

## 7 Video Preview Engine

### 7.1 WebCodecs Pipeline

```typescript
class WebCodecsPipeline {
  private videoDecoder: VideoDecoder;
  private audioDecoder: AudioDecoder;
  private canvasCtx: CanvasRenderingContext2D;
  private audioCtx: AudioContext;
  private compositor: TimelineCompositor;
  private isPlaying: boolean = false;
  private currentTime: number = 0;

  renderFrame(timeSec: number): void {
    const layers = this.compositor.getVisibleLayers(timeSec);
    this.canvasCtx.clearRect(0, 0, this.timeline.width, this.timeline.height);
    // Bottom-to-top compositing
    for (const layer of layers.sort((a,b) => a.zIndex - b.zIndex)) {
      switch (layer.type) {
        case 'video': {
          const frame = this.getDecodedFrame(layer.clipId, timeSec);
          if (frame) {
            this.canvasCtx.save();
            this.applyTransform(layer.transform);
            this.canvasCtx.drawImage(frame, 0, 0);
            this.canvasCtx.restore();
            frame.close();
          }
          break;
        }
        case 'text': {
          const props = layer.clip;
          this.canvasCtx.font = `${props.font_size}px ${props.font}`;
          this.canvasCtx.fillStyle = props.font_color || '#ffffff';
          this.applyAnimations(layer, timeSec);
          this.canvasCtx.fillText(props.text || '', layer.x, layer.y);
          break;
        }
        case 'image':
          this.canvasCtx.drawImage(layer.image, layer.x, layer.y, layer.w, layer.h);
          break;
        case 'shape':
          this.renderShape(layer);
          break;
      }
    }
  }

  play() {
    this.isPlaying = true;
    this.lastFrameTime = performance.now();
    requestAnimationFrame(() => this.playLoop());
  }
}
```

### 7.2 TimelineCompositor

```typescript
class TimelineCompositor {
  getVisibleLayers(timeSec: number): RenderLayer[] {
    const layers: RenderLayer[] = [];
    for (const track of this.timeline.tracks) {
      for (const clip of track.clips) {
        if (timeSec >= clip.start_sec && timeSec <= clip.start_sec + clip.duration_sec) {
          const clipTime = timeSec - clip.start_sec;
          const sourceTime = clip.source_offset_sec + clipTime / clip.speed;
          layers.push({
            type: mapKindToLayerType(clip.kind),
            clipId: clip.id, clip, sourceTime,
            zIndex: track.index,
            transform: this.computeTransform(clip, clipTime),
            animations: this.resolveAnimations(clip, clipTime),
          });
        }
      }
    }
    return layers;
  }

  private computeTransform(clip: Clip, clipTime: number): Transform2D {
    const interpolated = KeyframeInterpolator.interpolateProperties(
      clip.keyframes, clipTime / clip.duration_sec,
    );
    return {
      x: interpolated.positionX ?? clip.image_rect?.x ?? 0,
      y: interpolated.positionY ?? clip.image_rect?.y ?? 0,
      scaleX: interpolated.scaleX ?? 1,
      scaleY: interpolated.scaleY ?? 1,
      rotation: interpolated.rotation ?? 0,
      opacity: interpolated.opacity ?? clip.opacity,
    };
  }
}
```

### 7.3 Fallback Strategy

```
WebCodecs API available?
  YES -> Hardware-accelerated decode, low latency (<16ms/frame)
  NO  -> HTML5 VideoElement + currentTime seek
         -> Supported: software decode (higher latency but usable)
         -> Not supported: Frame proxy mode (backend /api/render/thumbnail)
```

---

## 8 Asset Library System

### 8.1 AI Match Panel

Backend API: `POST /api/material/search` (semantic search) + `POST /api/agent/suggest/clip`

```typescript
interface AiMatchResult {
  asset: MaterialAsset;
  score: number;          // 0-1
  matched_keywords: string[];
  source_name: string;
  scene_index: number;
  match_reason: string;   // LLM-generated reason
}

// Auto-trigger when playhead moves to a new scene
const { data: matches } = useQuery({
  queryKey: ['aiMatch', projectId, currentSceneIndex],
  queryFn: () => aiMatchApi.getSuggestions({
    projectId, sceneIndex: currentSceneIndex,
    sceneDescription: currentScene?.description,
    personaId: currentPersona?.id,
  }),
  enabled: !!currentScene,
  staleTime: 30_000,
});
```

### 8.2 Asset Upload

```typescript
<UploadArea
  accept="video/*,audio/*,image/*"
  maxSize={2 * 1024 * 1024 * 1024} // 2GB
  onFilesDrop={async (files) => {
    for (const file of files) {
      const assetId = await assetStore.uploadAsset(file, {
        onProgress: (pct) => console.log(`${file.name}: ${pct}%`),
      });
      // Trigger backend preprocessing (transcode proxy, scene detect, audio extract)
      await preprocessApi.start(assetId);
    }
  }}
/>
```

### 8.3 Asset Card Drag to Timeline

```typescript
function AssetCard({ asset }: { asset: MaterialAsset }) {
  const { setNodeRef, attributes, listeners, transform } = useDraggable({
    id: `asset-${asset.id}`,
    data: { asset, type: 'ASSET_CARD' },
  });
  // ... render card
}

function TimelineCanvas() {
  const { setNodeRef } = useDroppable({ id: 'timeline-drop-zone' });
  useDndMonitor({
    onDragEnd: (event) => {
      if (event.over?.id === 'timeline-drop-zone') {
        const asset = event.active.data.current.asset;
        const dropSec = xToSec(...); // Canvas coordinate conversion
        const dropTrack = findTrackAtY(...);
        timelineStore.addClip(dropTrack.id, createClipFromAsset(asset, dropSec));
      }
    },
  });
}
```
### 8.4 Vision Import (AI Image Recognition)

Backend API: `POST /api/vision/analyze` + `POST /api/vision/import`.

The AssetPanel includes a "Vision Import" button that opens a dialog for AI-powered image-to-library import. Users enter an image path, the backend analyzes it with an AI vision model, displays recognized content, and auto-imports to the library.

### 8.5 Material Source Multi-Select

The editor form includes a checkbox list of available material sources fetched via `GET /api/material/sources`. Selected source IDs are passed to the pipeline as `extra_params.material_source_ids`, constraining which sources the Material Agent searches during execution.

```typescript
function MaterialSourceSelector() {
  const { data: sources } = useQuery({
    queryKey: ['materialSources'],
    queryFn: () => materialApi.getSources(),
  });
  return sources?.map(s => (
    <label key={s.id}><input type="checkbox" value={s.id} /> {s.name}</label>
  ));
}
```

---

## 9 Property Panel Design

### 9.1 Context-Responsive Rendering

```typescript
function PropertyPanel() {
  const selectedClipIds = useSelectionStore(s => s.selectedClipIds);
  const clips = useTimelineStore(s =>
    s.tracks.flatMap(t => t.clips).filter(c => selectedClipIds.has(c.id))
  );

  if (clips.length === 0)
    return <EmptyState text="Select an element on the timeline to edit properties" />;
  if (clips.length > 1)
    return <MultiSelectProperties clips={clips} />;

  const clip = clips[0];
  return (
    <div className="property-panel">
      <PropertyHeader clip={clip} />
      <TransformControls clip={clip} />
      {clip.kind === ClipKind.VIDEO && <VideoProperties clip={clip} />}
      {clip.kind === ClipKind.IMAGE && <ImageProperties clip={clip} />}
      {clip.kind === ClipKind.TEXT && <TextProperties clip={clip} />}
      {clip.kind === ClipKind.AUDIO && <AudioProperties clip={clip} />}
      <TransitionSection clip={clip} />
      <KeyframeSection clip={clip} />
      <EffectsSection clip={clip} />
    </div>
  );
}
```

### 9.2 Keyframe Interaction Row

```typescript
function KeyframePropertyRow({ clipId, property, value }: {
  clipId: string; property: string; value: number;
}) {
  const playheadSec = useSelectionStore(s => s.playheadSec);
  const clip = useTimelineStore(s => getClipById(s, clipId))!;
  const clipTime = playheadSec - clip.start_sec;
  const existing = clip.keyframes.find(kf =>
    Math.abs(kf.time - clipTime / clip.duration_sec) < 0.005
  );

  return (
    <div className="keyframe-row">
      <span>{property}</span>
      <span>{value}</span>
      <button className={existing ? 'active' : ''}
        onClick={() => existing
          ? timelineStore.removeKeyframe(clipId, existing.time)
          : timelineStore.addKeyframe(clipId, clipTime/clip.duration_sec, {[property]: value})
        }>
        <DiamondIcon filled={!!existing} />
      </button>
      <button onClick={() => jumpToPrevKeyframe(clipId, property)}>Prev</button>
      <button onClick={() => jumpToNextKeyframe(clipId, property)}>Next</button>
    </div>
  );
}
```

---

## 10 Agent Co-pilot Panel

### 10.0 Requirements Agent: Two-Stage Pre-Pipeline Workflow

Before the pipeline starts, the Requirements Agent helps define the creative vision through conversation. This is a distinct phase BEFORE pipeline execution using these endpoints:

| Endpoint | Purpose |
|----------|---------|
| `POST /api/requirements/init` | Start session (topic, plugin, persona, script, audio) |
| `POST /api/requirements/chat` | Send message, get AI reply + creative_brief or production_plan |
| `GET /api/requirements/plan/{sessionId}` | Fetch production plan (polled every 2s) |
| `GET /api/requirements/session/{sessionId}` | Full session state for recovery/refresh |
| `POST /api/requirements/upload/{sessionId}` | Upload reference file |

**Two-stage flow:**
```
User enters topic/script + selects persona/plugin
    -> POST /api/requirements/init -> Agent starts
    -> Agent produces creative_brief { title, overview, target_audience, core_message,
         style_direction, structure_suggestion, duration_estimate, key_elements,
         special_requirements }
    -> User reviews brief -> clicks "Confirm"
    -> Agent produces production_plan (full Markdown with scenes, timelines, animations)
    -> User reviews plan with auto-generated TOC nav sidebar from headings
    -> clicks "Confirm & Produce" -> populates editor fields -> triggers pipeline
```

**9-state status badge:** gathering -> brief_ready -> brief_confirmed -> planning -> plan_ready -> plan_confirmed -> pipeline_running -> completed (or error/cancelled).

**Session recovery:** `localStorage` preserves sessionId. On reload, a banner offers "Resume session" via `GET /api/requirements/session/{id}`.

```typescript
interface RequirementsState {
  sessionId: string | null;
  status: 'idle' | 'gathering' | 'brief_ready' | 'brief_confirmed'
        | 'planning' | 'plan_ready' | 'plan_confirmed'
        | 'pipeline_running' | 'pipeline_done' | 'completed' | 'error';
  messages: RequirementMessage[];
  creativeBrief: CreativeBrief | null;
  productionPlan: ProductionPlan | null;
}
```

### 10.0b Video Mode Selector

Two production modes, selected before pipeline execution:

| Mode | Behavior |
|------|----------|
| `voiceover` | Script -> split to captions -> STT align with audio -> scene generation |
| `visual` | Each script line = one scene -> fixed durations -> no STT needed |

Passed as `extra_params.video_mode`. The mode selector is a dropdown in the editor's init form.

### 10.0c SSE Timeline Snapshot (Real-Time Intermediate Rendering)

During pipeline execution, the backend emits `timeline_snapshot` SSE events containing intermediate timeline state after each Agent completes. The frontend renders these **immediately** into the timeline view 鈥?this is the key UX differentiator for the Agent pipeline.

SSE event types handled by the frontend:
- `agent_start` / `agent_end` -> phase indicator update
- **`timeline_snapshot`** -> immediate timeline rendering (the critical event)
- `tool` / `llm` -> execution log entries
- `log` / `info` / `warning` -> real-time log stream
- `done` -> pipeline complete

The `AgentProgress` component includes a mini-timeline that updates incrementally as each snapshot arrives, giving users visual feedback throughout the 1-5 minute pipeline execution.

---

### 10.1 Three Agent Intervention Modes

| Mode | Trigger | Scope | API |
|------|---------|-------|-----|
| **Global Generate** | "Generate Draft" button | Topic -> full timeline | `POST /api/pipeline/run-v2` |
| **Local Replace** | Select region -> "Agent process" | Specific scene/empty region | `POST /api/pipeline/regenerate-scene/{id}/{idx}` |
| **Suggestion Mode** | Automatic (on playhead move) | Recommend assets for current scene | `POST /api/agent/suggest/clip` |

### 10.2 Agent State Flow

```typescript
type PipelinePhase =
  | 'idle' | 'structure' | 'material' | 'edit'
  | 'animation' | 'audio' | 'quality' | 'self_heal'
  | 'completed' | 'failed';

interface AgentState {
  pipelineId: string | null;
  phase: PipelinePhase;
  progress: number;           // 0-100
  agentTimeline: Timeline | null;
  suggestions: AgentSuggestion[];
  chatMessages: AgentChatMessage[];
  error: string | null;

  startPipeline(request: PipelineRequest): Promise<void>;
  acceptSuggestion(suggestionId: string): void;
  acceptAgentTimeline(): void;
  mergeAgentTimeline(): void;
  sendChatMessage(text: string): Promise<void>;
}
```

### 10.3 SSE Real-time Stream

```typescript
function useAgentStream(pipelineId: string) {
  useEffect(() => {
    const es = new EventSource(`${API_BASE}/api/pipeline/trace/stream/${pipelineId}`);
    es.addEventListener('agent_start', (e) => {
      const { agent_name } = JSON.parse(e.data);
      useAgentStore.getState().updatePhase(agent_name, 'running');
    });
    es.addEventListener('agent_complete', (e) => {
      const { agent_name, result } = JSON.parse(e.data);
      if (agent_name === 'edit_agent') {
        useAgentStore.getState().setAgentTimeline(result.timeline);
      }
    });
    es.addEventListener('agent_error', (e) => {
      const { agent_name, error } = JSON.parse(e.data);
      updatePhase(agent_name, 'failed', error);
    });
    es.addEventListener('pipeline_complete', () => es.close());
    es.addEventListener('self_heal', (e) => {
      const { redo_agent, reason } = JSON.parse(e.data);
      updatePhase('self_heal', `Rolling back to ${redo_agent}: ${reason}`);
    });
    return () => es.close();
  }, [pipelineId]);
}
```

### 10.4 Timeline Diff (Agent Suggestion vs Current)

```typescript
function TimelineDiff({ agentTL, currentTL }: { agentTL: Timeline; currentTL: Timeline }) {
  const diff = computeTimelineDiff(currentTL, agentTL);
  return (
    <div className="timeline-diff">
      <h4>Agent Suggested Changes</h4>
      {diff.addedClips.map(c => <div key={c.id} className="added">+ New: {c.id}</div>)}
      {diff.removedClips.map(c => <div key={c.id} className="removed">- Remove: {c.id}</div>)}
      {diff.modifiedClips.map(({current, proposed}) => (
        <div key={current.id} className="modified">
          ~ Modify: {current.id}
          {/* Field-by-field change list */}
        </div>
      ))}
      <div className="actions">
        <Button onClick={acceptAgentTimeline}>Accept All</Button>
        <Button variant="outline" onClick={mergeAgentTimeline}>Merge Selected</Button>
        <Button variant="ghost" onClick={rejectAgentTimeline}>Ignore</Button>
      </div>
    </div>
  );
}
```

---

## 11 Persona Management Module

### 11.1 Three-Component Editor

Each Persona has 3 components, each with its own editor tab:

```
Persona Detail Page (/persona/:personaId)
  Tab: Parameter Editor (YAML -> Visual Form)
    IdentityEditor     - tone, positioning, knowledge domains
    LanguageEditor     - academic density, slang ratio, forbidden patterns
    RhythmEditor       - cut profile, base shot duration
    VisualEditor       - palette, animation styles, transition weights
    AudioEditor        - BGM slots, voice clone, loudness target
    ConstraintsEditor  - max duration, source citation requirement

  Tab: Prompt Editor
    MarkdownEditor (left edit, right live preview)

  Tab: Knowledge Base (RAG)
    DocumentList, DocumentUpload (.md/.txt)
    IndexStatus (vector index state)
    SemanticSearch (test retrieval)

  Tab: Exemplar Layer
    ExemplarList (annotated clips)
    ExemplarUpload (upload clip + annotate)
    AutoAnnotate (backend PersonaForge)

  Tab: Inheritance & Versions
    InheritanceGraph (D3/React Flow tree)
    VersionHistory (rollback support)
    CompareView (two-version diff)
```

### 11.2 PersonaForge Wizard

```typescript
function PersonaForgeWizard() {
  const [step, setStep] = useState<'describe' | 'questions' | 'review'>('describe');
  const [answers, setAnswers] = useState<Record<string, string>>({});

  return (
    <WizardContainer currentStep={step}>
      {step === 'describe' && (
        <DescribeStep onSubmit={async (desc) => {
          // POST /api/persona/forge/from-prompt
          const persona = await personaForgeApi.fromPrompt({ description: desc });
          // Optionally go to dialogue mode
        }} />
      )}
      {step === 'questions' && (
        <DialogueStep
          questions={questions}
          onAnswer={(cat, field, answer) => {
            setAnswers(prev => ({ ...prev, [`${cat}.${field}`]: answer }));
          }}
          onFinish={() => setStep('review')}
        />
      )}
      {step === 'review' && (
        <ReviewStep persona={generatedPersona} onConfirm={saveAndNavigate} />
      )}
    </WizardContainer>
  );
}
```

### 11.2b Chat Forge (Conversational Persona Creation)

An alternative conversational flow for Persona creation using these dedicated endpoints:

| Endpoint | Purpose |
|----------|---------|
| `POST /api/persona/forge/chat/start` | Initiate chat session |
| `POST /api/persona/forge/chat/message` | Send message, get AI reply + persona_draft + progress |
| `POST /api/persona/forge/chat/knowledge` | Upload reference doc for chapter-by-chapter analysis |
| `POST /api/persona/forge/chat/commit` | Finalize and save the Persona |

The UI features chat-style messages (user/AI bubbles), **per-dimension progress bars** (identity, language, rhythm, visual, audio, constraints) showing 0-100% completion, a **live persona_draft preview panel** updating in real-time, and **knowledge file upload** that splits documents by H1 headings and analyzes each chapter sequentially.

```typescript
// personaStore.chatForge
interface ChatForgeState {
  sessionId: string | null;
  messages: ChatMessage[];
  personaDraft: Partial<ParameterLayer> | null;
  progress: { identity: number; language: number; rhythm: number;
              visual: number; audio: number; constraints: number; };
  knowledgeFiles: { name: string; chapters: number }[];
}
```

---

## 12 Animation & Keyframe Editor

### 12.1 Keyframe Interpolation Engine

```typescript
const EasingFunctions: Record<string, (t: number) => number> = {
  'linear':       t => t,
  'ease-in':      t => t * t,
  'ease-out':     t => t * (2 - t),
  'ease-in-out':  t => t < 0.5 ? 2*t*t : -1 + (4 - 2*t)*t,
  'ease-in-cubic': t => t * t * t,
  'ease-out-cubic': t => (--t) * t * t + 1,
  // ... 15+ Penner easing functions
};

class KeyframeInterpolator {
  static interpolateProperties(
    keyframes: Keyframe[],
    progress: number, // 0-1
    easing: string = 'linear',
  ): Record<string, number> {
    if (keyframes.length === 0) return {};
    if (keyframes.length === 1) return { ...keyframes[0].properties };

    const sorted = [...keyframes].sort((a, b) => a.time - b.time);
    if (progress <= sorted[0].time) return { ...sorted[0].properties };
    if (progress >= sorted.at(-1)!.time) return { ...sorted.at(-1)!.properties };

    // Binary search for surrounding keyframes
    let lo = 0, hi = sorted.length - 1;
    while (lo < hi - 1) {
      const mid = (lo + hi) >> 1;
      sorted[mid].time <= progress ? lo = mid : hi = mid;
    }

    const [prev, next] = [sorted[lo], sorted[hi]];
    const segmentDur = next.time - prev.time;
    const localT = segmentDur > 0 ? (progress - prev.time) / segmentDur : 0;
    const easedT = EasingFunctions[easing](localT);

    // Linear interpolation across all properties
    const result: Record<string, number> = {};
    const allProps = new Set([
      ...Object.keys(prev.properties), ...Object.keys(next.properties),
    ]);
    for (const prop of allProps) {
      const a = prev.properties[prop] ?? next.properties[prop] ?? 0;
      const b = next.properties[prop] ?? prev.properties[prop] ?? 0;
      result[prop] = a + (b - a) * easedT;
    }
    return result;
  }
}
```

---

## 13 Render & Export System

### 13.1 Export Presets

```typescript
export const EXPORT_PRESETS = {
  bilibili: { name: 'Bilibili 1080p', width: 1920, height: 1080, fps: 30, bitrate: '5M' },
  bilibili_4k: { name: 'Bilibili 4K', width: 3840, height: 2160, fps: 30, bitrate: '20M' },
  youtube: { name: 'YouTube 1080p', width: 1920, height: 1080, fps: 30, bitrate: '8M' },
  tiktok: { name: 'TikTok Vertical', width: 1080, height: 1920, fps: 30, bitrate: '4M' },
  weibo: { name: 'Weibo 720p', width: 1280, height: 720, fps: 25, bitrate: '3M' },
  custom: { name: 'Custom', width: 1920, height: 1080, fps: 30, bitrate: '5M' },
};
```

### 13.2 Render Queue with SSE Progress

```typescript
function RenderQueue() {
  const startRender = async (settings: ExportSettings) => {
    const timeline = timelineStore.getState().exportTimeline();
    const { task_id } = await renderApi.submitQueue({ timeline, settings });

    // SSE progress tracking
    const es = new EventSource(`${API_BASE}/api/render/queue/stream/${task_id}`);
    es.addEventListener('progress', (e) => {
      const { progress, phase, detail } = JSON.parse(e.data);
      updateRenderProgress(task_id, { progress, phase, detail });
    });
    es.addEventListener('complete', (e) => {
      toast.success('Render complete!');
      es.close();
    });
    es.addEventListener('error', () => es.close());
  };
  // Render: current queue list with progress bars per task
}
```

### 13.4 Script Split Mode (Pre-Pipeline)

Before pipeline starts, a split mode selector determines how script text is divided into caption segments:

| Mode | Behavior |
|------|----------|
| `period` | Split on sentence-ending punctuation (銆傦紒锛?!?) |
| `punctuation` | Split on all punctuation (锛屻€傦紱锛燂紒), preserving ?! at segment end |

Passed as `extra_params.split_mode` to the pipeline.

### 13.5 Audio File Probe

`GET /api/asset/probe?path=` retrieves media file metadata including duration, resolution, and codec. The duration is used to auto-fill the pipeline duration field when an audio file is uploaded.

### 13.6 Export Presets Dropdown

The export settings include a dropdown that auto-fills resolution, fps, and bitrate:

```typescript
const PRESETS = {
  'bilibili':  { res: '1920x1080', fps: 30, bitrate: '6M' },
  'youtube':   { res: '1920x1080', fps: 30, bitrate: '8M' },
  'tiktok':    { res: '1080x1920', fps: 30, bitrate: '4M' },
  '1080p':     { res: '1920x1080', fps: 30, bitrate: '5M' },
  '720p':      { res: '1280x720',  fps: 30, bitrate: '3M' },
  'custom':    { res: '1920x1080', fps: 30, bitrate: '5M' },
};
```

---

## 14 API Client & Data Flow

### 14.1 API Client Configuration

```typescript
// services/api/client.ts
import axios from 'axios';

let client: ReturnType<typeof axios.create>;

export function getApiClient() {
  if (!client) {
    const baseURL = useSettingsStore.getState().apiBaseUrl || 'http://localhost:8000';
    client = axios.create({ baseURL, timeout: 300_000 });
    client.interceptors.request.use((config) => {
      const token = useSettingsStore.getState().authToken;
      if (token) config.headers.Authorization = `Bearer ${token}`;
      return config;
    });
    client.interceptors.response.use(
      (res) => res,
      (err) => {
        if (err.response?.status === 401) { /* redirect login */ }
        if (err.response?.status === 503) toast.warning('Service busy, retry later');
        return Promise.reject(err);
      },
    );
  }
  return client;
}
```

### 14.2 Data Sync Strategy

```
User Edit -> Zustand Store --(debounce 3s)--> IndexedDB (auto-save)
              |
              +--(manual save)--> POST /api/project/save --> MongoDB

Agent Stream --> SSE --> Zustand Store --> Panel real-time refresh
Agent Result --> REST --> Zustand Store --> Timeline Diff trigger

Render Progress --> SSE --> Zustand Store --> Progress bar real-time
```

### 14.3 WebSocket Client

```typescript
class WsClient {
  private ws: WebSocket | null = null;
  private subs = new Map<string, Set<(data: any) => void>>();

  connect(url = 'ws://localhost:8000/ws') {
    this.ws = new WebSocket(url);
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      this.subs.get(msg.topic)?.forEach(cb => cb(msg.data));
    };
    this.ws.onclose = () => setTimeout(() => this.connect(url), 3000);
  }

  subscribe(topic: string, cb: (data: any) => void): () => void {
    if (!this.subs.has(topic)) this.subs.set(topic, new Set());
    this.subs.get(topic)!.add(cb);
    return () => this.subs.get(topic)?.delete(cb);
  }
}
```

---

## 15 Plugin System (Frontend)

### 15.1 Plugin SDK Interface

```typescript
export interface ClipwrightPlugin {
  id: string;
  name: string;
  version: string;
  type: 'panel' | 'tool' | 'asset-source' | 'animation' | 'effect' | 'theme';

  install(context: PluginContext): void;
  uninstall(): void;

  // Optional extension points
  registerPanels?(): PluginPanel[];
  registerToolbarButtons?(): ToolbarButton[];
  registerShortcuts?(): ShortcutBinding[];
  registerTimelineOverlays?(): TimelineOverlay[];
  registerAssetSource?(): AssetSourceDefinition;
  registerAnimations?(): AnimationDef[];
  registerExportPresets?(): ExportPreset[];
}

export interface PluginContext {
  stores: {
    timeline: () => TimelineState;
    selection: () => SelectionState;
    preview: () => PreviewState;
    asset: () => AssetState;
  };
  api: AxiosInstance;
  ui: {
    addPanel(panel: PluginPanel): void;
    removePanel(panelId: string): void;
    addToolbarButton(button: ToolbarButton): void;
    removeToolbarButton(buttonId: string): void;
    addShortcut(binding: ShortcutBinding): void;
  };
  events: {
    on(event: string, handler: (...args: any[]) => void): void;
    off(event: string, handler: (...args: any[]) => void): void;
    emit(event: string, ...args: any[]): void;
  };
}
```

### 15.2 Frontend Plugin Hook Points

```
Editor Lifecycle Hooks:
  onEditorLoad             - Editor ready
  onTimelineChange         - Timeline data changed
  onClipSelect             - Clip selected
  onClipDeselect           - Clip deselected
  onPlayheadMove           - Playhead moved
  onPreviewPlay            - Playback started
  onPreviewPause           - Playback paused
  onBeforeRender           - Before render
  onAfterRender            - Render complete
  onExport                 - On export
  onAgentSuggestion        - Agent suggestion received
  onAgentTimelineReady     - Agent timeline ready
```

---

## 16 Technology Decisions

### 16.1 Timeline Rendering

| Approach | Pros | Cons | Chosen |
|----------|------|------|:------:|
| Canvas 2D | Pixel control, high perf, infinite zoom | Self-implement all interactions | **YES** |
| HTML DOM | Accessible, CSS animations easy | Poor perf with 100+ clips | No |
| WebGL | Extreme framerate, 3D effects | High complexity, poor text | Plan B |
| SVG | Lossless vector zoom | Hard to implement complex interaction | No |
| Remotion | Declarative, React-friendly | Too abstract, not flexible enough | No |

### 16.2 State Management

| Library | Pros | Cons | Chosen |
|---------|------|------|:------:|
| Zustand | Lightweight, immutable, middleware, time-travel | Smaller community | **YES** |
| Redux Toolkit | Large ecosystem, DevTools | Boilerplate, bundle size | No |
| Jotai | Atomic, fine-grained | Hard for complex editor state | No |
| MobX | Reactive, auto-tracking | Too much magic, hard to debug | No |

### 16.3 Drag and Drop

| Library | Pros | Cons | Chosen |
|---------|------|------|:------:|
| DnD Kit | Lightweight, accessible, Canvas support | Need Canvas coordinate adaptation | **YES** |
| React DnD | Stable, mature | Stalled maintenance, old API | No |
| Pragmatic DnD | Excellent performance | Too new, sparse docs | No |

---

## 17 Phased Implementation Plan

### Phase 1: Scaffolding (Week 1-2) 鈥?MVP Foundation

**Goal**: Runnable project, basic layout visible

| ID | Task | Est. | Priority |
|----|------|------|:--------:|
| P1-1 | Init Vite + React 19 + TypeScript project | 0.5d | P0 |
| P1-2 | Configure Tailwind, Radix UI, shadcn/ui | 0.5d | P0 |
| P1-3 | Build Zustand base stores (project/timeline/selection) | 1d | P0 |
| P1-4 | Implement EditorLayout 4-panel (collapsible panels) | 1d | P0 |
| P1-5 | Implement TanStack Router | 0.5d | P0 |
| P1-6 | Implement API client (Axios + TanStack Query) | 1d | P0 |
| P1-7 | Implement Timeline JSON TypeScript types (matching backend) | 0.5d | P0 |
| P1-8 | Storybook setup (optional but recommended) | 0.5d | P1 |
| P1-9 | CI/CD (GitHub Actions + Vercel/Netlify) | 0.5d | P1 |
| P1-10 | Health Check Dashboard page with component status cards (mongodb/llm/ffmpeg/hyperframes/queue) | 0.5d | P1 |

### Phase 2: Core Timeline (Week 3-6) 鈥?Most Critical

**Goal**: Interactive multi-track timeline (PR baseline capability)

| ID | Task | Est. | Priority |
|----|------|------|:--------:|
| P2-1 | Canvas 2D engine skeleton (Layer Stack + Viewport) | 2d | P0 |
| P2-2 | Time ruler rendering (adaptive density) | 1d | P0 |
| P2-3 | Track background + track headers rendering | 1d | P0 |
| P2-4 | Clip rendering (video: color block, audio: waveform placeholder) | 1d | P0 |
| P2-5 | Zoom (scroll wheel) + Scroll (middle mouse/trackpad) | 1.5d | P0 |
| P2-6 | Playhead drag + position indicator | 1d | P0 |
| P2-7 | Clip select (click) + multi-select (Shift+Click) | 1d | P1 |
| P2-8 | Clip drag move (DnD Kit + Canvas coordinate conversion) | 2d | P0 |
| P2-9 | Clip drag trim (edge resize) | 1.5d | P0 |
| P2-10 | Clip split (Shortcut: S) | 1d | P1 |
| P2-11 | Snap system (clips/playhead/markers/grid) | 1.5d | P0 |
| P2-12 | Ripple Edit (Ripple Delete/Insert) | 1.5d | P1 |
| P2-13 | Track management (add/remove/reorder/lock/mute) | 1d | P0 |
| P2-14 | Markers system (add/edit/delete) | 1d | P2 |
| P2-15 | Range select (marquee select multiple clips) | 1d | P2 |
| P2-16 | Virtualized rendering (only visible viewport) | 2d | P1 |
| P2-17 | Dirty rect optimization + perf testing | 2d | P1 |
| P2-18 | Video thumbnail strip (basic, frame sampling) | 2d | P0 |
| P2-19 | Audio waveform rendering (ffmpeg wasm or backend pre-gen) | 2d | P2 |

### Phase 3: Preview & Assets (Week 7-9)

**Goal**: Real-time video preview, asset library workflow

| ID | Task | Est. | Priority |
|----|------|------|:--------:|
| P3-1 | WebCodecs video decode pipeline | 3d | P0 |
| P3-2 | Canvas compositing (multi-layer + transforms) | 2d | P0 |
| P3-3 | Playback controls (play/pause/frame-step/seek) | 1d | P0 |
| P3-4 | Audio sync playback (Web Audio API) | 1.5d | P1 |
| P3-5 | Preview zoom/fullscreen | 1d | P1 |
| P3-6 | Asset panel UI (3 tabs: AI/Assets/History) | 2d | P0 |
| P3-7 | Asset search + filter + pagination | 1d | P1 |
| P3-8 | Asset card DnD to timeline | 2d | P0 |
| P3-9 | Asset upload (drag/click) + progress | 1.5d | P1 |
| P3-10 | AI Match panel (backend semantic search) | 2d | P2 |
| P3-11 | Asset history usage records | 1d | P2 |
| P3-12 | HTML5 Video fallback (when WebCodecs unavailable) | 1.5d | P2 |
| P3-13 | Vision Import dialog (AI image analysis -> auto-import to library) | 1d | P2 |
| P3-14 | Material Source multi-select checkbox list in editor form | 0.5d | P2 |

### Phase 4: Properties & Animation (Week 10-12)

**Goal**: Clip property editing, keyframe animation editing

| ID | Task | Est. | Priority |
|----|------|------|:--------:|
| P4-1 | Property panel framework (context-sensitive by ClipKind) | 1d | P0 |
| P4-2 | Video properties (transform/trim/speed/transition) | 1.5d | P0 |
| P4-3 | Text properties (content/font/size/color/align) | 1d | P0 |
| P4-4 | Audio properties (volume/fade in-out) | 0.5d | P0 |
| P4-5 | Keyframe property row (add/delete/navigate) | 1.5d | P0 |
| P4-6 | Keyframe interpolator engine (Penner easing + linear) | 1.5d | P0 |
| P4-7 | Keyframe timeline embedded (below properties) | 2d | P1 |
| P4-8 | Easing curve editor (Bezier visual editing) | 2d | P2 |
| P4-9 | Animation preset panel (browse/preview/apply) | 1.5d | P1 |
| P4-10 | MG animation preview (Hyperframes HTML embed) | 2d | P2 |
| P4-11 | Preview transform handles (direct drag position/scale/rotate) | 2d | P1 |

### Phase 5: Agent Integration & Persona (Week 13-15)

**Goal**: Full Agent co-pilot panel, visual Persona management

| ID | Task | Est. | Priority |
|----|------|------|:--------:|
| P5-0a | Requirements Agent: Init Form + Session start | 1d | P0 |
| P5-0b | Requirements Agent: Chat UI (messages, Markdown, file upload) | 2d | P0 |
| P5-0c | Requirements Agent: Creative Brief display + confirm flow | 1d | P0 |
| P5-0d | Requirements Agent: Production Plan panel + nav sidebar + Confirm->Pipeline | 2d | P0 |
| P5-0e | Integration: confirm -> populate editor fields -> auto-trigger pipeline | 1d | P1 |
| P5-0f | SSE Timeline Snapshot incremental rendering in AgentProgress | 1.5d | P0 |
| P5-0g | Video Mode selector (voiceover/visual) in editor form | 0.5d | P1 |
| P5-1 | Agent panel UI (chat + suggestions + progress) | 2d | P0 |
| P5-2 | SSE stream Agent status listener | 2d | P0 |
| P5-3 | Global Agent call (topic -> Pipeline -> import timeline) | 2d | P0 |
| P5-4 | Local Agent call (select region -> reprocess) | 1.5d | P1 |
| P5-5 | Timeline Diff display (Agent suggestion vs current) | 2d | P0 |
| P5-6 | Accept/Merge/Reject Agent suggestions | 1d | P0 |
| P5-7 | Persona list + detail page | 1.5d | P1 |
| P5-8 | Persona parameter visual editor (YAML <-> Form two-way) | 2d | P1 |
| P5-9 | Persona Prompt editor (Markdown) | 1d | P2 |
| P5-10 | Persona knowledge base management (upload/index/search) | 1.5d | P2 |
| P5-11 | PersonaForge creation wizard (describe -> Q&A -> review) | 2d | P2 |
| P5-12 | Persona inheritance graph (D3/React Flow tree) | 1.5d | P2 |
| P5-13 | Chat Forge: conversational Persona creation with per-dimension progress bars + knowledge file chapter-by-chapter analysis | 3d | P1 |

### Phase 6: Export & System Polish (Week 16-18)

**Goal**: Render export, undo/redo, shortcuts, performance optimization

| ID | Task | Est. | Priority |
|----|------|------|:--------:|
| P6-1 | Export panel (preset selection + custom params) | 1.5d | P0 |
| P6-2 | Render queue UI + SSE progress | 2d | P0 |
| P6-3 | Download rendered result | 0.5d | P0 |
| P6-4 | Undo/Redo system (Command pattern, max 200) | 2d | P1 |
| P6-5 | Keybinding engine + all default bindings | 1.5d | P1 |
| P6-6 | Shortcut cheat sheet UI (Ctrl+/) | 1d | P2 |
| P6-7 | Project list page (create/open/delete) | 1d | P1 |
| P6-8 | Project auto-save (IndexedDB + periodic backend sync) | 1.5d | P1 |
| P6-9 | Panel layout persistence (resizable panel widths/visibility) | 1d | P2 |
| P6-10 | Performance optimization (virtual scroll, lazy load, code splitting) | 2d | P1 |
| P6-11 | Error boundaries + global error handling | 1d | P1 |
| P6-12 | i18n internationalization framework + zh/en translations | 2d | P2 |
| P6-13 | Dark/Light theme toggle | 1d | P2 |
| P6-14 | Model Test Panel (LLM/Embed/Rerank test with config display) | 1d | P2 |
| P6-15 | Font Configuration Panel (list/resolve/default font) | 0.5d | P2 |
| P6-16 | Plugin Admin Panel (list/load/unload/discover/capabilities) | 0.5d | P2 |
| P6-17 | Tool & Skill Admin Panels (list/execute/batch) | 1d | P2 |
| P6-18 | Type Maker Panel (CRUD for user-defined video types) | 0.5d | P2 |
| P6-19 | Template Manager Panel (CRUD/intro-outro/batch render) | 0.5d | P2 |
| P6-20 | Webhook Settings Panel (subscribe/unsubscribe/test) | 0.5d | P2 |
| P6-21 | Pipeline Admin Dashboard (Queue/Batch/Stats/LLM Cost/Span Trace Gantt) | 3d | P2 |
| P6-22 | Session Draft Auto-Recovery Banner (localStorage resume for Requirements Agent) | 0.5d | P2 |
| P6-23 | Script Split Mode selector + Audio Probe display in editor form | 0.5d | P2 |
| P6-24 | Prometheus Metrics card in Health Dashboard | 0.5d | P2 |

### Implementation Gantt Chart

```
Week:  1  2  3  4  5  6  7  8  9  10 11 12 13 14 15 16 17 18
P1:    鈻堚枅鈻堚枅鈻堚枅鈻堚枅
P2:          鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅
P3:                      鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅
P4:                                鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅
P5:                                          鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅
P6:                                                    鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅鈻堚枅
```

---

## 18 Testing Strategy

### 18.1 Test Pyramid

```
           +-------+
           |  E2E  |  Playwright (10-15 critical user flows)
           |  10%  |
          +---------+
          |Integration|  Vitest + Testing Library (component interactions, 30%)
          |   30%    |
         +-------------+
         |  Unit Tests |  Vitest (pure logic: engines/utils/stores, 60%)
         |    60%     |
         +-------------+
```

### 18.2 Unit Test Targets

| Module | What to Test | Est. Cases |
|--------|-------------|:----------:|
| `KeyframeInterpolator` | Interpolation calc, edge cases, all easing functions | 30+ |
| `snapUtils` | Snap logic, priorities, thresholds | 20+ |
| `timeUtils` | Time format conversion, frame math | 15+ |
| `rippleUtils` | Ripple edit computation | 10+ |
| `timelineStore` | All Actions, immutability | 40+ |
| `selectionStore` | Selection logic, multi-select | 15+ |
| `historyStore` | Undo/redo stack | 15+ |
| `EasingFunctions` | Each easing function f(0)=0, f(1)=1 | 20+ |
| `TimelineCompositor` | Layer ordering, transform computation | 10+ |

### 18.3 E2E Test Scenarios (Playwright)

| # | Scenario |
|:-:|----------|
| 1 | Create new project -> enter editor -> verify blank canvas |
| 2 | Upload video asset -> drag to timeline -> verify clip appears |
| 3 | Drag clip on timeline -> verify position updated |
| 4 | Trim clip edge -> verify duration changed |
| 5 | Split clip -> verify two independent clips |
| 6 | Add text track -> edit text properties -> preview verification |
| 7 | Select Persona -> click "Generate Draft" -> wait Agent -> verify timeline import |
| 8 | Accept Agent suggestion -> verify timeline updated |
| 9 | Set export params -> submit render -> wait completion -> download |
| 10 | Panel collapse/expand -> drag panel width -> verify persistence |

---

## 19 Performance Optimization

### 19.1 Key Performance Indicators

| Metric | Target | Measurement |
|--------|--------|-------------|
| Time to Interactive (TTI) | < 3s | Lighthouse |
| Timeline framerate (drag/scroll) | 60fps | Chrome DevTools FPS |
| First contentful paint (editor) | < 5s | Lighthouse / Web Vitals |
| Clip render (100 clips) | < 16ms/frame | Performance API |
| Asset card drag response | < 50ms | Manual testing |
| Memory usage (1h timeline) | < 500MB | Chrome Task Manager |
| First-screen JS (code-split) | < 500KB gzip | Bundle Analyzer |

### 19.2 Optimization Checklist

**Rendering:**
1. Canvas virtualization: only render viewport tracks/clips
2. Dirty rects: partial redraw only
3. Layered Canvases: separate static from dynamic layers
4. OffscreenCanvas + Web Worker: thumbnail pre-rendering
5. Frame cache: LRU strategy (max 500 frames)
6. rAF batching: merge consecutive operations

**Loading:**
7. Code splitting: by page (Editor ~2MB, Persona ~0.5MB)
8. Lazy components: `React.lazy` + `Suspense`
9. Font subsetting: use system fonts for text rendering
10. Image lazy load: IntersectionObserver for thumbnails
11. TanStack Query persistent cache: IndexedDB for search results

**Data:**
12. Web Worker: waveform/FFT computation off main thread
13. Large timeline pagination: on-demand thumbnail requests
14. Zustand selectors: avoid unnecessary re-renders
15. React.memo: memoize frequently rendered components

**Network:**
16. HTTP/2 multiplexing: batch asset requests
17. WebSocket over polling: Agent status real-time push
18. Gzip/Brotli: static assets
19. CDN: fonts, icons, static resources

---

## 20 Security & Permissions

### 20.1 Frontend Security

| Item | Mitigation |
|------|-----------|
| XSS | React auto-escaping + DOMPurify for user input |
| CSRF | Axios auto CSRF token |
| File upload limits | Frontend type/size validation + backend double-check |
| Plugin sandbox | iframe sandbox for third-party plugin isolation |
| CSP | Strict Content Security Policy via Vite config |
| Dependencies | `pnpm audit` in CI |

### 20.2 Permission Model (Reserved)

| Tier | Permissions |
|------|-------------|
| **Free** | 2 tracks, 3 Agent/mo, 720p export |
| **Pro** | Unlimited tracks, 30 Agent/mo, 1080p, Persona 4-layer |
| **Studio** | 150 Agent/mo, 4K, team collab, LoRA fine-tune |
| **Enterprise** | Unlimited, private deploy, API access |

---

## 21 Appendix

### A. Backend API Complete Map

| Category | Endpoint | Method | Frontend Module |
|----------|----------|--------|-----------------|
| Health | `/health` | GET | Global (startup check) |
| Pipeline | `/api/pipeline/run` | POST | Agent |
| Pipeline | `/api/pipeline/run-v2` | POST | Agent |
| Pipeline | `/api/pipeline/run-async` | POST | Agent |
| Pipeline | `/api/pipeline/trace/stream/{id}` | GET (SSE) | Agent |
| Pipeline | `/api/pipeline/result/{id}` | GET | Agent |
| Pipeline | `/api/pipeline/retry/{id}/{agent}` | POST | Agent |
| Pipeline | `/api/pipeline/regenerate-scene/{id}/{idx}` | POST | Agent |
| Pipeline | `/api/pipeline/step/{agent}` | POST | Agent |
| Pipeline | `/api/pipeline/predict-script` | POST | Quick Actions |
| Pipeline | `/api/pipeline/predict-material` | POST | Quick Actions |
| Pipeline | `/api/pipeline/stats` | GET | Agent |
| Render | `/api/render/start` | POST | Export |
| Render | `/api/render/queue` | POST | Export |
| Render | `/api/render/queue/stream/{id}` | GET (SSE) | Export |
| Render | `/api/render/status/{id}` | GET | Export |
| Render | `/api/render/download/{fn}` | GET | Export |
| Render | `/api/render/thumbnail` | GET | Timeline/Assets |
| Render | `/api/render/video` | GET | Proxy |
| Render | `/api/render/presets` | GET | Export |
| Project | `/api/project/save` | POST | Project |
| Project | `/api/project/load/{id}` | GET | Project |
| Project | `/api/project/list` | GET | Project |
| Project | `/api/project/delete/{id}` | DELETE | Project |
| Persona | `/api/persona/list` | GET | Persona |
| Persona | `/api/persona/create` | POST | Persona |
| Persona | `/api/persona/{id}` | GET | Persona |
| Persona | `/api/persona/forge/from-prompt` | POST | PersonaForge |
| Persona | `/api/persona/forge/from-script` | POST | PersonaForge |
| Persona | `/api/persona/forge/dialogue/generate-questions` | POST | PersonaForge |
| Persona | `/api/persona/forge/dialogue/build` | POST | PersonaForge |
| Persona | `/api/persona/forge/refine` | POST | PersonaForge |
| RAG | `/api/persona/{id}/rag/index` | POST | Persona |
| RAG | `/api/persona/{id}/rag/query` | POST | Persona |
| Tool | `/api/tool/list` | GET | Agent |
| Tool | `/api/tool/execute` | POST | Agent |
| Tool | `/api/tool/batch` | POST | Agent |
| Skill | `/api/skill/list` | GET | Agent |
| Skill | `/api/skill/execute` | POST | Agent |
| Material | `/api/material/search` | POST | Assets |
| Material | `/api/material/sources` | GET | Assets |
| Asset | `/api/asset/upload` | POST | Assets |
| Asset | `/api/asset/upload-batch` | POST | Assets |
| Asset | `/api/asset/list` | GET | Assets |
| Asset | `/api/asset/probe` | GET | Assets |
| Animation | `/api/animation/list` | GET | Animation |
| Plugin | `/api/plugin/list` | GET | Plugins |
| Plugin | `/api/plugin/llm_mg/generate` | POST | Animation (MG) |
| Plugin | `/api/plugin/llm_mg/templates` | GET | Animation (MG) |
| Type Maker | `/api/type-maker/list` | GET | Project |
| Type Maker | `/api/type-maker/create` | POST | Project |
| Template | `/api/template/list` | GET | Project |
| Template | `/api/template/create` | POST | Project |
| Edit | `/api/edit/session/create` | POST | Agent |
| Edit | `/api/edit/session/{id}/chat` | POST | Agent |
| Learn | `/api/learn/persona/{id}/record` | POST | Persona |
| Learn | `/api/learn/persona/{id}/preferences` | GET | Persona |
| Preprocess | `/api/preprocess/start/{id}` | POST | Assets |
| Webhook | `/api/webhook/subscribe` | POST | Settings |
| Webhook | `/api/webhook/unsubscribe` | POST | WebhookSettings |
| Webhook | `/api/webhook/subscriptions` | GET | WebhookSettings |
| Webhook | `/api/webhook/test/{event_type}` | POST | WebhookSettings |
| Requirements | `/api/requirements/init` | POST | RequirementsPanel |
| Requirements | `/api/requirements/chat` | POST | RequirementsPanel |
| Requirements | `/api/requirements/plan/{id}` | GET | RequirementsPanel |
| Requirements | `/api/requirements/session/{id}` | GET | RequirementsPanel |
| Requirements | `/api/requirements/upload/{id}` | POST | RequirementsPanel |
| Persona | `/api/persona/forge/chat/start` | POST | PersonaForgeChat |
| Persona | `/api/persona/forge/chat/message` | POST | PersonaForgeChat |
| Persona | `/api/persona/forge/chat/knowledge` | POST | PersonaForgeChat |
| Persona | `/api/persona/forge/chat/commit` | POST | PersonaForgeChat |
| Vision | `/api/vision/analyze` | POST | AssetPanel |
| Vision | `/api/vision/import` | POST | AssetPanel |
| Font | `/api/fonts/list` | GET | FontSettings |
| Font | `/api/fonts/resolve` | GET | FontSettings |
| Font | `/api/fonts/default` | GET | FontSettings |
| Test | `/api/test/config` | GET | ModelTest |
| Test | `/api/test/llm` | POST | ModelTest |
| Test | `/api/test/embed` | POST | ModelTest |
| Test | `/api/test/rerank` | POST | ModelTest |
| Plugin | `/api/plugin/discover` | GET | PluginAdmin |
| Plugin | `/api/plugin/load-all` | POST | PluginAdmin |
| Plugin | `/api/plugin/load/{id}` | POST | PluginAdmin |
| Plugin | `/api/plugin/unload/{id}` | POST | PluginAdmin |
| Plugin | `/api/plugin/capabilities` | GET | PluginAdmin |
| STT | `/api/stt/align` | POST | RequirementsPanel |
| Type Maker | `/api/type-maker/update/{id}` | PUT | TypeMaker |
| Type Maker | `/api/type-maker/get/{id}` | GET | TypeMaker |
| Type Maker | `/api/type-maker/delete/{id}` | DELETE | TypeMaker |
| Type Maker | `/api/type-maker/duplicate/{id}` | POST | TypeMaker |
| Template | `/api/template/update/{id}` | PUT | TemplateMgr |
| Template | `/api/template/get/{id}` | GET | TemplateMgr |
| Template | `/api/template/delete/{id}` | DELETE | TemplateMgr |
| Template | `/api/template/variables/{id}` | GET | TemplateMgr |
| Template | `/api/template/render/{id}` | POST | TemplateMgr |
| Template | `/api/template/run/{id}` | POST | TemplateMgr |
| Template | `/api/template/intro-outro/create` | POST | TemplateMgr |
| Template | `/api/template/intro-outro/list` | GET | TemplateMgr |
| Template | `/api/template/intro-outro/delete/{id}` | DELETE | TemplateMgr |
| Pipeline | `/api/pipeline/tasks` | GET | PipelineAdmin |
| Pipeline | `/api/pipeline/llm-usage` | GET | PipelineAdmin |
| Pipeline | `/api/pipeline/trace/{id}` | GET | PipelineAdmin |
| Pipeline | `/api/pipeline/batch` | POST | PipelineAdmin |
| Asset | `/api/asset/probe` | GET | Editor |
| Metrics | `/metrics` | GET | HealthDashboard |

### B. Default Keyboard Shortcuts

| Shortcut | Action | Scope |
|----------|--------|-------|
| `Space` | Play/Pause | Global |
| `Left/Right` | Frame step back/forward | Global |
| `Shift+Left/Right` | Jump to prev/next clip edge | Global |
| `Ctrl+Z` | Undo | Global |
| `Ctrl+Shift+Z` | Redo | Global |
| `S` | Split clip at playhead | Timeline |
| `Delete / Backspace` | Delete selected clips | Timeline |
| `Ctrl+D` | Duplicate selected | Timeline |
| `Ctrl+A` | Select all | Timeline |
| `Ctrl+C / V` | Copy/Paste clips | Timeline |
| `Up/Down` | Move clip to track above/below | Timeline |
| `Alt+Scroll` | Timeline zoom | Timeline |
| `Shift+Scroll` | Horizontal scroll | Timeline |
| `Home/End` | Jump to timeline start/end | Timeline |
| `Ctrl+S` | Save project | Global |
| `Ctrl+Shift+E` | Export panel | Global |
| `Ctrl+K` | Command palette | Global |
| `Ctrl+/` | Shortcut cheat sheet | Global |
| `M` | Add marker | Timeline |
| `I / O` | Set in/out point | Timeline |
| `R` | Range select tool | Tool switch |
| `V` | Select tool (default) | Tool switch |
| `B` | Razor tool | Tool switch |

### C. Package Dependencies

```json
{
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@tanstack/react-router": "^1.0.0",
    "@tanstack/react-query": "^5.0.0",
    "@tanstack/react-virtual": "^3.0.0",
    "zustand": "^5.0.0",
    "@dnd-kit/core": "^6.0.0",
    "@dnd-kit/sortable": "^8.0.0",
    "@dnd-kit/utilities": "^3.0.0",
    "@radix-ui/react-dialog": "^1.0.0",
    "@radix-ui/react-dropdown-menu": "^2.0.0",
    "@radix-ui/react-select": "^2.0.0",
    "@radix-ui/react-slider": "^1.0.0",
    "@radix-ui/react-tabs": "^1.0.0",
    "@radix-ui/react-popover": "^1.0.0",
    "@radix-ui/react-tooltip": "^1.0.0",
    "@radix-ui/react-context-menu": "^2.0.0",
    "@radix-ui/react-collapsible": "^1.0.0",
    "lucide-react": "^0.400.0",
    "tailwindcss": "^4.0.0",
    "axios": "^1.7.0",
    "clsx": "^2.0.0",
    "tailwind-merge": "^2.0.0",
    "dompurify": "^3.0.0",
    "i18next": "^23.0.0",
    "react-i18next": "^14.0.0"
  },
  "devDependencies": {
    "typescript": "^5.5.0",
    "vite": "^6.0.0",
    "@vitejs/plugin-react": "^4.0.0",
    "vitest": "^2.0.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/jest-dom": "^6.0.0",
    "playwright": "^1.45.0",
    "@playwright/test": "^1.45.0",
    "eslint": "^9.0.0",
    "@typescript-eslint/eslint-plugin": "^8.0.0",
    "prettier": "^3.0.0",
    "storybook": "^8.0.0",
    "@storybook/react": "^8.0.0",
    "@storybook/react-vite": "^8.0.0",
    "vite-bundle-visualizer": "^1.0.0",
    "msw": "^2.0.0"
  }
}
```

### D. Environment Variables

```bash
# .env.example

# API
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws

# App
VITE_APP_TITLE=ClipWright
VITE_APP_VERSION=0.1.0

# Feature flags
VITE_ENABLE_AGENT=true
VITE_ENABLE_PERSONA_MARKET=false
VITE_ENABLE_PLUGIN_MARKET=false
VITE_ENABLE_TELEMETRY=false

# Performance
VITE_MAX_THUMBNAIL_CACHE_SIZE=500
VITE_MAX_UNDO_HISTORY=200
VITE_MAX_CONCURRENT_UPLOADS=3

# Third-party
VITE_SENTRY_DSN=
VITE_ANALYTICS_ID=
```

---

> **Document Maintenance**: This file is updated alongside frontend implementation. After each Phase completion, update the corresponding section's implementation status.
>
> **Backend Reference Docs**:
> - `D:\ClipWeight\docs\structure.md` - 5-layer architecture
> - `D:\ClipWeight\docs\workflow.md` - Agent workflow design
> - `D:\ClipWeight\docs\Persona.md` - Persona 4-layer system
> - `D:\ClipWeight\docs\api_reference.md` - Complete API reference
> - `D:\ClipWeight\docs\development.md` - Dev guide
> - `D:\ClipWeight\design.md` - Overall system design
> - `D:\ClipWeight\README.md` - Project overview
