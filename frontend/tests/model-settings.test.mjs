import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

test('服务预设只填写地址，明确选择模型后保存并保留已保存Key', async () => {
  const mounted = [], unmounted = [], sent = [], events = []
  const config = { provider: 'server', base_url: 'https://default.test/v1', model: 'default', timeout: 60, key_source: 'environment', key_configured: true }
  const api = { getModelSettings: async () => ({ config, inherited: true, presets: [{ id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' }] }), discoverModels: async () => ({ models: ['deepseek-chat', 'deepseek-reasoner'] }), saveModelSettings: async (body) => { sent.push(body); const { api_key, ...fields } = body; return { config: { ...fields, key_configured: true }, inherited: false } }, testModelService: async (body) => { sent.push(body); return { message: '连接成功' } } }
  const source = readFileSync(new URL('../src/components/settings/SettingsPanel.vue', import.meta.url), 'utf8')
  const script = compileScript(parse(source).descriptor, { id: 'settings-test' })
  const code = ts.transpileModule(script.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const require = createRequire(import.meta.url), module = { exports: {} }
  new Function('require', 'module', 'exports', code)((name) => name === '../../api/modelSettings' ? api : name === '../../utils/dialog' ? createTypeScriptLoader()('src/utils/dialog.ts') : name === 'vue' ? { ...require('vue'), onMounted: (callback) => mounted.push(callback), onUnmounted: (callback) => unmounted.push(callback) } : require(name), module, module.exports)
  const state = module.exports.default.setup({}, { expose() {}, emit: (...args) => events.push(args) })
  await mounted[0]()
  state.draft.provider = 'deepseek'
  state.selectProvider()
  assert.equal(state.draft.model, '')
  await state.fetchModels()
  assert.deepEqual(state.models.value, ['deepseek-chat', 'deepseek-reasoner'])
  state.selectedModel.value = 'deepseek-reasoner'
  assert.equal(state.draft.model, 'deepseek-reasoner')
  state.draft.model = 'manual-model'
  assert.equal(state.selectedModel.value, '')
  state.selectedModel.value = 'deepseek-reasoner'
  state.apiKey.value = 'personal-test-key'
  let closed = 0
  state.dialog.value = { open: true, close() { this.open = false; closed++ } }
  await state.save()
  assert.equal(closed, 1)
  assert.ok(events.some(([name]) => name === 'saved'))
  assert.equal(sent[0].api_key, 'personal-test-key')
  assert.equal(state.apiKey.value, '')
  assert.equal(state.hasSavedKey.value, true)
  await state.test()
  assert.equal(sent[1].api_key, null)
  assert.ok(events.some(([name, model]) => name === 'modelChanged' && model === 'deepseek-reasoner'))
  assert.equal(state.isDirty.value, false)
  state.draft.timeout = 10
  assert.equal(state.isDirty.value, true)
  assert.equal(state.notice.value, '')
  state.presets.value.push({ id: 'ollama', name: 'Ollama', base_url: 'http://127.0.0.1:11434/v1', model: '' })
  state.draft.provider = 'ollama'
  state.selectProvider()
  assert.equal(state.draft.key_source, 'none')
  assert.equal(state.draft.timeout, 120)
  assert.equal(state.draft.model, '')
  unmounted[0]()
})

test('快捷列表仅包含服务返回模型，不混入旧配置，失败时不显示旧候选', async () => {
  const events = [], selected = []
  let failing = false
  const config = { provider: 'deepseek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', timeout: 60, key_source: 'personal', key_configured: true }
  const api = { getModelSettings: async () => ({ config }), discoverModels: async () => { if (failing) throw new Error('认证失败'); return { models: ['deepseek-flash', 'deepseek-v4-pro', 'deepseek-flash'] } }, selectModel: async (model) => { selected.push(model); return { config: { ...config, model } } } }
  const source = readFileSync(new URL('../src/components/chat/ComposerModelPicker.vue', import.meta.url), 'utf8')
  const script = compileScript(parse(source).descriptor, { id: 'picker-test' })
  const code = ts.transpileModule(script.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const require = createRequire(import.meta.url), module = { exports: {} }
  new Function('require', 'module', 'exports', code)((name) => name === '../../api/modelSettings' ? api : name.endsWith('.vue') ? {} : name === 'vue' ? { ...require('vue'), onMounted() {}, onUnmounted() {} } : require(name), module, module.exports)
  const state = module.exports.default.setup({ currentModel: 'deepseek-chat', disabled: false }, { expose() {}, emit: (...args) => events.push(args) })
  await state.load()
  assert.deepEqual(state.models.value, ['deepseek-flash', 'deepseek-v4-pro'])
  assert.equal(state.unavailableSelection.value, true)
  state.search.value = 'v4'
  assert.deepEqual(state.filteredModels.value, ['deepseek-v4-pro'])
  await state.choose('deepseek-chat')
  assert.deepEqual(selected, [])
  await state.choose('deepseek-v4-pro')
  assert.deepEqual(selected, ['deepseek-v4-pro'])
  assert.ok(events.some(([name, model]) => name === 'changed' && model === 'deepseek-v4-pro'))
  failing = true
  await state.load()
  assert.deepEqual(state.models.value, [])
  assert.equal(state.catalogLoaded.value, false)
  assert.equal(state.error.value, '认证失败')
})
