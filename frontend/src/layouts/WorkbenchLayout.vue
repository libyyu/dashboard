<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="brand">
        <span class="logo">▲</span>
        <div>
          <div class="brand-title">Plateau Dashboard</div>
          <div class="brand-sub">平台工作台</div>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        router
        class="menu"
        background-color="#1e2533"
        text-color="#aab2c5"
        active-text-color="#ffffff"
      >
        <el-menu-item-group title="工作台">
          <el-menu-item index="/graph">
            <el-icon><Share /></el-icon>
            <span>玩法图谱</span>
          </el-menu-item>
          <el-menu-item index="/text">
            <el-icon><Search /></el-icon>
            <span>文本检索</span>
          </el-menu-item>
          <el-menu-item index="/ai">
            <el-icon><ChatDotRound /></el-icon>
            <span>AI 助手</span>
          </el-menu-item>
        </el-menu-item-group>

        <el-menu-item-group title="诊断">
          <el-menu-item index="/console">
            <el-icon><Monitor /></el-icon>
            <span>控制台</span>
          </el-menu-item>
          <el-menu-item index="/console/logs">
            <el-icon><Document /></el-icon>
            <span>日志</span>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">{{ pageTitle }}</div>
        <div class="header-right">
          <HealthIndicator :health="health" :loading="runtime.loading" />
          <span class="pd-hint">v{{ version }}</span>
        </div>
      </el-header>

      <div v-if="banner" class="banner">
        <el-alert :title="banner.title" :type="banner.type" :closable="false" show-icon />
      </div>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { Share, Search, ChatDotRound, Monitor, Document } from '@element-plus/icons-vue'
import HealthIndicator from '../components/HealthIndicator.vue'
import { useRuntimeStore } from '../stores/runtime'

const route = useRoute()
const runtime = useRuntimeStore()

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => (route.meta.title as string) || '工作台')
const health = computed(() => runtime.data?.health ?? null)
const version = computed(() => runtime.data?.version || '1.0.0')

const banner = computed(() => {
  if (health.value === 'degraded') return { title: '部分服务降级', type: 'warning' as const }
  if (health.value === 'unavailable') return { title: '服务不可用', type: 'error' as const }
  return null
})

onMounted(() => {
  runtime.refresh()
  runtime.startPolling()
})
onUnmounted(() => runtime.stopPolling())
</script>

<style scoped>
.layout { height: 100%; }
.aside {
  background: #1e2533;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 18px 14px;
  color: #fff;
}
.logo {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: linear-gradient(135deg, #4a5bd0, #7a5bd0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}
.brand-title { font-size: 14px; font-weight: 650; line-height: 1.2; }
.brand-sub { font-size: 11px; color: #8b94ab; }
.menu { border-right: none; flex: 1; overflow-y: auto; }
.menu :deep(.el-menu-item) { height: 44px; }
.menu :deep(.el-menu-item.is-active) { background: #2c3649; }
.menu :deep(.el-menu-item-group__title) {
  color: #5a647a;
  padding: 12px 20px 4px;
  font-size: 11px;
}

.header {
  height: 54px;
  background: #fff;
  border-bottom: 1px solid #e6e9f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-title { font-size: 16px; font-weight: 640; }
.header-right { display: flex; align-items: center; gap: 14px; }
.banner { padding: 8px 24px 0; }
.main { padding: 20px 24px 40px; }
</style>
