<script setup lang="ts">
import type { DisplayMessage } from '../../types/chat'

defineProps<{ messages: DisplayMessage[] }>() // 接收父组件的消息列表，不在此处请求接口或修改消息
</script>

<template>
  <p v-if="messages.length === 0">还没有消息，发送一句话试试。</p>
  <div v-for="message in messages" :key="message.id" class="message">
    <strong>{{ message.role === 'user' ? '我' : '助手' }}：</strong>
    <span>{{ message.content }}</span>
    <small v-if="message.status === 'generating'">正在生成…</small>
    <small v-if="message.status === 'cancelled'">已停止</small>
    <small v-if="message.status === 'failed'">{{ message.error }}</small>
  </div>
</template>

<style scoped>
.message {
  margin-bottom: 16px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
