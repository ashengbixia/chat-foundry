import { createApiError } from './errors'
import type { FileInfo } from '../types/file'

export interface KnowledgeReference { id: string; name: string }
export interface KnowledgeBase extends KnowledgeReference { description: string; file_count: number; created_at: number; updated_at: number }
async function request<T>(url: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { method, signal, headers: body === undefined ? { Accept: 'application/json' } : { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  if (!response.ok) throw await createApiError(response, '知识库操作失败')
  return response.status === 204 ? undefined as T : await response.json()
}
const baseUrl = (id: string) => `/api/knowledge/${encodeURIComponent(id)}`
export const updateKnowledgeIndex = (id: string, signal?: AbortSignal) => request<{ queued: number; ready: number; busy: number; failed: number }>(`${baseUrl(id)}/index`, 'POST', undefined, signal)
export const listKnowledge = (signal?: AbortSignal) => request<KnowledgeBase[]>('/api/knowledge', 'GET', undefined, signal)
export const createKnowledge = (name: string, description: string, signal?: AbortSignal) => request<KnowledgeBase>('/api/knowledge', 'POST', { name, description }, signal)
export const updateKnowledge = (id: string, name: string, description: string, signal?: AbortSignal) => request<KnowledgeBase>(baseUrl(id), 'PATCH', { name, description }, signal)
export const deleteKnowledge = (id: string, signal?: AbortSignal) => request<void>(baseUrl(id), 'DELETE', undefined, signal)
export const listKnowledgeFiles = (id: string, signal?: AbortSignal) => request<FileInfo[]>(`${baseUrl(id)}/files`, 'GET', undefined, signal)
export const addKnowledgeFiles = (id: string, fileIds: string[], signal?: AbortSignal) => request<void>(`${baseUrl(id)}/files`, 'POST', { file_ids: fileIds }, signal)
export const removeKnowledgeFile = (id: string, fileId: string, signal?: AbortSignal) => request<void>(`${baseUrl(id)}/files/${encodeURIComponent(fileId)}`, 'DELETE', undefined, signal)
export const setChatKnowledge = (chatId: string, ids: string[], signal?: AbortSignal) => request<KnowledgeBase[]>(`/api/chats/${encodeURIComponent(chatId)}/knowledge`, 'PUT', { knowledge_base_ids: ids }, signal)
