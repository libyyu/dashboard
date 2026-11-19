<template>
  <div v-if="text" class="chat-thinking">
    <button type="button" class="ct-head" @click="open = !open">
      <span class="ct-dot" />
      <span class="ct-label">{{ label }}</span>
      <el-icon class="ct-arrow" :class="{ open }"><ArrowDown /></el-icon>
    </button>
    <div v-show="open" class="ct-body">{{ text }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    text: string
    label?: string
  }>(),
  { label: '思考过程' },
)

const open = ref(false)
</script>

<style scoped>
.chat-thinking {
  margin-bottom: 8px;
  border: 1px solid var(--pd-border);
  border-radius: 8px;
  background: #f8f9fc;
  overflow: hidden;
}
.ct-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  color: var(--pd-muted);
}
.ct-head:hover { background: #f1f3f9; }
.ct-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #8b94ab;
}
.ct-label { flex: 1; text-align: left; }
.ct-arrow { transition: transform 0.15s; }
.ct-arrow.open { transform: rotate(180deg); }
.ct-body {
  padding: 0 10px 10px;
  font-size: 12.5px;
  color: #6b7487;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
