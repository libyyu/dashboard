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

// 插件前端入口当前为占位（各插件 dist 尚未实现），统一渲染 ModulePlaceholder。
// 后续插件落地 dist 后，改为按 routes[].entry 运行时懒加载。
export function registerPluginRoutes(plugins: PluginInfo[]) {
  for (const plugin of plugins) {
    if (!plugin.enabled) continue
    // sidecar 纯服务插件（无 routes）不注册前端路由：只提供底层能力，没有页面。
    const paths = (plugin.routes ?? []).map((r) => r.path).filter(Boolean)
    if (!paths.length) continue
    const title = plugin.menu?.title ?? plugin.name ?? plugin.id
    for (const p of paths) {
      const rel = p.replace(/^\//, '')
      const full = '/' + rel
      if (router.hasRoute(full)) continue
      router.addRoute('workbench', {
        path: rel,
        name: `plugin-${rel.replace(/\//g, '-')}`,
        component: ModulePlaceholder,
        meta: { title, description: `插件「${title}」`, group: 'plugin' },
      })
    }
  }
}

export default router
