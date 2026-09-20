import { Link } from 'react-router-dom'
import { useEngines, useScenarios } from '../api/hooks'
import { EngineBadge, Panel, Skeleton } from '../components/Value'

export default function Home() {
  const scenarios = useScenarios()
  const engines = useEngines()

  return (
    <div className="mx-auto max-w-[1100px] px-4 py-8">
      <h1 className="text-2xl font-semibold text-navy">
        Dam-break and flash-flood inundation modelling for any Indian river
      </h1>
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-600">
        FloodGuard India simulates a dam break or river blockage from real terrain and
        hydrological data, routes the flood wave downstream with a verified two-dimensional
        shallow-water solver, and reports what lies inside the inundated area — with every
        number traceable to the file it came from.
      </p>

      {/* --- three-step flow --- */}
      <section className="mt-8 grid gap-3 sm:grid-cols-3">
        {[
          {
            step: '1',
            title: 'Choose a dam and a failure mode',
            body: 'Thirty dams from the CWC National Register, with per-field citations. Breach parameters come from three published models, shown side by side so you can see how much they disagree.',
          },
          {
            step: '2',
            title: 'Run the hydrodynamics',
            body: 'The breach hydrograph drives a Godunov finite-volume solver with an HLLC Riemann solver over real Copernicus terrain. It reproduces the Ritter and Stoker analytical solutions to better than 1% L2 error.',
          },
          {
            step: '3',
            title: 'Read the consequences',
            body: 'Depth, velocity and arrival time per town, exposure inside the flood extent, and an evacuation list sorted by lead time. Exports to SHP, KML, GeoJSON and GeoTIFF.',
          },
        ].map((s) => (
          <div key={s.step} className="rounded border border-slate-200 bg-white p-3">
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-navy text-xs font-semibold text-white">
              {s.step}
            </div>
            <h3 className="mt-2 text-sm font-medium text-slate-900">{s.title}</h3>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-600">{s.body}</p>
          </div>
        ))}
      </section>

      {/* --- demo scenarios --- */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Demo scenarios
        </h2>
        {scenarios.isLoading && <Skeleton className="mt-2 h-24" />}
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          {scenarios.data
            ?.filter((s) => s.valid)
            .map((s) => (
              <Link
                key={s.id}
                to="/simulation"
                className="block rounded border border-slate-200 bg-white p-3 transition-shadow hover:shadow"
              >
                <h3 className="text-sm font-medium text-slate-900">{s.name}</h3>
                <p className="mt-1 text-[11px] leading-relaxed text-slate-600">
                  {s.description}
                </p>
                <dl className="mt-2 grid grid-cols-3 gap-2 border-t border-slate-100 pt-2">
                  <Pair label="River" value={s.river} />
                  <Pair label="Reach" value={`${s.reach_length_km} km`} />
                  <Pair label="Duration" value={`${s.duration_hours} h`} />
                </dl>
              </Link>
            ))}
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          Tehri is steep and fast; Hirakud is flat and wide. The same code runs both with
          nothing changed but a YAML file, which is what makes the framework general rather
          than tuned to one valley.
        </p>
      </section>

      {/* --- honest capability report --- */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          What this machine can actually run
        </h2>
        <div className="mt-2">
          <Panel title="Hydrodynamic engines">
            {engines.isLoading && <Skeleton className="h-20" />}
            <div className="space-y-1.5">
              {engines.data?.engines.map((e) => (
                <div key={e.id} className="flex items-start gap-2">
                  <code className="w-24 shrink-0 text-[10px] text-slate-400">{e.id}</code>
                  <div className="flex-1">
                    <EngineBadge
                      displayName={e.display_name}
                      isRealSolver={e.is_real_solver}
                      substituted={!e.available}
                      detail={e.detail}
                    />
                    <p className="mt-0.5 text-[10px] leading-relaxed text-slate-500">
                      {e.detail}
                    </p>
                  </div>
                </div>
              ))}
            </div>
            {engines.data && (
              <p className="mt-2 border-t border-slate-100 pt-2 text-[10px] leading-relaxed text-slate-500">
                {engines.data.honesty_statement}
              </p>
            )}
          </Panel>
        </div>
      </section>
    </div>
  )
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[9px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="text-[11px] text-slate-700">{value}</dd>
    </div>
  )
}
