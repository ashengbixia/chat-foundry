<script setup lang="ts">
import type { Chat } from './api/chats'
import type { FileInfo } from './types/file'
import KnowledgePanel from './components/knowledge/KnowledgePanel.vue'
import { listKnowledge, setChatKnowledge } from './api/knowledge'
import type { KnowledgeBase, KnowledgeReference } from './api/knowledge'
import { getChat } from './api/chats'
import MessageList from './components/chat/MessageList.vue'
import MessageInput from './components/chat/MessageInput.vue'
import ChatSidebar from './components/chat/ChatSidebar.vue'
import FilePanel from './components/files/FilePanel.vue'
import AppLayout from './components/layout/AppLayout.vue'
import AppIcon from './components/AppIcon.vue'
import AccountMenu from './components/auth/AccountMenu.vue'
import SettingsPanel from './components/settings/SettingsPanel.vue'
import ComposerModelPicker from './components/chat/ComposerModelPicker.vue'
import { useAuthStore } from './stores/auth'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from './stores/chat'
import { clearLastChatId, getLastChatId, saveLastChatId } from './utils/chatStorage'
import { exportChatMarkdown } from './utils/chatExport'

const chatStore = useChatStore() // 获取当前应用共用的聊天状态仓库
const accountId = useAuthStore().user!.id // 固定本次页面所属账号，迟到请求不能覆盖另一个账号的浏览器记录
const settingsPanel = ref<InstanceType<typeof SettingsPanel> | null>(null) // 当前账号的模型服务设置入口
const currentModelName = ref('') // 设置面板加载或保存成功后更新实际模型名称
const filePanel = ref<InstanceType<typeof FilePanel> | null>(null) // 直接复用文件面板的校验、上传和索引流程
const uploadingAttachments = ref(false)
const switchingModel = ref(false)
const knowledgePanel = ref<InstanceType<typeof KnowledgePanel> | null>(null)
const pendingKnowledge = ref<KnowledgeReference[]>([]) // 空白页先选库，首次发送时随新会话一起保存
const switchingKnowledge = ref(false)
const selectedKnowledge = computed(() => currentChat.value?.knowledge_bases ?? pendingKnowledge.value)
const messageInput = ref<InstanceType<typeof MessageInput> | null>(null)
const toast = ref('')
const showLatest = ref(false)
const newChatRevision = ref(0)
let toastTimer: ReturnType<typeof setTimeout> | null = null
function notify(message: string) {
  toast.value = message
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 3200)
}
async function focusComposer() { await nextTick(); messageInput.value?.focus() }
function settingsSaved() { notify('模型配置已保存，下次回答生效'); void focusComposer() }
function attachFile(file: FileInfo) { // 已有资料直接引用，不重复上传或建立索引
  if (addAttachment(file)) notify(file.reused ? `同内容资料已存在，已复用：${file.filename}` : `已添加：${file.filename}`)
}
function addWebLink(url: string) {
  if (inputDisabled.value) return
  inputText.value = [inputText.value.trimEnd(), `请读取这个网页：${url}`].filter(Boolean).join('\n')
  void focusComposer()
}

function closeFiles() { filesOpen.value = false; void focusComposer() }
function jumpToLatest() {
  autoFollowMessages = true
  scrollMessagesToBottom()
  showLatest.value = false
}
const { setWebSearchEnabled, startBlankChat, createNewChat, openChatSession, initializeChat, renameChatSession, deleteChatSession, sendMessage, retryReply, stopReply, loadOlderMessages, addAttachment, removeAttachment, markFileDeleted, removeConversationFile } = chatStore
const { webSearchEnabled, updatingFeatures, inputText, selectedAttachments, messages, currentChat, chats, errorMessage, sendingMessage, generatingReply, generatingChatIds, navigationBusy, creatingChat, loadingChats, restoringChat, deletingChatId, renamingChatId, pageBusy, inputDisabled, sendDisabled, canRetryReply, hasOlderMessages, loadingOlderMessages } = storeToRefs(chatStore) // 保持响应式连接，不复制出另一份状态
const messageListElement = ref<HTMLElement | null>(null) // 消息区域的页面元素
const filesOpen = ref(false) // 是否显示文件管理区域，隐藏时保留上传与预览状态
const interactionBusy = computed(() => pageBusy.value || uploadingAttachments.value || switchingModel.value || switchingKnowledge.value)
const sidebarBusy = computed(() => navigationBusy.value || uploadingAttachments.value || switchingModel.value || switchingKnowledge.value)
async function applyKnowledge(items: KnowledgeBase[] | KnowledgeReference[]) {
  if (interactionBusy.value) throw new Error('请等待当前操作完成后再调整知识库')
  if (!currentChat.value) { pendingKnowledge.value = items; notify('已选择知识库，发送第一条消息时保存'); return }
  const chatId = currentChat.value.id
  switchingKnowledge.value = true
  try {
    const selected = await setChatKnowledge(chatId, items.map(item => item.id))
    if (!pageActive || currentChat.value?.id !== chatId) return
    currentChat.value.knowledge_bases = selected
    chats.value = chats.value.map(chat => chat.id === chatId ? { ...chat, knowledge_bases: selected } : chat)
    notify(selected.length ? '知识库已应用到当前会话' : '已移除当前会话的知识库')
  } finally { switchingKnowledge.value = false }
}
async function toggleComposerKnowledge(base: KnowledgeBase) {
  try {
    const current = selectedKnowledge.value
    const selected = current.some(item => item.id === base.id)
    await applyKnowledge(selected ? current.filter(item => item.id !== base.id) : [...current, base])
  } catch (cause) { errorMessage.value = cause instanceof Error ? cause.message : '选择知识库失败' }
}
async function removeKnowledge(id: string) {
  try { await applyKnowledge(selectedKnowledge.value.filter(item => item.id !== id)) }
  catch (cause) { errorMessage.value = cause instanceof Error ? cause.message : '移除知识库失败' }
}
async function refreshKnowledge() { // 管理面板改名、删库后，同步当前会话标签；迟到响应不能覆盖别的会话
  void filePanel.value?.refreshFiles()
  const chatId = currentChat.value?.id
  try {
    if (!chatId) {
      const catalog = await listKnowledge()
      if (pageActive && !currentChat.value) pendingKnowledge.value = pendingKnowledge.value.flatMap(item => { const found = catalog.find(base => base.id === item.id); return found ? [found] : [] })
      return
    }
    const chat = await getChat(chatId)
    if (pageActive && chat && currentChat.value?.id === chatId) { currentChat.value.knowledge_bases = chat.knowledge_bases ?? []; currentChat.value.files = chat.files ?? [] }
  } catch (cause) { if (pageActive) errorMessage.value = cause instanceof Error ? cause.message : '刷新知识库选择失败' }
}
let pageActive = true
let preservingScroll = false // 加载旧消息期间暂停自动滚动，避免前插内容后跳到底部
let autoFollowMessages = true // 是否跟随新消息滚动，向上翻阅时暂停
watch(generatingReply, (active) => { if (active) autoFollowMessages = true }, { flush: 'sync' }) // 开始生成时立即恢复跟随，赶在消息展示和滚动监听之前
onUnmounted(() => { pageActive = false; chatStore.stopAllReplies(); if (toastTimer) clearTimeout(toastTimer) }) // 退出账号时中止所有会话流程
function updateScrollFollow() { // 根据距离底部的位置判断是否继续自动滚动
  const element = messageListElement.value // 当前消息滚动容器
  if (!element || preservingScroll) return
  autoFollowMessages = element.scrollHeight - element.scrollTop - element.clientHeight <= 64
  showLatest.value = !autoFollowMessages
}
function scrollMessagesToBottom() { // 在允许跟随时将消息区域滚到底部
  const element = messageListElement.value // 当前消息滚动容器
  if (!element || preservingScroll || !autoFollowMessages) return
  element.scrollTop = element.scrollHeight
  showLatest.value = false
}
watch(() => [currentChat.value?.id, messages.value.length, messages.value[messages.value.length - 1]?.content, messages.value[messages.value.length - 1]?.status, messages.value[messages.value.length - 1]?.error, messages.value[messages.value.length - 1]?.output?.length], scrollMessagesToBottom, { flush: 'post' }) // 正文和工具记录更新后，在允许跟随时滚动

async function showOlderMessages() { // 加载旧消息后，用新增高度补偿滚动位置
  const element = messageListElement.value // 本次操作的滚动容器
  const chatId = currentChat.value?.id // 本次操作所属会话
  if (!element || !chatId || pageBusy.value || preservingScroll || !hasOlderMessages.value) return
  const previousHeight = element.scrollHeight // 加载前的内容总高度
  preservingScroll = true
  try {
    const loaded = await loadOlderMessages() // 数据请求及前插交给仓库
    if (!loaded) return
    const previousTop = element.scrollTop // 保留等待期间用户自行滚动后的位置
    await nextTick() // 等旧消息渲染完成后再读取新增高度
    if (currentChat.value?.id !== chatId || messageListElement.value !== element) return
    element.scrollTop = previousTop + element.scrollHeight - previousHeight
    autoFollowMessages = false // 加载旧消息后保持阅读，用户滚回底部时再恢复跟随
  } finally {
    preservingScroll = false
  }
}
async function editChatTitle(chat: Chat) { // 弹窗收集标题，再交给 store 保存
  if (sidebarBusy.value) return
  const input = window.prompt('请输入新的会话标题：', chat.title) // 用户输入的新标题
  if (input === null || input.trim() === chat.title) return
  await renameChatSession(chat.id, input)
}
const exportingChat = ref(false)
async function downloadChat(chat: Chat) {
  if (sidebarBusy.value || exportingChat.value) return
  exportingChat.value = true
  notify('正在准备对话下载…')
  try { await exportChatMarkdown(chat, () => pageActive); if (pageActive) notify('对话已导出为 Markdown') }
  catch (cause) { if (pageActive) errorMessage.value = cause instanceof Error ? cause.message : '下载对话失败' }
  finally { exportingChat.value = false }
}
async function removeChat(chat: Chat) { // 确认删除，成功后处理浏览器记录
  if (sidebarBusy.value) return
  const confirmed = window.confirm(`确定删除“${chat.title}”及全部消息吗？此操作不可恢复。`) // 获取删除确认
  if (!confirmed) return

  const wasCurrent = currentChat.value?.id === chat.id // 删除前记住它是否为当前会话
  const deleted = await deleteChatSession(chat.id) // 删除操作交给 store
  if (deleted && wasCurrent) clearLastChatId(accountId)
}
async function openChat(chat: Chat) { // 打开成功后，处理浏览器记录和页面滚动
  if (sidebarBusy.value) return
  switchingKnowledge.value = true
  try {
    const fresh = await getChat(chat.id) // 删库、改名及其他标签页修改后，不能恢复列表里过期的知识库标签
    if (!pageActive) return
    if (!fresh) throw new Error('会话已不存在，请刷新会话列表')
    const opened = await openChatSession(fresh)
    if (!opened) return
    pendingKnowledge.value = []
    autoFollowMessages = true
    saveLastChatId(chat.id, accountId)
    await nextTick()
    scrollMessagesToBottom()
    void focusComposer()
  } catch (cause) { errorMessage.value = cause instanceof Error ? cause.message : '打开会话失败' }
  finally { switchingKnowledge.value = false }
}
onMounted(async () => { // 启动时读取浏览器记录，由 store 恢复数据，页面只负责记录和滚动
  const result = await initializeChat(getLastChatId(accountId)) // 初始化结果区分会话不存在和请求失败
  if (result.status === 'missing') clearLastChatId(accountId)
  if (result.status !== 'restored' || result.chat === null) return
  saveLastChatId(result.chat.id, accountId)
  autoFollowMessages = true
  await nextTick()
  scrollMessagesToBottom()
})
async function startNewChat() { // 打开空白输入页，不请求创建接口、不增加空会话
  if (sidebarBusy.value) return
  if (!startBlankChat()) return
  clearLastChatId(accountId) // 空白页刷新后仍为空白，不恢复刚才离开的会话
  autoFollowMessages = true
  showLatest.value = false
  newChatRevision.value++
  void focusComposer()
  pendingKnowledge.value = []
}
async function sendChatMessage() { // 第一次发送时自动新建会话，保留已经写好的正文和附件
  if (interactionBusy.value || !inputText.value.trim()) return
  if (!currentChat.value) {
    const text = inputText.value
    const attachments = [...selectedAttachments.value]
    const chat = await createNewChat(pendingKnowledge.value.map(item => item.id))
    if (!chat || !pageActive) return
    inputText.value = text
    selectedAttachments.value = attachments
    pendingKnowledge.value = []
    saveLastChatId(chat.id, accountId)
    autoFollowMessages = true
  }
  await sendMessage()
}
</script>

<template>
  <SettingsPanel ref="settingsPanel" @model-changed="currentModelName = $event" @saved="settingsSaved" />
  <KnowledgePanel ref="knowledgePanel" :selected="selectedKnowledge" :selection-disabled="interactionBusy" :apply-selection="applyKnowledge" @changed="refreshKnowledge" />
  <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  <AppLayout :files-open="filesOpen" :current-chat-id="currentChat?.id" :new-chat-revision="newChatRevision" :drop-disabled="interactionBusy" @close-files="closeFiles" @open-settings="settingsPanel?.open()" @open-knowledge="knowledgePanel?.open()" @dropped-files="filePanel?.uploadSelectedFiles($event)" @attach-saved="filePanel?.attachById($event)">
    <template #account><AccountMenu /></template>
    <template #sidebar>
      <ChatSidebar
        :chats="chats"
        :current-chat-id="currentChat?.id ?? null"
        :loading="loadingChats"
        :creating="creatingChat"
        :deleting-chat-id="deletingChatId"
        :renaming-chat-id="renamingChatId"
        :action-disabled="sidebarBusy"
        :open-disabled="sidebarBusy"
        :generating-chat-ids="generatingChatIds"
        @create="startNewChat"
        @open="openChat"
        @remove="removeChat"
        @rename="editChatTitle"
        @download="downloadChat"
      />
    </template>
    <template #header>
      <div class="chat-title"><span class="workspace-label">工作空间 <span>/</span> 对话</span><strong>{{ currentChat?.title ?? '新的开始' }}</strong></div>
      <span class="model-placeholder" title="打开当前账号的模型服务设置">
        <button class="model-button" type="button" @click="settingsPanel?.open()"><span class="model-dot"></span>{{ currentModelName || '设置模型服务' }}<AppIcon name="chevron" :size="12" /></button>
      </span>
      <button class="files-toggle" :class="{ active: filesOpen }" type="button" aria-controls="file-manager" :aria-expanded="filesOpen" @click="filesOpen = !filesOpen"><AppIcon name="panel" :size="16" /><span>文件管理</span></button>
    </template>
    <template #messages>
      <p v-if="restoringChat" class="page-notice" role="status">正在恢复上次会话…</p>
      <p v-if="errorMessage" class="page-error" role="alert">{{ errorMessage }}</p>
      <section ref="messageListElement" class="message-list" @scroll="updateScrollFollow">
        <div class="message-content">
          <div v-if="currentChat" class="message-pagination">
            <button v-if="hasOlderMessages" type="button" :disabled="pageBusy" @click="showOlderMessages">
              {{ loadingOlderMessages ? '正在加载更早消息…' : '加载更早消息' }}
            </button>
            <small v-else-if="messages.length">对话从这里开始</small>
          </div>
          <MessageList :messages="messages" :has-chat="!!currentChat" @updated="chatStore.refreshSubagentMessages($event)" />
        </div>
      </section>
    </template>
    <template #composer>
      <div class="composer-content">
        <div v-if="showLatest" class="latest-actions"><button type="button" @click="jumpToLatest">↓ 回到最新消息</button></div>
        <div class="reply-actions">
          <button v-if="canRetryReply" class="retry-button" type="button" :disabled="!currentChat || interactionBusy" @click="retryReply"><AppIcon name="refresh" :size="13" />重试回答</button>
        </div>
        <MessageInput
          ref="messageInput"
          v-model="inputText"
          :input-disabled="inputDisabled"
          :send-disabled="sendDisabled || uploadingAttachments || switchingModel || switchingKnowledge"
          :sending="sendingMessage"
          :attachments="selectedAttachments"
          :uploading-attachments="uploadingAttachments"
          :knowledge-bases="selectedKnowledge"
          :knowledge-disabled="interactionBusy"
          :conversation-files="currentChat?.files ?? []"
          @remove-conversation-file="removeConversationFile"
          @open-knowledge="knowledgePanel?.open(true)"
          @manage-knowledge="knowledgePanel?.open()"
          @remove-knowledge="removeKnowledge"
          @attach-saved="attachFile"
          @add-web-link="addWebLink"
          :web-search-enabled="webSearchEnabled"
          :web-search-disabled="interactionBusy || updatingFeatures"
          @toggle-web-search="setWebSearchEnabled(!webSearchEnabled)"
          @toggle-knowledge="toggleComposerKnowledge"
          @remove-attachment="removeAttachment"
          @send="sendChatMessage"
          @stop="stopReply"
          @open-files="filesOpen = true"
          @pick-files="filePanel?.pickAttachments()"
          @paste-files="filePanel?.uploadSelectedFiles($event)"
        ><template #model-picker><ComposerModelPicker :current-model="currentModelName" :disabled="pageBusy || uploadingAttachments || switchingKnowledge" @changed="currentModelName = $event" @busy="switchingModel = $event" @open-settings="settingsPanel?.open()" /></template></MessageInput>
        <small class="input-hint"><span>ChatFoundry · 让思考发生</span><span>AI 回答仅供参考，请核实重要信息</span></small>
      </div>
    </template>
    <template #files>
      <FilePanel ref="filePanel" :attach-disabled="pageBusy || switchingModel || switchingKnowledge" :delete-disabled="interactionBusy" :attachment-slots="3 - selectedAttachments.length" :selected-ids="[...selectedAttachments, ...(currentChat?.files ?? []).filter(file => !file.deleted)].map(file => file.file_id)" @attach="attachFile" @removed="markFileDeleted" @attachment-upload="uploadingAttachments = $event" @attachment-error="errorMessage = $event" />
    </template>
  </AppLayout>
</template>

<style scoped>
.toast { position: fixed; top: 20px; left: 50%; transform: translateX(-50%); max-width: calc(100vw - 32px); z-index: 50; padding: 12px 18px; border: 1px solid #d6e5dc; border-radius: 12px; background: #f2f8f4; color: #365c45; box-shadow: 0 8px 28px #233d2f16; font-size: 13px; overflow-wrap: anywhere; }
.composer-content { position: relative; }
.latest-actions { position: absolute; bottom: calc(100% + 12px); left: 50%; transform: translateX(-50%); }
.latest-actions button { white-space: nowrap; padding: 8px 14px; border: 1px solid #dce5df; border-radius: 20px; background: #fff; color: #58715f; box-shadow: 0 4px 16px #233d2f14; font-size: 12px; }
.chat-title { flex: 1; min-width: 0; }
.workspace-label { display: flex; align-items: center; gap: 7px; color: #a0a5ad; font-size: 10px; }
.workspace-label > span { color: #d2d6dc; }
.chat-title > strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 5px; font-size: 12px; font-weight: 500; color: #50565f; }
.model-button, .files-toggle { display: flex; align-items: center; justify-content: center; gap: 7px; white-space: nowrap; padding: 7px 10px; border: 1px solid #e9ebee; border-radius: 8px; background: #fff; color: #7b838b; font-size: 11px; }
.model-button { border-color: transparent; opacity: .65; }
.model-dot { width: 5px; height: 5px; background: #9eaaa2; border-radius: 50%; }
.model-button svg { transform: rotate(90deg); }
.files-toggle:hover, .files-toggle.active { border-color: #d3e1d8; background: #f1f6f3; color: #47745b; }
.page-notice, .page-error { margin: 10px 30px 0; font-size: 12px; padding: 10px 14px; border-radius: 8px; }
.page-notice { color: #7c8780; background: #f5f7f5; }
.page-error { color: #a65a5a; background: #fdf3f3; }
.message-list { flex: 1; min-height: 0; overflow-y: auto; overflow-anchor: none; padding: 24px 36px; }
.message-content, .composer-content { width: 100%; max-width: 760px; margin: 0 auto; }
.message-pagination { display: flex; justify-content: center; align-items: center; margin-bottom: 24px; color: #b0b5bb; font-size: 10px; }
.message-pagination:empty { display: none; }
.message-pagination button { border: 1px solid var(--border); border-radius: 20px; padding: 7px 15px; background: #fff; color: #8b9590; font-size: 11px; }
.message-pagination small { font-size: 10px; }
.reply-actions { display: flex; justify-content: flex-end; gap: 8px; }
.retry-button { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; padding: 6px 10px; border: 1px solid var(--border); border-radius: 8px; background: #fff; color: #7c857f; font-size: 11px; }
.input-hint { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 5px; margin: 11px 4px 0; color: #a8adb3; font-size: 9px; }
@media (max-width: 600px) {
  .message-list { padding: 20px 16px; }
  .model-placeholder { display: none; }
  .input-hint { justify-content: center; font-size: 9px; }
  .input-hint > span:first-child { display: none; }
}
</style>
