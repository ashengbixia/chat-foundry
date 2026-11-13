import { createApiError } from './errors'

export interface UserInfo { // 只接收公开账号信息，不接收密码摘要或会话凭证
  id: string
  name: string
  username: string
  role: 'admin' | 'user' | 'disabled'
  admin_requested: boolean
  created_at: number
}
async function authRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> { // Cookie由浏览器保存，不手动读取登录凭证
  const response = await fetch(`/api/auth${path}`, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!response.ok) throw await createApiError(response, '账号操作失败')
  return response.status === 204 ? undefined as T : await response.json()
}
export async function getCurrentUser(): Promise<UserInfo | null> { // 未登录属于正常状态，不触发全局失效通知
  const response = await fetch('/api/auth/me', { credentials: 'same-origin', cache: 'no-store' })
  if (response.status === 401) return null
  if (!response.ok) throw await createApiError(response, '检查登录状态失败')
  return await response.json()
}
export const getAuthConfig = () => authRequest<{ setup_required: boolean; registration_enabled?: boolean; bootstrap_required?: boolean }>('/config')
export const login = (username: string, password: string) => authRequest<UserInfo>('/login', 'POST', { username, password })
export const signup = (username: string, password: string, setup: boolean, request_admin: boolean, bootstrap_token = '') => authRequest<UserInfo>('/signup', 'POST', { username, password, setup, request_admin, bootstrap_token })
export const logout = () => authRequest<void>('/logout', 'POST')
export const updateProfile = (name: string) => authRequest<UserInfo>('/profile', 'PATCH', { name })
export const changePassword = (current_password: string, new_password: string) => authRequest<void>('/password', 'POST', { current_password, new_password })
export const requestAdmin = () => authRequest<UserInfo>('/request-admin', 'POST')
export const listUsers = () => authRequest<UserInfo[]>('/users')
export const setUserRole = (id: string, role: UserInfo['role']) => authRequest<UserInfo>(`/users/${encodeURIComponent(id)}`, 'PATCH', { role })

export const createUser = (username: string, password: string) => authRequest<UserInfo>('/users', 'POST', { username, password })
