import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import ts from 'typescript'

const filename = new URL('../src/App.vue', import.meta.url) // 读取真实页面，测试实际滚动方法
const { descriptor } = parse(readFileSync(filename, 'utf8'))
const tree = ts.createSourceFile('App.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = ['showOlderMessages', 'scrollMessagesToBottom', 'updateScrollFollow'] // 提取页面中与滚动有关的方法
const source = tree.statements.filter((node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).map((node) => node.getText(tree)).join('\n')
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
function fixture(load, tick = async () => {}) { // 用可控容器模拟浏览器高度变化，不发送网络请求
  const element = { scrollHeight: 1000, scrollTop: 120, clientHeight: 300 }
  const refs = { messageListElement: { value: element }, currentChat: { value: { id: 'chat' } }, pageBusy: { value: false }, hasOlderMessages: { value: true }, showLatest: { value: false } }
  const build = new Function(...Object.keys(refs), 'loadOlderMessages', 'nextTick', `let preservingScroll = false; let autoFollowMessages = true; ${code}; return { showOlderMessages, scrollMessagesToBottom, updateScrollFollow, get preserving() { return preservingScroll }, get following() { return autoFollowMessages } }`)
  const actions = build(...Object.values(refs), () => load(element, refs), () => tick(element, refs))
  return { element, refs, actions }
}
test('前插旧消息补偿新增高度，加载期间不自动滚到底部', async () => {
  const { element, actions } = fixture(async (element) => { element.scrollTop = 180; return true }, async (element) => {
    element.scrollHeight = 1600
    actions.scrollMessagesToBottom() // 模拟正文 watch 在页面更新后执行
    assert.equal(element.scrollTop, 180)
  })
  await actions.showOlderMessages()
  assert.equal(element.scrollTop, 780)
  assert.equal(actions.preserving, false)
  assert.equal(actions.following, false)
  element.scrollTop = 1300
  actions.updateScrollFollow()
  assert.equal(actions.following, true)
})
test('读取失败不改变位置或跟随状态，仍可重试', async () => {
  let calls = 0
  const { element, actions } = fixture(async () => { calls++; return false })
  await actions.showOlderMessages()
  await actions.showOlderMessages()
  assert.equal(calls, 2)
  assert.equal(element.scrollTop, 120)
  assert.equal(actions.following, true)
  assert.equal(actions.preserving, false)
})
test('重复点击只加载一次，会话改变后不再补偿旧位置', async () => {
  let finish
  let calls = 0
  const { element, refs, actions } = fixture(() => { calls++; return new Promise((resolve) => { finish = resolve }) })
  const pending = actions.showOlderMessages()
  await actions.showOlderMessages()
  assert.equal(calls, 1)
  refs.currentChat.value = { id: 'other' }
  finish(true)
  await pending
  assert.equal(element.scrollTop, 120)
  assert.equal(actions.preserving, false)
})
test('忙碌或没有旧消息时不加载，按钮模板编译通过并绑定加载状态', async () => {
  const { refs, actions } = fixture(() => { throw new Error('不应请求') })
  refs.pageBusy.value = true
  await actions.showOlderMessages()
  refs.pageBusy.value = false
  refs.hasOlderMessages.value = false
  await actions.showOlderMessages()
  const result = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'scroll-test' })
  assert.deepEqual(result.errors, [])
  assert.match(result.code, /showOlderMessages/)
  assert.match(result.code, /loadingOlderMessages/)
})
