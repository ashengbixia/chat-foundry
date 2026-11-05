import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const utils = createTypeScriptLoader()('src/utils/toolOutput.ts') // 测试实际的记录解析方法
const call = (id, name = 'get_current_time') => ({ type: 'function_call', call_id: id, name, arguments: '{}' })
const result = (id, output = '{"timezone":"Asia/Shanghai"}') => ({ type: 'function_call_output', call_id: id, output })
function component(filename, mocks) { // 在内存编译真实 Vue 组件，用服务端渲染检查最终 HTML
  const source = readFileSync(new URL(`../src/components/chat/${filename}`, import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id: filename, inlineTemplate: true })
  const code = ts.transpileModule(script.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText
  const module = { exports: {} }
  const require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', code)((name) => mocks[name] ?? require(name), module, module.exports)
  return module.exports.default
}
const ToolCallList = component('ToolCallList.vue', { '../../utils/toolOutput': utils })
const MessageList = component('MessageList.vue', { './ToolCallList.vue': ToolCallList })
const MessageInput = component('MessageInput.vue', {})

test('按 ID 配对工具，保留调用顺序并忽略孤立结果', () => {
  assert.deepEqual(utils.getToolCalls([call('a'), call('b'), result('b', '第二个结果'), result('a'), result('orphan')]), [
    { callId: 'a', name: 'get_current_time', arguments: '{}', result: '{"timezone":"Asia/Shanghai"}', failed: false },
    { callId: 'b', name: 'get_current_time', arguments: '{}', result: '第二个结果', failed: false },
  ])
})
test('缺少结果、错误结果和空结果应区分展示', () => {
  const calls = utils.getToolCalls([call('a'), call('b'), result('b', '{"error":"工具失败"}'), call('c'), result('c', '')])
  assert.equal(calls[0].result, null)
  assert.equal(calls[1].failed, true)
  assert.equal(calls[2].result, '')
})
test('旧消息和损坏记录不导致页面报错', () => {
  for (const output of [undefined, null, {}, 'bad', [null, 1, {}, { ...call('a'), arguments: 1 }, { type: 'message' }]]) assert.deepEqual(utils.getToolCalls(output), [])
  assert.equal(utils.formatToolValue('不是JSON'), '不是JSON')
  assert.equal(utils.formatToolValue('{"中文":1}'), '{\n  "中文": 1\n}')
})
test('工具记录实际渲染成可折叠区域，JSON 结果可读且文本被转义', async () => {
  const html = await renderToString(createSSRApp(ToolCallList, { output: [call('a'), result('a', '{"内容":"<script>alert(1)</script>"}')] }))
  assert.match(html, /<details/)
  assert.match(html, /<summary>/)
  assert.match(html, /查询当前时间/)
  assert.match(html, /get_current_time/)
  assert.match(html, /已返回结果/)
  assert.match(html, /&lt;script&gt;/)
  assert.ok(!html.includes('<script>'))
})
test('无结果和返回错误的历史记录不误报成功', async () => {
  const html = await renderToString(createSSRApp(ToolCallList, { output: [call('a'), call('b'), result('b', '{"error":"工具失败"}')] }))
  assert.match(html, /未记录结果/)
  assert.match(html, /返回错误/)
  assert.ok(!html.includes('已返回结果'))
})
test('消息列表保留助手正文，只有助手的工具记录会展示', async () => {
  const output = [call('a'), result('a')]
  const messages = [{ id: 'user', role: 'user', content: '用户正文', status: 'completed', error: null, output }, { id: 'assistant', role: 'assistant', content: '助手正文', status: 'completed', error: null, output }]
  const html = await renderToString(createSSRApp(MessageList, { messages }))
  assert.equal((html.match(/<details/g) ?? []).length, 1)
  assert.match(html, /用户正文/)
  assert.match(html, /助手正文/)
})

test('实时调用随消息结束状态切换，不会在取消或报错后继续转圈', async () => {
  for (const [status, label] of [['generating', '正在调用'], ['cancelled', '已停止，未收到结果'], ['failed', '调用中断，未收到结果'], ['completed', '未记录结果']]) {
    const html = await renderToString(createSSRApp(ToolCallList, { output: [call('a')], status }))
    assert.ok(html.includes(label))
    if (status !== 'generating') assert.ok(!html.includes('正在调用'))
  }
  const html = await renderToString(createSSRApp(ToolCallList, { output: [call('a'), result('a')], status: 'generating' }))
  assert.ok(html.includes('已返回结果'))
  assert.ok(!html.includes('正在调用'))
})

test('历史消息展示附件名称和大小，同名附件用 ID 区分', async () => {
  const messages = [{ id: 'user', role: 'user', content: '问题', status: 'completed', error: null, output: [], attachments: [{ file_id: 'abcdefgh-1', filename: '<资料>.txt', size: 12 }] }]
  const html = await renderToString(createSSRApp(MessageList, { messages }))
  assert.match(html, /消息附件/)
  assert.match(html, /&lt;资料&gt;\.txt/)
  assert.match(html, /12字节/)
  assert.match(html, /abcdefgh/)
})

test('输入区展示待发送附件和移除按钮，忙碌时禁用', async () => {
  const html = await renderToString(createSSRApp(MessageInput, { modelValue: '问题', inputDisabled: true, sendDisabled: true, sending: true, attachments: [{ file_id: 'file-1', filename: '测试.txt', size: 10 }] }))
  assert.match(html, /本次问题的附件/)
  assert.match(html, /测试\.txt/)
  assert.match(html, /移除附件 测试\.txt/)
  assert.match(html, /<button[^>]*disabled[^>]*>移除<\/button>/)
})

test('附件读取工具展示中文名称和实际正文片段', async () => {
  const html = await renderToString(createSSRApp(ToolCallList, { output: [call('read-1', 'read_uploaded_file'), result('read-1', '{"text_content":"附件中的正文","has_more":false}')], status: 'completed' }))
  assert.match(html, /读取附件正文/)
  assert.match(html, /附件中的正文/)
  assert.match(html, /已返回结果/)
})

test('已删除文件保留历史附件名称和大小，并显示删除标记', async () => {
  const messages = [{ id: 'user', role: 'user', content: '原问题', status: 'completed', error: null, output: [], attachments: [{ file_id: 'old-file', filename: '旧资料.txt', size: 12, deleted: true }] }]
  const html = await renderToString(createSSRApp(MessageList, { messages }))
  assert.match(html, /旧资料\.txt/)
  assert.match(html, /12字节/)
  assert.match(html, /文件已删除/)
  assert.match(html, /原问题/)
})
