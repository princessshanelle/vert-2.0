import { defineStore } from 'pinia'
import client from '../api/client'

// One shared source of truth for devices and zones, so the dashboard,
// the device detail page, and the zones/provisioning screens never show
// three different stale copies of the same list.
export const useDevicesStore = defineStore('devices', {
  state: () => ({
    devices: [],
    zones: [],
    loading: false,
    error: ''
  }),
  getters: {
    zoneName: (state) => (zoneId) => {
      const zone = state.zones.find((z) => z.id === zoneId)
      return zone ? zone.name : null
    },
    devicesByZone: (state) => {
      const groups = new Map()
      const unassigned = []
      for (const device of state.devices) {
        if (!device.zone_id) {
          unassigned.push(device)
          continue
        }
        if (!groups.has(device.zone_id)) groups.set(device.zone_id, [])
        groups.get(device.zone_id).push(device)
      }
      return { groups, unassigned }
    }
  },
  actions: {
    async fetchDevices() {
      this.loading = true
      this.error = ''
      try {
        const { data } = await client.get('/api/v1/devices')
        this.devices = data || []
      } catch (err) {
        this.error = err.response?.data?.error || 'Could not reach the greenhouse right now.'
      } finally {
        this.loading = false
      }
    },
    async fetchZones() {
      // Called eagerly from several pages' onMounted -- swallow the
      // failure here rather than letting it become an unhandled
      // rejection every place that doesn't wrap it in its own try/catch.
      try {
        const { data } = await client.get('/api/v1/zones')
        this.zones = data || []
      } catch (err) {
        this.error = err.response?.data?.error || this.error || 'Could not reach the greenhouse right now.'
      }
    },
    async createZone(name) {
      const { data } = await client.post('/api/v1/zones', { name })
      this.zones.push(data)
      return data
    },
    async assignZone(deviceId, zoneId) {
      await client.put(`/api/v1/devices/${deviceId}/zone`, { zone_id: zoneId })
      await this.fetchDevices()
    },
    async provisionDevice(deviceId, deviceType) {
      const { data } = await client.post('/api/v1/devices/provision', {
        device_id: deviceId,
        device_type: deviceType
      })
      await this.fetchDevices()
      return data
    },
    async fetchFrames(deviceId) {
      const { data } = await client.get(`/api/v1/devices/${deviceId}/frames`)
      return data || []
    },
    async fetchDevice(deviceId) {
      const { data } = await client.get(`/api/v1/devices/${deviceId}`)
      return data
    },
    async fetchMetrics(deviceId) {
      const { data } = await client.get(`/api/v1/devices/${deviceId}/metrics`)
      return data || []
    },
    async fetchHistory(deviceId, metric, minutes = 60) {
      const { data } = await client.get(`/api/v1/devices/${deviceId}/readings`, {
        params: { metric, minutes }
      })
      return data || []
    },
    async sendCommand(deviceId, command) {
      await client.post(`/api/v1/devices/${deviceId}/command`, { command })
    }
  }
})
