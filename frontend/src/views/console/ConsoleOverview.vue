<template>
  <div class="console">
    <div class="pd-panel">
      <h2>健康状态</h2>
      <div class="health-hero">
        <HealthIndicator :health="health" :loading="runtime.loading" />
        <span v-if="reasonText" class="health-reason">{{ reasonText }}</span>
      </div>
      <div v-if="runtime.error" class="pd-note">状态获取失败：{{ runtime.error }}</div>
    </div>

    <div class="pd-panel">
      <h2>概览</h2>
      <div class="kv">
        <div class="kv-item"><b>服务</b><span>{{ st?.service || '—' }}</span></div>
        <div class="kv-item"><b>版本</b><span>{{ st?.version || '—' }}</span></div>
        <div class="kv-item"><b>Profile</b><span>{{ st?.profile || '—' }}</span></div>
        <div class="kv-item"><b>端口</b><span>{{ portsText }}</span></div>
        <div class="kv-item"><b>日志目录</b><span class="mono">{{ st?.logs_dir || '—' }}</span></div>
        <div class="kv-item"><b>日志保留</b><span>{{ st?.retention_days ?? '—' }} 天</span></div>
      </div>
    </div>

    <div class="pd-panel">
      <h2>服务</h2>
      <el-table :data="services" v-loading="servicesLoading" size="default">
        <el-table-column prop="name" label="服务" width="140" />
        <el-table-column label="必需" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.required" size="small" type="danger" effect="light">必需</el-tag>
            <span v-else class="pd-hint">可选</span>
          </template>
        </el-table-column>
        <el-table-column prop="port" label="端口" width="90" />
        <el-table-column prop="health_path" label="健康检查" />
        <el-table-column prop="ready_path" label="就绪检查" />
      </el-table>
    </div>

    <div class="pd-panel">
      <h2>cb 登录</h2>
      <template v-if="!st?.cb.configured">
        <div class="pd-note">cb 未配置，当前为独立运行模式（standalone）。</div>
      </template>
      <template v-else-if="st?.cb.login_status === 'ready'">
        <div class="login-ok">已登录：<b>{{ userName }}</b></div>
      </template>
      <template v-else>
        <div class="login-row">
          <el-button type="primary" :loading="loginLoading" @click="login">登录 cb</el-button>
          <span class="pd-hint">登录状态：{{ loginStatusText }}</span>
          <span v-if="loginError" class="login-err">{{ loginError }}</span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import HealthIndicator from '../../components/HealthIndicator.vue'
import { useRuntimeStore } from '../../stores/runtime'
import { http } from '../../api/http'
import { ENDPOINTS } from '../../api/contract'
import type { AuthLoginResult, ServiceInfo, ServiceList } from '../../api/contract'

const runtime = useRuntimeStore()

const st = computed(() => runtime.data)
const health = computed(() => st.value?.health ?? null)

const reasonText = computed(() => {
  switch (health.value) {
    case 'degraded': return 'cb 不可用'
    case 'unavailable': return 'dashboard 服务不可用'
    case 'standalone': return 'cb 未配置，不影响主工作台使用'
    default: return ''
  }
})

const portsText = computed(() => {
  const ports = st.value?.ports
  if (!ports) return '—'
  const entries = Object.entries(ports)
  return entries.length ? entries.map(([k, v]) => `${k}: ${v}`).join(' · ') : '—'
})

const userName = computed(() => {
  const auth = st.value?.cb.auth
  const name = auth && typeof auth === 'object' ? (auth as Record<string, unknown>).user_name : undefined
  return typeof name === 'string' ? name : '—'
})

const loginStatusText = computed(() => st.value?.cb.login_status ?? 'unknown')

const services = ref<ServiceInfo[]>([])
const servicesLoading = ref(false)
const loginLoading = ref(false)
const loginError = ref('')

async function loadServices() {
  servicesLoading.value = true
  try {
    const res = await http<ServiceList>(ENDPOINTS.runtimeServices)
    services.value = res.services
  } catch {
    services.value = []
  } finally {
    servicesLoading.value = false
  }
}

async function login() {
  loginLoading.value = true
  loginError.value = ''
  try {
    const res = await http<AuthLoginResult>(ENDPOINTS.runtimeLogin, { method: 'POST' })
    if (res.auth_url) window.open(res.auth_url, '_blank', 'noopener')
    await runtime.refresh()
  } catch (e) {
    loginError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loginLoading.value = false
  }
}

onMounted(loadServices)
</script>

<style scoped>
.health-hero { display: flex; align-items: center; gap: 14px; }
.health-reason { color: var(--pd-muted); font-size: 13px; }
.kv { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 24px; }
.kv-item { display: flex; flex-direction: column; font-size: 13px; }
.kv-item b { color: var(--pd-muted); font-weight: 500; font-size: 12px; margin-bottom: 2px; }
.mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 12px; word-break: break-all; }
.login-ok { font-size: 13px; }
.login-row { display: flex; align-items: center; gap: 12px; }
.login-err { color: #d9483b; font-size: 12px; }
</style>
