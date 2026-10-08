import axios from 'axios'

function createClient(baseURL) {
  const instance = axios.create({ baseURL })
  instance.interceptors.request.use((config) => {
    const token = localStorage.getItem('vert.token')
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })
  return instance
}

// By default both APIs are called on the same address the dashboard was
// loaded from, and the frontend's nginx forwards them (see nginx.conf) --
// so the dashboard works from localhost, the LAN IP, or a phone alike.
// Set VITE_API_BASE_URL / VITE_AUTOMATION_BASE_URL at build time only to
// point a dev server at APIs somewhere else.

// The gateway: accounts, devices, zones, frames, reminders.
const client = createClient(import.meta.env.VITE_API_BASE_URL || '')

// The automation service: rules and alerts. A separate service (see the
// architecture doc on why it's independent from ingestion), but the same
// login token works against both -- one account, two APIs.
export const automationClient = createClient(import.meta.env.VITE_AUTOMATION_BASE_URL || '')

export default client
