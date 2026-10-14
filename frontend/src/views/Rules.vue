<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRulesStore } from '../stores/rules'
import { useDevicesStore } from '../stores/devices'

const { t } = useI18n()
const rulesStore = useRulesStore()
const devicesStore = useDevicesStore()

const form = ref(newForm())
const submitting = ref(false)
const error = ref('')
const showForm = ref(false)

function newForm() {
  return {
    name: '',
    scopeType: 'zone', // 'zone' | 'device'
    zoneId: '',
    deviceId: '',
    metric: 'temp_c',
    op: '>',
    value: 30,
    forMinutes: 10,
    actionDeviceId: '',
    actionCommand: 'open'
  }
}

onMounted(() => {
  rulesStore.fetchRules()
  devicesStore.fetchDevices()
  devicesStore.fetchZones()
})

async function submit() {
  error.value = ''
  submitting.value = true
  try {
    const payload = {
      name: form.value.name.trim(),
      condition: {
        metric: form.value.metric,
        op: form.value.op,
        value: Number(form.value.value),
        for_minutes: Number(form.value.forMinutes)
      },
      action: {
        device_id: form.value.actionDeviceId,
        command: form.value.actionCommand
      }
    }
    if (form.value.scopeType === 'zone') {
      payload.zone_id = form.value.zoneId
    } else {
      payload.device_id = form.value.deviceId
    }
    await rulesStore.createRule(payload)
    form.value = newForm()
    showForm.value = false
  } catch (err) {
    error.value = err.response?.data?.error || t('rules.create_failed')
  } finally {
    submitting.value = false
  }
}

async function toggle(rule) {
  await rulesStore.setEnabled(rule.id, !rule.enabled)
}

async function remove(rule) {
  if (!confirm(t('rules.confirm_delete', { name: rule.name }))) return
  await rulesStore.deleteRule(rule.id)
}

function scopeLabel(rule) {
  if (rule.device_id) return rule.device_id
  const zone = devicesStore.zones.find((z) => z.id === rule.zone_id)
  return zone ? zone.name : t('dashboard.unassigned_zone')
}
</script>

<template>
  <main class="rules">
    <div class="page-head">
      <h1>{{ t('rules.title') }}</h1>
      <p class="subtitle">{{ t('rules.subtitle') }}</p>
      <button v-if="!showForm" @click="showForm = true">{{ t('rules.new_rule') }}</button>
    </div>

    <form v-if="showForm" class="rule-form" @submit.prevent="submit">
      <label>
        {{ t('rules.name_label') }}
        <input v-model="form.name" :placeholder="t('rules.name_placeholder')" required />
      </label>

      <div class="field-row">
        <label>
          {{ t('rules.scope_label') }}
          <select v-model="form.scopeType">
            <option value="zone">{{ t('rules.scope_zone') }}</option>
            <option value="device">{{ t('rules.scope_device') }}</option>
          </select>
        </label>
        <label v-if="form.scopeType === 'zone'">
          {{ t('device_detail.zone') }}
          <select v-model="form.zoneId" required>
            <option value="" disabled>{{ t('rules.pick_one') }}</option>
            <option v-for="z in devicesStore.zones" :key="z.id" :value="z.id">{{ z.name }}</option>
          </select>
        </label>
        <label v-else>
          {{ t('rules.watched_device') }}
          <select v-model="form.deviceId" required>
            <option value="" disabled>{{ t('rules.pick_one') }}</option>
            <option v-for="d in devicesStore.devices" :key="d.id" :value="d.id">{{ d.display_name || d.id }}</option>
          </select>
        </label>
      </div>

      <p class="section-label">{{ t('rules.when_label') }}</p>
      <div class="field-row">
        <label>
          {{ t('rules.metric_label') }}
          <input v-model="form.metric" placeholder="temp_c" required />
        </label>
        <label>
          {{ t('rules.op_label') }}
          <select v-model="form.op">
            <option value=">">&gt;</option>
            <option value=">=">&ge;</option>
            <option value="<">&lt;</option>
            <option value="<=">&le;</option>
            <option value="==">=</option>
          </select>
        </label>
        <label>
          {{ t('rules.value_label') }}
          <input v-model.number="form.value" type="number" step="0.1" required />
        </label>
        <label>
          {{ t('rules.for_minutes_label') }}
          <input v-model.number="form.forMinutes" type="number" min="0" required />
        </label>
      </div>

      <p class="section-label">{{ t('rules.then_label') }}</p>
      <div class="field-row">
        <label>
          {{ t('rules.action_device_label') }}
          <select v-model="form.actionDeviceId" required>
            <option value="" disabled>{{ t('rules.pick_one') }}</option>
            <option v-for="d in devicesStore.devices" :key="d.id" :value="d.id">{{ d.display_name || d.id }}</option>
          </select>
        </label>
        <label>
          {{ t('rules.action_command_label') }}
          <input v-model="form.actionCommand" placeholder="open" required />
        </label>
      </div>

      <p v-if="error" class="error">{{ error }}</p>
      <div class="form-actions">
        <button type="submit" :disabled="submitting">{{ t('rules.save') }}</button>
        <button type="button" class="secondary" @click="showForm = false">{{ t('common.cancel') }}</button>
      </div>
    </form>

    <p v-if="!rulesStore.rules.length" class="empty">{{ t('rules.no_rules') }}</p>
    <ul v-else class="rule-list">
      <li v-for="rule in rulesStore.rules" :key="rule.id" :class="{ disabled: !rule.enabled }">
        <div class="rule-main">
          <strong>{{ rule.name }}</strong>
          <span class="rule-detail">
            {{ t('rules.summary', {
              scope: scopeLabel(rule),
              metric: rule.condition?.metric,
              op: rule.condition?.op,
              value: rule.condition?.value,
              minutes: rule.condition?.for_minutes,
              target: rule.action?.device_id,
              command: rule.action?.command
            }) }}
          </span>
        </div>
        <div class="rule-controls">
          <button class="link" @click="toggle(rule)">
            {{ rule.enabled ? t('rules.disable') : t('rules.enable') }}
          </button>
          <button class="link danger" @click="remove(rule)">{{ t('common.delete') }}</button>
        </div>
      </li>
    </ul>
  </main>
</template>

<style scoped>
.rules { padding: 1.5rem; max-width: 700px; margin: 0 auto; }
.page-head { margin-bottom: 1.25rem; display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; }
.page-head h1 { margin: 0; width: 100%; }
.subtitle { color: var(--ink-2); margin: 0; flex: 1; }
.rule-form { display: flex; flex-direction: column; gap: 0.9rem; background: var(--surface); padding: 1.25rem; border-radius: 12px; margin-bottom: 1.25rem; box-shadow: var(--shadow); border: 1px solid var(--line); }
.field-row { display: flex; gap: 0.75rem; flex-wrap: wrap; }
.field-row label { flex: 1; min-width: 110px; }
label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.85rem; }
input, select { padding: 0.5rem; border: 1px solid var(--line-strong); border-radius: 8px; }
.section-label { font-size: 0.8rem; font-weight: 600; color: var(--canopy); margin: 0.25rem 0 0; }
.form-actions { display: flex; gap: 0.6rem; }
button { padding: 0.55rem 1rem; background: var(--vert-green); color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
button.secondary { background: transparent; color: var(--ink-2); border: 1px solid var(--line-strong); }
button.link { background: none; color: var(--vert-green); padding: 0.2rem 0.4rem; font-weight: 500; }
button.link.danger { color: var(--critical); }
.error { color: var(--critical); margin: 0; }
.empty { color: var(--ink-2); }
.rule-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.rule-list li { display: flex; justify-content: space-between; align-items: center; gap: 1rem; background: var(--surface); padding: 0.85rem 1rem; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
.rule-list li.disabled { opacity: 0.55; }
.rule-main { display: flex; flex-direction: column; gap: 0.15rem; }
.rule-detail { font-size: 0.8rem; color: var(--ink-3); }
.rule-controls { display: flex; gap: 0.5rem; flex-shrink: 0; }
</style>
