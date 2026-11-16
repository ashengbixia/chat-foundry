import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const load = createTypeScriptLoader()
const { parseSseEvent, streamChatReply } = load('src/api/stream.ts')
const call = { type: 'tool_call', callId: 'c1', name: 'get_current_time', arguments: '{}' } // 模拟已经收齐参数的调用
const result = { type: 'tool_result', callId: 'c1', output: '{"timezone":"Asia/Shanghai"}' }
const sse = (name, data) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
const tick = () => new Promise((resolve) => setImmediate(resolve)) // 等待异步回调处理

test('工具 SSE 解析并校验必要字段', () => {
  assert.deepEqual(parseSseEvent(sse('tool_call', { call_id: 'c1', name: 'get_current_time', arguments: '{}' })), call)
  assert.deepEqual(parseSseEvent(sse('tool_result', { call_id: 'c1', output: result.output })), result)
  for (const [name, data] of [['tool_call', { call_id: 'c1', name: 'get_current_time', arguments: {} }], ['tool_result', { call_id: '', output: '{}' }]]) {
    assert.throws(() => parseSseEvent(sse(name, data)), /事件字段不符合约定/)
  }
})

test('新注册工具的中文名称可由后端传递，历史记录无需前端名称表', () => {
  const event = parseSseEvent(sse('tool_call', { call_id: 'custom', name: 'new_tool', arguments: '{}', display_name: '新工具名称' }))
  assert.equal(event.displayName, '新工具名称')
  const calls = load('src/utils/toolOutput.ts').getToolCalls([{ type: 'function_call', call_id: event.callId, name: event.name, arguments: event.arguments, display_name: event.displayName }])
  assert.equal(calls[0].displayName, '新工具名称')
})
test('分段 SSE 保持调用、结果和正文的顺序，仍返回数据库消息 ID', async (context) => {
  const content = sse('tool_call', { call_id: 'c1', name: 'get_current_time', arguments: '{}' }) + sse('tool_result', { call_id: 'c1', output: result.output }) + sse('text_delta', { text: '北京时间' }) + sse('done', { message_id: 'saved' })
  const bytes = new TextEncoder().encode(content)
  context.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({ start(controller) { for (let index = 0; index < bytes.length; index += 7) controller.enqueue(bytes.slice(index, index + 7)); controller.close() } })))
  const events = []
  assert.equal(await streamChatReply('chat', (text) => events.push(text), undefined, (event) => events.push(event)), 'saved')
  assert.deepEqual(events, [call, result, '北京时间'])
})

function fixture(api = {}) { // 替换模型流，测试真正的 store 回调及消息状态
  let callbacks
  const streamChatReply = (_id, onText, signal, onTool, onProcess, onDone, onContext) => new Promise((resolve, reject) => {
    callbacks = { onText, onTool, onProcess, onDone, onContext, resolve, reject }
    signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')), { once: true })
  })
  const loader = createTypeScriptLoader({ '../api/chats': api, '../api/stream': { streamChatReply } })
  setActivePinia(createPinia())
  const store = loader('src/stores/chat.ts').useChatStore()
  store.restoringChat = false
  store.currentChat = { id: 'chat', title: '工具测试', created_at: 1, updated_at: 1 }
  store.messages = [{ id: 'user', role: 'user', content: '现在几点', status: 'completed', error: null, output: [] }]
  return { store, get callbacks() { return callbacks } }
}
test('仓库实时接收工具记录，完成后无需重新读取会话', async () => {
  const state = fixture()
  const pending = state.store.retryReply()
  await tick()
  state.callbacks.onTool(call)
  assert.equal(state.store.messages.at(-1).output[0].name, 'get_current_time')
  assert.equal(state.store.messages.at(-1).status, 'generating')
  state.callbacks.onTool(result)
  state.callbacks.onText('北京时间')
  state.callbacks.resolve('saved')
  await pending
  const draft = state.store.messages.at(-1)
  assert.equal(draft.id, 'saved')
  assert.equal(draft.status, 'completed')
  assert.equal(draft.content, '北京时间')
  assert.deepEqual(draft.output.map((item) => item.type), ['function_call', 'function_call_output'])
})

test('压缩检查点更新原消息，来源无需新工具调用即可点击', async () => {
  const state = fixture(), pending = state.store.retryReply()
  await tick()
  const summary = { text: '已有目标与未完成事项', through_sequence: 20, anchor_sequence: 21, created_at: 1, model: 'sample' }
  state.callbacks.onContext(parseSseEvent(sse('context_checkpoint', { message_id: 'user', summary })))
  assert.deepEqual(state.store.messages[0].context_summary, summary)
  const chunks = [{ file_id: 'f', filename: '资料.txt', content: '历史原文', start_offset: 0, citation_id: 1, historical: true }]
  state.callbacks.onContext(parseSseEvent(sse('context_sources', { chunks })))
  const sources = load('src/utils/citations.ts').getCitationSources(state.store.messages.at(-1).output)
  assert.equal(sources[0].content, '历史原文')
  assert.equal(sources[0].historical, true)
  state.callbacks.resolve('saved')
  await pending
})

test('提交追问答案携带结构化关联，保留原输入框草稿和附件', async () => {
  const sent = []
  const state = fixture({ saveUserMessage: async (...args) => { sent.push(args); return { id: 'user-answer', role: 'user', content: args[1], status: 'completed', output: [], attachments: [] } } })
  state.store.inputText = '未发送的草稿'
  state.store.selectedAttachments = [{ file_id: 'draft-file', filename: '草稿资料.txt', size: 1 }]
  state.store.messages.push({ id: 'question', role: 'assistant', content: '', status: 'completed', output: [{ type: 'function_call', call_id: 'ask', name: 'ask_user', arguments: '{}' }, { type: 'function_call_output', call_id: 'ask', output: '{"status":"awaiting_user","questions":[]}' }] })
  const clarification = { message_id: 'question', call_id: 'ask', answers: { scope: '完整' }, cancelled: false }
  const pending = state.store.sendMessage({ text: '我选择完整范围', clarification })
  await tick()
  assert.deepEqual(sent[0].slice(2), [[], clarification])
  assert.equal(state.store.inputText, '未发送的草稿')
  assert.equal(state.store.selectedAttachments[0].file_id, 'draft-file')
  assert.equal(JSON.parse(state.store.messages[1].output[1].output).status, 'answered')
  state.callbacks.resolve('continued')
  await pending
})
test('停止和报错保留收到的工具记录；停止后忽略迟到事件', async () => {
  for (const stopped of [true, false]) {
    const state = fixture()
    const pending = state.store.retryReply()
    await tick()
    state.callbacks.onTool(call)
    if (stopped) {
      state.store.stopReply()
      state.callbacks.onTool(result)
      state.callbacks.onText('不应追加')
    } else state.callbacks.reject(new Error('模型断线'))
    await pending
    const draft = state.store.messages.at(-1)
    assert.equal(draft.status, stopped ? 'cancelled' : 'failed')
    assert.equal(draft.output.length, 1)
    assert.equal(draft.content, '')
    assert.equal(state.store.generatingReply, false)
  }
})

test('思考和工具前过程不混入最终正文，耗时随完成事件保存', async () => {
  const state = fixture(), pending = state.store.retryReply()
  await tick()
  state.callbacks.onProcess({ type: 'reasoning_delta', text: '核对依据' })
  state.callbacks.onText('我先查询')
  state.callbacks.onProcess({ type: 'commentary', text: '我先查询' })
  assert.equal(state.store.messages.at(-1).content, '')
  state.callbacks.onText('最终结论')
  state.callbacks.onDone(63000)
  state.callbacks.resolve('saved')
  await pending
  const message = state.store.messages.at(-1)
  assert.equal(message.content, '最终结论')
  assert.equal(message.output[0].type, 'reasoning')
  assert.equal(message.output[1].phase, 'commentary')
  assert.equal(message.output.at(-1).duration_ms, 63000)
  assert.deepEqual(parseSseEvent(sse('reasoning_delta', { text: '思考' })), { type: 'reasoning_delta', text: '思考' })
})
