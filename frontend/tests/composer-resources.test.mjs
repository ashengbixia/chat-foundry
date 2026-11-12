import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const require = createRequire(import.meta.url)
function resourceFixture(api) {
  const source = readFileSync(new URL('../src/components/chat/ComposerResourceMenu.vue', import.meta.url), 'utf8')
  const script = compileScript(parse(source).descriptor, { id: 'resource-test' })
  const code = ts.transpileModule(script.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const module = { exports: {} }, events = []
  const vue = require('vue')
  const props = vue.reactive({ attachments: [], conversationFiles: [], selectedKnowledge: [], disabled: false })
  new Function('require', 'module', 'exports', code)(name => name.includes('/api/') ? api : name.endsWith('.vue') ? {} : name === 'vue' ? { ...vue, onMounted() {}, onUnmounted() {} } : require(name), module, module.exports)
  const state = module.exports.default.setup(props, { expose() {}, emit: (...args) => events.push(args) })
  return { state, events, props }
}

test('输入区支持搜索文件、跳过已选文件并浏览知识库成员', async () => {
  const file = { id: 'f1', filename: '绿萝.txt', status: 'completed' }, base = { id: 'k1', name: '养护', file_count: 1 }
  const { state, events, props } = resourceFixture({ listFiles: async () => [file], listKnowledge: async () => [base], listKnowledgeFiles: async id => { assert.equal(id, 'k1'); return [file] } })
  await state.navigate('files')
  state.query.value = '绿萝'
  assert.deepEqual(state.visibleFiles.value, [file])
  state.choose(file)
  props.attachments = [{ file_id: 'f1' }]
  state.choose(file)
  assert.equal(events.filter(([name]) => name === 'attach').length, 1)
  await state.navigate('knowledge')
  state.chooseBase(base)
  assert.equal(events.at(-1)[0], 'toggleKnowledge')
  await state.navigate('members', base)
  assert.equal(state.title.value, '养护')
  assert.deepEqual(state.files.value, [file])
})

test('引用网页只添加规范化链接到草稿，不抓取网页且拒绝凭据及非网页协议', () => {
  const { state, events } = resourceFixture({})
  for (const url of ['javascript:alert(1)', 'file:///private', 'https://user:password@example.com', 'https://example.com:9000']) {
    state.webUrl.value = url
    state.addWebLink()
    assert.equal(events.length, 0)
    assert.ok(state.error.value)
  }
  state.webUrl.value = ' https://example.com/article '
  state.addWebLink()
  assert.deepEqual(events, [['webLink', 'https://example.com/article']])
})

test('联网搜索入口发出开关事件，不生成提示词或发送搜索关键词', () => {
  const { state, events, props } = resourceFixture({})
  state.toggleWebSearch()
  assert.deepEqual(events, [['toggleWebSearch']])
  props.disabled = true
  state.toggleWebSearch()
  assert.equal(events.length, 1)
})

test('切换菜单后旧文件请求的迟到结果不能覆盖知识库列表', async () => {
  let resolveFiles
  const { state } = resourceFixture({ listFiles: () => new Promise(resolve => { resolveFiles = resolve }), listKnowledge: async () => [{ id: 'k1', name: '当前库' }] })
  const pending = state.navigate('files')
  await require('vue').nextTick()
  await state.navigate('knowledge')
  resolveFiles([{ id: 'stale', filename: '迟到.txt' }])
  await pending
  assert.equal(state.page.value, 'knowledge')
  assert.deepEqual(state.files.value, [])
  assert.equal(state.bases.value[0].id, 'k1')
})

test('下载对话读取全部分页并保持消息时间顺序', async () => {
  const calls = [], blobs = [], clicked = []
  const load = createTypeScriptLoader({ '../api/chats': { listMessages: async (id, before, limit) => {
    calls.push([id, before, limit])
    return before === null ? { items: [{ role: 'assistant', content: '第二条', attachments: [] }], has_more: true, next_before_sequence: 2 } : { items: [{ role: 'user', content: '第一条', attachments: [{ filename: '资料.txt' }] }], has_more: false, next_before_sequence: null }
  } } })
  const previous = { document: globalThis.document, create: URL.createObjectURL, revoke: URL.revokeObjectURL, timeout: globalThis.setTimeout }
  globalThis.document = { body: { append() {} }, createElement: () => ({ click() { clicked.push(this.download) }, remove() {} }) }
  URL.createObjectURL = blob => { blobs.push(blob); return 'blob:test' }
  URL.revokeObjectURL = () => {}
  globalThis.setTimeout = callback => { callback(); return 0 }
  try {
    await load('src/utils/chatExport.ts').exportChatMarkdown({ id: 'chat', title: '养护/记录' })
    const body = await blobs[0].text()
    assert.ok(body.indexOf('第一条') < body.indexOf('第二条'))
    assert.match(body, /附件：资料.txt/)
    assert.deepEqual(clicked, ['养护_记录.md'])
    assert.deepEqual(calls, [['chat', null, 100], ['chat', 2, 100]])
  } finally { globalThis.document = previous.document; URL.createObjectURL = previous.create; URL.revokeObjectURL = previous.revoke; globalThis.setTimeout = previous.timeout }
})
