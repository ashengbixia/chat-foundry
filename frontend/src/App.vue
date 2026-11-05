<script setup lang="ts">
import type { Chat } from './api/chats'
import MessageList from './components/chat/MessageList.vue'
import MessageInput from './components/chat/MessageInput.vue'
import ChatSidebar from './components/chat/ChatSidebar.vue'
import FilePanel from './components/files/FilePanel.vue'
import AppLayout from './components/layout/AppLayout.vue'
import AppIcon from './components/AppIcon.vue'
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from './stores/chat'
import { clearLastChatId, getLastChatId, saveLastChatId } from './utils/chatStorage'

const chatStore = useChatStore() // 获取当前应用共用的聊天状态仓库
const { createNewChat, openChatSession, initializeChat, renameChatSession, deleteChatSession, sendMessage, retryReply, stopReply, loadOlderMessages, addAttachment, removeAttachment, markFileDeleted } = chatStore // 获取仓库中的数据操作方法
const { inputText, selectedAttachments, messages, currentChat, chats, errorMessage, sendingMessage, generatingReply, creatingChat, loadingChats, restoringChat, deletingChatId, renamingChatId, pageBusy, inputDisabled, sendDisabled, canRetryReply, hasOlderMessages, loadingOlderMessages } = storeToRefs(chatStore) // 保持响应式连接，不复制出另一份状态
const messageListElement = ref<HTMLElement | null>(null) // 消息区域的页面元素
const filesOpen = ref(false) // 是否显示文件管理区域，隐藏时保留上传与预览状态
let preservingScroll = false // 加载旧消息期间暂停自动滚动，避免前插内容后跳到底部
let autoFollowMessages = true // 是否跟随新消息滚动，向上翻阅时暂停
watch(generatingReply, (active) => { if (active) autoFollowMessages = true }, { flush: 'sync' }) // 开始生成时立即恢复跟随，赶在消息展示和滚动监听之前
onUnmounted(stopReply) // 页面销毁时中止未完成流程，避免继续产生无人接收的回答
function updateScrollFollow() { // 根据距离底部的位置判断是否继续自动滚动
  const element = messageListElement.value // 当前消息滚动容器
  if (!element || preservingScroll) return
  autoFollowMessages = element.scrollHeight - element.scrollTop - element.clientHeight <= 64
}
function scrollMessagesToBottom() { // 在允许跟随时将消息区域滚到底部
  const element = messageListElement.value // 当前消息滚动容器
  if (!element || preservingScroll || !autoFollowMessages) return
  element.scrollTop = element.scrollHeight
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
  if (pageBusy.value) return
  const input = window.prompt('请输入新的会话标题：', chat.title) // 用户输入的新标题
  if (input === null || input.trim() === chat.title) return
  await renameChatSession(chat.id, input)
}
async function removeChat(chat: Chat) { // 确认删除，成功后处理浏览器记录
  if (pageBusy.value) return
  const confirmed = window.confirm(`确定删除“${chat.title}”及全部消息吗？此操作不可恢复。`) // 获取删除确认
  if (!confirmed) return

  const wasCurrent = currentChat.value?.id === chat.id // 删除前记住它是否为当前会话
  const deleted = await deleteChatSession(chat.id) // 删除操作交给 store
  if (deleted && wasCurrent) clearLastChatId()
}
async function openChat(chat: Chat) { // 打开成功后，处理浏览器记录和页面滚动
  if (pageBusy.value) return // 启动恢复由 store 内部打开会话，页面手动操作需要等恢复完成
  const opened = await openChatSession(chat) // 请求和状态更新交给 store
  if (!opened) return
  autoFollowMessages = true
  saveLastChatId(chat.id)
  await nextTick() // 等消息显示到页面后，再根据新高度滚动
  scrollMessagesToBottom()
}
onMounted(async () => { // 启动时读取浏览器记录，由 store 恢复数据，页面只负责记录和滚动
  const result = await initializeChat(getLastChatId()) // 初始化结果区分会话不存在和请求失败
  if (result.status === 'missing') clearLastChatId()
  if (result.status !== 'restored' || result.chat === null) return
  saveLastChatId(result.chat.id)
  autoFollowMessages = true
  await nextTick()
  scrollMessagesToBottom()
})
async function startNewChat() { // 新建成功后，恢复页面自动滚动
  const chat = await createNewChat() // 创建和状态更新交给 store
  if (chat === null) return
  saveLastChatId(chat.id) // 新建会话也记录为上次会话，刷新后可以恢复
  autoFollowMessages = true
}
</script>

<template>
  <AppLayout :files-open="filesOpen" :current-chat-id="currentChat?.id" @close-files="filesOpen = false">
    <template #sidebar>
      <ChatSidebar
        :chats="chats"
        :current-chat-id="currentChat?.id ?? null"
        :loading="loadingChats"
        :creating="creatingChat"
        :deleting-chat-id="deletingChatId"
        :renaming-chat-id="renamingChatId"
        :action-disabled="pageBusy"
        :open-disabled="pageBusy"
        @create="startNewChat"
        @open="openChat"
        @remove="removeChat"
        @rename="editChatTitle"
      />
    </template>
    <template #header>
      <div class="chat-title"><span class="workspace-label">工作空间 <span>/</span> 对话</span><strong>{{ currentChat?.title ?? '新的开始' }}</strong></div>
      <span class="model-placeholder" title="模型选择暂未开放，当前使用后端配置的模型">
        <button class="model-button" type="button" disabled><span class="model-dot"></span>默认模型<AppIcon name="chevron" :size="12" /></button>
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
          <MessageList :messages="messages" :has-chat="!!currentChat" />
        </div>
      </section>
    </template>
    <template #composer>
      <div class="composer-content">
        <div class="reply-actions">
          <button v-if="canRetryReply" class="retry-button" type="button" :disabled="!currentChat || pageBusy" @click="retryReply"><AppIcon name="refresh" :size="13" />重试回答</button>
        </div>
        <MessageInput
          v-model="inputText"
          :input-disabled="inputDisabled"
          :send-disabled="sendDisabled"
          :sending="sendingMessage"
          :attachments="selectedAttachments"
          @remove-attachment="removeAttachment"
          @send="sendMessage"
          @stop="stopReply"
          @open-files="filesOpen = true"
        />
        <small class="input-hint"><span>ChatFoundry · 让思考发生</span><span>AI 回答仅供参考，请核实重要信息</span></small>
      </div>
    </template>
    <template #files>
      <FilePanel :attach-disabled="!currentChat || pageBusy" :delete-disabled="pageBusy" @attach="addAttachment" @removed="markFileDeleted" />
    </template>
  </AppLayout>
</template>

<style scoped>
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
