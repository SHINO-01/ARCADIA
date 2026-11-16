/** Lightweight SVG charts (no chart library): responsive, hover crosshair + tooltip, legends. */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(300)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(120, Math.floor(e.contentRect.width))))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

export interface Series { key: string; label: string; values: (number | null)[]; color: string; kind?: 'line' | 'area' | 'bar'; axis?: 'left' | 'right'; unit?: string; dashed?: boolean }
export interface Band { lo: number[]; hi: number[]; color: string; label?: string }

function niceTicks(lo: number, hi: number, n = 4) {
  if (!isFinite(lo) || !isFinite(hi) || lo === hi) return [lo || 0, (hi || 1)]
  const span = hi - lo
  const step0 = span / n
  const mag = 10 ** Math.floor(Math.log10(step0))
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => span / s <= n) ?? step0
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6))
  return out
}

export function TimeChart({ labels, series, bands = [], height = 160, fmtX = (s) => s, yMin, rightMin, emptyText = 'No data yet', marker }: {
  labels: string[]; series: Series[]; bands?: Band[]; height?: number; fmtX?: (s: string) => string; yMin?: number; rightMin?: number
  emptyText?: string; marker?: number
}) {
  const [ref, W] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const n = labels.length
  const pad = { l: 34, r: series.some((s) => s.axis === 'right') ? 34 : 10, t: 8, b: 20 }
  const H = height
  const iw = W - pad.l - pad.r
  const ih = H - pad.t - pad.b
  const dom = useMemo(() => {
    const side = (ax: 'left' | 'right') => {
      const vals: number[] = []
      for (const s of series) if ((s.axis ?? 'left') === ax) for (const v of s.values) if (v != null && isFinite(v)) vals.push(v)
      if (ax === 'left') for (const b of bands) vals.push(...b.lo, ...b.hi)
      if (!vals.length) return [0, 1] as [number, number]
      let lo = Math.min(...vals), hi = Math.max(...vals)
      const base = ax === 'left' ? yMin : rightMin
      if (base != null) lo = Math.min(lo, base)
      if (series.some((s) => (s.axis ?? 'left') === ax && s.kind === 'bar')) lo = Math.min(lo, 0)
      if (hi - lo < 1e-9) hi = lo + 1
      const padv = (hi - lo) * 0.08
      return [base != null ? lo : lo - padv, hi + padv] as [number, number]
    }
    return { left: side('left'), right: side('right') }
  }, [series, bands, yMin, rightMin])
  if (!n) return <div ref={ref} className="flex items-center justify-center text-xs text-fg-3" style={{ height: H }}>{emptyText}</div>
  const x = (i: number) => pad.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw)
  const y = (v: number, ax: 'left' | 'right' = 'left') => { const [lo, hi] = dom[ax]; return pad.t + ih - ((v - lo) / (hi - lo)) * ih }
  const bw = Math.max(1, (iw / n) * 0.7)
  const path = (vals: (number | null)[], ax: 'left' | 'right') => {
    let d = ''; let pen = false
    vals.forEach((v, i) => { if (v == null || !isFinite(v)) { pen = false; return } d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v, ax).toFixed(1)}`; pen = true })
    return d
  }
  const ticksL = niceTicks(dom.left[0], dom.left[1])
  const ticksR = pad.r > 10 ? niceTicks(dom.right[0], dom.right[1]) : []
  const xt = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 70))))
  return (
    <div ref={ref} className="relative select-none" style={{ height: H }}
      onMouseLeave={() => setHover(null)}
      onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); const i = Math.round(((e.clientX - r.left - pad.l) / iw) * (n - 1)); setHover(Math.max(0, Math.min(n - 1, i))) }}>
      <svg width={W} height={H} role="img" aria-label={series.map((s) => s.label).join(', ')}>
        {ticksL.map((t) => <g key={`l${t}`}><line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--color-line)" strokeDasharray="2 4" />
          <text x={pad.l - 6} y={y(t) + 3} textAnchor="end" className="num" fontSize="9" fill="var(--color-fg-3)">{+t.toFixed(2)}</text></g>)}
        {ticksR.map((t) => <text key={`r${t}`} x={W - pad.r + 6} y={y(t, 'right') + 3} className="num" fontSize="9" fill="var(--color-fg-3)">{+t.toFixed(2)}</text>)}
        {labels.map((l, i) => i % xt === 0 && <text key={i} x={x(i)} y={H - 5} textAnchor="middle" fontSize="9" fill="var(--color-fg-3)">{fmtX(l)}</text>)}
        {bands.map((b, k) => {
          const up = b.hi.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join('')
          const dn = b.lo.map((_, i) => `L${x(b.lo.length - 1 - i)},${y(b.lo[b.lo.length - 1 - i])}`).join('')
          return <path key={k} d={up + dn + 'Z'} fill={b.color} opacity={0.18} />
        })}
        {series.filter((s) => s.kind === 'bar').map((s) => s.values.map((v, i) => v != null && v > 0 &&
          <rect key={`${s.key}${i}`} x={x(i) - bw / 2} y={y(v, s.axis)} width={bw} height={Math.max(0, y(0, s.axis) - y(v, s.axis))} fill={s.color} opacity={0.75} rx={1} />))}
        {series.filter((s) => s.kind === 'area').map((s) => {
          const d = path(s.values, s.axis ?? 'left')
          const first = s.values.findIndex((v) => v != null); const last = s.values.length - 1 - [...s.values].reverse().findIndex((v) => v != null)
          return d && <path key={s.key + 'a'} d={`${d}L${x(last)},${y(dom[s.axis ?? 'left'][0], s.axis)}L${x(first)},${y(dom[s.axis ?? 'left'][0], s.axis)}Z`} fill={s.color} opacity={0.12} />
        })}
        {series.filter((s) => s.kind !== 'bar').map((s) => <path key={s.key} d={path(s.values, s.axis ?? 'left')} fill="none" stroke={s.color} strokeWidth={1.6}
          strokeDasharray={s.dashed ? '4 3' : undefined} strokeLinejoin="round" strokeLinecap="round" />)}
        {marker != null && marker >= 0 && marker < n && <line x1={x(marker)} x2={x(marker)} y1={pad.t} y2={pad.t + ih} stroke="var(--color-sun)" strokeDasharray="3 3" />}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="var(--color-fg-3)" />}
        {hover != null && series.filter((s) => s.kind !== 'bar').map((s) => s.values[hover] != null &&
          <circle key={s.key} cx={x(hover)} cy={y(s.values[hover] as number, s.axis)} r={3} fill={s.color} stroke="var(--color-bg)" />)}
      </svg>
      {hover != null && (
        <div className="pointer-events-none absolute top-1 z-10 rounded-lg border border-line-2 bg-bg/95 px-2 py-1 text-[10px] shadow-lg"
          style={{ left: Math.min(W - 150, Math.max(0, x(hover) + 10)) }}>
          <div className="mb-0.5 text-fg-2">{fmtX(labels[hover])}</div>
          {series.map((s) => s.values[hover] != null && <div key={s.key} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: s.color }} /><span className="text-fg-3">{s.label}</span>
            <span className="num ml-auto pl-3 text-fg">{(s.values[hover] as number).toFixed(Math.abs(s.values[hover] as number) < 10 ? 2 : 1)}{s.unit ? ` ${s.unit}` : ''}</span></div>)}
          {bands.map((b, k) => b.label && <div key={k} className="num text-fg-3">{b.label} {b.lo[hover]?.toFixed(1)}–{b.hi[hover]?.toFixed(1)}</div>)}
        </div>
      )}
    </div>
  )
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-fg-2">{items.map((i) => <span key={i.label} className="flex items-center gap-1.5">
    <span className="inline-block h-0.5 w-3 rounded" style={{ background: i.color, borderTop: i.dashed ? `1px dashed ${i.color}` : undefined }} />{i.label}</span>)}</div>
}

/** Uncertainty bar: P10-P90 range with the median marked, against a 0..max scale. */
export function FanBar({ q, max, color = 'var(--color-leaf)', unit = '', fmt = (v: number) => v.toFixed(1) }: { q: { p10: number; p50: number; p90: number }; max: number; color?: string; unit?: string; fmt?: (v: number) => string }) {
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / Math.max(max, 1e-9)) * 100))}%`
  return (
    <div>
      <div className="relative h-2.5 rounded-full bg-line">
        <div className="absolute inset-y-0 rounded-full opacity-40" style={{ left: pct(q.p10), right: `calc(100% - ${pct(q.p90)})`, background: color }} />
        <div className="absolute -inset-y-0.5 w-1 -translate-x-1/2 rounded-full" style={{ left: pct(q.p50), background: color }} />
      </div>
      <div className="num mt-1 flex justify-between text-[10px] text-fg-3"><span>P10 {fmt(q.p10)}</span><span className="text-fg">P50 {fmt(q.p50)} {unit}</span><span>P90 {fmt(q.p90)}</span></div>
    </div>
  )
}

/** Stacked distribution bar with a legend (crop stages, operations...). */
export function StackBar({ parts, height = 10 }: { parts: { label: string; value: number; color: string }[]; height?: number }) {
  const total = parts.reduce((a, p) => a + p.value, 0)
  return (
    <div>
      <div className="flex overflow-hidden rounded-full bg-line" style={{ height }}>
        {total > 0 && parts.filter((p) => p.value > 0).map((p) => <div key={p.label} title={`${p.label}: ${p.value}`} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} className="transition-[width] duration-500" />)}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-fg-2">
        {parts.filter((p) => p.value > 0).map((p) => <span key={p.label} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />{p.label}<span className="num text-fg-3">{p.value}</span></span>)}
      </div>
    </div>
  )
}

export function Ring({ value, size = 44, stroke = 5, color = 'var(--color-leaf)', children }: { value: number; size?: number; stroke?: number; color?: string; children?: ReactNode }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90"><circle cx={size / 2} cy={size / 2} r={r} stroke="var(--color-line)" strokeWidth={stroke} fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeWidth={stroke} fill="none" strokeLinecap="round" strokeDasharray={c}
          strokeDashoffset={c * (1 - Math.max(0, Math.min(1, value)))} className="transition-[stroke-dashoffset] duration-700" /></svg>
      <div className="absolute inset-0 flex items-center justify-center">{children}</div>
    </div>
  )
}
