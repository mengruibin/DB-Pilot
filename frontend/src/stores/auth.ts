/**
 * 认证 Store
 *
 * 管理 JWT token、当前用户信息、登录/登出/初始化。
 * token 持久化到 localStorage，用户信息从 GET /api/auth/me 获取。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { http } from '@/api/client'

/** 用户信息接口（不含密码哈希） */
export interface User {
  id: string
  username: string
  role: 'admin' | 'readonly'
  is_active: boolean
  created_at: string
  updated_at: string
}

/** 登录响应接口 */
export interface LoginResponse {
  access_token: string
  token_type: string
  user: User
}

const TOKEN_KEY = 'db-pilot:token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const currentUser = ref<User | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  const isAuthenticated = computed(() => token.value !== null && currentUser.value !== null)
  const isAdmin = computed(() => currentUser.value?.role === 'admin')

  function setToken(t: string) {
    token.value = t
    localStorage.setItem(TOKEN_KEY, t)
  }

  function clearToken() {
    token.value = null
    currentUser.value = null
    localStorage.removeItem(TOKEN_KEY)
  }

  async function login(username: string, password: string): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      const res = await http.post<LoginResponse>('/api/auth/login', { username, password })
      setToken(res.access_token)
      currentUser.value = res.user
    } catch (err: unknown) {
      clearToken()
      const msg = err instanceof Error ? err.message : '登录失败'
      error.value = msg
      throw err
    } finally {
      isLoading.value = false
    }
  }

  function logout() {
    clearToken()
  }

  async function fetchMe(): Promise<void> {
    if (!token.value) return
    try {
      const user = await http.get<User>('/api/auth/me')
      currentUser.value = user
    } catch {
      // token 无效或过期
      clearToken()
    }
  }

  async function initAuth(): Promise<void> {
    if (token.value && !currentUser.value) {
      await fetchMe()
    }
  }

  return {
    token,
    currentUser,
    isLoading,
    error,
    isAuthenticated,
    isAdmin,
    login,
    logout,
    fetchMe,
    initAuth,
  }
})
