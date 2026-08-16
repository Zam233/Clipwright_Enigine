import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from '@tanstack/react-router';
import { lazy, Suspense } from 'react';
import { HomePage } from './pages/HomePage';

// Route-level code splitting: every page (except the landing HomePage) is
// lazy-loaded into its own chunk to keep the initial bundle small.
const lazyPage = <T extends Record<string, React.ComponentType>>(
  factory: () => Promise<T>,
  exportName: keyof T,
) => lazy(() => factory().then((m) => ({ default: m[exportName] })));

const EditorPage = lazyPage(() => import('./pages/EditorPage'), 'EditorPage');
const SettingsPage = lazyPage(() => import('./pages/SettingsPage'), 'SettingsPage');
const ExportPage = lazyPage(() => import('./pages/ExportPage'), 'ExportPage');
const PersonaPage = lazyPage(() => import('./pages/PersonaPage'), 'PersonaPage');
const PersonaDetailPage = lazyPage(() => import('./pages/PersonaDetailPage'), 'PersonaDetailPage');
const PersonaForgePage = lazyPage(() => import('./pages/PersonaForgePage'), 'PersonaForgePage');
const HelpPage = lazyPage(() => import('./pages/HelpPage'), 'HelpPage');
const ModelsPage = lazyPage(() => import('./pages/admin/ModelsPage'), 'ModelsPage');
const ToolsPage = lazyPage(() => import('./pages/admin/ToolsPage'), 'ToolsPage');
const PluginsPage = lazyPage(() => import('./pages/admin/PluginsPage'), 'PluginsPage');
const TypeMakerPage = lazyPage(() => import('./pages/admin/TypeMakerPage'), 'TypeMakerPage');
const TemplatesPage = lazyPage(() => import('./pages/admin/TemplatesPage'), 'TemplatesPage');
const LearningPage = lazyPage(() => import('./pages/admin/LearningPage'), 'LearningPage');
const VideoEditorPage = lazyPage(() => import('./pages/admin/VideoEditorPage'), 'VideoEditorPage');
const WebhooksPage = lazyPage(() => import('./pages/admin/WebhooksPage'), 'WebhooksPage');
const FontsPage = lazyPage(() => import('./pages/admin/FontsPage'), 'FontsPage');
const SubtitleToolsPage = lazyPage(() => import('./pages/admin/SubtitleToolsPage'), 'SubtitleToolsPage');
const PreprocessPage = lazyPage(() => import('./pages/admin/PreprocessPage'), 'PreprocessPage');
const PipelineAdminPage = lazyPage(() => import('./pages/admin/PipelineAdminPage'), 'PipelineAdminPage');
const VoicePage = lazyPage(() => import('./pages/VoicePage'), 'VoicePage');
const ProjectsPage = lazyPage(() => import('./pages/ProjectsPage'), 'ProjectsPage');

function RouteFallback() {
  return (
    <div className="h-full w-full flex items-center justify-center bg-surface">
      <div className="flex flex-col items-center gap-3">
        <span className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span className="font-mono text-caption text-on-surface-variant tracking-widest">LOADING…</span>
      </div>
    </div>
  );
}

const rootRoute = createRootRoute({
  component: () => (
    <Suspense fallback={<RouteFallback />}>
      <Outlet />
    </Suspense>
  ),
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: HomePage,
});

const editorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/editor/$projectId',
  beforeLoad: async ({ params }) => {
    const { projectId } = params;
    // Validate id format only; EditorPage handles loading + offline fallback
    // (avoids a redundant double-fetch and allows offline/demo editor access)
    if (!projectId || !/^proj_[A-Za-z0-9_-]{1,63}$/.test(projectId)) {
      sessionStorage.setItem('cw_guard_notice', '项目链接无效');
      throw redirect({ to: '/' });
    }
  },
  component: EditorPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  component: SettingsPage,
});

const exportRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/export/$projectId',
  component: ExportPage,
});

const personaRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/persona',
  component: PersonaPage,
});

const personaDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/persona/$personaId',
  component: PersonaDetailPage,
});

const personaForgeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/persona/forge',
  component: PersonaForgePage,
});

const helpRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/help',
  component: HelpPage,
});

const modelsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/models',
  component: ModelsPage,
});

const toolsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/tools',
  component: ToolsPage,
});

const pluginsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/plugins',
  component: PluginsPage,
});

const typeMakerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/type-maker',
  component: TypeMakerPage,
});

const templatesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/templates',
  component: TemplatesPage,
});

const webhooksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/webhooks',
  component: WebhooksPage,
});

const learningRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/learning',
  component: LearningPage,
});

const videoEditorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/video-editor',
  component: VideoEditorPage,
});

const fontsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/fonts',
  component: FontsPage,
});

const subtitleToolsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/subtitle-tools',
  component: SubtitleToolsPage,
});

const preprocessRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/preprocess',
  component: PreprocessPage,
});

const pipelineAdminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/pipeline-admin',
  component: PipelineAdminPage,
});

const voiceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/voice',
  component: VoicePage,
});

const projectsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/projects',
  component: ProjectsPage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  editorRoute,
  settingsRoute,
  exportRoute,
  personaRoute,
  personaDetailRoute,
  personaForgeRoute,
  helpRoute,
  modelsRoute,
  toolsRoute,
  pluginsRoute,
  typeMakerRoute,
  templatesRoute,
  webhooksRoute,
  learningRoute,
  videoEditorRoute,
  fontsRoute,
  subtitleToolsRoute,
  preprocessRoute,
  pipelineAdminRoute,
  voiceRoute,
  projectsRoute,
]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
