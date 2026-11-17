<template>
  <div class="module-placeholder">
    <el-empty :image-size="90">
      <template #description>
        <div class="ph-title">{{ title }}</div>
        <div class="ph-desc">{{ description }}</div>
      </template>
      <div class="ph-actions">
        <el-tag size="small" type="info" effect="plain">{{ badge }}</el-tag>
        <el-button v-if="isConsole" size="small" text type="primary" @click="backToWorkspace">
          返回工作台
        </el-button>
      </div>
    </el-empty>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const title = computed(() => (route.meta.title as string) || '模块')
const description = computed(() => (route.meta.description as string) || '')
const isConsole = computed(() => route.meta.group === 'console')
const badge = computed(() => (isConsole.value ? '诊断区' : '规划中'))

function backToWorkspace() {
  router.push('/graph')
}
</script>

<style scoped>
.module-placeholder { padding-top: 60px; }
.ph-title { font-size: 18px; font-weight: 650; margin-bottom: 6px; }
.ph-desc { color: var(--pd-muted); font-size: 13px; margin-bottom: 14px; }
.ph-actions { display: flex; gap: 10px; align-items: center; justify-content: center; }
</style>
