// 模型选择状态：单列表按服务分组的模型下拉 + 思考强度，被 ChatInput（输入组件
// 内嵌弹层）与 ChatSession（错误气泡登录引导）共用，其它对话模块也可直接复用。
//
// 数据源：GET /api/model/providers（注册表 KV ∪ 插件扫描）
//        GET /api/model/{provider}/models（OpenAI 兼容 /v1/models 透传，全量并行预取）
// 登录：POST /api/model/{provider}/auth/login（auth=login 的 provider）

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { ComputedRef, Ref } from 'vue'
import type { ModelProviderInfo } from '../../types/chat'
import { http } from '../../api/http'
import { ENDPOINTS } from '../../api/contract'

export type ReasoningEffort = 'low' | 'medium' | 'high'

// 选择态持久化：provider / model / 思考强度 刷新后保持上次选择
const SELECTION_KEY = 'plateau.chat.selection'

interface PersistedSelection {
  provider?: string
  model?: string
  reasoningEffort?: ReasoningEffort
}

function restoreSelection(): PersistedSelection {
  try {
    const raw = localStorage.getItem(SELECTION_KEY)
    if (!raw) return {}
    const v = JSON.parse(raw) as PersistedSelection
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}

function persistSelection(s: PersistedSelection): void {
  try {
    localStorage.setItem(SELECTION_KEY, JSON.stringify(s))
  } catch {
    /* localStorage 不可用时仅保持内存态 */
  }
}

export interface ModelSelection {
  providers: Ref<ModelProviderInfo[]>
  providersLoading: Ref<boolean>
  providersError: Ref<string>
  providerName: Ref<string>
  modelName: Ref<string>
  /** 各 provider 的模型列表（启动/刷新时并行预取，分组下拉直接用） */
  modelsByProvider: Ref<Record<string, string[]>>
  reasoningEffort: Ref<ReasoningEffort>
  currentProvider: ComputedRef<ModelProviderInfo | undefined>
  /** 各 login 型 provider 的登录态（auth/status 探测，ready=已登录） */
  loginStatus: Ref<Record<string, string>>
  loadProviders(): Promise<void>
  onProviderChange(name: string): void
  triggerLogin(provider?: string): Promise<void>
}

export function useModelSelection(): ModelSelection {
  const saved = restoreSelection()
  const providers = ref<ModelProviderInfo[]>([])
  const providersLoading = ref(false)
  const providersError = ref('')
  const providerName = ref('')
  const modelName = ref('')
  const modelsByProvider = ref<Record<string, string[]>>({})
  const reasoningEffort = ref<ReasoningEffort>(saved.reasoningEffort ?? 'medium')
  const loginStatus = ref<Record<string, string>>({})
  let pollTimer: ReturnType<typeof setInterval> | null = null

  const currentProvider = computed(() =>
    providers.value.find((p) => p.name === providerName.value),
  )

  onMounted(() => {
    void loadProviders()
  })

  // 选择变化即持久化（provider/model/思考强度）
  watch([providerName, modelName, reasoningEffort], () => {
    persistSelection({
      provider: providerName.value,
      model: modelName.value,
      reasoningEffort: reasoningEffort.value,
    })
  })

  async function loadProviders(): Promise<void> {
    providersLoading.value = true
    providersError.value = ''
    try {
      const data = await http<{ providers?: ModelProviderInfo[]; default?: string | null }>(
        ENDPOINTS.modelProviders,
      )
      providers.value = data.providers ?? []
      if (!providerName.value) {
        // 首次加载：优先恢复上次选择（provider 仍在列表时），否则回落 default
        if (saved.provider && providers.value.some((p) => p.name === saved.provider)) {
          providerName.value = saved.provider
          modelName.value = saved.model ?? ''
        } else {
          providerName.value = data.default ?? providers.value[0]?.name ?? ''
        }
      }
      void loadAllModels(providers.value)
      // 对 login 型 provider 并行探测登录态（供「去登录」按钮按需显隐）
      for (const p of providers.value) {
        if (p.auth === 'login') void refreshLoginStatus(p.name)
      }
      ensurePolling()
    } catch (e) {
      providersError.value = e instanceof Error ? e.message : '模型服务列表加载失败'
    } finally {
      providersLoading.value = false
    }
  }

  // 全量并行预取各 provider 的模型；单个失败 fail-open 为空列表（分组下拉不阻塞）
  async function loadAllModels(list: ModelProviderInfo[]): Promise<void> {
    const entries = await Promise.all(
      list.map(async (p) => {
        try {
          const data = await http<{ data?: Array<{ id?: string }> }>(
            ENDPOINTS.modelModels.replace('{provider}', encodeURIComponent(p.name)),
          )
          return [p.name, (data.data ?? []).map((m) => String(m.id ?? '')).filter(Boolean)] as const
        } catch {
          return [p.name, []] as const
        }
      }),
    )
    const map: Record<string, string[]> = {}
    for (const [name, models] of entries) map[name] = models
    modelsByProvider.value = map
  }

  function onProviderChange(name: string): void {
    providerName.value = name
    modelName.value = ''
  }

  // 探测单个 login 型 provider 的登录态；不可达记为 unknown（不当作「已登录」）
  async function refreshLoginStatus(name: string): Promise<void> {
    try {
      const data = await http<{ status?: string }>(
        ENDPOINTS.modelAuthStatus.replace('{provider}', encodeURIComponent(name)),
      )
      loginStatus.value = {
        ...loginStatus.value,
        [name]: typeof data.status === 'string' ? data.status : 'unknown',
      }
    } catch {
      loginStatus.value = { ...loginStatus.value, [name]: 'unknown' }
    }
    ensurePolling()
  }

  // 登录态轮询：存在未 ready 的 login 型 provider 时每 4s 探测一次，全部 ready 后停止。
  // 用于「点去登录在浏览器完成授权后」按钮状态自动消失，无需手动刷新。
  function ensurePolling(): void {
    const needPoll = providers.value.some(
      (p) => p.auth === 'login' && (loginStatus.value[p.name] ?? 'unknown') !== 'ready',
    )
    if (needPoll && !pollTimer) {
      pollTimer = setInterval(() => {
        const still = providers.value.some(
          (p) => p.auth === 'login' && (loginStatus.value[p.name] ?? 'unknown') !== 'ready',
        )
        if (!still) {
          if (pollTimer) {
            clearInterval(pollTimer)
            pollTimer = null
          }
          return
        }
        for (const p of providers.value) {
          if (p.auth === 'login' && (loginStatus.value[p.name] ?? 'unknown') !== 'ready') {
            void refreshLoginStatus(p.name)
          }
        }
      }, 4000)
    } else if (!needPoll && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  onUnmounted(() => {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  })

  // 登录引导：cb 为异步跳转流程，触发后刷新登录态与目录展示
  async function triggerLogin(provider?: string): Promise<void> {
    if (!provider) return
    try {
      await http(ENDPOINTS.modelAuthLogin.replace('{provider}', encodeURIComponent(provider)), {
        method: 'POST',
      })
    } catch {
      /* 登录失败保持现状，由错误气泡继续展示 */
    }
    await refreshLoginStatus(provider)
    void loadProviders()
  }

  return {
    providers,
    providersLoading,
    providersError,
    providerName,
    modelName,
    modelsByProvider,
    reasoningEffort,
    currentProvider,
    loginStatus,
    loadProviders,
    onProviderChange,
    triggerLogin,
  }
}
