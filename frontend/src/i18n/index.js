import { createI18n } from 'vue-i18n'

// Adding a new local language later = drop a new locale file here and add
// its code to SUPPORTED_LOCALES. No component code changes required,
// because components only ever reference translation keys (t('dashboard.title')),
// never literal strings.
import en from './locales/en.json'
import fr from './locales/fr.json'
import pcm from './locales/pcm.json'

export const SUPPORTED_LOCALES = [
  { code: 'en', label: 'English' },
  { code: 'fr', label: 'Français' },
  { code: 'pcm', label: 'Pidgin' }
]

function detectLocale() {
  const saved = localStorage.getItem('vert.locale')
  if (saved && SUPPORTED_LOCALES.some((l) => l.code === saved)) return saved

  const browser = navigator.language?.slice(0, 2)
  if (SUPPORTED_LOCALES.some((l) => l.code === browser)) return browser

  return 'en'
}

export const i18n = createI18n({
  legacy: false,
  locale: detectLocale(),
  fallbackLocale: 'en',
  messages: { en, fr, pcm }
})

export function setLocale(code) {
  i18n.global.locale.value = code
  localStorage.setItem('vert.locale', code)
}
