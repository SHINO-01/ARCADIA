/** Embedded Webots W3D stream with camera-follow controls (Viewpoint.follow via the supervisor). */
import { useEffect, useState } from 'react'
import { Box, Eye, ExternalLink, RefreshCw, WifiOff } from 'lucide-react'
import { clsx } from 'clsx'
import { api } from '../lib/api'
import { useLive } from '../store/live'
import { useUI } from '../store/ui'

// Same origin by default: Caddy proxies /webots-stream to the native streaming server, so the 3D view also
// works through a tunnel or a public domain. VITE_WEBOTS_STREAM_URL overrides it (e.g. ws://localhost:1234).
const STREAM_URL = (import.meta.env.VITE_WEBOTS_STREAM_URL as string | undefined) || `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/webots-stream`

export function WebotsView({ controls = true }: { controls?: boolean }) {
  const bridge = useLive((s) => s.bridge)
  const fleet = useLive((s) => s.fleet)
  const { follow, setFollow, notify } = useUI()
  const [nonce, setNonce] = useState(0)
  const [reachable, setReachable] = useState<boolean | null>(null)
  const rosLive = !!bridge?.ros_connected
  useEffect(() => {
    let alive = true
    const probe = () => { try { const ws = new WebSocket(STREAM_URL); const t = setTimeout(() => { ws.close(); if (alive) setReachable(false) }, 2500); ws.onopen = () => { clearTimeout(t); ws.close(); if (alive) setReachable(true) }; ws.onerror = () => { clearTimeout(t); if (alive) setReachable(false) } } catch { if (alive) setReachable(false) } }
    probe(); const id = setInterval(probe, reachable ? 30000 : 15000); return () => { alive = false; clearInterval(id) }
  }, [nonce, reachable])
  const setCam = async (id: string | null) => { try { await api.setView(id); setFollow(id); notify(id ? `Camera following ${id}` : 'Camera: farm overview', 'ok') } catch (e) { notify((e as Error).message, 'err') } }
  const viewerUrl = `/webots-viewer.html?url=${encodeURIComponent(STREAM_URL)}`
  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg border border-line bg-black">
      {reachable ? <iframe key={nonce} title="Webots 3D stream" src={viewerUrl} className="h-full w-full border-0" allow="fullscreen" /> : (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full border border-line-2 text-fg-3">{reachable === null ? <RefreshCw size={18} className="animate-spin" /> : <WifiOff size={18} />}</span>
          <div className="text-sm font-semibold text-fg-2">Webots stream offline</div>
          <p className="max-w-sm text-xs text-fg-3">{rosLive ? <>ROS 2 is connected but no streaming server answers at <span className="mono">{STREAM_URL}</span>.</> : <>The native simulator is not running. Start it with <span className="kbd">make sim</span>; the headless twin keeps the map live meanwhile.</>}</p>
          <button className="btn mt-2" onClick={() => { setReachable(null); setNonce((n) => n + 1) }}><RefreshCw size={12} />retry</button>
        </div>)}
      <div className="pointer-events-none absolute left-2 top-2 flex items-center gap-1.5 rounded bg-bg/80 px-2 py-1 text-[10px] text-fg-2"><Box size={11} className="text-sky" />Webots R2025a · W3D{follow ? ` · following ${follow}` : ''}</div>
      {controls && reachable && (
        <div className="pointer-events-auto absolute right-2 top-2 flex items-center gap-1 rounded-md border border-line bg-bg/85 p-1 text-[10px]">
          <Eye size={11} className="ml-1 text-fg-3" />
          <button className={clsx('rounded px-1.5 py-0.5', !follow ? 'bg-leaf-2/20 text-leaf' : 'text-fg-2 hover:bg-line')} onClick={() => setCam(null)}>overview</button>
          {Object.values(fleet).filter((r) => r.active).map((r) => <button key={r.id} className={clsx('rounded px-1.5 py-0.5', follow === r.id ? 'bg-leaf-2/20 text-leaf' : 'text-fg-2 hover:bg-line')} onClick={() => setCam(r.id)}>{r.id}</button>)}
          <a className="ml-1 rounded p-0.5 text-fg-3 hover:bg-line" href={viewerUrl} target="_blank" rel="noreferrer" title="Open stream in a new tab"><ExternalLink size={11} /></a>
        </div>)}
    </div>
  )
}
