<script setup lang="ts">
import { computed } from 'vue'
import { formatToolValue, getRetrievalPreview, getToolCalls, getToolStatus, getSummaryCoverage, getSearchPreview, getNavigationPreview } from '../../utils/toolOutput'
import type { DisplayMessage } from '../../types/chat'
import { formatDuration } from '../../utils/messageProcess'
import AppIcon from '../AppIcon.vue'
import SubagentTasks from './SubagentTasks.vue'

const props = defineProps<{ output: unknown[]; status?: DisplayMessage['status']; chatId?: string }>()
const emit = defineEmits<{ updated: [chatId: string] }>()
const labels: Record<string, string> = { get_current_time: '查询当前时间', read_uploaded_file: '读取附件正文', search_uploaded_files: '检索附件内容', list_available_files: '查看会话资料清单', read_history_sources: '查阅历史原文快照', summarize_uploaded_files: '分段读取并归纳资料' }
const toolCalls = computed(() => getToolCalls(props.output).map((call) => ({ ...call, retrieval: call.name === 'search_uploaded_files' ? getRetrievalPreview(call.result) : null, search: call.name === 'search_web' ? getSearchPreview(call.result) : null, navigation: call.name === 'read_webpage' ? getNavigationPreview(call.result) : null }))) // 消息变化时配对结果并解析来源卡片
function brief(argumentsText: string): string {
  try { const value = JSON.parse(argumentsText); return value.task ?? value.query ?? (value.url ? new URL(value.url).hostname : '') ?? '' } catch { return '' }
}
function taskCallId(result: string | null): string {
  try { const value = JSON.parse(result ?? '{}'); return typeof value.call_id === 'string' ? value.call_id : '' } catch { return '' }
}
</script>

<template>
  <div v-if="toolCalls.length" class="tool-call-list">
    <details v-for="(call, index) in toolCalls" :key="`${call.callId}-${index}`" class="tool-call">
      <summary>
        <AppIcon name="chevron" :size="11" />
        <span>{{ call.displayName ?? labels[call.name] ?? call.name }}</span>
        <span class="tool-brief" :title="brief(call.arguments)">{{ brief(call.arguments) }}</span>
        <small>{{ getToolStatus(call, props.status ?? 'completed') }}</small>
        <small v-if="call.retrieval">{{ call.retrieval.status === 'unavailable' ? '索引不可用' : `${call.retrieval.chunks.length} 个片段` }}</small>
        <small v-if="call.name === 'summarize_uploaded_files'">{{ getSummaryCoverage(call.result) }}</small>
        <small v-if="call.search">{{ call.search.results.length }} 条候选</small>
        <small v-if="call.durationMs !== undefined" class="tool-duration">{{ formatDuration(call.durationMs) }}</small>
      </summary>
      <SubagentTasks v-if="chatId && (call.name === 'delegate_task' || (call.name === 'get_subagent_task' && taskCallId(call.result)))" :chat-id="chatId" :call-id="taskCallId(call.result) || call.callId" :generating="status === 'generating'" @updated="emit('updated', $event)" />
      <div v-if="call.navigation" class="navigation-links">
        <p class="source-notice">{{ call.navigation.notice }}</p>
        <a v-for="link in call.navigation.links" :key="link.url" :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.title }} ↗</a>
      </div>
      <div v-if="call.search" class="web-search-results">
        <p class="search-query">{{ call.search.query }} <small>· {{ call.search.provider }}</small></p>
        <p class="source-notice">搜索摘要 · 尚未读取网页原文</p>
        <p v-if="!call.search.results.length">本次没有找到可用的搜索候选。</p>
        <article v-for="item in call.search.results" :key="item.url" class="web-search-card"><small>{{ item.host }}</small><a :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title }} ↗</a><p>{{ item.snippet }}</p></article>
        <p v-if="call.search.notice" class="source-notice">{{ call.search.notice }}</p>
      </div>
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
      <details v-if="call.retrieval || call.search || call.navigation || ['delegate_task', 'get_subagent_task'].includes(call.name)" class="raw-tool">
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
.tool-call-list { margin: 0 0 9px; white-space: normal; }
.navigation-links a { display: inline-block; margin: 3px 14px 3px 0; color: #527761; font-size: 12px; }
.web-search-results { margin-top: 12px; }.search-query { font-size: 12px; color: #555; }.search-query small { color: #999; }.web-search-card { margin: 9px 0; padding: 12px; border: 1px solid #e8ece9; border-radius: 9px; background: white; }.web-search-card > small { display: block; color: #999; font-size: 10px; }.web-search-card a { display: inline-block; margin-top: 5px; color: #456454; font-size: 13px; text-decoration: none; overflow-wrap: anywhere; }.web-search-card a:hover { text-decoration: underline; }.web-search-card p { margin: 6px 0 0; font-size: 12px; line-height: 1.7; color: #78827b; white-space: pre-wrap; }
.tool-call { padding: 4px 0; margin-bottom: 2px; border: 0; background: transparent; }
.tool-call > summary { display: flex; align-items: center; gap: 7px; list-style: none; min-height: 22px; }
.tool-call > summary::-webkit-details-marker { display: none; }
.tool-call[open] > summary > svg { transform: rotate(90deg); }
.tool-call > summary > span:not(.tool-brief) { flex-shrink: 0; }
.tool-brief { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px; color: #9ba1a8; font-size: 11px; min-width: 0; }
.tool-call > summary small { margin-left: 0; flex-shrink: 0; }
.tool-duration { margin-left: auto !important; }
.tool-call[open] { padding-bottom: 10px; }
@media (max-width: 600px) { .tool-brief { max-width: 110px; }.tool-call > summary small { font-size: 9px; }.tool-duration { display: none; } }
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
