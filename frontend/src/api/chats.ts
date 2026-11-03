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
    throw new Error(`创建会话失败，状态码：${response.status}`)
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
    throw new Error(`保存消息失败，状态码：${response.status}`)
  }

  const message: ChatMessage = await response.json()

  return message
}

export async function listChats(
  offset: number = 0,
  limit: number = 20,
): Promise<Chat[]> {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  })

  const response = await fetch(`/api/chats?${params}`, {
    headers: {
      Accept: 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`读取会话列表失败，状态码：${response.status}`)
  }

  const chats: Chat[] = await response.json()

  return chats
}

export async function listMessages(
  chatId: string,
  offset: number = 0,
  limit: number = 100,
): Promise<ChatMessage[]> {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  })

  const response = await fetch(
    `/api/chats/${encodeURIComponent(chatId)}/messages?${params}`,
    {
      headers: {
        Accept: 'application/json',
      },
    },
  )

  if (!response.ok) {
    throw new Error(`读取消息失败，状态码：${response.status}`)
  }

  const messages: ChatMessage[] = await response.json()

  return messages
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
    throw new Error(`读取会话失败，状态码：${response.status}`)
  }
  const chat: Chat = await response.json()
  return chat
}

export async function deleteChat(chatId: string): Promise<void> { // 删除指定会话及全部消息
  const response = await fetch(`/api/chats/${encodeURIComponent(chatId)}`, {
    method: 'DELETE',
  }) // 请求后端删除会话

  if (!response.ok) {
    throw new Error(`删除会话失败，状态码：${response.status}`)
  }
}

export async function renameChat(chatId: string, title: string): Promise<Chat> { // 修改会话标题并返回更新后的会话
  const response = await fetch(`/api/chats/${encodeURIComponent(chatId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  }) // 将新标题发送给后端
  if (!response.ok) {
    throw new Error(`修改标题失败，状态码：${response.status}`)
  }
  const chat: Chat = await response.json() // 读取更新后的会话信息
  return chat
}