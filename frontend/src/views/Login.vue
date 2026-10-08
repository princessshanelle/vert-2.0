<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter, RouterLink } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()

const email = ref('')
const password = ref('')
const error = ref('')

async function submit() {
  error.value = ''
  try {
    await auth.login(email.value, password.value)
    router.push({ name: 'dashboard' })
  } catch {
    error.value = t('auth.invalid_credentials')
  }
}
</script>

<template>
  <div class="auth-page">
    <form class="auth-card" @submit.prevent="submit">
      <h1>{{ t('auth.login') }}</h1>
      <label>{{ t('auth.email') }}<input v-model="email" type="email" required /></label>
      <label>{{ t('auth.password') }}<input v-model="password" type="password" required /></label>
      <p v-if="error" class="error">{{ error }}</p>
      <button type="submit">{{ t('auth.login_cta') }}</button>
      <RouterLink to="/register">{{ t('auth.switch_to_register') }}</RouterLink>
    </form>
  </div>
</template>

<style scoped>
.auth-page { display: flex; justify-content: center; align-items: center; min-height: 100vh; }
.auth-card { display: flex; flex-direction: column; gap: 0.75rem; width: 320px; background: white; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }
label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.9rem; }
input { padding: 0.5rem; border: 1px solid #ccc; border-radius: 6px; }
button { padding: 0.6rem; background: var(--vert-green); color: white; border: none; border-radius: 6px; cursor: pointer; }
.error { color: #c62828; font-size: 0.85rem; }
</style>
