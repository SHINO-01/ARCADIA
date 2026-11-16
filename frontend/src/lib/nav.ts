/** Navigation views (left rail order, keyboard shortcuts). */
import { AlertTriangle, BarChart3, Box, Globe2, Map as MapIcon, Radar } from 'lucide-react'
import type { View } from '../store/ui'

export const VIEWS: { id: View; label: string; icon: typeof MapIcon; key: string }[] = [
  { id: 'plan', label: 'Farm', icon: MapIcon, key: '1' },
  { id: 'world', label: 'World', icon: Globe2, key: '2' },
  { id: 'ops', label: 'Operations', icon: Radar, key: '3' },
  { id: 'twin', label: '3D Twin', icon: Box, key: '4' },
  { id: 'alerts', label: 'Alerts', icon: AlertTriangle, key: '5' },
  { id: 'analytics', label: 'Analytics', icon: BarChart3, key: '6' },
]
export const VIEW_KEYS = Object.fromEntries(VIEWS.map((v) => [v.key, v.id])) as Record<string, View>

