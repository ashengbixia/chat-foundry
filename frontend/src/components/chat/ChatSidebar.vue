<script setup lang="ts">
import type { Chat } from '../../api/chats'

defineProps<{ chats: Chat[]; currentChatId: string | null; loading: boolean; creating: boolean; deletingChatId: string | null; renamingChatId: string | null; actionDisabled: boolean; openDisabled: boolean }>() // 接收会话列表、当前会话和按钮状态
const emit = defineEmits<{ create: []; open: [chat: Chat]; remove: [chat: Chat]; rename: [chat: Chat] }>() // 只通知父组件操作意图，不在此处请求后端
</script>

<template>
  <button type="button" :disabled="actionDisabled" @click="emit('create')">{{ creating ? '创建中…' : '新建会话' }}</button>
  <section>
    <h2>会话列表</h2>
    <p v-if="loading">正在读取会话…</p>
    <p v-else-if="chats.length === 0">暂无会话</p>
    <ul v-else>
      <li v-for="chat in chats" :key="chat.id">
        <button type="button" :disabled="openDisabled" @click="emit('open', chat)">
          {{ currentChatId === chat.id ? '当前：' : '' }}
          {{ chat.title }}
        </button>
        <button type="button" :disabled="actionDisabled" @click="emit('remove', chat)">{{ deletingChatId === chat.id ? '删除中…' : '删除' }}</button>
        <button type="button" :disabled="actionDisabled" @click="emit('rename', chat)">{{ renamingChatId === chat.id ? '保存中…' : '重命名' }}</button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
button {
  padding: 12px 20px;
}
</style>
