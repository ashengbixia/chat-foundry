const LAST_CHAT_KEY = 'open-search:last-chat-id' // 浏览器保存上次会话 ID 的键，只在这里维护

export function getLastChatId(): string | null { // 读取上次会话 ID，浏览器禁止存储时按无记录处理
  try {
    return localStorage.getItem(LAST_CHAT_KEY) || null
  } catch (error) {
    console.warn('无法读取上次会话 ID', error)
    return null
  }
}

export function saveLastChatId(chatId: string): void { // 保存会话 ID，存储失败不阻止正常聊天
  try {
    localStorage.setItem(LAST_CHAT_KEY, chatId)
  } catch (error) {
    console.warn('无法保存当前会话 ID', error)
  }
}

export function clearLastChatId(): void { // 清除失效或已删除会话的浏览器记录
  try {
    localStorage.removeItem(LAST_CHAT_KEY)
  } catch (error) {
    console.warn('无法清除上次会话 ID', error)
  }
}
