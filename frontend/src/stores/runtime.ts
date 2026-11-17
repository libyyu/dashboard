import { defineStore } from 'pinia'
import { ref } from 'vue'
import { http } from '../api/http'
import { ENDPOINTS } from '../api/contract'
import type { RuntimeStatus } from '../api/contract'

// 运行时聚合状态：顶栏状态灯 / console 概览共用。轮询 /api/runtime/status（BEST_EFFORT，可 shed）。
export const useRuntimeStore = defineStore('runtime', () => {
  const loading = ref(false)
  const error = ref('')
  const data = ref<RuntimeStatus | null>(null)
  const lastUpdated = ref(0)
  let timer: number | null = null

  async function refresh() {
    loading.value = true
    error.value = ''
    try {
      data.value = await http<RuntimeStatus>(ENDPOINTS.runtimeStatus)
      lastUpdated.value = Date.now()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  function startPolling(intervalMs = 10000) {
    if (timer !== null) return
    timer = window.setInterval(refresh, intervalMs)
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  return { loading, error, data, lastUpdated, refresh, startPolling, stopPolling }
})
