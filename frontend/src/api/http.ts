// 统一 HTTP 客户端：契约层，不依赖后端实现，也不依赖具体页面。
// - VITE_API_MOCK=1 时完全离线，前后端可各自独立验证
// - 超时 / 取消 / request-id 透传 / 统一错误形状 / shed 与 degraded 语义
// - 只重试幂等 GET 的 shed 与 5xx，4xx 不重试

import type { ApiErrorBody, HttpMeta } from './contract'
import { mockResponse } from './mock'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''
const DEFAULT_TIMEOUT = Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 15000)
const MOCK = import.meta.env.VITE_API_MOCK === '1'

export interface ApiErrorOptions {
  code: string
  status: number
  requestId: string
  lane: string
  shed: boolean
  retryAfter: number
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly requestId: string
  readonly lane: string
  readonly shed: boolean
  readonly retryAfter: number

  constructor(message: string, options: ApiErrorOptions) {
    super(message)
    this.name = 'ApiError'
    this.code = options.code
    this.status = options.status
    this.requestId = options.requestId
    this.lane = options.lane
    this.shed = options.shed
    this.retryAfter = options.retryAfter
  }
}

export interface HttpOptions extends RequestInit {
  timeoutMs?: number
  retry?: number
}

let lastMeta: HttpMeta = { status: 0, requestId: '', lane: '', shed: false, retryAfter: 0 }

export function getLastMeta(): HttpMeta {
  return { ...lastMeta }
}

export function getLastRequestId(): string {
  return lastMeta.requestId
}

function newRequestId(): string {
  try {
    return crypto.randomUUID().replace(/-/g, '').slice(0, 16)
  } catch {
    return Math.random().toString(16).slice(2, 18).padEnd(16, '0')
  }
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function parseError(
  status: number,
  data: unknown,
  base: Omit<ApiErrorOptions, 'code' | 'shed'> & { code?: string; shed: boolean },
): ApiError {
  const obj = asObject(data)
  const raw = obj.error
  let code = base.code ?? 'HTTP_ERROR'
  let message = `请求失败 HTTP ${status}`
  let requestId = base.requestId
  if (typeof raw === 'string') {
    message = raw
  } else if (raw && typeof raw === 'object') {
    const err = raw as Partial<ApiErrorBody>
    code = typeof err.code === 'string' ? err.code : code
    message = typeof err.message === 'string' ? err.message : message
    if (typeof err.request_id === 'string') requestId = err.request_id
  }
  if (message === `请求失败 HTTP ${status}` && typeof obj.detail === 'string') {
    message = obj.detail
  }
  if (base.shed) code = 'GATEWAY_OVERLOAD_SHED'
  return new ApiError(message, { ...base, code, requestId })
}

export async function http<T = unknown>(path: string, options: HttpOptions = {}): Promise<T> {
  const { timeoutMs, retry = 1, ...init } = options
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const requestId = headers.get('x-request-id') ?? newRequestId()
  headers.set('x-request-id', requestId)

  if (MOCK) {
    lastMeta = { status: 200, requestId, lane: 'mock', shed: false, retryAfter: 0 }
    return mockResponse<T>(path)
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs ?? DEFAULT_TIMEOUT)
  if (init.signal) {
    init.signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  let resp: Response
  try {
    resp = await fetch(BASE_URL + path, { ...init, headers, signal: controller.signal })
  } catch {
    const aborted = controller.signal.aborted
    const code = aborted ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR'
    lastMeta = { status: 0, requestId, lane: '', shed: false, retryAfter: 0 }
    throw new ApiError(aborted ? '请求超时' : '网络不可用', {
      code, status: 0, requestId, lane: '', shed: false, retryAfter: 0,
    })
  } finally {
    clearTimeout(timer)
  }

  const lane = resp.headers.get('x-plateau-lane') ?? ''
  const shed = resp.headers.get('x-plateau-shed') === '1'
  const retryAfter = Number(resp.headers.get('retry-after') ?? '0') || 0
  const responseId = resp.headers.get('x-request-id') ?? requestId
  lastMeta = { status: resp.status, requestId: responseId, lane, shed, retryAfter }

  let data: unknown = null
  const text = await resp.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }

  const obj = asObject(data)
  if (!resp.ok || obj.ok === false) {
    const apiError = parseError(resp.status, data, {
      status: resp.status, requestId: responseId, lane, shed, retryAfter, code: 'HTTP_ERROR',
    })
    const method = (init.method ?? 'GET').toUpperCase()
    const retryable = method === 'GET' && (apiError.shed || apiError.status >= 502)
    if (retryable && retry > 0) {
      const delay = apiError.retryAfter > 0 ? apiError.retryAfter * 1000 : 500
      await new Promise((resolve) => setTimeout(resolve, delay))
      return http<T>(path, { ...options, retry: retry - 1 })
    }
    throw apiError
  }

  return data as T
}
