<script setup>
// One metric over time: a 2px line (the average when bucketed), a faint
// wash between each bucket's min and max, and the crop's target band
// shaded behind it. One y-axis only -- two measures get two charts.
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import {
  Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import { cssVar, themeVersion } from '../../lib/theme'

Chart.register(LineController, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip)

const props = defineProps({
  points: { type: Array, required: true }, // [{ t: Date, v, min, max }]
  colorVar: { type: String, default: '--series-health' },
  unit: { type: String, default: '' },
  digits: { type: Number, default: 1 },
  target: { type: Array, default: null }, // [lo, hi] shaded band
  height: { type: Number, default: 200 },
  label: { type: String, default: '' },
  yMin: { type: Number, default: undefined },
  yMax: { type: Number, default: undefined }
})

const canvas = ref(null)
let chart = null

// Draws the target band and a crosshair under the hovered x.
const overlay = {
  id: 'vertOverlay',
  beforeDatasetsDraw(c) {
    const band = c.options.plugins.vertOverlay?.target
    if (!band) return
    const { ctx, chartArea: a, scales: { y } } = c
    const top = Math.max(y.getPixelForValue(band[1]), a.top)
    const bottom = Math.min(y.getPixelForValue(band[0]), a.bottom)
    if (bottom <= top) return
    ctx.save()
    ctx.fillStyle = c.options.plugins.vertOverlay.fill
    ctx.fillRect(a.left, top, a.right - a.left, bottom - top)
    ctx.restore()
  },
  afterDatasetsDraw(c) {
    const active = c.tooltip?.getActiveElements?.()
    if (!active?.length) return
    const { ctx, chartArea: a } = c
    const x = active[0].element.x
    ctx.save()
    ctx.strokeStyle = c.options.plugins.vertOverlay.cross
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, a.top)
    ctx.lineTo(x, a.bottom)
    ctx.stroke()
    ctx.restore()
  }
}

function spanMs() {
  const p = props.points
  return p.length > 1 ? p[p.length - 1].t - p[0].t : 0
}

function build() {
  chart?.destroy()
  chart = null
  if (!canvas.value) return
  const color = cssVar(props.colorVar) || '#1d5c45'
  const ink3 = cssVar('--ink-3')
  const grid = cssVar('--grid')
  const banded = props.points.some((p) => p.max !== p.min)
  const fmt = (v) => `${Number(v).toFixed(props.digits)}${props.unit}`
  const days = spanMs() / 86400000

  const datasets = [
    {
      label: props.label,
      data: props.points.map((p) => ({ x: p.t, y: p.v })),
      borderColor: color,
      borderWidth: 2,
      borderJoinStyle: 'round',
      borderCapStyle: 'round',
      pointRadius: 0,
      pointHoverRadius: 4,
      pointHoverBorderWidth: 2,
      pointHoverBorderColor: cssVar('--surface'),
      pointHoverBackgroundColor: color,
      tension: 0.3,
      fill: false,
      order: 0
    }
  ]
  if (banded) {
    datasets.push(
      { data: props.points.map((p) => ({ x: p.t, y: p.max })), borderWidth: 0, pointRadius: 0, pointHoverRadius: 0, fill: false, order: 2, isBand: true },
      { data: props.points.map((p) => ({ x: p.t, y: p.min })), borderWidth: 0, pointRadius: 0, pointHoverRadius: 0,
        fill: '-1', backgroundColor: color + '1f', order: 2, isBand: true }
    )
  }

  chart = new Chart(canvas.value, {
    type: 'line',
    data: { datasets },
    plugins: [overlay],
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { display: false },
        vertOverlay: { target: props.target, fill: cssVar('--canopy-wash'), cross: cssVar('--axis') },
        tooltip: {
          filter: (item) => !item.dataset.isBand,
          backgroundColor: cssVar('--surface-raised'),
          titleColor: cssVar('--ink'),
          bodyColor: cssVar('--ink-2'),
          borderColor: cssVar('--line-strong'),
          borderWidth: 1,
          padding: 10,
          displayColors: false,
          callbacks: {
            title: (items) => items[0] && new Date(items[0].parsed.x).toLocaleString(undefined,
              { weekday: 'short', hour: '2-digit', minute: '2-digit', day: days > 1 ? 'numeric' : undefined }),
            label: (item) => {
              const p = props.points[item.dataIndex]
              if (!p) return fmt(item.parsed.y)
              return p.max !== p.min ? `${fmt(p.v)}  (${fmt(p.min)} to ${fmt(p.max)})` : fmt(p.v)
            }
          }
        }
      },
      scales: {
        x: {
          type: 'time',
          time: { unit: days > 2 ? 'day' : 'hour', displayFormats: { hour: 'HH:mm', day: 'EEE d' } },
          grid: { display: false },
          border: { color: cssVar('--axis') },
          ticks: { color: ink3, maxRotation: 0, autoSkipPadding: 18, font: { size: 11 } }
        },
        y: {
          min: props.yMin,
          max: props.yMax,
          grid: { color: grid },
          border: { display: false },
          ticks: { color: ink3, maxTicksLimit: 5, font: { size: 11 }, callback: (v) => `${v}${props.unit}` }
        }
      }
    }
  })
}

onMounted(build)
onBeforeUnmount(() => chart?.destroy())
watch(() => [props.points, props.target, props.colorVar, themeVersion.value], build)
</script>

<template>
  <div class="trend" :style="{ height: height + 'px' }">
    <canvas ref="canvas" role="img" :aria-label="label"></canvas>
  </div>
</template>

<style scoped>
.trend { position: relative; width: 100%; }
</style>
