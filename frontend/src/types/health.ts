/** Mirrors backend app/api/health.py. Will be generated from OpenAPI in Phase 7. */

export type EngineKind = 'native' | 'external' | 'unavailable'

export interface EngineStatus {
  id: string
  /** The exact string the UI badge must display. Never substitute our own wording. */
  display_name: string
  kind: EngineKind
  available: boolean
  is_real_solver: boolean
  version: string | null
  detail: string
  substitute_id: string | null
  evidence: Record<string, unknown>
}

export interface Health {
  status: string
  app: string
  version: string
  git_commit: string
  generated_utc: string
  python: string
  platform: string
  engines_available: string[]
  engines_unavailable: string[]
  credentials: Record<string, boolean>
  data_dir: string
}
