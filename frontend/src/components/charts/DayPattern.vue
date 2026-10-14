<script setup>
// When in the day does it happen? Days down, hours across, each cell the
// hour's average on a one-hue ramp (light = low on a light page; on a
// dark page low values recede into the dark instead).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { themeVersion } from '../../lib/theme'

const props = defineProps({
  points: { type: Array, required: true }, // [{ t: Date, v }]
  unit: { type: String, default: '' },
  label: { type: String, default: '' }
})
const { t } = useI18n()
const RAMP = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']

const isDark = computed(() => {
  themeVersion.value // re-evaluate on theme change
  return getComputedStyle(document.documentElement).colorScheme === 'dark'
})

const grid = computed(() => {
  const cells = new Map()
  for (const p of props.points) {
    const day = p.t.toLocaleDateString('en-CA')
    const key = `${day}|${p.t.getHours()}`
    const c = cells.get(key) || { sum: 0, n: 0 }
    c.sum += p.v
    c.n++
    cells.set(key, c)
  }
  const days = [...new Set([...cells.keys()].map((k) => k.split('|')[0]))].sort()
  const values = [...cells.values()].map((c) => c.sum / c.n)
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const ramp = isDark.value ? [...RAMP].reverse() : RAMP
  const colour = (v) => ramp[Math.min(ramp.length - 1, Math.floor(((v - lo) / (hi - lo || 1)) * ramp.length))]
  return {
    lo, hi, ramp,
    rows: days.map((day) => ({
      day,
      label: new Date(day + 'T12:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }),
      cells: Array.from({ length: 24 }, (_, h) => {
        const c = cells.get(`${day}|${h}`)
        const v = c ? c.sum / c.n : null
        return { h, v, bg: v === null ? 'transparent' : colour(v) }
      })
    }))
  }
})
</script>

<template>
  <div class="pattern" role="img" :aria-label="t('analytics.pattern_aria', { what: label })">
    <div class="rows">
      <div v-for="r in grid.rows" :key="r.day" class="row">
        <span class="day">{{ r.label }}</span>
        <span v-for="c in r.cells" :key="c.h" class="cell" :style="{ background: c.bg }"
              :title="c.v === null ? '' : `${r.label} ${String(c.h).padStart(2, '0')}:00  ${c.v.toFixed(0)}${unit}`"></span>
      </div>
      <div class="row hours" aria-hidden="true">
        <span class="day"></span>
        <span v-for="h in 24" :key="h" class="hour">{{ (h - 1) % 6 === 0 ? String(h - 1).padStart(2, '0') : '' }}</span>
      </div>
    </div>
    <div class="legend">
      <span class="tabular">{{ grid.lo.toFixed(0) }}{{ unit }}</span>
      <span class="ramp"><span v-for="c in grid.ramp" :key="c" :style="{ background: c }"></span></span>
      <span class="tabular">{{ grid.hi.toFixed(0) }}{{ unit }}</span>
    </div>
  </div>
</template>

<style scoped>
.pattern { display: flex; flex-direction: column; gap: 0.8rem; overflow-x: auto; }
.rows { display: flex; flex-direction: column; gap: 2px; min-width: 520px; }
.row { display: grid; grid-template-columns: 4.5rem repeat(24, 1fr); gap: 2px; align-items: center; }
.day { font-size: 0.74rem; color: var(--ink-2); white-space: nowrap; }
.cell { height: 18px; border-radius: 3px; }
.hours .hour { font-size: 0.68rem; color: var(--ink-3); }
.legend { display: flex; align-items: center; gap: 0.5rem; font-size: 0.74rem; color: var(--ink-2); }
.ramp { display: flex; gap: 2px; }
.ramp span { width: 22px; height: 10px; border-radius: 2px; }
</style>
