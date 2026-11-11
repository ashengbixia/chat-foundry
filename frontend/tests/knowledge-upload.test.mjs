import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const source = readFileSync(new URL('../src/components/knowledge/KnowledgePanel.vue', import.meta.url), 'utf8')
const script = compileScript(parse(source).descriptor, { id: 'knowledge-upload' })
const code = ts.transpileModule(script.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const nativeRequire = createRequire(import.meta.url)
function fixture() {
  const uploaded = [], joined = [], members = []
  const fileApi = { validateTextFile: createTypeScriptLoader()('src/api/files.ts').validateTextFile, uploadFile: async file => { uploaded.push(file.name); return { id: file.name, filename: file.name } }, listFiles: async () => [] }
  const api = { listKnowledge: async () => [{ id: 'kb', name: '测试库' }], listKnowledgeFiles: async () => members.slice(), addKnowledgeFiles: async (id, ids) => { joined.push([id, ids]); members.push(...ids.map(id => ({ id, filename: id }))) } }
  const module = { exports: {} }
  new Function('require', 'module', 'exports', code)(name => name === '../../api/knowledge' ? api : name === '../../api/files' ? fileApi : name === '../../utils/dialog' ? createTypeScriptLoader()('src/utils/dialog.ts') : name.endsWith('.vue') ? {} : name === 'vue' ? { ...nativeRequire('vue'), onUnmounted() {} } : nativeRequire(name), module, module.exports)
  const state = module.exports.default.setup({ selected: [], selectionDisabled: false, applySelection: async () => {} }, { expose() {}, emit() {} })
  state.activeId.value = 'kb'
  return { state, uploaded, joined }
}
function dropEvent(files = [], savedId = '') {
  return { prevented: false, preventDefault() { this.prevented = true }, dataTransfer: { types: savedId ? ['application/x-chatfoundry-file'] : ['Files'], files, getData: () => savedId } }
}

test('取消系统文件选择不关闭知识库，弹窗自身Esc仍可关闭', () => {
  const { state } = fixture()
  let closed = 0
  const dialog = { open: true, close() { closed++; this.open = false } }
  state.dialog.value = dialog
  state.name.value = '保留当前知识库'
  state.members.value = [{ id: 'existing' }]
  state.cancelDialog({ target: { type: 'file' }, preventDefault() { assert.fail('不能拦截文件选择器的取消事件') } })
  assert.equal(closed, 0)
  assert.equal(state.activeId.value, 'kb')
  assert.equal(state.members.value.length, 1)
  assert.equal(state.name.value, '保留当前知识库')
  let prevented = false
  state.cancelDialog({ target: state.dialog.value, preventDefault() { prevented = true } })
  assert.equal(prevented, true)
  assert.equal(closed, 1)
})

test('知识库拖放多份TXT沿用上传校验，非法文件与未选库不发请求', async () => {
  const { state, uploaded, joined } = fixture()
  await state.dropFiles(dropEvent([new File(['内容'], '不支持.zip')]))
  assert.equal(uploaded.length, 0)
  assert.match(state.error.value, /TXT/)
  const event = dropEvent([new File(['甲'], '甲.txt'), new File(['乙'], '乙.txt')])
  await state.dropFiles(event)
  assert.equal(event.prevented, true)
  assert.deepEqual(uploaded, ['甲.txt', '乙.txt'])
  assert.deepEqual(joined, [['kb', ['甲.txt']], ['kb', ['乙.txt']]])
  state.activeId.value = ''
  await state.dropFiles(dropEvent([new File(['丙'], '丙.txt')]))
  assert.equal(uploaded.length, 2)
  assert.match(state.error.value, /先创建或选择/)
})

test('拖放已有资料只写关联，重复文件、忙碌状态与普通文字拖放不产生上传', async () => {
  const { state, uploaded, joined } = fixture()
  state.allFiles.value = [{ id: 'saved', filename: '已有.txt' }]
  await state.dropFiles(dropEvent([], 'saved'))
  await state.dropFiles(dropEvent([], 'saved'))
  assert.deepEqual(joined, [['kb', ['saved']]])
  assert.deepEqual(uploaded, [])
  state.busy.value = true
  await state.dropFiles(dropEvent([new File(['甲'], '甲.txt')]))
  assert.match(state.error.value, /正在处理/)
  const text = dropEvent()
  text.dataTransfer.types = ['text/plain']
  await state.dropFiles(text)
  assert.equal(text.prevented, false)
})
