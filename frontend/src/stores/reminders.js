import { defineStore } from 'pinia'
import client from '../api/client'

export const useRemindersStore = defineStore('reminders', {
  state: () => ({
    reminders: [],
    loading: false,
    error: ''
  }),
  actions: {
    async fetchReminders() {
      this.loading = true
      this.error = ''
      try {
        const { data } = await client.get('/api/v1/reminders')
        this.reminders = data || []
      } catch (err) {
        this.error = err.response?.data?.error || 'Could not load your reminders right now.'
      } finally {
        this.loading = false
      }
    },
    async createReminder(reminder) {
      const { data } = await client.post('/api/v1/reminders', reminder)
      this.reminders.push(data)
      return data
    },
    async setCompleted(id, completed) {
      await client.patch(`/api/v1/reminders/${id}`, { completed })
      const reminder = this.reminders.find((r) => r.id === id)
      if (reminder) reminder.completed = completed
    },
    async deleteReminder(id) {
      await client.delete(`/api/v1/reminders/${id}`)
      this.reminders = this.reminders.filter((r) => r.id !== id)
    }
  }
})
