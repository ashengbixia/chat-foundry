<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
const props = defineProps<{ chatId: string; messageId: string; output: unknown[]; busy?: boolean }>()
const emit = defineEmits<{ choose: [question: string]; layout: [] }>()
const questions = ref<string[]>([]), status = ref('pending')
let timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined, revision = 0
async function load(retry = false, poll = false, attempt = 0) {
  clearTimeout(timer); controller?.abort()
  const request = new AbortController(), current = ++revision
  controller = request
  try {
    const response = await fetch(`/api/chats/${encodeURIComponent(props.chatId)}/messages/${encodeURIComponent(props.messageId)}/follow-ups${retry ? '?retry=true' : ''}`, { method: poll ? 'GET' : 'POST', signal: request.signal })
    if (!response.ok) throw new Error('生成推荐问题失败')
    const data = await response.json()
    if (current !== revision) return
    status.value = data.status
    questions.value = data.status === 'completed' && Array.isArray(data.questions) ? data.questions.filter((q: unknown): q is string => typeof q === 'string').slice(0, 3) : []
    if (data.status === 'generating') {
      if (attempt < 24) timer = setTimeout(() => void load(false, true, attempt + 1), 1500)
      else status.value = 'failed'
    }
  } catch { if (!request.signal.aborted && current === revision) status.value = 'failed' }
}
function initialize() {
  const stored = props.output.find((item: any) => item?.type === 'follow_ups') as { status: string; questions: string[] } | undefined
  if (stored?.status === 'completed') { status.value = 'completed'; questions.value = stored.questions; void load(false, true); return }
  if (stored?.status === 'failed') { status.value = 'failed'; return }
  void load(false, stored?.status === 'generating')
}
function changed() { void load(false) }
onMounted(() => { initialize(); window.addEventListener('chatfoundry:tool-settings', changed) })
watch(() => [props.chatId, props.messageId], initialize)
watch(() => questions.value.length, () => emit('layout'), { flush: 'post' })
onUnmounted(() => { revision++; controller?.abort(); clearTimeout(timer); window.removeEventListener('chatfoundry:tool-settings', changed) })
</script>
<template>
  <div v-if="questions.length" class="follow-ups" aria-label="推荐追问"><small>推荐追问</small><button v-for="question in questions" :key="question" type="button" :disabled="busy" @click="emit('choose', question)"><span>{{ question }}</span><span aria-hidden="true">↗</span></button></div>
  <small v-else-if="status === 'generating'" class="follow-up-status">正在整理推荐追问…</small>
  <button v-else-if="status === 'failed' || status === 'busy'" class="follow-up-retry" type="button" :disabled="busy" @click="load(true)">生成推荐追问</button>
</template>
<style scoped>
.follow-ups { margin: 20px 0 6px; display: grid; gap: 6px; max-width: 620px; }.follow-ups > small,.follow-up-status { color: #a0aaa3; font-size: 11px; }.follow-ups button { display: flex; justify-content: space-between; gap: 18px; padding: 8px 0; text-align: left; color: #708678; background: transparent; border: 0; border-bottom: 1px solid #edf1ee; font: inherit; font-size: 13px; line-height: 1.7; cursor: pointer; }.follow-ups button:hover { color: #3f7054; }.follow-ups button:disabled { opacity: .5; cursor: default; }.follow-up-retry { background: none; border: 0; padding: 9px 0; color: #9aa69e; font-size: 11px; cursor: pointer; }
</style>
