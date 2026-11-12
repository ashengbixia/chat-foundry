import type { ChatMessage } from '../api/chats'

export type DisplayMessage = Pick<ChatMessage, 'id' | 'role' | 'content' | 'status' | 'error' | 'output' | 'attachments'> & Partial<Pick<ChatMessage, 'created_at' | 'updated_at' | 'model' | 'chat_id'>>

export interface ToolCallRecord { // 助手 output 中的工具调用记录
  type: 'function_call'
  call_id: string
  name: string
  display_name?: string
  arguments: string
}
export interface ToolResultRecord { // 助手 output 中的工具结果记录
  type: 'function_call_output'
  call_id: string
  output: string
}
export interface DisplayToolCall { // 将调用与结果配对后，交给组件展示
  callId: string
  name: string
  displayName?: string
  arguments: string
  result: string | null
  failed: boolean
  durationMs?: number
}
