<script setup>
import { ref, onMounted, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRemindersStore } from '../stores/reminders'

const { t, locale } = useI18n()
const store = useRemindersStore()

const title = ref('')
const description = ref('')
const dueAt = ref('')
const recurrence = ref('')
const submitting = ref(false)
const error = ref('')

onMounted(() => store.fetchReminders())

const upcoming = computed(() => store.reminders.filter((r) => !r.completed))
const done = computed(() => store.reminders.filter((r) => r.completed))

async function submit() {
  error.value = ''
  if (!title.value.trim() || !dueAt.value) return
  submitting.value = true
  try {
    await store.createReminder({
      title: title.value.trim(),
      description: description.value.trim(),
      due_at: new Date(dueAt.value).toISOString(),
      recurrence: recurrence.value || null
    })
    title.value = ''
    description.value = ''
    dueAt.value = ''
    recurrence.value = ''
  } catch (err) {
    error.value = err.response?.data?.error || t('reminders.create_failed')
  } finally {
    submitting.value = false
  }
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleString(locale.value, { dateStyle: 'medium', timeStyle: 'short' })
  } catch {
    return iso
  }
}
</script>

<template>
  <main class="reminders">
    <div class="page-head">
      <h1>{{ t('reminders.title') }}</h1>
      <p class="subtitle">{{ t('reminders.subtitle') }}</p>
    </div>

    <form class="new-reminder" @submit.prevent="submit">
      <input v-model="title" :placeholder="t('reminders.title_placeholder')" required />
      <input v-model="description" :placeholder="t('reminders.description_placeholder')" />
      <div class="field-row">
        <input v-model="dueAt" type="datetime-local" required />
        <select v-model="recurrence">
          <option value="">{{ t('reminders.recurrence_none') }}</option>
          <option value="daily">{{ t('reminders.recurrence_daily') }}</option>
          <option value="weekly">{{ t('reminders.recurrence_weekly') }}</option>
          <option value="biweekly">{{ t('reminders.recurrence_biweekly') }}</option>
        </select>
        <button type="submit" :disabled="submitting">{{ t('reminders.add') }}</button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </form>

    <p v-if="!store.reminders.length" class="empty-state">{{ t('reminders.all_clear') }}</p>

    <ul v-else class="reminder-list">
      <li v-for="r in upcoming" :key="r.id">
        <label class="check">
          <input type="checkbox" :checked="r.completed" @change="store.setCompleted(r.id, true)" />
          <span>
            <strong>{{ r.title }}</strong>
            <span class="due">{{ t('reminders.due') }} {{ formatDate(r.due_at) }}</span>
            <span v-if="r.description" class="desc">{{ r.description }}</span>
          </span>
        </label>
        <button class="link danger" @click="store.deleteReminder(r.id)">{{ t('common.delete') }}</button>
      </li>
    </ul>

    <details v-if="done.length" class="done-section">
      <summary>{{ t('reminders.done_count', { count: done.length }) }}</summary>
      <ul class="reminder-list">
        <li v-for="r in done" :key="r.id" class="completed">
          <label class="check">
            <input type="checkbox" :checked="true" @change="store.setCompleted(r.id, false)" />
            <span><strong>{{ r.title }}</strong></span>
          </label>
          <button class="link danger" @click="store.deleteReminder(r.id)">{{ t('common.delete') }}</button>
        </li>
      </ul>
    </details>
  </main>
</template>

<style scoped>
.reminders { padding: 1.5rem; max-width: 600px; margin: 0 auto; }
.page-head { margin-bottom: 1.25rem; }
.subtitle { color: var(--ink-2); margin-top: 0.25rem; }
.new-reminder { display: flex; flex-direction: column; gap: 0.6rem; background: var(--surface); padding: 1.1rem; border-radius: 12px; margin-bottom: 1.25rem; box-shadow: var(--shadow); border: 1px solid var(--line); }
.new-reminder input, .new-reminder select { padding: 0.55rem; border: 1px solid var(--line-strong); border-radius: 8px; }
.field-row { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.field-row input[type="datetime-local"] { flex: 1; min-width: 180px; }
button { padding: 0.55rem 1rem; background: var(--vert-green); color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
button.link { background: none; color: var(--critical); padding: 0.2rem 0.4rem; font-weight: 500; flex-shrink: 0; }
.error { color: var(--critical); margin: 0; }
.empty-state { text-align: center; padding: 3rem 1rem; background: var(--surface); border-radius: 12px; color: var(--ink-2); }
.reminder-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.reminder-list li { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; background: var(--surface); padding: 0.85rem 1rem; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
.reminder-list li.completed { opacity: 0.6; }
.check { display: flex; gap: 0.6rem; align-items: flex-start; cursor: pointer; }
.check input { margin-top: 0.25rem; }
.check span { display: flex; flex-direction: column; }
.due { font-size: 0.75rem; color: var(--ink-3); }
.desc { font-size: 0.8rem; color: var(--ink-2); }
.done-section { margin-top: 1.5rem; color: var(--ink-2); }
.done-section summary { cursor: pointer; font-size: 0.9rem; margin-bottom: 0.6rem; }
</style>
