import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', label: 'Home', end: true },
  { to: '/simulation', label: 'Simulation' },
  { to: '/monitoring', label: 'Real-time Monitoring' },
  { to: '/about', label: 'About' },
]

function WaveLogo() {
  return (
    <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true">
      <circle cx="17" cy="17" r="16" fill="#1c4b80" />
      <path
        d="M4 20c3.2 0 3.2-3 6.5-3s3.3 3 6.5 3 3.2-3 6.5-3 3.3 3 6.5 3"
        fill="none"
        stroke="#9ed8f5"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
      <path
        d="M4 25c3.2 0 3.2-3 6.5-3s3.3 3 6.5 3 3.2-3 6.5-3 3.3 3 6.5 3"
        fill="none"
        stroke="#ffffff"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  )
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <header className="bg-navy text-white">
        <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-4 py-2.5">
          <WaveLogo />
          <div className="leading-tight">
            <div className="text-lg font-semibold">FloodGuard India</div>
            <div className="text-[11px] text-sky-200">
              Dam Break &amp; Flash Flood Simulation for a Safer Tomorrow
            </div>
          </div>

          <nav className="ml-6 flex gap-1 text-sm">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded px-3 py-1.5 transition-colors ${
                    isActive ? 'bg-white/15 font-medium' : 'text-sky-100 hover:bg-white/10'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-4">
            <span className="hidden text-[11px] text-sky-200 lg:inline">
              Data Driven | Resilient Communities | Safer India
            </span>
            <button aria-label="Notifications" className="rounded p-1.5 hover:bg-white/10">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.7 21a2 2 0 0 1-3.4 0" />
              </svg>
            </button>
            <button aria-label="Account" className="rounded p-1.5 hover:bg-white/10">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="bg-navy text-[11px] text-sky-200">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between px-4 py-2.5">
          <span>Indian Rivers. Safer Communities.</span>
          <span>Built for a Resilient India | HADR | v1.0.0 🇮🇳</span>
        </div>
      </footer>
    </div>
  )
}
