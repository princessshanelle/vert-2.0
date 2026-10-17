import { defineStore } from 'pinia'
import client from '../api/client'
import { setLocale } from '../i18n'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('vert.token') || null,
    user: JSON.parse(localStorage.getItem('vert.user') || 'null')
  }),
  getters: {
    isAuthenticated: (state) => !!state.token
  },
  actions: {
    async login(email, password) {
      const { data } = await client.post('/api/v1/auth/login', { email, password })
      this._persist(data)
    },
    async register(email, password, displayName, locale) {
      const { data } = await client.post('/api/v1/auth/register', {
        email,
        password,
        display_name: displayName,
        locale
      })
      this._persist(data)
    },
    // Both return a fresh token: the server ends every other session on a
    // password change, and the token carries the email.
    async changePassword(currentPassword, newPassword) {
      const { data } = await client.put('/api/v1/me/password', {
        current_password: currentPassword,
        new_password: newPassword
      })
      this._persist(data)
    },
    async changeEmail(email, currentPassword) {
      const { data } = await client.put('/api/v1/me/email', { email, current_password: currentPassword })
      this._persist(data)
    },
    logout() {
      this.token = null
      this.user = null
      localStorage.removeItem('vert.token')
      localStorage.removeItem('vert.user')
    },
    _persist(data) {
      this.token = data.token
      this.user = data.user
      localStorage.setItem('vert.token', data.token)
      localStorage.setItem('vert.user', JSON.stringify(data.user))
      if (data.user?.locale) setLocale(data.user.locale)
    }
  }
})
