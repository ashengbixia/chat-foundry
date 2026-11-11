<script setup lang="ts">
import type { MessageAttachment } from '../../types/file'
import type { KnowledgeReference } from '../../api/knowledge'
import { onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ modelValue: string; inputDisabled: boolean; sendDisabled: boolean; sending: boolean; attachments?: MessageAttachment[]; uploadingAttachments?: boolean; knowledgeBases?: KnowledgeReference[]; knowledgeDisabled?: boolean; conversationFiles?: MessageAttachment[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; send: []; stop: []; openFiles: []; pickFiles: []; openKnowledge: []; removeKnowledge: [id: string]; removeConversationFile: [id: string]; pasteFiles: [files: File[]]; removeAttachment: [fileId: string] }>()
const textareaElement = ref<HTMLTextAreaElement | null>(null) // 输入框元素，用于按内容调整高度
const attachmentMenu = ref<HTMLElement | null>(null)
const menuOpen = ref(false)
function closeOutside(event: PointerEvent) { if (event.target instanceof Node && !attachmentMenu.value?.contains(event.target)) menuOpen.value = false }
onMounted(() => document.addEventListener('pointerdown', closeOutside))
onUnmounted(() => document.removeEventListener('pointerdown', closeOutside))
watch(() => props.inputDisabled || props.uploadingAttachments, (disabled) => { if (disabled) menuOpen.value = false })
function focus() { textareaElement.value?.focus({ preventScroll: true }) }
function handlePaste(event: ClipboardEvent) { // 文本照常粘贴，剪贴板中的文件走统一上传校验
  const files = Array.from(event.clipboardData?.files ?? [])
  if (!files.length) return
  event.preventDefault()
  if (!props.inputDisabled && !props.uploadingAttachments) emit('pasteFiles', files)
}
defineExpose({ focus })
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
    <details v-if="conversationFiles?.length" class="conversation-files" open><summary>会话资料 · {{ conversationFiles.length }} <small>后续提问持续使用，移出不删除原文件</small></summary><ul class="pending-attachments"><li v-for="file in conversationFiles" :key="file.file_id" :class="{ 'deleted-file': file.deleted }"><AppIcon name="file" :size="14" /><span :title="file.filename">{{ file.filename }}{{ file.deleted ? ' · 原文件已删除' : '' }}</span><button class="icon-button" type="button" :disabled="knowledgeDisabled" :aria-label="`从会话移出 ${file.filename}`" title="从会话移出，保留原文件和历史回答" @click="emit('removeConversationFile', file.file_id)"><AppIcon name="close" :size="13" /></button></li></ul></details>
    <ul v-if="knowledgeBases?.length" class="pending-attachments knowledge-tags" aria-label="当前会话的知识库">
      <li v-for="base in knowledgeBases" :key="base.id"><AppIcon name="folder" :size="15" /><button class="knowledge-label" type="button" :disabled="knowledgeDisabled" :title="`${base.name} · 持续用于当前会话`" @click="emit('openKnowledge')">知识库：{{ base.name }}</button><button class="icon-button" type="button" :disabled="knowledgeDisabled" :aria-label="`移除知识库 ${base.name}`" @click="emit('removeKnowledge', base.id)"><AppIcon name="close" :size="13" /></button></li>
    </ul>
    <ul v-if="attachments?.length" class="pending-attachments" aria-label="本次问题的附件">
      <li class="draft-label">待发送 · 发送后加入会话资料</li>
      <li v-for="file in attachments" :key="file.file_id">
        <AppIcon name="file" :size="15" /><span :title="file.filename">{{ file.filename }}</span>
        <button class="icon-button" type="button" :disabled="inputDisabled" :aria-label="`移除附件 ${file.filename}`" @click="removeAttachment(file.file_id)"><AppIcon name="close" :size="13" /></button>
      </li>
    </ul>
    <textarea ref="textareaElement" :value="modelValue" :disabled="inputDisabled" rows="2" aria-label="输入你的问题" placeholder="发送消息，或把资料拖到这里…" @input="handleInput" @keydown="handleKeydown" @paste="handlePaste"></textarea>
    <div class="composer-toolbar">
      <div ref="attachmentMenu" class="attachment-menu" @keydown.esc.stop="menuOpen = false">
        <button class="attach-button" type="button" :disabled="inputDisabled || uploadingAttachments" aria-label="添加附件" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><AppIcon name="plus" :size="17" /><span>{{ uploadingAttachments ? '上传中…' : '添加资料' }}</span></button>
        <div v-if="menuOpen" class="attachment-options">
          <button type="button" @click="menuOpen = false; emit('pickFiles')"><AppIcon name="plus" :size="16" /><span>从电脑上传<small>TXT 文件，最大 5 MB</small></span></button>
          <button type="button" @click="menuOpen = false; emit('openFiles')"><AppIcon name="folder" :size="16" /><span>选择已有资料<small>复用上传过的文件</small></span></button>
          <button type="button" :disabled="knowledgeDisabled" @click="menuOpen = false; emit('openKnowledge')"><AppIcon name="folder" :size="16" /><span>选择知识库<small>持续用于当前会话</small></span></button>
        </div>
      </div>
      <span class="keyboard-hint">Enter 发送 · Shift + Enter 换行</span>
      <slot name="model-picker"></slot>
      <button v-if="sending" class="send-button stop-button" type="button" aria-label="停止生成" title="停止生成" @click="emit('stop')"><AppIcon name="stop" :size="17" /></button>
      <button v-else class="send-button" type="submit" :disabled="sendDisabled" aria-label="发送消息" title="发送消息"><AppIcon name="arrow" :size="20" /></button>
    </div>
    <p v-if="uploadingAttachments" class="attachment-progress" role="status">正在上传资料，完成后会自动添加。你可以继续编辑问题。</p>
  </form>
</template>

<style scoped>
.composer { border: 1px solid #e0e3e7; border-radius: 20px; padding: 16px 16px 11px; background: #fff; box-shadow: 0 4px 24px #27352e06, 0 1px 3px #27352e05; }
.attachment-menu { position: relative; flex-shrink: 0; }
.attachment-options { position: absolute; left: 0; bottom: calc(100% + 12px); z-index: 16; width: 230px; padding: 6px; border: 1px solid #dce5df; border-radius: 12px; background: #fff; box-shadow: 0 12px 36px #233d2f20; }
.attachment-options button { display: flex; align-items: center; gap: 12px; width: 100%; border: 0; border-radius: 8px; padding: 12px; background: transparent; text-align: left; color: #526b5c; font-size: 13px; }
.attachment-options button:hover { background: #f0f6f2; }.attachment-options small { display: block; margin-top: 4px; font-size: 11px; color: #8b9991; }
.attachment-progress { margin: 8px 3px 0; color: #718a7b; font-size: 11px; }
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
.knowledge-tags li { background: #eef6f0; border-color: #d6e6db; }.knowledge-label { min-width: 0; padding: 0; border: 0; background: transparent; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; color: #52725e; font-size: 11px; }
.conversation-files { margin-bottom: 12px; }.conversation-files > summary { cursor: pointer; margin-bottom: 8px; font-size: 11px; color: #6d8274; }.conversation-files small { color: #93a097; font-size: 10px; margin-left: 7px; }.conversation-files ul { max-height: 100px; overflow-y: auto; margin: 0; }.pending-attachments .deleted-file { color: #a88676; background: #faf5f1; }.pending-attachments .draft-label { flex-basis: 100%; padding: 0; border: 0; background: transparent; font-size: 10px; color: #91a096; }
@media (max-width: 600px) { .keyboard-hint, .attach-button span { display: none; } .composer { padding: 14px 12px 10px; border-radius: 16px; } }
</style>
