<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { useDevicesStore } from '../stores/devices'
import SensorChart from './SensorChart.vue'
import { subscribe } from '../api/liveSocket'

const { t, locale } = useI18n()
const store = useDevicesStore()
const props = defineProps({ device: { type: Object, required: true } })

const typeLabel = computed(() =>
  t(`device_types.${props.device.device_type}`, props.device.device_type)
)

const lastSeenLabel = computed(() => {
  if (!props.device.last_seen_at) return null
  try {
    return new Date(props.device.last_seen_at).toLocaleString(locale.value)
  } catch {
    return props.device.last_seen_at
  }
})

// ai-camera: the Raspberry Pi plant-cam (edge/plant-cam) -- same live
// view and photo gallery as the ESP32 camera, analysis done on-device.
const isCamera = computed(() => ['camera', 'ai-camera'].includes(props.device.device_type))

// Anything numeric in the last reading is a candidate chart tab -- a
// health-score reading from the AI model and a temp_c reading from a
// physical sensor both qualify equally, no per-device-type special
// casing needed.
const chartableMetrics = computed(() => {
  const reading = props.device.last_reading || {}
  return Object.keys(reading).filter((k) => typeof reading[k] === 'number')
})
const activeMetric = ref(null)
watch(chartableMetrics, (metrics) => {
  if (!activeMetric.value && metrics.length) activeMetric.value = metrics[0]
}, { immediate: true })

function selectMetric(metric, event) {
  event.preventDefault()
  event.stopPropagation()
  activeMetric.value = metric
}

// device.last_reading is a snapshot from whenever the devices list was
// last fetched -- it never moves on its own. liveOverlay is what makes
// the number next to the metric tabs actually track the moving chart
// below it, instead of sitting frozen at page-load value forever.
const liveOverlay = ref({})
let unsubscribeLive = null
onMounted(() => {
  unsubscribeLive = subscribe((msg) => {
    if (msg.device_id !== props.device.id) return
    liveOverlay.value = { ...liveOverlay.value, [msg.metric]: msg.v }
  })
})
onBeforeUnmount(() => unsubscribeLive?.())

const currentValue = computed(() => {
  if (!activeMetric.value) return null
  return liveOverlay.value[activeMetric.value] ?? props.device.last_reading?.[activeMetric.value]
})

// Live preview state is per-card, per-session -- purely a UI toggle
// today. The command still goes out over the same channel a real
// firmware update will listen on (see docs/TESTING.md), so turning this
// on is forward-compatible with nothing else changing here later.
const liveRequested = ref(false)
const streamUrl = computed(() => {
  const ip = props.device.local_ip
  return ip ? `http://${ip}:81/stream` : null
})

async function toggleLive(event) {
  event.preventDefault()
  event.stopPropagation()
  liveRequested.value = !liveRequested.value
  try {
    await store.sendCommand(props.device.id, liveRequested.value ? 'start_stream' : 'stop_stream')
  } catch {
    // The toggle still reflects what the user asked for even if the
    // command couldn't be sent right now (device offline, broker down)
    // -- better than silently reverting a click with no explanation.
  }
}
</script>

<template>
  <RouterLink :to="{ name: 'device-detail', params: { deviceId: device.id } }" class="device-card" :class="device.status">
    <div class="card-head">
      <div class="dot" :class="device.status"></div>
      <div class="body">
        <strong>{{ device.display_name || device.id }}</strong>
        <span class="type">{{ typeLabel }}</span>
      </div>
      <button v-if="isCamera" class="eye-btn" :class="{ active: liveRequested }" @click="toggleLive" :title="t('dashboard.toggle_live')">
        {{ liveRequested ? '\u{1F441}\u{FE0F}' : '\u{1F441}' }}
      </button>
    </div>

    <div v-if="isCamera" class="camera-preview">
      <img v-if="liveRequested && streamUrl" :src="streamUrl" :alt="t('device_detail.photo_alt')" class="live-img" />
      <p v-else-if="liveRequested" class="live-pending">{{ t('dashboard.live_pending') }}</p>
      <p v-else class="live-hint">{{ t('dashboard.tap_eye') }}</p>
    </div>

    <div v-else-if="chartableMetrics.length" class="sensor-preview">
      <div class="metric-row">
        <div class="metric-tabs">
          <button
            v-for="m in chartableMetrics" :key="m"
            class="metric-tab" :class="{ active: m === activeMetric }"
            @click="selectMetric(m, $event)"
          >{{ t(`readings.${m}`, m) }}</button>
        </div>
        <span class="metric-value">{{ currentValue }}</span>
      </div>
      <SensorChart v-if="activeMetric" :device-id="device.id" :metric="activeMetric" :minutes="30" compact color="#2e7d32" />
    </div>

    <span class="status">
      {{ device.status === 'online' ? t('dashboard.status_online') : t('dashboard.status_offline') }}
      <template v-if="lastSeenLabel"> &middot; {{ t('dashboard.last_seen') }} {{ lastSeenLabel }}</template>
    </span>
  </RouterLink>
</template>

<style scoped>
.device-card {
  display: flex; flex-direction: column; gap: 0.6rem; background: white; padding: 1rem;
  border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  text-decoration: none; color: inherit; transition: box-shadow 0.15s, transform 0.15s;
}
.device-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.1); transform: translateY(-1px); }
.card-head { display: flex; align-items: center; gap: 0.65rem; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #bbb; flex-shrink: 0; }
.dot.online { background: #2e7d32; }
.dot.offline { background: #c62828; }
.body { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.body strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.type { font-size: 0.8rem; color: #666; }
.status { font-size: 0.75rem; color: #888; }

.eye-btn {
  border: none; background: #f0f4ef; border-radius: 50%; width: 34px; height: 34px;
  font-size: 1rem; cursor: pointer; flex-shrink: 0; line-height: 1;
}
.eye-btn.active { background: #dff0de; }

.camera-preview { min-height: 40px; display: flex; align-items: center; justify-content: center; }
.live-img { width: 100%; border-radius: 8px; max-height: 160px; object-fit: cover; }
.live-hint, .live-pending { font-size: 0.78rem; color: #999; margin: 0; }

.sensor-preview { display: flex; flex-direction: column; gap: 0.35rem; }
.metric-row { display: flex; align-items: baseline; justify-content: space-between; gap: 0.5rem; }
.metric-tabs { display: flex; gap: 0.3rem; flex-wrap: wrap; }
.metric-tab {
  border: none; background: #f0f4ef; color: #445; font-size: 0.7rem; padding: 0.2rem 0.5rem;
  border-radius: 999px; cursor: pointer;
}
.metric-tab.active { background: #1b5e20; color: white; }
.metric-value { font-size: 1.1rem; font-weight: 700; color: #1b5e20; flex-shrink: 0; }
</style>
