<script setup>
// The greenhouse overview. It opens with a briefing written from live
// readings (what needs you, worst first), then one band per zone.
import { onMounted, onBeforeUnmount, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { useDevicesStore } from '../stores/devices'
import { useAuthStore } from '../stores/auth'
import { useAlertsStore } from '../stores/alerts'
import ZoneBay from '../components/ZoneBay.vue'
import { summariseZone, briefing } from '../lib/agronomy'

const { t } = useI18n()
const store = useDevicesStore()
const auth = useAuthStore()
const alerts = useAlertsStore()

let timer = null
function refresh() {
  store.fetchDevices()
  alerts.fetchAlerts()
}
onMounted(() => {
  store.fetchDevices()
  store.fetchZones()
  timer = setInterval(refresh, 30000)
})
onBeforeUnmount(() => clearInterval(timer))

const canProvision = computed(() => auth.user?.role === 'admin')

const summaries = computed(() => {
  const { groups, unassigned } = store.devicesByZone
  const out = store.zones.filter((z) => groups.has(z.id)).map((z) => summariseZone(z, groups.get(z.id)))
  if (unassigned.length) out.push(summariseZone({ id: 'unassigned', name: t('dashboard.unassigned_zone') }, unassigned))
  return out
})

const brief = computed(() => briefing(summaries.value, alerts.unacknowledgedCount))

const greeting = computed(() => {
  const h = new Date().getHours()
  const part = h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening'
  const name = auth.user?.display_name?.split(' ')[0]
  return t(`overview.greeting_${part}`, { name: name || '' }).replace(/,\s*$/, '')
})
const today = computed(() => new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }))
</script>

<template>
  <main class="overview">
    <p v-if="store.error" class="error" role="alert">{{ store.error }}</p>

    <div v-else-if="!store.loading && store.devices.length === 0" class="empty panel">
      <h1>{{ t('dashboard.no_devices') }}</h1>
      <RouterLink v-if="canProvision" :to="{ name: 'provision' }" class="btn primary">{{ t('dashboard.add_first_device') }}</RouterLink>
    </div>

    <template v-else>
      <section class="briefing" :class="brief.tone" aria-labelledby="brief-head">
        <p class="kicker">{{ greeting }} <span class="muted">{{ today }}</span></p>
        <h1 id="brief-head">{{ t(brief.headlineKey, brief.headlineParams, brief.headlineParams.n) }}</h1>
        <ol v-if="brief.items.length" class="items">
          <li v-for="(item, i) in brief.items" :key="i" :class="item.tone">
            <span class="mark" aria-hidden="true"></span>
            <span class="text">{{ t(item.key, { ...item.params, condition: item.params.condition ? t(`conditions.${item.params.condition}`, item.params.condition) : '' }) }}</span>
            <RouterLink v-if="item.deviceId" class="go" :to="{ name: 'device-detail', params: { deviceId: item.deviceId } }">{{ t('overview.look') }}</RouterLink>
          </li>
        </ol>
        <p v-if="alerts.unacknowledgedCount" class="alerts-line">
          <RouterLink :to="{ name: 'alerts' }">{{ t('overview.open_alerts', alerts.unacknowledgedCount) }}</RouterLink>
        </p>
      </section>

      <ZoneBay v-for="s in summaries" :key="s.zone.id" :summary="s" />
    </template>
  </main>
</template>

<style scoped>
.overview { padding: 2rem clamp(1rem, 3vw, 2.5rem) 3rem; max-width: 1280px; margin: 0 auto; display: flex; flex-direction: column; gap: 1.5rem; }

.briefing { padding: 0.5rem 0 0.5rem; max-width: 62rem; }
.kicker { font-size: 0.92rem; color: var(--ink-2); font-weight: 600; margin-bottom: 0.4rem; }
.kicker .muted { font-weight: 500; margin-left: 0.4rem; }
.briefing h1 {
  font-size: clamp(2rem, 1.3rem + 2.6vw, 3.3rem); font-weight: 700; letter-spacing: -0.035em; line-height: 1.02;
  font-variation-settings: 'wdth' 85; max-width: 18ch; color: var(--canopy);
}
.briefing.serious h1, .briefing.critical h1 { color: var(--ink); }
.items { list-style: none; padding: 0; margin: 1.25rem 0 0; display: flex; flex-direction: column; border-top: 1px solid var(--line); }
.items li { display: flex; align-items: center; gap: 0.85rem; padding: 0.7rem 0; border-bottom: 1px solid var(--line); }
.mark { width: 0.7rem; height: 0.7rem; border-radius: 50%; flex-shrink: 0; }
.items li.serious .mark, .items li.critical .mark { background: var(--serious-mark); }
.items li.warning .mark { background: var(--warning-mark); }
.items li.good .mark { background: var(--good); }
.text { flex: 1; font-size: 1rem; color: var(--ink); max-width: 70ch; }
.go { font-size: 0.82rem; font-weight: 650; color: var(--canopy-2); text-decoration: none; white-space: nowrap; }
.go:hover { text-decoration: underline; }
.alerts-line { margin-top: 0.8rem; font-size: 0.88rem; }
.alerts-line a { color: var(--ink-2); font-weight: 600; }

.empty { display: flex; flex-direction: column; align-items: flex-start; gap: 1rem; padding: 2.5rem; }
.empty h1 { font-size: 1.5rem; }
.error { color: var(--critical); }
</style>
