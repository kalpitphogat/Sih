/**
 * An explicit "not built yet" panel.
 *
 * Engineering rule 1: if a value cannot be computed yet, say so. A page that is
 * not implemented shows this, never a mock screenshot of one that is.
 */
export default function PhasePlaceholder({
  title,
  phase,
  children,
}: {
  title: string
  phase: string
  children: React.ReactNode
}) {
  return (
    <div className="mx-auto max-w-[900px] px-4 py-12">
      <h1 className="text-2xl font-semibold text-navy">{title}</h1>
      <div className="mt-3 inline-block rounded bg-amber-100 px-2 py-1 text-xs font-medium text-amber-800">
        Not implemented yet — arrives in {phase}
      </div>
      <div className="mt-4 max-w-2xl text-sm leading-relaxed text-slate-600">{children}</div>
    </div>
  )
}
