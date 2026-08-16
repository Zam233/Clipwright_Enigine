# ClipWright (帧艺) — AI Agent Context

## Project Identity

**ClipWright (帧艺)** is an AI-assisted video creation web editor frontend. React 19 + TypeScript 5.5 + Canvas 2D. Currently at Phase 5.

**Backend**: `J:\Clipwright` — Content Video Orchestration Engine v0.1.0 (Python, FastAPI, SSE pipeline orchestration).

Core product logic: Agent generates a rough-cut timeline → human reviews on the timeline → local dissatisfaction triggers Agent rework → final export. Human-in-the-loop iterative creation.

## Quick Start

```bash
npm install --cache "D:\.npm-cache"
npm run dev            # http://localhost:5173
npx tsc --noEmit       # type check
npm run build          # production build
npm run test           # unit tests (Vitest)
npm run test:e2e       # E2E tests (Playwright)
npm run lint           # ESLint
npm run format         # Prettier
npm run preview        # preview production build
```

**Proxy** (vite.config.ts): `/api` → `http://localhost:8000`.

**Env vars**: `VITE_API_BASE_URL` (default `http://localhost:8000`), `VITE_WS_URL` (default `ws://localhost:8000/ws`).

**Path alias**: `@/` → `src/` (tsconfig.json).

## Layered Architecture

```
Pages          → HomePage, EditorPage, PersonaPage*, ExportPage, SettingsPage*, etc.
Layouts        → EditorLayout (4-panel), StandardLayout
Features       → timeline (Canvas engine), preview, assets, properties, agent, keyboard
Stores (Zustand)→ timeline, selection, agent, asset, preview, workspace, settings, project, history, voice
Services       → api (Axios), ws (WebSocket), storage (localPrefs), media (mediaManager)
Core Infra     → Canvas2D TimelineEngine, KeyframeInterpolator, DnD Kit, KeybindingEngine
```

## Source Tree

```
src/
├── main.tsx / App.tsx / providers.tsx / router.tsx
├── pages/
│   ├── HomePage.tsx              # 项目工作台 (landing)
│   ├── EditorPage.tsx            # 编辑器主页面
│   ├── ProjectsPage.tsx          # 项目列表
│   ├── ExportPage.tsx            # 导出设置
│   ├── SettingsPage.tsx          # 全局设置
│   ├── PersonaPage.tsx           # Persona 管理
│   ├── PersonaDetailPage.tsx     # Persona 编辑
│   ├── PersonaForgePage.tsx      # Persona 交互式创建
│   ├── VoicePage.tsx             # 语音克隆
│   ├── HelpPage.tsx              # 帮助教程
│   └── admin/                    # 管理页面 (Models, Tools, Plugins, Fonts, etc.)
├── layouts/
│   ├── EditorLayout.tsx          # 4-panel editor
│   └── StandardLayout.tsx        # Standard page
├── features/
│   ├── timeline/                 # ★ Core Canvas 2D engine
│   │   ├── engine/               # TimelineEngine, renderers, snap, easing, types
│   │   └── components/           # TimelinePanel, EditorToolbar
│   ├── preview/                  # Canvas composite preview + playback controls
│   ├── assets/                   # Asset panel (AI match, library, dub view)
│   ├── properties/               # Property panel (keyframes, transitions, presets)
│   ├── agent/                    # ★ Agent co-pilot (requirements, pipeline, review)
│   └── keyboard/                 # Global keybinding engine + cheat sheet
├── stores/                       # 10 Zustand stores
├── services/
│   ├── api/                      # Axios API clients
│   ├── ws/                       # WebSocket client
│   ├── storage/                  # localStorage preference helpers
│   └── media/                    # Media management utilities
├── types/                        # TypeScript types (aligned with backend schema)
├── components/
│   ├── ui/                       # Button, Panel, Tooltip, Badge, Slider
│   └── shared/                   # ProjectCard, AudioPlayer, Markdown
├── lib/                          # Utility functions
└── styles/globals.css            # Design tokens + theme
```

## Route Table

| Path | Page | Lazy |
|------|------|:----:|
| `/` | HomePage | |
| `/editor/$projectId` | EditorPage | ✓ |
| `/projects` | ProjectsPage | ✓ |
| `/export` | ExportPage | ✓ |
| `/persona` | PersonaPage | ✓ |
| `/persona/$personaId` | PersonaDetailPage | ✓ |
| `/persona/forge` | PersonaForgePage | ✓ |
| `/voice` | VoicePage | ✓ |
| `/settings` | SettingsPage | ✓ |
| `/settings/models` | ModelsPage | ✓ |
| `/settings/tools` | ToolsPage | ✓ |
| `/settings/plugins` | PluginsPage | ✓ |
| `/settings/type-maker` | TypeMakerPage | ✓ |
| `/settings/templates` | TemplatesPage | ✓ |
| `/settings/webhooks` | WebhooksPage | ✓ |
| `/settings/learning` | LearningPage | ✓ |
| `/settings/video-editor` | VideoEditorPage | ✓ |
| `/settings/fonts` | FontsPage | ✓ |
| `/settings/subtitle-tools` | SubtitleToolsPage | ✓ |
| `/settings/preprocess` | PreprocessPage | ✓ |
| `/pipeline-admin` | PipelineAdminPage | ✓ |
| `/help` | HelpPage | ✓ |

All pages except the landing HomePage are lazy-loaded via `React.lazy()` for route-level code splitting.

## State Management

10 Zustand stores (v5), all defined with `create<T>()`:

| Store | Purpose |
|-------|---------|
| `timelineStore` | Timeline core data: tracks, clips, duration, fps |
| `selectionStore` | Current selection: selected clip IDs, playhead position |
| `agentStore` | Agent co-pilot state: pipeline status, SSE stream, phase, suggestions |
| `assetStore` | Asset library: asset list, search query, filters |
| `previewStore` | Preview playback: isPlaying, currentTime, volume, loop, shuttle |
| `workspaceStore` | Panel layout: panel widths, visible panels (localStorage persisted) |
| `settingsStore` | Global settings: theme, language, shortcuts, API base URL |
| `projectStore` | Project metadata: projectId, name, personaId, pluginId |
| `historyStore` | Undo/redo stacks (deep-clone based) |
| `voiceStore` | Voice cloning: upload, clone progress, records |

## API Services

```
getApiClient(), resetApiClient()    # Axios client factory
pipelineApi                          # Pipeline CRUD + status
personaApi                           # Persona CRUD + Forge
assetApi                             # Asset search + upload
renderApi                            # Render queue + status
requirementsApi                      # Requirements Agent init + chat + plan
projectApi                           # Project CRUD
healthApi                            # Backend health check
pluginApi                            # Plugin management
animationApi                         # Animation presets
toolApi                              # Tool execution
skillApi                             # Skill management
voiceApi                             # Voice cloning + records
```

**WebSocket**: Removed — `WsClient.ts` deleted (no `/ws` endpoint). Real-time is **SSE-only** (pipeline trace / requirements chat / render queue streams).

**SSE**: Used for Agent pipeline tracking (event stream from `/api/pipeline/{id}/events`).

## Key Patterns

- **Timeline JSON ↔ Backend**: Timeline data structure aligns with `clipwright/schema/timeline.py`. Undo/redo uses full deep-clone of timeline state.
- **Canvas 2D RAF Loop**: `TimelineEngine` runs a `requestAnimationFrame` loop with a dirty flag to avoid redundant redraws.
- **SSE Pipeline Tracking**: Agent pipeline emits SSE events (`agent_start`, `agent_end`, `tool`, `llm`, `timeline_snapshot`, `pipeline_complete`, etc.) for real-time progress UI.
- **Human-in-the-loop Review**: `TimelineDiffView` shows before/after timeline diff when Agent returns a new version. User accepts or rejects.
- **Undo/Redo**: `historyStore` deep-clones the full timeline on each mutation, supports undo/redo with named snapshots.
- **Layout Persistence**: Panel widths are persisted to `localStorage` via `workspaceStore`.
- **Offline Demo Mode**: When backend is unavailable, Agent pipeline runs with simulated data for development/demo.
- **Frontend–Backend Parity**: Full route/client/gap audit in `docs/frontend-backend-parity.md` (mirror in `J:\Clipwright\docs\`). Backend is the single source of truth for route shapes.

## Design System

Material You (dynamic color science, Monet palette) × Premiere Pro (dark high-density work surface).

- Source color: `#4F6BED` (ClipWright Blue) → tonal palette
- Dark theme base: `#0E101A`
- Track semantic colors: video `#4F8CFF` / audio `#34D399` / text `#FBBF24` / image `#A855F7` / animation `#FF6B6B`
- Fonts: Inter + Noto Sans SC (UI), JetBrains Mono (timecode/ruler)
- Design tokens: CSS custom properties prefixed `--color-*`, `--cw-*` in `src/styles/globals.css`
- Full spec: `ClipWright-Design-Specification.md`

Coding conventions:
- `@/` path alias (e.g. `@/stores/timelineStore`)
- Tailwind CSS 4 with `cw-*` custom tokens
- Zustand `create<T>()` pattern
- Bilingual comments (Chinese + English) in Chinese context
- ESLint + Prettier + TypeScript strict mode

## Testing

- **Unit tests**: Vitest (`npm run test`). Files co-located as `*.test.ts`/`*.test.tsx`. Config: `vitest.config.ts` (excludes `e2e/`).
- **E2E tests**: Playwright (`npm run test:e2e`). Specs in `e2e/` (chromium, headless). `e2e/helpers.ts` mocks all backend requests via route interception — E2E runs are hermetic (no real backend needed). Route patterns must be path-anchored regexes (`/https?:\/\/[^/]+\/api\//`) to avoid intercepting Vite module URLs like `/src/services/api/*.ts`. Project IDs in E2E must match the router guard `^proj_[A-Za-z0-9_-]{1,63}$`.
- Test examples: `timelineStore.test.ts`, `TimelineEngine.scroll.test.ts`, `TimelineEngine.wheel.test.ts`, `AssetCard.test.tsx`, `timelineDiff.test.ts`, `snap.test.ts`, `easing.test.ts`.

## Security Notes

- Backend API base URL fallbacks must use port **8000** (not 8080) — see `src/services/api/*.ts` SSE/URL builders.
- Backend supports optional API token auth (`CLIPWRIGHT_API_TOKEN`); see backend `docs/security.md`.
