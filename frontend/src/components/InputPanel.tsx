import { useEffect, useMemo, useState } from 'react'
import { useBreachPreview, useDams, useEngines, useRivers, useScenarios } from '../api/hooks'
import type { ScenarioType, SimulationRequest } from '../types/api'
import { EngineBadge, Panel, Skeleton } from './Value'

const SCENARIO_TYPES: Array<{ value: ScenarioType; label: string; note?: string }> = [
  { value: 'complete_dam_break', label: 'Complete Dam Break' },
  { value: 'partial_breach', label: 'Partial Breach' },
  { value: 'piping_failure', label: 'Piping Failure' },
  { value: 'overtopping', label: 'Overtopping' },
  { value: 'controlled_release', label: 'Controlled Release' },
  {
    value: 'landslide_dam_breach',
    label: 'Landslide Dam Breach',
    note: 'Natural blockage failure, as at Rishi Ganga in February 2021.',
  },
]

export interface InputPanelProps {
  onRun: (request: SimulationRequest) => void
  running: boolean
  onReset: () => void
}

export default function InputPanel({ onRun, running, onReset }: InputPanelProps) {
  const scenarios = useScenarios()
  const rivers = useRivers()
  const engines = useEngines()

  const [scenarioId, setScenarioId] = useState<string>('')
  const [river, setRiver] = useState<string>('')
  const [scenarioType, setScenarioType] = useState<ScenarioType>('complete_dam_break')
  const [level, setLevel] = useState<string>('')
  const [width, setWidth] = useState<string>('')
  const [depth, setDepth] = useState<string>('')
  const [formation, setFormation] = useState<string>('')
  const [duration, setDuration] = useState<string>('')
  const [resolution, setResolution] = useState<string>('90')
  const [selectedEngines, setSelectedEngines] = useState<string[]>(['swe_fv'])
  const [showAdvanced, setShowAdvanced] = useState(false)

  const dams = useDams(river || undefined)

  // Default to the first bundled scenario so the page is never empty.
  useEffect(() => {
    if (!scenarioId && scenarios.data?.length) {
      const first = scenarios.data.find((s) => s.valid)
      if (first) {
        setScenarioId(first.id)
        setRiver(first.river)
        setDuration(String(first.duration_hours))
      }
    }
  }, [scenarios.data, scenarioId])

  const active = scenarios.data?.find((s) => s.id === scenarioId) ?? null

  const request: SimulationRequest | null = useMemo(() => {
    if (!scenarioId) return null
    return {
      scenario_id: scenarioId,
      scenario_type: scenarioType,
      reservoir_level_m: level ? Number(level) : null,
      breach: {
        width_m: width ? Number(width) : null,
        depth_m: depth ? Number(depth) : null,
        formation_time_min: formation ? Number(formation) : null,
      },
      engines: selectedEngines,
      resolution_m: resolution ? Number(resolution) : null,
      duration_hours: duration ? Number(duration) : null,
    }
  }, [scenarioId, scenarioType, level, width, depth, formation, selectedEngines, resolution, duration])

  // Ghost hints: what the empirical models predict, live, before running.
  const preview = useBreachPreview(
    request ? { ...request, breach: {} } : null,
  )
  const predicted = preview.data?.used
  const spread = preview.data?.spread

  const toggleEngine = (id: string) =>
    setSelectedEngines((prev) =>
      prev.includes(id) ? prev.filter((e) => e !== id) : [...prev, id],
    )

  const validationError =
    preview.isError && request
      ? (preview.error as Error)?.message ?? 'this configuration is not valid'
      : null

  return (
    <div className="space-y-3">
      <Panel title="Select River & Dam">
        {scenarios.isLoading ? (
          <Skeleton className="h-16" />
        ) : (
          <div className="space-y-2">
            <Field label="Scenario">
              <select
                className={selectClass}
                value={scenarioId}
                onChange={(e) => {
                  const next = scenarios.data?.find((s) => s.id === e.target.value)
                  setScenarioId(e.target.value)
                  if (next) {
                    setRiver(next.river)
                    setDuration(String(next.duration_hours))
                  }
                }}
              >
                {scenarios.data?.filter((s) => s.valid).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="River">
              <select
                className={selectClass}
                value={river}
                onChange={(e) => setRiver(e.target.value)}
              >
                <option value="">All rivers</option>
                {rivers.data?.map((r) => (
                  <option key={r.name} value={r.name}>
                    {r.name} ({r.dam_count})
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Dam in catalog">
              <select className={selectClass} disabled>
                {dams.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} — {d.state}
                  </option>
                ))}
              </select>
            </Field>

            {active && (
              <p className="text-[11px] leading-relaxed text-slate-500">
                {active.dam} on the <strong>{active.river}</strong>, {active.state}.{' '}
                {active.reach_length_km} km reach, towns: {active.towns.join(', ')}.
              </p>
            )}
          </div>
        )}
      </Panel>

      <Panel
        title="Scenario Configuration"
        subtitle="Empirical predictions shown as hints; your values override them"
      >
        <div className="space-y-2">
          <Field label="Scenario Type">
            <select
              className={selectClass}
              value={scenarioType}
              onChange={(e) => setScenarioType(e.target.value as ScenarioType)}
            >
              {SCENARIO_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>
          {SCENARIO_TYPES.find((t) => t.value === scenarioType)?.note && (
            <p className="text-[10px] text-slate-500">
              {SCENARIO_TYPES.find((t) => t.value === scenarioType)!.note}
            </p>
          )}

          <NumberField
            label="Reservoir Water Level"
            unit="m MSL"
            value={level}
            onChange={setLevel}
            placeholder="scenario default"
          />
          <NumberField
            label="Breach Width"
            unit="m"
            value={width}
            onChange={setWidth}
            placeholder={predicted ? `${Math.round(predicted.width_m)} predicted` : 'predicted'}
            hint={
              predicted
                ? `${predicted.model.split(' ')[0]} predicts ${Math.round(predicted.width_m)} m`
                : undefined
            }
          />
          <NumberField
            label="Breach Depth"
            unit="m"
            value={depth}
            onChange={setDepth}
            placeholder={predicted ? `${Math.round(predicted.depth_m)} predicted` : 'predicted'}
          />
          <NumberField
            label="Breach Formation Time"
            unit="min"
            value={formation}
            onChange={setFormation}
            placeholder={
              predicted ? `${Math.round(predicted.formation_time_min)} predicted` : 'predicted'
            }
          />
          <NumberField
            label="Simulation Duration"
            unit="hours"
            value={duration}
            onChange={setDuration}
          />

          {spread && spread.width_m.spread_ratio > 2 && (
            <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-[10px] leading-relaxed text-amber-900">
              The three empirical breach models disagree by a factor of{' '}
              <strong>{spread.width_m.spread_ratio.toFixed(1)}</strong> on width
              ({Math.round(spread.width_m.min)}–{Math.round(spread.width_m.max)} m).
              Breach geometry, not the solver, is the dominant uncertainty in this result.
            </div>
          )}

          {validationError && (
            <div className="rounded border border-rose-200 bg-rose-50 px-2 py-1.5 text-[10px] text-rose-900">
              {validationError}
            </div>
          )}
        </div>
      </Panel>

      <Panel title="Select Models" subtitle="Availability probed on this machine">
        <div className="space-y-1.5">
          {engines.isLoading && <Skeleton className="h-12" />}
          {engines.data?.engines
            .filter((e) => ['sph_pysph', 'delft3d', 'swe_fv', 'anuga'].includes(e.id))
            .map((engine) => (
              <label
                key={engine.id}
                className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 hover:bg-slate-50"
              >
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={selectedEngines.includes(engine.id)}
                  onChange={() => toggleEngine(engine.id)}
                />
                <span className="flex-1">
                  <span className="block text-xs font-medium text-slate-800">
                    {engine.id === 'sph_pysph'
                      ? 'Smooth Particle Hydrodynamics (SPH)'
                      : engine.id === 'delft3d'
                        ? 'Delft3D'
                        : engine.id === 'anuga'
                          ? 'ANUGA (cross-check)'
                          : 'FloodGuard-SWE (2D shallow water)'}
                  </span>
                  <span className="mt-0.5 block">
                    <EngineBadge
                      displayName={engine.available ? engine.display_name : `→ ${engine.substitute_id ?? 'unavailable'}`}
                      isRealSolver={engine.is_real_solver}
                      substituted={!engine.available}
                      detail={engine.detail}
                    />
                  </span>
                </span>
              </label>
            ))}
          {engines.data && (
            <p className="pt-1 text-[10px] leading-relaxed text-slate-500">
              {engines.data.honesty_statement}
            </p>
          )}
        </div>
      </Panel>

      <div className="flex gap-2">
        <button
          type="button"
          disabled={!request || running || selectedEngines.length === 0}
          onClick={() => request && onRun(request)}
          className="flex-1 rounded bg-sky-700 px-3 py-2 text-sm font-medium text-white
                     transition-colors hover:bg-sky-800 disabled:cursor-not-allowed
                     disabled:bg-slate-300"
        >
          {running ? 'Running…' : 'Run Simulation'}
        </button>
        <button
          type="button"
          onClick={onReset}
          className="rounded border border-slate-300 px-3 py-2 text-sm text-slate-700
                     hover:bg-slate-50"
        >
          Reset
        </button>
      </div>

      <Panel
        title="Additional Options"
        action={
          <button
            type="button"
            className="text-[11px] text-sky-700 hover:underline"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? 'Hide' : 'Show'}
          </button>
        }
      >
        {showAdvanced ? (
          <div className="space-y-2">
            <NumberField
              label="Grid resolution"
              unit="m"
              value={resolution}
              onChange={setResolution}
              hint="Cost scales as roughly 1/res³. Peak depths are genuinely resolution-sensitive."
            />
            <p className="text-[10px] leading-relaxed text-slate-500">
              A coarse cell averages the channel together with its banks and under-predicts
              the peak, so this is exposed rather than fixed. Every output records the
              resolution it was computed at. 30 m is the publication setting; 90–120 m is
              interactive.
            </p>
          </div>
        ) : (
          <p className="text-[11px] text-slate-500">
            Grid resolution, Manning&rsquo;s n overrides, CFL and output interval.
          </p>
        )}
      </Panel>
    </div>
  )
}

const selectClass =
  'w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-800 ' +
  'focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-[11px] font-medium text-slate-600">{label}</span>
      {children}
    </label>
  )
}

function NumberField({
  label,
  unit,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string
  unit: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  hint?: string
}) {
  return (
    <label className="block">
      <span className="mb-0.5 flex items-baseline justify-between">
        <span className="text-[11px] font-medium text-slate-600">{label}</span>
        <span className="text-[10px] text-slate-400">{unit}</span>
      </span>
      <input
        type="number"
        inputMode="decimal"
        className={selectClass}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {hint && <span className="mt-0.5 block text-[10px] text-slate-400">{hint}</span>}
    </label>
  )
}
