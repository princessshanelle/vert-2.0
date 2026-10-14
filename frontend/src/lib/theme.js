// Light / dark / follow-the-system, remembered per browser. Charts read
// their colours from the CSS tokens, so they watch `themeVersion` and
// rebuild when the theme (or the OS setting) changes.
import { ref } from 'vue'

const KEY = 'vert.theme'
export const themeMode = ref(localStorage.getItem(KEY) || 'auto') // 'auto' | 'light' | 'dark'
export const themeVersion = ref(0)

function apply() {
  const root = document.documentElement
  if (themeMode.value === 'auto') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', themeMode.value)
  themeVersion.value++
}

export function setTheme(mode) {
  themeMode.value = mode
  localStorage.setItem(KEY, mode)
  apply()
}

export function initTheme() {
  apply()
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
    if (themeMode.value === 'auto') themeVersion.value++
  })
}

/** Current value of a CSS custom property, e.g. cssVar('--series-temp'). */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
