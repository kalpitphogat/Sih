import PhasePlaceholder from '../components/PhasePlaceholder'

export default function RealtimeMonitoring() {
  return (
    <PhasePlaceholder title="Real-time Monitoring" phase="Phase 8">
      <p>
        Near-real-time flood extent from Sentinel-1 GRD via Google Earth Engine: pre/post-event
        VV/VH change detection, permanent water excluded using JRC Global Surface Water, terrain
        shadow masked with the DEM, then overlaid on the simulated extent with a CSI/F1 agreement
        score.
      </p>
      <p className="mt-3">
        Requires a GEE service account in <code>GOOGLE_APPLICATION_CREDENTIALS</code>. Without
        it this page will show a clear &ldquo;GEE not configured&rdquo; state and a labelled
        cached demo result — never a fake live feed.
      </p>
    </PhasePlaceholder>
  )
}
