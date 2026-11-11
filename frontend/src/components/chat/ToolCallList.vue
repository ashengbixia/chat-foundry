<script setup lang="ts">
import { computed } from 'vue'
import { formatToolValue, getRetrievalPreview, getToolCalls, getToolStatus } from '../../utils/toolOutput'
import type { DisplayMessage } from '../../types/chat'

const props = defineProps<{ output: unknown[]; status?: DisplayMessage['status'] }>() // 接收消息状态，区分实时调用和已结束的历史记录
const toolCalls = computed(() => getToolCalls(props.output).map((call) => ({ ...call, retrieval: call.name === 'search_uploaded_files' ? getRetrievalPreview(call.result) : null }))) // 消息变化时配对结果并解析来源卡片
</script>

<template>
  <div v-if="toolCalls.length" class="tool-call-list">
    <details v-for="(call, index) in toolCalls" :key="`${call.callId}-${index}`" class="tool-call">
      <summary>
        <span>{{ call.name === 'get_current_time' ? '查询当前时间' : call.name === 'read_uploaded_file' ? '读取附件正文' : call.name === 'search_uploaded_files' ? '检索附件内容' : call.name }}</span>
        <small>{{ getToolStatus(call, props.status ?? 'completed') }}</small>
        <small v-if="call.retrieval">{{ call.retrieval.status === 'unavailable' ? '索引不可用' : `${call.retrieval.chunks.length} 个片段` }}</small>
      </summary>
      <div v-if="call.retrieval" class="retrieval-result">
        <p v-if="!call.retrieval.chunks.length">{{ call.retrieval.status === 'unavailable' ? '当前附件没有可用索引，可以建立索引或读取原文。' : '本次没有找到可用的候选片段。' }}</p>
        <details v-for="(chunk, chunkIndex) in call.retrieval.chunks" :key="`${chunk.chunkId}-${chunkIndex}`" class="source-card">
          <summary><span>{{ chunk.filename }}</span><small>片段 {{ chunk.chunkIndex + 1 }}</small></summary>
          <p class="source-content">{{ chunk.content }}</p>
          <small class="source-position">原文字符 {{ chunk.startOffset }}～{{ chunk.endOffset }}{{ chunk.truncated ? ' · 本片段未展示完整' : '' }}</small>
        </details>
        <p v-for="file in call.retrieval.unavailable" :key="file.fileId" class="source-warning">{{ file.filename }}：{{ file.message }}</p>
        <p v-if="call.retrieval.truncated" class="source-warning">结果已达到正文长度限制，需要更多内容时可继续读取原文。</p>
        <p v-if="call.retrieval.notice" class="source-notice">{{ call.retrieval.notice }}</p>
      </div>
      <details v-if="call.retrieval" class="raw-tool">
        <summary>查看原始调用数据</summary>
        <p>工具：<code>{{ call.name }}</code></p>
        <p>调用 ID：<code>{{ call.callId }}</code></p>
        <p>参数</p><pre>{{ formatToolValue(call.arguments) }}</pre>
        <p>结果</p><pre>{{ formatToolValue(call.result ?? '') }}</pre>
      </details>
      <template v-else>
        <p>工具：<code>{{ call.name }}</code></p>
        <p>调用 ID：<code>{{ call.callId }}</code></p>
        <p>参数</p>
        <pre>{{ formatToolValue(call.arguments) }}</pre>
        <template v-if="call.result !== null">
          <p>结果</p>
          <pre>{{ formatToolValue(call.result) }}</pre>
        </template>
        <p v-else>{{ getToolStatus(call, props.status ?? 'completed') }}</p>
      </template>
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
.retrieval-result { margin-top: 12px; }
.source-card { margin: 7px 0; padding: 9px 11px; border: 1px solid #e3e9e4; border-radius: 8px; background: #fff; }
.source-card summary { overflow-wrap: anywhere; }
.source-card .source-content { color: #55685a; font-size: 12px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }
.source-position { display: block; font-size: 10px; color: #9ba99f; }
.source-warning { color: #a37d51; }
.source-notice { color: #9ba49e; font-size: 10px; line-height: 1.8; }
.raw-tool { margin-top: 10px; padding-top: 9px; border-top: 1px solid #e7ebe8; }
.raw-tool > summary { font-size: 10px; color: #9ca79f; }
</style>
