<script setup>
// One zone of the greenhouse as a single band: plants (health + what the
// camera found), climate against the strawberry targets, disease
// pressure, harvest, and the latest photo. Reads only devices' latest
// readings plus one 24 h history for the climate trend.
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import HealthDial from './HealthDial.vue'
import ClimateReadout from './ClimateReadout.vue'
import RiskMeter from './RiskMeter.vue'
import RipenessStrip from './RipenessStrip.vue'
import TrendChart from './charts/TrendChart.vue'
import { TARGETS, HEALTH_STATUS, bandStatus, riskStatus } from '../lib/agronomy'
import { fetchSeries } from '../lib/series'
import { useDevicesStore } from '../stores/devices'

const props = defineProps({ summary: { type: Object, required: true } })
const { t } = useI18n()
const store = useDevicesStore()

const s = computed(() => props.summary)
// The zone's word is its worst signal: findings, disease pressure,
// climate against target, plant health, and devices gone quiet.
const RANK = { neutral: 0, good: 1, warning: 2, serious: 3 }
const zoneStatus = computed(() => {
  const c = s.value.climate
  const signals = [
    ...s.value.findings.map((f) => (f.status === 'detected' ? 'serious' : 'warning')),
    ...s.value.risks.map((r) => riskStatus(r.v)),
    c ? bandStatus('temp_c', c.temp) : 'neutral',
    c ? bandStatus('humidity_pct', c.rh) : 'neutral',
    c?.vpd !== null && c?.vpd !== undefined ? bandStatus('vpd_kpa', c.vpd) : 'neutral',
    s.value.moisture !== null ? bandStatus('moisture_pct', s.value.moisture) : 'neutral',
    s.value.plant ? HEALTH_STATUS[s.value.plant.label] || 'neutral' : 'neutral',
    s.value.offline.length ? 'warning' : 'neutral'
  ]
  const worst = signals.reduce((a, b) => (RANK[b] > RANK[a] ? b : a), 'neutral')
  return worst === 'neutral' && s.value.online ? 'good' : worst
})
const hasSide = computed(() => s.value.risks.length || s.value.ripeness || photo.value)

const humidity24h = ref([])
const photo = ref(null)
let timer = null

async function loadTrend() {
  const dev = s.value.climateDevice
  if (!dev) return
  try {
    humidity24h.value = await fetchSeries(dev.id, 'humidity_pct', 1440)
  } catch {
    // keep the last trend on screen
  }
}
async function loadPhoto() {
  const cam = s.value.camera
  if (!cam) return
  try {
    const frames = await store.fetchFrames(cam.id)
    photo.value = frames?.[0] || null
  } catch {
    photo.value = null
  }
}
onMounted(() => {
  loadTrend()
  loadPhoto()
  timer = setInterval(loadTrend, 120000)
})
onBeforeUnmount(() => clearInterval(timer))
watch(() => s.value.climateDevice?.id, loadTrend)

function photoTime(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}
</script>

<template>
  <section class="bay panel" :aria-labelledby="`zone-${s.zone.id}`">
    <header class="bay-head">
      <h2 :id="`zone-${s.zone.id}`">{{ s.zone.name }}</h2>
      <span class="status-chip" :class="zoneStatus">{{ t(`zone_status.${zoneStatus}`) }}</span>
      <span class="devices muted">{{ t('overview.devices_online', { n: s.online, total: s.devices.length }) }}</span>
      <RouterLink class="btn analytics-link" :to="{ name: 'analytics', query: { zone: s.zone.id } }">{{ t('overview.open_analytics') }}</RouterLink>
    </header>

    <div class="bay-grid" :class="{ 'no-side': !hasSide }">
      <div class="plants">
        <template v-if="s.plant">
          <RouterLink :to="{ name: 'device-detail', params: { deviceId: s.camera.id } }" class="dial-link" :aria-label="t('overview.plant_detail')">
            <HealthDial :score="s.plant.health_score" :label="s.plant.label" />
          </RouterLink>
          <div class="plant-text">
            <h3>{{ t('overview.plants') }}</h3>
            <ul v-if="s.findings.length" class="findings">
              <li v-for="f in s.findings" :key="f.id">
                <span class="status-chip" :class="f.status === 'detected' ? 'serious' : 'warning'">{{ t(`finding.${f.status}`) }}</span>
                <span class="finding-name">{{ t(`conditions.${f.id}`, f.id) }}</span>
              </li>
            </ul>
            <p v-else class="calm">{{ t('overview.no_findings') }}</p>
          </div>
        </template>
        <div v-else class="empty-slot">
          <h3>{{ t('overview.plants') }}</h3>
          <p class="muted">{{ t('overview.no_camera') }}</p>
        </div>
      </div>

      <div class="climate">
        <h3>{{ t('overview.climate') }}</h3>
        <div v-if="s.climate" class="readouts">
          <ClimateReadout metric="temp_c" :value="s.climate.temp" unit="°C" />
          <ClimateReadout metric="humidity_pct" :value="s.climate.rh" unit="%" :digits="0" />
          <ClimateReadout metric="vpd_kpa" :value="s.climate.vpd" unit=" kPa" :digits="2" />
          <ClimateReadout v-if="s.moisture !== null" metric="moisture_pct" :value="s.moisture" unit="%" :digits="0" />
        </div>
        <p v-else class="muted">{{ t('overview.no_climate') }}</p>
        <div v-if="humidity24h.length > 2" class="trend">
          <span class="trend-label muted">{{ t('overview.humidity_24h') }}</span>
          <TrendChart :points="humidity24h" color-var="--series-humidity" unit="%" :digits="0"
                      :target="TARGETS.humidity_pct.ideal" :height="84" :label="t('overview.humidity_24h')" />
        </div>
      </div>

      <div v-if="hasSide" class="side">
        <div v-if="s.risks.length" class="risks">
          <h3>{{ t('overview.disease_pressure') }}</h3>
          <RiskMeter v-for="r in s.risks" :key="r.id" :id="r.id" :value="r.v" :hours="r.hours" />
        </div>
        <div v-if="s.ripeness" class="harvest">
          <h3>{{ t('overview.harvest') }}</h3>
          <RipenessStrip :ripeness="s.ripeness" />
        </div>
        <RouterLink v-if="photo" :to="{ name: 'device-detail', params: { deviceId: s.camera.id } }" class="photo">
          <img :src="photo.url" :alt="t('overview.latest_photo', { zone: s.zone.name })" loading="lazy" />
          <span>{{ photoTime(photo.created_at) }}</span>
        </RouterLink>
      </div>
    </div>

    <footer class="device-strip">
      <RouterLink v-for="d in s.devices" :key="d.id" :to="{ name: 'device-detail', params: { deviceId: d.id } }"
                  class="device-pill" :class="d.status">
        <span class="dot" aria-hidden="true"></span>
        <span class="pill-name">{{ d.display_name || d.id }}</span>
        <span class="visually-hidden">{{ d.status === 'online' ? t('dashboard.status_online') : t('dashboard.status_offline') }}</span>
      </RouterLink>
    </footer>
  </section>
</template>

<style scoped>
.bay { display: flex; flex-direction: column; gap: 1.1rem; padding: 1.35rem 1.5rem; }
.bay-head { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.bay-head h2 { font-size: 1.45rem; }
.devices { font-size: 0.8rem; }
.analytics-link { margin-left: auto; }

.bay-grid {
  display: grid; gap: 1.5rem;
  grid-template-columns: minmax(230px, 1fr) minmax(300px, 1.5fr) minmax(240px, 1fr);
  align-items: start;
}
.bay-grid.no-side { grid-template-columns: minmax(230px, 1fr) minmax(300px, 2.5fr); }
.bay-grid.no-side .climate { border-right: 0; }
.bay-grid h3 { font-size: 0.85rem; color: var(--ink-2); font-family: var(--font-body); font-weight: 650; margin-bottom: 0.6rem; }

.plants { display: flex; gap: 1rem; align-items: center; }
.dial-link { border-radius: 50%; text-decoration: none; }
.plant-text { min-width: 0; }
.findings { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.45rem; }
.findings li { display: flex; flex-direction: column; align-items: flex-start; gap: 0.2rem; }
.finding-name { font-weight: 650; font-size: 0.95rem; }
.calm { color: var(--ink-2); font-size: 0.9rem; }

.climate { border-left: 1px solid var(--line); border-right: 1px solid var(--line); padding: 0 1.5rem; }
.readouts { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 0.9rem; }
.trend { margin-top: 1rem; }
.trend-label { font-size: 0.74rem; display: block; margin-bottom: 0.2rem; }

.side { display: flex; flex-direction: column; gap: 1.1rem; }
.risks { display: flex; flex-direction: column; gap: 0.65rem; }
.risks h3 { margin-bottom: 0; }
.photo { position: relative; display: block; border-radius: var(--radius-m); overflow: hidden; }
.photo img { display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: cover; }
.photo span {
  position: absolute; left: 0.5rem; bottom: 0.5rem; font-size: 0.72rem; color: #fff;
  background: rgba(0, 0, 0, 0.55); padding: 0.1rem 0.45rem; border-radius: 999px;
}

.device-strip { display: flex; flex-wrap: wrap; gap: 0.4rem; border-top: 1px solid var(--line); padding-top: 0.9rem; }
.device-pill {
  display: inline-flex; align-items: center; gap: 0.4rem; text-decoration: none; color: var(--ink-2);
  font-size: 0.78rem; padding: 0.22rem 0.65rem; border-radius: 999px; background: var(--surface-sunk); border: 1px solid var(--line);
}
.device-pill:hover { border-color: var(--canopy-2); color: var(--ink); }
.dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--ink-3); }
.device-pill.online .dot { background: var(--good); }
.device-pill.offline { color: var(--ink-3); }
.device-pill.offline .dot { background: transparent; box-shadow: inset 0 0 0 1.5px var(--critical); }

@media (max-width: 1080px) {
  .bay-grid { grid-template-columns: 1fr 1fr; }
  .climate { border-right: 0; padding-right: 0; }
  .side { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
}
@media (max-width: 680px) {
  .bay { padding: 1.1rem; }
  .bay-grid, .bay-grid.no-side { grid-template-columns: 1fr; }
  .climate { border: 0; border-top: 1px solid var(--line); padding: 1rem 0 0; }
  .analytics-link { margin-left: 0; }
}
</style>
