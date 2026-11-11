import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

test('登录状态从服务器恢复，初始化后普通注册，退出清除身份', async () => {
  const user = { id: 'u1', name: '用户', username: 'testuser', role: 'user', admin_requested: true }
  const api = { getCurrentUser: async () => null, getAuthConfig: async () => ({ setup_required: true }), signup: async (...args) => { assert.deepEqual(args, ['testuser', 'test-password', true, true]); return user }, logout: async () => {} }
  const { useAuthStore } = createTypeScriptLoader({ '../api/auth': api })('src/stores/auth.ts')
  setActivePinia(createPinia())
  const store = useAuthStore()
  await store.initialize()
  assert.equal(store.setupRequired, true)
  await store.register('testuser', 'test-password', true)
  assert.equal(store.user.id, 'u1')
  assert.equal(store.setupRequired, false)
  await store.signOut()
  assert.equal(store.user, null)
  assert.equal(store.loading, false)
})

test('同一浏览器的会话恢复记录按账号隔离', () => {
  const storage = createTypeScriptLoader()('src/utils/chatStorage.ts')
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const values = new Map()
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) } })
  try {
    storage.saveLastChatId('chat-a', 'user-a')
    storage.saveLastChatId('chat-b', 'user-b')
    assert.equal(storage.getLastChatId('user-a'), 'chat-a')
    assert.equal(storage.getLastChatId('user-b'), 'chat-b')
    storage.clearLastChatId('user-a')
    assert.equal(storage.getLastChatId('user-a'), null)
    assert.equal(storage.getLastChatId('user-b'), 'chat-b')
  } finally { if (original) Object.defineProperty(globalThis, 'localStorage', original); else delete globalThis.localStorage }
})
