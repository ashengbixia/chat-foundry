import { getToolCalls } from './toolOutput'
export interface UserQuestion { id: string; header: string; question: string; options: { label: string; description: string }[]; allow_other: boolean }
export interface QuestionCall { callId: string; questions: UserQuestion[]; status: string; answers: Record<string, string> }
export function getUserQuestions(output: unknown): QuestionCall[] {
  return getToolCalls(output).flatMap(call => {
    if (call.name !== 'ask_user' || !call.result || call.failed) return []
    try {
      const data = JSON.parse(call.result)
      if (!Array.isArray(data.questions) || !data.questions.length || data.questions.length > 3 || !data.questions.every((q: any) => typeof q?.id === 'string' && typeof q.question === 'string' && Array.isArray(q.options) && q.options.every((o: any) => typeof o?.label === 'string' && typeof o.description === 'string'))) return []
      return [{ callId: call.callId, questions: data.questions, status: data.status, answers: data.answers ?? {} }]
    } catch { return [] }
  })
}
