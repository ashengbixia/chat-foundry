<script setup lang="ts">
import { computed } from 'vue'
import { formatToolValue, getToolCalls, getToolStatus } from '../../utils/toolOutput'
import type { DisplayMessage } from '../../types/chat'

const props = defineProps<{ output: unknown[]; status?: DisplayMessage['status'] }>() // 接收消息状态，区分实时调用和已结束的历史记录
const toolCalls = computed(() => getToolCalls(props.output)) // 消息变化时重新配对工具结果
</script>

<template>
  <div v-if="toolCalls.length" class="tool-call-list">
    <details v-for="(call, index) in toolCalls" :key="`${call.callId}-${index}`" class="tool-call">
      <summary>
        <span>{{ call.name === 'get_current_time' ? '查询当前时间' : call.name === 'read_uploaded_file' ? '读取附件正文' : call.name }}</span>
        <small>{{ getToolStatus(call, props.status ?? 'completed') }}</small>
      </summary>
      <p>工具：<code>{{ call.name }}</code></p>
      <p>调用 ID：<code>{{ call.callId }}</code></p>
      <p>参数</p>
      <pre>{{ formatToolValue(call.arguments) }}</pre>
      <template v-if="call.result !== null">
        <p>结果</p>
        <pre>{{ formatToolValue(call.result) }}</pre>
      </template>
      <p v-else>{{ getToolStatus(call, props.status ?? 'completed') }}</p>
    </details>
  </div>
</template>

<style scoped>
.tool-call-list { margin: 0 0 14px; white-space: normal; }
.tool-call { padding: 10px 13px; margin-bottom: 7px; border: 1px solid #e7ebe8; border-radius: 10px; background: #fcfdfc; }
summary { cursor: pointer; color: #607367; font-size: 12px; line-height: 1.8; }
summary::marker { color: #94a69a; font-size: 10px; }
summary small { margin-left: 12px; color: #98a49c; font-size: 10px; }
p { margin: 12px 0 7px; color: #7d8781; font-size: 11px; }
pre { margin: 0; padding: 12px; border-radius: 7px; background: #f1f4f2; color: #4b5c51; font-family: Consolas, monospace; font-size: 11px; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
