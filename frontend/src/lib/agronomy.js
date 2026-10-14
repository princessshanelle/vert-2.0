// Greenhouse arithmetic and the strawberry targets the dashboard judges
// readings against. Pure functions: no Vue, no API, so they're easy to
// reason about and to test.
//
// The targets are conservative starting points for day-neutral
// strawberries under cover, the same spirit as edge/plant-cam's crop
// profile: tune them to your variety and season.

export const TARGETS = {
  temp_c: { low: 15, ideal: [18, 26], high: 30 },
  humidity_pct: { low: 50, ideal: [60, 80], high: 90 },
  vpd_kpa: { low: 0.3, ideal: [0.5, 1.2], high: 1.6 },
  moisture_pct: { low: 35, ideal: [45, 70], high: 85 }
}

/** Saturation vapour pressure, kPa (Tetens). */
export function svp(tempC) {
  return 0.6108 * Math.exp((17.27 * tempC) / (tempC + 237.3))
}

/** Vapour-pressure deficit, kPa: how hard the air pulls water from the leaves. */
export function vpd(tempC, rhPct) {
  if (!Number.isFinite(tempC) || !Number.isFinite(rhPct)) return null
  return Math.max(0, svp(tempC) * (1 - rhPct / 100))
}

/** Dew point, °C (Magnus). Leaves at or below it get wet: Botrytis weather. */
export function dewPoint(tempC, rhPct) {
  if (!Number.isFinite(tempC) || !Number.isFinite(rhPct) || rhPct <= 0) return null
  const a = 17.27
  const b = 237.7
  const g = Math.log(rhPct / 100) + (a * tempC) / (b + tempC)
  return (b * g) / (a - g)
}

/** 'good' inside the ideal band, 'warning' outside it, 'serious' past low/high. */
export function bandStatus(metric, value) {
  const t = TARGETS[metric]
  if (!t || !Number.isFinite(value)) return 'neutral'
  if (value < t.low || value > t.high) return 'serious'
  if (value < t.ideal[0] || value > t.ideal[1]) return 'warning'
  return 'good'
}

/** Disease-risk index (0-1, from plant-cam) -> status. */
export function riskStatus(v) {
  if (!Number.isFinite(v)) return 'neutral'
  if (v >= 0.6) return 'serious'
  if (v >= 0.3) return 'warning'
  return 'good'
}

export const HEALTH_STATUS = { thriving: 'good', steady: 'good', stressed: 'warning', needs_attention: 'serious' }

export const RIPENESS = [
  { key: 'fruit_green_count', id: 'green' },
  { key: 'fruit_white_count', id: 'white' },
  { key: 'fruit_turning_count', id: 'turning' },
  { key: 'fruit_ripe_count', id: 'ripe' },
  { key: 'fruit_overripe_count', id: 'over' }
]

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const mean = (xs) => {
  const ys = xs.filter((x) => x !== null)
  return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : null
}

/** A plant-cam (or older inference) reading, or null for any other device. */
export function plantReading(device) {
  const r = device?.last_reading
  return r && typeof r.health_score === 'number' && typeof r.label === 'string' ? r : null
}

/** Findings from a plant-health/2 reading, active first, most confident first. */
export function findingsOf(reading) {
  if (!reading) return []
  let list = Array.isArray(reading.conditions) ? reading.conditions : []
  if (!list.length) {
    // plant-health/2 always carries the numeric triple; rebuild from it if the array is missing.
    list = Object.keys(reading)
      .filter((k) => k.startsWith('cond_') && k.endsWith('_active') && reading[k] > 0)
      .map((k) => {
        const id = k.slice(5, -7)
        return { id, status: 'suspected', active: true, confidence: num(reading[`cond_${id}_conf`]) ?? 0,
          persistence: num(reading[`cond_${id}_persist`]) ?? 0 }
      })
  }
  return [...list].sort((a, b) => (b.active - a.active) || (b.confidence - a.confidence))
}

export function ripenessOf(reading) {
  if (!reading) return null
  const parts = RIPENESS.map((r) => ({ ...r, n: num(reading[r.key]) ?? 0 }))
  const total = parts.reduce((a, p) => a + p.n, 0)
  return total ? { parts, total, modelled: reading.fruit_counts_model === 1 } : null
}

/** Everything the overview needs to know about one zone, from devices' latest readings. */
export function summariseZone(zone, devices) {
  const online = devices.filter((d) => d.status === 'online')
  const climateDevices = online.filter((d) => d.device_type === 'temp-humidity')
  const temp = mean(climateDevices.map((d) => num(d.last_reading?.temp_c)))
  const rh = mean(climateDevices.map((d) => num(d.last_reading?.humidity_pct)))
  const soilDevices = online.filter((d) => d.device_type === 'soil-moisture')
  const moisture = mean(soilDevices.map((d) => num(d.last_reading?.moisture_pct ?? d.last_reading?.soil_moisture_pct)))
  const cameras = devices.filter((d) => plantReading(d))
  const camera = cameras[0] || null
  const plant = camera ? plantReading(camera) : null
  return {
    zone,
    devices,
    online: online.length,
    offline: devices.filter((d) => d.status !== 'online'),
    climateDevice: climateDevices[0] || null,
    soilDevice: soilDevices[0] || null,
    camera,
    climate: temp === null && rh === null ? null : {
      temp, rh, vpd: temp !== null && rh !== null ? vpd(temp, rh) : null,
      dewPoint: temp !== null && rh !== null ? dewPoint(temp, rh) : null
    },
    moisture,
    plant,
    findings: findingsOf(plant).filter((f) => f.active),
    ripeness: ripenessOf(plant),
    risks: plant ? [
      { id: 'botrytis', v: num(plant.risk_botrytis), hours: num(plant.risk_botrytis_hours) },
      { id: 'powdery_mildew', v: num(plant.risk_powdery_mildew), hours: num(plant.risk_powdery_mildew_hours) }
    ].filter((r) => r.v !== null) : []
  }
}

const RANK = { serious: 3, critical: 3, warning: 2, good: 1, neutral: 0 }

/**
 * The morning briefing: what needs attention, worst first, as
 * translation keys + params so every locale words it naturally.
 */
export function briefing(summaries, unackAlerts) {
  const items = []
  for (const s of summaries) {
    const zone = s.zone.name
    for (const f of s.findings) {
      items.push({ tone: f.status === 'detected' ? 'serious' : 'warning', key: 'brief.finding',
        params: { zone, condition: f.id, n: f.persistence }, zoneId: s.zone.id, deviceId: s.camera?.id })
    }
    if (s.climate?.rh !== null && s.climate?.rh >= TARGETS.humidity_pct.high) {
      items.push({ tone: 'serious', key: 'brief.humid', params: { zone, rh: Math.round(s.climate.rh) }, zoneId: s.zone.id,
        deviceId: s.climateDevice?.id })
    } else if (s.climate?.vpd !== null && s.climate?.vpd !== undefined && bandStatus('vpd_kpa', s.climate.vpd) !== 'good') {
      items.push({ tone: bandStatus('vpd_kpa', s.climate.vpd), key: s.climate.vpd < TARGETS.vpd_kpa.ideal[0] ? 'brief.vpd_low' : 'brief.vpd_high',
        params: { zone, vpd: s.climate.vpd.toFixed(2) }, zoneId: s.zone.id, deviceId: s.climateDevice?.id })
    }
    if (s.climate?.temp !== null && s.climate?.temp !== undefined && bandStatus('temp_c', s.climate.temp) === 'serious') {
      items.push({ tone: 'serious', key: s.climate.temp > TARGETS.temp_c.high ? 'brief.hot' : 'brief.cold',
        params: { zone, temp: s.climate.temp.toFixed(1) }, zoneId: s.zone.id, deviceId: s.climateDevice?.id })
    }
    for (const r of s.risks) {
      if (r.v >= 0.5) {
        items.push({ tone: riskStatus(r.v), key: `brief.risk_${r.id}`, params: { zone, hours: Math.round(r.hours ?? 0) },
          zoneId: s.zone.id, deviceId: s.camera?.id })
      }
    }
    if (s.moisture !== null && bandStatus('moisture_pct', s.moisture) !== 'good') {
      items.push({ tone: bandStatus('moisture_pct', s.moisture), key: s.moisture < TARGETS.moisture_pct.ideal[0] ? 'brief.dry' : 'brief.wet',
        params: { zone, v: Math.round(s.moisture) }, zoneId: s.zone.id, deviceId: s.soilDevice?.id })
    }
    const ripe = s.ripeness?.parts.find((p) => p.id === 'ripe')?.n ?? 0
    const over = s.ripeness?.parts.find((p) => p.id === 'over')?.n ?? 0
    if (ripe + over >= 3) {
      items.push({ tone: 'good', key: 'brief.harvest', params: { zone, n: ripe + over }, zoneId: s.zone.id, deviceId: s.camera?.id })
    }
    for (const d of s.offline) {
      items.push({ tone: 'warning', key: 'brief.offline', params: { zone, device: d.display_name || d.id }, zoneId: s.zone.id, deviceId: d.id })
    }
  }
  items.sort((a, b) => RANK[b.tone] - RANK[a.tone])
  const needs = items.filter((i) => i.tone !== 'good').length
  const worst = items.length ? items[0].tone : 'good'
  return {
    tone: needs ? worst : 'good',
    headlineKey: needs ? 'brief.headline_needs' : 'brief.headline_calm',
    headlineParams: { n: needs, alerts: unackAlerts },
    items
  }
}
