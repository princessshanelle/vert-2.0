<script setup>
// Fruit in view by ripeness, left (green) to right (overripe), each
// segment the colour of the fruit itself, 2px gaps between them. Counts
// sit in the legend, never inside a segment that might be too narrow.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({ ripeness: { type: Object, required: true } }) // { parts, total, modelled }
const { t } = useI18n()
const visible = computed(() => props.ripeness.parts.filter((p) => p.n > 0))
const ready = computed(() => props.ripeness.parts.filter((p) => p.id === 'ripe' || p.id === 'over').reduce((a, p) => a + p.n, 0))
</script>

<template>
  <div class="ripeness">
    <div class="head">
      <span class="ready figure">{{ ready }}</span>
      <span class="ready-label">{{ t('harvest.ready', ready) }}</span>
      <span class="total muted">{{ t('harvest.of_total', { n: ripeness.total }) }}</span>
    </div>
    <div class="strip" role="img" :aria-label="ripeness.parts.map((p) => `${t(`harvest.stage.${p.id}`)} ${p.n}`).join(', ')">
      <span v-for="p in visible" :key="p.id" class="seg" :class="p.id" :style="{ flexGrow: p.n }"></span>
    </div>
    <ul class="legend">
      <li v-for="p in ripeness.parts" :key="p.id" :class="{ zero: !p.n }">
        <span class="swatch" :class="p.id"></span>{{ t(`harvest.stage.${p.id}`) }} <strong class="tabular">{{ p.n }}</strong>
      </li>
    </ul>
    <p v-if="!ripeness.modelled" class="basis muted">{{ t('harvest.colour_estimate') }}</p>
  </div>
</template>

<style scoped>
.ripeness { display: flex; flex-direction: column; gap: 0.5rem; }
.head { display: flex; align-items: baseline; gap: 0.4rem; flex-wrap: wrap; }
.ready { font-size: 1.9rem; color: var(--ink); line-height: 1; }
.ready-label { font-weight: 650; color: var(--ink); }
.total { font-size: 0.8rem; margin-left: auto; }
.strip { display: flex; gap: 2px; height: 14px; border-radius: 999px; overflow: hidden; background: var(--surface-sunk); }
.seg { flex-basis: 0; min-width: 6px; }
.green { background: var(--ripe-green); }
.white { background: var(--ripe-white); }
.turning { background: var(--ripe-turning); }
.ripe { background: var(--ripe-ripe); }
.over { background: var(--ripe-over); }
.legend { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0.25rem 0.85rem; font-size: 0.76rem; color: var(--ink-2); }
.legend li { display: inline-flex; align-items: center; gap: 0.3rem; }
.legend li.zero { opacity: 0.55; }
.legend strong { color: var(--ink); }
.swatch { width: 0.65rem; height: 0.65rem; border-radius: 3px; display: inline-block; }
.basis { font-size: 0.72rem; }
</style>
