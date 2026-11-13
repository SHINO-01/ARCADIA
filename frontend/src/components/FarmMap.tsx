/**
 * Interactive geo-selection + live telemetry map.
 *  - MapLibre GL with satellite imagery (Mapbox if a token is configured, else Esri World Imagery)
 *  - Mapbox GL Draw for drawing the farm boundary polygon
 *  - H3 hex-grid layers coloured by crop state / moisture / health / crop type
 *  - Infrastructure markers (solar/wind docks, storage)
 *  - Altara canvas overlay for 60fps swarm rendering + click-to-dispatch
 */
import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Map as MLMap, MapMouseEvent, MapGeoJSONFeature, GeoJSONSource, StyleSpecification, IControl, RequestTransformFunction } from 'maplibre-gl'
import MapboxDraw from '@mapbox/mapbox-gl-draw'
import 'maplibre-gl/dist/maplibre-gl.css'
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css'
import * as turf from '@turf/turf'
import { AltaraRenderer } from '../altara/renderer'
import { api } from '../lib/api'
import { useLive, cellOverrides } from '../store/live'
import { useUI } from '../store/ui'
import type { Infrastructure } from '../lib/types'

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined

const SATELLITE_STYLE: StyleSpecification = {
  version: 8,
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  sources: {
    esri: {
      type: 'raster', tileSize: 256, maxzoom: 19,
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
    },
  },
  layers: [{ id: 'esri', type: 'raster', source: 'esri' }],
}

const STATE_COLORS: Record<string, string> = {
  fallow: '#6b5a3e', seeded: '#8a7a45', growing: '#3fb95a', ripe: '#f5c451', harvested: '#4b5f52', infra: '#2d4436',
}
const CROP_COLORS: Record<string, string> = { wheat: '#e2c27a', maize: '#f0a742', soy: '#8fd17a', tomato: '#ff6b5e', lettuce: '#5fd7a0' }

export interface FarmMapProps { onDrawn: (feature: GeoJSON.Feature<GeoJSON.Polygon>, areaM2: number) => void; onFps?: (fps: number) => void }

export function FarmMap({ onDrawn, onFps }: FarmMapProps) {
  const el = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MLMap | null>(null)
  const drawRef = useRef<MapboxDraw | null>(null)
  const altaraRef = useRef<AltaraRenderer | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const [ready, setReady] = useState(false)
  const { farm, mode, layer, dispatchArm, selectedRobot, hoverHex, selectRobot, armDispatch, setHoverHex, notify } = useUI()
  const cellVersion = useLive((s) => s.cellVersion)
  const chaosFlash = useLive((s) => s.chaosFlash)
  const fleet = useLive((s) => s.fleet)

  // ---- init
  useEffect(() => {
    if (!el.current || mapRef.current) return
    const style = MAPBOX_TOKEN
      ? `https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12?access_token=${MAPBOX_TOKEN}`
      : SATELLITE_STYLE
    const map = new maplibregl.Map({ container: el.current, style, center: [-119.7982, 36.7012], zoom: 15.6, attributionControl: false,
      maxPitch: 60, fadeDuration: 0 })
    if (MAPBOX_TOKEN) map.setTransformRequest(((url: string) => (url.startsWith('https://api.mapbox.com') ? { url: url.includes('access_token') ? url : `${url}${url.includes('?') ? '&' : '?'}access_token=${MAPBOX_TOKEN}` } : { url })) as RequestTransformFunction)
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')

    const draw = new MapboxDraw({ displayControlsDefault: false, controls: { polygon: true, trash: true }, defaultMode: 'simple_select',
      styles: drawStyles })
    // MapboxDraw expects mapbox-gl classnames; MapLibre is API compatible.
    map.addControl(draw as unknown as IControl, 'top-left')
    drawRef.current = draw

    const handleDraw = () => {
      const all = draw.getAll()
      const poly = all.features.find((f: GeoJSON.Feature) => f.geometry.type === 'Polygon') as GeoJSON.Feature<GeoJSON.Polygon> | undefined
      if (!poly) return
      // keep only the latest polygon
      for (const f of all.features) if (f.id !== poly.id) draw.delete(String(f.id))
      onDrawn(poly, turf.area(poly))
    }
    const anyMap = map as unknown as { on(type: string, fn: () => void): void }
    anyMap.on('draw.create', handleDraw)
    anyMap.on('draw.update', handleDraw)

    map.on('load', () => {
      map.addSource('hex', { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, promoteId: 'h3' })
      map.addLayer({ id: 'hex-fill', type: 'fill', source: 'hex', paint: { 'fill-color': ['coalesce', ['feature-state', 'color'], '#6b5a3e'], 'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.85, 0.55] } })
      map.addLayer({ id: 'hex-line', type: 'line', source: 'hex', paint: { 'line-color': '#0b1410', 'line-width': 0.6, 'line-opacity': 0.6 } })
      map.addLayer({ id: 'hex-weeds', type: 'fill', source: 'hex', filter: ['==', ['get', 'weeds'], true],
        paint: { 'fill-pattern': 'weeds', 'fill-opacity': 0.9 } })
      map.addLayer({ id: 'hex-target', type: 'line', source: 'hex', filter: ['==', ['get', 'h3'], ''], paint: { 'line-color': '#ffffff', 'line-width': 2.5 } })
      map.addSource('infra', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'infra-halo', type: 'circle', source: 'infra', paint: { 'circle-radius': 16, 'circle-color': ['match', ['get', 'kind'], 'solar_dock', '#f5c451', 'wind_dock', '#5cc8ff', '#c7a4ff'], 'circle-opacity': 0.25, 'circle-stroke-color': ['match', ['get', 'kind'], 'solar_dock', '#f5c451', 'wind_dock', '#5cc8ff', '#c7a4ff'], 'circle-stroke-width': 2 } })
      map.addLayer({ id: 'infra-label', type: 'symbol', source: 'infra', layout: { 'text-field': ['get', 'label'], 'text-size': 11, 'text-offset': [0, 2.1], 'text-font': ['Open Sans Semibold', 'Noto Sans Regular'] }, paint: { 'text-color': '#e6f0e9', 'text-halo-color': '#0b1410', 'text-halo-width': 1.4 } })
      map.addSource('paths', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'paths', type: 'line', source: 'paths', paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-dasharray': [2, 2], 'line-opacity': 0.8 } })
      // weed hatch pattern
      const c = document.createElement('canvas'); c.width = c.height = 8
      const g = c.getContext('2d')!; g.strokeStyle = 'rgba(255,77,94,.9)'; g.lineWidth = 1.5; g.beginPath(); g.moveTo(0, 8); g.lineTo(8, 0); g.stroke()
      map.addImage('weeds', g.getImageData(0, 0, 8, 8), { pixelRatio: 1 })

      altaraRef.current = new AltaraRenderer(map, el.current!, {
        getSelected: () => useUI.getState().selectedRobot, getHover: () => null, onStats: onFps })
      setReady(true)
    })

    let hovered: string | null = null
    map.on('mousemove', 'hex-fill', (e: MapMouseEvent & { features?: MapGeoJSONFeature[] }) => {
      const id = e.features?.[0]?.properties?.h3 as string | undefined
      if (hovered && hovered !== id) map.setFeatureState({ source: 'hex', id: hovered }, { hover: false })
      if (id) map.setFeatureState({ source: 'hex', id }, { hover: true })
      hovered = id ?? null
      setHoverHex(hovered)
      map.getCanvas().style.cursor = useUI.getState().dispatchArm ? 'crosshair' : 'pointer'
    })
    map.on('mouseleave', 'hex-fill', () => { if (hovered) map.setFeatureState({ source: 'hex', id: hovered }, { hover: false }); hovered = null; setHoverHex(null); map.getCanvas().style.cursor = '' })

    map.on('click', async (e: MapMouseEvent) => {
      const ui = useUI.getState()
      const hit = altaraRef.current?.hit(e.point.x, e.point.y)
      if (hit && !ui.dispatchArm) { selectRobot(hit === ui.selectedRobot ? null : hit); return }
      const feats = map.queryRenderedFeatures(e.point, { layers: ['hex-fill'] })
      const h3 = feats[0]?.properties?.h3 as string | undefined
      if (ui.dispatchArm) {
        const { robotId, kind } = ui.dispatchArm
        try {
          const m = await api.dispatch(robotId, h3 ? { kind, target_h3: h3 } : { kind, lat: e.lngLat.lat, lng: e.lngLat.lng })
          notify(`Dispatched ${robotId}: ${m.kind} → ${h3 ?? `${e.lngLat.lat.toFixed(5)}, ${e.lngLat.lng.toFixed(5)}`}`, 'ok')
        } catch (err) { notify((err as Error).message, 'err') }
        armDispatch(null)
        map.getCanvas().style.cursor = ''
        return
      }
      if (h3 && feats[0]) {
        const p = { ...feats[0].properties, ...(cellOverrides.get(h3) ?? {}) } as Record<string, unknown>
        popupRef.current?.remove()
        popupRef.current = new maplibregl.Popup({ closeButton: false, maxWidth: '260px' }).setLngLat(e.lngLat).setHTML(
          `<div class="mono" style="font-size:11px;line-height:1.5">
            <div style="color:#7ce38b;font-weight:600">${h3}</div>
            <div>${p.kind === 'crop' ? `${p.crop_type} · <b>${p.state}</b>` : `<b>${p.kind}</b>`}</div>
            ${p.kind === 'crop' ? `<div>moisture ${(Number(p.soil_moisture) * 100).toFixed(0)}% · health ${(Number(p.health) * 100).toFixed(0)}%${p.weeds ? ' · <span style="color:#ff4d5e">weeds</span>' : ''}</div>` : ''}
            <div style="color:#6b8273">ENU ${Number(p.x).toFixed(1)}, ${Number(p.y).toFixed(1)} m</div>
          </div>`).addTo(map)
      }
    })

    mapRef.current = map
    return () => { altaraRef.current?.destroy(); map.remove(); mapRef.current = null }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- draw mode toggles
  useEffect(() => {
    const draw = drawRef.current
    if (!draw || !ready) return
    const ctrl = el.current?.querySelector('.mapbox-gl-draw_ctrl-draw-btn')?.parentElement as HTMLElement | undefined
    if (ctrl) ctrl.style.display = mode === 'draw' ? '' : 'none'
    if (mode !== 'draw') { try { draw.changeMode('simple_select') } catch { /* noop */ } }
  }, [mode, ready])

  // ---- farm hex grid + infrastructure
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    if (!farm) {
      (map.getSource('hex') as GeoJSONSource)?.setData({ type: 'FeatureCollection', features: [] });
      (map.getSource('infra') as GeoJSONSource)?.setData({ type: 'FeatureCollection', features: [] })
      return
    }
    let cancelled = false
    ;(async () => {
      const [grid, infra] = await Promise.all([api.hexgrid(farm.id), farm.plan ? api.infrastructure(farm.id) : Promise.resolve([] as Infrastructure[])])
      if (cancelled) return
      cellOverrides.clear()
      ;(map.getSource('hex') as GeoJSONSource).setData(grid)
      ;(map.getSource('infra') as GeoJSONSource).setData({ type: 'FeatureCollection', features: infra.map((i) => ({
        type: 'Feature', geometry: { type: 'Point', coordinates: [i.lng, i.lat] },
        properties: { kind: i.kind, slug: i.slug, label: `${i.kind === 'solar_dock' ? '☀' : i.kind === 'wind_dock' ? '≋' : '⌂'} ${i.slug}` } })) })
      paintCells(map, grid, layer)
      drawRef.current?.deleteAll()
      const bbox = turf.bbox(farm.boundary as GeoJSON.Feature)
      map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 60, duration: 900, maxZoom: 17.5 })
      ;(map as unknown as { __grid: GeoJSON.FeatureCollection }).__grid = grid
    })()
    return () => { cancelled = true }
  }, [farm?.id, farm?.plan, ready]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- repaint on layer change / live cell updates
  useEffect(() => {
    const map = mapRef.current as (MLMap & { __grid?: GeoJSON.FeatureCollection }) | null
    if (!map || !ready || !map.__grid) return
    paintCells(map, map.__grid, layer)
  }, [layer, cellVersion, ready])

  // ---- mission path preview + target highlight for selected robot
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const robot = selectedRobot ? fleet[selectedRobot] : null
    const mid = robot?.current_mission_id ?? robot?.mission_id
    const missions = useLive.getState().missions
    const m = missions.find((x) => x.id === mid)
    const feats: GeoJSON.Feature[] = []
    if (m?.waypoints?.length && robot) {
      const coords = m.waypoints.filter((w) => w.lat != null && w.lng != null).map((w) => [w.lng!, w.lat!])
      if (robot.lng != null && robot.lat != null) coords.unshift([robot.lng, robot.lat])
      if (coords.length > 1) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: { color: '#ffffff' } })
    }
    ;(map.getSource('paths') as GeoJSONSource)?.setData({ type: 'FeatureCollection', features: feats })
    map.setFilter('hex-target', ['==', ['get', 'h3'], m?.target_h3 ?? ''])
  }, [selectedRobot, fleet, ready])

  useEffect(() => {
    if (!chaosFlash) return
    const r = fleet[chaosFlash.robot_id]
    if (r?.lat != null && r.lng != null) altaraRef.current?.flash(r.lat, r.lng)
  }, [chaosFlash]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (mapRef.current) mapRef.current.getCanvas().style.cursor = dispatchArm ? 'crosshair' : '' }, [dispatchArm])

  return (
    <div className="relative h-full w-full">
      <div ref={el} className="h-full w-full" />
      {dispatchArm && (
        <div className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-leaf-2 bg-bg/90 px-3 py-1.5 text-xs text-leaf anim-slide-up">
          Click a hex to dispatch <b>{dispatchArm.robotId}</b> · {dispatchArm.kind} <span className="kbd ml-2">Esc</span> cancel
        </div>
      )}
      {hoverHex && !dispatchArm && <div className="pointer-events-none absolute bottom-8 left-3 mono rounded bg-bg/80 px-2 py-1 text-[10px] text-fg-2">{hoverHex}</div>}
    </div>
  )
}

function paintCells(map: MLMap, grid: GeoJSON.FeatureCollection, layer: 'state' | 'moisture' | 'health' | 'crop') {
  for (const f of grid.features) {
    const p = { ...f.properties, ...(cellOverrides.get(String(f.properties?.h3)) ?? {}) } as Record<string, unknown>
    let color = STATE_COLORS[String(p.state)] ?? '#6b5a3e'
    if (p.kind !== 'crop') color = p.kind === 'dock' ? '#2d4436' : '#3a2f52'
    else if (layer === 'moisture') color = ramp(Number(p.soil_moisture ?? 0.5), ['#8a6a3d', '#c9a96a', '#5cc8ff', '#1d5fd1'])
    else if (layer === 'health') color = ramp(Number(p.health ?? 1), ['#ff4d5e', '#f5c451', '#7ce38b'])
    else if (layer === 'crop') color = CROP_COLORS[String(p.crop_type)] ?? '#888'
    map.setFeatureState({ source: 'hex', id: String(p.h3) }, { color })
  }
  // weeds layer reads properties, so patch the source data for weed flags that changed live
  if (cellOverrides.size) {
    let dirty = false
    for (const f of grid.features) {
      const o = cellOverrides.get(String(f.properties?.h3))
      if (o && 'weeds' in o && f.properties!.weeds !== o.weeds) { f.properties!.weeds = o.weeds; dirty = true }
      if (o && 'state' in o && f.properties!.state !== o.state) { f.properties!.state = o.state; dirty = true }
    }
    if (dirty) (map.getSource('hex') as GeoJSONSource).setData(grid)
  }
}

function ramp(v: number, stops: string[]) {
  const t = Math.max(0, Math.min(1, v)) * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(t))
  const k = t - i
  const a = hex(stops[i]), b = hex(stops[i + 1])
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * k)},${Math.round(a[1] + (b[1] - a[1]) * k)},${Math.round(a[2] + (b[2] - a[2]) * k)})`
}
function hex(h: string) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)] }

const drawStyles = [
  { id: 'gl-draw-polygon-fill', type: 'fill', filter: ['all', ['==', '$type', 'Polygon']], paint: { 'fill-color': '#7ce38b', 'fill-opacity': 0.12 } },
  { id: 'gl-draw-polygon-stroke', type: 'line', filter: ['all', ['==', '$type', 'Polygon']], paint: { 'line-color': '#7ce38b', 'line-width': 2.5 } },
  { id: 'gl-draw-line', type: 'line', filter: ['all', ['==', '$type', 'LineString']], paint: { 'line-color': '#7ce38b', 'line-dasharray': [0.4, 2], 'line-width': 2 } },
  { id: 'gl-draw-vertex-halo', type: 'circle', filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point']], paint: { 'circle-radius': 7, 'circle-color': '#0b1410' } },
  { id: 'gl-draw-vertex', type: 'circle', filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point']], paint: { 'circle-radius': 4.5, 'circle-color': '#7ce38b' } },
  { id: 'gl-draw-midpoint', type: 'circle', filter: ['all', ['==', 'meta', 'midpoint']], paint: { 'circle-radius': 3, 'circle-color': '#f5c451' } },
]
