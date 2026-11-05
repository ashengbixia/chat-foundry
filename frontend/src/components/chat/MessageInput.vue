<script setup lang="ts">
import type { MessageAttachment } from '../../types/file'

const props = defineProps<{ modelValue: string; inputDisabled: boolean; sendDisabled: boolean; sending: boolean; attachments?: MessageAttachment[] }>() // 接收正文、附件和禁用状态
const emit = defineEmits<{ 'update:modelValue': [value: string]; send: []; removeAttachment: [fileId: string] }>() // 将修改和发送意图交给父页面

function handleInput(event: Event) { // 把输入框中的最新文字交给父组件，不直接修改传入的数据
  if (props.inputDisabled) return
  const input = event.target as HTMLInputElement // 本次触发输入事件的输入框
  emit('update:modelValue', input.value)
}
function submitMessage() { // 通知父组件发送，实际请求仍由父组件执行
  if (props.inputDisabled || props.sendDisabled) return
  emit('send')
}
function removeAttachment(fileId: string) { // 移除待发送的附件，不删除文件
  if (props.inputDisabled) return
  emit('removeAttachment', fileId)
}
</script>

<template>
  <div>
    <ul v-if="attachments?.length" class="pending-attachments" aria-label="本次问题的附件">
      <li v-for="file in attachments" :key="file.file_id">
        {{ file.filename }} · {{ file.file_id.slice(0, 8) }}
        <button type="button" :disabled="inputDisabled" :aria-label="`移除附件 ${file.filename}`" @click="removeAttachment(file.file_id)">移除</button>
      </li>
    </ul>
    <form class="input-area" @submit.prevent="submitMessage">
      <input :value="modelValue" :disabled="inputDisabled" placeholder="输入你的问题" @input="handleInput" />
      <button type="submit" :disabled="sendDisabled">{{ sending ? '处理中…' : '发送' }}</button>
    </form>
  </div>
</template>

<style scoped>
.pending-attachments { padding-left: 20px; overflow-wrap: anywhere; }
.pending-attachments button { padding: 4px 8px; margin-left: 8px; }
.input-area {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}
input {
  flex: 1;
  min-width: 0;
  padding: 12px;
}
button {
  padding: 12px 20px;
}
</style>
