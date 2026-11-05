import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { parse } from '@vue/compiler-sfc'
import ts from 'typescript'

const { descriptor } = parse(readFileSync(new URL('../src/components/chat/MessageInput.vue', import.meta.url), 'utf8'))
const tree = ts.createSourceFile('MessageInput.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const functions = tree.statements.filter(ts.isFunctionDeclaration).map((node) => node.getText(tree)).join('\n') // 使用组件的真实输入处理方法
const code = ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
function fixture(overrides = {}) {
  const props = { inputDisabled: false, sendDisabled: false, ...overrides }
  const events = []
  const textareaElement = { value: { scrollHeight: 320, style: {} } }
  const actions = new Function('props', 'emit', 'textareaElement', `${code}; return { handleKeydown, handleInput, resizeInput, removeAttachment }`)(props, (...args) => events.push(args), textareaElement)
  return { props, events, textareaElement, actions }
}

test('Enter发送，Shift+Enter和中文输入法确认不发送也不拦截换行', () => {
  const { actions, events } = fixture()
  let prevented = 0
  const event = { key: 'Enter', shiftKey: false, isComposing: false, keyCode: 13, preventDefault: () => prevented++ }
  actions.handleKeydown({ ...event, shiftKey: true })
  actions.handleKeydown({ ...event, isComposing: true })
  actions.handleKeydown({ ...event, keyCode: 229 })
  assert.equal(prevented, 0)
  assert.deepEqual(events, [])
  actions.handleKeydown(event)
  assert.equal(prevented, 1)
  assert.deepEqual(events, [['send']])
})

test('忙碌或不可发送时，回车不会发起请求；禁用输入不能修改草稿', () => {
  for (const overrides of [{ inputDisabled: true }, { sendDisabled: true }]) {
    const { actions, events } = fixture(overrides)
    actions.handleKeydown({ key: 'Enter', preventDefault() {} })
    assert.deepEqual(events, [])
  }
  const { actions, events } = fixture({ inputDisabled: true })
  actions.handleInput({ target: { value: '不应写入' } })
  actions.removeAttachment('file')
  assert.deepEqual(events, [])
})

test('多行内容完整回传，高度达到上限后保持可滚动，清空后收回高度', () => {
  const { actions, events, textareaElement } = fixture()
  actions.handleInput({ target: { value: '第一行\n第二行' } })
  assert.deepEqual(events, [['update:modelValue', '第一行\n第二行']])
  assert.equal(textareaElement.value.style.height, '180px')
  textareaElement.value.scrollHeight = 54
  actions.resizeInput()
  assert.equal(textareaElement.value.style.height, '54px')
  textareaElement.value = null
  assert.doesNotThrow(actions.resizeInput)
})
