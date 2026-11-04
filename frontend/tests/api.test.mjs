import assert from 'node:assert/strict'
import test from 'node:test'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const loadTypeScript = createTypeScriptLoader() // 共用内存加载器，不访问真实服务

const { createApiError } = loadTypeScript('src/api/errors.ts') // 共用错误解析
const chats = loadTypeScript('src/api/chats.ts') // 普通请求方法
const { streamChatReply } = loadTypeScript('src/api/stream.ts') // 流式请求方法

test('错误正文：显示 FastAPI detail 字符串', async () => {
  const response = Response.json({ detail: '回答仍在清理中，请稍后重试' }, { status: 409 })
  assert.equal((await createApiError(response, '生成失败')).message, '回答仍在清理中，请稍后重试')
})

test('参数校验错误：展示 msg，不展示原始输入', async () => {
  const response = Response.json({ detail: [{ msg: '标题不能为空', input: 'private-test-value' }, { msg: '长度不能超过限制' }] }, { status: 422 })
  const error = await createApiError(response, '修改失败')
  assert.equal(error.message, '标题不能为空；长度不能超过限制')
  assert.ok(!error.message.includes('private-test-value'))
})

test('非 JSON、空正文和未知格式：使用操作名称与状态码', async () => {
  const responses = [new Response('<html>gateway error</html>', { status: 502 }), new Response(null, { status: 500 }), Response.json({ detail: '' }, { status: 500 }), Response.json(null, { status: 500 })]
  for (const response of responses) assert.equal((await createApiError(response, '读取失败')).message, `读取失败，状态码：${response.status}`)
})

test('读取错误正文被取消：保留原来的 AbortError', async () => {
  const aborted = new DOMException('Aborted', 'AbortError')
  await assert.rejects(createApiError({ status: 409, json: async () => { throw aborted } }, '生成失败'), (error) => error === aborted)
})

test('所有普通请求均展示后端错误说明', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => Response.json({ detail: '模拟后端错误' }, { status: 400 }))
  const calls = [() => chats.createChat(), () => chats.saveUserMessage('test', '你好'), () => chats.listChats(), () => chats.listMessages('test'), () => chats.getChat('test'), () => chats.deleteChat('test'), () => chats.renameChat('test', '新标题')]
  for (const call of calls) await assert.rejects(call(), { message: '模拟后端错误' })
})

test('getChat 的 404 仍返回 null，不尝试读取正文', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => ({ status: 404, ok: false, json: async () => { throw new Error('不应该解析正文') } }))
  assert.equal(await chats.getChat('missing'), null)
})

test('deleteChat 的 204 正常结束，不尝试读取正文', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => ({ status: 204, ok: true, json: async () => { throw new Error('不应该解析正文') } }))
  assert.equal(await chats.deleteChat('test'), undefined)
})

test('普通成功响应仍返回原有数据', async (context) => {
  const chat = { id: 'test', title: '测试会话', created_at: 1, updated_at: 1 }
  context.mock.method(globalThis, 'fetch', async () => Response.json(chat))
  assert.deepEqual(await chats.getChat('test'), chat)
})

test('会话列表一次请求全部基本信息，不携带分页参数', async (context) => {
  const history = Array.from({ length: 125 }, (_, index) => ({ id: `chat-${index}`, title: `会话${index}`, created_at: 1, updated_at: 1 })) // 超过原先 20 条和 100 条限制
  const urls = [] // 记录实际请求，防止仍然分页或重复查询
  context.mock.method(globalThis, 'fetch', async (url) => { urls.push(url); return Response.json(history) })
  assert.deepEqual(await chats.listChats(), history)
  assert.deepEqual(urls, ['/api/chats'])
})

test('流式接口的 HTTP 错误显示 detail，而非仅显示状态码', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => Response.json({ detail: '该会话正在生成或清理中，请稍后重试' }, { status: 409 }))
  await assert.rejects(streamChatReply('test', () => {}), { message: '该会话正在生成或清理中，请稍后重试' })
})

test('流式接口的非 JSON 错误正常回退', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => new Response('bad gateway', { status: 502 }))
  await assert.rejects(streamChatReply('test', () => {}), { message: '生成回答失败，状态码：502' })
})

test('流式正文和完成事件仍正常解析', async (context) => {
  const content = 'event: text_delta\ndata: {"text":"你好"}\n\nevent: done\ndata: {"message_id":"saved-test"}\n\n'
  context.mock.method(globalThis, 'fetch', async () => new Response(content, { headers: { 'Content-Type': 'text/event-stream' } }))
  const parts = []
  assert.equal(await streamChatReply('test', (text) => parts.push(text)), 'saved-test')
  assert.deepEqual(parts, ['你好'])
})

test('流中的 error 事件仍抛出错误', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => new Response('event: error\ndata: {"message":"模拟生成失败"}\n\n'))
  await assert.rejects(streamChatReply('test', () => {}), { message: '模拟生成失败' })
})

test('网络错误原样传播，不伪装为正文解析失败', async (context) => {
  const networkError = new TypeError('模拟网络不可用')
  context.mock.method(globalThis, 'fetch', async () => { throw networkError })
  await assert.rejects(chats.listChats(), (error) => error === networkError)
  await assert.rejects(streamChatReply('test', () => {}), (error) => error === networkError)
})

test('fetch 绑定取消信号，并保留 AbortError', async (context) => {
  const controller = new AbortController()
  const aborted = new DOMException('Aborted', 'AbortError')
  context.mock.method(globalThis, 'fetch', async (_url, options) => {
    assert.equal(options.signal, controller.signal)
    if (options.signal.aborted) throw aborted
    throw new Error('测试要求先取消')
  })
  controller.abort()
  await assert.rejects(streamChatReply('test', () => {}, controller.signal), (error) => error === aborted)
})
