/**
 * Primitives that enforce engineering rule 1 in the UI layer.
 *
 * A value that has not been computed renders as an em dash with a tooltip
 * explaining why. It is never rendered as 0, and there is no code path in the
 * app that turns a null into a number — which is why these components exist
 * rather than each panel formatting its own values.
 */

export const NOT_COMPUTED = '—'

export function formatNumber(
  value: number | null | undefined,
  decimals = 1,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return NOT_COMPUTED
  }
  return value.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return NOT_COMPUTED
  }
  return Math.round(value).toLocaleString('en-IN')
}

export function formatMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return NOT_COMPUTED
  }
  if (value < 90) return `${Math.round(value)} min`
  const hours = Math.floor(value / 60)
  const minutes = Math.round(value % 60)
  return `${hours} h ${minutes.toString().padStart(2, '0')} min`
}

/** A KPI card. `value` of null shows an em dash and the reason on hover. */
export function KpiCard({
  label,
  value,
  unit,
  decimals = 1,
  reason,
  accent = 'sky',
}: {
  label: string
  value: number | null | undefined
  unit?: string
  decimals?: number
  reason?: string
  accent?: 'sky' | 'amber' | 'rose' | 'emerald'
}) {
  const computed = value !== null && value !== undefined && Number.isFinite(value)
  const accents: Record<string, string> = {
    sky: 'border-sky-200 bg-sky-50',
    amber: 'border-amber-200 bg-amber-50',
    rose: 'border-rose-200 bg-rose-50',
    emerald: 'border-emerald-200 bg-emerald-50',
  }

  return (
    <div
      className={`rounded border px-3 py-2.5 ${computed ? accents[accent] : 'border-slate-200 bg-slate-50'}`}
      title={computed ? undefined : reason || 'not computed'}
    >
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 flex items-baseline gap-1">
        <span
          className={`text-xl font-semibold tabular-nums ${computed ? 'text-slate-900' : 'text-slate-400'}`}
        >
          {formatNumber(value, decimals)}
        </span>
        {computed && unit && <span className="text-xs text-slate-500">{unit}</span>}
      </div>
      {!computed && (
        <div className="mt-0.5 text-[10px] leading-tight text-slate-400">
          not computed
        </div>
      )}
    </div>
  )
}

/** A small labelled statistic in a dense row. */
export function Stat({
  label,
  value,
  title,
}: {
  label: string
  value: string
  title?: string
}) {
  return (
    <div title={title}>
      <dt className="text-[11px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="font-mono text-xs text-slate-800">{value}</dd>
    </div>
  )
}

/**
 * The engine badge. It renders `display_name` verbatim, which is what keeps
 * the UI from claiming Delft3D when Delft3D is not installed.
 */
export function EngineBadge({
  displayName,
  isRealSolver,
  substituted,
  detail,
}: {
  displayName: string
  isRealSolver: boolean
  substituted?: boolean
  detail?: string
}) {
  const tone = substituted
    ? 'bg-amber-100 text-amber-900 border-amber-300'
    : isRealSolver
      ? 'bg-emerald-100 text-emerald-900 border-emerald-300'
      : 'bg-slate-100 text-slate-700 border-slate-300'

  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${tone}`}
      title={detail}
    >
      {substituted && <span aria-hidden="true">⚠</span>}
      {displayName}
    </span>
  )
}

/** A caveat the user must see before quoting a number. */
export function Caveat({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-900">
      {children}
    </div>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-slate-200 ${className}`} />
}

export function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title: string
  subtitle?: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="rounded border border-slate-200 bg-white">
      <header className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">
            {title}
          </h2>
          {subtitle && <p className="text-[11px] text-slate-400">{subtitle}</p>}
        </div>
        {action}
      </header>
      <div className="p-3">{children}</div>
    </section>
  )
}
