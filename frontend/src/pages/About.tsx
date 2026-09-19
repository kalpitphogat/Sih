export default function About() {
  return (
    <div className="mx-auto max-w-[900px] px-4 py-10 text-sm leading-relaxed text-slate-700">
      <h1 className="text-2xl font-semibold text-navy">About FloodGuard India</h1>

      <h2 className="mt-6 font-semibold text-slate-900">Problem statement</h2>
      <p className="mt-1">
        Smart India Hackathon PS 26161 — <em>Dam Break Inundation Modelling Using Hydrodynamic
        Modelling of any River.</em> The system simulates dam-break and river-blockage scenarios
        from real DEM and hydrological data, produces inundation maps and GIS exports, and
        quantifies the humanitarian exposure inside the flooded area.
      </p>

      <h2 className="mt-6 font-semibold text-slate-900">Demo reaches</h2>
      <ul className="mt-1 list-disc pl-5">
        <li>
          <strong>Tehri Dam (Uttarakhand)</strong> on the <strong>Bhagirathi</strong>, which joins
          the Alaknanda at <strong>Devprayag</strong> to form the Ganga, then flows through
          Rishikesh and Haridwar.
        </li>
        <li>
          <strong>Hirakud Dam (Odisha)</strong> on the <strong>Mahanadi</strong> — flatter terrain,
          much wider spread, included to prove the framework generalises.
        </li>
      </ul>

      <h2 className="mt-6 font-semibold text-slate-900">Honesty statement</h2>
      <p className="mt-1">
        Every number this tool displays is computed from an input file on disk and carries
        provenance metadata (source dataset, engine, solver settings, git commit, UTC timestamp).
        Where a third-party solver such as Delft3D or DualSPHysics is not installed on the
        machine, the tool says so, names the substitute solver it ran instead, and repeats that
        substitution in the PDF report. The live capability table is on the Home page.
      </p>

      <h2 className="mt-6 font-semibold text-slate-900">Validation</h2>
      <p className="mt-1">
        The 2D solver is checked against the Ritter dry-bed and Stoker wet-bed analytical
        dam-break solutions, a frictional dam-break benchmark, a lake-at-rest well-balancedness
        test and a mass-conservation test. Plots and error tables will be published here and in
        <code className="mx-1">docs/validation/</code>. Nothing is claimed as validated until
        those plots exist.
      </p>
    </div>
  )
}
