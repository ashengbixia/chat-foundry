<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { listSubagents, subagentMessages, cancelSubagent } from '../../api/subagents'
import type { SubagentTask } from '../../api/subagents'
import type { DisplayMessage } from '../../types/chat'
import { formatDuration } from '../../utils/messageProcess'
import { createDialogBackdrop } from '../../utils/dialog'
import MessageList from './MessageList.vue'

const props = defineProps<{ chatId: string; callId: string; generating: boolean }>()
const emit = defineEmits<{ updated: [chatId: string] }>()
const tasks = ref<SubagentTask[]>([])
const selected = ref<SubagentTask | null>(null)
const messages = ref<DisplayMessage[]>([])
const dialog = ref<HTMLDialogElement | null>(null)
const error = ref('')
const loading = ref(false)
const cursor = ref<number | null>(null)
const hasMore = ref(false)
const stopping = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let controller = new AbortController()
let historyRequest = new AbortController()
let polling = false
let pollAgain = false
const labels: Record<string, string> = { queued: '排队中', running: '运行中', cancelling: '正在停止', completed: '已完成', cancelled: '已停止', failed: '失败', timed_out: '超时', interrupted: '已中断' }
const active = (task: SubagentTask) => ['queued', 'running', 'cancelling'].includes(task.status)
const taskStatus = (task: SubagentTask) => task.status === 'completed' && task.outcome === 'insufficient_evidence' ? '执行结束 · 资料不足' : labels[task.status] ?? task.status
const backdrop = createDialogBackdrop(close)
function close() { historyRequest.abort(); if (dialog.value?.open) dialog.value.close(); selected.value = null }
async function poll() {
  if (polling) { pollAgain = true; return }
  polling = true
  clearTimeout(timer)
  const request = controller
  try {
    const result = await listSubagents(props.chatId, props.callId, request.signal)
    if (request.signal.aborted) return
    error.value = ''
    const previous = new Map(tasks.value.map(task => [task.id, task.delivery_status]))
    tasks.value = result.items
    if (tasks.value.some(task => task.background && ['delivered', 'available'].includes(task.delivery_status) && previous.get(task.id) !== task.delivery_status)) emit('updated', props.chatId)
    if (selected.value) {
      selected.value = tasks.value.find(task => task.id === selected.value?.id) ?? selected.value
      if (!hasMore.value) await loadHistory(false)
    }
    if (props.generating || tasks.value.some(task => active(task) || ['pending', 'delivering'].includes(task.delivery_status))) timer = setTimeout(poll, 1500)
  } catch (cause) { if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : '读取任务失败' }
  finally { polling = false; if (pollAgain && !controller.signal.aborted) { pollAgain = false; void poll() } }
}
watch(() => [props.chatId, props.callId], () => { clearTimeout(timer); controller.abort(); controller = new AbortController(); tasks.value = []; void poll() }, { immediate: true })
watch(() => props.generating, () => { clearTimeout(timer); void poll() })
onUnmounted(() => { clearTimeout(timer); controller.abort(); historyRequest.abort() })
async function open(task: SubagentTask) {
  historyRequest.abort(); historyRequest = new AbortController(); loading.value = false
  selected.value = task; messages.value = []; cursor.value = null; hasMore.value = false
  await nextTick(); dialog.value?.showModal(); await loadHistory(false)
}
async function loadHistory(older: boolean) {
  if (!selected.value || loading.value) return
  const id = selected.value.id
  const request = historyRequest
  loading.value = true
  try {
    const page = await subagentMessages(props.chatId, id, older ? cursor.value : null, request.signal)
    if (selected.value?.id !== id || request.signal.aborted) return
    messages.value = older ? [...page.items, ...messages.value] : page.items
    cursor.value = page.next_before_sequence; hasMore.value = page.has_more
  } catch (cause) { if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : '读取子历史失败' }
  finally { if (request === historyRequest) loading.value = false }
}
async function stop(task: SubagentTask) {
  stopping.value = task.id
  try { await cancelSubagent(props.chatId, task.id, controller.signal); await poll() }
  catch (cause) { if (!controller.signal.aborted) error.value = cause instanceof Error ? cause.message : '停止失败' }
  finally { stopping.value = '' }
}
</script>

<template>
  <div class="subagent-tasks">
    <div v-for="task in tasks" :key="task.id" class="task-row">
      <span class="task-label" :title="task.task">{{ task.task }}</span>
      <small>{{ task.background ? '后台' : '前台' }} · {{ taskStatus(task) }}{{ task.delivery_status === 'delivering' ? ' · 正在汇总' : '' }}</small>
      <small>{{ formatDuration(((task.finished_at ?? Date.now() / 1000) - (task.started_at ?? task.created_at)) * 1000) }}</small>
      <button type="button" @click="open(task)">查看过程</button>
      <button v-if="active(task)" type="button" :disabled="stopping === task.id" @click="stop(task)">停止</button>
    </div>
    <p v-if="error" class="task-error" role="status">{{ error }} <button type="button" @click="poll">重试</button></p>
    <dialog ref="dialog" class="subagent-dialog" @cancel.prevent="close" @close="close" @pointerdown="backdrop.pointerdown" @click="backdrop.click">
      <template v-if="selected">
        <header><div><small>子任务 · {{ taskStatus(selected) }}</small><h3>{{ selected.task }}</h3></div><button type="button" aria-label="关闭子任务历史" @click="close">×</button></header>
        <p v-if="selected.error" class="task-error">{{ selected.error }}</p>
        <p v-if="selected.error_info" class="task-error">阶段：{{ selected.error_info.phase }} · {{ selected.error_info.code }}</p>
        <button v-if="hasMore" type="button" :disabled="loading" @click="loadHistory(true)">加载更早记录</button>
        <p v-if="loading && !messages.length" role="status">正在读取子历史…</p>
        <MessageList :messages="messages" :has-chat="true" />
      </template>
    </dialog>
  </div>
</template>

<style scoped>
.subagent-tasks { margin: 9px 0; font-size: 12px; }.task-row { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; padding: 5px 0; color: #7c8580; }.task-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 300px; }.task-row small { color: #9a9fa4; font-size: 10px; }.subagent-tasks button { border: 0; background: transparent; color: #527761; font-size: 11px; padding: 3px; cursor: pointer; }.task-error { font-size: 12px; color: #a76c58; }
.subagent-dialog { width: min(880px, calc(100vw - 32px)); max-height: 88dvh; padding: 24px; border: 1px solid #e6e9e7; border-radius: 13px; overflow-y: auto; color: #37423b; }.subagent-dialog::backdrop { background: #18271f66; }.subagent-dialog header { display: flex; gap: 18px; justify-content: space-between; margin-bottom: 22px; }.subagent-dialog h3 { font-size: 15px; margin: 5px 0; overflow-wrap: anywhere; }.subagent-dialog header small { color: #919b94; font-size: 11px; }.subagent-dialog header button { font-size: 23px; align-self: flex-start; }
@media(max-width:600px) { .task-label { max-width: 210px; }.subagent-dialog { width: 100vw; height: 100dvh; max-height: 100dvh; margin: 0; border-radius: 0; padding: 18px; } }
</style>
