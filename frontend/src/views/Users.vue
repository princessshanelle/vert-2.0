<script setup>
// Who can use this greenhouse, and as what. Admins only (router meta).
// Nobody is deleted -- devices, reminders and alerts point at the person
// who made them -- "Remove access" disables the account and signs it out
// everywhere; "Restore access" undoes it.
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import client from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t, locale } = useI18n()
const auth = useAuthStore()
const ROLES = ['admin', 'operator', 'viewer']

const users = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const showAdd = ref(false)
const form = ref(newForm())
const adding = ref(false)
const resetFor = ref(null) // user id whose reset form is open
const resetPassword = ref('')

function newForm() {
  return { display_name: '', email: '', role: 'operator', password: '' }
}
function errorText(err) {
  return err.response?.data?.error || t('users.failed')
}

async function load() {
  loading.value = true
  try {
    const { data } = await client.get('/api/v1/users')
    users.value = data || []
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
onMounted(load)

const sorted = computed(() => [...users.value].sort((a, b) => (a.disabled - b.disabled) || a.email.localeCompare(b.email)))
const isMe = (u) => u.id === auth.user?.id

function replace(updated) {
  users.value = users.value.map((u) => (u.id === updated.id ? updated : u))
}

async function add() {
  adding.value = true
  error.value = ''
  try {
    const { data } = await client.post('/api/v1/users', form.value)
    users.value.push(data)
    notice.value = t('users.added', { name: data.display_name, email: data.email })
    form.value = newForm()
    showAdd.value = false
  } catch (err) {
    error.value = errorText(err)
  } finally {
    adding.value = false
  }
}

async function update(u, change, message) {
  error.value = ''
  notice.value = ''
  try {
    const { data } = await client.patch(`/api/v1/users/${u.id}`, change)
    replace(data)
    notice.value = message(data)
  } catch (err) {
    error.value = errorText(err)
    await load() // put the role select back to what the server has
  }
}

function setRole(u, role) {
  update(u, { role }, (d) => t('users.role_changed', { name: d.display_name, role: t(`roles.${role}`) }))
}
function setAccess(u, disabled) {
  update(u, { disabled }, (d) => t(disabled ? 'users.removed' : 'users.restored', { name: d.display_name }))
}

async function doReset(u) {
  error.value = ''
  notice.value = ''
  try {
    await client.put(`/api/v1/users/${u.id}/password`, { new_password: resetPassword.value })
    notice.value = t('users.reset_done', { name: u.display_name })
    resetFor.value = null
    resetPassword.value = ''
  } catch (err) {
    error.value = errorText(err)
  }
}

function since(iso) {
  try {
    return new Date(iso).toLocaleDateString(locale.value, { day: 'numeric', month: 'short', year: 'numeric' })
  } catch {
    return ''
  }
}
</script>

<template>
  <main class="users">
    <header class="head">
      <div>
        <h1>{{ t('users.title') }}</h1>
        <p class="muted">{{ t('users.subtitle') }}</p>
      </div>
      <button class="btn primary" @click="showAdd = !showAdd" :aria-expanded="showAdd">{{ t('users.add') }}</button>
    </header>

    <form v-if="showAdd" class="panel add" @submit.prevent="add">
      <h2>{{ t('users.add') }}</h2>
      <div class="add-grid">
        <label>{{ t('settings.name') }}<input v-model="form.display_name" required /></label>
        <label>{{ t('settings.email') }}<input v-model="form.email" type="email" required /></label>
        <label>{{ t('settings.role') }}
          <select v-model="form.role">
            <option v-for="r in ROLES" :key="r" :value="r">{{ t(`roles.${r}`) }}</option>
          </select>
        </label>
        <label>{{ t('users.temp_password') }}
          <input v-model="form.password" type="text" minlength="8" autocomplete="off" required />
        </label>
      </div>
      <p class="muted hint">{{ t('users.temp_password_hint') }}</p>
      <div class="row-actions">
        <button class="btn primary" type="submit" :disabled="adding">{{ t('users.add') }}</button>
        <button class="btn" type="button" @click="showAdd = false">{{ t('common.cancel') }}</button>
      </div>
    </form>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>

    <section class="panel list" :aria-busy="loading">
      <p class="roles-help muted">{{ t('users.roles_help') }}</p>
      <ul>
        <li v-for="u in sorted" :key="u.id" :class="{ off: u.disabled }">
          <div class="who">
            <strong>{{ u.display_name || u.email }}</strong>
            <span v-if="isMe(u)" class="status-chip neutral">{{ t('users.you') }}</span>
            <span v-if="u.disabled" class="status-chip warning">{{ t('users.no_access') }}</span>
            <span class="email muted">{{ u.email }}</span>
            <span class="since muted">{{ t('users.since', { date: since(u.created_at) }) }}</span>
          </div>
          <div class="controls">
            <label class="role">
              <span class="visually-hidden">{{ t('settings.role') }}</span>
              <select :value="u.role" :disabled="isMe(u) || u.disabled" @change="setRole(u, $event.target.value)">
                <option v-for="r in ROLES" :key="r" :value="r">{{ t(`roles.${r}`) }}</option>
              </select>
            </label>
            <template v-if="!isMe(u)">
              <button class="btn" @click="resetFor = resetFor === u.id ? null : u.id; resetPassword = ''">{{ t('users.reset') }}</button>
              <button v-if="!u.disabled" class="btn danger" @click="setAccess(u, true)">{{ t('users.remove') }}</button>
              <button v-else class="btn" @click="setAccess(u, false)">{{ t('users.restore') }}</button>
            </template>
          </div>
          <form v-if="resetFor === u.id" class="reset" @submit.prevent="doReset(u)">
            <label>{{ t('users.new_temp_password', { name: u.display_name }) }}
              <input v-model="resetPassword" type="text" minlength="8" autocomplete="off" required />
            </label>
            <button class="btn primary" type="submit">{{ t('users.reset') }}</button>
            <p class="muted hint">{{ t('users.reset_hint') }}</p>
          </form>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.users { padding: 2rem clamp(1rem, 3vw, 2.5rem) 3rem; max-width: 900px; margin: 0 auto; display: flex; flex-direction: column; gap: 1.1rem; }
.head { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; }
.head p { margin-top: 0.35rem; }
.add h2 { margin-bottom: 0.8rem; }
.add-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 0.75rem; }
label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.86rem; font-weight: 600; color: var(--ink-2); }
input, select { padding: 0.5rem 0.65rem; font-weight: 400; color: var(--ink); }
.hint { font-size: 0.8rem; margin-top: 0.5rem; }
.row-actions { display: flex; gap: 0.5rem; margin-top: 0.8rem; }
.error { color: var(--critical); }
.notice { color: var(--good); font-weight: 600; }
.roles-help { font-size: 0.82rem; margin-bottom: 0.4rem; }
.list ul { list-style: none; margin: 0; padding: 0; }
.list li { display: grid; grid-template-columns: 1fr auto; gap: 0.6rem 1rem; align-items: center; padding: 0.85rem 0; border-top: 1px solid var(--line); }
.list li.off .who strong { color: var(--ink-3); }
.who { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem 0.55rem; min-width: 0; }
.email { flex-basis: 100%; font-size: 0.86rem; overflow-wrap: anywhere; }
.since { font-size: 0.75rem; flex-basis: 100%; }
.controls { display: flex; gap: 0.45rem; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.role select { border-radius: 999px; padding: 0.35rem 0.7rem; font-size: 0.85rem; }
.btn.danger:hover:not(:disabled) { border-color: var(--critical); color: var(--critical); }
.reset { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.6rem; background: var(--surface-sunk); padding: 0.8rem; border-radius: var(--radius-m); }
.reset label { flex: 1; min-width: 200px; }
.reset .hint { flex-basis: 100%; margin: 0; }
@media (max-width: 620px) {
  .list li { grid-template-columns: 1fr; }
  .controls { justify-content: flex-start; }
}
</style>
