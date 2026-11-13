import { clsx } from 'clsx'
import type { ReactNode } from 'react'

export function Panel({ title, right, children, className, bodyClass }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={clsx('panel flex min-h-0 flex-col', className)}>
      {title && (
        <header className="flex items-center justify-between border-b border-line px-3 py-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-2">{title}</h2>
          {right}
        </header>
      )}
      <div className={clsx('scroll min-h-0 flex-1 overflow-auto', bodyClass ?? 'p-3')}>{children}</div>
    </section>
  )
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'ok' | 'warn' | 'bad' }) {
  return (
    <div className="rounded-md border border-line bg-panel-2/60 px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wide text-fg-3">{label}</div>
      <div className={clsx('mono text-base font-semibold leading-tight', tone === 'ok' && 'text-leaf', tone === 'warn' && 'text-sun', tone === 'bad' && 'text-alert')}>{value}</div>
      {sub && <div className="text-[10px] text-fg-3">{sub}</div>}
    </div>
  )
}

export function Dot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span className="relative inline-flex h-2 w-2">
      {pulse && <span className="absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: color, animation: 'pulse-ring 1.2s ease-out infinite' }} />}
      <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: color }} />
    </span>
  )
}

export function Bar({ value, color, className }: { value: number; color?: string; className?: string }) {
  const c = color ?? (value < 20 ? '#ff4d5e' : value < 40 ? '#f5c451' : '#7ce38b')
  return (
    <div className={clsx('h-1.5 w-full overflow-hidden rounded-full bg-line', className)}>
      <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: c }} />
    </div>
  )
}

export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'ok' | 'warn' | 'bad' | 'info' }) {
  return (
    <span className={clsx('chip', tone === 'neutral' && 'bg-line text-fg-2', tone === 'ok' && 'bg-leaf-2/20 text-leaf', tone === 'warn' && 'bg-sun/20 text-sun',
      tone === 'bad' && 'bg-alert/20 text-alert', tone === 'info' && 'bg-sky/20 text-sky')}>{children}</span>
  )
}

export function Spark({ data, color = '#7ce38b', h = 28, w = 120, max }: { data: number[]; color?: string; h?: number; w?: number; max?: number }) {
  if (!data.length) return <svg width={w} height={h} />
  const mx = max ?? Math.max(...data, 1e-6)
  const mn = Math.min(...data, 0)
  const pts = data.map((v, i) => `${(i / Math.max(1, data.length - 1)) * w},${h - ((v - mn) / Math.max(1e-6, mx - mn)) * (h - 2) - 1}`).join(' ')
  return (
    <svg width={w} height={h} className="block">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}
