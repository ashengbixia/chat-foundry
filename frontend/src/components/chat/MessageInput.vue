<script setup lang="ts">
import type { MessageAttachment } from '../../types/file'
import { onMounted, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ modelValue: string; inputDisabled: boolean; sendDisabled: boolean; sending: boolean; attachments?: MessageAttachment[]; uploadingAttachments?: boolean }>() // 接收正文、附件和禁用状态
const emit = defineEmits<{ 'update:modelValue': [value: string]; send: []; stop: []; openFiles: []; pickFiles: []; removeAttachment: [fileId: string] }>() // 文件按钮直接选择上传，不必绕道管理面板
const textareaElement = ref<HTMLTextAreaElement | null>(null) // 输入框元素，用于按内容调整高度
function resizeInput() { // 高度最多180像素，超出后在输入框内滚动
  const element = textareaElement.value
  if (!element) return
  element.style.height = 'auto'
  element.style.height = `${Math.min(element.scrollHeight, 180)}px`
}
onMounted(resizeInput)
watch(() => props.modelValue, resizeInput, { flush: 'post' }) // 清空草稿或外部修改正文后同步高度
function handleKeydown(event: KeyboardEvent) { // Enter发送，Shift+Enter换行；中文选词期间不发送
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  submitMessage()
}

function handleInput(event: Event) { // 把输入框中的最新文字交给父组件，不直接修改传入的数据
  if (props.inputDisabled) return
  const input = event.target as HTMLTextAreaElement // 本次触发输入事件的多行输入框
  emit('update:modelValue', input.value)
  resizeInput()
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
  <form class="composer" @submit.prevent="submitMessage">
    <ul v-if="attachments?.length" class="pending-attachments" aria-label="本次问题的附件">
      <li v-for="file in attachments" :key="file.file_id">
        <AppIcon name="file" :size="15" /><span :title="file.filename">{{ file.filename }}</span>
        <button class="icon-button" type="button" :disabled="inputDisabled" :aria-label="`移除附件 ${file.filename}`" @click="removeAttachment(file.file_id)"><AppIcon name="close" :size="13" /></button>
      </li>
    </ul>
    <textarea ref="textareaElement" :value="modelValue" :disabled="inputDisabled" rows="2" aria-label="输入你的问题" placeholder="有什么想一起探索的？" @input="handleInput" @keydown="handleKeydown"></textarea>
    <div class="composer-toolbar">
      <button class="attach-button" type="button" :disabled="inputDisabled || uploadingAttachments" aria-label="选择文件并添加到问题" @click="emit('pickFiles')"><AppIcon name="plus" :size="17" /><span>{{ uploadingAttachments ? '上传中…' : '添加文件' }}</span></button>
      <span class="keyboard-hint">Enter 发送 · Shift + Enter 换行</span>
      <slot name="model-picker"></slot>
      <button v-if="sending" class="send-button stop-button" type="button" aria-label="停止生成" title="停止生成" @click="emit('stop')"><AppIcon name="stop" :size="17" /></button>
      <button v-else class="send-button" type="submit" :disabled="sendDisabled" aria-label="发送消息" title="发送消息"><AppIcon name="arrow" :size="20" /></button>
    </div>
  </form>
</template>

<style scoped>
.composer { border: 1px solid #e0e3e7; border-radius: 20px; padding: 16px 16px 11px; background: #fff; box-shadow: 0 4px 24px #27352e06, 0 1px 3px #27352e05; }
.composer:focus-within { border-color: #abc8bc; box-shadow: 0 0 0 3px #267d6906; }
textarea { display: block; width: 100%; min-height: 54px; max-height: 180px; padding: 0 3px; border: 0; outline: 0; background: transparent; resize: none; line-height: 1.8; font-size: 14px; color: #30353b; }
textarea:focus-visible { outline: 0; }
textarea::placeholder { color: #a0a5ad; }
textarea:disabled { opacity: .65; }
.composer-toolbar { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.attach-button { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border: 0; border-radius: 7px; background: transparent; font-size: 11px; color: #777f88; }
.attach-button:hover:not(:disabled) { background: var(--surface); }
.keyboard-hint { margin-left: auto; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 10px; color: #a1a5ad; }
.composer-toolbar :deep(.composer-model-picker) { margin-left: auto; }
.send-button { display: grid; place-items: center; width: 34px; height: 34px; flex-shrink: 0; border: 0; border-radius: 11px; background: #263b32; color: #fff; }
.send-button:hover:not(:disabled) { background: #267d69; }
.send-button:disabled { background: #e7ebe8; color: #9fa9a3; opacity: 1; }
.stop-button { background: #30383f; }
.pending-attachments { display: flex; flex-wrap: wrap; gap: 7px; list-style: none; margin: 0 0 12px; padding: 0; }
.pending-attachments li { display: flex; align-items: center; gap: 6px; max-width: 100%; padding: 5px 6px 5px 9px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; font-size: 11px; color: #66716a; }
.pending-attachments li > span { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.pending-attachments .icon-button { width: 22px; height: 22px; }
@media (max-width: 600px) { .keyboard-hint, .attach-button span { display: none; } .composer { padding: 14px 12px 10px; border-radius: 16px; } }
</style>
