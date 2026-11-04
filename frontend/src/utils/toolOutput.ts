import type { DisplayMessage, DisplayToolCall, ToolCallRecord, ToolResultRecord } from '../types/chat'

function isRecord(value: unknown): value is Record<string, unknown> { // 旧记录或异常数据不能直接当作对象读取
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function isToolCall(value: unknown): value is ToolCallRecord { // 检查调用记录所需的基本字段
  return isRecord(value) && value.type === 'function_call' && typeof value.call_id === 'string' && !!value.call_id && typeof value.name === 'string' && !!value.name && typeof value.arguments === 'string'
}
function isToolResult(value: unknown): value is ToolResultRecord { // 工具结果必须包含关联 ID 和字符串结果
  return isRecord(value) && value.type === 'function_call_output' && typeof value.call_id === 'string' && !!value.call_id && typeof value.output === 'string'
}
function hasToolError(result: string | null): boolean { // 本项目用包含 error 字段的 JSON 返回工具错误
  if (result === null) return false
  try {
    const value: unknown = JSON.parse(result) // 普通文本结果也允许展示
    return isRecord(value) && Object.hasOwn(value, 'error')
  } catch {
    return false
  }
}
export function getToolCalls(output: unknown): DisplayToolCall[] { // 按 call_id 配对，只读取记录，不执行工具
  if (!Array.isArray(output)) return []
  const results = new Map<string, string>() // 已保存的工具结果
  for (const item of output) {
    if (isToolResult(item)) results.set(item.call_id, item.output)
  }
  return output.filter(isToolCall).map((call) => {
    const result = results.get(call.call_id) ?? null // 没有结果时不能误显示为执行完成
    return { callId: call.call_id, name: call.name, arguments: call.arguments, result, failed: hasToolError(result) }
  })
}
export function formatToolValue(value: string): string { // JSON 缩进展示，普通文本保持原样
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}
export function getToolStatus(call: DisplayToolCall, status: DisplayMessage['status']): string { // 没有结果时结合消息状态判断，避免一直显示正在调用
  if (call.result !== null) return call.failed ? '返回错误' : '已返回结果'
  if (status === 'generating') return '正在调用'
  if (status === 'cancelled') return '已停止，未收到结果'
  if (status === 'failed') return '调用中断，未收到结果'
  return '未记录结果'
}
