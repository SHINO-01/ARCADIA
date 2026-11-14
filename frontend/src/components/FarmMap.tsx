/**
 * Mission map: geo-selection (Mapbox GL Draw on MapLibre), H3 planning layers (crop beds, travel
 * roads, docks, storage, repair station, waterlogged hazards), live swarm via the Altara canvas,
 * and RTS-style unit control:
 *   click unit = select · ctrl/shift+click = add to selection · right-click = move selection
 *   shift+right-click = append a route waypoint · Esc = clear
 */
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
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
import { CROP_COLORS, INFRA_COLOR, type Infrastructure } from '../lib/types'

// mapbox-gl-draw targets Mapbox GL class names; MapLibre renamed them. Without this the draw
// tool's event handlers never bind to the canvas (polygon tool "does nothing").
const DrawCtor = MapboxDraw as unknown as { constants: { classes: Record<string, string> } }
DrawCtor.constants.classes.CANVAS = 'maplibregl-canvas'
DrawCtor.constants.classes.CONTROL_BASE = 'maplibregl-ctrl'
DrawCtor.constants.classes.CONTROL_PREFIX = 'maplibregl-ctrl-'
DrawCtor.constants.classes.CONTROL_GROUP = 'maplibregl-ctrl-group'
DrawCtor.constants.classes.ATTRIBUTION = 'maplibregl-ctrl-attrib'

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined
const SATELLITE_STYLE: StyleSpecification = {
  version: 8,
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  sources: { esri: { type: 'raster', tileSize: 256, maxzoom: 19,
    tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics' } },
  layers: [{ id: 'esri', type: 'raster', source: 'esri' }],
}
const STATE_COLORS: Record<string, string> = { fallow: '#6b5a3e', seeded: '#8a7a45', growing: '#3fb95a', ripe: '#f5c451', harvested: '#4b5f52', infra: '#2d4436' }
const KIND_COLORS: Record<string, string> = { road: INFRA_COLOR.road, dock: '#2d4436', storage: '#3a2f52', repair: '#5a3a24' }

export interface FarmMapHandle { startDraw: () => void; clearDraw: () => void; fitFarm: () => void }
export interface FarmMapProps { visible: boolean; onDrawn: (feature: GeoJSON.Feature<GeoJSON.Polygon>, areaM2: number) => void; onFps?: (fps: number) => void }

export const FarmMap = forwardRef<FarmMapHandle, FarmMapProps>(function FarmMap({ visible, onDrawn, onFps }, ref) {
  const el = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MLMap | null>(null)
  const drawRef = useRef<MapboxDraw | null>(null)
  const altaraRef = useRef<AltaraRenderer | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const [ready, setReady] = useState(false)
  const { farm, mode, layer, dispatchArm, selected, selectedRobot, route, hoverHex, armDispatch, setHoverHex, notify } = useUI()
  const cellVersion = useLive((s) => s.cellVersion)
  const chaosFlash = useLive((s) => s.chaosFlash)
  const fleet = useLive((s) => s.fleet)

  useImperativeHandle(ref, () => ({
    startDraw: () => { const d = drawRef.current; if (!d) return; d.deleteAll(); d.changeMode('draw_polygon') },
    clearDraw: () => drawRef.current?.deleteAll(),
    fitFarm: () => { const m = mapRef.current; if (m && farm) { const b = turf.bbox(farm.boundary as GeoJSON.Feature); m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, duration: 700, maxZoom: 17.5 }) } },
  }), [farm])

  // ---------------------------------------------------------------- init
  useEffect(() => {
    if (!el.current || mapRef.current) return
    const style = MAPBOX_TOKEN ? `https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12?access_token=${MAPBOX_TOKEN}` : SATELLITE_STYLE
    const map = new maplibregl.Map({ container: el.current, style, center: [-119.7982, 36.7012], zoom: 15.6, attributionControl: false, maxPitch: 60, fadeDuration: 0 })
    if (MAPBOX_TOKEN) map.setTransformRequest(((url: string) => (url.startsWith('https://api.mapbox.com') ? { url: url.includes('access_token') ? url : `${url}${url.includes('?') ? '&' : '?'}access_token=${MAPBOX_TOKEN}` } : { url })) as RequestTransformFunction)
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
    const draw = new MapboxDraw({ displayControlsDefault: false, controls: {}, defaultMode: 'simple_select', styles: drawStyles })
    map.addControl(draw as unknown as IControl, 'top-left')
    drawRef.current = draw
    const handleDraw = () => {
      const all = draw.getAll()
      const poly = all.features.find((f: GeoJSON.Feature) => f.geometry.type === 'Polygon') as GeoJSON.Feature<GeoJSON.Polygon> | undefined
      if (!poly) return
      for (const f of all.features) if (f.id !== poly.id) draw.delete(String(f.id))
      onDrawn(poly, turf.area(poly))
    }
    const anyMap = map as unknown as { on(type: string, fn: () => void): void }
    anyMap.on('draw.create', handleDraw)
    anyMap.on('draw.update', handleDraw)

    map.on('load', () => {
      map.addSource('hex', { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, promoteId: 'h3' })
      map.addLayer({ id: 'hex-fill', type: 'fill', source: 'hex', paint: { 'fill-color': ['coalesce', ['feature-state', 'color'], '#6b5a3e'], 'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.9, ['==', ['get', 'kind'], 'road'], 0.85, 0.55] } })
      map.addLayer({ id: 'hex-line', type: 'line', source: 'hex', paint: { 'line-color': '#0b1410', 'line-width': 0.5, 'line-opacity': 0.5 } })
      map.addLayer({ id: 'hex-water', type: 'fill', source: 'hex', filter: ['==', ['get', 'waterlogged'], true], paint: { 'fill-pattern': 'water', 'fill-opacity': 0.9 } })
      map.addLayer({ id: 'hex-weeds', type: 'fill', source: 'hex', filter: ['==', ['get', 'weeds'], true], paint: { 'fill-pattern': 'weeds', 'fill-opacity': 0.9 } })
      map.addLayer({ id: 'hex-target', type: 'line', source: 'hex', filter: ['==', ['get', 'h3'], ''], paint: { 'line-color': '#ffffff', 'line-width': 2.5 } })
      map.addSource('beds', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'bed-outline', type: 'line', source: 'beds', paint: { 'line-color': '#e6f0e9', 'line-width': 1.6, 'line-opacity': 0.75 } })
      map.addLayer({ id: 'bed-label', type: 'symbol', source: 'beds', layout: { 'text-field': ['get', 'label'], 'text-size': 11, 'text-font': ['Open Sans Semibold', 'Noto Sans Regular'], 'text-allow-overlap': false }, paint: { 'text-color': '#ffffff', 'text-halo-color': '#0b1410', 'text-halo-width': 1.4 } })
      map.addSource('infra', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'infra-halo', type: 'circle', source: 'infra', paint: { 'circle-radius': 17, 'circle-color': ['get', 'color'], 'circle-opacity': 0.28, 'circle-stroke-color': ['get', 'color'], 'circle-stroke-width': 2.2 } })
      map.addLayer({ id: 'infra-label', type: 'symbol', source: 'infra', layout: { 'text-field': ['get', 'label'], 'text-size': 11, 'text-offset': [0, 2.2], 'text-font': ['Open Sans Semibold', 'Noto Sans Regular'] }, paint: { 'text-color': '#e6f0e9', 'text-halo-color': '#0b1410', 'text-halo-width': 1.4 } })
      map.addSource('paths', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'paths', type: 'line', source: 'paths', paint: { 'line-color': ['get', 'color'], 'line-width': 1.6, 'line-dasharray': [2, 2], 'line-opacity': 0.85 } })
      map.addSource('route', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'route-line', type: 'line', source: 'route', filter: ['==', ['geometry-type'], 'LineString'], paint: { 'line-color': '#7ce38b', 'line-width': 2.2, 'line-dasharray': [1, 1.5] } })
      map.addLayer({ id: 'route-pt', type: 'circle', source: 'route', filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-radius': 9, 'circle-color': '#0b1410', 'circle-stroke-color': '#7ce38b', 'circle-stroke-width': 2 } })
      map.addLayer({ id: 'route-no', type: 'symbol', source: 'route', filter: ['==', ['geometry-type'], 'Point'], layout: { 'text-field': ['get', 'n'], 'text-size': 10, 'text-font': ['Open Sans Semibold', 'Noto Sans Regular'] }, paint: { 'text-color': '#7ce38b' } })
      // patterns: weeds (red hatch) and waterlogged (blue hatch)
      const mk = (color: string, slope: 1 | -1) => { const c = document.createElement('canvas'); c.width = c.height = 8; const g = c.getContext('2d')!; g.strokeStyle = color; g.lineWidth = 1.6; g.beginPath(); if (slope === 1) { g.moveTo(0, 8); g.lineTo(8, 0) } else { g.moveTo(0, 0); g.lineTo(8, 8) } g.stroke(); return g.getImageData(0, 0, 8, 8) }
      map.addImage('weeds', mk('rgba(255,77,94,.95)', 1), { pixelRatio: 1 })
      map.addImage('water', mk('rgba(92,200,255,.85)', -1), { pixelRatio: 1 })
      altaraRef.current = new AltaraRenderer(map, el.current!, { getSelected: () => useUI.getState().selectedRobot, getHover: () => null, onStats: onFps,
        getSelection: () => useUI.getState().selected })
      setReady(true)
    })

    let hovered: string | null = null
    map.on('mousemove', 'hex-fill', (e: MapMouseEvent & { features?: MapGeoJSONFeature[] }) => {
      const id = e.features?.[0]?.properties?.h3 as string | undefined
      if (hovered && hovered !== id) map.setFeatureState({ source: 'hex', id: hovered }, { hover: false })
      if (id) map.setFeatureState({ source: 'hex', id }, { hover: true })
      hovered = id ?? null
      setHoverHex(hovered)
    })
    map.on('mouseleave', 'hex-fill', () => { if (hovered) map.setFeatureState({ source: 'hex', id: hovered }, { hover: false }); hovered = null; setHoverHex(null) })

    // ---- left click: select units / dispatch when armed / inspect hex
    map.on('click', async (e: MapMouseEvent) => {
      const ui = useUI.getState()
      if (ui.mode === 'draw' && drawRef.current && drawRef.current.getMode() === 'draw_polygon') return
      const hit = altaraRef.current?.hit(e.point.x, e.point.y)
      const multi = e.originalEvent.ctrlKey || e.originalEvent.metaKey || e.originalEvent.shiftKey
      if (hit && !ui.dispatchArm) { if (multi) ui.toggleSelect(hit); else ui.selectRobot(hit === ui.selectedRobot && ui.selected.length === 1 ? null : hit); return }
      const feats = map.queryRenderedFeatures(e.point, { layers: ['hex-fill'] })
      const h3 = feats[0]?.properties?.h3 as string | undefined
      if (ui.dispatchArm) {
        const { robotId, kind } = ui.dispatchArm
        try { const m = await api.dispatch(robotId, h3 ? { kind, target_h3: h3 } : { kind, lat: e.lngLat.lat, lng: e.lngLat.lng }); notify(`${robotId} → ${m.kind}${h3 ? ` @ ${h3.slice(-5)}` : ''}`, 'ok') }
        catch (err) { notify((err as Error).message, 'err') }
        armDispatch(null); return
      }
      if (!multi && ui.selected.length && !h3) ui.select([])
      if (h3 && feats[0]) {
        const p = { ...feats[0].properties, ...(cellOverrides.get(h3) ?? {}) } as Record<string, unknown>
        popupRef.current?.remove()
        popupRef.current = new maplibregl.Popup({ closeButton: false, maxWidth: '260px' }).setLngLat(e.lngLat).setHTML(
          `<div class="mono" style="font-size:11px;line-height:1.5">
            <div style="color:#7ce38b;font-weight:600">${h3}${p.bed_id ? ` · ${p.bed_id}` : ''}</div>
            <div>${p.kind === 'crop' ? `${p.crop_type} · <b>${p.state}</b>` : `<b>${{ road: 'travel road', dock: 'charging dock', storage: 'robot storage', repair: 'repair station' }[String(p.kind)] ?? p.kind}</b>`}</div>
            ${p.kind === 'crop' ? `<div>moisture ${(Number(p.soil_moisture) * 100).toFixed(0)}% · health ${(Number(p.health) * 100).toFixed(0)}%${p.weeds ? ' · <span style="color:#ff4d5e">weeds</span>' : ''}${p.waterlogged ? ' · <span style="color:#5cc8ff">waterlogged</span>' : ''}</div>` : ''}
            <div style="color:#6b8273">ENU ${Number(p.x).toFixed(1)}, ${Number(p.y).toFixed(1)} m</div></div>`).addTo(map)
      }
    })

    // ---- right click: RTS move / route waypoint
    map.on('contextmenu', async (e: MapMouseEvent) => {
      e.preventDefault()
      const ui = useUI.getState()
      if (!ui.selected.length) return
      if (e.originalEvent.shiftKey) { ui.addRoutePoint({ lat: e.lngLat.lat, lng: e.lngLat.lng }); return }
      const feats = map.queryRenderedFeatures(e.point, { layers: ['hex-fill'] })
      const h3 = feats[0]?.properties?.h3 as string | undefined
      for (const rid of ui.selected) {
        try { await api.dispatch(rid, h3 ? { kind: 'goto', target_h3: h3 } : { kind: 'goto', lat: e.lngLat.lat, lng: e.lngLat.lng }) }
        catch (err) { notify(`${rid}: ${(err as Error).message}`, 'err') }
      }
      notify(`Move order → ${ui.selected.join(', ')}`, 'ok')
      altaraRef.current?.flash(e.lngLat.lat, e.lngLat.lng, '#7ce38b')
    })

    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as { __map?: MLMap }).__map = map
    return () => { altaraRef.current?.destroy(); map.remove(); mapRef.current = null }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // resize when the view that hosts the map becomes visible again
  useEffect(() => { if (visible && mapRef.current) setTimeout(() => mapRef.current?.resize(), 30) }, [visible])
  useEffect(() => { const d = drawRef.current; if (d && ready && mode !== 'draw') { try { d.changeMode('simple_select') } catch { /* noop */ } } }, [mode, ready])

  // ---------------------------------------------------------------- farm data
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const clear = (id: string) => (map.getSource(id) as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: [] })
    if (!farm) { clear('hex'); clear('infra'); clear('beds'); return }
    let cancelled = false
    ;(async () => {
      const [grid, infra] = await Promise.all([api.hexgrid(farm.id), farm.plan ? api.infrastructure(farm.id) : Promise.resolve([] as Infrastructure[])])
      if (cancelled) return
      cellOverrides.clear()
      ;(map.getSource('hex') as GeoJSONSource).setData(grid)
      ;(map.getSource('infra') as GeoJSONSource).setData({ type: 'FeatureCollection', features: infra.map((i) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [i.lng, i.lat] },
        properties: { kind: i.kind, slug: i.slug, color: INFRA_COLOR[i.kind] ?? '#fff', label: `${{ solar_dock: '☀', wind_dock: '≋', storage: '⌂', repair_station: '⚒' }[i.kind] ?? '•'} ${i.slug}` } })) })
      ;(map.getSource('beds') as GeoJSONSource).setData(bedFeatures(grid, farm.plan?.beds ?? []))
      paintCells(map, grid, layer)
      drawRef.current?.deleteAll()
      const bbox = turf.bbox(farm.boundary as GeoJSON.Feature)
      map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 60, duration: 900, maxZoom: 17.5 })
      ;(map as unknown as { __grid: GeoJSON.FeatureCollection }).__grid = grid
    })()
    return () => { cancelled = true }
  }, [farm?.id, farm?.plan, ready]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { const map = mapRef.current as (MLMap & { __grid?: GeoJSON.FeatureCollection }) | null; if (map?.__grid && ready) paintCells(map, map.__grid, layer) }, [layer, cellVersion, ready])

  // mission path + target for the primary selection
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const feats: GeoJSON.Feature[] = []
    const missions = useLive.getState().missions
    for (const rid of selected) {
      const robot = fleet[rid]
      const m = missions.find((x) => x.id === (robot?.current_mission_id ?? robot?.mission_id))
      if (m?.waypoints?.length && robot?.lng != null && robot.lat != null) {
        const coords = [[robot.lng, robot.lat], ...m.waypoints.filter((w) => w.lat != null && w.lng != null).map((w) => [w.lng!, w.lat!])]
        if (coords.length > 1) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: { color: rid === selectedRobot ? '#ffffff' : '#a3b8aa' } })
      }
    }
    ;(map.getSource('paths') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: feats })
    const prim = selectedRobot ? fleet[selectedRobot] : null
    const pm = missions.find((x) => x.id === (prim?.current_mission_id ?? prim?.mission_id))
    map.setFilter('hex-target', ['==', ['get', 'h3'], pm?.target_h3 ?? ''])
  }, [selected, selectedRobot, fleet, ready])

  // route builder overlay
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const feats: GeoJSON.Feature[] = route.map((p, i) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lng, p.lat] }, properties: { n: String(i + 1) } }))
    if (route.length > 1) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: [...route.map((p) => [p.lng, p.lat]), ...(useUI.getState().routeLoop ? [[route[0].lng, route[0].lat]] : [])] }, properties: {} })
    ;(map.getSource('route') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: feats })
  }, [route, ready])

  useEffect(() => { if (!chaosFlash) return; const r = fleet[chaosFlash.robot_id]; if (r?.lat != null && r.lng != null) altaraRef.current?.flash(r.lat, r.lng) }, [chaosFlash]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (mapRef.current) mapRef.current.getCanvas().style.cursor = dispatchArm ? 'crosshair' : selected.length ? 'default' : '' }, [dispatchArm, selected])

  return (
    <div className="relative h-full w-full">
      <div ref={el} className="h-full w-full" />
      {dispatchArm && <div className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-leaf-2 bg-bg/90 px-3 py-1.5 text-xs text-leaf anim-slide-up">Click a target for <b>{dispatchArm.robotId}</b> · {dispatchArm.kind} <span className="kbd ml-2">Esc</span></div>}
      {mode === 'draw' && !farm && <div className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-line-2 bg-bg/90 px-3 py-1.5 text-xs text-fg-2 anim-slide-up">Click to place boundary vertices · double-click or click the first point to close</div>}
      {hoverHex && !dispatchArm && <div className="pointer-events-none absolute bottom-8 left-3 mono rounded bg-bg/80 px-2 py-1 text-[10px] text-fg-2">{hoverHex}</div>}
    </div>
  )
})

function bedFeatures(grid: GeoJSON.FeatureCollection, beds: { id: string; crop: string }[]): GeoJSON.FeatureCollection {
  const byBed = new Map<string, GeoJSON.Feature<GeoJSON.Polygon>[]>()
  for (const f of grid.features) { const b = f.properties?.bed_id as string | undefined; if (b) (byBed.get(b) ?? byBed.set(b, []).get(b)!).push(f as GeoJSON.Feature<GeoJSON.Polygon>) }
  const out: GeoJSON.Feature[] = []
  for (const [id, cells] of byBed) {
    const crop = beds.find((b) => b.id === id)?.crop ?? cells[0].properties?.crop_type
    try {
      const u = cells.length === 1 ? cells[0] : turf.union(turf.featureCollection(cells))
      if (u) { out.push({ type: 'Feature', geometry: u.geometry, properties: { id } }); const c = turf.centerOfMass(u); out.push({ type: 'Feature', geometry: c.geometry, properties: { id, label: `${id.replace('bed-', 'Bed ')} · ${crop}` } }) }
    } catch { /* degenerate union */ }
  }
  return { type: 'FeatureCollection', features: out }
}

function paintCells(map: MLMap, grid: GeoJSON.FeatureCollection, layer: string) {
  for (const f of grid.features) {
    const p = { ...f.properties, ...(cellOverrides.get(String(f.properties?.h3)) ?? {}) } as Record<string, unknown>
    let color: string
    if (p.kind !== 'crop') color = KIND_COLORS[String(p.kind)] ?? '#2d4436'
    else if (layer === 'plan' || layer === 'crop') color = CROP_COLORS[String(p.crop_type)] ?? '#888'
    else if (layer === 'moisture') color = ramp(Number(p.soil_moisture ?? 0.5), ['#8a6a3d', '#c9a96a', '#5cc8ff', '#1d5fd1'])
    else if (layer === 'health') color = ramp(Number(p.health ?? 1), ['#ff4d5e', '#f5c451', '#7ce38b'])
    else color = STATE_COLORS[String(p.state)] ?? '#6b5a3e'
    map.setFeatureState({ source: 'hex', id: String(p.h3) }, { color })
  }
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
function ramp(v: number, stops: string[]) { const t = Math.max(0, Math.min(1, v)) * (stops.length - 1); const i = Math.min(stops.length - 2, Math.floor(t)); const k = t - i; const a = hex(stops[i]), b = hex(stops[i + 1]); return `rgb(${Math.round(a[0] + (b[0] - a[0]) * k)},${Math.round(a[1] + (b[1] - a[1]) * k)},${Math.round(a[2] + (b[2] - a[2]) * k)})` }
function hex(h: string) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)] }
const drawStyles = [
  { id: 'gl-draw-polygon-fill', type: 'fill', filter: ['all', ['==', '$type', 'Polygon']], paint: { 'fill-color': '#7ce38b', 'fill-opacity': 0.12 } },
  { id: 'gl-draw-polygon-stroke', type: 'line', filter: ['all', ['==', '$type', 'Polygon']], paint: { 'line-color': '#7ce38b', 'line-width': 2.5 } },
  { id: 'gl-draw-line', type: 'line', filter: ['all', ['==', '$type', 'LineString']], paint: { 'line-color': '#7ce38b', 'line-dasharray': [0.4, 2], 'line-width': 2 } },
  { id: 'gl-draw-vertex-halo', type: 'circle', filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point']], paint: { 'circle-radius': 7, 'circle-color': '#0b1410' } },
  { id: 'gl-draw-vertex', type: 'circle', filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point']], paint: { 'circle-radius': 4.5, 'circle-color': '#7ce38b' } },
  { id: 'gl-draw-midpoint', type: 'circle', filter: ['all', ['==', 'meta', 'midpoint']], paint: { 'circle-radius': 3, 'circle-color': '#f5c451' } },
]
