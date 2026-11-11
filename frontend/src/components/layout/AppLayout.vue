<script setup lang="ts">
import { ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ filesOpen: boolean; currentChatId?: string | null; dropDisabled?: boolean }>() // 文件拖放与聊天共用忙碌状态
const emit = defineEmits<{ closeFiles: []; openSettings: []; openKnowledge: []; droppedFiles: [files: File[]]; attachSaved: [fileId: string] }>()
const sidebarOpen = ref(false) // 窄屏下是否展开侧栏
const dragDepth = ref(0)
function isFileDrag(event: DragEvent) { return Array.from(event.dataTransfer?.types ?? []).some((type) => type === 'Files' || type === 'application/x-chatfoundry-file') }
function dragEnter(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  dragDepth.value++
}
function dragOver(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = props.dropDisabled ? 'none' : 'copy'
}
function dragLeave() { dragDepth.value = Math.max(0, dragDepth.value - 1) }
function drop(event: DragEvent) { // 拦截文件导航，历史资料只传ID，本地文件交给统一上传入口
  dragDepth.value = 0
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (props.dropDisabled || !event.dataTransfer) return
  const fileId = event.dataTransfer.getData('application/x-chatfoundry-file')
  if (fileId) emit('attachSaved', fileId)
  else if (event.dataTransfer.files.length) emit('droppedFiles', Array.from(event.dataTransfer.files))
}
watch(() => props.currentChatId, () => { sidebarOpen.value = false }) // 成功新建或切换会话后收起窄屏导航
</script>

<template>
  <div class="app-layout" @keydown.esc="sidebarOpen = false; emit('closeFiles')" @dragend="dragDepth = 0">
    <button v-if="sidebarOpen" class="sidebar-mask" type="button" aria-label="关闭侧栏" @click="sidebarOpen = false"></button>
    <aside id="chat-sidebar" class="sidebar" :class="{ 'is-open': sidebarOpen }" aria-label="会话导航">
      <div class="brand">
        <span class="brand-symbol"><AppIcon name="spark" :size="21" /></span>
        <strong>ChatFoundry<small>你的思考工作空间</small></strong>
        <button class="icon-button mobile-button" type="button" aria-label="关闭侧栏" @click="sidebarOpen = false"><AppIcon name="close" /></button>
      </div>
      <div class="sidebar-content">
        <button class="knowledge-nav" type="button" @click="sidebarOpen = false; emit('openKnowledge')"><AppIcon name="folder" :size="17" /><span>知识库<small>整理资料，持续问答</small></span><AppIcon name="chevron" :size="13" /></button>
        <slot name="sidebar"></slot>
      </div>
      <footer class="sidebar-footer">
        <button class="settings-button" type="button" @click="sidebarOpen = false; emit('openSettings')"><AppIcon name="settings" :size="16" />设置<span>模型服务</span></button>
        <slot name="account"><div class="user-info">
          <span class="avatar" aria-hidden="true">U</span>
          <div>
            <strong>本地用户</strong>
            <small>个人工作空间</small>
          </div>
        </div></slot>
      </footer>
    </aside>
    <main class="workspace">
      <header class="chat-header">
        <button class="icon-button mobile-button" type="button" aria-label="展开侧栏" aria-controls="chat-sidebar" :aria-expanded="sidebarOpen" @click="sidebarOpen = true"><AppIcon name="panel" /></button>
        <slot name="header"></slot>
      </header>
      <div class="workspace-body">
        <section class="chat-content" aria-label="聊天" @dragenter="dragEnter" @dragover="dragOver" @dragleave="dragLeave" @drop="drop" @dragend="dragDepth = 0">
          <div v-if="dragDepth" class="drop-overlay" role="status"><strong>{{ dropDisabled ? '请等待当前操作完成' : '松开，把资料带进对话' }}</strong><span>已有资料直接引用 · 本地 TXT 自动上传 · 最多 3 份</span></div>
          <div class="message-region">
            <slot name="messages"></slot>
          </div>
          <footer class="composer-region">
            <slot name="composer"></slot>
          </footer>
        </section>
        <button v-if="filesOpen" class="files-mask" type="button" aria-label="关闭资料空间" @click="emit('closeFiles')"></button>
        <aside v-show="filesOpen" id="file-manager" class="files-region" aria-label="文件管理">
          <div class="files-heading">
            <div><AppIcon name="folder" :size="17" /><strong>资料空间</strong></div>
            <button class="icon-button" type="button" aria-label="关闭文件管理" @click="emit('closeFiles')"><AppIcon name="close" :size="16" /></button>
          </div>
          <div class="files-content">
            <slot name="files"></slot>
          </div>
        </aside>
      </div>
    </main>
  </div>
</template>

<style scoped>
.app-layout { display: flex; height: 100vh; height: 100dvh; overflow: hidden; background: #fff; }
.sidebar { width: 248px; flex-shrink: 0; display: flex; flex-direction: column; background: #f6f7f8; border-right: 1px solid #eeeff1; }
.brand { height: 92px; flex-shrink: 0; padding: 0 21px; display: flex; align-items: center; gap: 10px; }
.brand-symbol { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 11px; background: #2e4438; color: #e6efe8; }
.brand > strong { font-size: 15px; letter-spacing: -.4px; font-weight: 650; }
.brand small { display: block; font-size: 9px; font-weight: 400; letter-spacing: .04em; color: #989eaa; margin-top: 4px; }
.brand .mobile-button { margin-left: auto; }
.sidebar-content { flex: 1; min-height: 0; overflow-y: auto; padding: 0 13px; }
.knowledge-nav { display: flex; align-items: center; gap: 10px; width: 100%; margin-bottom: 16px; padding: 12px; border: 1px solid #e4e9e6; border-radius: 10px; background: #fff; text-align: left; color: #617768; font-size: 13px; }.knowledge-nav span { flex: 1; }.knowledge-nav small { display: block; margin-top: 4px; font-size: 10px; color: #91a096; }.knowledge-nav:hover { background: #f0f6f2; }
.sidebar-footer { padding: 12px 16px 18px; border-top: 1px solid #e9ebee; }
.settings-button { display: flex; align-items: center; gap: 9px; width: 100%; padding: 8px 7px; text-align: left; border: 0; border-radius: 8px; background: transparent; font-size: 12px; opacity: .65; }
.settings-button span { margin-left: auto; font-size: 9px; color: #a0a5ad; }
.user-info { display: flex; align-items: center; gap: 10px; margin-top: 13px; padding: 7px; }
.user-info strong { font-size: 12px; font-weight: 500; }
.user-info strong, .user-info small { display: block; }
.user-info small { margin-top: 4px; color: #989fa8; font-size: 10px; }
.avatar { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 50%; background: #e4e9e4; color: #667d6c; font-size: 12px; }
.workspace { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.chat-header { min-height: 68px; flex-shrink: 0; display: flex; align-items: center; gap: 12px; padding: 12px 30px; border-bottom: 1px solid #f1f2f4; }
.workspace-body { flex: 1; min-height: 0; display: flex; }
.chat-content { position: relative; flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.drop-overlay { pointer-events: none; position: absolute; inset: 12px; z-index: 18; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 12px; padding: 24px; border: 2px dashed #79a98f; border-radius: 20px; background: #f1f9f5f5; color: #365c45; text-align: center; }
.drop-overlay strong { font-size: 20px; }.drop-overlay span { font-size: 12px; color: #718a7b; }
.files-mask { display: none; }
.message-region { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.composer-region { flex-shrink: 0; padding: 12px 36px 16px; }
.files-region { width: 334px; max-width: 40%; min-width: 0; flex-shrink: 0; display: flex; flex-direction: column; border-left: 1px solid var(--border); background: #fcfcfd; }
.files-heading { display: flex; align-items: center; justify-content: space-between; padding: 17px 19px 7px; }
.files-heading > div { display: flex; align-items: center; gap: 8px; color: #6a756e; }
.files-heading strong { color: #424b44; font-size: 13px; font-weight: 500; }
.files-content { flex: 1; min-height: 0; overflow-y: auto; padding: 0 18px 20px; overflow-wrap: anywhere; }
.mobile-button, .sidebar-mask { display: none; }
@media (max-width: 900px) {
  .mobile-button { display: inline-flex; }
  .sidebar { position: fixed; inset: 0 auto 0 0; z-index: 30; transform: translateX(-100%); visibility: hidden; }
  .sidebar.is-open { transform: translateX(0); visibility: visible; }
  .sidebar-mask { display: block; position: fixed; inset: 0; z-index: 20; border: 0; background: #0006; }
  .chat-header { padding: 12px 16px; }
  .composer-region { padding: 12px 16px; }
}
@media (max-width: 600px) {
  .files-mask { display: block; position: fixed; inset: 0; z-index: 35; border: 0; background: #15291e66; }
  .files-region { position: fixed; inset: 0 0 0 auto; z-index: 36; width: min(380px, 92vw); max-width: none; box-sizing: border-box; box-shadow: -12px 0 40px #15291e20; }
}
</style>
