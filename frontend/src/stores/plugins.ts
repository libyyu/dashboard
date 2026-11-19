import { defineStore } from 'pinia'
import { ref } from 'vue'
import { http } from '../api/http'
import { ENDPOINTS } from '../api/contract'
import type { PluginInfo, PluginList, RegistryCatalog } from '../api/contract'

// 插件运行时状态：本机已装清单 + registry 可安装清单。
// 轮询 /api/plugins + /api/plugins/registry（BEST_EFFORT，可 shed）。
export const usePluginsStore = defineStore('plugins', () => {
  const loading = ref(false)
  const error = ref('')
  const installed = ref<PluginInfo[]>([])
  const catalog = ref<RegistryCatalog | null>(null)
  const installing = ref<string | null>(null)
  let timer: number | null = null

  async function refresh() {
    loading.value = true
    error.value = ''
    try {
      const [list, registry] = await Promise.all([
        http<PluginList>(ENDPOINTS.plugins),
        http<RegistryCatalog>(ENDPOINTS.pluginsRegistry),
      ])
      installed.value = list.plugins ?? []
      catalog.value = registry
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function install(id: string, version = ''): Promise<boolean> {
    installing.value = id
    error.value = ''
    try {
      await http(ENDPOINTS.pluginsInstall, {
        method: 'POST',
        body: JSON.stringify({ id, version }),
      })
      await refresh()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return false
    } finally {
      installing.value = null
    }
  }

  async function uninstall(id: string): Promise<boolean> {
    installing.value = id
    error.value = ''
    try {
      await http(ENDPOINTS.pluginsUninstall, {
        method: 'POST',
        body: JSON.stringify({ id }),
      })
      await refresh()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return false
    } finally {
      installing.value = null
    }
  }

  function isInstalled(id: string): boolean {
    return installed.value.some((p) => p.id === id)
  }

  function startPolling(intervalMs = 30000) {
    if (timer !== null) return
    timer = window.setInterval(refresh, intervalMs)
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  return {
    loading,
    error,
    installed,
    catalog,
    installing,
    refresh,
    install,
    uninstall,
    isInstalled,
    startPolling,
    stopPolling,
  }
})
