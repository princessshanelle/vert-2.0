<script setup>
// Plant health as a ring: the arc is health_score (0-1), coloured by the
// status plant-cam assigned, with the score and its word in the middle.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { HEALTH_STATUS } from '../lib/agronomy'

const props = defineProps({
  score: { type: Number, default: null },
  label: { type: String, default: '' },
  size: { type: Number, default: 132 }
})
const { t } = useI18n()
const R = 52
const C = 2 * Math.PI * R
const status = computed(() => HEALTH_STATUS[props.label] || 'neutral')
const pct = computed(() => (props.score === null ? 0 : Math.round(Math.max(0, Math.min(1, props.score)) * 100)))
const color = computed(() => ({ good: 'var(--good)', warning: 'var(--warning-mark)', serious: 'var(--serious-mark)' })[status.value] || 'var(--ink-3)')
</script>

<template>
  <figure class="dial" :style="{ width: size + 'px' }">
    <svg :width="size" :height="size" viewBox="0 0 120 120" role="img"
         :aria-label="t('health.dial_aria', { pct, label: t(`health.labels.${label}`, label) })">
      <circle cx="60" cy="60" :r="R" class="track" />
      <circle cx="60" cy="60" :r="R" class="arc" :stroke="color"
              :stroke-dasharray="`${(C * pct) / 100} ${C}`" transform="rotate(-90 60 60)" />
    </svg>
    <figcaption>
      <span class="pct figure">{{ score === null ? '–' : pct }}</span>
      <span class="word">{{ t(`health.labels.${label}`, label || '') }}</span>
    </figcaption>
  </figure>
</template>

<style scoped>
.dial { position: relative; margin: 0; flex-shrink: 0; }
svg { display: block; }
.track { fill: none; stroke: var(--surface-sunk); stroke-width: 9; }
.arc { fill: none; stroke-width: 9; stroke-linecap: round; transition: stroke-dasharray 0.6s ease; }
figcaption {
  position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; line-height: 1.05;
}
.pct { font-size: 2.1rem; color: var(--ink); }
.word { font-size: 0.75rem; color: var(--ink-2); font-weight: 600; margin-top: 0.15rem; max-width: 80%; }
</style>
