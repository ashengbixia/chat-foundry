import { createApiError } from './errors'

// 对应后端 ChatResponse 的返回结构。
export interface Chat {
  id: string
  title: string
  created_at: number
  updated_at: number
}

export async function createChat(title: string = '新会话',): Promise<Chat>
{
  const response = await fetch('/api/chats', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ title }),
  })

  if (!response.ok) {
    throw await createApiError(response, '创建会话失败')
  }
  const chat: Chat = await response.json()
  return chat
}

export interface ChatMessage {
  id: string
  chat_id: string
  sequence: number
  role: 'user' | 'assistant'
  content: string
  output: unknown[]
  status: 'generating' | 'completed' | 'cancelled' | 'failed'
  model: string | null
  usage: Record<string, unknown> | null
  error: string | null
  created_at: number
  updated_at: number
}

export interface MessagePage { // 后端返回的一批消息及下一批查询信息
  items: ChatMessage[] // 本批消息，按序号从小到大排列
  has_more: boolean // 是否还有更早消息
  next_before_sequence: number | null // 下一批查询边界
}
export async function saveUserMessage(
  chatId: string,
  content: string,
): Promise<ChatMessage> {
  const response = await fetch(
    `/api/chats/${encodeURIComponent(chatId)}/messages`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ content }),
    },
  )

  if (!response.ok) {
    throw await createApiError(response, '保存消息失败')
  }

  const message: ChatMessage = await response.json()

  return message
}

export async function listChats(): Promise<Chat[]> { // 一次读取全部会话的基本信息，不加载消息正文
  const response = await fetch('/api/chats', {
    headers: {
      Accept: 'application/json',
    },
  })

  if (!response.ok) {
    throw await createApiError(response, '读取会话列表失败')
  }

  const chats: Chat[] = await response.json()

  return chats
}

export async function listMessages(chatId: string, beforeSequence: number | null = null, limit: number = 8): Promise<MessagePage> { // 页面每批默认查询 8 条消息
  const params = new URLSearchParams({ limit: String(limit) }) // 本次查询数量
  if (beforeSequence !== null) params.set('before_sequence', String(beforeSequence))
  const response = await fetch(`/api/chats/${encodeURIComponent(chatId)}/messages?${params}`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw await createApiError(response, '读取消息失败')
  const page: MessagePage = await response.json() // 接收后端分页对象
  if (!page || !Array.isArray(page.items) || typeof page.has_more !== 'boolean' || (page.next_before_sequence !== null && (!Number.isInteger(page.next_before_sequence) || page.next_before_sequence < 1)) || (page.has_more && page.next_before_sequence === null)) {
    throw new Error('消息分页响应格式不正确')
  }
  return page
}

export async function getChat(
  chatId: string,
): Promise<Chat | null> {
  const response = await fetch(
    `/api/chats/${encodeURIComponent(chatId)}`,
    {
      headers: {
        Accept: 'application/json',
      },
    },
  )

  // 会话被删除时，明确返回“不存在”。
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw await createApiError(response, '读取会话失败')
  }
  const chat: Chat = await response.json()
  return chat
}

export async function deleteChat(chatId: string): Promise<void> { // 删除指定会话及全部消息
  const response = await fetch(`/api/chats/${encodeURIComponent(chatId)}`, {
    method: 'DELETE',
  }) // 请求后端删除会话

  if (!response.ok) {
    throw await createApiError(response, '删除会话失败')
  }
}

export async function renameChat(chatId: string, title: string): Promise<Chat> { // 修改会话标题并返回更新后的会话
  const response = await fetch(`/api/chats/${encodeURIComponent(chatId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  }) // 将新标题发送给后端
  if (!response.ok) {
    throw await createApiError(response, '修改标题失败')
  }
  const chat: Chat = await response.json() // 读取更新后的会话信息
  return chat
}
