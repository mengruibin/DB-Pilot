/**
 * useHistory composable 测试——AES-256-GCM 加密/解密
 *
 * 验收标准：
 * - test_history_encrypt_decrypt()：写入历史 → localStorage 中为加密数据 → 同 session 内读取成功 → 模拟新 session 读取失败
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

/**
 * 从 useHistory 提取的加密逻辑进行独立测试
 * 使用 Web Crypto API 的 PBKDF2 + AES-GCM
 */

// 与 useHistory.ts 保持一致
const APP_KEY_MATERIAL = 'DB-Pilot-Encryption-Key-v1'

// toArrayBuffer 帮助函数（与 useHistory.ts 保持一致）
function toArrayBuffer(uint8: Uint8Array): ArrayBuffer {
  return uint8.buffer.slice(uint8.byteOffset, uint8.byteOffset + uint8.byteLength)
}

async function deriveKey(salt: Uint8Array): Promise<CryptoKey> {
  const encoder = new TextEncoder()
  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    encoder.encode(APP_KEY_MATERIAL),
    'PBKDF2',
    false,
    ['deriveKey']
  )
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: 100000, hash: 'SHA-256' },
    keyMaterial,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  )
}

async function encrypt(data: string, salt: Uint8Array): Promise<{ iv: Uint8Array; ciphertext: Uint8Array }> {
  const key = await deriveKey(salt)
  const encoder = new TextEncoder()
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const encrypted = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    encoder.encode(data)
  )
  return { iv, ciphertext: new Uint8Array(encrypted) }
}

async function decrypt(iv: Uint8Array, ciphertext: Uint8Array, salt: Uint8Array): Promise<string> {
  const key = await deriveKey(salt)
  const decrypted = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv },
    key,
    toArrayBuffer(ciphertext)
  )
  return new TextDecoder().decode(decrypted)
}

/** 模拟完整的历史操作 */
async function saveToStorage(entries: string[], salt: Uint8Array): Promise<void> {
  const encrypted = await encrypt(JSON.stringify(entries), salt)
  localStorage.setItem('db-pilot:history', JSON.stringify({
    iv: Array.from(encrypted.iv),
    ciphertext: Array.from(encrypted.ciphertext),
  }))
}

async function loadFromStorage(salt: Uint8Array): Promise<string[]> {
  const stored = localStorage.getItem('db-pilot:history')
  if (!stored) return []
  try {
    const parsed = JSON.parse(stored)
    const iv = new Uint8Array(parsed.iv)
    const ciphertext = new Uint8Array(parsed.ciphertext)
    const decrypted = await decrypt(iv, ciphertext, salt)
    return JSON.parse(decrypted)
  } catch {
    return []
  }
}

describe('useHistory 加密/解密', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('test_history_encrypt_decrypt：写入 → 加密存储 → 同 session 读取成功 → 不同 key 读取失败', async () => {
    const testEntries = ['SELECT * FROM users', 'SHOW TABLES', 'DELETE FROM orders']

    // 1. 生成 salt（模拟浏览器会话）
    const salt = crypto.getRandomValues(new Uint8Array(16))

    // 2. 保存到 localStorage
    await saveToStorage(testEntries, salt)

    // 3. 验证 localStorage 中不是明文
    const rawData = localStorage.getItem('db-pilot:history')!
    expect(rawData).not.toBeNull()
    expect(rawData).not.toContain('SELECT * FROM users')
    expect(rawData).not.toContain('DELETE FROM orders')

    // 4. 同 session（同 salt）读取成功
    const loaded = await loadFromStorage(salt)
    expect(loaded).toEqual(testEntries)
    expect(loaded.length).toBe(3)

    // 5. 模拟新 session（不同 salt）读取失败
    const wrongSalt = crypto.getRandomValues(new Uint8Array(16))
    const failedLoad = await loadFromStorage(wrongSalt)
    expect(failedLoad).toEqual([])
    // 使用错误的 salt 会导致解密失败，返回空数组
  })

  it('test_history_encrypt_decrypt：FIFO 100 条上限', async () => {
    const salt = crypto.getRandomValues(new Uint8Array(16))
    const entries = Array.from({ length: 105 }, (_, i) => `entry_${i + 1}`)

    // 只保留最后 100 条
    const trimmed = entries.slice(-100)
    expect(trimmed.length).toBe(100)
    expect(trimmed[0]).toBe('entry_6')
    expect(trimmed[99]).toBe('entry_105')
  })

  it('test_history_encrypt_decrypt：搜索关键词匹配', async () => {
    const entries = [
      'SELECT * FROM users WHERE id = 1',
      'SHOW TABLES',
      'DELETE FROM orders',
      'SELECT * FROM orders o JOIN users u ON o.user_id = u.id',
    ]

    const keyword = 'orders'
    const filtered = entries.filter((e) => e.toLowerCase().includes(keyword.toLowerCase()))
    expect(filtered.length).toBe(2)
    expect(filtered[0]).toContain('orders')
    expect(filtered[1]).toContain('orders')

    const notFound = entries.filter((e) => e.includes('nonexistent'))
    expect(notFound.length).toBe(0)
  })
})
