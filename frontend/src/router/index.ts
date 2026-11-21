import type { Component } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import WorkbenchLayout from '../layouts/WorkbenchLayout.vue'
import ModulePlaceholder from '../views/ModulePlaceholder.vue'
import type { PluginInfo } from '../api/contract'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'workbench',
      component: WorkbenchLayout,
      children: [
        { path: '', redirect: '/plugins' },
        {
          path: 'plugins',
          name: 'plugin-store',
          component: () => import('../views/plugins/PluginStore.vue'),
          meta: { title: '插件仓库', description: '浏览、安装与卸载插件', group: 'workspace' },
        },
        {
          path: 'chat',
          name: 'chat',
          component: () => import('../views/chat/ChatView.vue'),
          meta: { title: 'AI 助手', description: '与本地大模型流式对话', group: 'workspace' },
        },
        {
          path: 'console',
          name: 'console',
          component: () => import('../views/console/ConsoleOverview.vue'),
          meta: { title: '控制台', description: '服务健康状态、版本、端口与登录管理', group: 'console' },
        },
        {
          path: 'console/logs',
          name: 'console-logs',
          component: () => import('../views/console/ConsoleLogs.vue'),
          meta: { title: '日志', description: '运行日志检索与下钻', group: 'console' },
        },
      ],
    },
  ],
})

// 插件前端入口：按 routes[].entry 运行时懒加载。
// entry 约定为同源绝对路径（/…）或完整 URL；Vite 构建期无法静态分析运行时变量，
// 用 @vite-ignore 保留原生动态 import。加载失败回退 ModulePlaceholder，避免插件
// 入口缺失 / 未就绪（尚未落地 dist / 已卸载）拖垮整条路由。
function pluginComponent(entry: string | undefined): Component | (() => Promise<Component>) {
  if (!entry) return ModulePlaceholder
  return () =>
    import(/* @vite-ignore */ entry)
      .then((mod) => (mod && mod.default) || mod)
      .catch(() => ModulePlaceholder)
}

// 插件前端路由：按 manifest 的 routes[].entry 运行时懒加载（早期统一渲染 ModulePlaceholder）。
export function registerPluginRoutes(plugins: PluginInfo[]) {
  for (const plugin of plugins) {
    if (!plugin.enabled) continue
    // sidecar 纯服务插件（无 routes）不注册前端路由：只提供底层能力，没有页面。
    const routes = (plugin.routes ?? []).filter((r) => r && r.path)
    if (!routes.length) continue
    const title = plugin.menu?.title ?? plugin.name ?? plugin.id
    for (const r of routes) {
      const rel = r.path.replace(/^\//, '')
      const name = `plugin-${rel.replace(/\//g, '-')}`
      // hasRoute 按 name 匹配（非 path），用 name 去重，避免插件清单刷新时重复 addRoute。
      if (router.hasRoute(name)) continue
      router.addRoute('workbench', {
        path: rel,
        name,
        component: pluginComponent(r.entry),
        meta: { title, description: `插件「${title}」`, group: 'plugin', pluginId: plugin.id },
      })
    }
  }
}

export default router
