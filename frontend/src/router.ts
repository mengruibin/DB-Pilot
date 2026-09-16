import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', noAuth: true },
  },
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
  {
    path: '/users',
    name: 'users',
    component: () => import('@/views/UserManagementView.vue'),
    meta: { title: '用户管理', requiresAdmin: true },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { title: '设置' },
  },
  // 兜底：不存在的路径重定向到首页
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

const TOKEN_KEY = 'db-pilot:token'

router.beforeEach(async (to, _from, next) => {
  // 登录页：已登录则跳转首页
  if (to.path === '/login') {
    if (localStorage.getItem(TOKEN_KEY)) {
      next('/')
    } else {
      next()
    }
    return
  }

  // 其他路由：必须登录
  const token = localStorage.getItem(TOKEN_KEY)
  if (!token) {
    next('/login')
    return
  }

  // 管理页：检查 admin 角色
  if (to.meta.requiresAdmin) {
    // 懒加载 authStore 避免循环依赖
    const { useAuthStore } = await import('@/stores/auth')
    const authStore = useAuthStore()
    if (!authStore.currentUser) {
      await authStore.fetchMe()
    }
    if (authStore.currentUser?.role !== 'admin') {
      next('/')
      return
    }
  }

  next()
})

export default router
