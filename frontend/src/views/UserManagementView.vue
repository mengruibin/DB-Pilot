<script setup lang="ts">
import { h, ref, onMounted } from 'vue'
import {
  useMessage, useDialog,
  NButton, NDataTable, NModal, NForm, NFormItem,
  NInput, NSelect, NSwitch,
} from 'naive-ui'
import { http } from '@/api/client'
import type { User } from '@/stores/auth'

/** 格式化 ISO 时间为 yyyy-MM-dd HH:mm */
function formatTime(iso: string): string {
  if (!iso) return '-'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const message = useMessage()
const dialog = useDialog()

/** 用户列表数据 */
const users = ref<User[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const loading = ref(false)

/** 创建/编辑弹窗 */
const showModal = ref(false)
const modalMode = ref<'create' | 'edit'>('create')
const editingUser = ref<User | null>(null)
const formData = ref({
  username: '',
  password: '',
  role: 'readonly',
})
const formLoading = ref(false)
const formError = ref('')

/** 角色选项 */
const roleOptions = [
  { label: '管理员 (admin)', value: 'admin' },
  { label: '只读 (readonly)', value: 'readonly' },
]

async function loadUsers() {
  loading.value = true
  try {
    const res = await http.get<{ items: User[]; total: number }>(
      `/api/users?page=${page.value}&pageSize=${pageSize.value}`
    )
    users.value = res.items
    total.value = res.total
  } catch (err: unknown) {
    message.error(err instanceof Error ? err.message : '加载用户列表失败')
  } finally {
    loading.value = false
  }
}

function openCreateModal() {
  modalMode.value = 'create'
  editingUser.value = null
  formData.value = { username: '', password: '', role: 'readonly' }
  formError.value = ''
  showModal.value = true
}

function openEditModal(user: User) {
  modalMode.value = 'edit'
  editingUser.value = user
  formData.value = { username: user.username, password: '', role: user.role }
  formError.value = ''
  showModal.value = true
}

async function handleSubmit() {
  formError.value = ''
  if (modalMode.value === 'create') {
    if (!formData.value.username.trim()) {
      formError.value = '请输入用户名'
      return
    }
    if (!formData.value.password || formData.value.password.length < 6) {
      formError.value = '密码至少 6 位'
      return
    }
  }

  formLoading.value = true
  try {
    if (modalMode.value === 'create') {
      await http.post('/api/users', {
        username: formData.value.username.trim(),
        password: formData.value.password,
        role: formData.value.role,
      })
      message.success('用户创建成功')
    } else if (editingUser.value) {
      const payload: Record<string, unknown> = { role: formData.value.role }
      if (formData.value.password) {
        payload.password = formData.value.password
      }
      await http.put(`/api/users/${editingUser.value.id}`, payload)
      message.success('用户更新成功')
    }
    showModal.value = false
    await loadUsers()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '操作失败'
    // 检查是否为 409 USERNAME_EXISTS
    formError.value = msg
  } finally {
    formLoading.value = false
  }
}

async function toggleActive(user: User) {
  try {
    await http.put(`/api/users/${user.id}`, { is_active: !user.is_active })
    message.success(user.is_active ? '用户已禁用' : '用户已启用')
    await loadUsers()
  } catch (err: unknown) {
    message.error(err instanceof Error ? err.message : '操作失败')
  }
}

function confirmDelete(user: User) {
  dialog.warning({
    title: '确认删除',
    content: `确定要删除用户 "${user.username}" 吗？此操作不可撤销。`,
    positiveText: '确认删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await http.delete(`/api/users/${user.id}`)
        message.success('用户已删除')
        await loadUsers()
      } catch (err: unknown) {
        message.error(err instanceof Error ? err.message : '删除失败')
      }
    },
  })
}

onMounted(() => {
  loadUsers()
})
</script>

<template>
  <div class="user-mgmt-page">
    <div class="page-header">
      <h2 class="page-title">用户管理</h2>
      <n-button type="primary" size="small" @click="openCreateModal">
        <template #icon>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </template>
        创建用户
      </n-button>
    </div>

    <n-data-table
      :columns="[
        { title: '用户名', key: 'username', width: 160 },
        {
          title: '角色',
          key: 'role',
          width: 130,
          render(row: User) {
            return h('span', { class: row.role === 'admin' ? 'role-admin' : 'role-readonly' },
              row.role === 'admin' ? '管理员' : '只读'
            )
          }
        },
        {
          title: '状态',
          key: 'is_active',
          width: 90,
          render(row: User) {
            return h('span', { class: row.is_active ? 'status-active' : 'status-disabled' },
              row.is_active ? '启用' : '禁用'
            )
          }
        },
        { title: '创建时间', key: 'created_at', width: 170, render(row: User) { return formatTime(row.created_at) } },
        {
          title: '操作',
          key: 'actions',
          width: 200,
          render(row: User) {
            return h('div', { class: 'action-btns' }, [
              h(NButton, {
                size: 'tiny',
                quaternary: true,
                onClick: () => openEditModal(row)
              }, '编辑'),
              h(NSwitch, {
                size: 'small',
                value: row.is_active,
                'onUpdate:value': () => toggleActive(row)
              }),
              h(NButton, {
                size: 'tiny',
                quaternary: true,
                type: 'error',
                onClick: () => confirmDelete(row)
              }, '删除'),
            ])
          }
        }
      ]"
      :data="users"
      :loading="loading"
      :bordered="false"
      :single-line="false"
      size="small"
      striped
      :pagination="{
        page: page,
        pageSize: pageSize,
        itemCount: total,
        onChange: (p: number) => { page = p; loadUsers() },
      }"
      class="user-table"
    />

    <!-- 创建/编辑弹窗 -->
    <n-modal v-model:show="showModal" preset="card" :title="modalMode === 'create' ? '创建用户' : '编辑用户'" style="width: 420px;" :bordered="false" :segmented="false">
      <n-form label-placement="top" :show-label="true">
        <n-form-item label="用户名" v-if="modalMode === 'create'">
          <n-input v-model:value="formData.username" placeholder="请输入用户名" />
        </n-form-item>
        <n-form-item label="用户名" v-else>
          <n-input :value="formData.username" disabled />
        </n-form-item>

        <n-form-item :label="modalMode === 'create' ? '密码' : '新密码（可选）'">
          <n-input
            v-model:value="formData.password"
            type="password"
            show-password-on="click"
            :placeholder="modalMode === 'create' ? '至少 6 位' : '留空则不修改'"
          />
        </n-form-item>

        <n-form-item label="角色">
          <n-select v-model:value="formData.role" :options="roleOptions" />
        </n-form-item>

        <div v-if="formError" class="form-error">{{ formError }}</div>

        <div class="modal-actions">
          <n-button @click="showModal = false">取消</n-button>
          <n-button type="primary" :loading="formLoading" @click="handleSubmit">
            {{ modalMode === 'create' ? '创建' : '保存' }}
          </n-button>
        </div>
      </n-form>
    </n-modal>
  </div>
</template>

<style scoped>
.user-mgmt-page {
  padding: 24px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.page-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.user-table {
  flex: 1;
}

.role-admin {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
  background: rgba(45, 212, 191, 0.12);
  color: var(--accent-teal);
}

.role-readonly {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
  background: rgba(148, 163, 184, 0.12);
  color: var(--text-secondary);
}

.status-active {
  color: var(--color-success);
  font-size: 12px;
  font-weight: 500;
}

.status-disabled {
  color: var(--text-tertiary);
  font-size: 12px;
}

.action-btns {
  display: flex;
  align-items: center;
  gap: 8px;
}

.form-error {
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: rgba(248, 113, 113, 0.08);
  border: 1px solid rgba(248, 113, 113, 0.2);
  color: var(--color-error);
  font-size: 13px;
  margin-bottom: 12px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

[data-theme="light"] .role-admin {
  background: rgba(30, 41, 59, 0.08);
  color: #1E293B;
}
</style>
