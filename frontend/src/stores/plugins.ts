import { defineStore } from 'pinia'
import { ref } from 'vue'
import { http } from '../api/http'
import { ENDPOINTS } from '../api/contract'
import type { CliEnvCheck, PluginInfo, PluginInstallProgress, PluginList, RegistryCatalog } from '../api/contract'

// 插件运行时状态：本机已装清单 + registry 可安装清单。
// 轮询 /api/plugins + /api/plugins/registry（BEST_EFFORT，可 shed）。
export const usePluginsStore = defineStore('plugins', () => {
  const loading = ref(false)
  const error = ref('')
  const installed = ref<PluginInfo[]>([])
  const catalog = ref<RegistryCatalog | null>(null)
  const installing = ref<string | null>(null)
  const progress = ref<PluginInstallProgress | null>(null)
  const envCheck = ref<CliEnvCheck | null>(null)
  const checkingEnv = ref(false)
  let timer: number | null = null
  let progressTimer: number | null = null

  async function pollProgress(id: string) {
    try {
      const state = await http<PluginInstallProgress>(
        `${ENDPOINTS.pluginsInstallProgress}?id=${encodeURIComponent(id)}`,
      )
      progress.value = state
    } catch {
      // 进度查询失败不打断安装主流程
    }
  }

  function startProgressPolling(id: string) {
    stopProgressPolling()
    progress.value = { active: true, stage: 'resolve', message: '开始安装…' }
    progressTimer = window.setInterval(() => pollProgress(id), 400)
  }

  function stopProgressPolling() {
    if (progressTimer !== null) {
      clearInterval(progressTimer)
      progressTimer = null
    }
  }

  async function checkEnv() {
    if (checkingEnv.value) return
    checkingEnv.value = true
    try {
      envCheck.value = await http<CliEnvCheck>(ENDPOINTS.pluginsCliEnvCheck)
    } catch {
      // 环境检查失败不打断主流程（保留上一次结果）
    } finally {
      checkingEnv.value = false
    }
  }

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
    // 目录拉取后自动检查运行环境（不阻塞目录展示）
    void checkEnv()
  }

  async function install(id: string, version = ''): Promise<boolean> {
    installing.value = id
    error.value = ''
    startProgressPolling(id)
    try {
      await http(ENDPOINTS.pluginsInstall, {
        method: 'POST',
        body: JSON.stringify({ id, version }),
      })
      await refresh()
      await checkEnv()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return false
    } finally {
      stopProgressPolling()
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
      await checkEnv()
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
    progress,
    envCheck,
    checkingEnv,
    refresh,
    checkEnv,
    install,
    uninstall,
    isInstalled,
    startPolling,
    stopPolling,
  }
})
