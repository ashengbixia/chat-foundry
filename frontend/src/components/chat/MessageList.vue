<script setup lang="ts">
import type { DisplayMessage } from '../../types/chat'
import { ref } from 'vue'
import MessageProcess from './MessageProcess.vue'
import { answerContent, messageClock, isTaskUpdate } from '../../utils/messageProcess'
import AppIcon from '../AppIcon.vue'
import MessageAnswer from './MessageAnswer.vue'

defineProps<{ messages: DisplayMessage[]; hasChat?: boolean }>() // 接收消息和会话状态，不在此处请求接口或修改消息
const emit = defineEmits<{ updated: [chatId: string] }>()
const copied = ref('')
const copyError = ref('')
async function copyAnswer(message: DisplayMessage) {
  try { await navigator.clipboard.writeText(answerContent(message)); copied.value = message.id; copyError.value = '' }
  catch { copyError.value = message.id }
}
</script>

<template>
  <div v-if="messages.length === 0" class="welcome">
    <div class="welcome-symbol"><AppIcon name="spark" :size="30" /></div>
    <p class="welcome-eyebrow">一点好奇，无限可能</p>
    <h1>从一个想法开始。</h1>
    <p class="welcome-description">提个问题，带来一份资料，或一起理清思路。<br />让复杂的事情，变得清楚一点。</p>
    <div class="capabilities">
      <div><AppIcon name="chat" :size="19" /><strong>一起想清楚</strong><span>拆解问题，探索新的思路</span></div>
      <div><AppIcon name="file" :size="19" /><strong>读懂你的资料</strong><span>添加文本附件，围绕内容交流</span></div>
      <div><AppIcon name="spark" :size="19" /><strong>让工具来帮忙</strong><span>按需调用，过程清晰可见</span></div>
    </div>
    <small>{{ hasChat ? '在下方输入问题，开始这段对话' : '直接输入问题即可开始新对话，也可以先添加资料' }}</small>
  </div>
  <article v-for="message in messages" :key="message.id" class="message" :class="message.role === 'user' ? 'user-message' : 'assistant-message'" :aria-label="message.role === 'user' ? '我的消息' : '助手消息'">
    <div v-if="message.role === 'assistant'" class="assistant-heading"><span class="assistant-mark"><AppIcon name="spark" :size="16" /></span><strong>ChatFoundry</strong><span>{{ isTaskUpdate(message) ? '子任务更新' : '助手' }}</span></div>
    <div class="message-body">
    <MessageProcess v-if="message.role === 'assistant'" :message="message" @updated="emit('updated', $event)" />
    <MessageAnswer v-if="answerContent(message) && message.role === 'assistant'" class="message-text markdown-body" :content="answerContent(message)" :output="message.output" />
    <div v-else-if="message.role === 'user' && message.content" class="message-text">{{ message.content }}</div>
    <ul v-if="message.attachments?.length" class="message-attachments" aria-label="消息附件">
      <li v-for="file in message.attachments" :key="file.file_id" :title="file.file_id"><AppIcon name="file" :size="16" /><span>{{ file.filename }}<small>{{ file.size }} 字节</small></span><span v-if="file.deleted" class="deleted-file">（文件已删除）</span></li>
    </ul>
    <small v-if="message.status === 'generating'" class="message-status" role="status"><span class="activity-dot"></span>正在生成…</small>
    <small v-if="message.status === 'cancelled'" class="message-status">已停止</small>
    <small v-if="message.status === 'failed'" class="message-status failed" role="alert">{{ message.error }}</small>
    <footer v-if="message.status !== 'generating'" class="message-footer">
      <button v-if="message.role === 'assistant' && answerContent(message)" type="button" :aria-label="copied === message.id ? '已复制回答' : '复制回答'" :title="copied === message.id ? '已复制' : '复制回答'" @click="copyAnswer(message)"><AppIcon :name="copied === message.id ? 'check' : 'copy'" :size="15" /></button>
      <span v-if="copyError === message.id" role="status">复制失败，请手动选择文字</span>
      <time v-if="message.created_at" :datetime="new Date(message.created_at * 1000).toISOString()" :title="`${new Date(message.created_at * 1000).toLocaleString()}${message.model ? ` · ${message.model}` : ''}`">{{ messageClock(message.created_at) }}</time>
    </footer>
    </div>
  </article>
</template>

<style scoped>
.welcome { padding: clamp(36px, 10vh, 110px) 0 32px; text-align: center; }
.welcome-symbol { display: grid; place-items: center; width: 62px; height: 62px; margin: 0 auto 24px; border: 1px solid #dde9e1; border-radius: 22px; background: #f2f7f3; color: #527761; box-shadow: 0 5px 18px #354f3d05; }
.welcome-eyebrow { margin: 0 0 12px; font-size: 11px; letter-spacing: .2em; color: #8b948e; }
.welcome h1 { margin: 0; font-size: clamp(27px, 3vw, 36px); font-weight: 550; letter-spacing: -.04em; color: #2d3832; }
.welcome-description { margin: 17px 0 32px; font-size: 13px; line-height: 1.95; color: #8b9298; }
.capabilities { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; max-width: 660px; margin: 0 auto 25px; text-align: left; }
.capabilities > div { padding: 19px 17px; border: 1px solid #edeef0; border-radius: 13px; background: #fdfdfd; }
.capabilities svg { display: block; margin-bottom: 16px; color: #688272; }
.capabilities strong { display: block; margin-bottom: 7px; font-size: 12px; font-weight: 500; }
.capabilities span, .welcome > small { font-size: 10px; line-height: 1.7; color: #959ba2; }
.message { margin: 0 0 32px; overflow-wrap: anywhere; }
.user-message { display: flex; justify-content: flex-end; padding-left: 15%; }
.user-message .message-body { max-width: 100%; padding: 12px 18px; border-radius: 17px 17px 5px 17px; background: #f1f3f5; color: #333b43; }
.assistant-heading { display: flex; align-items: center; gap: 8px; margin-bottom: 13px; font-size: 11px; }
.assistant-heading strong { font-weight: 600; }
.assistant-heading > span:last-child { color: #a1a6ad; font-size: 10px; }
.assistant-mark { display: grid; place-items: center; width: 25px; height: 25px; background: #edf4ef; color: #47715b; border-radius: 8px; }
.message-text { white-space: pre-wrap; font-size: 15px; line-height: 1.95; }
.markdown-body { white-space: normal; }
.markdown-body :deep(p) { margin: 0 0 16px; }
.markdown-body :deep(> :last-child) { margin-bottom: 0; }
.markdown-body :deep(h1), .markdown-body :deep(h2), .markdown-body :deep(h3), .markdown-body :deep(h4) { margin: 22px 0 12px; font-weight: 600; line-height: 1.5; }
.markdown-body :deep(h1) { font-size: 24px; }
.markdown-body :deep(h2) { font-size: 21px; }
.markdown-body :deep(h3) { font-size: 18px; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 24px; margin: 12px 0 18px; }
.markdown-body :deep(li) { margin: 5px 0; }
.markdown-body :deep(code) { padding: 2px 5px; border-radius: 4px; background: #f0f3f1; color: #4a6755; font-family: Consolas, monospace; font-size: .86em; }
.markdown-body :deep(pre) { max-width: 100%; overflow-x: auto; margin: 16px 0; padding: 18px; border: 1px solid #e8ece9; border-radius: 11px; background: #f6f8f7; white-space: pre; line-height: 1.7; }
.markdown-body :deep(pre code) { padding: 0; background: transparent; color: #4c5851; font-size: 12px; }
.markdown-body :deep(blockquote) { margin: 16px 0; padding: 3px 0 3px 16px; border-left: 3px solid #c3d6c8; color: #89968d; }
.markdown-body :deep(blockquote p) { margin: 0; }
.markdown-body :deep(a) { color: #267d69; text-underline-offset: 3px; }
.markdown-body :deep(hr) { margin: 24px 0; border: 0; border-top: 1px solid var(--border); }
.markdown-body :deep(.table-wrap) { max-width: 100%; overflow-x: auto; margin: 16px 0; }
.markdown-body :deep(table) { width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }
.markdown-body :deep(th), .markdown-body :deep(td) { padding: 10px 13px; border: 1px solid #e6ebe7; text-align: left; }
.markdown-body :deep(th) { background: #f3f6f3; font-weight: 500; }
.markdown-body :deep(.markdown-image) { color: #939d96; font-size: 12px; }
.assistant-message .message-body { padding-left: 33px; }
.message-attachments { display: flex; flex-wrap: wrap; gap: 8px; list-style: none; margin: 12px 0 0; padding: 0; }
.message-attachments li { display: flex; align-items: center; gap: 8px; padding: 9px 11px; border: 1px solid #e4e8e5; border-radius: 9px; background: #fff; font-size: 11px; color: #637568; }
.message-attachments small { display: block; font-size: 10px; color: #9a9fa5; margin-top: 3px; }
.deleted-file { color: #b36767; }
.message-status { display: flex; align-items: center; gap: 7px; margin-top: 10px; color: #9299a0; font-size: 11px; }
.activity-dot { width: 6px; height: 6px; border-radius: 50%; background: #6c9780; animation: breathe 1.5s ease-in-out infinite; }
.failed { color: #b45353; }
.message-footer { display: flex; align-items: center; gap: 12px; margin-top: 12px; color: #a0a5ab; font-size: 11px; min-height: 24px; }
.message-footer button { display: inline-flex; padding: 3px; border: 0; background: transparent; color: #979da4; border-radius: 4px; cursor: pointer; }.message-footer button:hover { color: #45594c; background: #f2f5f3; }
.user-message .message-footer { justify-content: flex-end; margin-top: 5px; }
@keyframes breathe { 50% { opacity: .3; } }
@media (max-width: 600px) { .welcome { padding-top: 36px; } .capabilities { gap: 7px; } .capabilities > div { padding: 13px 9px; } .capabilities span { display: none; } .capabilities svg { margin-bottom: 12px; } .capabilities strong { font-size: 11px; } .assistant-message .message-body { padding-left: 0; } }
</style>
