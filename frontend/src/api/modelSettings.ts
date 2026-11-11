import { createApiError } from './errors'

export type ModelProvider = 'server' | 'deepseek' | 'openai' | 'qwen' | 'ollama' | 'custom'
export interface ModelCatalog { models: string[]; notice?: string; capabilities?: Record<string, string[]>; excluded_models?: { id: string; reason: string }[] } // 未知类型保留，只有服务明确声明的向量模型才被过滤
export interface ModelServiceConfig { // 响应中只有Key状态，没有明文或密文
  provider: ModelProvider
  base_url: string
  model: string
  timeout: number
  key_source: 'environment' | 'personal' | 'none'
  key_configured: boolean
}
export interface ModelServiceDraft extends Omit<ModelServiceConfig, 'key_configured'> { api_key: string | null } // null表示地址未变时保留已保存的个人Key
export interface ModelPreset { id: ModelProvider; name: string; base_url: string; model: string }
export interface ModelSettingsResponse { config: ModelServiceConfig; inherited: boolean; presets: ModelPreset[] }
async function request<T>(path = '', method = 'GET', body?: ModelServiceDraft, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/settings/model-service${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal })
  if (!response.ok) throw await createApiError(response, '模型服务操作失败')
  return response.status === 204 ? undefined as T : await response.json()
}
export const getModelSettings = (signal?: AbortSignal) => request<ModelSettingsResponse>('', 'GET', undefined, signal)
export const saveModelSettings = (body: ModelServiceDraft, signal?: AbortSignal) => request<{ config: ModelServiceConfig; inherited: boolean }>('', 'PUT', body, signal)
export const resetModelSettings = (signal?: AbortSignal) => request<void>('', 'DELETE', undefined, signal)
export const discoverModels = (body: ModelServiceDraft, signal?: AbortSignal) => request<ModelCatalog>('/models', 'POST', body, signal)
export const testModelService = (body: ModelServiceDraft, signal?: AbortSignal) => request<{ message: string; status?: 'ok' | 'limited' }>('/test', 'POST', body, signal)
export async function selectModel(model: string, base_url: string, signal?: AbortSignal): Promise<{ config: ModelServiceConfig; inherited: boolean }> { // 快捷切换只提交模型名和校验地址，不携带Key
  const response = await fetch('/api/settings/model-service/model', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model, base_url }), signal })
  if (!response.ok) throw await createApiError(response, '切换模型失败')
  return await response.json()
}
