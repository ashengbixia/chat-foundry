import { createApiError } from './errors'
import type { FileContent, FileInfo } from '../types/file'

export const MAX_UPLOAD_SIZE = 5 * 1024 * 1024 // 与后端一致，每个文件最大 5MB
export async function deleteFile(fileId: string, signal?: AbortSignal): Promise<void> { // 删除成功返回204，不读取空响应正文
  const response = await fetch(`/api/files/${encodeURIComponent(fileId)}`, { method: 'DELETE', signal })
  if (!response.ok) throw await createApiError(response, '删除文件失败')
}
export async function listFiles(signal?: AbortSignal): Promise<FileInfo[]> { // 页面打开或手动刷新时读取全部文件基本信息
  const response = await fetch('/api/files', { headers: { Accept: 'application/json' }, signal })
  if (!response.ok) throw await createApiError(response, '读取文件列表失败')
  return await response.json()
}
export function validateTextFile(file: Pick<File, 'name' | 'size'>): string { // 前端提前提示，后端仍需实际校验
  if (!file.name || file.name.length > 255) return '文件名不能为空，且不能超过255个字符'
  if (!file.name.toLowerCase().endsWith('.txt')) return '当前仅支持UTF-8编码的TXT文件'
  if (file.size === 0) return '不能上传空文件'
  if (file.size > MAX_UPLOAD_SIZE) return '文件大小不能超过5MB'
  return ''
}
export async function uploadFile(file: File, signal?: AbortSignal): Promise<FileInfo> { // 上传原文件，等待后端保存并提取文字
  const error = validateTextFile(file) // 避免无效文件发起网络请求
  if (error) throw new Error(error)
  const form = new FormData() // 浏览器自动生成 multipart 请求头与分隔符
  form.append('file', file)
  const response = await fetch('/api/files', { method: 'POST', body: form, signal })
  if (!response.ok) throw await createApiError(response, '上传文件失败')
  return await response.json()
}
export async function getFile(fileId: string, signal?: AbortSignal): Promise<FileInfo> { // 查询最新处理状态
  const response = await fetch(`/api/files/${encodeURIComponent(fileId)}`, { headers: { Accept: 'application/json' }, signal })
  if (!response.ok) throw await createApiError(response, '读取文件信息失败')
  return await response.json()
}
export async function rebuildFileIndex(fileId: string, signal?: AbortSignal): Promise<FileInfo> { // 202表示已安排后台任务，之后手动刷新状态
  const response = await fetch(`/api/files/${encodeURIComponent(fileId)}/index`, { method: 'POST', headers: { Accept: 'application/json' }, signal })
  if (!response.ok) throw await createApiError(response, '建立文件索引失败')
  return await response.json()
}
export async function getFileContent(fileId: string, signal?: AbortSignal): Promise<FileContent> { // 点击预览时才查询正文
  const response = await fetch(`/api/files/${encodeURIComponent(fileId)}/content`, { headers: { Accept: 'application/json' }, signal })
  if (!response.ok) throw await createApiError(response, '读取文件正文失败')
  return await response.json()
}
