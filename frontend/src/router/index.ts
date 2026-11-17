import { createRouter, createWebHistory } from 'vue-router'
import WorkbenchLayout from '../layouts/WorkbenchLayout.vue'
import ModulePlaceholder from '../views/ModulePlaceholder.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: WorkbenchLayout,
      children: [
        { path: '', redirect: '/graph' },
        {
          path: 'graph',
          name: 'graph',
          component: ModulePlaceholder,
          meta: { title: '玩法图谱', description: '玩法结构可视化与节点关系探索，建设中', group: 'workspace' },
        },
        {
          path: 'text',
          name: 'text',
          component: ModulePlaceholder,
          meta: { title: '文本检索', description: '全文检索与内容定位，建设中', group: 'workspace' },
        },
        {
          path: 'ai',
          name: 'ai',
          component: ModulePlaceholder,
          meta: { title: 'AI 助手', description: '对话式智能助手，建设中', group: 'workspace' },
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

export default router
