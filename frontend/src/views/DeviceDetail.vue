<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useDevicesStore } from '../stores/devices'
import { useAuthStore } from '../stores/auth'
import SensorChart from '../components/SensorChart.vue'
import { subscribe } from '../api/liveSocket'
import client from '../api/client'

const { t, locale } = useI18n()
const route = useRoute()
const store = useDevicesStore()
const auth = useAuthStore()

const device = ref(null)
const frames = ref([])
const loading = ref(true)
const notFound = ref(false)
const assigningZone = ref(false)

const canManage = computed(() => ['admin', 'operator'].includes(auth.user?.role))
const isCamera = computed(() => ['camera', 'ai-camera'].includes(device.value?.device_type))

const chartableMetrics = computed(() => {
  const reading = device.value?.last_reading || {}
  return Object.keys(reading).filter((k) => typeof reading[k] === 'number')
})
const activeMetric = ref(null)

// Two metrics known to always be measured together -- and worth reading
// together, since it's the temperature/humidity combination (not either
// one alone) that tells you anything about mold risk, VPD, or plant
// stress. When a device has both, one merged chart replaces the
// tab-switcher so both lines are visible at once instead of hiding one
// behind the other.
const comboSeries = [
  { metric: 'temp_c', label: t('readings.temp_c'), color: '#e2572b', axis: 'y' },
  { metric: 'humidity_pct', label: t('readings.humidity_pct'), color: '#1e88e5', axis: 'y1' }
]
const showComboChart = computed(() =>
  ['temp_c', 'humidity_pct'].every((m) => chartableMetrics.value.includes(m))
)
const activeRangeMinutes = ref(60)
const ranges = [
  { label: '30m', minutes: 30 },
  { label: '1h', minutes: 60 },
  { label: '6h', minutes: 360 },
  { label: '24h', minutes: 1440 }
]
watch(chartableMetrics, (metrics) => {
  if ((!activeMetric.value || !metrics.includes(activeMetric.value)) && metrics.length) {
    activeMetric.value = metrics[0]
  }
}, { immediate: true })

const liveRequested = ref(false)
const streamUrl = computed(() => {
  const ip = device.value?.local_ip
  return ip ? `http://${ip}:81/stream` : null
})
async function toggleLive() {
  liveRequested.value = !liveRequested.value
  try {
    await store.sendCommand(device.value.id, liveRequested.value ? 'start_stream' : 'stop_stream')
  } catch {
    // Reflect the user's intent regardless -- see DeviceCard's identical
    // toggle for the reasoning.
  }
}

// A health reading (from services/inference) has this shape; anything
// else is a plain sensor reading and gets rendered as simple stat tiles.
const healthReading = computed(() => {
  const r = device.value?.last_reading
  return r && typeof r.label === 'string' && typeof r.note === 'string' ? r : null
})
// liveOverlay holds any metric this device has published over the
// WebSocket since the page loaded, taking priority over device.last_reading
// -- which is only ever a snapshot from the moment `load()` ran. Without
// this, the "latest reading" tiles above the chart would sit frozen at
// whatever value was true on page load even while the chart right below
// them keeps moving, which is exactly the stale-tile bug this fixes.
const liveOverlay = ref({})
let unsubscribeLive = null
function onLiveReading(msg) {
  if (msg.device_id !== device.value?.id) return
  liveOverlay.value = { ...liveOverlay.value, [msg.metric]: msg.v }
}

const sensorReading = computed(() => {
  if (healthReading.value) return null
  if (!device.value?.last_reading) return null
  return { ...device.value.last_reading, ...liveOverlay.value }
})

const healthTone = computed(() => {
  const label = healthReading.value?.label
  if (label === 'thriving') return 'good'
  if (label === 'needs_attention') return 'warn'
  return 'neutral'
})

async function load() {
  loading.value = true
  notFound.value = false
  liveOverlay.value = {}
  try {
    if (!store.zones.length) await store.fetchZones()
    device.value = await store.fetchDevice(route.params.deviceId)
    if (isCamera.value) {
      frames.value = await store.fetchFrames(route.params.deviceId)
    }
  } catch {
    notFound.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  unsubscribeLive = subscribe(onLiveReading)
})
onBeforeUnmount(() => unsubscribeLive?.())
watch(() => route.params.deviceId, load)

async function onZoneChange(event) {
  assigningZone.value = true
  try {
    await store.assignZone(device.value.id, event.target.value)
    device.value = await store.fetchDevice(route.params.deviceId)
  } finally {
    assigningZone.value = false
  }
}

function formatDate(iso) {
  if (!iso) return null
  try {
    return new Date(iso).toLocaleString(locale.value)
  } catch {
    return iso
  }
}

function readingLabel(key) {
  return t(`readings.${key}`, key.replace(/_/g, ' '))
}

const exporting = ref(false)
// Exporting hits the gateway directly (not the JSON store client) because
// the response here is a CSV file, not JSON -- axios still carries the
// same auth header via the shared client, but as a blob so the browser
// can save it rather than trying to parse it as data.
async function exportCsv() {
  exporting.value = true
  try {
    const { data } = await client.get(`/api/v1/devices/${device.value.id}/readings/export`, {
      params: { minutes: activeRangeMinutes.value },
      responseType: 'blob'
    })
    const url = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = url
    link.download = `${device.value.id}-readings.csv`
    link.click()
    URL.revokeObjectURL(url)
  } catch {
    // Leaves the existing chart/page untouched -- a failed export isn't
    // a reason to disrupt anything else on the page.
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <main class="device-detail">
    <RouterLink to="/" class="back-link">&larr; {{ t('dashboard.title') }}</RouterLink>

    <p v-if="loading" class="muted">{{ t('common.loading') }}</p>
    <p v-else-if="notFound" class="error">{{ t('device_detail.not_found') }}</p>

    <template v-else>
      <div class="page-head">
        <div class="dot" :class="device.status"></div>
        <div>
          <h1>{{ device.display_name || device.id }}</h1>
          <p class="subtitle">{{ t(`device_types.${device.device_type}`, device.device_type) }}</p>
        </div>
      </div>

      <section class="card">
        <div class="fact-row">
          <span>{{ t('device_detail.status') }}</span>
          <strong>{{ device.status === 'online' ? t('dashboard.status_online') : t('dashboard.status_offline') }}</strong>
        </div>
        <div class="fact-row" v-if="device.last_seen_at">
          <span>{{ t('dashboard.last_seen') }}</span>
          <strong>{{ formatDate(device.last_seen_at) }}</strong>
        </div>
        <div class="fact-row">
          <span>{{ t('device_detail.zone') }}</span>
          <strong v-if="!canManage">{{ store.zoneName(device.zone_id) || t('dashboard.unassigned_zone') }}</strong>
          <select v-else :value="device.zone_id || ''" @change="onZoneChange" :disabled="assigningZone">
            <option value="" disabled>{{ t('dashboard.unassigned_zone') }}</option>
            <option v-for="z in store.zones" :key="z.id" :value="z.id">{{ z.name }}</option>
          </select>
        </div>
      </section>

      <section v-if="healthReading" class="card health-card" :class="healthTone">
        <h2>{{ t('device_detail.plant_health') }}</h2>
        <p class="note">{{ healthReading.note }}</p>
        <div class="health-meta">
          <span>{{ t('device_detail.health_score') }}: {{ Math.round(healthReading.health_score * 100) }}%</span>
          <span>{{ t('device_detail.confidence') }}: {{ Math.round(healthReading.confidence * 100) }}%</span>
        </div>
      </section>

      <section v-else-if="sensorReading" class="card">
        <h2>{{ t('device_detail.latest_reading') }}</h2>
        <div class="stat-grid">
          <div v-for="(value, key) in sensorReading" :key="key" class="stat-tile">
            <span class="stat-label">{{ readingLabel(key) }}</span>
            <span class="stat-value">{{ value }}</span>
          </div>
        </div>
      </section>

      <section v-if="chartableMetrics.length" class="card">
        <div class="chart-head">
          <h2>{{ t('device_detail.history') }}</h2>
          <div class="range-tabs">
            <button
              v-for="r in ranges" :key="r.minutes"
              class="range-tab" :class="{ active: r.minutes === activeRangeMinutes }"
              @click="activeRangeMinutes = r.minutes"
            >{{ r.label }}</button>
          </div>
        </div>
        <div v-if="!showComboChart" class="metric-tabs">
          <button
            v-for="m in chartableMetrics" :key="m"
            class="metric-tab" :class="{ active: m === activeMetric }"
            @click="activeMetric = m"
          >{{ readingLabel(m) }}</button>
        </div>
        <SensorChart
          v-if="showComboChart"
          :device-id="device.id" :series="comboSeries" :minutes="activeRangeMinutes"
        />
        <SensorChart
          v-else-if="activeMetric"
          :device-id="device.id" :metric="activeMetric" :minutes="activeRangeMinutes" color="#1b5e20"
        />
        <button class="export-btn" :disabled="exporting" @click="exportCsv">
          {{ exporting ? t('device_detail.exporting') : t('device_detail.export_csv') }}
        </button>
      </section>

      <section v-if="isCamera" class="card">
        <div class="chart-head">
          <h2>{{ t('device_detail.live_view') }}</h2>
          <button class="live-toggle" :class="{ active: liveRequested }" @click="toggleLive">
            {{ liveRequested ? t('device_detail.stop_live') : t('device_detail.go_live') }}
          </button>
        </div>
        <img v-if="liveRequested && streamUrl" :src="streamUrl" :alt="t('device_detail.photo_alt')" class="live-stream" />
        <p v-else-if="liveRequested" class="muted">{{ t('dashboard.live_pending') }}</p>
        <p v-else class="muted">{{ t('device_detail.live_view_hint') }}</p>
      </section>

      <section v-if="isCamera" class="card">
        <h2>{{ t('device_detail.photos') }}</h2>
        <p v-if="!frames.length" class="muted">{{ t('device_detail.no_photos') }}</p>
        <div v-else class="photo-grid">
          <a v-for="f in frames" :key="f.id" :href="f.url" target="_blank" rel="noopener" class="photo">
            <img :src="f.url" :alt="t('device_detail.photo_alt')" loading="lazy" />
            <span class="photo-date">{{ formatDate(f.created_at) }}</span>
          </a>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.device-detail { padding: 1.5rem; max-width: 700px; margin: 0 auto; }
.back-link { color: var(--vert-green); text-decoration: none; font-size: 0.9rem; display: inline-block; margin-bottom: 1rem; }
.page-head { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.5rem; }
.subtitle { color: #666; margin: 0.15rem 0 0; }
.dot { width: 14px; height: 14px; border-radius: 50%; background: #bbb; flex-shrink: 0; }
.dot.online { background: #2e7d32; }
.dot.offline { background: #c62828; }
.card { background: white; border-radius: 12px; padding: 1.25rem; margin-bottom: 1.25rem; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
.card h2 { margin-top: 0; font-size: 1rem; color: #1b5e20; }
.fact-row { display: flex; justify-content: space-between; align-items: center; padding: 0.4rem 0; border-bottom: 1px solid #f0f0f0; font-size: 0.9rem; }
.fact-row:last-child { border-bottom: none; }
.fact-row select { padding: 0.3rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.health-card.good { background: #f1f8f2; }
.health-card.warn { background: #fdf3f1; }
.health-card .note { font-size: 1.05rem; margin: 0.25rem 0 0.75rem; }
.health-meta { display: flex; gap: 1.5rem; font-size: 0.8rem; color: #666; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 0.75rem; }
.stat-tile { background: #f7f9f6; border-radius: 8px; padding: 0.6rem 0.75rem; display: flex; flex-direction: column; }
.stat-label { font-size: 0.75rem; color: #777; text-transform: capitalize; }
.stat-value { font-size: 1.1rem; font-weight: 600; }
.photo-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 0.75rem; }
.photo { display: flex; flex-direction: column; gap: 0.25rem; text-decoration: none; color: inherit; }
.photo img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 8px; }
.photo-date { font-size: 0.7rem; color: #888; }
.muted { color: #888; }
.error { color: #c62828; }
.chart-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.5rem; }
.range-tabs, .metric-tabs { display: flex; gap: 0.3rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.range-tab, .metric-tab {
  border: none; background: #f0f4ef; color: #445; font-size: 0.75rem; padding: 0.25rem 0.6rem;
  border-radius: 999px; cursor: pointer;
}
.range-tab.active, .metric-tab.active { background: #1b5e20; color: white; }
.live-toggle {
  border: 1px solid var(--vert-green); background: white; color: var(--vert-green);
  padding: 0.35rem 0.8rem; border-radius: 999px; font-size: 0.8rem; cursor: pointer; font-weight: 600;
}
.live-toggle.active { background: var(--vert-green); color: white; }
.export-btn {
  margin-top: 0.85rem; border: 1px solid #ccc; background: white; color: #445;
  padding: 0.4rem 0.9rem; border-radius: 999px; font-size: 0.8rem; cursor: pointer;
}
.export-btn:hover:not(:disabled) { border-color: var(--vert-green); color: var(--vert-green); }
.export-btn:disabled { opacity: 0.6; cursor: default; }
.live-stream { width: 100%; border-radius: 10px; max-height: 360px; object-fit: cover; }
</style>
