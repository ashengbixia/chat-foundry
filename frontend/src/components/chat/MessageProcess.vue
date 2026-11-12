<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import type { DisplayMessage } from '../../types/chat'
import { formatDuration, messageDuration, processParts } from '../../utils/messageProcess'
import { getToolCalls } from '../../utils/toolOutput'
import { renderMarkdown } from '../../utils/markdown'
import ToolCallList from './ToolCallList.vue'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ message: DisplayMessage }>()
const emit = defineEmits<{ updated: [chatId: string] }>()
const parts = computed(() => processParts(props.message.output))
const calls = computed(() => getToolCalls(props.message.output))
const hasReasoning = computed(() => parts.value.some(part => part.kind === 'reasoning'))
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
watch(() => props.message.status, status => {
  clearInterval(timer)
  if (status === 'generating') timer = setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })
onUnmounted(() => clearInterval(timer))
const elapsed = computed(() => props.message.status === 'generating' ? now.value - (props.message.created_at ?? now.value / 1000) * 1000 : messageDuration(props.message))
const label = computed(() => props.message.status === 'generating' ? (hasReasoning.value && !calls.value.length ? '正在思考' : '正在处理') : props.message.status === 'cancelled' ? '已停止' : props.message.status === 'failed' ? '处理未完成' : hasReasoning.value ? '已思考' : '处理过程')
</script>

<template>
  <details v-if="parts.length" class="message-process" :open="message.status === 'generating'">
    <summary><AppIcon name="chevron" :size="12" /><span>{{ label }}</span><span v-if="elapsed !== undefined">· {{ formatDuration(elapsed) }}</span><span v-if="calls.length">· {{ calls.length }} 项工具</span><span v-if="calls.some(call => call.failed)" class="process-warning">有调用未成功</span></summary>
    <div class="process-body">
      <template v-for="(part, index) in parts" :key="index">
        <ToolCallList v-if="part.kind === 'tools'" :output="part.output ?? []" :status="message.status" :chat-id="message.chat_id" @updated="emit('updated', $event)" />
        <details v-else-if="part.kind === 'reasoning'" class="reasoning-section">
          <summary>模型思考 <small>展开查看</small></summary>
          <div class="process-text" v-html="renderMarkdown(part.text ?? '')"></div>
        </details>
        <div v-else class="process-text commentary" v-html="renderMarkdown(part.text ?? '')"></div>
      </template>
    </div>
  </details>
</template>

<style scoped>
.message-process { margin: 0 0 16px; color: #92969c; font-size: 12px; white-space: normal; }
.message-process > summary { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; width: fit-content; cursor: pointer; list-style: none; padding: 3px 0; }
summary::-webkit-details-marker { display: none; }
.message-process[open] > summary svg { transform: rotate(90deg); }
.process-body { margin-top: 12px; padding: 2px 0 2px 14px; border-left: 1px solid #e9eaec; max-height: 360px; overflow-y: auto; scrollbar-width: thin; }
.process-text { color: #7e848b; font-size: 13px; line-height: 1.8; overflow-wrap: anywhere; }
.process-text :deep(p) { margin: 5px 0 12px; }.process-text :deep(pre) { overflow-x: auto; padding: 9px; background: #f6f7f8; }.process-text :deep(a) { color: #668575; }
.reasoning-section { margin: 3px 0 12px; }.reasoning-section > summary { cursor: pointer; color: #8d939a; }.reasoning-section small { font-size: 10px; margin-left: 6px; color: #a5a9af; }
.process-warning { color: #ab8b69; font-size: 10px; margin-left: 4px; }
</style>
