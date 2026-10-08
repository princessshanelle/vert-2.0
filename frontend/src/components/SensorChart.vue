<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import {
  Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip, Legend
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import { useDevicesStore } from '../stores/devices'
import { subscribe } from '../api/liveSocket'

Chart.register(LineController, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip, Legend)

const props = defineProps({
  deviceId: { type: String, required: true },
  // Single-metric shorthand (unchanged, still used by DeviceCard's compact
  // sparkline where one line at a time is the point). For more than one
  // line on the same chart -- e.g. temperature and humidity together --
  // pass `series` instead: an array of { metric, label, color, axis },
  // where axis is 'y' (left) or 'y1' (right). When both are given,
  // `series` wins.
  metric: { type: String, default: null },
  series: { type: Array, default: null },
  minutes: { type: Number, default: 60 },
  compact: { type: Boolean, default: false },
  color: { type: String, default: '#1b5e20' },
  // With the WebSocket live feed (below) handling real-time updates, this
  // poll is just a periodic correctness resync -- it re-fetches the true
  // history from TimescaleDB so any point lost to a missed/duplicate
  // socket message, a socket reconnect gap, or a metric not yet flowing
  // over the socket, self-heals on its own without user action.
  pollMs: { type: Number, default: 20000 }
})

// The one place "what lines does this chart draw" gets decided, so
// refresh(), buildChart(), and onLiveReading() never have to each
// re-derive it from the two different prop shapes.
const seriesConfig = computed(() => {
  if (props.series?.length) return props.series
  return [{ metric: props.metric, label: props.metric, color: props.color, axis: 'y' }]
})
const usesRightAxis = computed(() => seriesConfig.value.some((s) => s.axis === 'y1'))

const store = useDevicesStore()
const canvas = ref(null)
let chart = null
let timer = null
let unsubscribeLive = null

function buildChart(seriesPoints) {
  const datasets = seriesConfig.value.map((s, i) => ({
    label: s.label,
    data: seriesPoints[i].map((p) => ({ x: new Date(p.t), y: p.v })),
    borderColor: s.color,
    backgroundColor: s.color + '22',
    yAxisID: s.axis || 'y',
    fill: seriesConfig.value.length === 1,
    tension: 0.35,
    pointRadius: props.compact ? 0 : 2,
    borderWidth: 2
  }))

  if (chart) {
    chart.data.datasets = datasets
    chart.update('none')
    return
  }

  const scales = {
    x: {
      type: 'time',
      display: !props.compact,
      time: { unit: props.minutes > 180 ? 'hour' : 'minute' },
      grid: { display: false }
    },
    y: {
      display: !props.compact,
      position: 'left',
      grid: { color: '#f0f0f0' }
    }
  }
  if (usesRightAxis.value) {
    scales.y1 = { display: !props.compact, position: 'right', grid: { display: false } }
  }

  chart = new Chart(canvas.value, {
    type: 'line',
    data: { datasets },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        tooltip: { enabled: !props.compact },
        legend: { display: !props.compact && seriesConfig.value.length > 1 }
      },
      scales
    }
  })
}

async function refresh() {
  try {
    const seriesPoints = await Promise.all(
      seriesConfig.value.map((s) => store.fetchHistory(props.deviceId, s.metric, props.minutes))
    )
    buildChart(seriesPoints)
  } catch {
    // A missed poll just leaves the last-known chart on screen -- no
    // reason to blank out a working chart because of one flaky fetch.
  }
}

// onLiveReading appends one freshly-published point the instant it
// arrives over the WebSocket, instead of waiting for the next poll --
// this is what makes the chart actually move in real time. It only ever
// appends to the existing chart (built by the last refresh()); a device
// or metric this chart doesn't show is ignored, and a point that's not
// newer than what's already drawn is ignored too, since the socket can
// redeliver on reconnect.
function onLiveReading(msg) {
  if (!chart || msg.device_id !== props.deviceId) return
  const seriesIndex = seriesConfig.value.findIndex((s) => s.metric === msg.metric)
  if (seriesIndex === -1) return
  const data = chart.data.datasets[seriesIndex].data
  const point = { x: new Date(msg.t), y: msg.v }
  const last = data[data.length - 1]
  if (last && point.x <= last.x) return
  data.push(point)
  chart.update('none')
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, props.pollMs)
  unsubscribeLive = subscribe(onLiveReading)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  unsubscribeLive?.()
  chart?.destroy()
})
watch(() => [props.deviceId, props.metric, props.series, props.minutes], () => {
  chart?.destroy()
  chart = null
  refresh()
})
</script>

<template>
  <div class="chart-wrap" :class="{ compact }">
    <canvas ref="canvas"></canvas>
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; width: 100%; height: 220px; }
.chart-wrap.compact { height: 56px; }
</style>
