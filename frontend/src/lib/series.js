// History fetching for charts that span hours to weeks. Long windows ask
// the gateway for bucketed points (average + min + max per bucket), so a
// week of 30-second readings arrives as ~170 points, not ~20,000.
import client from '../api/client'

export function bucketFor(minutes) {
  if (minutes <= 360) return 0 // raw: every reading
  if (minutes <= 1440) return 600 // 10 min
  if (minutes <= 10080) return 3600 // 1 h
  return 3 * 3600
}

export async function fetchSeries(deviceId, metric, minutes) {
  const bucket = bucketFor(minutes)
  const { data } = await client.get(`/api/v1/devices/${deviceId}/readings`, {
    params: bucket ? { metric, minutes, bucket } : { metric, minutes }
  })
  return (data || []).map((p) => ({ t: new Date(p.t), v: p.v, min: p.min ?? p.v, max: p.max ?? p.v }))
}

/** Join two series on timestamp (same device => same envelope timestamps / buckets). */
export function zipSeries(a, b, fn) {
  const byTime = new Map(b.map((p) => [p.t.getTime(), p]))
  const out = []
  for (const p of a) {
    const q = byTime.get(p.t.getTime())
    if (!q) continue
    const v = fn(p.v, q.v)
    if (v === null || !Number.isFinite(v)) continue
    out.push({ t: p.t, v, min: v, max: v })
  }
  return out
}

/** Per-day min / mean / max (local days), oldest first: the table twin of a chart. */
export function dailySummary(points) {
  const days = new Map()
  for (const p of points) {
    const key = p.t.toLocaleDateString('en-CA') // YYYY-MM-DD in local time
    const d = days.get(key) || { day: key, min: Infinity, max: -Infinity, sum: 0, n: 0 }
    d.min = Math.min(d.min, p.min)
    d.max = Math.max(d.max, p.max)
    d.sum += p.v
    d.n++
    days.set(key, d)
  }
  return [...days.values()].map((d) => ({ day: d.day, min: d.min, max: d.max, mean: d.sum / d.n }))
}

/** Share of points inside [lo, hi]: "time in range", the grower's KPI. */
export function timeInRange(points, lo, hi) {
  if (!points.length) return null
  return points.filter((p) => p.v >= lo && p.v <= hi).length / points.length
}
