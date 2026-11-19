import { defineStore } from 'pinia'
import { ref } from 'vue'

// 界面偏好：主题明暗 + 侧边栏宽度。落 localStorage，跨会话保留。
// 侧边栏拖到很窄时自动切换为图标模式（由布局层根据宽度判断，不在此存储显式模式）。

export type ThemeMode = 'light' | 'dark'

const LS_KEY = 'pd.settings.v1'

interface Persisted {
  theme?: ThemeMode
  sidebarWidth?: number
}

function load(): Persisted {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || '{}') as Persisted
  } catch {
    return {}
  }
}

export const useSettingsStore = defineStore('settings', () => {
  const saved = load()
  const theme = ref<ThemeMode>(saved.theme ?? 'light')
  const sidebarWidth = ref<number>(saved.sidebarWidth ?? 220)

  function applyTheme() {
    const root = document.documentElement
    root.setAttribute('data-theme', theme.value)
    root.style.colorScheme = theme.value
  }

  function persist() {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({ theme: theme.value, sidebarWidth: sidebarWidth.value }),
    )
  }

  function setTheme(t: ThemeMode) {
    theme.value = t
    applyTheme()
    persist()
  }

  function setSidebarWidth(w: number) {
    sidebarWidth.value = w
    persist()
  }

  applyTheme()

  return { theme, sidebarWidth, setTheme, setSidebarWidth, applyTheme }
})
