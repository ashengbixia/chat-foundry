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
.tool-call-list { margin: 8px 0; white-space: normal; }
.tool-call { padding: 10px 12px; margin-bottom: 8px; border: 1px solid #ccc; border-radius: 6px; }
summary { cursor: pointer; }
summary small { margin-left: 12px; color: #666; }
p { margin: 8px 0; }
pre { margin: 0; padding: 8px; background: #f4f4f4; color: #222; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
