<script setup lang="ts">
import type { DisplayMessage } from '../../types/chat'
import ToolCallList from './ToolCallList.vue'

defineProps<{ messages: DisplayMessage[] }>() // 接收父组件的消息列表，不在此处请求接口或修改消息
</script>

<template>
  <p v-if="messages.length === 0">还没有消息，发送一句话试试。</p>
  <div v-for="message in messages" :key="message.id" class="message">
    <strong>{{ message.role === 'user' ? '我' : '助手' }}：</strong>
    <ToolCallList v-if="message.role === 'assistant'" :output="message.output" :status="message.status" />
    <span>{{ message.content }}</span>
    <ul v-if="message.attachments?.length" class="message-attachments" aria-label="消息附件">
      <li v-for="file in message.attachments" :key="file.file_id">附件：{{ file.filename }} · {{ file.size }}字节 · {{ file.file_id.slice(0, 8) }} <span v-if="file.deleted" class="deleted-file">（文件已删除）</span></li>
    </ul>
    <small v-if="message.status === 'generating'">正在生成…</small>
    <small v-if="message.status === 'cancelled'">已停止</small>
    <small v-if="message.status === 'failed'">{{ message.error }}</small>
  </div>
</template>

<style scoped>
.message-attachments { padding-left: 20px; white-space: normal; font-size: 14px; color: #666; }
.deleted-file { color: #b91c1c; }
.message {
  margin-bottom: 16px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
