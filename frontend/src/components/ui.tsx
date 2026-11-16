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

/** Card with a quiet heading row (icon + title + optional right slot). */
export function Card({ title, icon, right, children, className, bodyClass }: { title?: ReactNode; icon?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={clsx('panel flex min-h-0 flex-col', className)}>
      {title && (
        <header className="flex items-center gap-2 px-4 pt-3">
          {icon && <span className="text-fg-3">{icon}</span>}
          <h2 className="text-[13px] font-semibold text-fg">{title}</h2>
          <div className="ml-auto flex items-center gap-1.5">{right}</div>
        </header>
      )}
      <div className={clsx('scroll min-h-0 flex-1 overflow-auto', bodyClass ?? 'px-4 pb-4 pt-3')}>{children}</div>
    </section>
  )
}

/** Big-number KPI with unit and an optional caption. */
export function Kpi({ label, value, unit, caption, tone, icon }: { label: string; value: ReactNode; unit?: string; caption?: ReactNode; tone?: 'ok' | 'warn' | 'bad' | 'info'; icon?: ReactNode }) {
  return (
    <div className="panel px-4 py-3">
      <div className="label flex items-center gap-1.5">{icon}{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className={clsx('num text-2xl font-semibold leading-none tracking-tight', tone === 'ok' && 'text-leaf', tone === 'warn' && 'text-sun', tone === 'bad' && 'text-alert', tone === 'info' && 'text-sky')}>{value}</span>
        {unit && <span className="text-xs text-fg-3">{unit}</span>}
      </div>
      {caption && <div className="mt-1 text-[11px] text-fg-3">{caption}</div>}
    </div>
  )
}

export function Segmented<T extends string>({ value, options, onChange, size = 'sm' }: { value: T; options: { value: T; label: ReactNode; title?: string }[]; onChange: (v: T) => void; size?: 'sm' | 'xs' }) {
  return (
    <div role="radiogroup" className="inline-flex rounded-lg border border-line-2 bg-bg p-0.5">
      {options.map((o) => (
        <button key={o.value} role="radio" aria-checked={value === o.value} title={o.title} onClick={() => onChange(o.value)}
          className={clsx('cursor-pointer rounded-md font-medium transition-colors duration-200', size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-2 py-0.5 text-[10px]',
            value === o.value ? 'bg-panel-2 text-fg shadow-sm' : 'text-fg-3 hover:text-fg')}>{o.label}</button>
      ))}
    </div>
  )
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={clsx('relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors duration-200', checked ? 'border-leaf-2 bg-leaf-2/40' : 'border-line-2 bg-line')}>
      <span className={clsx('inline-block h-3.5 w-3.5 rounded-full bg-fg transition-transform duration-200', checked ? 'translate-x-[18px]' : 'translate-x-[3px]')} />
    </button>
  )
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
      {icon && <div className="text-fg-3">{icon}</div>}
      <div className="text-sm font-medium text-fg-2">{title}</div>
      {children && <div className="max-w-sm text-xs text-fg-3">{children}</div>}
    </div>
  )
}
