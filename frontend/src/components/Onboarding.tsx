/** First run: no farm exists yet -> find the land and draw the field. */
import { useState } from 'react'
import { CloudSun, Layers, Loader2, MapPin, Pencil, ScanSearch, Search, Sprout, X } from 'lucide-react'
import { Logo } from './Shell'

interface Place { display_name: string; lat: string; lon: string; type: string }

export function Onboarding({ onLocate, onDraw, onClose }: { onLocate: (lng: number, lat: number) => void; onDraw: () => void; onClose: () => void }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Place[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const search = async () => {
    if (!q.trim()) return
    setBusy(true); setErr(null)
    try {
      const r = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=5&q=${encodeURIComponent(q)}`, { headers: { Accept: 'application/json' } })
      const j = (await r.json()) as Place[]
      setResults(j)
      if (!j.length) setErr('No place found. Try a town, county or farm road name.')
    } catch { setErr('Place search is unavailable; pan the map to your land instead.') } finally { setBusy(false) }
  }
  const steps = [
    { icon: <Pencil size={15} />, title: 'Draw the field', text: 'Any shape or size.' },
    { icon: <ScanSearch size={15} />, title: 'Read the site', text: 'Roads, buildings, water and trees from satellite imagery; 10 years of climate; soil.' },
    { icon: <Layers size={15} />, title: 'Lay it out', text: 'Beds, tramlines and headland optimised per zone for yield.' },
    { icon: <Sprout size={15} />, title: 'Grow', text: 'Pick the crop the simulation recommends, then run the season.' },
  ]
  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-[2px] anim-fade" role="dialog" aria-modal="true" aria-labelledby="onb-title">
      <div className="panel relative w-full max-w-[560px] p-6 shadow-2xl">
        <button className="btn btn-ghost absolute right-3 top-3 px-2" onClick={onClose} aria-label="Close"><X size={14} /></button>
        <div className="flex items-center gap-3"><Logo size={40} /><div>
          <h2 id="onb-title" className="text-lg font-semibold">Set up your farm</h2>
          <p className="text-xs text-fg-3">ARCADIA builds a living model of your field: weather, soil, water, crop and machines.</p></div></div>
        <ol className="mt-5 grid grid-cols-2 gap-2">
          {steps.map((s, i) => <li key={s.title} className="rounded-xl border border-line bg-panel-2/60 px-3 py-2.5">
            <div className="flex items-center gap-2 text-[13px] font-medium"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-leaf-2/20 text-[10px] font-semibold text-leaf">{i + 1}</span>{s.title}<span className="ml-auto text-fg-3">{s.icon}</span></div>
            <p className="mt-1 text-[11px] leading-snug text-fg-3">{s.text}</p></li>)}
        </ol>
        <div className="mt-5">
          <label htmlFor="onb-q" className="label">Where is the land?</label>
          <div className="mt-1.5 flex gap-1.5">
            <div className="relative flex-1"><Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-3" />
              <input id="onb-q" className="field pl-8" placeholder="Town, county, road…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} autoFocus /></div>
            <button className="btn" onClick={search} disabled={busy}>{busy ? <Loader2 size={13} className="animate-spin" /> : 'Find'}</button>
          </div>
          {err && <p className="mt-1.5 text-[11px] text-sun">{err}</p>}
          {!!results.length && <ul className="mt-2 max-h-40 space-y-1 overflow-auto">{results.map((r) => <li key={r.display_name}>
            <button className="flex w-full cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-line" onClick={() => { onLocate(Number(r.lon), Number(r.lat)); setResults([]) }}>
              <MapPin size={13} className="mt-0.5 shrink-0 text-leaf" /><span className="text-fg-2">{r.display_name}</span></button></li>)}</ul>}
        </div>
        <div className="mt-5 flex items-center gap-2">
          <p className="flex items-center gap-1.5 text-[11px] text-fg-3"><CloudSun size={13} />Place search by OpenStreetMap Nominatim</p>
          <button className="btn btn-solid ml-auto" onClick={onDraw}><Pencil size={13} />Draw my field</button>
        </div>
      </div>
    </div>
  )
}
