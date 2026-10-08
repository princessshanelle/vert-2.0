import { defineStore } from 'pinia'
import { automationClient } from '../api/client'

export const useRulesStore = defineStore('rules', {
  state: () => ({
    rules: [],
    loading: false,
    error: ''
  }),
  actions: {
    async fetchRules() {
      this.loading = true
      this.error = ''
      try {
        const { data } = await automationClient.get('/api/v1/rules')
        this.rules = data || []
      } catch (err) {
        this.error = err.response?.data?.error || 'Could not reach the automation service right now.'
      } finally {
        this.loading = false
      }
    },
    async createRule(rule) {
      const { data } = await automationClient.post('/api/v1/rules', rule)
      this.rules.push(data)
      return data
    },
    async setEnabled(id, enabled) {
      await automationClient.patch(`/api/v1/rules/${id}`, { enabled })
      const rule = this.rules.find((r) => r.id === id)
      if (rule) rule.enabled = enabled
    },
    async deleteRule(id) {
      await automationClient.delete(`/api/v1/rules/${id}`)
      this.rules = this.rules.filter((r) => r.id !== id)
    }
  }
})
