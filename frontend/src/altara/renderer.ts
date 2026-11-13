/**
 * Altara — a Canvas 2D overlay renderer for live swarm tracking on top of MapLibre.
 *
 * Why: moving DOM markers for every robot at telemetry rate causes layout thrash. Altara keeps
 * robots out of the DOM entirely; one <canvas> sits over the map, and each animation frame
 * interpolates robot positions (between the last two telemetry samples), projects them through
 * the map's current camera and draws sprites, headings, trails, battery arcs and status rings.
 * The map is re-projected per frame so rendering stays glued to the imagery while panning/zooming.
 */
import type { Map as MLMap } from 'maplibre-gl'
import { liveRegistry, type LiveRobot } from '../store/live'
import { ROBOT_COLOR, STATUS_COLOR } from '../lib/types'

export interface AltaraOptions {
  getSelected: () => string | null
  getHover: () => string | null
  onStats?: (fps: number) => void
}

export class AltaraRenderer {
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private raf = 0
  private frames = 0
  private lastFps = performance.now()
  private dpr = Math.min(2, window.devicePixelRatio || 1)
  private flashes: { lat: number; lng: number; t0: number; color: string }[] = []
  private hitboxes: { id: string; x: number; y: number; r: number }[] = []

  private map: MLMap
  private opts: AltaraOptions

  constructor(map: MLMap, container: HTMLElement, opts: AltaraOptions) {
    this.map = map
    this.opts = opts
    this.canvas = document.createElement('canvas')
    this.canvas.className = 'altara-layer'
    Object.assign(this.canvas.style, { position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '5' })
    container.appendChild(this.canvas)
    this.ctx = this.canvas.getContext('2d', { alpha: true })!
    this.resize()
    this.map.on('resize', this.resize)
    this.loop()
  }

  destroy() {
    cancelAnimationFrame(this.raf)
    this.map.off('resize', this.resize)
    this.canvas.remove()
  }

  flash(lat: number, lng: number, color = '#ff4d5e') { this.flashes.push({ lat, lng, t0: performance.now(), color }) }

  /** Robot under a screen point (for click-to-select) */
  hit(x: number, y: number): string | null {
    for (const h of this.hitboxes) if (Math.hypot(h.x - x, h.y - y) <= h.r) return h.id
    return null
  }

  private resize = () => {
    const { clientWidth: w, clientHeight: h } = this.map.getContainer()
    this.canvas.width = Math.round(w * this.dpr)
    this.canvas.height = Math.round(h * this.dpr)
    this.canvas.style.width = `${w}px`
    this.canvas.style.height = `${h}px`
  }

  private loop = () => {
    this.raf = requestAnimationFrame(this.loop)
    this.draw()
    this.frames++
    const now = performance.now()
    if (now - this.lastFps > 1000) { this.opts.onStats?.(this.frames); this.frames = 0; this.lastFps = now }
  }

  private draw() {
    const ctx = this.ctx
    const { width, height } = this.canvas
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
    ctx.clearRect(0, 0, width, height)
    const now = performance.now()
    const zoom = this.map.getZoom()
    const scale = Math.max(0.6, Math.min(1.6, (zoom - 13) / 4 + 0.8))
    const selected = this.opts.getSelected()
    const hover = this.opts.getHover()
    this.hitboxes = []

    // flashes (chaos injections, events)
    this.flashes = this.flashes.filter((f) => now - f.t0 < 1600)
    for (const f of this.flashes) {
      const p = this.map.project([f.lng, f.lat])
      const k = (now - f.t0) / 1600
      ctx.beginPath(); ctx.arc(p.x, p.y, 10 + 60 * k, 0, Math.PI * 2)
      ctx.strokeStyle = f.color; ctx.globalAlpha = 1 - k; ctx.lineWidth = 2; ctx.stroke(); ctx.globalAlpha = 1
    }

    for (const r of liveRegistry.values()) {
      // Interpolate between the previous and latest telemetry samples
      const k = Math.min(1, Math.max(0, (now - r.tPrev) / Math.max(1, r.tNext - r.tPrev)))
      const lat = r.prevLat + (r.lat - r.prevLat) * k
      const lng = r.prevLng + (r.lng - r.prevLng) * k
      const heading = lerpAngle(r.prevHeading, r.heading, k)
      const p = this.map.project([lng, lat])
      if (p.x < -60 || p.y < -60 || p.x > width / this.dpr + 60 || p.y > height / this.dpr + 60) continue

      this.drawTrail(r)
      const color = ROBOT_COLOR[r.type] ?? '#ffffff'
      const sColor = STATUS_COLOR[r.status] ?? '#a3b8aa'
      const isSel = selected === r.id
      const isHover = hover === r.id
      const base = (r.type === 'scout_uav' ? 11 : 12) * scale

      // shadow for UAV altitude
      if (r.type === 'scout_uav' && r.z > 0.5) {
        ctx.beginPath(); ctx.ellipse(p.x + r.z * 0.8 * scale, p.y + r.z * 1.2 * scale, base * 0.7, base * 0.35, 0, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.fill()
      }
      // status ring (pulsing on fault/stranded)
      const pulse = r.status === 'stranded' || r.status === 'fault' ? 1 + 0.25 * Math.sin(now / 160) : 1
      ctx.beginPath(); ctx.arc(p.x, p.y, (base + 6) * pulse, 0, Math.PI * 2)
      ctx.strokeStyle = sColor; ctx.lineWidth = isSel ? 3 : 1.5; ctx.globalAlpha = isSel || isHover ? 1 : 0.75; ctx.stroke(); ctx.globalAlpha = 1
      // battery arc
      ctx.beginPath(); ctx.arc(p.x, p.y, base + 3, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (r.battery / 100))
      ctx.strokeStyle = r.battery < 20 ? '#ff4d5e' : r.battery < 40 ? '#f5c451' : '#7ce38b'; ctx.lineWidth = 2.5; ctx.stroke()
      // body sprite
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(-heading + Math.PI / 2)
      ctx.fillStyle = color; ctx.strokeStyle = '#0b1410'; ctx.lineWidth = 1.5
      if (r.type === 'scout_uav') drawQuad(ctx, base, now)
      else if (r.type === 'logistics_agv') drawTruck(ctx, base)
      else drawRover(ctx, base)
      ctx.restore()
      // heading tick
      if (r.speed > 0.05) {
        ctx.beginPath(); ctx.moveTo(p.x, p.y)
        ctx.lineTo(p.x + Math.cos(heading) * (base + 12), p.y - Math.sin(heading) * (base + 12))
        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke()
      }
      // label
      if (zoom > 14.5 || isSel || isHover) {
        ctx.font = `${isSel ? 600 : 500} ${11 * Math.min(1.2, scale)}px "IBM Plex Mono", monospace`
        const label = `${r.id}  ${Math.round(r.battery)}%${r.latency > 500 ? '  ⚠lag' : ''}`
        const tw = ctx.measureText(label).width
        ctx.fillStyle = 'rgba(11,20,16,.85)'; roundRect(ctx, p.x - tw / 2 - 5, p.y + base + 9, tw + 10, 16, 4); ctx.fill()
        ctx.fillStyle = isSel ? '#ffffff' : '#e6f0e9'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
        ctx.fillText(label, p.x, p.y + base + 17)
      }
      this.hitboxes.push({ id: r.id, x: p.x, y: p.y, r: base + 10 })
    }
  }

  private drawTrail(r: LiveRobot) {
    if (r.trail.length < 2) return
    const ctx = this.ctx
    const color = ROBOT_COLOR[r.type] ?? '#fff'
    ctx.beginPath()
    const n = r.trail.length
    for (let i = Math.max(0, n - 160); i < n; i++) {
      const p = this.map.project([r.trail[i].lng, r.trail[i].lat])
      if (i === Math.max(0, n - 160)) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y)
    }
    ctx.strokeStyle = color; ctx.globalAlpha = 0.45; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke(); ctx.globalAlpha = 1
  }
}

function lerpAngle(a: number, b: number, k: number) {
  let d = b - a
  while (d > Math.PI) d -= Math.PI * 2
  while (d < -Math.PI) d += Math.PI * 2
  return a + d * k
}
function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath()
}
function drawQuad(ctx: CanvasRenderingContext2D, s: number, now: number) {
  ctx.beginPath(); ctx.moveTo(-s * .55, -s * .55); ctx.lineTo(s * .55, s * .55); ctx.moveTo(s * .55, -s * .55); ctx.lineTo(-s * .55, s * .55)
  ctx.lineWidth = 3; ctx.strokeStyle = ctx.fillStyle as string; ctx.stroke()
  const rot = (now / 40) % (Math.PI * 2)
  for (const [dx, dy] of [[-1, -1], [1, 1], [1, -1], [-1, 1]]) {
    ctx.save(); ctx.translate(dx * s * .55, dy * s * .55); ctx.rotate(rot)
    ctx.beginPath(); ctx.ellipse(0, 0, s * .32, s * .1, 0, 0, Math.PI * 2); ctx.globalAlpha = .7; ctx.fill(); ctx.globalAlpha = 1; ctx.restore()
  }
  ctx.beginPath(); ctx.arc(0, 0, s * .3, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = '#0b1410'; ctx.lineWidth = 1.5; ctx.stroke()
  ctx.beginPath(); ctx.moveTo(0, -s * .75); ctx.lineTo(-s * .18, -s * .35); ctx.lineTo(s * .18, -s * .35); ctx.closePath(); ctx.fillStyle = '#0b1410'; ctx.fill()
}
function drawRover(ctx: CanvasRenderingContext2D, s: number) {
  roundRect(ctx, -s * .5, -s * .65, s, s * 1.3, s * .18); ctx.fill(); ctx.stroke()
  ctx.fillStyle = '#0b1410'
  for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) { roundRect(ctx, dx * s * .55 - s * .12, dy * s * .4 - s * .22, s * .24, s * .44, 2); ctx.fill() }
  ctx.beginPath(); ctx.moveTo(0, -s * .5); ctx.lineTo(-s * .22, -s * .1); ctx.lineTo(s * .22, -s * .1); ctx.closePath(); ctx.fill()
}
function drawTruck(ctx: CanvasRenderingContext2D, s: number) {
  roundRect(ctx, -s * .6, -s * .8, s * 1.2, s * 1.6, s * .12); ctx.fill(); ctx.stroke()
  ctx.fillStyle = '#0b1410'; roundRect(ctx, -s * .45, -s * .7, s * .9, s * .4, 3); ctx.fill()
  roundRect(ctx, -s * .72, -s * .5, s * .14, s * 1.1, 2); ctx.fill(); roundRect(ctx, s * .58, -s * .5, s * .14, s * 1.1, 2); ctx.fill()
}
