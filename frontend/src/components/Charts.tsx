import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useCrossSection, useHydrographs } from '../api/hooks'
import type { ScenarioSummary } from '../types/api'
import { NOT_COMPUTED, Panel, Skeleton, formatNumber } from './Value'

const AXIS = { fontSize: 10, fill: '#64748b' }
const GRID = '#e2e8f0'

/**
 * Breach outflow versus time.
 *
 * These are the routed hydrographs the solver actually consumed, read back
 * from the CSV the run wrote. Nothing here is smoothed for appearance: the
 * recession is the reservoir draining, and its shape is a result.
 */
export function HydrographChart({ runId }: { runId: string | null }) {
  const data = useHydrographs(runId)

  const rows = (() => {
    if (!data.data) return []
    const discharge = data.data.series.find((s) => s.label === 'discharge_m3s')
    const level = data.data.series.find((s) => s.label === 'reservoir_level_m')
    if (!discharge) return []
    // The routing writes thousands of adaptive steps; thin for the chart.
    const stride = Math.max(1, Math.floor(discharge.times_hours.length / 400))
    const out: Array<Record<string, number | null>> = []
    for (let i = 0; i < discharge.times_hours.length; i += stride) {
      out.push({
        hours: discharge.times_hours[i],
        discharge: discharge.values[i],
        level: level?.values[i] ?? null,
      })
    }
    return out
  })()

  return (
    <Panel
      title="Flood Arrival Time"
      subtitle="breach outflow and reservoir level, as routed"
    >
      {!runId && <p className="text-xs text-slate-500">No simulation loaded.</p>}
      {runId && data.isLoading && <Skeleton className="h-40" />}
      {runId && data.isError && (
        <p className="text-[11px] text-slate-600">
          {(data.error as Error)?.message ?? 'no hydrograph for this run'}
        </p>
      )}
      {rows.length > 0 && (
        <>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={rows} margin={{ top: 4, right: 40, bottom: 16, left: 4 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="2 3" />
              <XAxis
                dataKey="hours"
                tick={AXIS}
                tickFormatter={(v) => `${Number(v).toFixed(1)}`}
                label={{ value: 'time (hours)', position: 'insideBottom', offset: -8, ...AXIS }}
              />
              <YAxis
                yAxisId="q"
                tick={AXIS}
                tickFormatter={(v) => `${(Number(v) / 1000).toFixed(0)}k`}
                label={{
                  value: 'discharge (m³/s)',
                  angle: -90,
                  position: 'insideLeft',
                  ...AXIS,
                }}
              />
              <YAxis yAxisId="h" orientation="right" tick={AXIS} />
              <Tooltip
                contentStyle={{ fontSize: 11 }}
                formatter={(value, name) => {
                  const n = typeof value === 'number' ? value : Number(value)
                  if (!Number.isFinite(n)) return [NOT_COMPUTED, String(name)]
                  return name === 'discharge'
                    ? [`${Math.round(n).toLocaleString('en-IN')} m³/s`, 'Breach outflow']
                    : [`${n.toFixed(1)} m`, 'Reservoir level']
                }}
                labelFormatter={(v) => `t = ${Number(v).toFixed(2)} h`}
              />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Line
                yAxisId="q"
                type="monotone"
                dataKey="discharge"
                name="Breach outflow"
                stroke="#0369a1"
                strokeWidth={1.8}
                dot={false}
              />
              <Line
                yAxisId="h"
                type="monotone"
                dataKey="level"
                name="Reservoir level (m MSL)"
                stroke="#b45309"
                strokeWidth={1.2}
                strokeDasharray="4 3"
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
          {data.data?.note && (
            <p className="mt-1 text-[10px] leading-relaxed text-slate-500">
              {data.data.note}
            </p>
          )}
        </>
      )}
    </Panel>
  )
}

/**
 * Valley cross-section with the maximum water surface over it.
 *
 * Where the water surface could not be sampled the API returns null and the
 * chart leaves a gap, rather than interpolating a line through terrain nobody
 * computed.
 */
export function CrossSectionChart({
  runId,
  scenario,
  location,
  onLocationChange,
}: {
  runId: string | null
  scenario: ScenarioSummary | null
  location: string | null
  onLocationChange: (l: string) => void
}) {
  const section = useCrossSection(runId, location)

  const rows =
    section.data?.offsets_m.map((offset, i) => ({
      offset,
      bed: section.data!.bed_m[i],
      water: section.data!.water_surface_m[i],
    })) ?? []

  const options = scenario?.towns ?? []

  return (
    <Panel
      title={`Cross-section View${location ? ` (at ${location})` : ''}`}
      action={
        options.length > 0 ? (
          <select
            className="rounded border border-slate-300 px-1.5 py-0.5 text-[11px]"
            value={location ?? ''}
            onChange={(e) => onLocationChange(e.target.value)}
          >
            {options.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        ) : null
      }
    >
      {!runId && <p className="text-xs text-slate-500">No simulation loaded.</p>}
      {runId && section.isLoading && <Skeleton className="h-40" />}
      {runId && section.isError && (
        <p className="text-[11px] text-slate-600">
          {(section.error as Error)?.message ?? 'no cross-section for this location'}
        </p>
      )}
      {rows.length > 0 && (
        <>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={rows} margin={{ top: 4, right: 8, bottom: 16, left: 4 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="2 3" />
              <XAxis
                dataKey="offset"
                tick={AXIS}
                tickFormatter={(v) => `${Math.round(Number(v))}`}
                label={{
                  value: 'distance across the valley (m)',
                  position: 'insideBottom',
                  offset: -8,
                  ...AXIS,
                }}
              />
              <YAxis
                tick={AXIS}
                domain={['dataMin - 5', 'dataMax + 5']}
                label={{ value: 'elevation (m MSL)', angle: -90, position: 'insideLeft', ...AXIS }}
              />
              <Tooltip
                contentStyle={{ fontSize: 11 }}
                formatter={(value, name) => {
                  const n = typeof value === 'number' ? value : Number(value)
                  return [
                    Number.isFinite(n) ? `${n.toFixed(1)} m` : NOT_COMPUTED,
                    name === 'bed' ? 'Terrain' : 'Max water level',
                  ]
                }}
                labelFormatter={(v) => `${Math.round(Number(v))} m from centreline`}
              />
              <Area
                type="monotone"
                dataKey="water"
                name="Max water level"
                stroke="#0284c7"
                fill="#7fc4e8"
                fillOpacity={0.6}
                connectNulls={false}
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="bed"
                name="Terrain"
                stroke="#64748b"
                fill="#cbd5e1"
                fillOpacity={0.9}
                isAnimationActive={false}
              />
              <ReferenceLine x={0} stroke="#94a3b8" strokeDasharray="3 3" />
            </AreaChart>
          </ResponsiveContainer>
          <div className="mt-1 flex items-baseline justify-between text-[10px] text-slate-500">
            <span>
              chainage {formatNumber((section.data?.chainage_m ?? 0) / 1000, 1)} km downstream
            </span>
            {section.data?.offset_from_path_m != null &&
              section.data.offset_from_path_m > 2000 && (
                <span className="text-amber-700">
                  town is {formatNumber(section.data.offset_from_path_m / 1000, 1)} km from the
                  channel — this is a valley section, not a town section
                </span>
              )}
          </div>
        </>
      )}
    </Panel>
  )
}
