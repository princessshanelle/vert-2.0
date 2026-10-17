<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDevicesStore } from '../stores/devices'

const { t } = useI18n()
const store = useDevicesStore()

const deviceId = ref('')
const deviceType = ref('temp-humidity')
const error = ref('')
const submitting = ref(false)
const result = ref(null)
const copied = ref(false)

const deviceTypeOptions = ['temp-humidity', 'soil-moisture', 'ph', 'camera', 'ai-camera', 'pump', 'light', 'window']

async function submit() {
  error.value = ''
  result.value = null
  const id = deviceId.value.trim()
  if (!id) return
  submitting.value = true
  try {
    result.value = await store.provisionDevice(id, deviceType.value)
    deviceId.value = ''
  } catch (err) {
    error.value = err.response?.data?.error || t('provision.failed')
  } finally {
    submitting.value = false
  }
}

async function copySecret() {
  try {
    await navigator.clipboard.writeText(result.value.secret)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // Clipboard access can be denied by the browser; the secret is still
    // selectable text on screen, so nothing is actually lost here.
  }
}

function startAnother() {
  result.value = null
  copied.value = false
}
</script>

<template>
  <main class="provision">
    <div class="page-head">
      <h1>{{ t('provision.title') }}</h1>
      <p class="subtitle">{{ t('provision.subtitle') }}</p>
    </div>

    <div v-if="result" class="secret-card">
      <h2>{{ t('provision.secret_ready', { id: result.device_id }) }}</h2>
      <p class="warning">{{ t('provision.secret_warning') }}</p>
      <div class="secret-box">
        <code>{{ result.secret }}</code>
        <button @click="copySecret">{{ copied ? t('provision.copied') : t('provision.copy') }}</button>
      </div>
      <p class="next-step">{{ t('provision.next_step', { id: result.device_id }) }}</p>
      <button class="secondary" @click="startAnother">{{ t('provision.provision_another') }}</button>
    </div>

    <form v-else class="provision-form" @submit.prevent="submit">
      <label>
        {{ t('provision.device_id_label') }}
        <input v-model="deviceId" placeholder="dht22-01" required />
        <small>{{ t('provision.device_id_hint') }}</small>
      </label>
      <label>
        {{ t('provision.device_type_label') }}
        <select v-model="deviceType">
          <option v-for="opt in deviceTypeOptions" :key="opt" :value="opt">
            {{ t(`device_types.${opt}`, opt) }}
          </option>
        </select>
      </label>
      <p v-if="error" class="error">{{ error }}</p>
      <button type="submit" :disabled="submitting">{{ t('provision.submit') }}</button>
    </form>
  </main>
</template>

<style scoped>
.provision { padding: 1.5rem; max-width: 480px; margin: 0 auto; }
.page-head { margin-bottom: 1.25rem; }
.subtitle { color: var(--ink-2); margin-top: 0.25rem; }
.provision-form { display: flex; flex-direction: column; gap: 1rem; background: var(--surface); padding: 1.5rem; border-radius: 12px; box-shadow: var(--shadow); border: 1px solid var(--line); }
.provision-form label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.9rem; }
.provision-form input, .provision-form select { padding: 0.55rem; border: 1px solid var(--line-strong); border-radius: 8px; }
.provision-form small { color: var(--ink-3); font-weight: 400; }
button { padding: 0.6rem 1rem; background: var(--canopy); color: var(--ink-inverse); border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
button.secondary { background: transparent; color: var(--canopy); border: 1px solid var(--canopy); margin-top: 1rem; }
.error { color: var(--critical); margin: 0; }
.secret-card { background: var(--surface); padding: 1.5rem; border-radius: 12px; box-shadow: var(--shadow); border: 1px solid var(--line); border: 2px solid #f6c945; }
.secret-card h2 { margin-top: 0; font-size: 1.05rem; }
.warning { color: #8a6d00; background: #fff8e1; padding: 0.6rem 0.75rem; border-radius: 8px; font-size: 0.85rem; }
.secret-box { display: flex; gap: 0.5rem; align-items: center; margin: 1rem 0; }
.secret-box code { flex: 1; background: var(--surface-sunk); padding: 0.6rem 0.75rem; border-radius: 8px; word-break: break-all; font-size: 0.9rem; }
.next-step { font-size: 0.85rem; color: var(--ink-2); }
</style>
