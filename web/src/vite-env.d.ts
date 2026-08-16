/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_WS_URL?: string;
  readonly VITE_APP_TITLE?: string;
  readonly VITE_APP_VERSION?: string;
  readonly VITE_ENABLE_AGENT?: string;
  readonly VITE_MAX_UNDO_HISTORY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
