/**
 * Mission map: geo-selection (built-in boundary tool), the controlled-traffic field layout (bed
 * sections, tramlines, headland, alleys, yard spurs and pads, waterlogged hazards, weeds), an
 * the live swarm via the Altara canvas, and RTS-style unit control:
 *   click unit = select · ctrl/shift+click = add to selection · right-click = move selection
 *   shift+right-click = append a route waypoint · Esc = clear
 */
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import type { Map as MLMap, MapMouseEvent, MapGeoJSONFeature, GeoJSONSource, StyleSpecification, RequestTransformFunction } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { area as tArea, bbox as tBbox, bearing as tBearing, centroid as tCentroid, point as tPoint, polygon as tPolygon } from '@turf/turf'
import { AltaraRenderer } from '../altara/renderer'
import { api } from '../lib/api'
import { useLive, sectionOverrides } from '../store/live'
import { useUI } from '../store/ui'
import { CROP_COLORS, INFRA_COLOR, TRACK_COLOR, type Infrastructure } from '../lib/types'

// The bundler does not emit MapLibre's worker on its own; hand it an explicit asset URL.
maplibregl.setWorkerUrl(maplibreWorkerUrl)

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined
const SATELLITE_STYLE: StyleSpecification = {
  version: 8,
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  sources: { esri: { type: 'raster', tileSize: 256, maxzoom: 19,
    tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics' } },
  layers: [{ id: 'esri', type: 'raster', source: 'esri' }],
}
import { STAGE_COLORS, worldApi } from '../lib/world'

export interface FarmMapHandle { startDraw: () => void; clearDraw: () => void; fitFarm: () => void; flyTo: (lng: number, lat: number, zoom?: number) => void }
export interface FarmMapProps { visible: boolean; onDrawn: (feature: GeoJSON.Feature<GeoJSON.Polygon>, areaM2: number) => void; onFps?: (fps: number) => void }

export const FarmMap = forwardRef<FarmMapHandle, FarmMapProps>(function FarmMap({ visible, onDrawn, onFps }, ref) {
  const el = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MLMap | null>(null)
  const boundary = useRef<{ active: boolean; pts: [number, number][]; done: boolean }>({ active: false, pts: [], done: false })
  const [drawState, setDrawState] = useState<{ active: boolean; n: number; areaM2: number }>({ active: false, n: 0, areaM2: 0 })
  const altaraRef = useRef<AltaraRenderer | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const [ready, setReady] = useState(false)
  const [imagery, setImagery] = useState(false)  // first full render (satellite tiles can be slow)
  const { farm, mode, layer, overlays, dispatchArm, selected, selectedRobot, route, hoverInfo, armDispatch, setHoverInfo, notify } = useUI()
  const cellVersion = useLive((s) => s.cellVersion)
  const chaosFlash = useLive((s) => s.chaosFlash)
  const fleet = useLive((s) => s.fleet)

  const renderBoundary = () => {
    const map = mapRef.current
    if (!map) return
    const b = boundary.current
    const feats: GeoJSON.Feature[] = b.pts.map((p, i) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: p }, properties: { first: i === 0 } }))
    if (b.pts.length >= 2) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: b.done ? [...b.pts, b.pts[0]] : b.pts }, properties: {} })
    if (b.pts.length >= 3) feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[...b.pts, b.pts[0]]] }, properties: { done: b.done } })
    ;(map.getSource('boundary') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: feats })
    const area = b.pts.length >= 3 ? tArea(tPolygon([[...b.pts, b.pts[0]]])) : 0
    setDrawState({ active: b.active, n: b.pts.length, areaM2: area })
    map.getCanvas().style.cursor = b.active ? 'crosshair' : ''
  }
  const finishBoundary = () => {
    const b = boundary.current
    if (b.pts.length < 3) return
    b.done = true; b.active = false
    renderBoundary()
    const feature: GeoJSON.Feature<GeoJSON.Polygon> = { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[...b.pts, b.pts[0]]] } }
    onDrawn(feature, tArea(feature))
  }
  const resetBoundary = () => { boundary.current = { active: false, pts: [], done: false }; renderBoundary() }

  useImperativeHandle(ref, () => ({
    startDraw: () => { boundary.current = { active: true, pts: [], done: false }; renderBoundary() },
    clearDraw: () => resetBoundary(),
    fitFarm: () => { const m = mapRef.current; if (m && farm) { const b = tBbox(farm.boundary as GeoJSON.Feature); m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, duration: 700, maxZoom: 17.5 }) } },
    flyTo: (lng, lat, zoom = 15.5) => mapRef.current?.flyTo({ center: [lng, lat], zoom, duration: 1600, essential: true }),
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
    // Live data keeps the map re-rendering, so 'idle' is unreliable: poll for the imagery tiles.
    const imageryPoll = setInterval(() => { if (map.isStyleLoaded() && map.areTilesLoaded()) { setImagery(true); clearInterval(imageryPoll) } }, 400)
    setTimeout(() => { setImagery(true); clearInterval(imageryPoll) }, 20000)  // never block the UI on a slow tile server
    map.on('load', () => {
      // physical layout: tracks under beds, beds, hazards and yard pads on top
      map.addSource('layout', { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, promoteId: 'id' })
      map.addLayer({ id: 'track-fill', type: 'fill', source: 'layout', filter: ['==', ['get', 'layer'], 'track'],
        paint: { 'fill-color': ['match', ['get', 'kind'], 'lane', TRACK_COLOR.lane, 'spur', TRACK_COLOR.spur, 'road', '#7d7f86', TRACK_COLOR.headland], 'fill-opacity': 0.92 } })
      map.addLayer({ id: 'section-fill', type: 'fill', source: 'layout', filter: ['==', ['get', 'layer'], 'section'],
        paint: { 'fill-color': ['coalesce', ['feature-state', 'color'], '#6b5a3e'], 'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.95, 0.8] } })
      map.addLayer({ id: 'section-line', type: 'line', source: 'layout', filter: ['==', ['get', 'layer'], 'section'],
        paint: { 'line-color': '#0b1410', 'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.2, 18, 1], 'line-opacity': 0.55 } })
      map.addLayer({ id: 'section-target', type: 'line', source: 'layout', filter: ['==', ['get', 'id'], ''], paint: { 'line-color': '#ffffff', 'line-width': 2.5 } })
      map.addLayer({ id: 'hazard-fill', type: 'fill', source: 'layout', filter: ['==', ['get', 'layer'], 'hazard'], paint: { 'fill-pattern': 'water', 'fill-opacity': 0.95 } })
      map.addLayer({ id: 'hazard-line', type: 'line', source: 'layout', filter: ['==', ['get', 'layer'], 'hazard'], paint: { 'line-color': '#5cc8ff', 'line-width': 1.5 } })
      map.addLayer({ id: 'pad-fill', type: 'fill', source: 'layout', filter: ['==', ['get', 'layer'], 'pad'],
        paint: { 'fill-color': ['match', ['get', 'kind'], 'solar_dock', INFRA_COLOR.solar_dock, 'wind_dock', INFRA_COLOR.wind_dock, 'storage', INFRA_COLOR.storage, INFRA_COLOR.repair_station], 'fill-opacity': 0.55 } })
      map.addSource('weeds', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'weeds', type: 'circle', source: 'weeds', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 15, 2.5, 18, 6], 'circle-color': '#ff4d5e', 'circle-stroke-color': '#0b1410', 'circle-stroke-width': 1 } })
      map.addSource('detect', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'detect-obst', type: 'fill', source: 'detect', filter: ['==', ['geometry-type'], 'Polygon'],
        paint: { 'fill-color': ['match', ['get', 'kind'], 'building', '#e3825a', 'water', '#5fade8', 'trees', '#2f7d4a', '#888'], 'fill-opacity': 0.45, 'fill-outline-color': '#0a0f0d' } })
      map.addLayer({ id: 'detect-road', type: 'line', source: 'detect', filter: ['==', ['geometry-type'], 'LineString'],
        paint: { 'line-color': '#e2b44a', 'line-width': ['interpolate', ['linear'], ['zoom'], 14, 1.5, 18, 4], 'line-dasharray': [2, 1.2], 'line-opacity': 0.9 } })
      map.addSource('beds', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'bed-label', type: 'symbol', source: 'beds', minzoom: 15.5, layout: { 'text-field': ['get', 'label'], 'text-size': 10, 'text-rotate': ['get', 'rot'], 'text-rotation-alignment': 'map', 'text-font': ['Open Sans Semibold'], 'text-allow-overlap': false }, paint: { 'text-color': '#ffffff', 'text-halo-color': '#0b1410', 'text-halo-width': 1.2 } })
      map.addSource('infra', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'infra-halo', type: 'circle', source: 'infra', paint: { 'circle-radius': 17, 'circle-color': ['get', 'color'], 'circle-opacity': 0.28, 'circle-stroke-color': ['get', 'color'], 'circle-stroke-width': 2.2 } })
      map.addLayer({ id: 'infra-label', type: 'symbol', source: 'infra', layout: { 'text-field': ['get', 'label'], 'text-size': 11, 'text-offset': [0, 2.2], 'text-font': ['Open Sans Semibold'] }, paint: { 'text-color': '#e6f0e9', 'text-halo-color': '#0b1410', 'text-halo-width': 1.4 } })
      map.addSource('paths', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'paths', type: 'line', source: 'paths', paint: { 'line-color': ['get', 'color'], 'line-width': 1.6, 'line-dasharray': [2, 2], 'line-opacity': 0.85 } })
      map.addSource('boundary', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'boundary-fill', type: 'fill', source: 'boundary', filter: ['==', ['geometry-type'], 'Polygon'], paint: { 'fill-color': '#7ce38b', 'fill-opacity': ['case', ['boolean', ['get', 'done'], false], 0.14, 0.08] } })
      map.addLayer({ id: 'boundary-line', type: 'line', source: 'boundary', filter: ['==', ['geometry-type'], 'LineString'], paint: { 'line-color': '#7ce38b', 'line-width': 2.5 } })
      map.addLayer({ id: 'boundary-vertex', type: 'circle', source: 'boundary', filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-radius': ['case', ['boolean', ['get', 'first'], false], 7, 5], 'circle-color': ['case', ['boolean', ['get', 'first'], false], '#f5c451', '#7ce38b'], 'circle-stroke-color': '#0b1410', 'circle-stroke-width': 2 } })
      map.addSource('route', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'route-line', type: 'line', source: 'route', filter: ['==', ['geometry-type'], 'LineString'], paint: { 'line-color': '#7ce38b', 'line-width': 2.2, 'line-dasharray': [1, 1.5] } })
      map.addLayer({ id: 'route-pt', type: 'circle', source: 'route', filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-radius': 9, 'circle-color': '#0b1410', 'circle-stroke-color': '#7ce38b', 'circle-stroke-width': 2 } })
      map.addLayer({ id: 'route-no', type: 'symbol', source: 'route', filter: ['==', ['geometry-type'], 'Point'], layout: { 'text-field': ['get', 'n'], 'text-size': 10, 'text-font': ['Open Sans Semibold'] }, paint: { 'text-color': '#7ce38b' } })
      // patterns: weeds (red hatch) and waterlogged (blue hatch)
      const mk = (color: string, slope: 1 | -1) => { const c = document.createElement('canvas'); c.width = c.height = 8; const g = c.getContext('2d')!; g.strokeStyle = color; g.lineWidth = 1.6; g.beginPath(); if (slope === 1) { g.moveTo(0, 8); g.lineTo(8, 0) } else { g.moveTo(0, 0); g.lineTo(8, 8) } g.stroke(); return g.getImageData(0, 0, 8, 8) }
      map.addImage('weeds', mk('rgba(255,77,94,.95)', 1), { pixelRatio: 1 })
      map.addImage('water', mk('rgba(92,200,255,.85)', -1), { pixelRatio: 1 })
      altaraRef.current = new AltaraRenderer(map, el.current!, { getSelected: () => useUI.getState().selectedRobot, getHover: () => null, onStats: onFps,
        getSelection: () => useUI.getState().selected })
      setReady(true)
    })

    let hovered: string | null = null
    map.on('mousemove', 'section-fill', (e: MapMouseEvent & { features?: MapGeoJSONFeature[] }) => {
      const id = e.features?.[0]?.properties?.id as string | undefined
      if (hovered && hovered !== id) map.setFeatureState({ source: 'layout', id: hovered }, { hover: false })
      if (id) map.setFeatureState({ source: 'layout', id }, { hover: true })
      hovered = id ?? null
      setHoverInfo(hovered ? `section ${hovered}` : null)
    })
    map.on('mouseleave', 'section-fill', () => { if (hovered) map.setFeatureState({ source: 'layout', id: hovered }, { hover: false }); hovered = null; setHoverInfo(null) })

    // ---- left click: select units / dispatch when armed / inspect the layout
    map.doubleClickZoom.disable()
    map.on('dblclick', (e: MapMouseEvent) => { if (boundary.current.active) { e.preventDefault(); finishBoundary() } })
    map.on('click', async (e: MapMouseEvent) => {
      const ui = useUI.getState()
      const b = boundary.current
      if (b.active) {
        if (b.pts.length >= 3) {
          const first = map.project(b.pts[0])
          if (Math.hypot(first.x - e.point.x, first.y - e.point.y) < 12) { finishBoundary(); return }
        }
        b.pts.push([e.lngLat.lng, e.lngLat.lat])
        renderBoundary()
        return
      }
      const hit = altaraRef.current?.hit(e.point.x, e.point.y)
      const multi = e.originalEvent.ctrlKey || e.originalEvent.metaKey || e.originalEvent.shiftKey
      if (hit && !ui.dispatchArm) { if (multi) ui.toggleSelect(hit); else ui.selectRobot(hit === ui.selectedRobot && ui.selected.length === 1 ? null : hit); return }
      const feats = map.queryRenderedFeatures(e.point, { layers: ['section-fill', 'track-fill', 'hazard-fill', 'pad-fill'] })
      const sec = feats.find((f) => f.properties?.layer === 'section')
      const secId = sec?.properties?.id as string | undefined
      if (ui.dispatchArm) {
        const { robotId, kind } = ui.dispatchArm
        try {
          const m = await api.dispatch(robotId, secId && kind !== 'goto' ? { kind, target_section: secId } : { kind, lat: e.lngLat.lat, lng: e.lngLat.lng })
          notify(`${robotId} → ${m.kind}${secId ? ` · section ${secId}` : ''}${m.waypoints ? ` · ${m.waypoints.length} on-track waypoints` : ''}`, 'ok')
        } catch (err) { notify((err as Error).message, 'err') }
        armDispatch(null); return
      }
      if (!multi && ui.selected.length && !feats.length) ui.select([])
      const f0 = feats[0]
      if (!f0) return
      popupRef.current?.remove()
      const p = { ...f0.properties, ...(f0.properties?.layer === 'section' ? sectionOverrides.get(String(f0.properties?.id)) ?? {} : {}) } as Record<string, unknown>
      const html = p.layer === 'section'
        ? sectionPopup(p)
        : p.layer === 'track' ? `<div style="font-weight:600">${{ lane: 'Tramline', headland: 'Headland', alley: 'Farm track (alley)', spur: 'Yard spur', road: 'Existing road (from imagery)' }[String(p.kind)] ?? p.kind}</div><div style="color:#6b8273">${p.id}</div>`
        : p.layer === 'hazard' ? '<div style="color:#5cc8ff;font-weight:600">Waterlogged low spot</div><div>ground vehicles can bog down here</div>'
        : `<div style="font-weight:600">${p.id}</div><div style="color:#6b8273">${String(p.kind).replace('_', ' ')} · farm yard</div>`
      popupRef.current = new maplibregl.Popup({ closeButton: false, maxWidth: '280px' }).setLngLat(e.lngLat)
        .setHTML(`<div class="mono" style="font-size:11px;line-height:1.5">${html}</div>`).addTo(map)
    })

    // ---- right click: RTS move / route waypoint
    map.on('contextmenu', async (e: MapMouseEvent) => {
      e.preventDefault()
      const ui = useUI.getState()
      if (!ui.selected.length) return
      if (e.originalEvent.shiftKey) { ui.addRoutePoint({ lat: e.lngLat.lat, lng: e.lngLat.lng }); return }
      for (const rid of ui.selected) {  // ground units: the backend snaps the goal onto the nearest track
        try { await api.dispatch(rid, { kind: 'goto', lat: e.lngLat.lat, lng: e.lngLat.lng }) }
        catch (err) { notify(`${rid}: ${(err as Error).message}`, 'err') }
      }
      notify(`Move order → ${ui.selected.join(', ')}`, 'ok')
      altaraRef.current?.flash(e.lngLat.lat, e.lngLat.lng, '#7ce38b')
    })

    mapRef.current = map
    // The map container is re-positioned over whichever view hosts it; track its own size.
    const ro = new ResizeObserver(() => map.resize())
    ro.observe(el.current)
    // debug handles (harmless in production; used by the browser-based smoke tests)
    ;(window as unknown as { __map?: MLMap }).__map = map
    return () => { ro.disconnect(); altaraRef.current?.destroy(); map.remove(); mapRef.current = null }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // resize when the view that hosts the map becomes visible again
  useEffect(() => { if (visible && mapRef.current) setTimeout(() => mapRef.current?.resize(), 30) }, [visible])
  useEffect(() => { if (ready && mode !== 'draw' && boundary.current.active) resetBoundary() }, [mode, ready]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!boundary.current.active) return
      if (e.key === 'Backspace') { boundary.current.pts.pop(); renderBoundary() }
      if (e.key === 'Enter') finishBoundary()
      if (e.key === 'Escape') resetBoundary()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------- farm data
  useEffect(() => {
    const map = mapRef.current as (MLMap & { __layout?: GeoJSON.FeatureCollection }) | null
    if (!map || !ready) return
    const clear = (id: string) => (map.getSource(id) as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: [] })
    if (!farm) { for (const id of ['layout', 'infra', 'beds', 'weeds']) clear(id); map.__layout = undefined; return }
    let cancelled = false
    ;(async () => {
      const [layoutFC, infra] = await Promise.all([
        farm.plan ? api.layout(farm.id) : Promise.resolve({ type: 'FeatureCollection', features: [] } as GeoJSON.FeatureCollection),
        farm.plan ? api.infrastructure(farm.id) : Promise.resolve([] as Infrastructure[])])
      if (cancelled) return
      sectionOverrides.clear()
      ;(map.getSource('layout') as GeoJSONSource).setData(layoutFC)
      ;(map.getSource('infra') as GeoJSONSource).setData({ type: 'FeatureCollection', features: infra.map((i) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [i.lng, i.lat] },
        properties: { kind: i.kind, slug: i.slug, color: INFRA_COLOR[i.kind] ?? '#fff', label: `${{ solar_dock: '☀', wind_dock: '≋', storage: '⌂', repair_station: '⚒' }[i.kind] ?? '•'} ${i.slug}` } })) })
      ;(map.getSource('beds') as GeoJSONSource).setData(bedLabels(layoutFC))
      map.__layout = layoutFC
      paintSections(map, layoutFC, layer)
      resetBoundary()
      const bbox = tBbox(farm.boundary as GeoJSON.Feature)
      map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 60, duration: 900, maxZoom: 17.5 })
    })()
    return () => { cancelled = true }
  }, [farm?.id, farm?.plan, ready]) // eslint-disable-line react-hooks/exhaustive-deps

  // repaint the sections at most 4x a second: UAV sightings can arrive several times a second
  const paintTimer = useRef<number | null>(null)
  useEffect(() => {
    const map = mapRef.current as (MLMap & { __layout?: GeoJSON.FeatureCollection }) | null
    if (!map?.__layout || !ready || paintTimer.current != null) return
    paintTimer.current = window.setTimeout(() => {
      paintTimer.current = null
      const m = mapRef.current as (MLMap & { __layout?: GeoJSON.FeatureCollection }) | null
      if (m?.__layout) paintSections(m, m.__layout, useUI.getState().layer)
    }, 250)
  }, [layer, cellVersion, ready])
  useEffect(() => () => { if (paintTimer.current != null) window.clearTimeout(paintTimer.current) }, [])

  // site-analysis overlays: roads and obstacles found in the imagery, the classification image, soil maps
  const analysisV = useLive((s) => s.cellVersion) // cheap tick so overlays refresh after re-plans
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const setImage = (id: string, url: string | null, coords: number[][] | null, before: string, opacity: number) => {
      if (map.getLayer(id)) map.removeLayer(id)
      if (map.getSource(id)) map.removeSource(id)
      if (!url || !coords) return
      map.addSource(id, { type: 'image', url, coordinates: coords as [[number, number], [number, number], [number, number], [number, number]] })
      map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': opacity, 'raster-fade-duration': 200 } }, map.getLayer(before) ? before : undefined)
    }
    const det = map.getSource('detect') as GeoJSONSource | undefined
    if (!farm) { det?.setData({ type: 'FeatureCollection', features: [] }); setImage('imgprev', null, null, 'track-fill', 0); setImage('soilimg', null, null, 'bed-label', 0); return }
    let cancelled = false
    worldApi.analysis(farm.id).then((a) => {
      if (cancelled) return
      const im = a.imagery
      const feats: GeoJSON.Feature[] = []
      if (im && overlays.detections) {
        for (const r of im.roads) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: r.lnglat }, properties: { kind: 'road', width: r.width_m, inside: r.inside } })
        for (const o of im.obstacles) if (o.lnglat.length >= 4) feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [o.lnglat] }, properties: { kind: o.kind, inside: o.inside } })
      }
      det?.setData({ type: 'FeatureCollection', features: feats })
      setImage('imgprev', overlays.imagery && im?.preview_corners ? `/api/farms/${farm.id}/imagery.jpg?v=${farm.id.slice(0, 6)}${a.status?.pct ?? ''}` : null, im?.preview_corners ?? null, 'track-fill', 0.85)
    }).catch(() => det?.setData({ type: 'FeatureCollection', features: [] }))
    if (overlays.soil && farm.status === 'active') {
      worldApi.soilLayers().then((sl) => { if (!cancelled) setImage('soilimg', `/api/world/soil/${overlays.soil}.png?f=${farm.id.slice(0, 6)}`, sl.coordinates, 'bed-label', 0.8) }).catch(() => {})
    } else setImage('soilimg', null, null, 'bed-label', 0)
    return () => { cancelled = true }
  }, [farm?.id, farm?.plan, farm?.status, overlays.detections, overlays.imagery, overlays.soil, ready, analysisV > -1]) // eslint-disable-line react-hooks/exhaustive-deps

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
    map.setFilter('section-target', ['==', ['get', 'id'], String((pm?.payload as { section?: string } | null)?.section ?? '')])
  }, [selected, selectedRobot, fleet, ready])

  // route builder overlay: the numbered clicks, plus the path the unit would really take
  // (ground units: along the tracks, from the backend's router; the UAV: straight lines)
  const routeLoop = useUI((st) => st.routeLoop)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const pts: GeoJSON.Feature[] = route.map((p, i) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lng, p.lat] }, properties: { n: String(i + 1) } }))
    const straight: GeoJSON.Feature[] = route.length > 1 ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: [...route.map((p) => [p.lng, p.lat]), ...(routeLoop ? [[route[0].lng, route[0].lat]] : [])] }, properties: {} }] : []
    const put = (lines: GeoJSON.Feature[]) => (map.getSource('route') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: [...lines, ...pts] })
    put(straight)
    const robot = selectedRobot
    if (!robot || !route.length) return
    let cancelled = false
    const t = window.setTimeout(() => {
      api.routePreview(robot, { waypoints: route.map((p) => ({ lat: p.lat, lng: p.lng })), loop: routeLoop })
        .then((f) => { if (!cancelled) put([f]) })
        .catch(() => {})
    }, 120)
    return () => { cancelled = true; window.clearTimeout(t) }
  }, [route, routeLoop, selectedRobot, ready])

  useEffect(() => { if (!chaosFlash) return; const r = fleet[chaosFlash.robot_id]; if (r?.lat != null && r.lng != null) altaraRef.current?.flash(r.lat, r.lng) }, [chaosFlash]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (mapRef.current) mapRef.current.getCanvas().style.cursor = dispatchArm ? 'crosshair' : selected.length ? 'default' : '' }, [dispatchArm, selected])

  return (
    <div className="relative h-full w-full">
      <div ref={el} className="h-full w-full" />
      {!imagery && <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-panel/80">
        <div className="flex items-center gap-2 rounded-md border border-line bg-bg/90 px-3 py-2 text-xs text-fg-2">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-leaf border-t-transparent" />Loading satellite imagery…</div></div>}
      {dispatchArm && <div className="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2 rounded-md border border-leaf-2 bg-bg/90 px-3 py-1.5 text-xs text-leaf anim-slide-up">Click a target for <b>{dispatchArm.robotId}</b> · {dispatchArm.kind} <span className="kbd ml-2">Esc</span></div>}
      {drawState.active && <div className="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2 rounded-md border border-leaf-2 bg-bg/90 px-3 py-1.5 text-xs text-fg anim-slide-up">
        <b className="text-leaf">Drawing boundary</b> · {drawState.n} vertices{drawState.areaM2 > 0 ? ` · ${(drawState.areaM2 / 10000).toFixed(2)} ha` : ''} · click the first point / double-click / <span className="kbd">Enter</span> to close · <span className="kbd">⌫</span> undo · <span className="kbd">Esc</span> cancel</div>}
      {hoverInfo && !dispatchArm && !drawState.active && <div className="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2 rounded-md border border-line bg-bg/90 px-2.5 py-1 font-mono text-[11px] text-fg shadow-lg">{hoverInfo}</div>}
    </div>
  )
})

function sectionPopup(p: Record<string, unknown>): string {
  const pct = (v: unknown) => (v == null ? '—' : `${Math.round(Number(v) * 100)}%`)
  const soil = (typeof p.soil === 'string' ? JSON.parse(p.soil as string) : p.soil) as Record<string, number> | null
  const muted = 'color:#72857a'
  const row = (k: string, v: string) => `<div style="display:flex;gap:12px"><span style="${muted}">${k}</span><span style="margin-left:auto">${v}</span></div>`
  return `<div style="color:#5fd68e;font-weight:600">Section ${p.id} · ${String(p.bed).replace('bed-', 'Bed ')}</div>
    <div style="margin-bottom:4px">${p.crop} · <b>${String(p.state ?? '').replace('_', ' ')}</b></div>
    ${p.surveyed || p.ndvi != null ? [row('NDVI', p.ndvi == null ? '—' : Number(p.ndvi).toFixed(2)), row('Water stress (CWSI)', p.cwsi == null ? '—' : Number(p.cwsi).toFixed(2)),
      row('Soil water', pct(p.moisture)), row('Health', pct(p.health)), row('Disease', p.disease ? pct(p.disease) : 'none seen'),
      row('Weeds', p.weeds ? '<span style="color:#f06464">present</span>' : 'none seen'), p.yield_est != null ? row('Yield estimate', `${Number(p.yield_est).toFixed(1)} t/ha`) : ''].join('')
      : `<div style="${muted}">not scouted yet: the UAV measures it on its next survey</div>`}
    ${soil ? `<div style="margin-top:4px;border-top:1px solid #1e2b25;padding-top:4px">${row('Soil', `clay ${soil.clay}% · OM ${soil.om}% · pH ${soil.ph}`)}${row('Available water', `${soil.taw_mm_m} mm/m`)}${row('Salinity', `${soil.ec} dS/m`)}</div>` : ''}`
}

/** One label per bed, placed on its middle section and rotated along the bed. */
function bedLabels(layout: GeoJSON.FeatureCollection): GeoJSON.FeatureCollection {
  const byBed = new Map<string, GeoJSON.Feature<GeoJSON.Polygon>[]>()
  for (const f of layout.features) {
    if (f.properties?.layer !== 'section') continue
    const b = String(f.properties.bed)
    ;(byBed.get(b) ?? byBed.set(b, []).get(b)!).push(f as GeoJSON.Feature<GeoJSON.Polygon>)
  }
  const out: GeoJSON.Feature[] = []
  for (const [bed, secs] of byBed) {
    const mid = secs[Math.floor(secs.length / 2)]
    const ring = mid.geometry.coordinates[0]
    const rot = -tBearing(tPoint(ring[0]), tPoint(ring[1])) + 90
    out.push({ type: 'Feature', geometry: tCentroid(mid).geometry, properties: { label: `${bed.replace('bed-', 'Bed ')}  ${mid.properties?.crop}`, rot } })
  }
  return { type: 'FeatureCollection', features: out }
}

function paintSections(map: MLMap, layout: GeoJSON.FeatureCollection, layer: string) {
  const weeds: GeoJSON.Feature[] = []
  for (const f of layout.features) {
    if (f.properties?.layer !== 'section') continue
    const p = { ...f.properties, ...(sectionOverrides.get(String(f.properties.id)) ?? {}) } as Record<string, unknown>
    let color: string
    const unseen = '#3a4741'
    if (layer === 'plan' || layer === 'crop') color = CROP_COLORS[String(p.crop)] ?? '#888'
    else if (layer === 'moisture') color = p.moisture == null ? unseen : ramp(Number(p.moisture), ['#8a6a3d', '#c9a96a', '#5fade8', '#1d5fd1'])
    else if (layer === 'health') color = p.health == null ? unseen : ramp(Number(p.health), ['#f06464', '#e2b44a', '#5fd68e'])
    else if (layer === 'ndvi') color = p.ndvi == null ? unseen : ramp(Number(p.ndvi), ['#8a6a3d', '#c9b46a', '#7fc45a', '#1f7a3c'])
    else if (layer === 'yield') color = p.yield_est == null ? unseen : ramp(Math.min(1, Number(p.yield_est) / 14), ['#4b3a26', '#c9a640', '#e2b44a', '#fff1b8'])
    else color = STAGE_COLORS[String(p.state)] ?? '#6b5a3e'
    map.setFeatureState({ source: 'layout', id: String(p.id) }, { color })
    if (p.weeds) weeds.push({ type: 'Feature', geometry: tCentroid(f as GeoJSON.Feature<GeoJSON.Polygon>).geometry, properties: { id: p.id } })
  }
  ;(map.getSource('weeds') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: weeds })
}

function ramp(v: number, stops: string[]) { const t = Math.max(0, Math.min(1, v)) * (stops.length - 1); const i = Math.min(stops.length - 2, Math.floor(t)); const k = t - i; const a = hex(stops[i]), b = hex(stops[i + 1]); return `rgb(${Math.round(a[0] + (b[0] - a[0]) * k)},${Math.round(a[1] + (b[1] - a[1]) * k)},${Math.round(a[2] + (b[2] - a[2]) * k)})` }
function hex(h: string) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)] }
