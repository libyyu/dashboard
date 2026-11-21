<template>
  <div class="msg" :class="[`msg-${message.role}`, { 'msg-err': message.status === 'error' }]">
    <div class="bubble">
      <ChatThinking v-if="message.role === 'assistant'" :text="message.thinking || ''" />
      <ChatToolCall
        v-for="call in message.toolCalls ?? []"
        :key="call.id || call.name"
        :call="call"
      />
      <MarkdownContent v-if="message.content" :text="message.content" />
      <div v-else-if="message.role === 'assistant' && message.status === 'streaming'" class="typing">
        <span class="t-dot" /><span class="t-dot" /><span class="t-dot" />
      </div>
      <div v-if="message.role === 'user' && message.attachments?.length" class="atts">
        <div v-for="a in message.attachments" :key="a.id" class="att">
          <img v-if="a.kind === 'image' && a.dataUrl" :src="a.dataUrl" class="att-img" :alt="a.name" />
          <a
            v-else-if="a.kind === 'file' && a.url"
            :href="a.url"
            class="att-file att-file-link"
            target="_blank"
            rel="noopener"
          >
            <span class="att-file-name">{{ a.name }}</span>
            <span class="att-file-meta">文件</span>
          </a>
          <span v-else class="att-file">
            <span class="att-file-name">{{ a.name }}</span>
            <span class="att-file-meta">{{ a.kind === 'text' ? '文本' : '文件' }}</span>
          </span>
        </div>
      </div>
      <div v-if="message.status === 'error' && message.error" class="err-text">
        <span>{{ message.error }}</span>
        <el-button
          v-if="message.errorCode === 'PROVIDER_AUTH_REQUIRED' && message.provider"
          size="small"
          text
          type="primary"
          class="login-link"
          @click="$emit('login', message.provider)"
        >去登录</el-button>
      </div>
      <div v-if="message.status === 'cancelled'" class="hint-text">已停止</div>
    </div>

    <!-- 消息操作栏：模型/用量脚注 + 复制 + 重试/重新生成 -->
    <div v-if="showActions" class="msg-actions">
      <span v-if="metaText" class="msg-meta">{{ metaText }}</span>
      <span class="actions-spacer" />
      <button v-if="message.content" type="button" class="act-btn" @click="copyContent">复制</button>
      <button v-if="isLast" type="button" class="act-btn act-primary" @click="$emit('regenerate')">
        {{ message.status === 'error' ? '重试' : '重新生成' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ChatThinking from './ChatThinking.vue'
import ChatToolCall from './ChatToolCall.vue'
import MarkdownContent from './MarkdownContent.vue'
import type { ChatMessage } from '../../types/chat'

const props = withDefaults(
  defineProps<{
    message: ChatMessage
    /** 是否为会话最后一条消息（决定是否显示「重新生成/重试」） */
    isLast?: boolean
  }>(),
  { isLast: false },
)

const emit = defineEmits<{
  (e: 'login', provider: string): void
  (e: 'regenerate'): void
}>()

const showActions = computed(() => {
  const m = props.message
  if (m.role !== 'assistant') return false
  if (m.status === 'streaming' || m.status === 'pending') return false
  return !!m.content || !!metaText.value || props.isLast
})

const metaText = computed(() => {
  const parts: string[] = []
  const m = props.message
  // 模型带 provider 前缀（{provider}/{model}），与输入框 / 下拉项显示一致
  if (m.model) parts.push(m.provider ? `${m.provider}/${m.model}` : m.model)
  const total = props.message.usage?.total_tokens
  if (total) parts.push(`${fmtTok(total)} tokens`)
  const d = props.message.durationMs
  if (d) parts.push(fmtDuration(d))
  return parts.join(' · ')
})

function fmtTok(n: number): string {
  if (n < 1000) return String(n)
  return `${(n / 1000).toFixed(1)}k`
}

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

async function copyContent() {
  const text = props.message.content
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    } catch {
      /* 复制失败静默忽略 */
    }
  }
}
</script>

<style scoped>
.msg { display: flex; flex-direction: column; margin-bottom: 16px; }
.msg-user { align-items: flex-end; }
.msg-assistant,
.msg-system,
.msg-tool { align-items: flex-start; }
.bubble {
  max-width: 76%;
  padding: 11px 15px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}
.msg-user .bubble {
  background: var(--pd-primary);
  color: #fff;
  border-bottom-right-radius: 5px;
  box-shadow: var(--pd-shadow-sm);
}
.msg-assistant .bubble {
  background: var(--pd-surface);
  border: 1px solid var(--pd-border);
  border-bottom-left-radius: 5px;
  color: var(--pd-text);
  box-shadow: var(--pd-shadow-sm);
}
.msg-system .bubble,
.msg-tool .bubble {
  background: var(--pd-surface-2);
  border: 1px solid var(--pd-border);
  color: var(--pd-muted);
  font-size: 12px;
}
.msg-err .bubble { border-color: #f0b9b2; }
.err-text { margin-top: 6px; color: var(--pd-danger); font-size: 12px; }
.err-text .login-link { margin-left: 4px; padding: 0; font-size: 12px; }
.hint-text { margin-top: 4px; color: var(--pd-muted); font-size: 12px; }
.atts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.att { position: relative; }
.att-img {
  display: block;
  max-width: 160px;
  max-height: 160px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.35);
  object-fit: cover;
}
.att-file {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
}
.att-file-name { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.att-file-meta { opacity: 0.75; font-size: 11px; }
.att-file-link { text-decoration: none; color: inherit; }
.att-file-link:hover { text-decoration: underline; }
.typing { display: inline-flex; gap: 4px; padding: 6px 2px; }
.t-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pd-faint);
  animation: blink 1.2s infinite ease-in-out;
}
.t-dot:nth-child(2) { animation-delay: 0.2s; }
.t-dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 80%, 100% { opacity: 0.25; }
  40% { opacity: 1; }
}

/* —— 操作栏 —— */
.msg-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 5px;
  padding-left: 15px;
}
.msg-meta {
  font-family: var(--pd-mono);
  font-size: 11px;
  color: var(--pd-faint);
}
.actions-spacer { flex: 1; }
.act-btn {
  border: none;
  background: transparent;
  color: var(--pd-faint);
  font-size: 12px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 5px;
  transition: color 0.12s, background 0.12s;
}
.act-btn:hover { color: var(--pd-text); background: var(--pd-surface-2); }
.act-primary { color: var(--pd-muted); }
.act-primary:hover { color: var(--pd-primary); }
</style>
