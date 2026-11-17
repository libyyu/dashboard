// 离线 mock：VITE_API_MOCK=1 时前端完全不连后端，可独立开发/验证。
// 数据形状遵循 contracts/api.json；不依赖任何后端模块。

import type { AuthLoginResult, LogSources, LogTail, RuntimeStatus, ServiceList } from './contract'

const now = () => new Date().toISOString()

const runtimeStatus = (): RuntimeStatus => ({
  service: 'plateau-dashboard',
  version: '1.0.0',
  profile: 'default',
  ports: { dashboard: 8580, cb: 8677 },
  health: 'healthy',
  reason: '',
  dashboard: { status: 'healthy', ready: true },
  cb: {
    configured: true,
    available: true,
    login_status: 'ready',
    health: { status: 'ok', service: 'cbopenai' },
    auth: { status: 'ready', user_name: 'mock-user' },
  },
  logs_dir: '~/.plateau-dashboard/profiles/default/logs',
  retention_days: 14,
})

const services = (): ServiceList => ({
  profile: 'default',
  home: '~/.plateau-dashboard',
  logs_dir: '~/.plateau-dashboard/profiles/default/logs',
  services: [
    { name: 'dashboard', required: true, port: 8580, health_path: '/health/liveness', ready_path: '/health/ready' },
    { name: 'cb', required: false, port: 8677, health_path: '/health', ready_path: null },
  ],
})

const logSources = (): LogSources => ({
  root: '~/.plateau-dashboard/profiles/default/logs',
  retention_days: 14,
  sources: [
    { name: 'dashboard-2026-11-17.log', size: 2048, mtime: now() },
    { name: 'launcher-2026-11-17.log', size: 1024, mtime: now() },
  ],
})

const logTail = (): LogTail => ({
  source: 'dashboard-2026-11-17.log',
  exists: true,
  truncated: false,
  lines: [
    { ts: now(), level: 'INFO', service: 'dashboard', event: 'http_request', message: 'http_request', request_id: 'mock00000001', duration_ms: 12.5, lane: 'control' },
    { ts: now(), level: 'INFO', service: 'dashboard', logger: 'uvicorn.error', message: 'Application startup complete.' },
  ],
})

export function mockResponse<T>(path: string): T {
  const clean = path.split('?')[0]
  let data: unknown
  switch (clean) {
    case '/health/liveness': data = { status: 'alive', service: 'plateau-dashboard', pid: 0 }; break
    case '/health/ready': data = { status: 'ready', service: 'plateau-dashboard', phase: 'routers_mounted' }; break
    case '/health': data = { status: 'healthy', service: 'plateau-dashboard' }; break
    case '/api/ping': data = { ok: true, service: 'plateau-dashboard' }; break
    case '/api/logs/sources': data = logSources(); break
    case '/api/logs/tail': data = logTail(); break
    case '/api/runtime/status': data = runtimeStatus(); break
    case '/api/runtime/services': data = services(); break
    case '/api/runtime/auth/login': data = { started: true, status: 'ready' } as AuthLoginResult; break
    default: data = {}
  }
  return data as T
}
