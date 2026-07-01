import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '对话' },
  },
  {
    path: '/connections',
    name: 'connections',
    component: () => import('@/views/ConnectionView.vue'),
    meta: { title: '连接管理' },
  },
  {
    path: '/reports',
    name: 'reports',
    component: () => import('@/views/ReportView.vue'),
    meta: { title: '健康巡检' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
