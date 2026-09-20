import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useEngines } from '../api/hooks'
import { EngineBadge, Panel, Skeleton, formatNumber } from '../components/Value'

interface ValidationCheck {
  name: string
  passed: boolean
  threshold: string
  note: string
  plot: string | null
  metrics: Record<string, unknown>
}

interface ValidationResults {
  available: boolean
  passed?: boolean
  runtime_s?: number
  detail?: string
  checks: ValidationCheck[]
  caveat?: string
}

export default function About() {
  const engines = useEngines()
  const validation = useQuery({
    queryKey: ['validation'],
    queryFn: () => api<ValidationResults>('/api/validation'),
  })

  return (
    <div className="mx-auto max-w-[900px] px-4 py-8 text-sm leading-relaxed text-slate-700">
      <h1 className="text-2xl font-semibold text-navy">About FloodGuard India</h1>

      {/* --- problem statement --- */}
      <h2 className="mt-6 text-base font-semibold text-slate-900">Problem statement</h2>
      <p className="mt-1">
        Smart India Hackathon PS 26161 — <em>Dam Break Inundation Modelling Using
        Hydrodynamic Modelling of any River.</em> The system simulates dam-break and
        river-blockage scenarios from real DEM and hydrological data, produces inundation
        maps and GIS exports, and quantifies the humanitarian exposure inside the flooded
        area.
      </p>

      {/* --- demo reaches --- */}
      <h2 className="mt-6 text-base font-semibold text-slate-900">Demo reaches</h2>
      <ul className="mt-1 list-disc space-y-1 pl-5">
        <li>
          <strong>Tehri Dam (Uttarakhand)</strong> on the <strong>Bhagirathi</strong>,
          which joins the Alaknanda at <strong>Devprayag</strong> to form the Ganga, then
          flows through Rishikesh and Haridwar. 260 m of head in a Himalayan gorge: a
          deep, fast wave with minutes of warning.
        </li>
        <li>
          <strong>Hirakud Dam (Odisha)</strong> on the <strong>Mahanadi</strong>. 61 m of
          head behind a 4.8 km embankment on a deltaic plain, with more than twice Tehri&rsquo;s
          storage: a wide, slow wave with hours of warning.
        </li>
      </ul>
      <p className="mt-2 text-[13px] text-slate-600">
        They are deliberately opposite regimes. The same code runs both with nothing
        changed but a YAML file, which is what makes the framework general rather than
        tuned to one valley.
      </p>

      {/* --- verification, from the real file --- */}
      <h2 className="mt-8 text-base font-semibold text-slate-900">Solver verification</h2>
      <p className="mt-1">
        These are comparisons against exact analytical solutions, so the answers are not a
        matter of opinion. The table below is read from the file{' '}
        <code>make validate</code> wrote — this page cannot show a passing result that was
        not actually produced.
      </p>

      <div className="mt-3">
        {validation.isLoading && <Skeleton className="h-32" />}
        {validation.data && !validation.data.available && (
          <div className="rounded border border-amber-300 bg-amber-50 p-3 text-[12px] text-amber-900">
            {validation.data.detail}
          </div>
        )}
        {validation.data?.available && (
          <Panel
            title="Verification results"
            subtitle={`${validation.data.checks.filter((c) => c.passed).length} of ${
              validation.data.checks.length
            } checks passed in ${formatNumber(validation.data.runtime_s, 1)} s`}
          >
            <table className="w-full text-[12px]">
              <thead className="text-left text-[10px] uppercase text-slate-400">
                <tr>
                  <th className="pb-1">Check</th>
                  <th className="pb-1 w-16">Result</th>
                  <th className="pb-1">Criterion</th>
                </tr>
              </thead>
              <tbody>
                {validation.data.checks.map((c) => (
                  <tr key={c.name} className="border-t border-slate-50 align-top">
                    <td className="py-1 pr-2 text-slate-800">{c.name}</td>
                    <td className="py-1">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                          c.passed
                            ? 'bg-emerald-100 text-emerald-800'
                            : 'bg-rose-100 text-rose-800'
                        }`}
                      >
                        {c.passed ? 'PASS' : 'FAIL'}
                      </span>
                    </td>
                    <td className="py-1 font-mono text-[10px] text-slate-600">
                      {c.threshold}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {validation.data.caveat && (
              <p className="mt-3 border-t border-slate-100 pt-2 text-[11px] leading-relaxed text-slate-600">
                <strong>Verification is not validation.</strong>{' '}
                {validation.data.caveat.replace('Verification is not validation. ', '')}
              </p>
            )}
          </Panel>
        )}
      </div>

      {/* --- honesty statement --- */}
      <h2 className="mt-8 text-base font-semibold text-slate-900">Honesty statement</h2>
      <p className="mt-1">
        Every number this tool displays is computed from an input file on disk and carries
        provenance metadata: source dataset, engine, solver settings, git commit and UTC
        timestamp. Where a third-party solver such as Delft3D or DualSPHysics is not
        installed on the machine, the tool says so, names the substitute solver it ran
        instead, and repeats that substitution in the PDF report. A value that could not
        be computed renders as an em dash, never as zero — those are different statements
        about the world.
      </p>

      <div className="mt-3">
        <Panel title="Engines on this machine" subtitle="probed live, not configured">
          {engines.isLoading && <Skeleton className="h-24" />}
          <div className="space-y-2">
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
        </Panel>
      </div>

      {/* --- limitations --- */}
      <h2 className="mt-8 text-base font-semibold text-slate-900">Known limitations</h2>
      <ol className="mt-1 list-decimal space-y-1 pl-5 text-[13px]">
        <li>
          Copernicus GLO-30 is a <em>surface</em> model. Over forested valleys it sits
          above true ground, biasing depths low and arrival times late.
        </li>
        <li>
          Reservoir bathymetry beneath the water surface is <em>reconstructed</em> from a
          conic approximation calibrated to registered storage, not surveyed.
        </li>
        <li>
          Manning&rsquo;s n is uniform unless a land-cover raster is supplied. Friction is the
          second most sensitive parameter after grid resolution.
        </li>
        <li>
          Breach geometry dominates the uncertainty. The three empirical models disagree
          by more than the solver&rsquo;s own error in most scenarios.
        </li>
        <li>
          Results are resolution-sensitive: a coarse cell averages the channel with its
          banks and under-predicts the peak. Every output records the resolution it used.
        </li>
        <li>
          At a confluence only one limb is routed; the other&rsquo;s discharge is a boundary
          inflow, not a simulated limb.
        </li>
        <li>
          No sediment, no debris, and no downstream structural failure. A real dam-break
          surge carries an enormous sediment load and destroys bridges.
        </li>
      </ol>

      {/* --- documentation --- */}
      <h2 className="mt-8 text-base font-semibold text-slate-900">Documentation</h2>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[13px]">
        <li>
          <code>docs/METHODOLOGY.md</code> — equations, schemes, assumptions and all eight
          limitations
        </li>
        <li>
          <code>docs/DATA_SOURCES.md</code> — every dataset, licence, and what each one
          cannot tell you
        </li>
        <li>
          <code>docs/AUDIT.md</code> — what we found in every reference repository, file
          by file
        </li>
        <li>
          <code>docs/validation/</code> — verification plots and error tables
        </li>
      </ul>

      <h2 className="mt-8 text-base font-semibold text-slate-900">Attribution</h2>
      <p className="mt-1 text-[13px]">
        Dam attributes from the CWC National Register of Large Dams (NRLD-2019), cited per
        field. Terrain from Copernicus DEM GLO-30 (© DLR / ESA). Exposure layers from
        OpenStreetMap (ODbL) and WorldPop (CC BY 4.0). ANUGA (Apache-2.0) and PySPH
        (BSD/MIT) are installed dependencies; DualSPHysics (LGPL-2.1) and Delft3D
        (AGPL/GPL/LGPL/BSD) are invoked as external processes or read only for their file
        formats, with no source copied into this repository.
      </p>
    </div>
  )
}
