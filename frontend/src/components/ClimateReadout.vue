<script setup>
// One climate figure judged against the crop's target band: the value,
// its status in words, and the band it should sit in.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { TARGETS, bandStatus } from '../lib/agronomy'

const props = defineProps({
  metric: { type: String, required: true }, // temp_c | humidity_pct | vpd_kpa | moisture_pct
  value: { type: Number, default: null },
  digits: { type: Number, default: 1 },
  unit: { type: String, default: '' }
})
const { t } = useI18n()
const status = computed(() => bandStatus(props.metric, props.value))
const band = computed(() => TARGETS[props.metric]?.ideal)
</script>

<template>
  <div class="readout" :class="status">
    <span class="name">{{ t(`climate.${metric}`) }}</span>
    <span class="value figure">
      <template v-if="value === null">–</template>
      <template v-else>{{ value.toFixed(digits) }}<small>{{ unit }}</small></template>
    </span>
    <span v-if="value !== null" class="status-chip" :class="status">{{ t(`climate.status_${status}`) }}</span>
    <span v-if="band" class="band muted">{{ t('climate.target', { lo: band[0], hi: band[1], unit }) }}</span>
  </div>
</template>

<style scoped>
.readout { display: flex; flex-direction: column; gap: 0.2rem; min-width: 0; }
.name { font-size: 0.78rem; color: var(--ink-2); font-weight: 600; }
.value { font-size: 1.75rem; line-height: 1.05; color: var(--ink); }
.value small { font-size: 0.9rem; font-weight: 600; color: var(--ink-2); margin-left: 0.1rem; }
.status-chip { align-self: flex-start; }
.band { font-size: 0.72rem; }
</style>
