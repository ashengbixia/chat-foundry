import assert from 'node:assert/strict'
import test from 'node:test'
import { createTypeScriptLoader } from './helpers/loadTypeScript.mjs'

const { renderMarkdown } = createTypeScriptLoader()('src/utils/markdown.ts')
test('助手正文支持标题、加粗、列表、代码块和表格', () => {
  const html = renderMarkdown('# 标题\n\n**重点**\n\n- 第一项\n\n```python\nprint("你好")\n```\n\n| 名称 | 数量 |\n| --- | --- |\n| 资料 | 2 |')
  for (const expected of ['<h1>标题</h1>', '<strong>重点</strong>', '<li>第一项</li>', 'language-python', '<div class="table-wrap"><table>', '<td>资料</td>']) assert.ok(html.includes(expected), expected)
})
test('HTML、事件属性和脚本链接不能变成可执行内容', () => {
  const html = renderMarkdown('<script>alert(1)</script>\n<img src=x onerror="alert(1)">\n[危险](javascript:alert(1))\n[正常](https://example.com)')
  assert.ok(!html.includes('<script>'))
  assert.ok(!html.includes('<img'))
  assert.ok(!html.includes('href="javascript:'))
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /href="https:\/\/example.com"/)
})
test('Markdown图片不加载外部资源，流式中未闭合代码片段仍安全渲染', () => {
  assert.ok(!renderMarkdown('![图片](https://example.com/track.png)').includes('<img'))
  assert.match(renderMarkdown('```html\n<script>'), /&lt;script&gt;/)
  assert.doesNotThrow(() => renderMarkdown('**还没写完'))
})

test('引用只链接工具实际提供的编号，代码和普通链接不变', () => {
  const html = renderMarkdown('结论[1]，未知[99]。`[1]`\n\n[链接[1]](https://example.com)', [1])
  assert.equal((html.match(/data-citation-id="1"/g) || []).length, 1)
  assert.ok(!html.includes('data-citation-id="99"'))
  assert.ok(html.includes('<code>[1]</code>'))
})

test('历史快照的中文引用别名使用本轮真实来源，未知编号和代码不转换', () => {
  const html = renderMarkdown('正文〔引用4〕、【引用3】、[引用4]、未知〔引用99〕、旧标记〔原回答引用4〕。`〔引用4〕`\n\n[链接〔引用4〕](https://example.com)', [3, 4])
  assert.equal((html.match(/data-citation-id="4"/g) || []).length, 2)
  assert.equal((html.match(/data-citation-id="3"/g) || []).length, 1)
  assert.ok(!html.includes('data-citation-id="99"'))
  assert.ok(html.includes('>\u005b4\u005d</button>'))
  assert.ok(html.includes('<code>〔引用4〕</code>'))
  assert.ok(html.includes('〔原回答引用4〕'))
})
