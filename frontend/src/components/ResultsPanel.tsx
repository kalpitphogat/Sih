import { useComparison, useImpact, useResultSummary, useTowns } from '../api/hooks'
import type { EngineSummary } from '../types/api'
import {
  Caveat,
  EngineBadge,
  KpiCard,
  NOT_COMPUTED,
  Panel,
  Skeleton,
  formatInteger,
  formatMinutes,
  formatNumber,
} from './Value'

export function ResultsPanel({
  runId,
  engineIndex,
  onEngineChange,
}: {
  runId: string | null
  engineIndex: number
  onEngineChange: (i: number) => void
}) {
  const summary = useResultSummary(runId)

  if (!runId) {
    return (
      <Panel title="Simulation Results">
        <p className="text-xs text-slate-500">
          Run a simulation to see results. Nothing is shown here until it has been
          computed.
        </p>
      </Panel>
    )
  }
  if (summary.isLoading) {
    return (
      <Panel title="Simulation Results">
        <Skeleton className="h-28" />
      </Panel>
    )
  }
  if (summary.isError || !summary.data) {
    return (
      <Panel title="Simulation Results">
        <p className="text-xs text-rose-700">
          {(summary.error as Error)?.message ?? 'results are not available yet'}
        </p>
      </Panel>
    )
  }

  const engines = summary.data.engines
  const engine: EngineSummary | undefined = engines[Math.min(engineIndex, engines.length - 1)]

  return (
    <Panel
      title={`Simulation Results${engine ? ` — ${engine.engine_display_name}` : ''}`}
      subtitle={`run ${summary.data.run_id} at ${formatNumber(summary.data.resolution_m, 0)} m`}
      action={
        engines.length > 1 ? (
          <div className="flex gap-1">
            {engines.map((e, i) => (
              <button
                key={e.engine_id}
                type="button"
                onClick={() => onEngineChange(i)}
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  i === engineIndex
                    ? 'bg-sky-700 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {e.engine_id}
              </button>
            ))}
          </div>
        ) : null
      }
    >
      {engine && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <KpiCard
              label="Flooded Area"
              value={engine.flooded_area_km2}
              unit="km²"
              decimals={1}
              accent="sky"
            />
            <KpiCard
              label="Maximum Depth"
              value={engine.max_depth_m}
              unit="m"
              decimals={1}
              accent="rose"
            />
            <KpiCard
              label="Maximum Velocity"
              value={engine.max_velocity_ms}
              unit="m/s"
              decimals={2}
              accent="amber"
            />
            <KpiCard
              label="Earliest Arrival"
              value={engine.earliest_arrival_min}
              unit="min"
              decimals={0}
              accent="emerald"
              reason="nothing exceeded the wet threshold"
            />
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <EngineBadge
              displayName={engine.engine_display_name}
              isRealSolver={engine.is_real_solver}
              substituted={engine.substituted}
              detail={engine.honesty_note}
            />
            <span className="text-[10px] text-slate-500">
              {formatNumber(engine.runtime_s, 0)} s, {formatInteger(engine.steps)} steps
            </span>
          </div>

          {engine.substituted && <Caveat>{engine.honesty_note}</Caveat>}

          <dl className="grid grid-cols-3 gap-2 border-t border-slate-100 pt-2">
            <MiniStat
              label="Peak discharge"
              value={`${formatInteger(summary.data.peak_discharge_m3s)} m³/s`}
            />
            <MiniStat
              label="Time to peak"
              value={formatMinutes(summary.data.time_to_peak_min)}
            />
            <MiniStat
              label="Volume released"
              value={`${formatNumber(summary.data.total_volume_mcm, 0)} MCM`}
            />
          </dl>

          {summary.data.warnings.length > 0 && (
            <details className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5">
              <summary className="cursor-pointer text-[11px] font-medium text-amber-900">
                {summary.data.warnings.length} caveat
                {summary.data.warnings.length === 1 ? '' : 's'} — read before quoting any
                number
              </summary>
              <ul className="mt-1 space-y-1 text-[10px] leading-relaxed text-amber-900">
                {summary.data.warnings.map((w, i) => (
                  <li key={i}>• {w}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Panel>
  )
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="font-mono text-[11px] text-slate-800">{value}</dd>
    </div>
  )
}

/** The six Affected Elements cards. */
export function ImpactPanel({ runId }: { runId: string | null }) {
  const impact = useImpact(runId)

  const cards = [
    { key: 'buildings', label: 'Buildings', icon: '🏠' },
    { key: 'roads', label: 'Roads', icon: '🛣️' },
    { key: 'population', label: 'Population', icon: '👥' },
    { key: 'agriculture', label: 'Agricultural Land', icon: '🌾' },
    { key: 'hospitals', label: 'Hospitals', icon: '🏥' },
    { key: 'schools', label: 'Schools', icon: '🏫' },
  ]

  return (
    <Panel title="Affected Elements" subtitle="within the inundation area">
      {!runId && <p className="text-xs text-slate-500">No simulation loaded.</p>}
      {runId && impact.isLoading && <Skeleton className="h-24" />}
      {runId && impact.isError && (
        <p className="text-[11px] leading-relaxed text-slate-600">
          {(impact.error as Error)?.message ??
            'Impact analysis is not available for this run.'}
        </p>
      )}
      {impact.data && (
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-2">
            {cards.map(({ key, label, icon }) => {
              const m = impact.data.metrics[key]
              return (
                <div
                  key={key}
                  className={`rounded border px-2 py-1.5 ${
                    m?.computed ? 'border-slate-200 bg-white' : 'border-slate-200 bg-slate-50'
                  }`}
                  title={m?.computed ? m.assumption : m?.reason}
                >
                  <div className="text-[14px]">{icon}</div>
                  <div
                    className={`text-base font-semibold tabular-nums ${
                      m?.computed ? 'text-slate-900' : 'text-slate-400'
                    }`}
                  >
                    {m?.display ?? NOT_COMPUTED}
                  </div>
                  <div className="text-[10px] leading-tight text-slate-500">{label}</div>
                  {m && !m.computed && (
                    <div className="mt-0.5 text-[9px] leading-tight text-slate-400">
                      not computed
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {impact.data.evacuation_priority.length > 0 && (
            <div className="border-t border-slate-100 pt-2">
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                Evacuation priority, by lead time
              </h3>
              <table className="w-full text-[11px]">
                <thead className="text-left text-[10px] uppercase text-slate-400">
                  <tr>
                    <th className="pb-0.5">Settlement</th>
                    <th className="pb-0.5 text-right">Lead time</th>
                    <th className="pb-0.5 text-right">Depth</th>
                    <th className="pb-0.5 text-right">Population</th>
                  </tr>
                </thead>
                <tbody>
                  {impact.data.evacuation_priority.slice(0, 10).map((row) => (
                    <tr key={row.name} className="border-t border-slate-50">
                      <td className="py-0.5 text-slate-800">{row.name}</td>
                      <td className="py-0.5 text-right font-medium tabular-nums text-slate-900">
                        {formatMinutes(row.arrival_min)}
                      </td>
                      <td className="py-0.5 text-right tabular-nums text-slate-700">
                        {row.depth_m !== null ? `${formatNumber(row.depth_m, 1)} m` : NOT_COMPUTED}
                      </td>
                      <td className="py-0.5 text-right tabular-nums text-slate-700">
                        {formatInteger(row.population)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {impact.data.warnings.map((w, i) => (
            <Caveat key={i}>{w}</Caveat>
          ))}
        </div>
      )}
    </Panel>
  )
}

/** The Model Comparison table. Every cell comes from the API. */
export function ComparisonTable({ runId }: { runId: string | null }) {
  const comparison = useComparison(runId)

  return (
    <Panel title="Model Comparison">
      {!runId && <p className="text-xs text-slate-500">No simulation loaded.</p>}
      {runId && comparison.isLoading && <Skeleton className="h-20" />}
      {comparison.data && comparison.data.rows.length === 0 && (
        <p className="text-[11px] leading-relaxed text-slate-600">{comparison.data.note}</p>
      )}
      {comparison.data && comparison.data.rows.length > 0 && (
        <div className="space-y-2">
          <table className="w-full text-[11px]">
            <thead className="text-left text-[10px] uppercase text-slate-400">
              <tr>
                <th className="pb-1">Metric</th>
                {comparison.data.engines.map((e) => (
                  <th key={e} className="pb-1 text-right">
                    {comparison.data!.engine_display_names[e]?.split('(')[0] ?? e}
                  </th>
                ))}
                <th className="pb-1 text-right">Difference</th>
              </tr>
            </thead>
            <tbody>
              {comparison.data.rows.map((row) => (
                <tr key={row.metric} className="border-t border-slate-50">
                  <td className="py-0.5 text-slate-700">
                    {row.metric}{' '}
                    <span className="text-slate-400">({row.unit})</span>
                  </td>
                  {comparison.data!.engines.map((e) => (
                    <td key={e} className="py-0.5 text-right tabular-nums text-slate-900">
                      {formatNumber(row.values[e], 2)}
                    </td>
                  ))}
                  <td
                    className={`py-0.5 text-right font-medium tabular-nums ${
                      row.difference_pct === null
                        ? 'text-slate-400'
                        : row.difference_pct > 0
                          ? 'text-rose-700'
                          : 'text-emerald-700'
                    }`}
                  >
                    {row.difference_pct === null
                      ? NOT_COMPUTED
                      : `${row.difference_pct > 0 ? '+' : ''}${row.difference_pct.toFixed(1)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {comparison.data.note && (
            <p className="text-[10px] leading-relaxed text-slate-500">
              {comparison.data.note}
            </p>
          )}
        </div>
      )}
    </Panel>
  )
}

/** Downloads. Each links straight at the API, so nothing is generated client-side. */
export function ExportPanel({ runId }: { runId: string | null }) {
  const formats = [
    { format: 'shp', label: 'Download Flood Map (.SHP)', tone: 'bg-emerald-700 hover:bg-emerald-800' },
    { format: 'kml', label: 'Download as KML', tone: 'bg-sky-700 hover:bg-sky-800' },
    { format: 'geojson', label: 'Download GeoJSON', tone: 'bg-slate-700 hover:bg-slate-800' },
    { format: 'tif', label: 'Download Depth Raster (.TIF)', tone: 'bg-slate-700 hover:bg-slate-800' },
    { format: 'pdf', label: 'Generate Report (PDF)', tone: 'bg-slate-600 hover:bg-slate-700' },
  ]

  return (
    <Panel title="Export Results">
      <div className="space-y-1.5">
        {formats.map(({ format, label, tone }) => (
          <a
            key={format}
            href={runId ? `/api/results/${runId}/export?format=${format}` : undefined}
            className={`block rounded px-3 py-1.5 text-center text-xs font-medium text-white
                        transition-colors ${runId ? tone : 'pointer-events-none bg-slate-300'}`}
          >
            {label}
          </a>
        ))}
        <p className="pt-1 text-[10px] leading-relaxed text-slate-500">
          Every file carries a provenance block: DEM source and resolution, dam parameters
          and their citations, engine name and version, solver settings, git commit and UTC
          timestamp.
        </p>
      </div>
    </Panel>
  )
}

/** Depth, velocity and arrival time at each named town. */
export function TownTable({ runId }: { runId: string | null }) {
  const towns = useTowns(runId)
  if (!runId || !towns.data?.length) return null

  return (
    <Panel title="Arrival at named locations" subtitle="sorted by lead time">
      <table className="w-full text-[11px]">
        <thead className="text-left text-[10px] uppercase text-slate-400">
          <tr>
            <th className="pb-1">Location</th>
            <th className="pb-1 text-right">Arrival</th>
            <th className="pb-1 text-right">Max depth</th>
            <th className="pb-1 text-right">Max velocity</th>
          </tr>
        </thead>
        <tbody>
          {towns.data.map((t) => (
            <tr key={t.name} className="border-t border-slate-50">
              <td className="py-0.5 text-slate-800">
                {t.name}
                {!t.in_domain && (
                  <span className="ml-1 text-[9px] text-amber-700">outside domain</span>
                )}
              </td>
              <td className="py-0.5 text-right font-medium tabular-nums text-slate-900">
                {formatMinutes(t.arrival_min)}
              </td>
              <td className="py-0.5 text-right tabular-nums text-slate-700">
                {t.max_depth_m !== null ? `${formatNumber(t.max_depth_m, 2)} m` : NOT_COMPUTED}
              </td>
              <td className="py-0.5 text-right tabular-nums text-slate-700">
                {t.max_velocity_ms !== null
                  ? `${formatNumber(t.max_velocity_ms, 2)} m/s`
                  : NOT_COMPUTED}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
