/**
 * API types, mirroring backend/app/schemas/models.py.
 *
 * The pervasive `| null` on result fields is the "not computed" state from
 * engineering rule 1. It is a different thing from zero, and the UI must
 * render it as an em dash, never as 0.
 */

export interface RiverSummary {
  name: string
  dam_count: number
  states: string[]
}

export interface DamSummary {
  id: string
  name: string
  river: string
  state: string
  lon: number
  lat: number
  dam_type: string
  structural_height_m: number | null
  crest_length_m: number | null
  gross_storage_mcm: number | null
  live_storage_mcm: number | null
  /** Null for every catalog record: the NRLD tables do not carry it. */
  frl_m: number | null
  mddl_m: number | null
  commissioned_year: number | null
  nrld_id: string | null
  notes: string[]
  /** Per-field citation, so any number in the UI traces to a source. */
  sources: Record<string, string>
}

export interface ScenarioSummary {
  id: string
  name: string
  description: string
  dam: string
  river: string
  state: string
  lon: number
  lat: number
  scenario_type: string
  resolution_m: number
  duration_hours: number
  reach_length_km: number
  towns: string[]
  valid: boolean
  error?: string
}

export type EngineKind = 'native' | 'external' | 'unavailable'

export interface EngineStatus {
  id: string
  /** The exact string the badge must display. Never substitute our own wording. */
  display_name: string
  kind: EngineKind
  available: boolean
  is_real_solver: boolean
  version: string | null
  detail: string
  substitute_id: string | null
  evidence: Record<string, unknown>
}

export interface EngineHealth {
  engines: EngineStatus[]
  honesty_statement: string
}

export interface BreachInput {
  shape?: 'trapezoidal' | 'rectangular' | 'triangular'
  growth?: 'linear' | 'sine' | 'parabolic'
  width_m?: number | null
  depth_m?: number | null
  side_slope?: number
  formation_time_min?: number | null
  parameter_model?: string
}

export type ScenarioType =
  | 'complete_dam_break'
  | 'partial_breach'
  | 'piping_failure'
  | 'overtopping'
  | 'controlled_release'
  | 'landslide_dam_breach'

export interface SimulationRequest {
  scenario_id?: string
  dam_id?: string
  scenario_type?: ScenarioType
  reservoir_level_m?: number | null
  breach?: BreachInput
  engines?: string[]
  resolution_m?: number | null
  duration_hours?: number | null
  cfl?: number | null
  wet_threshold_m?: number | null
  manning_n_overrides?: Record<string, number>
}

export interface BreachPrediction {
  model: string
  width_m: number
  depth_m: number
  side_slope: number
  formation_time_min: number
  reference: string
  applicable: boolean
  caveats: string[]
}

export interface BreachComparison {
  used: BreachPrediction
  predictions: BreachPrediction[]
  spread: {
    n_models: number
    width_m: { min: number; max: number; mean: number; spread_ratio: number }
    formation_time_min: { min: number; max: number; mean: number; spread_ratio: number }
    interpretation: string
  }
}

export interface JobCreated {
  job_id: string
  status: string
  scenario_id: string
  websocket: string
}

export type JobStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface JobState {
  id: string
  scenario_id: string
  status: JobStatus
  created_utc: string
  fraction: number
  phase: string
  message: string
  log: string[]
  eta_seconds: number | null
  elapsed_seconds: number | null
  error: string | null
}

export interface EngineSummary {
  engine_id: string
  engine_display_name: string
  is_real_solver: boolean
  substituted: boolean
  honesty_note: string
  flooded_area_km2: number | null
  max_depth_m: number | null
  max_velocity_ms: number | null
  earliest_arrival_min: number | null
  max_hazard_m2s: number | null
  runtime_s: number | null
  steps: number | null
  mass_error: number | null
  warnings: string[]
}

export interface DepthBandStat {
  index: number
  min_m: number
  max_m: number | null
  label: string
  colour: string
  cells: number
  area_km2: number
}

export interface HazardStats {
  standard: string
  wet_threshold_m: number
  flooded_area_km2: number
  by_depth_band: DepthBandStat[]
  by_hazard_class: Array<{
    code: number
    label: string
    description: string
    colour: string
    cells: number
    area_km2: number
  }>
  max_depth_m: number | null
  max_velocity_ms: number | null
  max_dv_m2s: number | null
}

export interface ResultSummary {
  run_id: string
  scenario_id: string
  engines: EngineSummary[]
  hazard: HazardStats
  breach: BreachComparison
  peak_discharge_m3s: number | null
  time_to_peak_min: number | null
  total_volume_mcm: number | null
  resolution_m: number
  warnings: string[]
  provenance: Record<string, unknown>
}

export interface ComparisonRow {
  metric: string
  unit: string
  values: Record<string, number | null>
  difference_pct: number | null
}

export interface Comparison {
  run_id: string
  engines: string[]
  engine_display_names: Record<string, string>
  rows: ComparisonRow[]
  critical_success_index: number | null
  extent_rmse_m: number | null
  note: string
}

export interface TownResult {
  name: string
  lon: number
  lat: number
  population: number | null
  population_source: string | null
  max_depth_m: number | null
  max_velocity_ms: number | null
  arrival_min: number | null
  in_domain: boolean
}

export interface ImpactMetric {
  label: string
  value: number | null
  unit: string
  computed: boolean
  reason: string
  assumption: string
  display: string
  detail: Record<string, unknown>
}

export interface ImpactResponse {
  run_id: string
  metrics: Record<string, ImpactMetric>
  facilities: Array<Record<string, unknown>>
  evacuation_priority: Array<{
    name: string
    population: number | null
    depth_m: number | null
    arrival_min: number | null
  }>
  warnings: string[]
  provenance: Record<string, unknown>
}

export interface HydrographSeries {
  label: string
  unit: string
  times_hours: number[]
  values: Array<number | null>
}

export interface HydrographResponse {
  run_id: string
  series: HydrographSeries[]
  note: string
}

export interface CrossSectionResponse {
  run_id: string
  location: string
  chainage_m: number
  offset_from_path_m: number | null
  offsets_m: number[]
  bed_m: Array<number | null>
  water_surface_m: Array<number | null>
  max_depth_m: number | null
  note: string
}
