import { createApiError } from './errors'
import type { MessagePage } from './chats'

export interface SubagentTask {
  id: string; parent_chat_id: string; child_chat_id: string; parent_message_id: string; call_id: string
  task: string; background: boolean; status: string; error: string | null; delivery_status: string
  created_at: number; started_at: number | null; finished_at: number | null
  outcome?: string
  error_info?: { phase: string; code: string; retryable?: boolean } | null
}
async function read<T>(url: string, signal?: AbortSignal, method = 'GET'): Promise<T> {
  const response = await fetch(url, { method, signal })
  if (!response.ok) throw await createApiError(response, '读取子任务失败')
  return await response.json()
}
export function listSubagents(chatId: string, callId: string, signal?: AbortSignal) {
  return read<{ items: SubagentTask[]; has_more: boolean }>(`/api/chats/${encodeURIComponent(chatId)}/subagents?call_id=${encodeURIComponent(callId)}`, signal)
}
export function subagentMessages(chatId: string, taskId: string, before?: number | null, signal?: AbortSignal) {
  return read<MessagePage>(`/api/chats/${encodeURIComponent(chatId)}/subagents/${encodeURIComponent(taskId)}/messages${before ? `?before_sequence=${before}` : ''}`, signal)
}
export function cancelSubagent(chatId: string, taskId: string, signal?: AbortSignal) {
  return read(`/api/chats/${encodeURIComponent(chatId)}/subagents/${encodeURIComponent(taskId)}/cancel`, signal, 'POST')
}
