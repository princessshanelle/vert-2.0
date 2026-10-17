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

// Primary places, in the order a grower uses them. The phone tab bar
// shows the first four; the rest stay one tap away in the sidebar/menu.
const nav = computed(() => [
  { to: '/', key: 'dashboard', icon: 'M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z' },
  { to: '/analytics', key: 'analytics', icon: 'M4 20V10M10 20V4M16 20v-7M22 20H2' },
  { to: '/alerts', key: 'alerts', icon: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 21h4', badge: alerts.unacknowledgedCount },
  { to: '/rules', key: 'rules', icon: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4' },
  { to: '/zones', key: 'zones', icon: 'M3 7l9-4 9 4-9 4-9-4zM3 12l9 4 9-4M3 17l9 4 9-4' },
  { to: '/reminders', key: 'reminders', icon: 'M8 3v3M16 3v3M4 8h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1zM9 14l2 2 4-4' },
  ...(isAdmin.value ? [
    { to: '/provision', key: 'provision', icon: 'M12 5v14M5 12h14' },
    { to: '/users', key: 'users', icon: 'M16 20v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 20v-1a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8' }
  ] : [])
])
</script>

<template>
  <div class="app-shell" :class="{ authed: auth.isAuthenticated }">
    <a href="#main" class="skip-link">{{ t('nav.skip') }}</a>
    <template v-if="auth.isAuthenticated">
      <aside class="sidebar">
        <RouterLink to="/" class="brand" :aria-label="t('app.name')">
          <svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true">
            <path d="M16 17C11 17 6 14 6 8c5 0 10 3 10 9z" fill="var(--canopy-2)" />
            <path d="M16 17c5 0 10-3 10-9-5 0-10 3-10 9z" fill="var(--canopy)" />
            <path d="M16 17c-3-4-3-9 0-13 3 4 3 9 0 13z" fill="var(--canopy-2)" opacity=".85" />
            <circle cx="16" cy="24" r="5" fill="var(--berry)" />
          </svg>
          <span class="wordmark">VERT</span>
        </RouterLink>
        <nav class="side-nav" :aria-label="t('nav.main')">
          <RouterLink v-for="item in nav" :key="item.key" :to="item.to" class="nav-item">
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path :d="item.icon" /></svg>
            <span class="nav-label">{{ t(`nav.${item.key}`) }}</span>
            <span v-if="item.badge" class="badge">{{ item.badge }}<span class="visually-hidden"> {{ t('nav.unread') }}</span></span>
          </RouterLink>
        </nav>
        <div class="side-foot">
          <RouterLink to="/settings" class="nav-item">
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
            <span class="nav-label">{{ t('nav.settings') }}</span>
          </RouterLink>
          <label class="locale">
            <span class="visually-hidden">{{ t('settings.language') }}</span>
            <select :value="locale" @change="setLocale($event.target.value)">
              <option v-for="l in SUPPORTED_LOCALES" :key="l.code" :value="l.code">{{ l.label }}</option>
            </select>
          </label>
          <button class="logout" @click="logout">{{ t('nav.logout') }}</button>
        </div>
      </aside>

      <nav class="tabbar" :aria-label="t('nav.main')">
        <RouterLink v-for="item in nav.slice(0, 4)" :key="item.key" :to="item.to" class="tab">
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path :d="item.icon" /></svg>
          <span>{{ t(`nav.${item.key}`) }}</span>
          <span v-if="item.badge" class="badge">{{ item.badge }}</span>
        </RouterLink>
        <RouterLink to="/settings" class="tab">
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
          <span>{{ t('nav.more') }}</span>
        </RouterLink>
      </nav>
    </template>
    <div id="main" class="main" tabindex="-1">
      <RouterView />
    </div>
  </div>
</template>

<style>
.skip-link { position: absolute; left: -999px; top: 0.5rem; z-index: 100; background: var(--surface); padding: 0.5rem 0.8rem; border-radius: var(--radius-s); }
.skip-link:focus { left: 0.5rem; }
.main:focus { outline: none; }

.app-shell.authed { padding-left: var(--sidebar-w); }
.sidebar {
  position: fixed; inset: 0 auto 0 0; width: var(--sidebar-w); display: flex; flex-direction: column; gap: 1.5rem;
  padding: 1.35rem 0.9rem 1rem; background: var(--surface); border-right: 1px solid var(--line); z-index: 20;
}
.brand { display: flex; align-items: center; gap: 0.55rem; text-decoration: none; padding: 0 0.5rem; }
.wordmark { font-family: var(--font-display); font-weight: 750; font-size: 1.45rem; letter-spacing: -0.03em; color: var(--canopy); }
.side-nav { display: flex; flex-direction: column; gap: 0.15rem; }
.nav-item {
  display: flex; align-items: center; gap: 0.7rem; padding: 0.55rem 0.65rem; border-radius: var(--radius-m);
  text-decoration: none; color: var(--ink-2); font-size: 0.92rem; font-weight: 600;
}
.nav-item svg, .tab svg { fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; flex-shrink: 0; }
.nav-item:hover { background: var(--surface-sunk); color: var(--ink); }
.nav-item.router-link-exact-active { background: var(--canopy-wash); color: var(--canopy); }
.nav-label { flex: 1; }
.badge {
  background: var(--berry); color: #fff; font-size: 0.7rem; font-weight: 700; border-radius: 999px;
  min-width: 1.3rem; padding: 0.05rem 0.4rem; text-align: center; line-height: 1.35;
}
.side-foot { margin-top: auto; display: flex; flex-direction: column; gap: 0.5rem; border-top: 1px solid var(--line); padding-top: 0.8rem; }
.locale select {
  width: 100%; padding: 0.45rem 0.6rem; border-radius: var(--radius-m); border: 1px solid var(--line); background: var(--surface-sunk);
  font-size: 0.85rem;
}
.logout {
  border: 1px solid var(--line); background: transparent; color: var(--ink-2); padding: 0.45rem; border-radius: var(--radius-m);
  cursor: pointer; font-size: 0.85rem; font-weight: 600;
}
.logout:hover { color: var(--critical); border-color: var(--critical); }
.tabbar { display: none; }

@media (max-width: 860px) {
  .app-shell.authed { padding-left: 0; padding-bottom: 4.5rem; }
  .sidebar { display: none; }
  .tabbar {
    display: flex; position: fixed; inset: auto 0 0 0; z-index: 20; background: var(--surface);
    border-top: 1px solid var(--line); padding: 0.35rem 0.25rem calc(0.35rem + env(safe-area-inset-bottom));
  }
  .tab {
    flex: 1; display: flex; flex-direction: column; align-items: center; gap: 0.1rem; text-decoration: none;
    color: var(--ink-3); font-size: 0.68rem; font-weight: 650; position: relative; padding: 0.25rem 0; border-radius: var(--radius-m);
  }
  .tab.router-link-exact-active { color: var(--canopy); }
  .tab .badge { position: absolute; top: 0; left: 55%; font-size: 0.62rem; min-width: 1.1rem; }
}

/* Older pages share these until they get their own pass. */
.page-head { margin-bottom: 1.5rem; }
.page-head .subtitle { color: var(--ink-2); margin-top: 0.35rem; }
</style>
