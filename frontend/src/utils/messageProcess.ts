import type { DisplayMessage } from '../types/chat'

type RecordValue = Record<string, unknown>
export function records(output: unknown): RecordValue[] {
  return Array.isArray(output) ? output.filter((item): item is RecordValue => !!item && typeof item === 'object') : []
}
function messageText(item: RecordValue): string {
  return Array.isArray(item.content) ? item.content.map(part => part?.type === 'output_text' && typeof part.text === 'string' ? part.text : '').join('') : ''
}
function isCommentary(item: RecordValue, index: number, items: RecordValue[]) {
  return item.type === 'message' && (item.phase === 'commentary' || items.slice(index + 1).some(next => next.type === 'function_call'))
}
export function answerContent(message: DisplayMessage): string {
  const items = records(message.output)
  const final = items.filter((item, index) => item.type === 'message' && !isCommentary(item, index, items)).map(messageText).join('\n\n')
  // 旧记录按结构化轮次分离过程；流式正文尚未存入output时继续显示实时content。
  if (final) return final
  let body = message.content
  items.filter((item, index) => isCommentary(item, index, items)).forEach(item => {
    const text = messageText(item)
    if (text && body.startsWith(text)) body = body.slice(text.length)
  })
  return body
}
export interface ProcessPart { kind: 'reasoning' | 'commentary' | 'tools'; text?: string; output?: unknown[] }
export function processParts(output: unknown): ProcessPart[] {
  const items = records(output), parts: ProcessPart[] = []
  items.forEach((item, index) => {
    if (item.type === 'reasoning' && typeof item.text === 'string') parts.push({ kind: 'reasoning', text: item.text })
    else if (isCommentary(item, index, items)) parts.push({ kind: 'commentary', text: messageText(item) })
    else if (item.type === 'function_call' || item.type === 'function_call_output') {
      let part = parts.at(-1)
      if (part?.kind !== 'tools') { part = { kind: 'tools', output: [] }; parts.push(part) }
      part.output!.push(item)
    }
  })
  return parts
}
export function messageDuration(message: DisplayMessage): number | undefined {
  const meta = records(message.output).find(item => item.type === 'turn_metadata')
  if (typeof meta?.duration_ms === 'number') return meta.duration_ms
  if (message.created_at && message.updated_at && message.updated_at >= message.created_at) return (message.updated_at - message.created_at) * 1000
}
export function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.round(milliseconds / 1000))
  if (seconds < 1) return '<1秒'
  return seconds >= 60 ? `${Math.floor(seconds / 60)}分${seconds % 60 ? `${seconds % 60}秒` : ''}` : `${seconds}秒`
}
export function messageClock(timestamp?: number): string {
  return timestamp ? new Date(timestamp * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }) : ''
}
export function isTaskUpdate(message: DisplayMessage): boolean {
  return records(message.output).some(item => item.type === 'turn_metadata' && item.run_kind === 'subagent_update')
}
