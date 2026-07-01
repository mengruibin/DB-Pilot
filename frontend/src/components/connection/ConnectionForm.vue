<script setup lang="ts">
/**
 * ConnectionForm — 新建/编辑连接表单
 *
 * 含所有表单字段、校验、测试连接、SSL 配置折叠区。
 * 依据 api-contract §1.1 POST/PUT /api/connections
 * 及 frontend AGENTS.md §2 连接凭据展示规范
 */
import { ref, computed } from 'vue'
import {
  NButton,
  NInput,
  NInputNumber,
  NSelect,
  NCollapse,
  NCollapseItem,
  NSwitch,
} from 'naive-ui'
import { useConnection } from '@/composables/useConnection'
import type { ConnectionConfig, DbType } from '@/types/connection'

const props = defineProps<{
  /** 编辑模式传入的连接配置 */
  connection?: ConnectionConfig | null
}>()

const emit = defineEmits<{
  saved: [config: ConnectionConfig]
  cancel: []
}>()

const {
  form,
  errors,
  submitting,
  testing,
  mode,
  testResultText,
  testSuccess,
  validateField,
  resetForm,
  editConnection,
  handleTest,
  handleSubmit,
} = useConnection()

/** 数据库类型选项 */
const dbTypeOptions = [
  { label: 'MySQL', value: 'mysql' as DbType },
  { label: 'PostgreSQL', value: 'postgresql' as DbType },
  { label: 'Oracle', value: 'oracle' as DbType },
]

/** SSL 折叠面板展开的 panel name 数组 */
const sslExpandedNames = ref<string[]>([])

/** 密码输入框类型控制 */
const showPassword = ref(false)

/** 编辑模式显示密码占位符号 */
const passwordPlaceholder = computed(() => {
  return mode.value === 'edit' ? '•••••••• 不修改则留空' : '输入连接密码'
})

// ─── 初始化 ───

if (props.connection) {
  editConnection(props.connection)
  // 如果已有密码占位，展开 SSL（若 SSL 已启用）
  if (props.connection.ssl_enabled) {
    sslExpandedNames.value = ['ssl']
  }
}

/** 保存操作 */
async function onSave() {
  // 触发全量校验
  const result = await handleSubmit()
  if (result) {
    emit('saved', result)
  }
}

/** 取消操作 */
function onCancel() {
  resetForm()
  emit('cancel')
}
</script>

<template>
  <div class="connection-form">
    <div class="form-panel">
      <!-- 标题 -->
      <h2 class="form-title">
        {{ mode === 'create' ? '新建数据库连接' : '编辑数据库连接' }}
      </h2>

      <!-- 表单字段 -->
      <div class="form-grid">
        <!-- 第一行：名称 + 数据库类型 -->
        <div class="field-group">
          <label class="field-label">连接名称</label>
          <n-input
            v-model:value="form.name"
            placeholder="例如：生产 MySQL"
            :maxlength="64"
            :status="errors.name ? 'error' : undefined"
            clearable
            @blur="validateField('name')"
          />
          <p v-if="errors.name" class="field-error">{{ errors.name }}</p>
        </div>

        <div class="field-group">
          <label class="field-label">数据库类型</label>
          <n-select
            v-model:value="form.db_type"
            :options="dbTypeOptions"
            @blur="validateField('db_type')"
          />
          <p v-if="errors.db_type" class="field-error">{{ errors.db_type }}</p>
        </div>

        <!-- 第二行：主机 + 端口 -->
        <div class="field-group">
          <label class="field-label">主机地址</label>
          <n-input
            v-model:value="form.host"
            placeholder="IP 或域名"
            :status="errors.host ? 'error' : undefined"
            clearable
            @blur="validateField('host')"
          />
          <p v-if="errors.host" class="field-error">{{ errors.host }}</p>
        </div>

        <div class="field-group">
          <label class="field-label">端口</label>
          <n-input-number
            v-model:value="form.port"
            placeholder="3306"
            :min="1"
            :max="65535"
            :status="errors.port ? 'error' : undefined"
            :update-value-on-input="true"
            style="width: 100%;"
            @blur="validateField('port')"
          />
          <p v-if="errors.port" class="field-error">{{ errors.port }}</p>
        </div>

        <!-- 第三行：数据库 + 用户名 -->
        <div class="field-group">
          <label class="field-label">数据库名称</label>
          <n-input
            v-model:value="form.database"
            placeholder="例如：orders"
            :status="errors.database ? 'error' : undefined"
            clearable
            @blur="validateField('database')"
          />
          <p v-if="errors.database" class="field-error">{{ errors.database }}</p>
        </div>

        <div class="field-group">
          <label class="field-label">用户名</label>
          <n-input
            v-model:value="form.user"
            placeholder="数据库用户名"
            :status="errors.user ? 'error' : undefined"
            clearable
            @blur="validateField('user')"
          />
          <p v-if="errors.user" class="field-error">{{ errors.user }}</p>
        </div>

        <!-- 第四行：密码（独占一行） -->
        <div class="field-group full-width">
          <label class="field-label">密码</label>
          <n-input
            v-model:value="form.password"
            :type="showPassword ? 'text' : 'password'"
            :placeholder="passwordPlaceholder"
            :autocomplete="mode === 'edit' ? 'new-password' : 'current-password'"
            clearable
            @click.stop
          >
            <template #suffix>
              <button
                class="password-toggle"
                tabindex="-1"
                type="button"
                @click="showPassword = !showPassword"
              >
                <svg v-if="!showPassword" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                  <circle cx="12" cy="12" r="3"/>
                </svg>
                <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                  <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
                  <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
                  <line x1="1" y1="1" x2="23" y2="23"/>
                </svg>
              </button>
            </template>
          </n-input>
        </div>
      </div>

      <!-- SSL 折叠区 -->
      <div class="ssl-section">
        <n-collapse v-model:expanded-names="sslExpandedNames" :default-expanded-names="[]">
          <n-collapse-item title="SSL 配置" name="ssl">
            <div class="ssl-content">
              <div class="ssl-toggle-row">
                <span class="ssl-label">启用 SSL</span>
                <n-switch v-model:value="form.ssl_enabled" />
              </div>
              <div v-if="form.ssl_enabled" class="ssl-cert-area">
                <label class="field-label">CA 证书（PEM）</label>
                <n-input
                  v-model:value="form.ssl_ca_cert"
                  type="textarea"
                  placeholder="粘贴 CA 证书 PEM 内容…"
                  :rows="4"
                />
              </div>
            </div>
          </n-collapse-item>
        </n-collapse>
      </div>

      <!-- 操作按钮 -->
      <div class="form-actions">
        <div class="test-result" v-if="testResultText">
          <span class="test-badge" :class="{ success: testSuccess, fail: !testSuccess }">
            {{ testSuccess ? '✓' : '✗' }}
          </span>
          <span class="test-text">{{ testResultText }}</span>
        </div>

        <div class="action-buttons">
          <n-button quaternary size="small" @click="onCancel">
            取消
          </n-button>
          <n-button
            v-if="mode === 'edit'"
            :loading="testing"
            :disabled="testing"
            secondary
            size="small"
            @click="handleTest"
          >
            测试连接
          </n-button>
          <n-button
            type="primary"
            :loading="submitting"
            :disabled="submitting"
            size="small"
            @click="onSave"
          >
            {{ mode === 'create' ? '创建连接' : '保存修改' }}
          </n-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.connection-form {
  flex: 1;
  max-width: 680px;
}

.form-panel {
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 24px;
}

.form-title {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}

/* ─── 表单网格 ─── */
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px 20px;
}

.field-group.full-width {
  grid-column: 1 / -1;
}

.field-label {
  display: block;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 4px;
  font-family: var(--font-body);
  letter-spacing: 0.2px;
}

.field-error {
  font-size: 11px;
  color: var(--color-error);
  margin-top: 2px;
  padding-left: 2px;
}

/* 密码切换按钮 */
.password-toggle {
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  padding: 4px;
  display: flex;
  align-items: center;
  border-radius: 4px;
  transition: color var(--transition-fast);
}

.password-toggle:hover {
  color: var(--text-secondary);
}

/* ─── SSL 配置 ─── */
.ssl-section {
  margin-top: 4px;
}

.ssl-content {
  padding: 8px 0;
}

.ssl-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
}

.ssl-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.ssl-cert-area {
  margin-top: 12px;
}

/* ─── 操作按钮 ─── */
.form-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
}

.test-result {
  display: flex;
  align-items: center;
  gap: 6px;
}

.test-badge {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
}

.test-badge.success {
  background: rgba(52, 211, 153, 0.15);
  color: var(--color-success);
}

.test-badge.fail {
  background: rgba(248, 113, 113, 0.15);
  color: var(--color-error);
}

.test-text {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.action-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
