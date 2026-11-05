import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import ts from 'typescript'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const api = createTypeScriptLoader()('src/api/files.ts') // 加载真实的文件请求代码
const record = { id: 'file-id', filename: '资料.txt', content_type: 'text/plain', size: 10, status: 'completed', error: null, created_at: 1, updated_at: 1 }
const selected = () => new File(['测试资料'], '资料.txt', { type: 'text/plain' })
const source = readFileSync(new URL('../src/components/files/FilePanel.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'file-panel-test' })
const template = compileTemplate({ source: descriptor.template.content, filename: 'FilePanel.vue', id: 'file-panel-test', compilerOptions: { bindingMetadata: script.bindings } })
const nativeRequire = createRequire(import.meta.url)
function evaluate(code, mocks = {}) { // 在内存运行真实组件，不写编译产物
  const compiled = ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText
  const module = { exports: {} }
  new Function('require', 'module', 'exports', compiled)((name) => mocks[name] ?? nativeRequire(name), module, module.exports)
  return module.exports
}
function fixture(overrides = {}, props = {}) { // 使用真实 Vue ref，替换接口并捕获生命周期和组件事件
  const calls = []
  const unmount = []
  const mount = []
  const emitted = []
  const actions = { validateTextFile: api.validateTextFile, deleteFile: async () => {}, listFiles: async () => [record], uploadFile: async () => record, getFile: async () => record, getFileContent: async () => ({ id: record.id, text_content: '提取的正文' }), ...overrides }
  const tracked = Object.fromEntries(Object.entries(actions).map(([name, action]) => [name, (...args) => { calls.push([name, ...args]); return action(...args) }]))
  const component = evaluate(script.content, { '../../api/files': tracked, vue: { ...nativeRequire('vue'), onMounted: (callback) => mount.push(callback), onUnmounted: (callback) => unmount.push(callback) } }).default
  const state = component.setup(props, { expose() {}, emit: (...args) => emitted.push(args) })
  return { state, calls, mount, unmount, component, emitted }
}
function choose(state, file = selected()) { // 模拟浏览器文件选择事件
  const input = { files: file ? [file] : [], value: file?.name ?? '' }
  state.selectFile({ target: input })
  return input
}
function confirmDeletion(context, confirmed = true) { // 安装模拟确认框，测试结束后恢复环境
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { confirm: () => confirmed } })
  context.after(() => { if (descriptor) Object.defineProperty(globalThis, 'window', descriptor); else delete globalThis.window })
}

test('删除请求编码 ID，204不解析空正文，409显示处理中说明', async (context) => {
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/files/a%2Fb')
    assert.equal(options.method, 'DELETE')
    return { ok: true, status: 204, json: () => { throw new Error('不能解析空正文') } }
  })
  await api.deleteFile('a/b')
  context.mock.method(globalThis, 'fetch', async () => Response.json({ detail: '文件正在处理中' }, { status: 409 }))
  await assert.rejects(api.deleteFile('id'), /正在处理中/)
})
test('删除其他文件保留当前预览；删除当前文件选中下一份；全部删除清空预览', async (context) => {
  confirmDeletion(context)
  const other = { ...record, id: 'other' }
  const { state, emitted } = fixture()
  state.files.value = [record, other]
  state.displayFile(record)
  await state.togglePreview()
  await state.removeFile(other)
  assert.equal(state.uploadedFile.value.id, record.id)
  assert.equal(state.previewOpen.value, true)
  state.files.value.push(other)
  await state.removeFile(record)
  assert.equal(state.uploadedFile.value.id, other.id)
  assert.equal(state.previewOpen.value, false)
  assert.equal(state.textContent.value, '')
  await state.removeFile(other)
  assert.equal(state.uploadedFile.value, null)
  assert.deepEqual(state.files.value, [])
  assert.equal(emitted.filter(([name]) => name === 'removed').length, 3)
})
test('取消确认不删除，删除失败保留文件和预览', async (context) => {
  confirmDeletion(context, false)
  const { state, calls } = fixture({ deleteFile: async () => { throw new Error('删除文件失败') } })
  state.files.value = [record]
  state.displayFile(record)
  await state.togglePreview()
  await state.removeFile(record)
  assert.equal(calls.filter(([name]) => name === 'deleteFile').length, 0)
  globalThis.window.confirm = () => true
  await state.removeFile(record)
  assert.equal(state.errorMessage.value, '删除文件失败')
  assert.equal(state.files.value.length, 1)
  assert.equal(state.previewOpen.value, true)
  assert.equal(state.deletingFileId.value, null)
})
test('删除期间拦截重复操作，聊天忙碌时不能发起删除', async (context) => {
  confirmDeletion(context)
  let finish
  const props = { deleteDisabled: true }
  const { state, calls } = fixture({ deleteFile: () => new Promise((resolve) => { finish = resolve }) }, props)
  state.files.value = [record]
  state.displayFile(record)
  await state.removeFile(record)
  assert.equal(calls.length, 0)
  props.deleteDisabled = false
  const pending = state.removeFile(record)
  assert.equal(state.busy.value, true)
  await state.removeFile(record)
  await state.loadFiles()
  state.selectSavedFile('missing')
  assert.equal(state.uploadedFile.value.id, record.id)
  assert.equal(calls.filter(([name]) => name === 'deleteFile').length, 1)
  finish()
  await pending
  assert.equal(state.busy.value, false)
})
test('历史文件每行显示一个删除叉号', async () => {
  const { state, component } = fixture()
  state.files.value = [record, { ...record, id: 'other', filename: '另一份.txt' }]
  const html = await renderToString(createSSRApp({ ...component, setup: () => state, render: evaluate(template.code).render }))
  assert.equal((html.match(/class="delete-file"/g) ?? []).length, 2)
  assert.match(html, /删除文件 资料\.txt/)
  assert.match(html, /删除文件 另一份\.txt/)
})

test('文件校验：大小边界、空文件、扩展名和文件名长度', () => {
  assert.equal(api.validateTextFile({ name: '资料.TXT', size: api.MAX_UPLOAD_SIZE }), '')
  for (const file of [{ name: 'a.txt', size: 0 }, { name: 'a.txt', size: api.MAX_UPLOAD_SIZE + 1 }, { name: 'a.pdf', size: 1 }, { name: 'a'.repeat(252) + '.txt', size: 1 }]) assert.ok(api.validateTextFile(file))
})
test('上传使用 FormData，保留取消信号，不手动设置 Content-Type', async (context) => {
  const controller = new AbortController()
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/files')
    assert.equal(options.method, 'POST')
    assert.equal(options.signal, controller.signal)
    assert.equal(options.headers, undefined)
    assert.equal(options.body.get('file').name, '资料.txt')
    return Response.json(record, { status: 201 })
  })
  assert.deepEqual(await api.uploadFile(selected(), controller.signal), record)
})

test('文件列表请求只读取基本信息，错误正文正常传播', async (context) => {
  const controller = new AbortController()
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/files')
    assert.equal(options.signal, controller.signal)
    return Response.json([record])
  })
  assert.deepEqual(await api.listFiles(controller.signal), [record])
  context.mock.method(globalThis, 'fetch', async () => Response.json({ detail: '列表不可用' }, { status: 500 }))
  await assert.rejects(api.listFiles(), /列表不可用/)
})

test('文件面板通过事件添加处理成功的文件，忙碌和禁用时拦截', () => {
  const props = { attachDisabled: false }
  const { state, emitted } = fixture({}, props)
  state.attachFile()
  assert.deepEqual(emitted, [])
  state.uploadedFile.value = { ...record, status: 'processing' }
  state.attachFile()
  state.uploadedFile.value = record
  props.attachDisabled = true
  state.attachFile()
  props.attachDisabled = false
  state.loadingFiles.value = true
  state.attachFile()
  assert.deepEqual(emitted, [])
  state.loadingFiles.value = false
  state.attachFile()
  assert.equal(emitted[0][0], 'attach')
  assert.equal(emitted[0][1].id, record.id)
})
test('文件查询编码 ID，读取失败展示后端说明', async (context) => {
  const urls = []
  context.mock.method(globalThis, 'fetch', async (url) => { urls.push(url); return Response.json({ detail: '文件尚未处理成功' }, { status: 409 }) })
  await assert.rejects(api.getFile('a/b'), /文件尚未处理成功/)
  await assert.rejects(api.getFileContent('a/b'), /文件尚未处理成功/)
  assert.deepEqual(urls, ['/api/files/a%2Fb', '/api/files/a%2Fb/content'])
})
test('无效文件不发请求，取消异常原样传播', async (context) => {
  let count = 0
  const error = new DOMException('Aborted', 'AbortError')
  context.mock.method(globalThis, 'fetch', async () => { count++; throw error })
  await assert.rejects(api.uploadFile(new File(['pdf'], 'a.pdf')), /TXT/)
  assert.equal(count, 0)
  await assert.rejects(api.uploadFile(selected()), (value) => value === error)
})
test('选择文件只校验；上传完成后才显示文件；正文按需获取且可收起', async () => {
  const { state, calls } = fixture()
  choose(state)
  assert.equal(state.uploadedFile.value, null)
  assert.equal(calls.filter(([name]) => name !== 'validateTextFile').length, 0)
  await state.submitFile()
  assert.deepEqual(state.uploadedFile.value, record)
  assert.deepEqual(state.files.value, [record])
  assert.equal(state.contentLoaded.value, false)
  await state.togglePreview()
  assert.equal(state.textContent.value, '提取的正文')
  assert.equal(state.previewOpen.value, true)
  await state.togglePreview()
  assert.equal(state.previewOpen.value, false)
  await state.togglePreview()
  assert.equal(calls.filter(([name]) => name === 'getFileContent').length, 1)
})
test('上传过程中阻止重复提交、切换文件及其他文件请求', async () => {
  let finish
  const { state, calls } = fixture({ uploadFile: () => new Promise((resolve) => { finish = resolve }) })
  choose(state)
  const original = state.selectedFile.value
  const pending = state.submitFile()
  assert.equal(state.busy.value, true)
  await state.submitFile()
  choose(state, new File(['other'], 'other.txt'))
  await state.refreshFile()
  await state.togglePreview()
  assert.equal(state.selectedFile.value, original)
  assert.equal(calls.filter(([name]) => name === 'uploadFile').length, 1)
  finish(record)
  await pending
  assert.equal(state.busy.value, false)
})
test('无效选择清空选择框；上传和预览失败后可重试，保留原成功数据', async () => {
  let failed = true
  const { state } = fixture({ uploadFile: async () => { if (failed) throw new Error('上传失败'); return { ...record, id: 'new-file' } }, getFileContent: async () => { if (failed) throw new Error('正文读取失败'); return { id: record.id, text_content: '重试正文' } } })
  assert.equal(choose(state, new File(['bad'], 'bad.pdf')).value, '')
  assert.equal(state.selectedFile.value, null)
  state.uploadedFile.value = record
  choose(state)
  await state.submitFile()
  assert.equal(state.errorMessage.value, '上传失败')
  assert.equal(state.uploadedFile.value.id, record.id)
  await state.togglePreview()
  assert.equal(state.errorMessage.value, '正文读取失败')
  assert.equal(state.contentLoaded.value, false)
  failed = false
  await state.togglePreview()
  assert.equal(state.textContent.value, '重试正文')
  await state.submitFile()
  assert.equal(state.uploadedFile.value.id, 'new-file')
  assert.equal(state.previewOpen.value, false)
  assert.equal(state.textContent.value, '')
})
test('刷新处理状态，处理未完成时不能预览正文', async () => {
  const { state, calls } = fixture({ getFile: async () => ({ ...record, status: 'failed', error: '编码错误' }) })
  state.uploadedFile.value = { ...record, status: 'processing' }
  state.files.value = [state.uploadedFile.value]
  assert.equal(state.statusText.value, '处理中')
  await state.togglePreview()
  assert.equal(calls.length, 0)
  await state.refreshFile()
  assert.equal(state.statusText.value, '处理失败')
  assert.equal(state.uploadedFile.value.error, '编码错误')
  assert.equal(state.files.value[0].status, 'failed')
})

test('页面挂载恢复列表，默认选第一份；不自动读取正文', async () => {
  const { state, calls, mount } = fixture()
  await mount[0]()
  assert.deepEqual(state.files.value, [record])
  assert.equal(state.uploadedFile.value.id, record.id)
  assert.equal(state.contentLoaded.value, false)
  assert.deepEqual(calls.map(([name]) => name), ['listFiles'])
})
test('刷新保留当前文件和预览；切换文件清理旧正文；文件消失时改选第一份', async () => {
  const other = { ...record, id: 'other', filename: '资料.txt' }
  let records = [record, other]
  const { state } = fixture({ listFiles: async () => records })
  await state.loadFiles()
  await state.togglePreview()
  await state.loadFiles()
  assert.equal(state.previewOpen.value, true)
  state.selectSavedFile(other.id)
  assert.equal(state.uploadedFile.value.id, other.id)
  assert.equal(state.textContent.value, '')
  assert.equal(state.contentLoaded.value, false)
  await state.togglePreview()
  records = [other, record]
  await state.loadFiles()
  assert.equal(state.uploadedFile.value.id, other.id)
  assert.equal(state.previewOpen.value, true)
  records = [record]
  await state.loadFiles()
  assert.equal(state.uploadedFile.value.id, record.id)
  assert.equal(state.previewOpen.value, false)
  records = []
  await state.loadFiles()
  assert.equal(state.uploadedFile.value, null)
  assert.deepEqual(state.files.value, [])
})
test('列表请求失败保留内容并可重试，上传成功在列表前面去重', async () => {
  let failed = false
  const { state } = fixture({ listFiles: async () => { if (failed) throw new Error('列表失败'); return [record] } })
  await state.loadFiles()
  await state.togglePreview()
  failed = true
  await state.loadFiles()
  assert.equal(state.errorMessage.value, '列表失败')
  assert.equal(state.files.value[0].id, record.id)
  assert.equal(state.previewOpen.value, true)
  assert.equal(state.loadingFiles.value, false)
  failed = false
  await state.loadFiles()
  assert.equal(state.errorMessage.value, '')
  choose(state)
  await state.submitFile()
  assert.equal(state.files.value.length, 1)
})
test('列表加载时拦截切换、上传和重复请求，卸载后不接受迟到列表', async () => {
  let finish
  let signal
  const { state, calls, unmount } = fixture({ listFiles: (value) => { signal = value; return new Promise((resolve) => { finish = resolve }) } })
  state.uploadedFile.value = record
  state.files.value = [record]
  choose(state)
  const pending = state.loadFiles()
  assert.equal(state.busy.value, true)
  await state.loadFiles()
  await state.submitFile()
  state.selectSavedFile('missing')
  assert.equal(state.uploadedFile.value.id, record.id)
  assert.equal(calls.filter(([name]) => name === 'listFiles').length, 1)
  assert.equal(calls.filter(([name]) => name === 'uploadFile').length, 0)
  unmount[0]()
  assert.equal(signal.aborted, true)
  finish([])
  await pending
  assert.equal(state.files.value.length, 1)
})
test('组件卸载取消正在上传的请求，忽略迟到的成功结果', async () => {
  let finish
  let signal
  const { state, unmount } = fixture({ uploadFile: (_file, value) => { signal = value; return new Promise((resolve) => { finish = resolve }) } })
  choose(state)
  const pending = state.submitFile()
  unmount.forEach((callback) => callback())
  assert.equal(signal.aborted, true)
  finish(record)
  await pending
  assert.equal(state.uploadedFile.value, null)
})
test('页面渲染上传状态和预览正文；HTML 正文只作为文字展示', async () => {
  assert.deepEqual(template.errors, [])
  const { state, component } = fixture()
  state.uploadedFile.value = record
  state.textContent.value = '<script>alert(1)</script>\n第二行'
  state.contentLoaded.value = true
  state.previewOpen.value = true
  const render = evaluate(template.code).render
  const html = await renderToString(createSSRApp({ ...component, setup: () => state, render }))
  assert.match(html, /文件资料/)
  assert.match(html, /处理完成/)
  assert.match(html, /&lt;script&gt;/)
  assert.ok(!html.includes('<script>'))
})
