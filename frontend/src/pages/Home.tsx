import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { EngineStatus, Health } from '../types/health'

/**
 * Phase 0 shell. It renders exactly one thing beyond the chrome: the live
 * capability report from the backend. That proves the frontend/backend wiring
 * is real without inventing any scientific content.
 */
export default function Home() {
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/health') })
  const engines = useQuery({
    queryKey: ['engines'],
    queryFn: () => api<{ engines: EngineStatus[]; honesty_statement: string }>('/api/health/engines'),
  })

  return (
    <div className="mx-auto max-w-[1100px] px-4 py-8">
      <h1 className="text-2xl font-semibold text-navy">FloodGuard India</h1>
      <p className="mt-1 max-w-3xl text-sm text-slate-600">
        Dam-break and flash-flood inundation modelling for any Indian river. Smart India
        Hackathon PS 26161. This build is at <strong>Phase 0</strong>: the scaffold is up and the
        backend reports its real capabilities below. No simulation results exist yet, and none
        are shown.
      </p>

      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Backend status
        </h2>
        <div className="mt-2 rounded border border-slate-200 bg-white p-4 text-sm">
          {health.isLoading && <span className="text-slate-400">Checking…</span>}
          {health.isError && (
            <span className="text-red-600">
              Backend unreachable. Start it with <code>make serve-backend</code>.
            </span>
          )}
          {health.data && (
            <dl className="grid grid-cols-2 gap-x-8 gap-y-1 sm:grid-cols-3">
              <Item label="Status" value={health.data.status} />
              <Item label="Version" value={health.data.version} />
              <Item label="Git commit" value={health.data.git_commit} />
              <Item label="Python" value={health.data.python} />
              <Item label="Platform" value={health.data.platform} />
              <Item label="Generated (UTC)" value={health.data.generated_utc} />
            </dl>
          )}
        </div>
      </section>

      <section className="mt-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Hydrodynamic engines on this machine
        </h2>
        <div className="mt-2 overflow-hidden rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2">Engine</th>
                <th className="px-3 py-2">Reported as</th>
                <th className="px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {engines.data?.engines.map((e) => (
                <tr key={e.id} className="border-t border-slate-100 align-top">
                  <td className="px-3 py-2 font-mono text-xs text-slate-500">{e.id}</td>
                  <td className="px-3 py-2">
                    <div className="font-medium text-slate-800">{e.display_name}</div>
                    <div className="mt-0.5 text-xs text-slate-500">{e.detail}</div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {e.available ? (
                      <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
                        {e.kind === 'native' ? 'native solver' : 'real binary'}
                      </span>
                    ) : (
                      <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                        unavailable{e.substitute_id ? ` → ${e.substitute_id}` : ''}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {engines.data && (
          <p className="mt-2 max-w-3xl text-xs leading-relaxed text-slate-500">
            {engines.data.honesty_statement}
          </p>
        )}
      </section>
    </div>
  )
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="font-mono text-xs text-slate-800">{value}</dd>
    </div>
  )
}
