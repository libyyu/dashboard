<template>
  <el-container class="layout">
    <el-aside :style="asideStyle" class="aside">
      <div class="brand" :class="{ compact: isRail }">
        <span class="brand-mark" aria-hidden="true">▲</span>
        <div v-if="!isRail" class="brand-word">
          <span class="brand-name">Plateau</span>
          <span class="brand-sub">DASHBOARD</span>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        router
        class="menu"
        :class="{ rail: isRail }"
        :collapse="isRail"
      >
        <el-menu-item-group v-for="g in navGroups" :key="g.title" :title="g.title">
          <el-menu-item v-for="it in g.items" :key="it.path" :index="it.path">
            <el-icon><component :is="it.icon" /></el-icon>
            <template #title>{{ it.title }}</template>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
    </el-aside>

    <div class="resizer" role="separator" aria-orientation="vertical" @mousedown="onResizeStart" />

    <el-container>
      <el-header class="header">
        <div class="header-title">{{ pageTitle }}</div>
        <div class="header-right">
          <HealthIndicator :health="health" :loading="runtime.loading" />
          <span class="pd-hint">v{{ version }}</span>
          <el-popover placement="bottom-end" :width="240" trigger="click">
            <template #reference>
              <el-button circle text :icon="Setting" class="settings-btn" />
            </template>
            <div class="settings">
              <span class="settings-label">主题</span>
              <el-radio-group :model-value="settings.theme" size="small" @change="onThemeChange">
                <el-radio-button value="light">浅色</el-radio-button>
                <el-radio-button value="dark">深色</el-radio-button>
              </el-radio-group>
            </div>
          </el-popover>
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
import { computed, markRaw, onMounted, onUnmounted, watch } from 'vue'
import type { Component } from 'vue'
import { useRoute } from 'vue-router'
import {
  Shop,
  Box,
  Monitor,
  Document,
  ChatDotRound,
  Setting,
} from '@element-plus/icons-vue'
import HealthIndicator from '../components/HealthIndicator.vue'
import { useRuntimeStore } from '../stores/runtime'
import { usePluginsStore } from '../stores/plugins'
import { useSettingsStore } from '../stores/settings'
import type { ThemeMode } from '../stores/settings'
import { registerPluginRoutes } from '../router'

interface NavItem { path: string; title: string; icon: Component }
interface NavGroup { title: string; items: NavItem[] }

const MIN_WIDTH = 72
const MAX_WIDTH = 360
const RAIL_THRESHOLD = 128

const route = useRoute()
const runtime = useRuntimeStore()
const plugins = usePluginsStore()
const settings = useSettingsStore()

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => (route.meta.title as string) || '工作台')
const health = computed(() => runtime.data?.health ?? null)
const version = computed(() => runtime.data?.version || '1.0.0')

const isRail = computed(() => settings.sidebarWidth < RAIL_THRESHOLD)
const asideStyle = computed(() => ({ width: `${settings.sidebarWidth}px` }))

const banner = computed(() => {
  if (health.value === 'degraded') return { title: '部分服务降级', type: 'warning' as const }
  if (health.value === 'unavailable') return { title: '服务不可用', type: 'error' as const }
  return null
})

// 已装插件菜单（数据驱动）：只有带 menu 且 enabled 的插件才进侧边栏
const pluginMenus = computed(() =>
  plugins.installed
    .filter((p) => p.enabled && p.menu)
    .map((p) => ({ id: p.id, path: p.menu!.path, title: p.menu!.title })),
)

const navGroups = computed<NavGroup[]>(() => [
  {
    title: '工作台',
    items: [
      { path: '/plugins', title: '插件仓库', icon: markRaw(Shop) },
      { path: '/chat', title: 'AI 助手', icon: markRaw(ChatDotRound) },
      ...pluginMenus.value.map((p) => ({
        path: p.path,
        title: p.title,
        icon: markRaw(Box),
      })),
    ],
  },
  {
    title: '诊断',
    items: [
      { path: '/console', title: '控制台', icon: markRaw(Monitor) },
      { path: '/console/logs', title: '日志', icon: markRaw(Document) },
    ],
  },
])

function onThemeChange(v: string | number | boolean | undefined) {
  if (v === 'light' || v === 'dark') settings.setTheme(v as ThemeMode)
}

// 拖拽调整侧边栏宽度；低于阈值自动进入图标模式
function onResizeStart(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const startW = settings.sidebarWidth
  const onMove = (ev: MouseEvent) => {
    settings.setSidebarWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startW + (ev.clientX - startX))))
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

// 插件清单变化时同步动态注册路由
watch(
  () => plugins.installed,
  (list) => registerPluginRoutes(list),
  { deep: true, immediate: true },
)

onMounted(() => {
  runtime.refresh()
  runtime.startPolling()
  plugins.refresh()
  plugins.startPolling()
})

onUnmounted(() => {
  runtime.stopPolling()
  plugins.stopPolling()
})
</script>

<style scoped>
.layout { height: 100%; }
.aside {
  background: var(--pd-sidebar);
  border-right: 1px solid var(--pd-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  flex: none;
  transition: width 0.2s var(--pd-ease);
}
.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 20px 20px 16px;
}
.brand.compact { justify-content: center; padding: 20px 0 16px; }
.brand-mark {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  color: #fff;
  background: var(--pd-primary);
  font-size: 14px;
  line-height: 1;
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
  flex: none;
}
.brand-word { display: flex; flex-direction: column; line-height: 1; gap: 3px; overflow: hidden; }
.brand-name {
  font-family: var(--pd-font-display);
  font-weight: 650;
  font-size: 18px;
  letter-spacing: -0.01em;
  color: var(--pd-text);
  white-space: nowrap;
}
.brand-sub {
  font-family: var(--pd-mono);
  font-size: 9.5px;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  color: var(--pd-faint);
  white-space: nowrap;
}
.menu { border-right: none; flex: 1; overflow-y: auto; padding: 2px 12px; }
.menu.rail { padding: 2px 6px; }
.menu :deep(.el-menu-item-group__title) {
  font-family: var(--pd-mono);
  color: var(--pd-terrain);
  padding: 16px 10px 6px;
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
}
.menu :deep(.el-menu-item) {
  height: 38px;
  line-height: 38px;
  border-radius: 8px;
  margin: 2px 0;
  color: var(--pd-sidebar-text);
  transition: background-color 0.16s var(--pd-ease), color 0.16s var(--pd-ease);
}
.menu :deep(.el-menu-item:hover) { background: var(--pd-sidebar-2); color: var(--pd-text); }
.menu :deep(.el-menu-item.is-active) {
  background: var(--pd-sidebar-active);
  color: var(--pd-primary-strong);
  font-weight: 600;
  box-shadow: inset 3px 0 0 var(--pd-primary);
}
.menu :deep(.el-menu-item .el-icon) { color: var(--pd-faint); }
.menu :deep(.el-menu-item.is-active .el-icon) { color: var(--pd-primary); }

.resizer {
  width: 5px;
  flex: none;
  cursor: col-resize;
  background: transparent;
  transition: background 0.16s var(--pd-ease);
  position: relative;
}
.resizer:hover,
.resizer:active { background: var(--pd-primary-ring); }
.resizer::after {
  content: "";
  position: absolute;
  left: 2px;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--pd-border);
}

.header {
  height: 56px;
  background: color-mix(in oklch, var(--pd-surface) 92%, transparent);
  backdrop-filter: saturate(1.3) blur(10px);
  border-bottom: 1px solid var(--pd-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
}
.header-title { font-family: var(--pd-font-display); font-size: 18px; font-weight: 600; letter-spacing: -0.01em; }
.header-right { display: flex; align-items: center; gap: 14px; }
.settings-btn { color: var(--pd-muted); }
.settings { display: flex; flex-direction: column; gap: 12px; }
.settings-label {
  font-family: var(--pd-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--pd-muted);
}
.banner { padding: 8px 24px 0; }
.main { padding: 24px; }
</style>
