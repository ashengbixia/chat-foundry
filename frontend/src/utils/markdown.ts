import MarkdownIt from 'markdown-it'

const parser = new MarkdownIt({ html: false, breaks: true, linkify: false }) // 解析常用 Markdown，禁用模型提供的原始 HTML
parser.renderer.rules.image = (tokens, index) => { // 图片不自动加载外部地址，避免回答触发第三方请求
  return `<span class="markdown-image">[图片：${parser.utils.escapeHtml(tokens[index]?.content || '未命名图片')}]</span>`
}
parser.renderer.rules.table_open = () => '<div class="table-wrap"><table>' // 宽表格只在自己的区域横向滚动
parser.renderer.rules.table_close = () => '</table></div>'

export function renderMarkdown(content: string): string { // 只有经过受限解析的结果才交给 v-html
  return parser.render(content)
}
