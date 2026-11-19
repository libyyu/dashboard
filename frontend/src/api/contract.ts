// 前端契约类型：只描述 HTTP/JSON 形状，不 import 任何后端代码。
// 端点清单与车道以 contracts/api.json 为准，由 scripts/ci_cheap.py 守卫一致性。

import type { AgentEvent } from '../types/chat'

export const ENDPOINTS = {
  liveness: '/health/liveness',
  ready: '/health/ready',
  health: '/health',
  ping: '/api/ping',
  logsSources: '/api/logs/sources',
  logsTail: '/api/logs/tail',
  runtimeStatus: '/api/runtime/status',
  runtimeServices: '/api/runtime/services',
  runtimeStorage: '/api/runtime/storage',
  runtimeLogin: '/api/runtime/auth/login',
  modelProviders: '/api/model/providers',
  modelModels: '/api/model/{provider}/models',
  modelModelDetail: '/api/model/{provider}/models/{model_id}',
  modelChat: '/api/model/{provider}/chat/completions',
  modelAuthStatus: '/api/model/{provider}/auth/status',
  modelAuthLogin: '/api/model/{provider}/auth/login',
  agentRunStream: '/api/agent/runs/stream',
  agentCancel: '/api/agent/runs/{id}/cancel',
  agentSessions: '/api/agent/sessions',
  agentSession: '/api/agent/sessions/{id}',
  agentSessionMessages: '/api/agent/sessions/{id}/messages',
  agentSessionEvents: '/api/agent/sessions/{id}/events',
  agentFiles: '/api/agent/files',
  agentFile: '/api/agent/files/{file_id}',
  graphNodes: '/api/graph/nodes',
  search: '/api/search',
  plugins: '/api/plugins',
  pluginsCapabilities: '/api/plugins/capabilities',
  pluginsInstall: '/api/plugins/install',
  pluginsUninstall: '/api/plugins/uninstall',
  pluginsRegistry: '/api/plugins/registry',
} as const

export type HealthLevel = 'healthy' | 'degraded' | 'standalone' | 'unavailable'
export type LoginStatus = 'unknown' | 'pending' | 'ready' | 'failed'

export interface RuntimeStatus {
  service: string
  version: string
  profile: string
  ports: Record<string, number>
  health: HealthLevel
  reason: string
  dashboard: { status: string; ready: boolean }
  cb: {
    configured: boolean
    available: boolean
    login_status: LoginStatus
    health: Record<string, unknown> | null
    auth: Record<string, unknown> | null
  }
  logs_dir: string
  retention_days: number
}

export interface ServiceInfo {
  name: string
  required: boolean
  port: number | null
  health_path: string | null
  ready_path: string | null
}

export interface ServiceList {
  profile: string
  home: string
  logs_dir: string
  services: ServiceInfo[]
}

export interface LogSource {
  name: string
  size: number
  mtime: string
}

export interface LogSources {
  root: string
  retention_days: number
  sources: LogSource[]
}

export interface LogEntry {
  ts?: string
  level?: string
  logger?: string
  service?: string
  message?: string
  request_id?: string
  event?: string
  [key: string]: unknown
}

export interface LogTail {
  source: string
  exists: boolean
  truncated: boolean
  lines: LogEntry[]
}

export interface AuthLoginResult {
  started: boolean
  status?: LoginStatus
  auth_url?: string
  user_name?: string
  error?: string
}

export interface PluginMenu {
  title: string
  path: string
  group: string
  icon?: string
}

export interface PluginRoute {
  path: string
  entry: string
}

export interface PluginInfo {
  id: string
  version: string
  kind: 'frontend' | 'sidecar'
  name?: string
  requires: string[]
  provides?: string
  enabled: boolean
  installed_at?: string
  menu?: PluginMenu
  routes?: PluginRoute[]
}

export interface PluginList {
  plugins: PluginInfo[]
}

export interface RegistryPlugin {
  id: string
  name?: string
  description?: string
  kind?: 'frontend' | 'sidecar'
  latest_version?: string
  requires_core?: string
  requires?: string[]
  manifest_url?: string
  [key: string]: unknown
}

export interface RegistryCatalog {
  schema: number
  plugins: RegistryPlugin[]
  reachable?: boolean
}

export interface CapabilityInfo {
  id: string
  name: string
  provider: string | null
  satisfied: boolean
}

export interface CapabilityList {
  capabilities: CapabilityInfo[]
}

export interface ApiErrorBody {
  code: string
  message: string
  request_id?: string
  [key: string]: unknown
}

export interface HttpMeta {
  status: number
  requestId: string
  lane: string
  shed: boolean
  retryAfter: number
}

export interface AgentSessionEvents {
  session_id: string
  after_seq: number
  events: AgentEvent[]
}
