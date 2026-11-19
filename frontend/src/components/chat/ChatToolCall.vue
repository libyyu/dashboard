<template>
  <div class="chat-tool">
    <button type="button" class="tool-head" @click="open = !open">
      <el-icon class="tool-icon"><Tools /></el-icon>
      <span class="tool-name">{{ call.name || '工具调用' }}</span>
      <el-tag v-if="call.status === 'running'" size="small" type="warning" effect="light">执行中</el-tag>
      <el-tag v-else-if="call.status === 'error'" size="small" type="danger" effect="light">失败</el-tag>
      <el-tag v-else size="small" type="success" effect="light">完成</el-tag>
      <el-icon class="tool-arrow" :class="{ open }"><ArrowDown /></el-icon>
    </button>
    <div v-show="open" class="tool-body">
      <div v-if="call.arguments" class="tool-sec">
        <div class="tool-sec-title">参数</div>
        <pre class="tool-pre">{{ prettyArgs }}</pre>
      </div>
      <div v-if="call.result" class="tool-sec">
        <div class="tool-sec-title">结果</div>
        <pre class="tool-pre">{{ shownResult }}</pre>
        <button v-if="resultLong" type="button" class="tool-more" @click="resultExpanded = !resultExpanded">
          {{ resultExpanded ? '收起' : '展开全部' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowDown, Tools } from '@element-plus/icons-vue'
import type { ToolCall } from '../../types/chat'

const props = defineProps<{ call: ToolCall }>()

const open = ref(true)
const resultExpanded = ref(false)
const RESULT_PREVIEW = 2000

// 尝试 JSON 美化；非 JSON 文本原样展示
function pretty(s: string): string {
  if (!s) return ''
  try {
    return JSON.stringify(JSON.parse(s), null, 2)
  } catch {
    return s
  }
}

const prettyArgs = computed(() => pretty(props.call.arguments))
const prettyResult = computed(() => pretty(props.call.result ?? ''))
const resultLong = computed(() => prettyResult.value.length > RESULT_PREVIEW)
const shownResult = computed(() =>
  resultLong.value && !resultExpanded.value
    ? `${prettyResult.value.slice(0, RESULT_PREVIEW)}\n…`
    : prettyResult.value,
)
</script>

<style scoped>
.chat-tool {
  margin-bottom: 8px;
  border: 1px solid var(--pd-border);
  border-radius: 8px;
  background: #fbfcfe;
  overflow: hidden;
}
.tool-head {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 12.5px;
  color: var(--pd-text);
}
.tool-head:hover { background: #f2f4f9; }
.tool-icon { color: var(--pd-primary); }
.tool-name { flex: 1; text-align: left; font-weight: 550; }
.tool-arrow { color: var(--pd-muted); transition: transform 0.15s; }
.tool-arrow.open { transform: rotate(180deg); }
.tool-body { padding: 0 10px 10px; }
.tool-sec { margin-top: 8px; }
.tool-sec-title { font-size: 11px; color: var(--pd-muted); margin-bottom: 4px; }
.tool-pre {
  margin: 0;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.6;
  background: #f1f3f8;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  max-height: 320px;
  overflow-y: auto;
}
.tool-more {
  margin-top: 4px;
  border: none;
  background: transparent;
  color: var(--pd-primary);
  font-size: 12px;
  cursor: pointer;
  padding: 2px 4px;
}
.tool-more:hover { text-decoration: underline; }
</style>
