<script setup lang="ts">
import type { FileInfo, MessageAttachment } from '../../types/file'
import type { KnowledgeBase, KnowledgeReference } from '../../api/knowledge'
import { onMounted, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import ComposerResourceMenu from './ComposerResourceMenu.vue'

const props = defineProps<{ modelValue: string; inputDisabled: boolean; sendDisabled: boolean; sending: boolean; attachments?: MessageAttachment[]; uploadingAttachments?: boolean; knowledgeBases?: KnowledgeReference[]; knowledgeDisabled?: boolean; conversationFiles?: MessageAttachment[]; webSearchEnabled?: boolean; webSearchDisabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; send: []; stop: []; openFiles: []; pickFiles: []; openKnowledge: []; manageKnowledge: []; attachSaved: [file: FileInfo]; toggleKnowledge: [base: KnowledgeBase]; addWebLink: [url: string]; toggleWebSearch: []; removeKnowledge: [id: string]; removeConversationFile: [id: string]; pasteFiles: [files: File[]]; removeAttachment: [fileId: string] }>()
const textareaElement = ref<HTMLTextAreaElement | null>(null) // 输入框元素，用于按内容调整高度
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
    <textarea ref="textareaElement" :value="modelValue" :disabled="inputDisabled" rows="2" aria-label="输入你的问题" placeholder="有什么我能帮您的吗？" @input="handleInput" @keydown="handleKeydown" @paste="handlePaste"></textarea>
    <div class="composer-toolbar">
      <ComposerResourceMenu :disabled="inputDisabled" :uploading="uploadingAttachments" :knowledge-disabled="knowledgeDisabled" :attachments="attachments" :conversation-files="conversationFiles" :selected-knowledge="knowledgeBases" @upload="emit('pickFiles')" @attach="emit('attachSaved', $event)" @toggle-knowledge="emit('toggleKnowledge', $event)" @manage-files="emit('openFiles')" @manage-knowledge="emit('manageKnowledge')" @web-link="emit('addWebLink', $event)" :web-search-enabled="webSearchEnabled" :web-search-disabled="webSearchDisabled" @toggle-web-search="emit('toggleWebSearch')" />
      <button v-if="webSearchEnabled" class="web-search-chip" type="button" :disabled="webSearchDisabled" aria-label="关闭联网搜索" title="联网搜索已开启，点击关闭" @click="emit('toggleWebSearch')"><AppIcon name="globe" :size="14" /><span>联网搜索</span><AppIcon name="close" :size="12" /></button>
      <span class="keyboard-hint">Enter 发送 · Shift + Enter 换行</span>
      <slot name="model-picker"></slot>
      <button v-if="sending" class="send-button stop-button" type="button" aria-label="停止生成" title="停止生成" @click="emit('stop')"><AppIcon name="stop" :size="17" /></button>
      <button v-else class="send-button" type="submit" :disabled="sendDisabled" aria-label="发送消息" title="发送消息"><AppIcon name="arrow" :size="20" /></button>
    </div>
    <p v-if="uploadingAttachments" class="attachment-progress" role="status">正在上传资料，完成后会自动添加。你可以继续编辑问题。</p>
  </form>
</template>

<style scoped>
.composer { border: 1px solid #e0e3e7; border-radius: 24px; padding: 17px 16px 11px; background: #fff; box-shadow: 0 5px 20px #00000006, 0 1px 3px #00000004; }
.attachment-progress { margin: 8px 3px 0; color: #718a7b; font-size: 11px; }
.composer:focus-within { border-color: #d2d2d2; box-shadow: 0 5px 22px #00000008; }
textarea { display: block; width: 100%; min-height: 54px; max-height: 180px; padding: 0 3px; border: 0; outline: 0; background: transparent; resize: none; line-height: 1.8; font-size: 16px; color: #30353b; }
textarea:focus-visible { outline: 0; }
textarea::placeholder { color: #8d8d8d; }
textarea:disabled { opacity: .65; }
.composer-toolbar { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.attach-button { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border: 0; border-radius: 7px; background: transparent; font-size: 11px; color: #777f88; }
.attach-button:hover:not(:disabled) { background: var(--surface); }
.web-search-chip { display: flex; align-items: center; gap: 5px; flex-shrink: 0; padding: 6px 8px; border: 1px solid #d8e5df; border-radius: 16px; background: #eef5f1; color: #456d59; font-size: 11px; }.web-search-chip:hover:not(:disabled) { background: #e3eee7; }
.keyboard-hint { margin-left: auto; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 10px; color: #a1a5ad; }
.composer-toolbar :deep(.composer-model-picker) { margin-left: auto; }
.send-button { display: grid; place-items: center; width: 34px; height: 34px; flex-shrink: 0; border: 0; border-radius: 11px; background: #242424; color: #fff; }
.send-button:hover:not(:disabled) { background: #444; }
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
