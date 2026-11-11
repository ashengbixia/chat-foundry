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
  new Function('require', 'module', 'exports', code)((name) => mocks[name] ?? (name === '../AppIcon.vue' ? component('../AppIcon.vue', {}) : require(name)), module, module.exports)
  return module.exports.default
}
const ToolCallList = component('ToolCallList.vue', { '../../utils/toolOutput': utils })
const loader = createTypeScriptLoader()
const MessageAnswer = component('MessageAnswer.vue', { '../../utils/markdown': loader('src/utils/markdown.ts'), '../../utils/citations': loader('src/utils/citations.ts'), '../../utils/dialog': loader('src/utils/dialog.ts'), '../../api/files': loader('src/api/files.ts'), '../../api/errors': loader('src/api/errors.ts') })
const MessageList = component('MessageList.vue', { './ToolCallList.vue': ToolCallList, './MessageAnswer.vue': MessageAnswer })
const MessageInput = component('MessageInput.vue', {})

test('来源只从配对工具结果恢复，快照定位兼容中文及emoji', () => {
  const citations = loader('src/utils/citations.ts')
  const snapshot = { file_id: 'f1', filename: '资料.txt', citation_id: 1, offset: 2, text_content: '原文😀' }
  const output = [call('a', 'read_uploaded_file'), result('a', JSON.stringify(snapshot)), result('orphan', JSON.stringify({ ...snapshot, citation_id: 99 }))]
  const sources = citations.getCitationSources(output)
  assert.equal(sources.length, 1)
  assert.equal(sources[0].endOffset, 5)
  assert.deepEqual(citations.locateCitation('中😀原文😀后文', sources[0]), { before: '中😀', quote: '原文😀', after: '后文', matches: true })
  assert.equal(citations.locateCitation('已变化的正文', sources[0]).matches, false)
})

test('历史来源保留原回答信息，分段总结显示覆盖范围并以原文生成引用', async () => {
  const citations = loader('src/utils/citations.ts')
  const historical = { file_id: 'f1', filename: '历史资料.txt', citation_id: 1, start_offset: 3, content: '真正的原文', historical: true, origin_message_id: 'answer-before', origin_created_at: 100 }
  const sources = citations.getCitationSources([call('h', 'read_history_sources'), result('h', JSON.stringify({ chunks: [historical] }))])
  assert.equal(sources[0].historical, true)
  assert.equal(sources[0].originMessageId, 'answer-before')
  const summary = { chunks: [{ ...historical, historical: false, summary: '模型生成的归纳' }], total_files: 3, complete_files: 1, complete: false }
  assert.equal(citations.getCitationSources([call('s', 'summarize_uploaded_files'), result('s', JSON.stringify(summary))])[0].content, '真正的原文')
  const html = await renderToString(createSSRApp(ToolCallList, { output: [call('s', 'summarize_uploaded_files'), result('s', JSON.stringify(summary))] }))
  assert.match(html, /完整覆盖 1\/3 份/)
  assert.match(html, /其余未完整读取/)
})

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
  assert.match(html, /12\s*字节/)
  assert.match(html, /abcdefgh/)
})

test('输入区展示待发送附件和移除按钮，忙碌时禁用', async () => {
  const html = await renderToString(createSSRApp(MessageInput, { modelValue: '问题', inputDisabled: true, sendDisabled: true, sending: true, attachments: [{ file_id: 'file-1', filename: '测试.txt', size: 10 }] }))
  assert.match(html, /本次问题的附件/)
  assert.match(html, /测试\.txt/)
  assert.match(html, /移除附件 测试\.txt/)
  assert.match(html, /<button[^>]*disabled[^>]*aria-label="移除附件 测试\.txt"/)
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
  assert.match(html, /12\s*字节/)
  assert.match(html, /文件已删除/)
  assert.match(html, /原问题/)
})

const retrievalResult = { status: 'ok', query: '住宿上限', chunks: [{ file_id: 'file-1', filename: '<规定>.txt', chunk_id: 'chunk-1', chunk_index: 2, start_offset: 1700, end_offset: 1710, content: '住宿上限437元。<script>alert(1)</script>', distance: 0.18, truncated: false }], unavailable_files: [], notice: '候选片段不代表全文，距离不是正确率。', truncated: false }

test('检索来源卡片展示文件名、块序号、原文位置和正文，文本仍转义', async () => {
  const html = await renderToString(createSSRApp(ToolCallList, { output: [call('s1', 'search_uploaded_files'), result('s1', JSON.stringify(retrievalResult))], status: 'completed' }))
  for (const text of ['检索附件内容', '1 个片段', '&lt;规定&gt;.txt', '片段 3', '1700', '1710', '住宿上限437元', '查看原始调用数据']) assert.ok(html.includes(text), text)
  assert.ok(!html.includes('<script>'))
  assert.match(html, /&lt;script&gt;/)
})

test('索引不可用与没有命中分别提示，长度截断和部分文件失败可见', async () => {
  const unavailable = { ...retrievalResult, status: 'unavailable', chunks: [], unavailable_files: [{ file_id: 'file-1', filename: '规定.txt', message: '请先建立索引' }] }
  const html = await renderToString(createSSRApp(ToolCallList, { output: [call('s1', 'search_uploaded_files'), result('s1', JSON.stringify(unavailable))] }))
  assert.match(html, /索引不可用/)
  assert.match(html, /请先建立索引/)
  const empty = await renderToString(createSSRApp(ToolCallList, { output: [call('s2', 'search_uploaded_files'), result('s2', JSON.stringify({ ...unavailable, status: 'no_matches', unavailable_files: [] }))] }))
  assert.match(empty, /没有找到可用的候选片段/)
  const truncated = utils.getRetrievalPreview(JSON.stringify({ ...retrievalResult, truncated: true, chunks: [{ ...retrievalResult.chunks[0], truncated: true }] }))
  assert.equal(truncated.truncated, true)
  assert.equal(truncated.chunks[0].truncated, true)
})

test('异常检索结果回退原始展示，非法片段不会渲染为来源卡片', async () => {
  for (const value of [null, 'bad-json', '[]', '{"error":"失败"}', '{"status":"ok","chunks":{}}']) assert.equal(utils.getRetrievalPreview(value), null)
  const parsed = utils.getRetrievalPreview(JSON.stringify({ ...retrievalResult, chunks: [null, {}, { ...retrievalResult.chunks[0], start_offset: -1 }, { ...retrievalResult.chunks[0], content: {} }, retrievalResult.chunks[0]] }))
  assert.equal(parsed.chunks.length, 1)
  const html = await renderToString(createSSRApp(ToolCallList, { output: [call('s1', 'search_uploaded_files'), result('s1', '损坏的旧结果')] }))
  assert.match(html, /损坏的旧结果/)
  assert.match(html, /调用 ID/)
})
