<template>
  <span class="health" :class="levelClass" :title="title">
    <span class="dot"></span>
    <span class="label">{{ label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { HealthLevel } from '../api/contract'

const props = defineProps<{
  health: HealthLevel | null
  loading?: boolean
}>()

const MAP: Record<HealthLevel, { label: string }> = {
  healthy: { label: '运行正常' },
  degraded: { label: '部分降级' },
  standalone: { label: '独立运行' },
  unavailable: { label: '不可用' },
}

const levelClass = computed(() => (props.health ? props.health : 'unknown'))
const label = computed(() => {
  if (props.loading) return '连接中'
  return props.health ? MAP[props.health].label : '未连接'
})
const title = computed(() => (props.health ? `健康状态：${props.health}` : '尚未获取到运行状态'))
</script>

<style scoped>
.health {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--pd-muted);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #a4acbd;
}
.healthy .dot { background: #22c55e; }
.degraded .dot { background: #f59e0b; }
.standalone .dot { background: #64748b; }
.unavailable .dot { background: #ef4444; }
.unknown .dot { background: #a4acbd; }
</style>
