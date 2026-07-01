/**
 * 历史命令管理 composable
 *
 * AES-256-GCM 加密存储到 localStorage，FIFO 100 条上限，
 * 按键搜索，清空二次确认。
 * 加密密钥基于会话级随机盐派生，浏览器关闭后不可读。
 *
 * 依据 frontend AGENTS.md §4 历史命令管理
 */
import { ref, computed } from 'vue'

// ─── 类型 ───

/** 历史记录条目 */
export interface HistoryEntry {
  /** 输入文本 */
  text: string
  /** 输入模式 */
  mode: 'natural_language' | 'sql_editor'
  /** 时间戳 ISO 8601 */
  timestamp: string
  /** 执行状态 */
  status: 'success' | 'error'
}

// ─── 常量 ───

/** localStorage 键名 */
const STORAGE_KEY = 'db-pilot:history'
/** sessionStorage 键名（存储加密盐） */
const SALT_KEY = 'db-pilot:history-salt'
/** 历史最大条目 */
const MAX_ENTRIES = 100
/** PBKDF2 迭代次数 */
const PBKDF2_ITERATIONS = 100000
/** 应用密钥材料（源码级别混淆，非加密安全） */
const APP_KEY_MATERIAL = 'db-pilot-history-v1'

// ─── 加密工具 ───

/** 将 Uint8Array 安全转换为 ArrayBuffer */
function toArrayBuffer(uint8: Uint8Array): ArrayBuffer {
  const buf = new ArrayBuffer(uint8.byteLength)
  new Uint8Array(buf).set(uint8)
  return buf
}

/** 获取或创建随机盐（存储在 sessionStorage，浏览器关闭后丢失） */
function getOrCreateSalt(): Uint8Array {
  const stored = sessionStorage.getItem(SALT_KEY)
  if (stored) {
    try {
      return new Uint8Array(JSON.parse(stored) as number[])
    } catch {
      // 损坏则重新生成
    }
  }
  const salt = crypto.getRandomValues(new Uint8Array(16))
  sessionStorage.setItem(SALT_KEY, JSON.stringify(Array.from(salt)))
  return salt
}

/** 派生 AES-256-GCM 密钥 */
async function deriveKey(salt: Uint8Array): Promise<CryptoKey> {
  const encoder = new TextEncoder()
  const keyData = encoder.encode(APP_KEY_MATERIAL)
  const keyBuf = toArrayBuffer(keyData)
  const saltBuf = toArrayBuffer(salt)

  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    keyBuf,
    { name: 'PBKDF2' },
    false,
    ['deriveKey']
  )

  return crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: saltBuf,
      iterations: PBKDF2_ITERATIONS,
      hash: 'SHA-256',
    },
    keyMaterial,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  )
}

/** AES-256-GCM 加密 */
async function encrypt(data: string): Promise<{ iv: Uint8Array; ciphertext: ArrayBuffer }> {
  const salt = getOrCreateSalt()
  const key = await deriveKey(salt)
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const encoded = new TextEncoder().encode(data)
  const ivBuf = toArrayBuffer(iv)
  const encodedBuf = toArrayBuffer(encoded)
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: ivBuf },
    key,
    encodedBuf
  )
  return { iv, ciphertext }
}

/** AES-256-GCM 解密 */
async function decrypt(iv: Uint8Array, ciphertext: Uint8Array): Promise<string> {
  const salt = getOrCreateSalt()
  const key = await deriveKey(salt)
  const ivBuf = toArrayBuffer(iv)
  const ctBuf = toArrayBuffer(ciphertext)
  const decrypted = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: ivBuf },
    key,
    ctBuf
  )
  return new TextDecoder().decode(decrypted)
}

// ─── 加密存储序列化 ───

interface EncryptedPayload {
  iv: number[]
  data: number[]
}

/** 读取并解密历史 */
async function loadDecrypted(): Promise<HistoryEntry[]> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []

    const payload: EncryptedPayload = JSON.parse(raw)
    const iv = new Uint8Array(payload.iv)
    const ciphertext = new Uint8Array(payload.data)
    const json = await decrypt(iv, ciphertext)
    return JSON.parse(json) as HistoryEntry[]
  } catch {
    // 解密失败（密钥变更或数据损坏）→ 清空
    localStorage.removeItem(STORAGE_KEY)
    return []
  }
}

/** 加密并写入历史 */
async function saveEncrypted(entries: HistoryEntry[]): Promise<void> {
  const json = JSON.stringify(entries)
  const { iv, ciphertext } = await encrypt(json)
  const payload: EncryptedPayload = {
    iv: Array.from(iv),
    data: Array.from(new Uint8Array(ciphertext)),
  } as EncryptedPayload
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
}

// ─── Composable ───

export function useHistory() {
  /** 解密后的历史条目（响应式） */
  const entries = ref<HistoryEntry[]>([])
  /** 是否已加载 */
  const loaded = ref(false)
  /** 搜索关键词 */
  const searchQuery = ref('')

  /** 过滤后的历史条目 */
  const filteredEntries = computed(() => {
    if (!searchQuery.value.trim()) return entries.value
    const q = searchQuery.value.toLowerCase()
    return entries.value.filter((e) => e.text.toLowerCase().includes(q))
  })

  /** 条目数量 */
  const count = computed(() => entries.value.length)
  const filteredCount = computed(() => filteredEntries.value.length)

  // ─── 初始化 ───

  /** 加载历史（首次调用时执行解密） */
  async function load(): Promise<void> {
    if (loaded.value) return
    try {
      entries.value = await loadDecrypted()
    } catch {
      entries.value = []
    } finally {
      loaded.value = true
    }
  }

  // ─── 添加 ───

  /**
   * 添加一条历史记录
   * FIFO 淘汰：超出上限时移除最旧条目
   */
  async function addEntry(
    text: string,
    mode: 'natural_language' | 'sql_editor',
    status: 'success' | 'error'
  ): Promise<void> {
    // 加载（如果尚未加载）
    if (!loaded.value) await load()

    const newEntry: HistoryEntry = {
      text: text.slice(0, 80), // 截断前 80 字符
      mode,
      timestamp: new Date().toISOString(),
      status,
    }

    // FIFO: 追加到末尾，超出上限移除最旧
    entries.value.push(newEntry)
    if (entries.value.length > MAX_ENTRIES) {
      entries.value = entries.value.slice(-MAX_ENTRIES)
    }

    // 持久化
    await saveEncrypted(entries.value)
  }

  // ─── 搜索 ───

  function setSearchQuery(query: string): void {
    searchQuery.value = query
  }

  function clearSearch(): void {
    searchQuery.value = ''
  }

  // ─── 清空 ───

  /**
   * 清空所有历史
   */
  async function clearHistory(): Promise<void> {
    entries.value = []
    localStorage.removeItem(STORAGE_KEY)
    searchQuery.value = ''
  }

  // ─── 格式化 ───

  /** 格式化时间戳为可读文本 */
  function formatTimestamp(iso: string): string {
    try {
      const d = new Date(iso)
      const now = new Date()
      const isToday =
        d.getFullYear() === now.getFullYear() &&
        d.getMonth() === now.getMonth() &&
        d.getDate() === now.getDate()

      const time = d.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      })

      if (isToday) return time
      const date = d.toLocaleDateString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
      })
      return `${date} ${time}`
    } catch {
      return '—'
    }
  }

  /** 截断显示文本 */
  function truncateText(text: string, maxLen = 80): string {
    return text.length > maxLen ? `${text.slice(0, maxLen)}…` : text
  }

  return {
    // state
    entries,
    loaded,
    searchQuery,

    // getters
    filteredEntries,
    count,
    filteredCount,

    // methods
    load,
    addEntry,
    setSearchQuery,
    clearSearch,
    clearHistory,
    formatTimestamp,
    truncateText,
  }
}
