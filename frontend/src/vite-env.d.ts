/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Override the API origin. Empty in dev (Vite proxies) and in production. */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
