import { useEffect, useMemo, useState } from 'react'
import { useJob, useJobSocket, useScenarios, useSubmitSimulation, useTowns } from '../api/hooks'
import { CrossSectionChart, HydrographChart } from '../components/Charts'
import InputPanel from '../components/InputPanel'
import MapView from '../components/MapView'
import {
  ComparisonTable,
  ExportPanel,
  ImpactPanel,
  ResultsPanel,
  TownTable,
} from '../components/ResultsPanel'
import { Panel, formatMinutes } from '../components/Value'
import type { SimulationRequest } from '../types/api'

type MapTab = 'inundation' | 'comparison' | '3d'

export default function Simulation() {
  const scenarios = useScenarios()
  const submit = useSubmitSimulation()

  const [jobId, setJobId] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [scenarioId, setScenarioId] = useState<string | null>(null)
  const [engineIndex, setEngineIndex] = useState(0)
  const [tab, setTab] = useState<MapTab>('inundation')
  const [sectionLocation, setSectionLocation] = useState<string | null>(null)

  const job = useJob(jobId)
  const { live, connected } = useJobSocket(jobId)
  const towns = useTowns(runId)

  const scenario = useMemo(
    () => scenarios.data?.find((s) => s.id === scenarioId) ?? null,
    [scenarios.data, scenarioId],
  )

  // The run id is the job id: the pipeline writes into data/runs/<job_id>.
  useEffect(() => {
    if (job.data?.status === 'succeeded' && jobId) setRunId(jobId)
  }, [job.data?.status, jobId])

  useEffect(() => {
    if (!sectionLocation && scenario?.towns.length) setSectionLocation(scenario.towns[0])
  }, [scenario, sectionLocation])

  const status = (live?.status as string) ?? job.data?.status ?? null
  const running = status === 'queued' || status === 'running'
  const fraction = live?.fraction ?? job.data?.fraction ?? 0
  const phase = live?.phase ?? job.data?.phase ?? ''
  const message = live?.message ?? job.data?.message ?? ''
  const eta = job.data?.eta_seconds ?? null

  // --- time slider ---
  const [timeFraction, setTimeFraction] = useState<number | null>(null)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    if (!playing) return
    const id = window.setInterval(() => {
      setTimeFraction((prev) => {
        const next = (prev ?? 0) + 0.02
        if (next >= 1) {
          setPlaying(false)
          return 1
        }
        return next
      })
    }, 120)
    return () => window.clearInterval(id)
  }, [playing])

  const handleRun = (request: SimulationRequest) => {
    setScenarioId(request.scenario_id ?? null)
    setRunId(null)
    setTimeFraction(null)
    submit.mutate(request, { onSuccess: (data) => setJobId(data.job_id) })
  }

  const handleReset = () => {
    setJobId(null)
    setRunId(null)
    setTimeFraction(null)
    setPlaying(false)
  }

  const durationMin = (scenario?.duration_hours ?? 6) * 60

  return (
    <div className="mx-auto max-w-[1600px] px-3 py-3">
      <div className="grid grid-cols-12 gap-3">
        {/* ---------------- left: inputs ---------------- */}
        <div className="col-span-12 space-y-3 lg:col-span-3">
          <InputPanel onRun={handleRun} running={running} onReset={handleReset} />

          {jobId && (
            <Panel
              title="Run Progress"
              action={
                <span
                  className={`text-[10px] ${connected ? 'text-emerald-600' : 'text-slate-400'}`}
                  title={
                    connected
                      ? 'live over WebSocket'
                      : 'WebSocket unavailable; falling back to polling'
                  }
                >
                  {connected ? '● live' : '○ polling'}
                </span>
              }
            >
              <div className="space-y-1.5">
                <div className="h-1.5 w-full overflow-hidden rounded bg-slate-200">
                  <div
                    className="h-full bg-sky-600 transition-all"
                    style={{ width: `${Math.round(fraction * 100)}%` }}
                  />
                </div>
                <div className="flex items-baseline justify-between text-[11px]">
                  <span className="font-medium text-slate-700">
                    {phase || status} — {Math.round(fraction * 100)}%
                  </span>
                  {eta !== null && running && (
                    <span className="text-slate-500">
                      ~{formatMinutes(eta / 60)} left
                    </span>
                  )}
                </div>
                {message && (
                  <p className="truncate font-mono text-[10px] text-slate-500" title={message}>
                    {message}
                  </p>
                )}
                {status === 'failed' && (
                  <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded bg-rose-50 p-1.5 text-[10px] text-rose-900">
                    {job.data?.error}
                  </pre>
                )}
                {running && (
                  <button
                    type="button"
                    onClick={() =>
                      fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' }).catch(() => {})
                    }
                    className="w-full rounded border border-slate-300 py-1 text-[11px]
                               text-slate-600 hover:bg-slate-50"
                  >
                    Cancel
                  </button>
                )}
              </div>
            </Panel>
          )}
        </div>

        {/* ---------------- centre: map ---------------- */}
        <div className="col-span-12 space-y-3 lg:col-span-6">
          <section className="overflow-hidden rounded border border-slate-200 bg-white">
            <div className="flex border-b border-slate-200">
              {(
                [
                  ['inundation', 'Flood Inundation Map'],
                  ['comparison', 'Comparison View'],
                  ['3d', '3D View (Beta)'],
                ] as Array<[MapTab, string]>
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setTab(value)}
                  className={`px-3 py-1.5 text-xs transition-colors ${
                    tab === value
                      ? 'border-b-2 border-sky-700 font-medium text-sky-800'
                      : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {tab === 'inundation' && (
              <>
                <MapView
                  runId={runId}
                  scenario={scenario}
                  towns={towns.data ?? []}
                  timeFraction={timeFraction}
                  className="h-[440px]"
                />
                <div className="flex items-center gap-2 border-t border-slate-100 px-3 py-2">
                  <button
                    type="button"
                    disabled={!runId}
                    onClick={() => {
                      if (timeFraction === null) setTimeFraction(0)
                      setPlaying((p) => !p)
                    }}
                    className="rounded bg-sky-700 px-2 py-1 text-[11px] text-white
                               disabled:bg-slate-300"
                  >
                    {playing ? '❚❚ Pause' : '▶ Play'}
                  </button>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    disabled={!runId}
                    value={timeFraction ?? 1}
                    onChange={(e) => {
                      setPlaying(false)
                      setTimeFraction(Number(e.target.value))
                    }}
                    className="flex-1"
                    aria-label="Simulation time"
                  />
                  <span className="w-24 text-right font-mono text-[11px] text-slate-600">
                    {timeFraction === null
                      ? 'max extent'
                      : formatMinutes(timeFraction * durationMin)}
                  </span>
                  <button
                    type="button"
                    disabled={!runId}
                    onClick={() => {
                      setPlaying(false)
                      setTimeFraction(null)
                    }}
                    className="rounded border border-slate-300 px-2 py-1 text-[11px]
                               text-slate-600 disabled:opacity-50"
                    title="Show the maximum extent over the whole simulation"
                  >
                    Max
                  </button>
                </div>
                <p className="border-t border-slate-100 px-3 py-1 text-[10px] leading-relaxed text-slate-500">
                  The slider marks each town as the wave reaches it, using computed arrival
                  times. The depth raster currently shows the maximum extent over the whole
                  simulation; time-indexed tiles are the next step, and the map says so
                  rather than implying the raster is animating.
                </p>
              </>
            )}

            {tab === 'comparison' && (
              <div className="p-3">
                <ComparisonTable runId={runId} />
              </div>
            )}

            {tab === '3d' && (
              <div className="flex h-[440px] items-center justify-center p-6 text-center">
                <div className="max-w-sm text-xs leading-relaxed text-slate-500">
                  <p className="mb-1 font-medium text-slate-700">3D View — not built yet</p>
                  <p>
                    A deck.gl TerrainLayer over the DEM with an animated water surface. It is
                    deliberately empty rather than showing a placeholder scene, so nothing on
                    this tab can be mistaken for a result.
                  </p>
                </div>
              </div>
            )}
          </section>

          <HydrographChart runId={runId} />
          <CrossSectionChart
            runId={runId}
            scenario={scenario}
            location={sectionLocation}
            onLocationChange={setSectionLocation}
          />
        </div>

        {/* ---------------- right: results ---------------- */}
        <div className="col-span-12 space-y-3 lg:col-span-3">
          <ResultsPanel
            runId={runId}
            engineIndex={engineIndex}
            onEngineChange={setEngineIndex}
          />
          <ImpactPanel runId={runId} />
          <TownTable runId={runId} />
          <ExportPanel runId={runId} />
        </div>
      </div>
    </div>
  )
}
