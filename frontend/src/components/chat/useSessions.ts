// 会话持久化状态：会话列表 + 当前会话 id（localStorage 续接）+ 消息回放。
//
// 数据源：
//   GET /api/agent/sessions                    会话元信息列表
//   GET /api/agent/sessions/{id}/messages      会话消息回放（user/assistant 完整对话）
// 当前会话 id 持久化到 localStorage，刷新/重进后继续同一会话；后端按 session_id
// 落库 user/assistant 消息，因此回放不依赖前端内存态。

import { ref } from 'vue'
import type { Ref } from 'vue'
import type {
  AgentSessionMessages,
  ChatMessage,
  ChatSessionMeta,
  StoredChatMessage,
  ToolCall,
} from '../../types/chat'
import { http } from '../../api/http'
import { ENDPOINTS } from '../../api/contract'

const STORAGE_KEY = 'plateau.chat.sessionId'
const PIN_KEY = 'plateau.chat.pinned'

// 单页消息回放结果：messages 为升序页，hasMore/nextBeforeSeq 驱动「加载更早」真分页
export interface MessagesPage {
  messages: ChatMessage[]
  hasMore: boolean
  nextBeforeSeq: number
}

export interface SessionState {
  sessions: Ref<ChatSessionMeta[]>
  sessionsLoading: Ref<boolean>
  sessionsError: Ref<string>
  activeId: Ref<string>
  /** 已置顶的会话 id 集合 */
  pinned: Ref<Set<string>>
  /** 切换当前会话 id（传空串 = 新建会话）；同时持久化到 localStorage */
  selectSession(id: string): void
  togglePin(id: string): void
  loadSessions(query?: string): Promise<void>
  loadMessages(id: string, beforeSeq?: number): Promise<MessagesPage>
  renameSession(id: string, title: string): Promise<void>
  deleteSession(id: string): Promise<void>
}

function restorePinned(): Set<string> {
  try {
    const raw = localStorage.getItem(PIN_KEY)
    if (!raw) return new Set()
    const arr = JSON.parse(raw) as unknown
    return new Set(Array.isArray(arr) ? (arr as string[]) : [])
  } catch {
    return new Set()
  }
}

function persistPinned(ids: string[]): void {
  try {
    localStorage.setItem(PIN_KEY, JSON.stringify(ids))
  } catch {
    /* localStorage 不可用时仅保持内存态 */
  }
}

export function useSessions(): SessionState {
  const sessions = ref<ChatSessionMeta[]>([])
  const sessionsLoading = ref(false)
  const sessionsError = ref('')
  const activeId = ref(restoreActiveId())
  const pinned = ref<Set<string>>(restorePinned())

  function restoreActiveId(): string {
    try {
      return localStorage.getItem(STORAGE_KEY) || ''
    } catch {
      return ''
    }
  }

  function persistActiveId(id: string): void {
    activeId.value = id
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* localStorage 不可用时仅保持内存态 */
    }
  }

  function selectSession(id: string): void {
    persistActiveId(id)
  }

  function togglePin(id: string): void {
    const next = new Set(pinned.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    pinned.value = next
    persistPinned([...next])
  }

  async function loadSessions(query?: string): Promise<void> {
    sessionsLoading.value = true
    sessionsError.value = ''
    try {
      const q = (query ?? '').trim()
      const url = q
        ? `${ENDPOINTS.agentSessions}?q=${encodeURIComponent(q)}`
        : ENDPOINTS.agentSessions
      const data = await http<{ sessions?: ChatSessionMeta[] }>(url)
      sessions.value = data.sessions ?? []
    } catch (e) {
      sessionsError.value = e instanceof Error ? e.message : '会话列表加载失败'
    } finally {
      sessionsLoading.value = false
    }
  }

  async function loadMessages(id: string, beforeSeq?: number): Promise<MessagesPage> {
    const base = ENDPOINTS.agentSessionMessages.replace('{id}', encodeURIComponent(id))
    const query = beforeSeq !== undefined && beforeSeq >= 0 ? `?before_seq=${beforeSeq}` : ''
    const data = await http<AgentSessionMessages>(base + query)
    return {
      messages: (data.messages ?? []).map(toChatMessage),
      hasMore: data.has_more === true,
      nextBeforeSeq: typeof data.next_before_seq === 'number' ? data.next_before_seq : -1,
    }
  }

  async function renameSession(id: string, title: string): Promise<void> {
    await http(ENDPOINTS.agentSession.replace('{id}', encodeURIComponent(id)), {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    })
    const target = sessions.value.find((s) => s.session_id === id)
    if (target) target.title = title
  }

  async function deleteSession(id: string): Promise<void> {
    await http(ENDPOINTS.agentSession.replace('{id}', encodeURIComponent(id)), {
      method: 'DELETE',
    })
    sessions.value = sessions.value.filter((s) => s.session_id !== id)
    if (activeId.value === id) persistActiveId('')
    if (pinned.value.has(id)) {
      const next = new Set(pinned.value)
      next.delete(id)
      pinned.value = next
      persistPinned([...next])
    }
  }

  return {
    sessions,
    sessionsLoading,
    sessionsError,
    activeId,
    pinned,
    selectSession,
    togglePin,
    loadSessions,
    loadMessages,
    renameSession,
    deleteSession,
  }
}

function toChatMessage(s: StoredChatMessage): ChatMessage {
  const toolCalls: ToolCall[] = (s.tool_calls ?? []).map((c) => ({
    id: c.id,
    name: c.name,
    arguments: c.arguments ?? '',
    result: c.result,
    status: 'done' as const,
  }))
  return {
    id: genId(),
    role: s.role,
    content: s.content ?? '',
    thinking: s.thinking || undefined,
    toolCalls: toolCalls.length ? toolCalls : undefined,
    attachments: s.attachments?.length ? s.attachments.map((a) => ({ ...a })) : undefined,
    status: 'done',
    createdAt: s.created_at ? Date.parse(s.created_at) : Date.now(),
    provider: s.provider || undefined,
    model: s.model || undefined,
  }
}

function genId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return Math.random().toString(36).slice(2)
  }
}
