import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const chat = { id: 'chat', title: '附件聊天', created_at: 1, updated_at: 1 }
const file = (id, status = 'completed') => ({ id, filename: `资料${id}.txt`, size: 10, status, content_type: 'text/plain', error: null, created_at: 1, updated_at: 1 })
const attachment = { file_id: 'f1', filename: '资料f1.txt', size: 10 }
const saved = { id: 'user', role: 'user', content: '问题', output: [], attachments: [attachment], status: 'completed', error: null }
function fixture(overrides = {}, stream) { // 使用真实仓库，替换网络和模型调用
  const calls = []
  const api = { createChat: async () => ({ ...chat, id: 'new' }), deleteChat: async () => {}, removeChatFile: async () => {}, listMessages: async () => ({ items: [saved], has_more: false, next_before_sequence: null }), saveUserMessage: async () => saved, ...overrides }
  const tracked = Object.fromEntries(Object.entries(api).map(([name, action]) => [name, (...args) => { calls.push([name, ...args]); return action(...args) }]))
  let streamCalls = 0
  const streamChatReply = (...args) => { streamCalls++; return stream ? stream(...args) : Promise.resolve('reply') }
  const load = createTypeScriptLoader({ '../api/chats': tracked, '../api/stream': { streamChatReply } })
  setActivePinia(createPinia())
  const store = load('src/stores/chat.ts').useChatStore()
  store.restoringChat = false
  store.currentChat = { ...chat }
  store.inputText = '问题'
  return { store, calls, get streamCalls() { return streamCalls } }
}
test('保存消息发送附件 ID，旧调用使用空列表', async (context) => {
  const payloads = []
  context.mock.method(globalThis, 'fetch', async (_url, options) => { payloads.push(JSON.parse(options.body)); return Response.json(saved) })
  const { saveUserMessage } = createTypeScriptLoader()('src/api/chats.ts')
  await saveUserMessage('chat', '问题', ['f1'])
  await saveUserMessage('chat', '纯文本')
  assert.deepEqual(payloads, [{ content: '问题', file_ids: ['f1'] }, { content: '纯文本', file_ids: [] }])
})
test('附件校验、去重、最多3份和移除都在仓库执行', () => {
  const { store } = fixture()
  store.currentChat = null
  assert.equal(store.addAttachment(file('f1')), true)
  store.removeAttachment('f1') // 空白页可先准备附件，首次发送时再创建会话
  store.currentChat = chat
  assert.equal(store.addAttachment(file('bad', 'failed')), false)
  for (const id of ['f1', 'f2', 'f3']) assert.equal(store.addAttachment(file(id)), true)
  assert.equal(store.addAttachment(file('f1')), false)
  assert.equal(store.addAttachment(file('f4')), false)
  store.loadingOlderMessages = true
  store.removeAttachment('f1')
  assert.equal(store.selectedAttachments.length, 3)
  assert.equal(store.addAttachment(file('f4')), false)
  store.loadingOlderMessages = false
  store.removeAttachment('f1')
  assert.deepEqual(store.selectedAttachments.map((item) => item.file_id), ['f2', 'f3'])
})
test('保存成功清空附件草稿，返回的附件进入历史消息', async () => {
  const { store, calls } = fixture()
  store.addAttachment(file('f1'))
  await store.sendMessage()
  assert.deepEqual(calls[0], ['saveUserMessage', chat.id, '问题', ['f1']])
  assert.deepEqual(store.messages[0].attachments, [attachment])
  assert.deepEqual(store.messages[1].attachments, [])
  assert.deepEqual(store.selectedAttachments, [])
  assert.deepEqual(store.currentChat.files, [attachment])
  assert.equal(store.inputText, '')
})
test('保存失败保留问题和附件；不会启动模型', async () => {
  const state = fixture({ saveUserMessage: async () => { throw new Error('文件已失效') } })
  state.store.addAttachment(file('f1'))
  await state.store.sendMessage()
  assert.equal(state.store.inputText, '问题')
  assert.deepEqual(state.store.selectedAttachments, [attachment])
  assert.equal(state.store.messages.length, 0)
  assert.equal(state.streamCalls, 0)
  assert.equal(state.store.errorMessage, '文件已失效')
})

test('移出会话只改变持续资料，保留历史消息和原附件快照，失败可重试', async () => {
  let fail = true
  const { store, calls } = fixture({ removeChatFile: async () => { if (fail) throw new Error('暂不可移出') } })
  store.currentChat.files = [{ ...attachment }]
  store.messages = [saved]
  assert.equal(store.addAttachment(file('f1')), false)
  await store.removeConversationFile('f1')
  assert.equal(store.currentChat.files.length, 1)
  fail = false
  await store.removeConversationFile('f1')
  assert.deepEqual(store.currentChat.files, [])
  assert.deepEqual(store.messages[0].attachments, [attachment])
  assert.equal(store.addAttachment(file('f1')), true)
  assert.equal(calls.filter(([name]) => name === 'deleteFile').length, 0)
})
test('生成失败后重试复用已保存附件，不重复保存用户消息', async () => {
  let failed = true
  const { store, calls } = fixture({}, async () => { if (failed) throw new Error('模型失败'); return 'reply' })
  store.addAttachment(file('f1'))
  await store.sendMessage()
  assert.equal(store.messages.at(-1).status, 'failed')
  assert.deepEqual(store.selectedAttachments, [])
  failed = false
  await store.retryReply()
  assert.equal(calls.filter(([name]) => name === 'saveUserMessage').length, 1)
  assert.deepEqual(store.messages[0].attachments, [attachment])
})
test('保存期间停止：保存成功仍更新页面并清空草稿，不再请求模型', async () => {
  let finish
  const state = fixture({ saveUserMessage: () => new Promise((resolve) => { finish = resolve }) })
  state.store.addAttachment(file('f1'))
  const pending = state.store.sendMessage()
  state.store.stopReply()
  finish(saved)
  await pending
  assert.equal(state.streamCalls, 0)
  assert.equal(state.store.messages.length, 1)
  assert.deepEqual(state.store.messages[0].attachments, [attachment])
  assert.deepEqual(state.store.selectedAttachments, [])
  assert.equal(state.store.inputText, '')
})
test('新建和切换成功清理附件，切换失败保留草稿；历史附件正常恢复', async () => {
  let failed = true
  const { store } = fixture({ listMessages: async () => { if (failed) throw new Error('消息读取失败'); return { items: [saved], has_more: false, next_before_sequence: null } } })
  store.addAttachment(file('f1'))
  assert.equal(await store.openChatSession(chat), false)
  assert.deepEqual(store.selectedAttachments, [attachment])
  failed = false
  await store.openChatSession(chat)
  assert.deepEqual(store.selectedAttachments, [])
  assert.deepEqual(store.messages[0].attachments, [attachment])
  store.addAttachment(file('f1'))
  await store.createNewChat()
  assert.deepEqual(store.selectedAttachments, [])
})
test('删除其他会话保留附件，删除当前会话清理附件', async () => {
  const { store } = fixture()
  store.addAttachment(file('f1'))
  await store.deleteChatSession('other')
  assert.deepEqual(store.selectedAttachments, [attachment])
  await store.deleteChatSession(chat.id)
  assert.deepEqual(store.selectedAttachments, [])
})

test('后端已删除文件时，即使聊天刚进入忙碌状态，也清理失效的附件草稿', () => {
  const { store } = fixture()
  store.addAttachment(file('f1'))
  store.sendingMessage = true
  store.messages = [{ ...saved, attachments: [{ ...attachment }] }]
  store.markFileDeleted('f1')
  assert.deepEqual(store.selectedAttachments, [])
  assert.equal(store.messages[0].attachments[0].deleted, true)
  assert.equal(store.messages[0].attachments[0].filename, attachment.filename)
})
