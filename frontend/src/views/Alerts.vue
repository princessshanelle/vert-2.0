<script setup>
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAlertsStore } from '../stores/alerts'

const { t, locale } = useI18n()
const store = useAlertsStore()

onMounted(() => store.fetchAlerts())

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleString(locale.value)
  } catch {
    return iso
  }
}
</script>

<template>
  <main class="alerts">
    <div class="page-head">
      <h1>{{ t('alerts.title') }}</h1>
      <p class="subtitle">{{ t('alerts.subtitle') }}</p>
    </div>

    <p v-if="store.error" class="error">{{ store.error }}</p>

    <div v-if="!store.loading && store.alerts.length === 0" class="empty-state">
      <p>{{ t('alerts.all_quiet') }}</p>
    </div>

    <ul v-else class="alert-list">
      <li v-for="a in store.alerts" :key="a.id" class="alert" :class="[a.severity, { acknowledged: a.acknowledged }]">
        <div class="alert-body">
          <p class="message">{{ a.message }}</p>
          <span class="meta">{{ a.device_id }} &middot; {{ formatDate(a.created_at) }}</span>
        </div>
        <button v-if="!a.acknowledged" @click="store.acknowledge(a.id)">{{ t('alerts.acknowledge') }}</button>
        <span v-else class="seen">{{ t('alerts.seen') }}</span>
      </li>
    </ul>
  </main>
</template>

<style scoped>
.alerts { padding: 1.5rem; max-width: 700px; margin: 0 auto; }
.page-head { margin-bottom: 1.25rem; }
.subtitle { color: var(--ink-2); margin-top: 0.25rem; }
.empty-state { text-align: center; padding: 3rem 1rem; background: var(--surface); border-radius: 12px; color: var(--ink-2); }
.alert-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.alert { display: flex; justify-content: space-between; align-items: center; gap: 1rem; background: var(--surface); padding: 0.9rem 1.1rem; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); border-left: 4px solid #999; }
.alert.warning { border-left-color: #f2a63c; }
.alert.critical { border-left-color: var(--critical); }
.alert.info { border-left-color: var(--good); }
.alert.acknowledged { opacity: 0.55; }
.alert-body { display: flex; flex-direction: column; gap: 0.2rem; }
.message { margin: 0; }
.meta { font-size: 0.75rem; color: var(--ink-3); }
button { padding: 0.4rem 0.9rem; background: var(--canopy); color: var(--ink-inverse); border: none; border-radius: 8px; cursor: pointer; font-size: 0.85rem; flex-shrink: 0; }
.seen { font-size: 0.8rem; color: var(--ink-3); flex-shrink: 0; }
.error { color: var(--critical); }
</style>
