import { createApiError } from './errors'
import type { ModelServiceConfig, ModelServiceDraft } from './modelSettings'
import type { Chat } from './chats'

export async function workspaceRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  if (!response.ok) throw await createApiError(response, '工作空间操作失败')
  return response.status === 204 ? undefined as T : response.json()
}
export interface Connection extends ModelServiceConfig { id: string; name: string; active?: boolean }
export interface Assistant { id: string; name: string; system_prompt: string; connection_id: string | null; model: string; tool_names: string[] | null; knowledge_ids: string[] }
export type AssistantDraft = Omit<Assistant, 'id'>
const root = '/api/workspace'
export const listConnections = () => workspaceRequest<Connection[]>(`${root}/connections`)
export const saveConnection = (name: string, draft: ModelServiceDraft) => workspaceRequest<Connection>(`${root}/connections`, 'POST', { ...draft, name })
export const activateConnection = (id: string, model?: string) => workspaceRequest<{ config: ModelServiceConfig }>(`${root}/connections/${encodeURIComponent(id)}/activate`, 'POST', { model })
export const deleteConnection = (id: string) => workspaceRequest<void>(`${root}/connections/${encodeURIComponent(id)}`, 'DELETE')
export const aggregateModels = () => workspaceRequest<{ connections: { connection_id: string; connection_name: string; models: string[]; notice?: string }[] }>(`${root}/models`, 'POST')
export const listAssistants = () => workspaceRequest<Assistant[]>(`${root}/assistants`)
export const saveAssistant = (draft: AssistantDraft, id?: string) => workspaceRequest<Assistant>(`${root}/assistants${id ? `/${encodeURIComponent(id)}` : ''}`, id ? 'PUT' : 'POST', draft)
export const deleteAssistant = (id: string) => workspaceRequest<void>(`${root}/assistants/${encodeURIComponent(id)}`, 'DELETE')
export const listBranches = (id: string) => workspaceRequest<Chat[]>(`/api/chats/${encodeURIComponent(id)}/branches`)
export const forkBranch = (id: string, message_id: string, content?: string, regenerate = false) => workspaceRequest<Chat>(`/api/chats/${encodeURIComponent(id)}/branches`, 'POST', { message_id, content, regenerate })
export interface Share { id: string; username: string }
export const listShares = (id: string) => workspaceRequest<Share[]>(`${root}/knowledge/${encodeURIComponent(id)}/shares`)
export const addShare = (id: string, username: string) => workspaceRequest<Share>(`${root}/knowledge/${encodeURIComponent(id)}/shares`, 'POST', { username })
export const removeShare = (id: string, user: string) => workspaceRequest<void>(`${root}/knowledge/${encodeURIComponent(id)}/shares/${encodeURIComponent(user)}`, 'DELETE')
