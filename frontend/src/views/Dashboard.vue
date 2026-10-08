<script setup>
import { onMounted, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { useDevicesStore } from '../stores/devices'
import { useAuthStore } from '../stores/auth'
import DeviceCard from '../components/DeviceCard.vue'

const { t } = useI18n()
const store = useDevicesStore()
const auth = useAuthStore()

onMounted(() => {
  store.fetchDevices()
  store.fetchZones()
})

const canProvision = computed(() => auth.user?.role === 'admin')

const zoneSections = computed(() => {
  const { groups, unassigned } = store.devicesByZone
  const sections = store.zones
    .filter((z) => groups.has(z.id))
    .map((z) => ({ id: z.id, name: z.name, devices: groups.get(z.id) }))
  if (unassigned.length) {
    sections.push({ id: null, name: t('dashboard.unassigned_zone'), devices: unassigned })
  }
  return sections
})
</script>

<template>
  <main class="dashboard">
    <div class="page-head">
      <h1>{{ t('dashboard.title') }}</h1>
      <p class="subtitle">{{ t('dashboard.subtitle') }}</p>
    </div>

    <p v-if="store.error" class="error">{{ store.error }}</p>

    <div v-else-if="!store.loading && store.devices.length === 0" class="empty-state">
      <p>{{ t('dashboard.no_devices') }}</p>
      <RouterLink v-if="canProvision" :to="{ name: 'provision' }" class="cta">
        {{ t('dashboard.add_first_device') }}
      </RouterLink>
    </div>

    <section v-for="section in zoneSections" :key="section.id || 'unassigned'" class="zone-section">
      <h2>{{ section.name }}</h2>
      <div class="grid">
        <DeviceCard v-for="d in section.devices" :key="d.id" :device="d" />
      </div>
    </section>
  </main>
</template>

<style scoped>
.dashboard { padding: 1.5rem; max-width: 1000px; margin: 0 auto; }
.page-head { margin-bottom: 1.5rem; }
.subtitle { color: #666; margin-top: 0.25rem; }
.zone-section { margin-bottom: 2rem; }
.zone-section h2 { font-size: 1.1rem; color: #1b5e20; margin-bottom: 0.75rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 1rem; }
.empty-state {
  text-align: center; padding: 3rem 1rem; background: white; border-radius: 12px;
  color: #555; display: flex; flex-direction: column; gap: 1rem; align-items: center;
}
.cta {
  display: inline-block; background: var(--vert-green); color: white; padding: 0.6rem 1.2rem;
  border-radius: 8px; text-decoration: none; font-weight: 600;
}
.error { color: #c62828; margin-bottom: 1rem; }
</style>
