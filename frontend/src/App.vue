<script setup>
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from './stores/auth'
import { useAlertsStore } from './stores/alerts'
import { useRouter, RouterView, RouterLink } from 'vue-router'
import { SUPPORTED_LOCALES, setLocale } from './i18n'

const { t, locale } = useI18n()
const auth = useAuthStore()
const alerts = useAlertsStore()
const router = useRouter()

const isAdmin = computed(() => auth.user?.role === 'admin')

watch(
  () => auth.isAuthenticated,
  (loggedIn) => { if (loggedIn) alerts.fetchAlerts() },
  { immediate: true }
)

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="app-shell">
    <template v-if="auth.isAuthenticated">
      <header class="app-header">
        <RouterLink to="/" class="brand">{{ t('app.name') }}</RouterLink>
        <nav class="main-nav">
          <RouterLink to="/">{{ t('nav.dashboard') }}</RouterLink>
          <RouterLink to="/zones">{{ t('nav.zones') }}</RouterLink>
          <RouterLink to="/rules">{{ t('nav.rules') }}</RouterLink>
          <RouterLink to="/alerts" class="with-badge">
            {{ t('nav.alerts') }}
            <span v-if="alerts.unacknowledgedCount" class="badge">{{ alerts.unacknowledgedCount }}</span>
          </RouterLink>
          <RouterLink to="/reminders">{{ t('nav.reminders') }}</RouterLink>
          <RouterLink v-if="isAdmin" to="/provision">{{ t('nav.provision') }}</RouterLink>
        </nav>
        <select :value="locale" @change="setLocale($event.target.value)" class="locale-select">
          <option v-for="l in SUPPORTED_LOCALES" :key="l.code" :value="l.code">{{ l.label }}</option>
        </select>
        <RouterLink to="/settings" class="icon-link" :title="t('nav.settings')">&#9881;</RouterLink>
        <button class="logout" @click="logout">{{ t('nav.logout') }}</button>
      </header>
    </template>
    <RouterView />
  </div>
</template>

<style>
:root {
  --vert-green: #1b5e20;
  --vert-green-light: #e8f3e8;
  --vert-bg: #f7f9f6;
  font-family: system-ui, -apple-system, sans-serif;
}
body { margin: 0; background: var(--vert-bg); }

.app-header {
  display: flex; align-items: center; gap: 1.25rem; flex-wrap: wrap;
  padding: 0.75rem 1.25rem; background: var(--vert-green); color: white;
}
.brand { font-weight: 700; color: white; text-decoration: none; font-size: 1.05rem; }
.main-nav { display: flex; gap: 1rem; flex-wrap: wrap; margin-right: auto; }
.main-nav a {
  color: rgba(255,255,255,0.85); text-decoration: none; font-size: 0.9rem;
  padding: 0.3rem 0.1rem; border-bottom: 2px solid transparent;
}
.main-nav a.router-link-exact-active { color: white; border-bottom-color: white; font-weight: 600; }
.with-badge { position: relative; display: inline-flex; align-items: center; gap: 0.3rem; }
.badge {
  background: #f2a63c; color: #442d00; font-size: 0.7rem; font-weight: 700;
  border-radius: 999px; padding: 0.05rem 0.4rem; line-height: 1.3;
}
.locale-select { border-radius: 6px; border: none; padding: 0.25rem 0.4rem; }
.icon-link { color: white; text-decoration: none; font-size: 1.1rem; }
.logout {
  background: rgba(255,255,255,0.15); color: white; border: none; padding: 0.4rem 0.8rem;
  border-radius: 6px; cursor: pointer; font-size: 0.85rem;
}
.logout:hover { background: rgba(255,255,255,0.25); }
</style>
