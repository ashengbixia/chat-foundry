import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { parse } from '@vue/compiler-sfc'
import ts from 'typescript'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const chat = { id: 'saved-chat', title: '测试会话', created_at: 1, updated_at: 1 } // 模拟的上次会话
const history = [{ id: 'test-message', role: 'user', content: '测试问题', status: 'completed', error: null }] // 模拟历史消息

function fixture(overrides = {}) { // 每个测试使用独立 store 和模拟接口
  const calls = [] // 记录接口调用顺序和参数
  const api = { listChats: async () => [chat], getChat: async () => chat, listMessages: async () => ({ items: history, has_more: false, next_before_sequence: null }), ...overrides } // 默认成功的模拟接口
  const trackedApi = Object.fromEntries(Object.entries(api).map(([name, action]) => [name, (...args) => { calls.push([name, ...args]); return action(...args) }])) // 跟踪调用但不发送网络请求
  const load = createTypeScriptLoader({ '../api/chats': trackedApi, '../api/stream': { streamChatReply: () => { throw new Error('初始化不应请求模型') } } }) // 阻止意外模型请求
  setActivePinia(createPinia())
  return { store: load('src/stores/chat.ts').useChatStore(), calls }
}

test('无上次记录：只加载会话列表，结束恢复状态', async () => {
  const { store, calls } = fixture()
  assert.deepEqual(await store.initializeChat(null), { status: 'empty', chat: null })
  assert.deepEqual(calls, [['listChats']])
  assert.equal(store.restoringChat, false)
  assert.equal(store.currentChat, null)
})

test('空白页开关搜索不改草稿或创建会话，首次创建才提交独立布尔字段', async () => {
  const { store, calls } = fixture({ createChat: async (title, ids, enabled) => ({ ...chat, web_search_enabled: enabled }) })
  await store.initializeChat(null)
  calls.length = 0
  store.inputText = 'python'
  assert.equal(await store.setWebSearchEnabled(true), true)
  assert.equal(store.webSearchEnabled, true)
  assert.equal(store.inputText, 'python')
  assert.equal(store.currentChat, null)
  assert.deepEqual(calls, [])
  await store.createNewChat()
  assert.deepEqual(calls, [['createChat', '新会话', [], true]])
  assert.equal(store.webSearchEnabled, true)
})

test('会话搜索设置失败保持原状态，保存后可恢复且新对话默认关闭', async () => {
  let failing = true, enabled = false
  const { store } = fixture({ getChat: async () => ({ ...chat, web_search_enabled: enabled }), setChatFeatures: async (id, value) => { if (failing) throw new Error('保存失败'); enabled = value; return { ...chat, web_search_enabled: value } } })
  await store.initializeChat(chat.id)
  store.inputText = 'python'
  assert.equal(await store.setWebSearchEnabled(true), false)
  assert.equal(store.webSearchEnabled, false)
  assert.equal(store.inputText, 'python')
  failing = false
  assert.equal(await store.setWebSearchEnabled(true), true)
  await store.openChatSession({ ...chat, id: 'other', web_search_enabled: false })
  assert.equal(store.webSearchEnabled, false)
  await store.initializeChat(chat.id)
  assert.equal(store.webSearchEnabled, true)
  store.startBlankChat()
  assert.equal(store.webSearchEnabled, false)
})

test('正常恢复：列表不重复，消息只请求一次', async () => {
  const { store, calls } = fixture()
  assert.deepEqual(await store.initializeChat(chat.id), { status: 'restored', chat })
  assert.deepEqual(calls, [['listChats'], ['getChat', chat.id], ['listMessages', chat.id]])
  assert.equal(store.chats.length, 1)
  assert.deepEqual(store.currentChat, chat)
  assert.deepEqual(store.messages, history)
  assert.equal(store.restoringChat, false)
  assert.equal(store.loadingMessages, false)
})

test('上次会话不在首页：补入列表并恢复', async () => {
  const { store } = fixture({ listChats: async () => [] })
  assert.equal((await store.initializeChat(chat.id)).status, 'restored')
  assert.deepEqual(store.chats, [chat])
})

test('会话确实不存在：返回 missing，不请求消息', async () => {
  const { store, calls } = fixture({ getChat: async () => null })
  assert.deepEqual(await store.initializeChat(chat.id), { status: 'missing', chat: null })
  assert.equal(calls.some(([name]) => name === 'listMessages'), false)
  assert.equal(store.restoringChat, false)
})

test('列表请求失败：不误判为 missing，也不继续恢复', async () => {
  const { store, calls } = fixture({ listChats: async () => { throw new Error('列表网络错误') } })
  store.chats = [chat]
  assert.equal((await store.initializeChat(chat.id)).status, 'failed')
  assert.deepEqual(calls, [['listChats']])
  assert.deepEqual(store.chats, [chat])
  assert.equal(store.errorMessage, '列表网络错误')
  assert.equal(store.restoringChat, false)
  assert.equal(store.loadingChats, false)
})

test('查询会话失败：保留失败语义，不清除记录', async () => {
  const { store } = fixture({ getChat: async () => { throw new Error('后端不可用') } })
  assert.equal((await store.initializeChat(chat.id)).status, 'failed')
  assert.equal(store.errorMessage, '后端不可用')
  assert.equal(store.restoringChat, false)
})

test('消息请求失败：不覆盖原来的消息和输入', async () => {
  const { store } = fixture({ listMessages: async () => { throw new Error('读取消息失败') } })
  const previous = { ...chat, id: 'previous' } // 请求失败时应保留的当前会话
  store.currentChat = previous
  store.messages = history
  store.inputText = '保留草稿'
  assert.equal((await store.initializeChat(chat.id)).status, 'failed')
  assert.deepEqual(store.currentChat, previous)
  assert.deepEqual(store.messages, history)
  assert.equal(store.inputText, '保留草稿')
  assert.equal(store.loadingMessages, false)
  assert.equal(store.restoringChat, false)
})

test('初始化尚未结束：页面保持忙碌，完成后释放', async () => {
  let resolveList // 控制模拟请求完成时间
  const { store } = fixture({ listChats: () => new Promise((resolve) => { resolveList = resolve }) })
  const pending = store.initializeChat(null) // 尚未完成的初始化任务
  assert.equal(store.pageBusy, true)
  assert.equal(store.loadingChats, true)
  assert.equal(await store.createNewChat(), null)
  resolveList([])
  await pending
  assert.equal(store.pageBusy, false)
})

function mockStorage(context, storage) { // 安装仅本测试使用的浏览器存储，测试结束恢复
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage') // 原来的全局属性
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage })
  context.after(() => {
    if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor)
    else delete globalThis.localStorage
  })
}

test('浏览器记录：读取、保存、清除使用同一个键', (context) => {
  const entries = new Map() // 模拟浏览器存储
  mockStorage(context, { getItem: (key) => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value), removeItem: (key) => entries.delete(key) })
  const storage = createTypeScriptLoader()('src/utils/chatStorage.ts') // 存储工具模块
  assert.equal(storage.getLastChatId(), null)
  storage.saveLastChatId(chat.id)
  assert.equal(entries.get('open-search:last-chat-id'), chat.id)
  assert.equal(storage.getLastChatId(), chat.id)
  storage.clearLastChatId()
  assert.equal(storage.getLastChatId(), null)
})

test('浏览器禁止存储：不阻止正常初始化', async (context) => {
  const denied = () => { throw new Error('模拟存储被禁止') } // 模拟浏览器拒绝存储访问
  mockStorage(context, { getItem: denied, setItem: denied, removeItem: denied })
  context.mock.method(console, 'warn', () => {})
  const storage = createTypeScriptLoader()('src/utils/chatStorage.ts') // 存储工具模块
  assert.equal(storage.getLastChatId(), null)
  assert.doesNotThrow(() => storage.saveLastChatId(chat.id))
  assert.doesNotThrow(() => storage.clearLastChatId())
  const { store } = fixture()
  assert.equal((await store.initializeChat(storage.getLastChatId())).status, 'empty')
})

test('页面协调：仅 missing 清记录，仅 restored 等待页面更新后滚动', async () => {
  const filename = fileURLToPath(new URL('../src/App.vue', import.meta.url)) // 当前页面源文件
  const script = parse(readFileSync(filename, 'utf8')).descriptor.scriptSetup.content // 页面脚本
  const tree = ts.createSourceFile('App.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS) // 定位真实启动回调
  const hook = tree.statements.find((node) => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression) && node.expression.expression.getText(tree) === 'onMounted') // 启动钩子语句
  assert.ok(hook)
  const code = ts.transpileModule(hook.getText(tree), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText // 仅运行协调代码，不挂载页面或发请求
  for (const status of ['empty', 'missing', 'failed', 'restored']) {
    const events = [] // 当前场景的执行记录
    let mounted // 页面登记的启动回调
    let pageUpdated = false // 模拟页面更新是否完成
    const result = { status, chat: status === 'restored' ? chat : null } // 模拟 store 初始化结果
    const wrapper = new Function('onMounted', 'initializeChat', 'getLastChatId', 'clearLastChatId', 'saveLastChatId', 'nextTick', 'scrollMessagesToBottom', `const accountId = 'test-user'; let autoFollowMessages = false; ${code}; return { get following() { return autoFollowMessages } }`)(
      (callback) => { mounted = callback }, async () => result, () => chat.id, () => events.push('clear'), () => events.push('save'), async () => { pageUpdated = true; events.push('tick') }, () => { assert.equal(pageUpdated, true); events.push('scroll') },
    )
    await mounted()
    assert.deepEqual(events, status === 'missing' ? ['clear'] : status === 'restored' ? ['save', 'tick', 'scroll'] : [])
    assert.equal(wrapper.following, status === 'restored')
  }
})

test('空白页第一次发送自动创建会话，保留正文与附件', async () => {
  const script = parse(readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content
  const tree = ts.createSourceFile('App.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const method = tree.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === 'sendChatMessage')
  const code = ts.transpileModule(method.getText(tree), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  const input = { value: '已经写好的问题' }, current = { value: null }, attachments = { value: [{ file_id: 'f1' }] }, sent = []
  const send = new Function('inputText', 'currentChat', 'selectedAttachments', 'createNewChat', 'sendMessage', 'saveLastChatId', `const pendingKnowledge = {value:[{id:'kb1',name:'资料库'}]}, interactionBusy = {value:false}, accountId = 'u1', pageActive = true; let autoFollowMessages = false; ${code}; return sendChatMessage`)(input, current, attachments, async (ids) => { assert.deepEqual(ids, ['kb1']); current.value = { id: 'new' }; input.value = ''; attachments.value = []; return current.value }, async () => sent.push([current.value.id, input.value, attachments.value]), () => {})
  await send()
  assert.deepEqual(sent, [['new', '已经写好的问题', [{ file_id: 'f1' }]]])
})

test('新建入口只清空当前草稿和分页，不创建或删除历史会话', async () => {
  const { store, calls } = fixture()
  await store.initializeChat(chat.id)
  calls.length = 0
  store.inputText = '旧草稿'
  store.selectedAttachments = [{ file_id: 'file' }]
  store.hasOlderMessages = true
  store.nextBeforeSequence = 8
  assert.equal(store.startBlankChat(), true)
  assert.equal(store.startBlankChat(), true)
  assert.equal(store.currentChat, null)
  assert.equal(store.inputText, '')
  assert.deepEqual(store.messages, [])
  assert.deepEqual(store.selectedAttachments, [])
  assert.deepEqual(store.chats, [chat])
  assert.equal(store.hasOlderMessages, false)
  assert.equal(store.nextBeforeSequence, null)
  assert.deepEqual(calls, [])
  store.creatingChat = true
  store.inputText = '正在发送'
  assert.equal(store.startBlankChat(), false)
  assert.equal(store.inputText, '正在发送')
})

test('点击新建清除上次会话记录并返回空白页，重复点击仍关闭手机导航', async () => {
  const script = parse(readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content
  const tree = ts.createSourceFile('App.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const method = tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'startNewChat')
  const code = ts.transpileModule(method.getText(tree), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  const events = [], pending = { value: [{ id: 'old-kb' }] }, revision = { value: 0 }, latest = { value: true }
  const start = new Function('startBlankChat', 'clearLastChatId', 'focusComposer', 'pendingKnowledge', 'newChatRevision', 'showLatest', `const sidebarBusy = { value: false }, accountId = 'u1'; let autoFollowMessages = false; ${code}; return startNewChat`)(() => { events.push('blank'); return true }, id => events.push(['clear', id]), () => events.push('focus'), pending, revision, latest)
  await start()
  await start()
  assert.deepEqual(events, ['blank', ['clear', 'u1'], 'focus', 'blank', ['clear', 'u1'], 'focus'])
  assert.deepEqual(pending.value, [])
  assert.equal(revision.value, 2)
  assert.equal(latest.value, false)
})

test('自动标题异步刷新只更新标题字段，手动改名后忽略迟到结果', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const tick = () => new Promise(resolve => setImmediate(resolve))
  for (const manual of [false, true]) {
    let loaded = false, resolvePoll
    const initial = { ...chat, title: '新会话', title_status: 'pending', web_search_enabled: true }
    const { store } = fixture({
      listChats: async () => [initial],
      getChat: async () => { if (!loaded) { loaded = true; return initial }; return await new Promise(resolve => { resolvePoll = resolve }) },
      listMessages: async () => ({ items: [{ ...history[0], role: 'assistant', status: 'completed' }], has_more: false, next_before_sequence: null }),
      renameChat: async () => ({ ...initial, title: '我手动命名', title_status: 'manual' }),
    })
    await store.initializeChat(chat.id)
    t.mock.timers.tick(1500)
    await tick()
    assert.equal(typeof resolvePoll, 'function')
    if (manual) await store.renameChatSession(chat.id, '我手动命名')
    resolvePoll({ ...initial, title: 'Python学习网站推荐', title_status: 'generated', web_search_enabled: false })
    await tick()
    assert.equal(store.currentChat.title, manual ? '我手动命名' : 'Python学习网站推荐')
    assert.equal(store.chats[0].title, store.currentChat.title)
    assert.equal(store.currentChat.web_search_enabled, true)
    assert.equal(store.errorMessage, '')
    store.$dispose()
  }
})
