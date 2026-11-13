import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

function fixture() {
  const chats = ['a', 'b'].map(id => ({ id, title: id, created_at: 1, updated_at: 1 }))
  const saved = new Map(chats.map(chat => [chat.id, [{ id: `user-${chat.id}`, chat_id: chat.id, role: 'user', content: `问题${chat.id}`, status: 'completed', error: null, output: [] }]]))
  const streams = new Map()
  const streamChatReply = (id, onText, signal, onTool) => new Promise((resolve, reject) => {
    let content = ''
    const finish = (status, messageId, error) => {
      saved.get(id).push({ id: messageId, chat_id: id, role: 'assistant', content, status, error: error?.message ?? null, output: [] })
      if (error) reject(error); else resolve(messageId)
    }
    streams.set(id, { signal, text: text => { content += text; onText(text) }, tool: onTool, resolve: messageId => finish('completed', messageId), fail: () => finish('failed', `failed-${id}`, new Error(`${id}断线`)) })
    signal.addEventListener('abort', () => finish('cancelled', `cancelled-${id}`, new DOMException('停止', 'AbortError')), { once: true })
  })
  const api = { listMessages: async id => ({ items: [...saved.get(id)], has_more: false, next_before_sequence: null }) }
  const loader = createTypeScriptLoader({ '../api/chats': api, '../api/stream': { streamChatReply } })
  setActivePinia(createPinia())
  const store = loader('src/stores/chat.ts').useChatStore()
  store.restoringChat = false
  store.chats = chats
  store.currentChat = chats[0]
  store.messages = [...saved.get('a')]
  return { store, streams, chats }
}

test('会话可切换并同时生成，消息与草稿隔离，停止仅影响当前会话', async () => {
  const { store, streams, chats } = fixture()
  const first = store.retryReply()
  assert.equal(store.navigationBusy, false)
  assert.equal(await store.openChatSession(chats[1]), true)
  assert.equal(store.sendingMessage, false)
  store.inputText = 'B的未发送草稿'
  const second = store.retryReply()
  streams.get('a').text('A的内容')
  streams.get('b').text('B的内容')
  assert.deepEqual(store.generatingChatIds, ['a', 'b'])
  assert.equal(store.messages.at(-1).content, 'B的内容')
  assert.equal(await store.openChatSession(chats[0]), true)
  assert.equal(store.messages.at(-1).content, 'A的内容')
  const draft = store.messages.at(-1)
  assert.equal(await store.openChatSession(chats[0]), true)
  assert.equal(store.messages.at(-1), draft)
  store.stopReply()
  await first
  assert.equal(streams.get('b').signal.aborted, false)
  streams.get('b').resolve('answer-b')
  await second
  assert.equal(store.messages.at(-1).status, 'cancelled')
  assert.equal(await store.openChatSession(chats[1]), true)
  assert.equal(store.messages.at(-1).content, 'B的内容')
  assert.equal(store.inputText, 'B的未发送草稿')
  assert.equal(store.sendingMessage, false)
  store.$dispose()
})

test('后台错误不污染当前会话，空白页可打开，销毁时停止所有流', async () => {
  const { store, streams, chats } = fixture()
  const first = store.retryReply()
  await store.openChatSession(chats[1])
  store.errorMessage = 'B的提示'
  streams.get('a').fail()
  await first
  assert.equal(store.errorMessage, 'B的提示')
  await store.openChatSession(chats[0])
  assert.equal(store.errorMessage, 'a断线')
  const retry = store.retryReply()
  assert.equal(store.startBlankChat(), true)
  assert.equal(store.currentChat, null)
  assert.equal(streams.get('a').signal.aborted, false)
  await store.openChatSession(chats[1])
  const second = store.retryReply()
  store.$dispose()
  await Promise.all([retry, second])
  assert.equal(streams.get('a').signal.aborted, true)
  assert.equal(streams.get('b').signal.aborted, true)
})
