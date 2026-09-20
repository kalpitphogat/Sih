import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { Caveat, Panel, Skeleton, formatNumber } from '../components/Value'

interface MonitoringStatus {
  configured: boolean
  package_installed: boolean
  credentials_present: boolean
  detail: string
  collections: Record<string, string>
}

interface DetectionResult {
  flooded_area_km2: number | null
  permanent_water_area_km2: number | null
  threshold_db: number
  threshold_method: string
  polarisation: string
  pre_window: [string, string]
  post_window: [string, string]
  scene_counts: { pre: number; post: number }
  rainfall_mm: Array<{ date: string; mm: number | null }>
  warnings: string[]
  provenance: Record<string, string | number>
  agreement?: {
    computed: boolean
    reason?: string
    critical_success_index?: number | null
    probability_of_detection?: number | null
    false_alarm_ratio?: number | null
    interpretation?: string
    caveats?: string[]
  }
}

export default function RealtimeMonitoring() {
  const status = useQuery({
    queryKey: ['monitoring-status'],
    queryFn: () => api<MonitoringStatus>('/api/monitoring/status'),
  })

  const [bbox, setBbox] = useState('78.10, 29.90, 78.90, 30.74')
  const [postStart, setPostStart] = useState('')
  const [postEnd, setPostEnd] = useState('')
  const [runId, setRunId] = useState('')

  const detect = useMutation({
    mutationFn: () =>
      api<DetectionResult>('/api/monitoring/analyze', {
        method: 'POST',
        body: JSON.stringify({
          bbox: bbox.split(',').map((v) => Number(v.trim())),
          post_start: postStart,
          post_end: postEnd,
          run_id: runId || null,
        }),
      }),
  })

  return (
    <div className="mx-auto max-w-[1100px] px-4 py-6">
      <h1 className="text-xl font-semibold text-navy">Real-time Flood Monitoring</h1>
      <p className="mt-1 max-w-3xl text-sm leading-relaxed text-slate-600">
        Detects open water from Sentinel-1 radar backscatter, which works through cloud
        and at night — the conditions a flood actually happens in, and the reason optical
        imagery is useless for the first 48 hours of most events.
      </p>

      {/* --- capability state, shown before anything can be run --- */}
      <div className="mt-4">
        {status.isLoading && <Skeleton className="h-16" />}
        {status.data && !status.data.configured && (
          <div className="rounded border border-amber-300 bg-amber-50 p-3">
            <h2 className="text-sm font-semibold text-amber-900">
              Google Earth Engine is not configured
            </h2>
            <p className="mt-1 text-[12px] leading-relaxed text-amber-900">
              {status.data.detail}
            </p>
            <p className="mt-2 text-[11px] leading-relaxed text-amber-800">
              No live detection will run on this deployment, and nothing on this page is a
              live feed. This panel exists instead of a cached screenshot presented as
              current data.
            </p>
          </div>
        )}
        {status.data?.configured && (
          <div className="rounded border border-emerald-300 bg-emerald-50 p-3 text-[12px] text-emerald-900">
            {status.data.detail}
          </div>
        )}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[320px_1fr]">
        {/* --- inputs --- */}
        <div className="space-y-3">
          <Panel title="Area and dates">
            <div className="space-y-2">
              <label className="block">
                <span className="mb-0.5 block text-[11px] font-medium text-slate-600">
                  AOI bounding box (W, S, E, N)
                </span>
                <input
                  className={inputClass}
                  value={bbox}
                  onChange={(e) => setBbox(e.target.value)}
                />
              </label>
              <div className="grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="mb-0.5 block text-[11px] font-medium text-slate-600">
                    Post-event from
                  </span>
                  <input
                    type="date"
                    className={inputClass}
                    value={postStart}
                    onChange={(e) => setPostStart(e.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="mb-0.5 block text-[11px] font-medium text-slate-600">
                    to
                  </span>
                  <input
                    type="date"
                    className={inputClass}
                    value={postEnd}
                    onChange={(e) => setPostEnd(e.target.value)}
                  />
                </label>
              </div>
              <label className="block">
                <span className="mb-0.5 block text-[11px] font-medium text-slate-600">
                  Compare against run (optional)
                </span>
                <input
                  className={inputClass}
                  placeholder="run id"
                  value={runId}
                  onChange={(e) => setRunId(e.target.value)}
                />
              </label>
              <button
                type="button"
                disabled={!status.data?.configured || !postStart || !postEnd || detect.isPending}
                onClick={() => detect.mutate()}
                className="w-full rounded bg-sky-700 px-3 py-2 text-sm font-medium text-white
                           hover:bg-sky-800 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {detect.isPending ? 'Detecting…' : 'Detect flood extent'}
              </button>
              <p className="text-[10px] leading-relaxed text-slate-500">
                The pre-event baseline defaults to the 45 days before the post window.
                Sentinel-1 revisits every 6–12 days, so a window shorter than that may
                contain no scenes.
              </p>
            </div>
          </Panel>

          <Panel title="Method">
            <ol className="list-decimal space-y-1 pl-4 text-[11px] leading-relaxed text-slate-600">
              <li>Pre/post change detection on Sentinel-1 GRD backscatter.</li>
              <li>Refined Lee speckle filter, so noise is not read as flooding.</li>
              <li>Otsu threshold computed per scene, not fixed.</li>
              <li>Permanent water excluded using JRC Global Surface Water.</li>
              <li>
                Terrain shadow masked by local incidence angle — the dominant false
                positive in Himalayan terrain.
              </li>
            </ol>
          </Panel>
        </div>

        {/* --- results --- */}
        <div className="space-y-3">
          {detect.isError && (
            <div className="rounded border border-rose-300 bg-rose-50 p-3 text-[12px] text-rose-900">
              {(detect.error as Error)?.message ?? 'detection failed'}
            </div>
          )}

          {detect.data && (
            <>
              <Panel title="Detected extent">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Stat
                    label="Flooded area"
                    value={`${formatNumber(detect.data.flooded_area_km2, 2)} km²`}
                  />
                  <Stat
                    label="Permanent water"
                    value={`${formatNumber(detect.data.permanent_water_area_km2, 2)} km²`}
                  />
                  <Stat
                    label="Threshold"
                    value={`${formatNumber(detect.data.threshold_db, 2)} dB`}
                  />
                  <Stat
                    label="Scenes (pre / post)"
                    value={`${detect.data.scene_counts.pre} / ${detect.data.scene_counts.post}`}
                  />
                </div>
                <p className="mt-2 text-[10px] text-slate-500">
                  {detect.data.threshold_method}; {detect.data.provenance.method}
                </p>
                {detect.data.warnings.map((w, i) => (
                  <div key={i} className="mt-2">
                    <Caveat>{w}</Caveat>
                  </div>
                ))}
              </Panel>

              {detect.data.agreement && (
                <Panel title="Agreement with the simulation">
                  {!detect.data.agreement.computed ? (
                    <p className="text-[11px] text-slate-600">
                      {detect.data.agreement.reason}
                    </p>
                  ) : (
                    <>
                      <div className="grid grid-cols-3 gap-3">
                        <Stat
                          label="CSI"
                          value={formatNumber(
                            detect.data.agreement.critical_success_index,
                            3,
                          )}
                        />
                        <Stat
                          label="POD"
                          value={formatNumber(
                            detect.data.agreement.probability_of_detection,
                            3,
                          )}
                        />
                        <Stat
                          label="FAR"
                          value={formatNumber(detect.data.agreement.false_alarm_ratio, 3)}
                        />
                      </div>
                      <p className="mt-2 text-[10px] leading-relaxed text-slate-600">
                        {detect.data.agreement.interpretation}
                      </p>
                      {detect.data.agreement.caveats?.map((c, i) => (
                        <div key={i} className="mt-2">
                          <Caveat>{c}</Caveat>
                        </div>
                      ))}
                    </>
                  )}
                </Panel>
              )}
            </>
          )}

          {!detect.data && !detect.isError && (
            <Panel title="Results">
              <p className="text-[11px] leading-relaxed text-slate-500">
                Nothing has been detected yet. Results appear here after a run, with the
                threshold, the scene counts and the agreement score against a simulation —
                whatever those come out as.
              </p>
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}

const inputClass =
  'w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-800 ' +
  'focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500'

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="font-mono text-sm text-slate-900">{value}</div>
    </div>
  )
}
