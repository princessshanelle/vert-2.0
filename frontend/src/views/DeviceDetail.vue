<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useDevicesStore } from '../stores/devices'
import { useAuthStore } from '../stores/auth'
import SensorChart from '../components/SensorChart.vue'
import PlantHealthPanel from '../components/PlantHealthPanel.vue'
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

// Temperature and humidity are read together (it's the pair that says
// anything about mould risk or VPD), so a device with both shows them as
// two aligned charts, one above the other, sharing the time range. Not
// one chart with two y-axes: the alignment of two scales is arbitrary and
// invents correlations that aren't in the data.
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

      <PlantHealthPanel v-if="healthReading" :reading="healthReading" class="card" />

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
        <div v-if="showComboChart" class="stacked">
          <p class="stack-label">{{ readingLabel('temp_c') }}</p>
          <SensorChart :device-id="device.id" metric="temp_c" :minutes="activeRangeMinutes" color="#eb6834" />
          <p class="stack-label">{{ readingLabel('humidity_pct') }}</p>
          <SensorChart :device-id="device.id" metric="humidity_pct" :minutes="activeRangeMinutes" color="#2a78d6" />
        </div>
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
.device-detail { padding: 2rem clamp(1rem, 3vw, 2.5rem) 3rem; max-width: 980px; margin: 0 auto; }
.back-link { color: var(--canopy-2); text-decoration: none; font-size: 0.9rem; font-weight: 600; display: inline-block; margin-bottom: 1rem; }
.page-head { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.5rem; }
.subtitle { color: var(--ink-2); margin: 0.15rem 0 0; }
.dot { width: 14px; height: 14px; border-radius: 50%; background: var(--ink-3); flex-shrink: 0; }
.dot.online { background: var(--good); }
.dot.offline { background: transparent; box-shadow: inset 0 0 0 2px var(--critical); }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-l); padding: 1.25rem 1.4rem; margin-bottom: 1.25rem; box-shadow: var(--shadow); }
.card h2 { margin: 0 0 0.6rem; font-size: 1.1rem; }
.fact-row { display: flex; justify-content: space-between; align-items: center; padding: 0.5rem 0; border-bottom: 1px solid var(--line); font-size: 0.9rem; color: var(--ink-2); }
.fact-row strong { color: var(--ink); }
.fact-row:last-child { border-bottom: none; }
.fact-row select { padding: 0.35rem 0.6rem; border-radius: 999px; border: 1px solid var(--line-strong); background: var(--surface); }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 0.75rem; }
.stat-tile { background: var(--surface-sunk); border-radius: var(--radius-m); padding: 0.7rem 0.85rem; display: flex; flex-direction: column; }
.stat-label { font-size: 0.75rem; color: var(--ink-3); font-weight: 600; }
.stat-value { font-family: var(--font-display); font-size: 1.35rem; font-weight: 650; color: var(--ink); }
.photo-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0.75rem; }
.photo { display: flex; flex-direction: column; gap: 0.25rem; text-decoration: none; color: inherit; }
.photo img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius-m); }
.photo-date { font-size: 0.72rem; color: var(--ink-3); }
.muted { color: var(--ink-3); }
.error { color: var(--critical); }
.chart-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.5rem; }
.chart-head h2 { margin: 0; }
.range-tabs, .metric-tabs { display: flex; gap: 0.3rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.range-tab, .metric-tab {
  border: 1px solid var(--line); background: var(--surface-sunk); color: var(--ink-2); font-size: 0.75rem; padding: 0.25rem 0.65rem;
  border-radius: 999px; cursor: pointer; font-weight: 600;
}
.range-tab.active, .metric-tab.active { background: var(--canopy); border-color: var(--canopy); color: var(--ink-inverse); }
.stacked { display: flex; flex-direction: column; gap: 0.25rem; }
.stack-label { font-size: 0.8rem; font-weight: 650; color: var(--ink-2); margin-top: 0.4rem; }
.live-toggle {
  border: 1px solid var(--canopy-2); background: var(--surface); color: var(--canopy-2);
  padding: 0.35rem 0.85rem; border-radius: 999px; font-size: 0.8rem; cursor: pointer; font-weight: 650;
}
.live-toggle.active { background: var(--canopy); color: var(--ink-inverse); }
.export-btn {
  margin-top: 0.85rem; border: 1px solid var(--line-strong); background: var(--surface); color: var(--ink-2);
  padding: 0.4rem 0.95rem; border-radius: 999px; font-size: 0.8rem; cursor: pointer; font-weight: 600;
}
.export-btn:hover:not(:disabled) { border-color: var(--canopy-2); color: var(--canopy-2); }
.export-btn:disabled { opacity: 0.6; cursor: default; }
.live-stream { width: 100%; border-radius: var(--radius-m); max-height: 420px; object-fit: cover; }
</style>
