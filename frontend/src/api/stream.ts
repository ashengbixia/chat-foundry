import { createApiError } from './errors'

export type ToolStreamEvent = // 后端收齐工具参数后才发送调用事件，结果用同一个 callId 关联
  | { type: 'tool_call'; callId: string; name: string; arguments: string }
  | { type: 'tool_result'; callId: string; output: string }

export type ChatStreamEvent =
  | { type: 'text_delta'; text: string }
  | { type: 'done'; messageId: string }
  | { type: 'error'; message: string }
  | ToolStreamEvent


export function parseSseEvent(
  block: string,
): ChatStreamEvent | null {
  let eventType = ''//事件名称
  const dataLines: string[] = []

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''))
    }
  }

  // 忽略空事件或没有数据的内容。
  if (dataLines.length === 0) return null

  const data: unknown = JSON.parse(dataLines.join('\n'))

  if (typeof data !== 'object' || data === null) {
    throw new Error('事件数据不是对象')
  }

  if (eventType === 'text_delta') {
    if ('text' in data && typeof data.text === 'string') {
      return { type: 'text_delta', text: data.text }
    }
  } else if (eventType === 'tool_call') {
    if ('call_id' in data && typeof data.call_id === 'string' && data.call_id && 'name' in data && typeof data.name === 'string' && data.name && 'arguments' in data && typeof data.arguments === 'string') {
      return { type: 'tool_call', callId: data.call_id, name: data.name, arguments: data.arguments }
    }
  } else if (eventType === 'tool_result') {
    if ('call_id' in data && typeof data.call_id === 'string' && data.call_id && 'output' in data && typeof data.output === 'string') {
      return { type: 'tool_result', callId: data.call_id, output: data.output }
    }
  } else if (eventType === 'done') {
    if ('message_id' in data && typeof data.message_id === 'string') {
      return { type: 'done', messageId: data.message_id }
    }
  } else if (eventType === 'error') {
    if ('message' in data && typeof data.message === 'string') {
      return { type: 'error', message: data.message }
    }
  } else {
    // 暂时忽略未支持的事件类型。
    return null
  }

  throw new Error(`事件字段不符合约定：${eventType}`)
}

export async function streamChatReply(
    chatId: string,
    onText: (text: string) => void,
    signal?: AbortSignal,
    onTool?: (event: ToolStreamEvent) => void): Promise<string> { // 保留原有正文和取消参数，增加可选工具事件回调
  const response = await fetch(
      `/api/chats/${encodeURIComponent(chatId)}/respond`,
      {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    signal, // 将取消信号绑定到这个请求
  })

  if (!response.ok) {
    throw await createApiError(response, '生成回答失败')
  }

  if (!response.body) {
    throw new Error('浏览器未获取到响应流')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()

      // 字节转成文字；流式解码可以处理被拆开的中文字符。
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true })

      // 一个空行标志一条 SSE 事件结束。
      let boundary = /\r?\n\r?\n/.exec(buffer)

      while (boundary !== null) {
        const block = buffer.slice(0, boundary.index)

        buffer = buffer.slice(
          boundary.index + boundary[0].length,
        )

        const event = parseSseEvent(block)

        if (event?.type === 'text_delta') {
          onText(event.text)
        } else if (event?.type === 'tool_call' || event?.type === 'tool_result') {
          onTool?.(event) // 将工具事件交给仓库，不在请求层修改页面状态
        } else if (event?.type === 'error') {
          throw new Error(event.message)
        } else if (event?.type === 'done') {
          return event.messageId
        }

        boundary = /\r?\n\r?\n/.exec(buffer)
      }

      if (done) {
        // 网络关闭不等于生成成功，必须收到后端的 done。
        throw new Error('连接已结束，但没有收到完成事件')
      }
    }
  } finally {
    // 正常返回、解析失败或回调出错时，都清理读取器。
    try {
      await reader.cancel()
    } finally {
      reader.releaseLock()
    }
  }
}
