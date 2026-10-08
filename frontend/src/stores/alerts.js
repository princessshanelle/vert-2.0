import { defineStore } from 'pinia'
import { automationClient } from '../api/client'

export const useAlertsStore = defineStore('alerts', {
  state: () => ({
    alerts: [],
    loading: false,
    error: ''
  }),
  getters: {
    unacknowledgedCount: (state) => state.alerts.filter((a) => !a.acknowledged).length
  },
  actions: {
    async fetchAlerts() {
      this.loading = true
      this.error = ''
      try {
        const { data } = await automationClient.get('/api/v1/alerts')
        this.alerts = data || []
      } catch (err) {
        this.error = err.response?.data?.error || 'Could not reach the automation service right now.'
      } finally {
        this.loading = false
      }
    },
    async acknowledge(id) {
      await automationClient.post(`/api/v1/alerts/${id}/acknowledge`)
      const alert = this.alerts.find((a) => a.id === id)
      if (alert) alert.acknowledged = true
    }
  }
})
