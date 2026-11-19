<template>
  <div ref="scroller" class="chat-list" @scroll="onScroll">
    <div v-if="empty" class="empty">
      <div class="empty-icon">◈</div>
      <div class="empty-title">开始对话</div>
      <div class="empty-sub">{{ placeholder }}</div>
    </div>

    <div v-if="!empty && hasMore" class="load-more">
      <el-button size="small" text :loading="loadingMore" @click="onLoadMore">
        加载更早消息
      </el-button>
    </div>

    <ChatMessageItem
      v-for="(message, i) in messages"
      :key="message.id"
      :message="message"
      :is-last="i === messages.length - 1"
      @login="onLogin"
      @regenerate="$emit('regenerate')"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import ChatMessageItem from './ChatMessageItem.vue'
import type { ChatMessage } from '../../types/chat'

const props = withDefaults(
  defineProps<{
    messages: ChatMessage[]
    placeholder?: string
    /** 是否还有更早消息可加载（会话消息分页） */
    hasMore?: boolean
    loadingMore?: boolean
  }>(),
  { placeholder: '输入消息，与 AI 助手对话', hasMore: false, loadingMore: false },
)

const emit = defineEmits<{
  (e: 'login', provider: string): void
  (e: 'regenerate'): void
  (e: 'load-more'): void
}>()

const scroller = ref<HTMLElement | null>(null)
const empty = computed(() => props.messages.length === 0)

// 是否贴近底部：仅当用户处于底部附近时才自动跟随滚动（避免打断向上翻历史）
let stickToBottom = true
// 加载更早前的视口锚点（距底部距离），用于加载后保持视口位置
let pendingAnchor = 0

function onScroll() {
  const el = scroller.value
  if (!el) return
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight
  stickToBottom = dist < 80
}

function onLoadMore() {
  const el = scroller.value
  if (el) pendingAnchor = el.scrollHeight - el.scrollTop
  emit('load-more')
}

watch(
  () => props.messages,
  async () => {
    await nextTick()
    const el = scroller.value
    if (!el) return
    if (pendingAnchor > 0) {
      // 加载更早后保持视口：距底部距离不变
      el.scrollTop = el.scrollHeight - pendingAnchor
      pendingAnchor = 0
    } else if (stickToBottom) {
      el.scrollTop = el.scrollHeight
    }
  },
  { deep: true },
)
</script>

<style scoped>
.chat-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px 20px 8px;
}
.empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--pd-muted);
}
.empty-icon {
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--pd-primary-soft);
  color: var(--pd-primary);
  font-size: 22px;
  margin-bottom: 14px;
}
.empty-title { font-size: 15px; color: var(--pd-text); font-weight: 640; letter-spacing: -0.01em; }
.empty-sub { font-size: 12.5px; margin-top: 6px; }
.load-more {
  display: flex;
  justify-content: center;
  padding: 4px 0 12px;
}
</style>
