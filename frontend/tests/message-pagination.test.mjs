import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const chat = { id: 'chat-1', title: '分页测试', created_at: 1, updated_at: 1 } // 测试会话
const message = (sequence) => ({ id: `m-${sequence}`, role: 'user', content: `消息${sequence}`, status: 'completed', error: null }) // 构造页面消息
const page = (numbers, cursor = null) => ({ items: numbers.map(message), has_more: cursor !== null, next_before_sequence: cursor }) // 构造分页响应
function fixture(overrides = {}) { // 使用独立状态和模拟接口，不请求真实服务
  const calls = []
  const api = { listMessages: async () => page([51, 52], 51), createChat: async () => ({ ...chat, id: 'new' }), deleteChat: async () => {}, ...overrides }
  const tracked = Object.fromEntries(Object.entries(api).map(([name, action]) => [name, (...args) => { calls.push([name, ...args]); return action(...args) }]))
  const load = createTypeScriptLoader({ '../api/chats': tracked, '../api/stream': { streamChatReply: () => { throw new Error('不应调用模型') } } })
  setActivePinia(createPinia())
  const store = load('src/stores/chat.ts').useChatStore()
  store.restoringChat = false
  return { store, calls }
}
test('API 使用序号边界、默认 8 条并编码会话 ID', async (context) => {
  const urls = []
  context.mock.method(globalThis, 'fetch', async (url) => { urls.push(url); return Response.json(page([51], 51)) })
  const { listMessages } = createTypeScriptLoader()('src/api/chats.ts')
  assert.deepEqual(await listMessages('a/b'), page([51], 51))
  await listMessages('a/b', 51, 20)
  assert.deepEqual(urls, ['/api/chats/a%2Fb/messages?limit=8', '/api/chats/a%2Fb/messages?limit=20&before_sequence=51'])
})
test('API 拒绝旧数组和缺少有效边界的分页响应', async (context) => {
  const { listMessages } = createTypeScriptLoader()('src/api/chats.ts')
  for (const body of [[], { items: [], has_more: true, next_before_sequence: null }, { items: [], has_more: true, next_before_sequence: 0 }]) {
    context.mock.method(globalThis, 'fetch', async () => Response.json(body))
    await assert.rejects(listMessages(chat.id), /消息分页响应格式不正确/)
  }
})
test('打开会话保存分页信息；加载更早消息去重并前插', async () => {
  const { store, calls } = fixture({ listMessages: async (_, cursor) => cursor ? { ...page([49, 50, 51]), items: [message(49), message(49), message(50), message(51)] } : page([51, 52], 51) })
  assert.equal(await store.openChatSession(chat), true)
  assert.equal(store.nextBeforeSequence, 51)
  assert.equal(await store.loadOlderMessages(), true)
  assert.deepEqual(store.messages.map((item) => item.id), ['m-49', 'm-50', 'm-51', 'm-52'])
  assert.equal(store.hasOlderMessages, false)
  assert.equal(store.nextBeforeSequence, null)
  assert.deepEqual(calls, [['listMessages', chat.id], ['listMessages', chat.id, 51]])
})
test('旧消息读取失败保留内容与边界，重试仍使用原边界', async () => {
  let failed = true
  const { store, calls } = fixture({ listMessages: async (_, cursor) => { if (!cursor) return page([51, 52], 51); if (failed) throw new Error('读取失败'); return page([50]) } })
  await store.openChatSession(chat)
  assert.equal(await store.loadOlderMessages(), false)
  assert.deepEqual(store.messages, page([51, 52]).items)
  assert.equal(store.nextBeforeSequence, 51)
  assert.equal(store.hasOlderMessages, true)
  assert.equal(store.loadingOlderMessages, false)
  failed = false
  assert.equal(await store.loadOlderMessages(), true)
  assert.deepEqual(calls.slice(1), [['listMessages', chat.id, 51], ['listMessages', chat.id, 51]])
})
test('加载期间拦截重复加载以及创建、切换、删除、发送、重试', async () => {
  let finish
  const { store, calls } = fixture({ listMessages: () => new Promise((resolve) => { finish = resolve }) })
  store.currentChat = chat
  store.messages = [message(51)]
  store.hasOlderMessages = true
  store.nextBeforeSequence = 51
  store.inputText = '新问题'
  const pending = store.loadOlderMessages()
  assert.equal(store.pageBusy, true)
  assert.equal(store.inputDisabled, true)
  assert.equal(await store.loadOlderMessages(), false)
  assert.equal(await store.createNewChat(), null)
  assert.equal(await store.openChatSession(chat), false)
  assert.equal(await store.deleteChatSession(chat.id), false)
  await store.sendMessage()
  await store.retryReply()
  assert.equal(calls.length, 1)
  finish(page([50]))
  await pending
  assert.equal(store.pageBusy, false)
})
test('无会话、无更多消息或无边界时不发请求', async () => {
  const { store, calls } = fixture()
  assert.equal(await store.loadOlderMessages(), false)
  store.currentChat = chat
  assert.equal(await store.loadOlderMessages(), false)
  store.hasOlderMessages = true
  assert.equal(await store.loadOlderMessages(), false)
  assert.deepEqual(calls, [])
})
test('新建及删除当前会话清理分页，删除其他会话保留分页', async () => {
  const { store } = fixture()
  await store.openChatSession(chat)
  await store.deleteChatSession('other')
  assert.equal(store.nextBeforeSequence, 51)
  await store.deleteChatSession(chat.id)
  assert.equal(store.hasOlderMessages, false)
  assert.equal(store.nextBeforeSequence, null)
  await store.openChatSession(chat)
  await store.createNewChat()
  assert.equal(store.hasOlderMessages, false)
  assert.equal(store.nextBeforeSequence, null)
})
test('请求返回时会话或边界已变更，忽略过期结果', async () => {
  for (const change of ['chat', 'cursor']) {
    let finish
    const { store } = fixture({ listMessages: () => new Promise((resolve) => { finish = resolve }) })
    store.currentChat = chat
    store.messages = [message(51)]
    store.hasOlderMessages = true
    store.nextBeforeSequence = 51
    const pending = store.loadOlderMessages()
    if (change === 'chat') store.currentChat = { ...chat, id: 'other' }
    else store.nextBeforeSequence = 40
    finish(page([50]))
    assert.equal(await pending, false)
    assert.deepEqual(store.messages, [message(51)])
    assert.equal(store.loadingOlderMessages, false)
  }
})
