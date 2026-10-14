<script setup>
// A disease-pressure index (0-1) from plant-cam's environmental model:
// the fill carries severity, the word next to it says it without colour.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { riskStatus } from '../lib/agronomy'

const props = defineProps({
  id: { type: String, required: true },
  value: { type: Number, required: true },
  hours: { type: Number, default: null }
})
const { t } = useI18n()
const status = computed(() => riskStatus(props.value))
const pct = computed(() => Math.round(props.value * 100))
</script>

<template>
  <div class="risk" :class="status">
    <div class="row">
      <span class="name">{{ t(`risk.${id}`) }}</span>
      <span class="level">{{ t(`risk.level_${status}`) }}</span>
    </div>
    <div class="track" role="meter" :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100" :aria-label="t(`risk.${id}`)">
      <div class="fill" :style="{ width: Math.max(pct, 3) + '%' }"></div>
    </div>
    <p v-if="hours" class="hours">{{ t('risk.hours', { h: Math.round(hours) }) }}</p>
  </div>
</template>

<style scoped>
.risk { display: flex; flex-direction: column; gap: 0.3rem; }
.row { display: flex; justify-content: space-between; gap: 0.5rem; font-size: 0.82rem; }
.name { color: var(--ink-2); font-weight: 600; }
.level { color: var(--ink); font-weight: 650; }
.track { height: 8px; border-radius: 999px; background: var(--surface-sunk); overflow: hidden; }
.fill { height: 100%; border-radius: 999px; transition: width 0.5s ease; }
.good .fill { background: var(--good); }
.warning .fill { background: var(--warning-mark); }
.serious .fill { background: var(--serious-mark); }
.hours { font-size: 0.75rem; color: var(--ink-3); }
</style>
