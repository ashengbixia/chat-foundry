<script setup lang="ts">
const props = defineProps<{ modelValue: string; inputDisabled: boolean; sendDisabled: boolean; sending: boolean }>() // 接收输入内容、禁用状态和发送状态
const emit = defineEmits<{ 'update:modelValue': [value: string]; send: [] }>() // 向父组件报告文字变化和发送操作

function handleInput(event: Event) { // 把输入框中的最新文字交给父组件，不直接修改传入的数据
  if (props.inputDisabled) return
  const input = event.target as HTMLInputElement // 本次触发输入事件的输入框
  emit('update:modelValue', input.value)
}
function submitMessage() { // 通知父组件发送，实际请求仍由父组件执行
  if (props.inputDisabled || props.sendDisabled) return
  emit('send')
}
</script>

<template>
  <form class="input-area" @submit.prevent="submitMessage">
    <input :value="modelValue" :disabled="inputDisabled" placeholder="输入你的问题" @input="handleInput" />
    <button type="submit" :disabled="sendDisabled">{{ sending ? '处理中…' : '发送' }}</button>
  </form>
</template>

<style scoped>
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
