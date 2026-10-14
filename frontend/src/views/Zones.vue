<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { useDevicesStore } from '../stores/devices'

const { t } = useI18n()
const store = useDevicesStore()

const newZoneName = ref('')
const creating = ref(false)
const error = ref('')

onMounted(() => {
  store.fetchZones()
  store.fetchDevices()
})

const deviceCountByZone = computed(() => {
  const counts = new Map()
  for (const d of store.devices) {
    if (!d.zone_id) continue
    counts.set(d.zone_id, (counts.get(d.zone_id) || 0) + 1)
  }
  return counts
})

async function createZone() {
  error.value = ''
  const name = newZoneName.value.trim()
  if (!name) return
  creating.value = true
  try {
    await store.createZone(name)
    newZoneName.value = ''
  } catch (err) {
    error.value = err.response?.data?.error || t('zones.create_failed')
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <main class="zones">
    <div class="page-head">
      <h1>{{ t('zones.title') }}</h1>
      <p class="subtitle">{{ t('zones.subtitle') }}</p>
    </div>

    <form class="new-zone" @submit.prevent="createZone">
      <input v-model="newZoneName" :placeholder="t('zones.name_placeholder')" />
      <button type="submit" :disabled="creating">{{ t('zones.create') }}</button>
    </form>
    <p v-if="error" class="error">{{ error }}</p>

    <p v-if="!store.zones.length" class="empty">{{ t('zones.no_zones') }}</p>
    <ul v-else class="zone-list">
      <li v-for="z in store.zones" :key="z.id">
        <span class="zone-name">{{ z.name }}</span>
        <span class="zone-count">{{ t('zones.device_count', { count: deviceCountByZone.get(z.id) || 0 }) }}</span>
      </li>
    </ul>

    <p class="hint">
      <RouterLink to="/">{{ t('zones.assign_hint') }}</RouterLink>
    </p>
  </main>
</template>

<style scoped>
.zones { padding: 1.5rem; max-width: 600px; margin: 0 auto; }
.page-head { margin-bottom: 1.25rem; }
.subtitle { color: var(--ink-2); margin-top: 0.25rem; }
.new-zone { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.new-zone input { flex: 1; padding: 0.6rem; border: 1px solid var(--line-strong); border-radius: 8px; }
.new-zone button { padding: 0.6rem 1.1rem; background: var(--vert-green); color: white; border: none; border-radius: 8px; cursor: pointer; }
.zone-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.zone-list li { display: flex; justify-content: space-between; background: var(--surface); padding: 0.85rem 1rem; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
.zone-name { font-weight: 600; }
.zone-count { color: var(--ink-3); font-size: 0.85rem; }
.empty { color: var(--ink-2); }
.error { color: var(--critical); margin-bottom: 0.75rem; }
.hint { margin-top: 1.25rem; font-size: 0.85rem; }
.hint a { color: var(--vert-green); }
</style>
