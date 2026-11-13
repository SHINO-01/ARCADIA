export const fmt = {
  m: (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(2)} km` : `${v.toFixed(0)} m`),
  ha: (m2: number) => `${(m2 / 10_000).toFixed(2)} ha`,
  pct: (v: number) => `${Math.round(v)}%`,
  kw: (v: number) => `${v.toFixed(1)} kW`,
  kwh: (v: number) => `${v.toFixed(1)} kWh`,
  time: (iso: string | number) => new Date(typeof iso === 'number' ? iso * 1000 : iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  hour: (h: number) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.floor((h % 1) * 60)).padStart(2, '0')}`,
  short: (id: string) => (id.length > 10 ? `${id.slice(0, 8)}…` : id),
}
