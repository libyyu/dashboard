<template>
  <div class="chat-session">
    <!-- 未配置模型服务时的空态引导 -->
    <div v-if="showModelGuide" class="model-guide">
      <div class="guide-mark" aria-hidden="true">▲</div>
      <div class="guide-title">还没有可用的模型服务</div>
      <p class="guide-text">
        AI 助手需要至少一个模型服务才能对话。可到插件仓库安装 cb 大模型插件，
        或配置外部 OpenAI 兼容服务。
      </p>
      <div class="guide-actions">
        <el-button type="primary" @click="goPlugins">去插件仓库安装</el-button>
        <el-button :loading="selection.providersLoading.value" @click="recheck">重新检测</el-button>
      </div>
    </div>

    <template v-else>
      <aside class="session-side">
        <div class="side-head">
          <div class="side-head-row">
            <el-button type="primary" size="small" :icon="Plus" class="side-new" @click="newSession">
              新对话
            </el-button>
            <el-dropdown trigger="click" @command="onExport">
              <el-button size="small" text :icon="Download" class="side-export" title="导出当前会话" />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="md">导出 Markdown</el-dropdown-item>
                  <el-dropdown-item command="json">导出 JSON</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <el-input
            v-model="search"
            size="small"
            clearable
            :prefix-icon="Search"
            placeholder="搜索会话"
            class="side-search"
          />
        </div>
        <div v-if="sessionsError" class="side-err">{{ sessionsError }}</div>
        <div v-loading="sessionsLoading" class="side-list">
          <template v-for="g in groupedSessions" :key="g.label">
            <div class="side-group">{{ g.label }}</div>
            <div
              v-for="s in g.items"
              :key="s.session_id"
              class="side-item"
              :class="{ active: s.session_id === currentSessionId }"
            >
              <div class="side-main" @click="switchSession(s.session_id)">
                <span class="side-title">
                  <el-icon v-if="pinned.has(s.session_id)" class="pin-ic"><Top /></el-icon>
                  <span class="side-title-text">{{ s.title || '未命名会话' }}</span>
                </span>
                <span class="side-time">{{ relTime(s.updated_at) }}</span>
              </div>
              <el-dropdown trigger="click" @command="(cmd: string) => onSessionCommand(cmd, s)">
                <el-icon class="side-more-icon"><MoreFilled /></el-icon>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="pin">
                      {{ pinned.has(s.session_id) ? '取消置顶' : '置顶' }}
                    </el-dropdown-item>
                    <el-dropdown-item command="rename">重命名</el-dropdown-item>
                    <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
          <div v-if="!sessionsLoading && !sortedSessions.length" class="side-empty">
            {{ search ? '无匹配会话' : '暂无历史会话' }}
          </div>
        </div>
      </aside>

      <div class="session-main">
        <ChatMessageList
          :messages="displayed"
          :placeholder="placeholder"
          :has-more="hasMore"
          :loading-more="loadingMore"
          @login="onLogin"
          @regenerate="regenerate"
          @load-more="loadMore"
        />
        <ChatInput
          :streaming="streaming"
          :selection="selection"
          :hint="inputHint"
          @send="handleSend"
          @stop="handleStop"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
// 通用对话会话组件：任何需要对话能力的页面直接 <ChatSession /> 即可复用。
// 内置会话侧栏（新建/切换/历史回放/搜索/置顶/分组/导出）与模型选择；页面只负责外壳与标题。
// 登录引导由 ChatInput 与错误气泡（PROVIDER_AUTH_REQUIRED）共同触发。
//
// 可选定制：placeholder（空态文案） / inputHint（输入区提示语） / sessionId（外部指定会话）。

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, MoreFilled, Plus, Search, Top } from '@element-plus/icons-vue'
import ChatMessageList from './ChatMessageList.vue'
import ChatInput from './ChatInput.vue'
import { useChatStream } from './useChatStream'
import type { StreamStatus } from './useChatStream'
import type {
  ChatAttachment,
  ChatContentPart,
  ChatMessage,
  ChatRequestMessage,
  ChatSendRequest,
  ChatSessionMeta,
  ChatUsage,
  MessageStatus,
} from '../../types/chat'
import { useModelSelection, type ReasoningEffort } from './useModelSelection'
import { useSessions } from './useSessions'

const props = withDefaults(
  defineProps<{
    sessionId?: string
    placeholder?: string
    inputHint?: string
  }>(),
  { sessionId: '', placeholder: '', inputHint: '' },
)

const router = useRouter()

// —— 消息服务端分页：allMessages 存已加载消息（升序），hasMore/nextBeforeSeq 驱动「加载更早」 ——
const allMessages = ref<ChatMessage[]>([])
const hasMore = ref(false)
const nextBeforeSeq = ref(-1)
const loadingMore = ref(false)

// 模型选择状态：会话持有单份，输入组件与登录引导共享
const selection = useModelSelection()

// 会话持久化状态：列表 + 当前会话 id（localStorage 续接）+ 消息回放 + 置顶
const sessionState = useSessions()
const {
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
} = sessionState

// 当前会话 id：外部传入优先，其次持久化的 activeId；空 = 尚未落库的新会话
const currentSessionId = computed(() => props.sessionId || activeId.value || '')

// 最近一次发送携带的系统提示词（用于「重新生成」复用）
const lastSystem = ref('')

const search = ref('')

// 未配置任何模型服务时的空态：加载完成、无错误、且 provider 列表为空
const showModelGuide = computed(
  () =>
    !selection.providersLoading.value &&
    !selection.providersError.value &&
    selection.providers.value.length === 0,
)

function goPlugins() {
  router.push('/plugins')
}

function recheck() {
  void selection.loadProviders()
}

onMounted(() => {
  void loadSessions()
  void restoreActive()
  window.addEventListener('keydown', onGlobalKey)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKey)
  if (searchTimer) clearTimeout(searchTimer)
})

// 全局快捷键：Ctrl/Cmd+N 新建会话，Esc 停止生成
function onGlobalKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'n') {
    e.preventDefault()
    newSession()
  } else if (e.key === 'Escape' && streaming.value) {
    handleStop()
  }
}

// 分页游标复位：清空 hasMore/nextBeforeSeq，回到「仅最近一页」的初始态
function resetPage() {
  hasMore.value = false
  nextBeforeSeq.value = -1
}

// 载入会话最近一页消息，并据服务端返回设置分页游标
async function loadInitial(id: string) {
  resetPage()
  try {
    const page = await loadMessages(id)
    allMessages.value = page.messages
    hasMore.value = page.hasMore
    nextBeforeSeq.value = page.nextBeforeSeq
  } catch {
    // 会话不存在或数据库未配置：保持空，不阻塞输入
    allMessages.value = []
    resetPage()
  }
}

// 刷新/重进后恢复上次会话：按持久化 activeId（或外部 sessionId）回放消息
async function restoreActive() {
  const id = props.sessionId || activeId.value
  if (!id) return
  await loadInitial(id)
}

function newSession() {
  if (streaming.value) void stream.cancel()
  stream.reset()
  allMessages.value = []
  resetPage()
  selectSession('')
}

async function switchSession(id: string) {
  if (id === currentSessionId.value) return
  if (streaming.value) void stream.cancel()
  stream.reset()
  allMessages.value = []
  resetPage()
  selectSession(id)
  await loadInitial(id)
}

function onSessionCommand(cmd: string, s: ChatSessionMeta) {
  if (cmd === 'pin') onPin(s)
  else if (cmd === 'rename') onRename(s)
  else if (cmd === 'delete') onDelete(s)
}

function onPin(s: ChatSessionMeta) {
  togglePin(s.session_id)
}

function onRename(s: ChatSessionMeta) {
  ElMessageBox.prompt('请输入新的会话标题', '重命名会话', {
    inputValue: s.title,
    inputPlaceholder: '会话标题',
    confirmButtonText: '确定',
    cancelButtonText: '取消',
  })
    .then(({ value }) => {
      const title = (value ?? '').trim()
      if (!title || title === s.title) return
      void renameSession(s.session_id, title)
    })
    .catch(() => {})
}

async function onDelete(s: ChatSessionMeta) {
  try {
    await ElMessageBox.confirm('删除后会话及其历史消息将不可恢复，确认删除？', '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  const wasActive = currentSessionId.value === s.session_id
  if (streaming.value) void stream.cancel()
  stream.reset()
  if (wasActive) {
    allMessages.value = []
    resetPage()
  }
  await deleteSession(s.session_id)
}

// —— 会话侧栏：置顶优先 + updated_at 降序（标题过滤已走后端 ?q=，见 search watch） ——
const sortedSessions = computed(() => {
  return [...sessions.value].sort((a, b) => {
    const ap = pinned.value.has(a.session_id) ? 1 : 0
    const bp = pinned.value.has(b.session_id) ? 1 : 0
    if (ap !== bp) return bp - ap
    return Date.parse(b.updated_at || '') - Date.parse(a.updated_at || '')
  })
})

// 搜索防抖走后端：避免每次击键都刷新列表；清空即回全量
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, (v) => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => void loadSessions(v), 250)
})

const BUCKET_ORDER = ['今天', '昨天', '近 7 天', '更早']

function timeBucket(iso: string): string {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '更早'
  const now = new Date()
  const d = new Date(t)
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const diffDays = Math.floor((todayStart - dayStart) / 86400000)
  if (diffDays <= 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (diffDays < 7) return '近 7 天'
  return '更早'
}

const groupedSessions = computed(() => {
  const groups: { label: string; items: ChatSessionMeta[] }[] = []
  const pinnedList = sortedSessions.value.filter((s) => pinned.value.has(s.session_id))
  if (pinnedList.length) groups.push({ label: '置顶', items: pinnedList })
  const normal = sortedSessions.value.filter((s) => !pinned.value.has(s.session_id))
  const buckets = new Map<string, ChatSessionMeta[]>()
  for (const s of normal) {
    const b = timeBucket(s.updated_at)
    if (!buckets.has(b)) buckets.set(b, [])
    buckets.get(b)!.push(s)
  }
  for (const label of BUCKET_ORDER) {
    const items = buckets.get(label)
    if (items?.length) groups.push({ label, items })
  }
  return groups
})

// —— 导出 ——
function onExport(cmd: string) {
  if (cmd === 'md') exportChat('md')
  else if (cmd === 'json') exportChat('json')
}

function exportChat(format: 'md' | 'json') {
  const id = currentSessionId.value
  if (!id) {
    ElMessage.warning('当前没有可导出的会话')
    return
  }
  const msgs = allMessages.value
  if (!msgs.length) {
    ElMessage.warning('当前会话暂无内容')
    return
  }
  const title = sessions.value.find((s) => s.session_id === id)?.title || '对话'
  if (format === 'json') {
    download(JSON.stringify(msgs, null, 2), `${safeName(title)}.json`, 'application/json')
  } else {
    download(toMarkdown(title, msgs), `${safeName(title)}.md`, 'text/markdown')
  }
}

function toMarkdown(title: string, msgs: ChatMessage[]): string {
  const lines: string[] = [`# ${title}`, '']
  for (const m of msgs) {
    if (m.role === 'user') {
      lines.push('## 我', '', m.content || '', '')
    } else if (m.role === 'assistant') {
      if (m.thinking) lines.push('> 思考过程', '', m.thinking, '')
      if (m.content) lines.push(m.content, '')
    }
  }
  return lines.join('\n')
}

function safeName(title: string): string {
  return title.replace(/[\\/:*?"<>|]/g, '_').slice(0, 60) || '对话'
}

function download(text: string, filename: string, mime: string) {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// —— 流式状态 ——
const stream = useChatStream()
const {
  status,
  error,
  errorCode,
  content,
  thinking,
  toolCalls,
  activeProvider,
  activeModel,
  donePayload,
  durationMs,
} = stream

const streaming = computed(() => status.value === 'connecting' || status.value === 'streaming')

// 正在生成的 assistant 消息：由 useChatStream 的片段实时合成，进入终端态后 commit 进 allMessages
const liveMessage = computed<ChatMessage | null>(() => {
  if (status.value === 'idle') return null
  return {
    id: '__live__',
    role: 'assistant',
    content: content.value,
    thinking: thinking.value || undefined,
    toolCalls: toolCalls.value.length ? toolCalls.value.map((c) => ({ ...c })) : undefined,
    status: toMessageStatus(status.value),
    createdAt: Date.now(),
    error: error.value || undefined,
    errorCode: errorCode.value || undefined,
    provider: activeProvider.value || undefined,
    model: activeModel.value || undefined,
    usage: toUsage(donePayload.value?.usage),
    durationMs: durationMs.value || undefined,
  }
})

const displayed = computed<ChatMessage[]>(() =>
  liveMessage.value ? [...allMessages.value, liveMessage.value] : allMessages.value,
)

// 加载更早一页：以 nextBeforeSeq 为游标取更早消息，prepend 到现有历史前
async function loadMore() {
  if (loadingMore.value) return
  const id = currentSessionId.value
  if (!id || nextBeforeSeq.value < 0) return
  loadingMore.value = true
  try {
    const page = await loadMessages(id, nextBeforeSeq.value)
    if (page.messages.length) {
      allMessages.value = [...page.messages, ...allMessages.value]
      nextBeforeSeq.value = page.nextBeforeSeq
      hasMore.value = page.hasMore
    } else {
      // 服务端 has_more 为「>=limit」的保守估计，空页即真正到底
      nextBeforeSeq.value = -1
      hasMore.value = false
    }
  } catch {
    // 加载失败保持现状，允许再次点击重试
  } finally {
    loadingMore.value = false
  }
}

// —— 发送 ——
async function handleSend(payload: {
  text: string
  provider?: string
  model?: string
  reasoningEffort?: ReasoningEffort
  system?: string
  attachments?: ChatAttachment[]
}) {
  if (streaming.value) return
  let id = currentSessionId.value
  const isNew = !id
  if (!id) {
    // 新会话：首次发送才落 id，并持久化续接
    id = genId()
    selectSession(id)
  }
  allMessages.value.push({
    id: genId(),
    role: 'user',
    content: payload.text,
    attachments: payload.attachments?.length ? payload.attachments : undefined,
    status: 'done',
    createdAt: Date.now(),
  })

  lastSystem.value = payload.system ?? ''

  const request: ChatSendRequest = {
    session_id: id,
    provider: payload.provider,
    model: payload.model,
    reasoning_effort: payload.reasoningEffort,
    system: payload.system,
    messages: buildMessages(),
  }
  await stream.send(request)
  void loadSessions()

  // 新会话自动用首条消息生成标题（后端无标题时兜底）
  if (isNew) {
    const title = makeTitle(payload.text)
    if (title) void renameSession(id, title)
  }
}

// 重新生成 / 重试：移除最后一条 assistant 回复，用当前历史重新请求
async function regenerate() {
  if (streaming.value) return
  const last = allMessages.value[allMessages.value.length - 1]
  if (!last || last.role !== 'assistant') return
  allMessages.value = allMessages.value.slice(0, -1)
  const id = currentSessionId.value
  if (!id) return
  const request: ChatSendRequest = {
    session_id: id,
    provider: selection.providerName.value || undefined,
    model: selection.modelName.value || undefined,
    reasoning_effort: selection.reasoningEffort.value,
    system: lastSystem.value || undefined,
    messages: buildMessages(),
  }
  await stream.send(request)
  void loadSessions()
}

function buildMessages(): ChatRequestMessage[] {
  return allMessages.value
    .filter((m) => m.role === 'user' || m.role === 'assistant')
    .map((m) => ({
      role: m.role,
      content: toRequestContent(m),
      attachments: m.attachments?.length ? m.attachments : undefined,
    }))
}

// 把带附件的用户消息重建为多模态 content：图片走 image_url，文本附件拼进正文，
// file 附件以引用形式拼进正文；无附件的消息保持纯文本。
function toRequestContent(m: ChatMessage): string | ChatContentPart[] {
  const atts = m.attachments ?? []
  const images = atts.filter((a) => a.kind === 'image' && a.dataUrl)
  const texts = atts.filter((a) => a.kind === 'text' && a.text)
  const files = atts.filter((a) => a.kind === 'file' && a.url)
  if (images.length === 0 && texts.length === 0 && files.length === 0) return m.content
  const parts: ChatContentPart[] = []
  let text = m.content
  for (const t of texts) {
    if (t.text) text = text ? `${text}\n\n${t.text}` : t.text
  }
  for (const f of files) {
    const note = `[已上传文件：${f.name}（${f.url}）]`
    text = text ? `${text}\n${note}` : note
  }
  if (text) parts.push({ type: 'text', text })
  for (const img of images) {
    if (img.dataUrl) parts.push({ type: 'image_url', image_url: { url: img.dataUrl } })
  }
  return parts
}

// 错误气泡「去登录」：走共享的登录引导（触发后刷新 provider 目录登录态）
function onLogin(provider: string) {
  void selection.triggerLogin(provider)
}

function handleStop() {
  void stream.cancel()
}

watch(status, (s) => {
  if (s === 'done' || s === 'error' || s === 'cancelled') commit()
})

function commit() {
  const s = status.value
  const msg: ChatMessage = {
    id: genId(),
    role: 'assistant',
    content: content.value,
    thinking: thinking.value || undefined,
    toolCalls: toolCalls.value.length ? toolCalls.value.map((c) => ({ ...c })) : undefined,
    status: toMessageStatus(s),
    createdAt: Date.now(),
    error: error.value || undefined,
    errorCode: errorCode.value || undefined,
    provider: activeProvider.value || undefined,
    model: activeModel.value || undefined,
    usage: toUsage(donePayload.value?.usage),
    durationMs: durationMs.value || undefined,
  }
  // 空回复仅在出错时落一条错误消息，其余情况不产生占位气泡
  if (msg.content || msg.thinking || msg.toolCalls?.length || s === 'error') {
    allMessages.value.push(msg)
  }
  stream.reset()
  void loadSessions()
}

function toMessageStatus(s: StreamStatus): MessageStatus {
  switch (s) {
    case 'done': return 'done'
    case 'error': return 'error'
    case 'cancelled': return 'cancelled'
    default: return 'streaming'
  }
}

function toUsage(u: unknown): ChatUsage | undefined {
  if (!u || typeof u !== 'object') return undefined
  const o = u as Record<string, unknown>
  const usage: ChatUsage = {}
  if (typeof o.total_tokens === 'number') usage.total_tokens = o.total_tokens
  if (typeof o.prompt_tokens === 'number') usage.prompt_tokens = o.prompt_tokens
  if (typeof o.completion_tokens === 'number') usage.completion_tokens = o.completion_tokens
  return usage.total_tokens || usage.prompt_tokens || usage.completion_tokens ? usage : undefined
}

// 会话侧栏的相对时间：刚刚 / n 分钟前 / n 小时前 / n 天前 / 日期
function relTime(iso: string): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const minutes = Math.floor((Date.now() - t) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  const d = new Date(t)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

function makeTitle(text: string): string {
  const t = text.replace(/\s+/g, ' ').trim()
  if (!t) return ''
  return t.length > 30 ? `${t.slice(0, 30)}…` : t
}

function genId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return Math.random().toString(36).slice(2)
  }
}
</script>

<style scoped>
.chat-session {
  display: flex;
  height: 100%;
}
.model-guide {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px;
  gap: 8px;
}
.guide-mark {
  width: 52px;
  height: 52px;
  display: grid;
  place-items: center;
  color: #fff;
  background: var(--pd-primary);
  font-size: 22px;
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
  margin-bottom: 8px;
}
.guide-title { font-family: var(--pd-font-display); font-size: 20px; font-weight: 600; letter-spacing: -0.01em; }
.guide-text { color: var(--pd-muted); font-size: 13.5px; max-width: 42ch; margin: 0; }
.guide-actions { display: flex; gap: 10px; margin-top: 12px; }

.session-side {
  width: 224px;
  flex: none;
  border-right: 1px solid var(--pd-border);
  background: var(--pd-surface-2);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.side-head {
  padding: 12px;
  border-bottom: 1px solid var(--pd-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.side-head-row { display: flex; align-items: center; gap: 6px; }
.side-new { flex: 1; }
.side-export { color: var(--pd-muted); }
.side-search { width: 100%; }
.side-err {
  padding: 8px 12px;
  font-size: 12px;
  color: var(--pd-danger);
  border-bottom: 1px solid var(--pd-border);
}
.side-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
  min-height: 0;
}
.side-group {
  padding: 10px 8px 4px;
  font-size: 11px;
  color: var(--pd-faint);
  letter-spacing: 0.04em;
}
.side-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px 6px 10px;
  border-radius: 8px;
  transition: background 0.12s;
}
.side-item:hover { background: var(--pd-surface); }
.side-item.active { background: var(--pd-primary-soft); }
.side-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  cursor: pointer;
}
.side-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--pd-text);
}
.side-title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.side-item.active .side-title { color: var(--pd-primary); font-weight: 600; }
.pin-ic { flex: none; color: var(--pd-primary); }
.side-time { font-size: 11px; color: var(--pd-faint); }
.side-more-icon {
  flex: none;
  color: var(--pd-faint);
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
}
.side-more-icon:hover { color: var(--pd-text); background: var(--pd-border); }
.side-empty {
  padding: 20px 12px;
  text-align: center;
  font-size: 12px;
  color: var(--pd-faint);
}

.session-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
</style>
