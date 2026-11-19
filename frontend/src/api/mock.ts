// 离线 mock：VITE_API_MOCK=1 时前端完全不连后端，可独立开发/验证。
// 数据形状遵循 contracts/api.json；不依赖任何后端模块。

import type {
  AuthLoginResult,
  LogSources,
  LogTail,
  PluginList,
  RegistryCatalog,
  RuntimeStatus,
  ServiceList,
} from './contract'

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

const plugins = (): PluginList => ({
  plugins: [
    {
      id: 'cb-openai',
      version: '2.0.0',
      kind: 'sidecar',
      name: 'CB大模型',
      requires: [],
      provides: 'model',
      enabled: true,
      installed_at: now(),
    },
  ],
})

const modelProviders = () => ({
  providers: [
    {
      name: 'cb-openai',
      display_name: 'CB大模型',
      base_url: 'http://127.0.0.1:8708',
      source: 'sidecar',
      api_type: 'agent-events',
      auth: 'login',
      protocols: ['agent-events', 'openai-completions'],
    },
    {
      name: 'zhipu-mock',
      display_name: '智谱（mock）',
      base_url: 'https://open.bigmodel.cn/api/paas/v4',
      source: 'registry',
      api_type: 'openai-completions',
      auth: 'api-key',
    },
  ],
  default: 'cb-openai',
})

const registry = (): RegistryCatalog => ({
  schema: 1,
  plugins: [
    {
      id: 'cb-openai',
      name: 'CB大模型',
      description: 'OpenAI 兼容大模型服务（/v1/models、/v1/chat/completions、/auth/*），sidecar 常驻。',
      kind: 'sidecar',
      latest_version: '2.0.0',
      requires: [],
      provides: 'model',
    },
    {
      id: 'graph-atlas',
      name: '玩法图谱',
      description: '玩法结构可视化与节点关系探索。',
      kind: 'frontend',
      latest_version: '1.0.0',
      requires: ['graph'],
    },
    {
      id: 'text-search',
      name: '文本检索',
      description: '全文检索与内容定位。',
      kind: 'frontend',
      latest_version: '1.0.0',
      requires: ['search'],
    },
  ],
  reachable: true,
})

export function mockResponse<T>(path: string): T {
  const clean = path.split('?')[0]
  // 动态路径：会话消息回放
  const messagesMatch = clean.match(/^\/api\/agent\/sessions\/([^/]+)\/messages$/)
  if (messagesMatch) {
    return {
      session_id: messagesMatch[1],
      messages: [
        { role: 'user', content: '示例：你好', created_at: now() },
        { role: 'assistant', content: '你好，我是离线 mock 助手。', created_at: now() },
      ],
    } as T
  }
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
    case '/api/plugins': data = plugins(); break
    case '/api/plugins/registry': data = registry(); break
    case '/api/model/providers': data = modelProviders(); break
    case '/api/model/cb-openai/models': data = { object: 'list', data: [
      { id: 'glm-4.6', object: 'model', owned_by: 'zhipu' },
      { id: 'glm-4.5-air', object: 'model', owned_by: 'zhipu' },
    ] }; break
    case '/api/model/cb-openai/auth/login': data = { started: true, status: 'ready' } as AuthLoginResult; break
    case '/api/agent/sessions': data = {
      sessions: [
        { session_id: 'mock-session-1', title: '示例对话', last_seq: 3, created_at: now(), updated_at: now() },
      ],
    }; break
    case '/api/agent/files': data = {
      file_id: 'mock-file',
      name: 'upload.bin',
      mime: 'application/octet-stream',
      size: 0,
      url: '/api/agent/files/mock-file',
    }; break
    default: data = {}
  }
  return data as T
}
