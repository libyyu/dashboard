// 前端契约类型：只描述 HTTP/JSON 形状，不 import 任何后端代码。
// 端点清单与车道以 contracts/api.json 为准，由 scripts/ci_cheap.py 守卫一致性。

export const ENDPOINTS = {
  liveness: '/health/liveness',
  ready: '/health/ready',
  health: '/health',
  ping: '/api/ping',
  logsSources: '/api/logs/sources',
  logsTail: '/api/logs/tail',
  runtimeStatus: '/api/runtime/status',
  runtimeServices: '/api/runtime/services',
  runtimeLogin: '/api/runtime/auth/login',
  agentModels: '/api/agent/models',
  agentRunStream: '/api/agent/runs/stream',
  agentCancel: '/api/agent/runs/{id}/cancel',
  agentSessionEvents: '/api/agent/sessions/{id}/events',
  graphNodes: '/api/graph/nodes',
  search: '/api/search',
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
