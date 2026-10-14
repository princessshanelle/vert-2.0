<script setup>
// Zone analytics: how well the climate held the strawberry targets, how
// the plants responded, and when in the day things go wrong. One filter
// row scopes everything on the page; every chart has a table twin.
import { ref, computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDevicesStore } from '../stores/devices'
import TrendChart from '../components/charts/TrendChart.vue'
import DayPattern from '../components/charts/DayPattern.vue'
import { TARGETS, vpd, dewPoint, plantReading } from '../lib/agronomy'
import { fetchSeries, zipSeries, dailySummary, timeInRange } from '../lib/series'
import client from '../api/client'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const store = useDevicesStore()

const RANGES = [
  { id: '24h', minutes: 1440 },
  { id: '7d', minutes: 10080 },
  { id: '30d', minutes: 43200 }
]
const zoneId = ref(route.query.zone || '')
const range = ref(route.query.range || '7d')
const minutes = computed(() => RANGES.find((r) => r.id === range.value)?.minutes ?? 10080)
const loading = ref(false)
const series = ref({})
const tables = ref({})

const zoneDevices = computed(() => store.devices.filter((d) => d.zone_id === zoneId.value))
const climateDev = computed(() => zoneDevices.value.find((d) => d.device_type === 'temp-humidity'))
const soilDev = computed(() => zoneDevices.value.find((d) => d.device_type === 'soil-moisture'))
const cameraDev = computed(() => zoneDevices.value.find((d) => plantReading(d)))
const conditionMetrics = computed(() => Object.keys(cameraDev.value?.last_reading || {})
  .filter((k) => k.startsWith('cond_') && k.endsWith('_conf')))

// What each chart shows and where its numbers come from.
const PANELS = computed(() => [
  { id: 'temp', group: 'climate', title: t('climate.temp_c'), dev: climateDev.value, metric: 'temp_c', unit: '°C', color: '--series-temp', target: TARGETS.temp_c.ideal },
  { id: 'rh', group: 'climate', title: t('climate.humidity_pct'), dev: climateDev.value, metric: 'humidity_pct', unit: '%', digits: 0, color: '--series-humidity', target: TARGETS.humidity_pct.ideal },
  { id: 'vpd', group: 'climate', title: t('climate.vpd_kpa'), derived: 'vpd', unit: ' kPa', digits: 2, color: '--series-vpd', target: TARGETS.vpd_kpa.ideal, note: t('analytics.vpd_note') },
  { id: 'dew', group: 'climate', title: t('analytics.dew_gap'), derived: 'dew', unit: '°C', color: '--series-humidity', target: [2, 15], note: t('analytics.dew_note') },
  { id: 'moisture', group: 'climate', title: t('climate.moisture_pct'), dev: soilDev.value, metric: 'moisture_pct', unit: '%', digits: 0, color: '--series-moisture', target: TARGETS.moisture_pct.ideal },
  { id: 'health', group: 'plants', title: t('analytics.health'), dev: cameraDev.value, metric: 'health_score', unit: '', digits: 2, color: '--series-health', yMin: 0, yMax: 1 },
  ...conditionMetrics.value.map((m) => ({ id: m, group: 'plants', title: t('analytics.condition_signal', { condition: t(`conditions.${m.slice(5, -5)}`, m.slice(5, -5)) }),
    dev: cameraDev.value, metric: m, unit: '', digits: 2, color: '--series-temp', yMin: 0, yMax: 1, note: t('analytics.signal_note') })),
  { id: 'botrytis', group: 'plants', title: t('risk.botrytis'), dev: cameraDev.value, metric: 'risk_botrytis', unit: '', digits: 2, color: '--series-vpd', yMin: 0, yMax: 1 },
  { id: 'ripe', group: 'plants', title: t('analytics.ripe_fruit'), dev: cameraDev.value, metric: 'fruit_ripe_count', unit: '', digits: 0, color: '--series-temp' },
  { id: 'canopy', group: 'plants', title: t('analytics.canopy'), dev: cameraDev.value, metric: 'canopy_coverage_pct', unit: '%', digits: 0, color: '--series-moisture' },
  { id: 'lux', group: 'plants', title: t('analytics.light'), dev: cameraDev.value, metric: 'lux', unit: '', digits: 0, color: '--series-light' }
])

async function load() {
  if (!zoneId.value) return
  loading.value = true
  const out = {}
  const tasks = PANELS.value.filter((p) => p.dev).map(async (p) => {
    try {
      out[p.id] = await fetchSeries(p.dev.id, p.metric, minutes.value)
    } catch {
      out[p.id] = []
    }
  })
  await Promise.all(tasks)
  if (out.temp?.length && out.rh?.length) {
    out.vpd = zipSeries(out.temp, out.rh, (tc, rh) => vpd(tc, rh))
    out.dew = zipSeries(out.temp, out.rh, (tc, rh) => {
      const dp = dewPoint(tc, rh)
      return dp === null ? null : tc - dp
    })
  }
  series.value = out
  loading.value = false
}

onMounted(async () => {
  if (!store.zones.length) await store.fetchZones()
  if (!store.devices.length) await store.fetchDevices()
  if (!zoneId.value) zoneId.value = store.zones.find((z) => store.devices.some((d) => d.zone_id === z.id))?.id || store.zones[0]?.id || ''
  load()
})
watch([zoneId, range], () => {
  router.replace({ query: { zone: zoneId.value, range: range.value } })
  load()
})

const visiblePanels = computed(() => PANELS.value.filter((p) => (series.value[p.id] || []).length > 1))
const groups = computed(() => ['climate', 'plants'].map((g) => ({ id: g, panels: visiblePanels.value.filter((p) => p.group === g) }))
  .filter((g) => g.panels.length))

function latest(p) {
  const s = series.value[p.id]
  return s?.length ? s[s.length - 1].v : null
}
function inRange(p) {
  return p.target ? timeInRange(series.value[p.id] || [], p.target[0], p.target[1]) : null
}
function fmt(v, digits = 1) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '–'
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

// Headline KPIs: time in target range is the number a grower manages to.
const kpis = computed(() => {
  const out = []
  for (const id of ['temp', 'rh', 'vpd']) {
    const p = PANELS.value.find((x) => x.id === id)
    const r = p && inRange(p)
    if (r !== null && r !== undefined) out.push({ id, label: t('analytics.kpi_in_range', { what: p.title }), value: Math.round(r * 100), unit: '%' })
  }
  const h = series.value.health
  if (h?.length > 3) {
    const half = Math.floor(h.length / 2)
    const avg = (xs) => xs.reduce((a, p) => a + p.v, 0) / xs.length
    const first = avg(h.slice(0, half))
    const second = avg(h.slice(half))
    out.push({ id: 'health', label: t('analytics.kpi_health'), value: Math.round(second * 100), unit: '',
      delta: Math.round((second - first) * 100) })
  }
  return out
})

// Plain-language findings computed from the series (no model involved).
const insights = computed(() => {
  const out = []
  const rh = series.value.rh || []
  if (rh.length > 3) {
    const humid = rh.filter((p) => p.max >= TARGETS.humidity_pct.high)
    if (humid.length) {
      const byHour = new Array(24).fill(0)
      humid.forEach((p) => byHour[p.t.getHours()]++)
      const peak = byHour.indexOf(Math.max(...byHour))
      const stepH = rh.length > 1 ? (rh[1].t - rh[0].t) / 3600000 : 1
      out.push({ tone: 'warning', text: t('analytics.insight_humid', { hours: Math.round(humid.length * stepH),
        at: `${String(peak).padStart(2, '0')}:00` }) })
    }
  }
  const v = series.value.vpd || []
  if (v.length > 3) {
    const low = v.filter((p) => p.v < TARGETS.vpd_kpa.ideal[0]).length / v.length
    if (low > 0.2) out.push({ tone: 'warning', text: t('analytics.insight_vpd_low', { pct: Math.round(low * 100) }) })
  }
  for (const m of conditionMetrics.value) {
    const s = series.value[m] || []
    const firstHigh = s.find((p) => p.v >= 0.45)
    if (firstHigh) {
      out.push({ tone: 'serious', text: t('analytics.insight_condition', {
        condition: t(`conditions.${m.slice(5, -5)}`, m.slice(5, -5)),
        when: firstHigh.t.toLocaleString(undefined, { weekday: 'long', hour: '2-digit', minute: '2-digit' }) }) })
    }
  }
  const ripe = series.value.ripe || []
  if (ripe.length > 3) {
    const delta = ripe[ripe.length - 1].v - ripe[0].v
    if (delta > 0) out.push({ tone: 'good', text: t('analytics.insight_ripening', { n: Math.round(delta) }) })
  }
  return out
})

function toggleTable(id) {
  tables.value = { ...tables.value, [id]: !tables.value[id] }
}

const exporting = ref(false)
async function exportZone() {
  exporting.value = true
  try {
    for (const d of zoneDevices.value) {
      const { data } = await client.get(`/api/v1/devices/${d.id}/readings/export`, { params: { minutes: minutes.value }, responseType: 'blob' })
      const url = URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${d.id}-${range.value}.csv`
      a.click()
      URL.revokeObjectURL(url)
    }
  } catch {
    // a device with no history yet has nothing to export
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <main class="analytics">
    <header class="head">
      <h1>{{ t('analytics.title') }}</h1>
      <p class="muted">{{ t('analytics.subtitle') }}</p>
    </header>

    <div class="filters" role="group" :aria-label="t('analytics.filters')">
      <label class="zone-pick">
        <span>{{ t('analytics.zone') }}</span>
        <select v-model="zoneId">
          <option v-for="z in store.zones" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
      </label>
      <div class="seg" role="group" :aria-label="t('analytics.range')">
        <button v-for="r in RANGES" :key="r.id" :aria-pressed="range === r.id" @click="range = r.id">{{ t(`analytics.range_${r.id}`) }}</button>
      </div>
      <button class="btn export" :disabled="exporting || !zoneDevices.length" @click="exportZone">
        {{ exporting ? t('device_detail.exporting') : t('analytics.export') }}
      </button>
    </div>

    <div class="body" :class="{ refreshing: loading }">
      <section v-if="kpis.length" class="kpis" :aria-label="t('analytics.kpis')">
        <div v-for="k in kpis" :key="k.id" class="kpi">
          <span class="kpi-label">{{ k.label }}</span>
          <span class="kpi-value figure">{{ k.value }}<small>{{ k.unit }}</small></span>
          <span v-if="k.delta !== undefined" class="kpi-delta" :class="k.delta >= 0 ? 'up' : 'down'">
            {{ t(k.delta >= 0 ? 'analytics.delta_up' : 'analytics.delta_down', { n: Math.abs(k.delta) }) }}
          </span>
          <span v-else class="kpi-delta muted">{{ t(`analytics.range_${range}`) }}</span>
        </div>
      </section>

      <section v-if="insights.length" class="insights panel" :aria-label="t('analytics.insights')">
        <h2>{{ t('analytics.insights') }}</h2>
        <ul>
          <li v-for="(i, n) in insights" :key="n" :class="i.tone"><span class="mark" aria-hidden="true"></span>{{ i.text }}</li>
        </ul>
      </section>

      <section v-if="(series.rh || []).length > 6" class="panel pattern">
        <div class="panel-head">
          <h2>{{ t('analytics.pattern_title') }}</h2>
          <p class="muted">{{ t('analytics.pattern_sub') }}</p>
        </div>
        <DayPattern :points="series.rh" unit="%" :label="t('climate.humidity_pct')" />
      </section>

      <p v-if="!loading && !visiblePanels.length" class="panel empty">{{ zoneId ? t('analytics.no_data') : t('analytics.pick_zone') }}</p>

      <section v-for="g in groups" :key="g.id" class="group">
        <h2 class="group-title">{{ t(`analytics.group_${g.id}`) }}</h2>
        <div class="multiples">
          <article v-for="p in g.panels" :key="p.id" class="panel chart-card">
            <header class="chart-head">
              <h3>{{ p.title }}</h3>
              <span class="now figure">{{ fmt(latest(p), p.digits ?? 1) }}<small>{{ p.unit }}</small></span>
            </header>
            <p class="chart-sub muted">
              <template v-if="p.target">{{ t('analytics.in_range_line', { pct: Math.round((inRange(p) ?? 0) * 100), lo: p.target[0], hi: p.target[1], unit: p.unit }) }}</template>
              <template v-else-if="p.note">{{ p.note }}</template>
            </p>
            <TrendChart :points="series[p.id]" :color-var="p.color" :unit="p.unit" :digits="p.digits ?? 1"
                        :target="p.target" :y-min="p.yMin" :y-max="p.yMax" :height="170" :label="p.title" />
            <p v-if="p.target && p.note" class="chart-note muted">{{ p.note }}</p>
            <button class="table-toggle" :aria-expanded="!!tables[p.id]" @click="toggleTable(p.id)">
              {{ tables[p.id] ? t('analytics.hide_table') : t('analytics.show_table') }}
            </button>
            <table v-if="tables[p.id]" class="day-table tabular">
              <thead><tr><th>{{ t('analytics.day') }}</th><th>{{ t('analytics.min') }}</th><th>{{ t('analytics.mean') }}</th><th>{{ t('analytics.max') }}</th></tr></thead>
              <tbody>
                <tr v-for="d in dailySummary(series[p.id])" :key="d.day">
                  <td>{{ new Date(d.day + 'T12:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) }}</td>
                  <td>{{ fmt(d.min, p.digits ?? 1) }}</td><td>{{ fmt(d.mean, p.digits ?? 1) }}</td><td>{{ fmt(d.max, p.digits ?? 1) }}</td>
                </tr>
              </tbody>
            </table>
          </article>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.analytics { padding: 2rem clamp(1rem, 3vw, 2.5rem) 3rem; max-width: 1280px; margin: 0 auto; display: flex; flex-direction: column; gap: 1.25rem; }
.head p { margin-top: 0.35rem; }
.filters { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; position: sticky; top: 0; z-index: 5;
  background: var(--page); padding: 0.6rem 0; border-bottom: 1px solid var(--line); }
.zone-pick { display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; font-weight: 600; color: var(--ink-2); }
.zone-pick select { padding: 0.4rem 0.7rem; border-radius: 999px; border: 1px solid var(--line-strong); background: var(--surface); }
.export { margin-left: auto; }
.body { display: flex; flex-direction: column; gap: 1.25rem; transition: opacity 0.2s; }
.body.refreshing { opacity: 0.6; }

.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px; background: var(--line);
  border: 1px solid var(--line); border-radius: var(--radius-l); overflow: hidden; }
.kpi { background: var(--surface); padding: 1rem 1.2rem; display: flex; flex-direction: column; gap: 0.2rem; }
.kpi-label { font-size: 0.8rem; color: var(--ink-2); font-weight: 600; }
.kpi-value { font-size: 2.3rem; line-height: 1.05; color: var(--ink); }
.kpi-value small { font-size: 1rem; color: var(--ink-2); }
.kpi-delta { font-size: 0.78rem; font-weight: 600; }
.kpi-delta.up { color: var(--good); }
.kpi-delta.down { color: var(--critical); }

.insights h2 { margin-bottom: 0.6rem; }
.insights ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.insights li { display: flex; gap: 0.65rem; align-items: baseline; max-width: 80ch; }
.mark { width: 0.6rem; height: 0.6rem; border-radius: 50%; flex-shrink: 0; transform: translateY(-1px); }
.insights li.serious .mark { background: var(--serious-mark); }
.insights li.warning .mark { background: var(--warning-mark); }
.insights li.good .mark { background: var(--good); }

.panel-head { margin-bottom: 0.9rem; }
.panel-head p { font-size: 0.85rem; margin-top: 0.2rem; }

.group-title { font-size: 1.3rem; margin: 0.5rem 0 0.75rem; }
.multiples { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 1rem; align-items: start; }
.chart-card { display: flex; flex-direction: column; gap: 0.35rem; padding: 1.1rem 1.2rem; }
.chart-head { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; }
.chart-head h3 { font-size: 0.98rem; }
.now { font-size: 1.35rem; color: var(--ink); }
.now small { font-size: 0.8rem; color: var(--ink-2); }
.chart-sub { font-size: 0.78rem; min-height: 1.1rem; margin-bottom: 0.3rem; }
.chart-note { font-size: 0.74rem; }
.table-toggle { align-self: flex-start; border: 0; background: none; color: var(--canopy-2); font-size: 0.78rem; font-weight: 650; cursor: pointer; padding: 0.2rem 0; }
.day-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; margin-top: 0.3rem; }
.day-table th { text-align: right; color: var(--ink-3); font-weight: 600; padding: 0.3rem 0.4rem; border-bottom: 1px solid var(--line); }
.day-table td { text-align: right; padding: 0.3rem 0.4rem; border-bottom: 1px solid var(--line); }
.day-table th:first-child, .day-table td:first-child { text-align: left; }
.empty { color: var(--ink-2); }
@media (max-width: 560px) { .multiples { grid-template-columns: 1fr; } .export { margin-left: 0; } }
</style>
