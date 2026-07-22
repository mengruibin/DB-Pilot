<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')

async function handleLogin() {
  if (!username.value.trim() || !password.value.trim()) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  errorMsg.value = ''
  loading.value = true
  try {
    await authStore.login(username.value.trim(), password.value.trim())
    router.push('/')
  } catch (err: unknown) {
    errorMsg.value = err instanceof Error ? err.message : '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <!-- 品牌标识 -->
      <div class="brand">
        <svg class="brand-icon" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <ellipse cx="12" cy="5" rx="9" ry="3" />
          <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
        </svg>
        <h1 class="brand-title">DB-Pilot</h1>
        <p class="brand-subtitle">AI 数据库运维助手</p>
      </div>

      <!-- 登录表单 -->
      <form class="login-form" @submit.prevent="handleLogin">
        <div class="field-group">
          <label class="field-label">用户名</label>
          <input
            v-model="username"
            type="text"
            class="field-input"
            placeholder="请输入用户名"
            autocomplete="username"
            @keydown.enter="handleLogin"
          />
        </div>

        <div class="field-group">
          <label class="field-label">密码</label>
          <input
            v-model="password"
            type="password"
            class="field-input"
            placeholder="请输入密码"
            autocomplete="current-password"
            @keydown.enter="handleLogin"
          />
        </div>

        <!-- 错误提示 -->
        <div v-if="errorMsg" class="error-message">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          {{ errorMsg }}
        </div>

        <button
          type="submit"
          class="login-btn"
          :class="{ loading }"
          :disabled="loading"
        >
          <span v-if="loading" class="spinner" />
          <span v-else>登 录</span>
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: var(--bg-primary);
  position: relative;
  overflow: hidden;
}

.login-page::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 20% 30%, rgba(45, 212, 191, 0.06) 0%, transparent 50%),
    radial-gradient(circle at 80% 70%, rgba(59, 130, 246, 0.04) 0%, transparent 50%);
  pointer-events: none;
}

.login-card {
  position: relative;
  width: 380px;
  padding: 40px 32px;
  border-radius: var(--radius-xl);
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  box-shadow: 0 8px 32px -8px rgba(0, 0, 0, 0.3);
}

.brand {
  text-align: center;
  margin-bottom: 32px;
}

.brand-icon {
  color: var(--accent-teal);
  margin-bottom: 12px;
}

.brand-title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.3px;
}

.brand-subtitle {
  font-size: 13px;
  color: var(--text-tertiary);
  margin-top: 4px;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.field-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.field-input {
  height: 40px;
  padding: 0 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  background: var(--bg-surface);
  color: var(--text-primary);
  font-family: var(--font-body);
  font-size: 14px;
  outline: none;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.field-input::placeholder {
  color: var(--text-tertiary);
}

.field-input:focus {
  border-color: var(--accent-teal);
  box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.12);
}

.error-message {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: rgba(248, 113, 113, 0.08);
  border: 1px solid rgba(248, 113, 113, 0.2);
  color: var(--color-error);
  font-size: 13px;
  line-height: 1.4;
}

.login-btn {
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  border: none;
  background: var(--accent-teal);
  color: #0B0F1A;
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity var(--transition-fast), transform var(--transition-fast);
  letter-spacing: 2px;
}

.login-btn:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
}

.login-btn:active:not(:disabled) {
  transform: translateY(0);
}

.login-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-btn.loading {
  opacity: 0.8;
}

.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(11, 15, 26, 0.3);
  border-top-color: #0B0F1A;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 浅色主题 */
[data-theme="light"] .login-card {
  box-shadow: 0 4px 24px -8px rgba(0, 0, 0, 0.08);
}

[data-theme="light"] .login-btn {
  color: #FFFFFF;
  background: #1E293B;
}
</style>
