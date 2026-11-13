import MarkdownIt from 'markdown-it'

const parser = new MarkdownIt({ html: false, breaks: true, linkify: false }) // 解析常用 Markdown，禁用模型提供的原始 HTML
parser.renderer.rules.image = (tokens, index) => { // 图片不自动加载外部地址，避免回答触发第三方请求
  return `<span class="markdown-image">[图片：${parser.utils.escapeHtml(tokens[index]?.content || '未命名图片')}]</span>`
}
parser.renderer.rules.table_open = () => '<div class="table-wrap"><table>' // 宽表格只在自己的区域横向滚动
parser.renderer.rules.table_close = () => '</table></div>'
parser.renderer.rules.text = (tokens, index, _options, env) => { // 仅正文文本中的真实来源编号可点击，代码块和普通链接保持原样
  const content = parser.utils.escapeHtml(tokens[index]?.content || '')
  let linkDepth = 0 // 防止在模型生成的普通链接内部嵌套引用按钮
  for (let offset = 0; offset < index; offset++) {
    if (tokens[offset]?.type === 'link_open') linkDepth++
    if (tokens[offset]?.type === 'link_close') linkDepth--
  }
  if (linkDepth > 0 || !env?.citationIds) return content
  return content.replace(/\[(?:引用\s*|citation_id\s*[:：]\s*)?(\d+)\]|〔(?:引用\s*|citation_id\s*[:：]\s*)?(\d+)〕|【(?:引用\s*|citation_id\s*[:：]\s*)(\d+)】/gi, (label, square, chinese, wide) => {
    const id = Number(square ?? chinese ?? wide)
    return env.citationIds.has(id) ? `<button type="button" class="citation-link" data-citation-id="${id}" aria-label="查看来源 ${id}">[${id}]</button>` : label
  })
}

export function renderMarkdown(content: string, citationIds: number[] = []): string { // 只有经过受限解析的结果才交给 v-html
  return parser.render(content, { citationIds: new Set(citationIds) })
}
