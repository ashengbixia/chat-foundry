import { listMessages } from '../api/chats'
import type { Chat } from '../api/chats'

export async function exportChatMarkdown(chat: Chat, active: () => boolean = () => true) {
  const pages: string[][] = []
  let before: number | null = null
  while (active()) {
    const page = await listMessages(chat.id, before, 100)
    if (!active()) return
    pages.unshift(page.items.map(message => `## ${message.role === 'user' ? '用户' : '助手'}\n\n${message.content}${message.attachments?.length ? `\n\n附件：${message.attachments.map(file => file.filename).join('、')}` : ''}`))
    if (!page.has_more) break
    if (page.next_before_sequence === null || (before !== null && page.next_before_sequence >= before)) throw new Error('读取历史消息失败，请刷新后重试')
    before = page.next_before_sequence
  }
  if (!active()) return
  const content = `# ${chat.title}\n\n${pages.flat().join('\n\n---\n\n')}\n`
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `${chat.title.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 100) || '对话'}.md`
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
