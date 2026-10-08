// One shared WebSocket to the gateway's live-readings stream, reused by
// every SensorChart on screen. A dashboard can show many charts at once
// (device cards' sparklines plus a detail page's full chart) -- opening
// one socket per chart would multiply connections for no benefit, since
// the stream already carries every device's readings and each listener
// just filters for the one it cares about.
//
// Reconnects with backoff on its own; a chart that subscribes never has
// to know or care whether the socket is currently up. If the socket is
// down (or was never reachable), each SensorChart's periodic HTTP poll
// is still the source of truth and keeps the chart correct -- this is
// purely a low-latency nudge on top of that.
import client from './client'

const listeners = new Set()

let socket = null
let reconnectDelayMs = 1000
const maxReconnectDelayMs = 30000
let reconnectTimer = null
let intentionallyClosed = false

function wsBaseURL() {
  const httpBase = client.defaults.baseURL || window.location.origin
  return httpBase.replace(/^http/, 'ws')
}

function scheduleReconnect() {
  if (intentionallyClosed || reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connect()
  }, reconnectDelayMs)
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, maxReconnectDelayMs)
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return
  const token = localStorage.getItem('vert.token')
  if (!token) return // not logged in yet -- a chart's own poll still works

  intentionallyClosed = false
  socket = new WebSocket(`${wsBaseURL()}/api/v1/ws/readings?token=${encodeURIComponent(token)}`)

  socket.onopen = () => {
    reconnectDelayMs = 1000
  }
  socket.onmessage = (event) => {
    let msg
    try {
      msg = JSON.parse(event.data)
    } catch {
      return // not a reading we understand -- ignore rather than throw
    }
    for (const listener of listeners) listener(msg)
  }
  socket.onclose = () => {
    socket = null
    scheduleReconnect()
  }
  socket.onerror = () => {
    socket?.close()
  }
}

// subscribe registers a callback for every live reading and returns an
// unsubscribe function, matching the onMounted/onBeforeUnmount pairing
// every SensorChart already does for its poll timer.
export function subscribe(callback) {
  listeners.add(callback)
  connect()
  return () => {
    listeners.delete(callback)
    if (listeners.size === 0) {
      intentionallyClosed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.close()
      socket = null
    }
  }
}
