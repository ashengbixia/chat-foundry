import { getToolCalls } from './toolOutput'

export interface CitationSource { // 从后端工具结果恢复来源快照，不从模型正文猜测文件或编号
  id: number
  fileId: string
  filename: string
  content: string
  startOffset: number
  endOffset: number
}
export function getCitationSources(output: unknown): CitationSource[] { // 历史和流式消息使用相同的来源数据
  const sources = new Map<number, CitationSource>() // 同一段原文重复检索时只展示一次
  for (const call of getToolCalls(output)) {
    if (call.failed || call.result === null || !['search_uploaded_files', 'read_uploaded_file'].includes(call.name)) continue
    try {
      const data = JSON.parse(call.result)
      const items = call.name === 'search_uploaded_files' ? data?.chunks : [data]
      if (!Array.isArray(items)) continue
      for (const item of items) {
        if (!item || !Number.isSafeInteger(item.citation_id) || item.citation_id < 1 || sources.has(item.citation_id)) continue
        const content = call.name === 'search_uploaded_files' ? item.content : item.text_content // 原文快照随工具记录保存
        const start = call.name === 'search_uploaded_files' ? item.start_offset : item.offset
        if (typeof content !== 'string' || !content.trim() || typeof item.file_id !== 'string' || typeof item.filename !== 'string' || !Number.isSafeInteger(start) || start < 0) continue
        sources.set(item.citation_id, { id: item.citation_id, fileId: item.file_id, filename: item.filename, content, startOffset: start, endOffset: start + Array.from(content).length })
      }
    } catch { /* 损坏的旧记录不生成来源链接 */ }
  }
  return [...sources.values()].sort((left, right) => left.id - right.id)
}
export function locateCitation(text: string, source: CitationSource) { // 后端按Unicode字符计数，不能直接用JS的UTF-16位置切片
  let position = 0, start = 0, end = 0 // 字符数与浏览器字符串位置分别累计
  for (const character of text) {
    if (position < source.startOffset) start += character.length
    if (position < source.endOffset) end += character.length
    else break
    position++
  }
  const quote = text.slice(start, end) // 只有快照与当前原文一致时才高亮
  return { before: text.slice(0, start), quote, after: text.slice(end), matches: quote === source.content }
}
