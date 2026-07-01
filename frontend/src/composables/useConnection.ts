/**
 * 连接表单编排 composable
 *
 * 处理表单状态管理、字段校验、创建/更新提交、连接测试
 *
 * 依据 api-contract §1.1 POST/PUT /api/connections
 * 及 frontend AGENTS.md §2 连接凭据展示规范
 */
import { reactive, ref, computed, watch } from 'vue'
import { useConnectionStore } from '@/stores/connection'
import type { DbType, ConnectionConfig } from '@/types/connection'

/** 表单数据接口 */
export interface ConnectionFormData {
  name: string
  db_type: DbType
  host: string
  port: number | null
  database: string
  user: string
  password: string
  ssl_enabled: boolean
  ssl_ca_cert: string
}

/** 字段错误映射 */
export interface FieldErrors {
  name?: string
  db_type?: string
  host?: string
  port?: string
  database?: string
  user?: string
}

/** 各数据库类型默认端口 */
const DEFAULT_PORTS: Record<DbType, number> = {
  mysql: 3306,
  postgresql: 5432,
  oracle: 1521,
}

/** 创建空白表单数据 */
export function createEmptyForm(): ConnectionFormData {
  return {
    name: '',
    db_type: 'mysql',
    host: '',
    port: 3306,
    database: '',
    user: '',
    password: '',
    ssl_enabled: false,
    ssl_ca_cert: '',
  }
}

/** 从已有连接填充表单（编辑模式） */
export function fromConnection(config: ConnectionConfig): ConnectionFormData {
  return {
    name: config.name,
    db_type: config.db_type,
    host: config.host,
    port: config.port,
    database: config.database,
    user: config.user,
    password: '', // 编辑时密码不在 API 响应中，用户未修改则不更新
    ssl_enabled: config.ssl_enabled,
    ssl_ca_cert: config.ssl_ca_cert ?? '',
  }
}

export function useConnection() {
  const store = useConnectionStore()

  /** 表单数据 */
  const form = reactive<ConnectionFormData>(createEmptyForm())

  /** 字段错误 */
  const errors = reactive<FieldErrors>({})

  /** 是否正在提交 */
  const submitting = ref(false)

  /** 是否正在测试 */
  const testing = ref(false)

  /** 提交模式 */
  const mode = ref<'create' | 'edit'>('create')

  /** 编辑中的连接 ID */
  const editId = ref<string | null>(null)

  /** 测试结果文本 */
  const testResultText = ref<string | null>(null)

  /** 测试是否成功 */
  const testSuccess = ref<boolean | null>(null)

  /** 表单是否有效 */
  const isValid = computed(() => {
    return Object.keys(errors).length === 0
      && form.name.trim().length > 0
      && form.host.trim().length > 0
      && form.port !== null && form.port >= 1 && form.port <= 65535
  })

  // ─── 数据库类型变更时自动更新默认端口 ───
  watch(() => form.db_type, (newType) => {
    if (form.port === null || form.port === DEFAULT_PORTS[form.db_type]) {
      form.port = DEFAULT_PORTS[newType]
    }
  })

  // ─── 校验函数 ───

  /** 校验单个字段 */
  function validateField(field: keyof FieldErrors): void {
    switch (field) {
      case 'name': {
        const v = form.name.trim()
        if (!v) errors.name = '连接名称不能为空'
        else if (v.length > 64) errors.name = '连接名称最多 64 个字符'
        else delete errors.name
        break
      }
      case 'db_type': {
        const valid: DbType[] = ['mysql', 'postgresql', 'oracle']
        if (!valid.includes(form.db_type)) errors.db_type = '请选择有效的数据库类型'
        else delete errors.db_type
        break
      }
      case 'host': {
        if (!form.host.trim()) errors.host = '主机地址不能为空'
        else delete errors.host
        break
      }
      case 'port': {
        const p = form.port
        if (p === null || p === undefined) errors.port = '端口不能为空'
        else if (!Number.isInteger(p) || p < 1 || p > 65535) errors.port = '端口范围 1-65535'
        else delete errors.port
        break
      }
      case 'database': {
        if (!form.database.trim()) errors.database = '数据库名称不能为空'
        else delete errors.database
        break
      }
      case 'user': {
        if (!form.user.trim()) errors.user = '用户名不能为空'
        else delete errors.user
        break
      }
    }
  }

  /** 全量校验 */
  function validateAll(): boolean {
    validateField('name')
    validateField('db_type')
    validateField('host')
    validateField('port')
    validateField('database')
    validateField('user')
    return Object.keys(errors).length === 0
  }

  // ─── 表单操作 ───

  /** 重置表单为初始状态 */
  function resetForm(data?: ConnectionFormData): void {
    const initial = data ?? createEmptyForm()
    form.name = initial.name
    form.db_type = initial.db_type
    form.host = initial.host
    form.port = initial.port
    form.database = initial.database
    form.user = initial.user
    form.password = initial.password
    form.ssl_enabled = initial.ssl_enabled
    form.ssl_ca_cert = initial.ssl_ca_cert
    // 清空错误
    Object.keys(errors).forEach((k) => delete errors[k as keyof FieldErrors])
    testResultText.value = null
    testSuccess.value = null
    mode.value = 'create'
    editId.value = null
  }

  /** 进入编辑模式 */
  function editConnection(config: ConnectionConfig): void {
    mode.value = 'edit'
    editId.value = config.id
    const data = fromConnection(config)
    form.name = data.name
    form.db_type = data.db_type
    form.host = data.host
    form.port = data.port
    form.database = data.database
    form.user = data.user
    form.password = data.password
    form.ssl_enabled = data.ssl_enabled
    form.ssl_ca_cert = data.ssl_ca_cert
    testResultText.value = null
    testSuccess.value = null
  }

  /** 测试连接 */
  async function handleTest(): Promise<void> {
    if (testing.value) return

    // 如果已有 activeId 且 form 未修改，直接测试已有连接
    // 否则先保存再测试（简化：仅当编辑已有连接时测试）
    if (mode.value === 'edit' && editId.value) {
      testing.value = true
      testResultText.value = null
      testSuccess.value = null
      try {
        await store.testConnection(editId.value)
        if (store.testResult?.success) {
          testSuccess.value = true
          testResultText.value = `延迟 ${store.testLatencyMs}ms · ${store.testResult.version}`
        } else {
          testSuccess.value = false
          testResultText.value = store.testError || '连接测试失败'
        }
      } catch {
        testSuccess.value = false
        testResultText.value = '连接测试失败'
      } finally {
        testing.value = false
      }
    }
  }

  /** 提交表单（创建或更新） */
  async function handleSubmit(): Promise<ConnectionConfig | null> {
    if (!validateAll()) return null

    submitting.value = true
    try {
      if (mode.value === 'create') {
        // 创建连接
        const created = await store.addConnection({
          name: form.name.trim(),
          db_type: form.db_type,
          host: form.host.trim(),
          port: form.port ?? DEFAULT_PORTS[form.db_type],
          database: form.database.trim(),
          user: form.user.trim(),
          password: form.password,
          ssl_enabled: form.ssl_enabled,
          ssl_ca_cert: form.ssl_ca_cert || undefined,
        })
        return created
      } else {
        // 更新连接：仅当密码非空时才更新密码
        if (editId.value) {
          const payload: Parameters<typeof store.updateConnection>[1] = {
            name: form.name.trim(),
            db_type: form.db_type,
            host: form.host.trim(),
            port: form.port ?? DEFAULT_PORTS[form.db_type],
            database: form.database.trim(),
            user: form.user.trim(),
            ssl_enabled: form.ssl_enabled,
          }
          // 用户手动修改了密码才更新
          if (form.password.length > 0) {
            payload.password = form.password
          }
          if (form.ssl_ca_cert) {
            payload.ssl_ca_cert = form.ssl_ca_cert
          }
          const updated = await store.updateConnection(editId.value, payload)
          return updated
        }
      }
    } finally {
      submitting.value = false
    }
    return null
  }

  return {
    form,
    errors,
    submitting,
    testing,
    mode,
    editId,
    testResultText,
    testSuccess,
    isValid,
    validateField,
    validateAll,
    resetForm,
    editConnection,
    handleTest,
    handleSubmit,
  }
}
