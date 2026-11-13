/**
 * Embedded Webots 3D physics stream. Webots runs natively with `--stream` (W3D over WebSocket);
 * the official WebotsView web component connects to it. Falls back to a status card when the
 * native stack is not running (the headless twin keeps the 2D map alive meanwhile).
 */
import { useEffect, useState } from 'react'
import { Box, RefreshCw, WifiOff } from 'lucide-react'
import { useLive } from '../store/live'

const STREAM_URL = (import.meta.env.VITE_WEBOTS_STREAM_URL as string | undefined) || 'ws://localhost:1234'

export function WebotsView() {
  const bridge = useLive((s) => s.bridge)
  const [nonce, setNonce] = useState(0)
  const [reachable, setReachable] = useState<boolean | null>(null)
  const rosLive = !!bridge?.ros_connected

  useEffect(() => {
    // Probe the streaming server so we only mount the heavy viewer when it is actually up.
    let alive = true
    const probe = () => {
      try {
        const ws = new WebSocket(STREAM_URL)
        const t = setTimeout(() => { ws.close(); if (alive) setReachable(false) }, 2500)
        ws.onopen = () => { clearTimeout(t); ws.close(); if (alive) setReachable(true) }
        ws.onerror = () => { clearTimeout(t); if (alive) setReachable(false) }
      } catch { if (alive) setReachable(false) }
    }
    probe()
    const id = setInterval(probe, reachable ? 30000 : 20000)
    return () => { alive = false; clearInterval(id) }
  }, [nonce, reachable])

  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg border border-line bg-black">
      {reachable ? (
        <iframe key={nonce} title="Webots 3D stream" src={`/webots-viewer.html?url=${encodeURIComponent(STREAM_URL)}`} className="h-full w-full border-0" allow="fullscreen" />
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full border border-line-2 text-fg-3">{reachable === null ? <RefreshCw size={18} className="animate-spin" /> : <WifiOff size={18} />}</span>
          <div className="text-sm font-semibold text-fg-2">Webots stream offline</div>
          <p className="max-w-sm text-xs text-fg-3">
            {rosLive ? 'ROS 2 is connected but the Webots streaming server is not reachable at ' : 'Native robotics stack not running. The headless digital twin is driving the map. Start it with '}
            {rosLive ? <span className="mono">{STREAM_URL}</span> : <span className="kbd">make sim</span>}
            {rosLive ? '. Launch Webots with --stream.' : ' and the 3D physics view attaches automatically.'}
          </p>
          <button className="btn mt-2" onClick={() => { setReachable(null); setNonce((n) => n + 1) }}><RefreshCw size={12} />retry</button>
        </div>
      )}
      <div className="pointer-events-none absolute left-2 top-2 flex items-center gap-1.5 rounded bg-bg/80 px-2 py-1 text-[10px] text-fg-2"><Box size={11} className="text-sky" />Webots R2025a · W3D stream</div>
    </div>
  )
}
