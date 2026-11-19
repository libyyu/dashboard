// 流式传输 composable：SSE 解析 / 增量拼接 / 取消。
//
// 消费 POST /api/agent/runs/stream 的 AgentEvent 流（SSE，帧带 id:{seq}），
// 用 eventsource-parser 切帧，把后端统一事件（run_started / thinking_delta /
// message_delta / tool_call / tool_result / run_done / error / cancelled）
// 增量拼接为「正在生成的 assistant 消息」的各个片段（content / thinking / toolCalls），
// 由 ChatSession 组合进 messages。
//
// 与页面解耦：只依赖 types/chat 与 api 契约，不 import 任何视图。

import { ref } from 'vue'
import {
  createParser,
  type EventSourceMessage,
  type EventSourceParser,
  type ParseError,
} from 'eventsource-parser'
import type { AgentEvent, AgentSessionEvents, ChatSendRequest, ToolCall } from '../../types/chat'
import { ENDPOINTS } from '../../api/contract'
import { BASE_URL, MOCK, http, newRequestId } from '../../api/http'

export type StreamStatus =
  | 'idle'
  | 'connecting'
  | 'streaming'
  | 'done'
  | 'error'
  | 'cancelled'

export interface RunDonePayload {
  finish_reason?: string
  usage?: unknown
  model?: string
}

// 终端态：进入这些状态后本次 run 结束，可提交到消息列表
const TERMINAL: readonly StreamStatus[] = ['done', 'error', 'cancelled']

function asString(value: unknown, key = 'text'): string {
  if (value && typeof value === 'object') {
    const v = (value as Record<string, unknown>)[key]
    return typeof v === 'string' ? v : ''
  }
  return ''
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

export function useChatStream() {
  const status = ref<StreamStatus>('idle')
  const runId = ref('')
  const error = ref('')

  // 正在生成的 assistant 消息片段
  const content = ref('')
  const thinking = ref('')
  const toolCalls = ref<ToolCall[]>([])
  const donePayload = ref<RunDonePayload | null>(null)
  // 契约错误码（error 事件 payload.code），如 PROVIDER_AUTH_REQUIRED 供登录引导
  const errorCode = ref('')
  // 本轮 run 使用的 provider（run_started 回填）
  const activeProvider = ref('')
  const activeModel = ref('')
  // 本轮生成耗时（毫秒）
  const durationMs = ref(0)
  // 最近一次收到的 seq（断点续读的游标）
  const lastSeq = ref(-1)

  let controller: AbortController | null = null
  let startedAt = 0

  function reset() {
    if (controller) {
      controller.abort()
      controller = null
    }
    status.value = 'idle'
    runId.value = ''
    error.value = ''
    errorCode.value = ''
    content.value = ''
    thinking.value = ''
    toolCalls.value = []
    donePayload.value = null
    activeProvider.value = ''
    activeModel.value = ''
    durationMs.value = 0
    lastSeq.value = -1
    startedAt = 0
  }

  function upsertToolCall(payload: Record<string, unknown>) {
    const id = typeof payload.id === 'string' ? payload.id : ''
    const name = typeof payload.name === 'string' ? payload.name : ''
    const argumentsText = typeof payload.arguments === 'string' ? payload.arguments : ''
    if (!id && !name) return
    const existing = toolCalls.value.find((c) => c.id === id || (c.id === '' && c.name === name))
    if (existing) {
      existing.arguments = argumentsText || existing.arguments
    } else {
      toolCalls.value.push({
        id,
        name,
        arguments: argumentsText,
        status: 'running',
      })
    }
  }

  function applyToolResult(payload: Record<string, unknown>) {
    const id = typeof payload.id === 'string' ? payload.id : ''
    const name = typeof payload.name === 'string' ? payload.name : ''
    const result = typeof payload.result === 'string' ? payload.result : ''
    // 先按 id 匹配；id 缺失时回退按 name 匹配（与 upsertToolCall 的 name 兜底对齐）
    let target = toolCalls.value.find((c) => c.id === id && id !== '')
    if (!target && name) {
      target = toolCalls.value.find((c) => c.name === name)
    }
    if (target) {
      target.result = result
      target.status = 'done'
    }
  }

  function applyEvent(ev: AgentEvent) {
    if (typeof ev.seq === 'number' && ev.seq > lastSeq.value) lastSeq.value = ev.seq
    if (!runId.value && ev.run_id) runId.value = ev.run_id
    const payload = ev.payload ?? {}
    switch (ev.type) {
      case 'run_started':
        status.value = 'streaming'
        activeProvider.value = typeof payload.provider === 'string' ? payload.provider : ''
        activeModel.value = typeof payload.model === 'string' ? payload.model : ''
        break
      case 'thinking_delta':
        thinking.value += asString(payload, 'text')
        break
      case 'message_delta':
        content.value += asString(payload, 'text')
        break
      case 'tool_call':
        upsertToolCall(payload)
        break
      case 'tool_result':
        applyToolResult(payload)
        break
      case 'run_done':
        donePayload.value = {
          finish_reason: typeof payload.finish_reason === 'string' ? payload.finish_reason : undefined,
          usage: payload.usage,
          model: typeof payload.model === 'string' ? payload.model : undefined,
        }
        if (typeof payload.model === 'string' && payload.model) activeModel.value = payload.model
        if (startedAt) durationMs.value = Date.now() - startedAt
        status.value = 'done'
        break
      case 'error':
        error.value = asString(payload, 'message') || '流式响应出错'
        errorCode.value = typeof payload.code === 'string' ? payload.code : ''
        status.value = 'error'
        break
      case 'cancelled':
        status.value = 'cancelled'
        break
    }
  }

  function parseEvent(msg: EventSourceMessage): void {
    // 后端 SSE 帧：id:{seq}\ndata:{AgentEvent JSON}，无 event: 字段；类型在 data 里。
    if (!msg.data) return
    let ev: AgentEvent
    try {
      ev = JSON.parse(msg.data) as AgentEvent
    } catch {
      return
    }
    if (!ev || typeof ev !== 'object' || typeof ev.type !== 'string') return
    applyEvent(ev)
  }

  async function send(request: ChatSendRequest): Promise<void> {
    reset()
    status.value = 'connecting'
    startedAt = Date.now()

    if (MOCK) {
      await runMock(request)
      return
    }

    const abort = new AbortController()
    controller = abort
    const parser: EventSourceParser = createParser({
      onEvent: parseEvent,
      onError: (e: ParseError) => {
        // 解析错误不中断整体流程，仅记录到 error（若尚未进入终端态）
        if (!TERMINAL.includes(status.value)) {
          error.value = `SSE 解析错误：${e.message}`
        }
      },
    })

    let resp: Response
    try {
      resp = await fetch(BASE_URL + ENDPOINTS.agentRunStream, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-request-id': newRequestId(),
        },
        body: JSON.stringify(request),
        signal: abort.signal,
      })
    } catch (e) {
      if (abort.signal.aborted) {
        status.value = 'cancelled'
      } else {
        status.value = 'error'
        error.value = e instanceof Error ? e.message : '网络不可用'
      }
      return
    }

    if (!resp.ok || !resp.body) {
      status.value = 'error'
      const errInfo = await readError(resp)
      error.value = errInfo.text
      errorCode.value = errInfo.code
      return
    }

    status.value = 'streaming'
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let interrupted = false
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        parser.feed(decoder.decode(value, { stream: true }))
      }
      parser.feed(decoder.decode())
    } catch {
      if (abort.signal.aborted) {
        status.value = 'cancelled'
      } else {
        // 读中断：先标记，尝试 after_seq 断点续读（后台 run 可能仍在跑）
        interrupted = true
      }
    } finally {
      try {
        reader.releaseLock()
      } catch {
        /* noop */
      }
    }

    // 流异常中断或未收到 run_done：先尝试按 after_seq 断点续读，兜底再置错误
    if (interrupted || status.value === 'streaming') {
      await recover(request.session_id)
      if (!TERMINAL.includes(status.value)) {
        status.value = 'error'
        if (!error.value) error.value = '流提前结束'
      }
    }
  }

  async function readError(resp: Response): Promise<{ text: string; code: string }> {
    try {
      const text = await resp.text()
      if (!text) return { text: `请求失败 HTTP ${resp.status}`, code: '' }
      const obj = JSON.parse(text)
      const err = asObject(obj).error
      if (err && typeof err === 'object') {
        const record = err as Record<string, unknown>
        const message = typeof record.message === 'string' ? record.message : ''
        const code = typeof record.code === 'string' ? record.code : ''
        return { text: message || `请求失败 HTTP ${resp.status}`, code }
      }
      const detail = asObject(obj).detail
      if (typeof detail === 'string') return { text: detail, code: '' }
      return { text: `请求失败 HTTP ${resp.status}`, code: '' }
    } catch {
      return { text: `请求失败 HTTP ${resp.status}`, code: '' }
    }
  }

  // 断点续读：SSE 连接中断后，按 after_seq 拉取后台 run 已持久化的剩余事件，
  // 继续拼接直到终态（run 在后台线程跑完，不受连接断开影响）。
  async function recover(sessionId: string): Promise<void> {
    if (!sessionId || MOCK) return
    for (let attempt = 0; attempt < 120; attempt++) {
      let data: AgentSessionEvents
      try {
        data = await http<AgentSessionEvents>(
          `${ENDPOINTS.agentSessionEvents.replace('{id}', encodeURIComponent(sessionId))}?after_seq=${lastSeq.value}&limit=200`,
          { timeoutMs: 5000 },
        )
      } catch {
        await sleep(400)
        continue
      }
      const events = data.events ?? []
      for (const ev of events) applyEvent(ev)
      if (TERMINAL.includes(status.value)) return
      await sleep(500)
    }
  }

  async function cancel(): Promise<void> {
    const id = runId.value
    if (controller) {
      controller.abort()
      controller = null
    }
    if (status.value !== 'idle' && !TERMINAL.includes(status.value)) {
      status.value = 'cancelled'
    }
    if (id && !MOCK) {
      // 通知服务端协作式取消（fire-and-forget，不阻塞 UI）
      void fetch(BASE_URL + ENDPOINTS.agentCancel.replace('{id}', encodeURIComponent(id)), {
        method: 'POST',
        headers: { 'x-request-id': newRequestId() },
      }).catch(() => {
        /* 取消失败不阻塞本地状态 */
      })
    }
  }

  // 离线 mock：VITE_API_MOCK=1 时模拟一段流式输出，保持前后端可独立验证
  async function runMock(request: ChatSendRequest): Promise<void> {
    runId.value = 'mock-run'
    status.value = 'streaming'
    const last = request.messages[request.messages.length - 1]
    const echo = typeof last?.content === 'string' ? last.content : ''
    const parts = [`收到你的消息：${echo}`, '，这是离线 mock 的流式回复。']
    for (const part of parts) {
      for (const ch of part) {
        content.value += ch
        await sleep(12)
      }
    }
    donePayload.value = { finish_reason: 'stop', model: 'mock' }
    if (startedAt) durationMs.value = Date.now() - startedAt
    status.value = 'done'
  }

  return {
    status,
    runId,
    error,
    errorCode,
    content,
    thinking,
    toolCalls,
    donePayload,
    activeProvider,
    activeModel,
    durationMs,
    send,
    cancel,
    reset,
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
