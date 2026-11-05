import type { ChatMessage } from '../api/chats'

export type DisplayMessage = Pick<ChatMessage, 'id' | 'role' | 'content' | 'status' | 'error' | 'output' | 'attachments'> // 页面展示字段，包含工具和附件记录

export interface ToolCallRecord { // 助手 output 中的工具调用记录
  type: 'function_call'
  call_id: string
  name: string
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
  arguments: string
  result: string | null
  failed: boolean
}
