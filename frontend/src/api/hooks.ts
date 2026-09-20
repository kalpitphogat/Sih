/** TanStack Query hooks. One place where every endpoint is called. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from './client'
import type {
  Comparison,
  CrossSectionResponse,
  DamSummary,
  EngineHealth,
  HydrographResponse,
  ImpactResponse,
  JobCreated,
  JobState,
  ResultSummary,
  RiverSummary,
  ScenarioSummary,
  SimulationRequest,
  TownResult,
  BreachComparison,
} from '../types/api'

export function useEngines() {
  return useQuery({
    queryKey: ['engines'],
    queryFn: () => api<EngineHealth>('/api/health/engines'),
    staleTime: 60_000,
  })
}

export function useRivers() {
  return useQuery({
    queryKey: ['rivers'],
    queryFn: () => api<RiverSummary[]>('/api/rivers'),
    staleTime: Infinity,
  })
}

export function useDams(river?: string) {
  return useQuery({
    queryKey: ['dams', river ?? 'all'],
    queryFn: () =>
      api<DamSummary[]>(`/api/dams${river ? `?river=${encodeURIComponent(river)}` : ''}`),
    staleTime: Infinity,
  })
}

export function useScenarios() {
  return useQuery({
    queryKey: ['scenarios'],
    queryFn: () => api<ScenarioSummary[]>('/api/scenarios'),
    staleTime: Infinity,
  })
}

/** Breach predictions for the ghost hints, without running a simulation. */
export function useBreachPreview(request: SimulationRequest | null) {
  return useQuery({
    queryKey: ['breach-preview', JSON.stringify(request)],
    queryFn: () =>
      api<BreachComparison>('/api/scenarios/validate', {
        method: 'POST',
        body: JSON.stringify(request),
      }),
    enabled: !!request,
    retry: false,
  })
}

export function useSubmitSimulation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (request: SimulationRequest) =>
      api<JobCreated>('/api/simulate', {
        method: 'POST',
        body: JSON.stringify(request),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api<JobState>(`/api/jobs/${jobId}`),
    enabled: !!jobId,
    // The WebSocket carries live progress; this poll is the fallback for when
    // the socket cannot connect, and the safety net for a missed close frame.
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'running' || status === 'queued' ? 4000 : false
    },
  })
}

/**
 * Live job progress over the WebSocket, falling back silently to the poll in
 * `useJob` when the socket cannot be established.
 */
export function useJobSocket(jobId: string | null) {
  const [state, setState] = useState<Partial<JobState> | null>(null)
  const [connected, setConnected] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!jobId) {
      setState(null)
      return
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/ws/jobs/${jobId}`

    let closed = false
    let ws: WebSocket
    try {
      ws = new WebSocket(url)
    } catch {
      return
    }
    socketRef.current = ws

    ws.onopen = () => !closed && setConnected(true)
    ws.onclose = () => !closed && setConnected(false)
    ws.onerror = () => !closed && setConnected(false)
    ws.onmessage = (event) => {
      if (closed) return
      try {
        const payload = JSON.parse(event.data)
        if (payload.type === 'ping') return
        setState((prev) => ({ ...(prev ?? {}), ...payload }))
      } catch {
        /* a malformed frame must not break the panel */
      }
    }

    return () => {
      closed = true
      ws.close()
      socketRef.current = null
    }
  }, [jobId])

  return { live: state, connected }
}

export function useResultSummary(runId: string | null) {
  return useQuery({
    queryKey: ['result', runId, 'summary'],
    queryFn: () => api<ResultSummary>(`/api/results/${runId}/summary`),
    enabled: !!runId,
    retry: false,
  })
}

export function useTowns(runId: string | null) {
  return useQuery({
    queryKey: ['result', runId, 'towns'],
    queryFn: () => api<TownResult[]>(`/api/results/${runId}/towns`),
    enabled: !!runId,
    retry: false,
  })
}

export function useComparison(runId: string | null) {
  return useQuery({
    queryKey: ['result', runId, 'comparison'],
    queryFn: () => api<Comparison>(`/api/results/${runId}/comparison`),
    enabled: !!runId,
    retry: false,
  })
}

export function useImpact(runId: string | null) {
  return useQuery({
    queryKey: ['result', runId, 'impact'],
    queryFn: () => api<ImpactResponse>(`/api/results/${runId}/impact`),
    enabled: !!runId,
    retry: false,
  })
}

export function useHydrographs(runId: string | null) {
  return useQuery({
    queryKey: ['result', runId, 'hydrographs'],
    queryFn: () => api<HydrographResponse>(`/api/results/${runId}/hydrographs`),
    enabled: !!runId,
    retry: false,
  })
}

export function useCrossSection(runId: string | null, location: string | null) {
  return useQuery({
    queryKey: ['result', runId, 'cross-section', location],
    queryFn: () =>
      api<CrossSectionResponse>(
        `/api/results/${runId}/cross-section?location=${encodeURIComponent(location!)}`,
      ),
    enabled: !!runId && !!location,
    retry: false,
  })
}
