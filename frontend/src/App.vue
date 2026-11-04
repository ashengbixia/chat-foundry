<script setup lang="ts">
import type { Chat } from './api/chats'
import MessageList from './components/chat/MessageList.vue'
import MessageInput from './components/chat/MessageInput.vue'
import ChatSidebar from './components/chat/ChatSidebar.vue'
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from './stores/chat'
import { clearLastChatId, getLastChatId, saveLastChatId } from './utils/chatStorage'

const chatStore = useChatStore() // 获取当前应用共用的聊天状态仓库
const { createNewChat, openChatSession, initializeChat, renameChatSession, deleteChatSession, sendMessage, retryReply, stopReply, loadOlderMessages } = chatStore // 获取仓库中的数据操作方法
const { inputText, messages, currentChat, chats, errorMessage, sendingMessage, generatingReply, creatingChat, loadingChats, restoringChat, deletingChatId, renamingChatId, pageBusy, inputDisabled, sendDisabled, canRetryReply, hasOlderMessages, loadingOlderMessages } = storeToRefs(chatStore) // 保持响应式连接，不复制出另一份状态
const messageListElement = ref<HTMLElement | null>(null) // 消息区域的页面元素
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
watch(() => [currentChat.value?.id, messages.value.length, messages.value[messages.value.length - 1]?.content, messages.value[messages.value.length - 1]?.status, messages.value[messages.value.length - 1]?.error], scrollMessagesToBottom, { flush: 'post' }) // 等页面更新后再调整滚动位置

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
  <main class="chat-page">
    <h1>我的 AI 助手</h1>
    <p v-if="restoringChat">正在恢复上次会话…</p>
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
<p v-if="currentChat">
  当前会话：{{ currentChat.title }}
  <br />
  ID：{{ currentChat.id }}
</p>

<p v-if="errorMessage" role="alert">
  {{ errorMessage }}
</p>
    <div v-if="currentChat" class="message-pagination">
      <button v-if="hasOlderMessages" type="button" :disabled="pageBusy" @click="showOlderMessages">
        {{ loadingOlderMessages ? '正在加载更早消息…' : '加载更早消息' }}
      </button>
      <small v-else>已加载全部消息</small>
    </div>
    <section ref="messageListElement" class="message-list" @scroll="updateScrollFollow">
      <MessageList :messages="messages" />
    </section>
    <button type="button" :disabled="!generatingReply" @click="stopReply">
      停止生成
    </button>
    <button
      v-if="canRetryReply"
      type="button"
      :disabled="!currentChat || pageBusy"
      @click="retryReply"
    >
      重试回答
    </button>
    <MessageInput
      v-model="inputText"
      :input-disabled="inputDisabled"
      :send-disabled="sendDisabled"
      :sending="sendingMessage"
      @send="sendMessage"
    />
  </main>
</template>

<style scoped>
.chat-page {
  width: min(760px, 100%);
  margin: 0 auto;
  padding: 24px;
  box-sizing: border-box;
}

.message-pagination {
  height: 48px;
  display: flex;
  align-items: center;
}

.message-list {
  min-height: 300px;
  max-height: 60vh;
  overflow-y: auto;
  overflow-anchor: none;
  padding: 16px;
  border: 1px solid #ccc;
  border-radius: 8px;
}

button {
  padding: 12px 20px;
}
</style>
