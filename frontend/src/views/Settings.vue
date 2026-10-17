<script setup>
import { ref, computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../stores/auth'
import { SUPPORTED_LOCALES, setLocale } from '../i18n'
import { themeMode, setTheme } from '../lib/theme'

const { t, locale } = useI18n()
const auth = useAuthStore()

// On a phone the tab bar holds four places plus "More" (this page), so the
// rest of the app is reachable from here.
const places = computed(() => [
  { to: '/zones', key: 'zones' },
  { to: '/reminders', key: 'reminders' },
  ...(auth.user?.role === 'admin' ? [{ to: '/provision', key: 'provision' }, { to: '/users', key: 'users' }] : [])
])

// Each form keeps its own status, so a success in one never hides an error in the other.
const email = ref({ value: '', password: '', error: '', done: false, busy: false })
const pw = ref({ current: '', next: '', confirm: '', error: '', done: false, busy: false })

function serverError(err, fallback) {
  const status = err.response?.status
  if (status === 403) return t('settings.wrong_password')
  if (status === 409) return t('settings.email_taken')
  if (status === 400) return err.response?.data?.error || fallback
  return fallback
}

async function changeEmail() {
  const f = email.value
  f.error = ''
  f.done = false
  f.busy = true
  try {
    await auth.changeEmail(f.value, f.password)
    email.value = { value: '', password: '', error: '', done: true, busy: false }
  } catch (err) {
    f.error = serverError(err, t('settings.change_failed'))
  } finally {
    f.busy = false
  }
}

async function changePassword() {
  const f = pw.value
  f.error = ''
  f.done = false
  if (f.next.length < 8) {
    f.error = t('settings.password_too_short')
    return
  }
  if (f.next !== f.confirm) {
    f.error = t('settings.password_mismatch')
    return
  }
  f.busy = true
  try {
    await auth.changePassword(f.current, f.next)
    pw.value = { current: '', next: '', confirm: '', error: '', done: true, busy: false }
  } catch (err) {
    f.error = serverError(err, t('settings.change_failed'))
  } finally {
    f.busy = false
  }
}
</script>

<template>
  <main class="settings">
    <h1>{{ t('settings.title') }}</h1>

    <nav class="panel places" :aria-label="t('nav.more')">
      <RouterLink v-for="p in places" :key="p.key" :to="p.to" class="place">{{ t(`nav.${p.key}`) }}</RouterLink>
    </nav>

    <section class="panel">
      <h2>{{ t('settings.account') }}</h2>
      <dl class="facts">
        <div><dt>{{ t('settings.name') }}</dt><dd>{{ auth.user?.display_name }}</dd></div>
        <div><dt>{{ t('settings.email') }}</dt><dd>{{ auth.user?.email }}</dd></div>
        <div><dt>{{ t('settings.role') }}</dt><dd>{{ t(`roles.${auth.user?.role}`, auth.user?.role) }}</dd></div>
      </dl>
    </section>

    <section class="panel" aria-labelledby="pw-head">
      <h2 id="pw-head">{{ t('settings.change_password') }}</h2>
      <p class="muted hint">{{ t('settings.change_password_hint') }}</p>
      <form class="stack" @submit.prevent="changePassword">
        <label>{{ t('settings.current_password') }}
          <input v-model="pw.current" type="password" autocomplete="current-password" required />
        </label>
        <label>{{ t('settings.new_password') }}
          <input v-model="pw.next" type="password" autocomplete="new-password" minlength="8" required />
        </label>
        <label>{{ t('settings.confirm_password') }}
          <input v-model="pw.confirm" type="password" autocomplete="new-password" minlength="8" required />
        </label>
        <p v-if="pw.error" class="error" role="alert">{{ pw.error }}</p>
        <p v-if="pw.done" class="done" role="status">{{ t('settings.password_changed') }}</p>
        <button class="btn primary" type="submit" :disabled="pw.busy">{{ t('settings.change_password') }}</button>
      </form>
    </section>

    <section class="panel" aria-labelledby="email-head">
      <h2 id="email-head">{{ t('settings.change_email') }}</h2>
      <form class="stack" @submit.prevent="changeEmail">
        <label>{{ t('settings.new_email') }}
          <input v-model="email.value" type="email" autocomplete="email" required />
        </label>
        <label>{{ t('settings.current_password') }}
          <input v-model="email.password" type="password" autocomplete="current-password" required />
        </label>
        <p v-if="email.error" class="error" role="alert">{{ email.error }}</p>
        <p v-if="email.done" class="done" role="status">{{ t('settings.email_changed', { email: auth.user?.email }) }}</p>
        <button class="btn primary" type="submit" :disabled="email.busy">{{ t('settings.change_email') }}</button>
      </form>
    </section>

    <section class="panel">
      <h2>{{ t('settings.language') }}</h2>
      <p class="muted hint">{{ t('settings.language_hint') }}</p>
      <select :value="locale" @change="setLocale($event.target.value)" :aria-label="t('settings.language')">
        <option v-for="l in SUPPORTED_LOCALES" :key="l.code" :value="l.code">{{ l.label }}</option>
      </select>
    </section>

    <section class="panel">
      <h2>{{ t('settings.appearance') }}</h2>
      <p class="muted hint">{{ t('settings.appearance_hint') }}</p>
      <div class="seg" role="group" :aria-label="t('settings.appearance')">
        <button v-for="m in ['auto', 'light', 'dark']" :key="m" :aria-pressed="themeMode === m" @click="setTheme(m)">
          {{ t(`settings.theme_${m}`) }}
        </button>
      </div>
    </section>

    <section class="panel about">
      <h2>{{ t('settings.about') }}</h2>
      <p>{{ t('settings.about_text') }}</p>
    </section>
  </main>
</template>

<style scoped>
.settings { padding: 2rem clamp(1rem, 3vw, 2.5rem) 3rem; max-width: 600px; margin: 0 auto; display: flex; flex-direction: column; gap: 1.1rem; }
.panel h2 { margin-bottom: 0.6rem; }
.facts { margin: 0; }
.facts div { display: flex; justify-content: space-between; gap: 1rem; padding: 0.45rem 0; border-bottom: 1px solid var(--line); font-size: 0.92rem; }
.facts div:last-child { border-bottom: 0; }
.facts dt { color: var(--ink-2); }
.facts dd { margin: 0; font-weight: 600; text-align: right; overflow-wrap: anywhere; }
.hint { font-size: 0.86rem; margin-bottom: 0.75rem; }
.stack { display: flex; flex-direction: column; gap: 0.75rem; }
.stack label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.88rem; font-weight: 600; color: var(--ink-2); }
.stack input { padding: 0.55rem 0.7rem; font-weight: 400; color: var(--ink); }
.stack .btn { align-self: flex-start; }
select { padding: 0.5rem 0.7rem; }
.error { color: var(--critical); font-size: 0.86rem; }
.done { color: var(--good); font-size: 0.86rem; font-weight: 600; }
.about p { color: var(--ink-2); }
.places { display: none; padding: 0.4rem 1.25rem; }
.place { display: block; padding: 0.75rem 0; border-bottom: 1px solid var(--line); color: var(--ink); text-decoration: none; font-weight: 600; }
.place:last-child { border-bottom: 0; }
.place::after { content: '\203A'; float: right; color: var(--ink-3); }
@media (max-width: 860px) { .places { display: block; } }
</style>
