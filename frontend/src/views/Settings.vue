<script setup>
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../stores/auth'
import { SUPPORTED_LOCALES, setLocale } from '../i18n'
import { themeMode, setTheme } from '../lib/theme'

const { t, locale } = useI18n()
const auth = useAuthStore()
</script>

<template>
  <main class="settings">
    <div class="page-head">
      <h1>{{ t('settings.title') }}</h1>
    </div>

    <section class="card">
      <h2>{{ t('settings.account') }}</h2>
      <div class="fact-row"><span>{{ t('settings.name') }}</span><strong>{{ auth.user?.display_name }}</strong></div>
      <div class="fact-row"><span>{{ t('settings.email') }}</span><strong>{{ auth.user?.email }}</strong></div>
      <div class="fact-row"><span>{{ t('settings.role') }}</span><strong>{{ t(`roles.${auth.user?.role}`, auth.user?.role) }}</strong></div>
    </section>

    <section class="card">
      <h2>{{ t('settings.language') }}</h2>
      <p class="muted">{{ t('settings.language_hint') }}</p>
      <select :value="locale" @change="setLocale($event.target.value)">
        <option v-for="l in SUPPORTED_LOCALES" :key="l.code" :value="l.code">{{ l.label }}</option>
      </select>
    </section>

    <section class="card">
      <h2>{{ t('settings.appearance') }}</h2>
      <p class="muted">{{ t('settings.appearance_hint') }}</p>
      <div class="seg theme-pick" role="group" :aria-label="t('settings.appearance')">
        <button v-for="m in ['auto', 'light', 'dark']" :key="m" :aria-pressed="themeMode === m" @click="setTheme(m)">
          {{ t(`settings.theme_${m}`) }}
        </button>
      </div>
    </section>

    <section class="card about">
      <h2>{{ t('settings.about') }}</h2>
      <p>{{ t('settings.about_text') }}</p>
    </section>
  </main>
</template>

<style scoped>
.settings { padding: 2rem clamp(1rem, 3vw, 2.5rem); max-width: 560px; margin: 0 auto; }
.theme-pick { margin-top: 0.6rem; }
select { background: var(--surface); }
.page-head { margin-bottom: 1.25rem; }
.card { background: var(--surface); border-radius: 12px; padding: 1.25rem; margin-bottom: 1.25rem; box-shadow: var(--shadow); border: 1px solid var(--line); }
.card h2 { margin-top: 0; font-size: 1rem; color: var(--canopy); }
.fact-row { display: flex; justify-content: space-between; padding: 0.4rem 0; border-bottom: 1px solid var(--line); font-size: 0.9rem; }
.fact-row:last-child { border-bottom: none; }
.muted { color: var(--ink-3); font-size: 0.85rem; }
select { padding: 0.5rem; border: 1px solid var(--line-strong); border-radius: 8px; margin-top: 0.5rem; }
.about p { color: var(--ink-2); line-height: 1.5; }
</style>
