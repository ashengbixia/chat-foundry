export async function createApiError(response: Response, operation: string): Promise<Error> { // 读取后端错误说明，非 JSON 或未知格式时使用操作名称和状态码
  const fallback = `${operation}，状态码：${response.status}` // 没有可用错误正文时的默认说明
  let data: unknown // 解析后的错误正文
  try {
    data = await response.json()
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error // 取消不是正文格式错误，继续传播原来的取消
    return new Error(fallback)
  }
  if (typeof data === 'object' && data !== null && 'detail' in data) {
    const detail = data.detail // FastAPI 返回的错误说明
    if (typeof detail === 'string' && detail.trim()) return new Error(detail)
    if (Array.isArray(detail)) {
      const messages = detail.map((item: unknown) => { // 参数校验错误只展示说明，不拼接可能包含隐私的 input 字段
        if (typeof item === 'object' && item !== null && 'msg' in item && typeof item.msg === 'string') return item.msg.trim()
        return ''
      }).filter((message) => message.length > 0)
      if (messages.length > 0) return new Error(messages.join('；'))
    }
  }
  return new Error(fallback)
}
