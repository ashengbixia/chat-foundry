import type { ChatMessage } from '../api/chats'

export type DisplayMessage = Pick<ChatMessage, 'id' | 'role' | 'content' | 'status' | 'error'> // 页面展示字段，也用于尚未取得数据库 ID 的助手占位消息
