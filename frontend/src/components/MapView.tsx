import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useRef, useState } from 'react'
import type { ScenarioSummary, TownResult } from '../types/api'

/**
 * The inundation map.
 *
 * Depth is served as XYZ raster tiles from the backend rather than as GeoJSON
 * features. That is the spec's large-data requirement: a 30 m inundation
 * polygon set over a 120 km reach is tens of megabytes of geometry, and the
 * browser should never see it. Tiles keep the payload constant regardless of
 * how large the simulated domain is.
 *
 * The basemap uses a keyless raster source so the map works at a venue with no
 * accounts configured. Swapping in a satellite style is one constant.
 */

/** Legend bins, matching backend floodguard/postprocess/hazard.py DEPTH_BANDS. */
export const DEPTH_LEGEND = [
  { label: '> 10 m', colour: '#8b1a1a' },
  { label: '5 – 10 m', colour: '#e8762c' },
  { label: '2 – 5 m', colour: '#f2d024' },
  { label: '0.5 – 2 m', colour: '#7fc4e8' },
  { label: '0.1 – 0.5 m', colour: '#2b7bba' },
]

const BASEMAP_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
      maxzoom: 19,
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
}

export interface MapViewProps {
  runId: string | null
  scenario: ScenarioSummary | null
  towns: TownResult[]
  /** 0..1 through the simulated duration; null means show the maximum extent. */
  timeFraction: number | null
  className?: string
}

export default function MapView({
  runId,
  scenario,
  towns,
  timeFraction,
  className = '',
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markersRef = useRef<maplibregl.Marker[]>([])
  const [ready, setReady] = useState(false)

  // --- create the map once ---
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASEMAP_STYLE,
      center: [78.4, 30.2],
      zoom: 8,
      attributionControl: { compact: true },
    })
    map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'top-right')
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left')
    map.on('load', () => setReady(true))
    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // --- fly to the scenario ---
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !scenario) return
    map.flyTo({ center: [scenario.lon, scenario.lat], zoom: 9, duration: 900 })
  }, [ready, scenario])

  // --- depth tiles ---
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return

    if (map.getLayer('depth')) map.removeLayer('depth')
    if (map.getSource('depth')) map.removeSource('depth')
    if (!runId) return

    // `t` is carried so a future time-indexed tile endpoint can honour it. The
    // current backend serves the maximum-extent raster and ignores it; the
    // slider therefore animates the town markers and the legend readout, and
    // the UI says so rather than pretending the raster is changing.
    const t = timeFraction ?? 1
    map.addSource('depth', {
      type: 'raster',
      tiles: [`${window.location.origin}/api/results/${runId}/tiles/{z}/{x}/{y}.png?t=${t}`],
      tileSize: 256,
      minzoom: 5,
      maxzoom: 14,
    })
    map.addLayer({
      id: 'depth',
      type: 'raster',
      source: 'depth',
      paint: { 'raster-opacity': 0.75, 'raster-resampling': 'nearest' },
    })
  }, [ready, runId, timeFraction])

  // --- dam and town markers ---
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return

    markersRef.current.forEach((m) => m.remove())
    markersRef.current = []

    if (scenario) {
      const el = document.createElement('div')
      el.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center">
          <div style="width:0;height:0;border-left:8px solid transparent;
               border-right:8px solid transparent;border-bottom:14px solid #c62828"></div>
          <div style="background:#c62828;color:#fff;font:600 10px sans-serif;
               padding:1px 4px;border-radius:2px;white-space:nowrap;margin-top:1px">
            ${scenario.dam}
          </div>
        </div>`
      markersRef.current.push(
        new maplibregl.Marker({ element: el, anchor: 'bottom' })
          .setLngLat([scenario.lon, scenario.lat])
          .addTo(map),
      )
    }

    const arrived = timeFraction === null ? towns : towns.filter((t) => hasArrived(t, timeFraction, scenario))

    towns.forEach((town) => {
      const isWet = arrived.includes(town)
      const el = document.createElement('div')
      el.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center">
          <div style="width:9px;height:9px;border-radius:50%;
               background:${isWet ? '#1565c0' : '#fff'};
               border:2px solid ${isWet ? '#0d47a1' : '#607d8b'}"></div>
          <div style="background:rgba(255,255,255,.9);color:#263238;
               font:500 10px sans-serif;padding:0 3px;border-radius:2px;
               white-space:nowrap;margin-top:1px">${town.name}</div>
        </div>`
      el.title = town.arrival_min
        ? `${town.name}: arrives at ${Math.round(town.arrival_min)} min, depth ${town.max_depth_m?.toFixed(1) ?? '—'} m`
        : `${town.name}: not flooded in this run`
      markersRef.current.push(
        new maplibregl.Marker({ element: el, anchor: 'top' })
          .setLngLat([town.lon, town.lat])
          .addTo(map),
      )
    })
  }, [ready, scenario, towns, timeFraction])

  return (
    <div className={`relative ${className}`}>
      <div ref={containerRef} className="h-full w-full" />
      <Legend />
      {!runId && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="rounded bg-white/90 px-4 py-2 text-xs text-slate-600 shadow">
            No simulation loaded. Configure a scenario and run one to see the inundation map.
          </div>
        </div>
      )}
    </div>
  )
}

function hasArrived(
  town: TownResult,
  fraction: number,
  scenario: ScenarioSummary | null,
): boolean {
  if (town.arrival_min === null) return false
  const durationMin = (scenario?.duration_hours ?? 6) * 60
  return town.arrival_min <= fraction * durationMin
}

function Legend() {
  return (
    <div className="absolute bottom-6 right-2 rounded border border-slate-200 bg-white/95 p-2 text-[10px] shadow">
      <div className="mb-1 font-semibold uppercase tracking-wide text-slate-600">
        Water depth
      </div>
      {DEPTH_LEGEND.map((bin) => (
        <div key={bin.label} className="flex items-center gap-1.5 leading-tight">
          <span
            className="inline-block h-2.5 w-4 rounded-sm"
            style={{ background: bin.colour }}
          />
          <span className="text-slate-700">{bin.label}</span>
        </div>
      ))}
    </div>
  )
}
