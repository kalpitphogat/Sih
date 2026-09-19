import PhasePlaceholder from '../components/PhasePlaceholder'

export default function Simulation() {
  return (
    <PhasePlaceholder title="Simulation" phase="Phase 9 (UI), backed by Phases 1–7">
      <p>
        The three-column simulation workspace — river/dam selection, scenario configuration,
        engine selection, the MapLibre inundation map with a time slider, KPI cards, HADR
        impact cards, the engine comparison table and the export panel.
      </p>
      <p className="mt-3">
        It is deliberately empty until the data pipeline, the solver and the API behind it are
        real, so that nothing on this page is ever a placeholder number.
      </p>
    </PhasePlaceholder>
  )
}
