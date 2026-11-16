import { createApiError } from './errors'

export interface MemoryRecord { id: string; content: string; path: string; type: 'user' | 'context'; revision: number; created_at: number; updated_at: number }
export interface ToolFeatures { memory_enabled: boolean; history_enabled: boolean; knowledge_enabled: boolean; follow_ups_enabled: boolean }
export interface ToolEntry { name: string; label: string; description: string; available: boolean; on_demand: boolean }
export async function memoryRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/${path}`, { method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  if (!response.ok) throw await createApiError(response, '记忆或工具操作失败')
  return response.status === 204 ? undefined as T : await response.json()
}
