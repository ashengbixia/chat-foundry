<script setup lang="ts">
import { createChat, deleteChat, getChat, listChats, listMessages, renameChat, saveUserMessage } from './api/chats'
import {streamChatReply} from "./api/stream.ts";
import type { Chat, ChatMessage } from './api/chats'
import { computed, onMounted, reactive, ref, shallowRef, watch } from 'vue'
// 输入框中的文字。
const inputText = ref('')

// 当前页面上的消息，暂时只保存正文。
type DisplayMessage = Pick<ChatMessage,'id' | 'role' | 'content' | 'status' | 'error'>

const messages = ref<DisplayMessage[]>([])
const sendingMessage = ref(false)//正在发送消息或等待回答。
const currentChat = ref<Chat | null>(null)//会话对象
const creatingChat = ref(false)//正在创建会话
const errorMessage = ref('')
const chats = ref<Chat[]>([])
const loadingChats = ref(false)
const loadingMessages = ref(false)//正在加载消息
const LAST_CHAT_KEY = 'open-search:last-chat-id'
const restoringChat = ref(true)
const deletingChatId = ref<string | null>(null) // 当前正在删除的会话 ID，null 表示没有删除任务
const renamingChatId = ref<string | null>(null) // 正在重命名的会话 ID
const messageListElement = ref<HTMLElement | null>(null) // 消息区域的页面元素
let autoFollowMessages = true // 是否跟随新消息滚动，向上翻阅时暂停
const replyController = shallowRef<AbortController | null>(null) // 当前模型请求的取消控制器
const canRetryReply = computed(() => { // 根据页面上最后一条消息决定是否提供重试入口
  const latest = messages.value[messages.value.length - 1] // 当前显示的最后一条消息
  if (!latest) return false
  if (latest.role === 'user') return latest.status === 'completed'
  return latest.status === 'failed' || latest.status === 'cancelled'
})

async function retryReply() { // 根据已有问题重试，不重复保存用户消息
  const chat = currentChat.value // 本次重试所属的会话
  if (!chat || !canRetryReply.value || sendingMessage.value || creatingChat.value || loadingMessages.value || restoringChat.value || deletingChatId.value !== null || renamingChatId.value !== null) return
  sendingMessage.value = true
  errorMessage.value = ''
  try {
    await generateAssistantReply(chat.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重试回答失败'
  } finally {
    sendingMessage.value = false
  }
}
function stopReply() { // 中止当前流式请求
  replyController.value?.abort()
}
function updateScrollFollow() { // 根据距离底部的位置判断是否继续自动滚动
  const element = messageListElement.value // 当前消息滚动容器
  if (!element) return
  autoFollowMessages = element.scrollHeight - element.scrollTop - element.clientHeight <= 64
}
function scrollMessagesToBottom() { // 在允许跟随时将消息区域滚到底部
  const element = messageListElement.value // 当前消息滚动容器
  if (!element || !autoFollowMessages) return
  element.scrollTop = element.scrollHeight
}
watch(() => [currentChat.value?.id, messages.value.length, messages.value[messages.value.length - 1]?.content, messages.value[messages.value.length - 1]?.status, messages.value[messages.value.length - 1]?.error], scrollMessagesToBottom, { flush: 'post' }) // 等页面更新后再调整滚动位置

async function editChatTitle(chat: Chat) { // 输入新标题，保存后更新页面
  if (renamingChatId.value !== null || deletingChatId.value !== null || sendingMessage.value || creatingChat.value || loadingMessages.value || restoringChat.value) return
  const input = window.prompt('请输入新的会话标题：', chat.title) // 默认显示原来的标题
  if (input === null) return
  const title = input.trim() // 去掉标题两端空白
  if (!title || title.length > 200) {
    errorMessage.value = '标题必须为 1～200 个字符'
    return
  }
  if (title === chat.title) return
  renamingChatId.value = chat.id
  errorMessage.value = ''
  try {
    const updatedChat = await renameChat(chat.id, title) // 获取后端保存后的会话
    chats.value = chats.value.map((item) => item.id === updatedChat.id ? updatedChat : item)
    if (currentChat.value?.id === updatedChat.id) {
      currentChat.value = updatedChat
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '修改标题失败'
  } finally {
    renamingChatId.value = null
  }
}
async function removeChat(chat: Chat) { // 确认后删除会话，并同步页面状态
  if (renamingChatId.value !== null || deletingChatId.value !== null || sendingMessage.value || creatingChat.value || loadingMessages.value || restoringChat.value) return
  const confirmed = window.confirm(`确定删除“${chat.title}”及全部消息吗？此操作不可恢复。`) // 获取用户确认
  if (!confirmed) return

  deletingChatId.value = chat.id
  errorMessage.value = ''
  try {
    await deleteChat(chat.id)
    chats.value = chats.value.filter((item) => item.id !== chat.id)
    if (currentChat.value?.id === chat.id) {
      currentChat.value = null
      messages.value = []
      inputText.value = ''
      try {
        localStorage.removeItem(LAST_CHAT_KEY)
      } catch (error) {
        console.warn('无法清除上次会话 ID', error)
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '删除会话失败'
  } finally {
    deletingChatId.value = null
  }
}
function rememberChat(chatId: string) {
  try {
    localStorage.setItem(LAST_CHAT_KEY, chatId)
  } catch (error) {
    // 浏览器不允许存储时，不影响正常聊天。
    console.warn('无法保存当前会话 ID', error)
  }
}
async function openChat(chat: Chat) {
  if (renamingChatId.value !== null || loadingMessages.value || sendingMessage.value || creatingChat.value || deletingChatId.value !== null) return
  loadingMessages.value = true
  errorMessage.value = ''
  try {
    const history = await listMessages(chat.id)
    // 读取成功后，再切换当前会话和消息。
    autoFollowMessages = true // 打开会话时重新跟随消息
    currentChat.value = chat
    rememberChat(chat.id)
    messages.value = history
    inputText.value = ''
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : '读取消息失败'
  } finally {
    loadingMessages.value = false
  }
}
async function loadChats() {
  loadingChats.value = true

  try {
    chats.value = await listChats()
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : '读取会话列表失败'
  } finally {
    loadingChats.value = false
  }
}

// 页面组件挂载后，读取会话列表。
onMounted(async () => {
  try {
    await loadChats()
    const chatId = localStorage.getItem(LAST_CHAT_KEY)
    if (!chatId) return
    const chat = await getChat(chatId)
    if (chat === null) {
      // 会话确实不存在时，才移除保存的 ID。
      localStorage.removeItem(LAST_CHAT_KEY)
      return
    }
    // 上次会话可能不在列表前 20 条中，补到页面列表。
    if (!chats.value.some((item) => item.id === chat.id)) {
      chats.value.unshift(chat)
    }
    await openChat(chat)
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : '恢复会话失败'
  } finally {
    restoringChat.value = false
  }
})
async function startNewChat() {
  if (renamingChatId.value !== null || deletingChatId.value !== null || creatingChat.value || sendingMessage.value || loadingMessages.value || restoringChat.value) return
  creatingChat.value = true
  errorMessage.value = ''
  try {
    const chat = await createChat()
    autoFollowMessages = true // 新会话恢复自动滚动
    currentChat.value = chat
    chats.value.unshift(chat)
    // 创建成功后，清空上一会话的页面内容。
    messages.value = []
    inputText.value = ''
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : '创建会话失败'
  } finally {
    creatingChat.value = false
  }
}
async function generateAssistantReply(chatId: string): Promise<void> { // 根据已保存历史生成回答，不重复保存用户问题
  const draft = reactive<DisplayMessage>({ // 页面上的助手占位消息
    id: `pending-${crypto.randomUUID()}`,
    role: 'assistant',
    content: '',
    status: 'generating',
    error: null,
  })
  const controller = new AbortController() // 本次生成的取消控制器
  messages.value.push(draft)
  replyController.value = controller
  autoFollowMessages = true
  try {
    const messageId = await streamChatReply(chatId, (text) => { draft.content += text }, controller.signal)
    draft.id = messageId
    draft.status = 'completed'
  } catch (error) {
    if (controller.signal.aborted) {
      draft.status = 'cancelled'
    } else {
      draft.status = 'failed'
      draft.error = error instanceof Error ? error.message : '生成回答失败'
      throw error // 普通错误继续交给调用方处理
    }
  } finally {
    replyController.value = null
  }
}
async function sendMessage() { // 保存用户问题，再生成回答
  const text = inputText.value.trim() // 本次问题
  const chat = currentChat.value // 当前会话
  if (!text || renamingChatId.value !== null || sendingMessage.value || creatingChat.value || loadingMessages.value || restoringChat.value || deletingChatId.value !== null) return
  if (!chat) {
    errorMessage.value = '请先新建一个会话'
    return
  }
  sendingMessage.value = true
  errorMessage.value = ''
  try {
    const userMessage = await saveUserMessage(chat.id, text) // 只在正常发送时保存问题
    autoFollowMessages = true
    messages.value.push(userMessage)
    inputText.value = ''
    await generateAssistantReply(chat.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发送或生成失败'
  } finally {
    sendingMessage.value = false
  }
}
</script>

<template>
  <main class="chat-page">
    <h1>我的 AI 助手</h1>
    <p v-if="restoringChat">正在恢复上次会话…</p>
<button
  type="button"
  :disabled="creatingChat || sendingMessage || loadingMessages || restoringChat || deletingChatId !== null || renamingChatId !== null"
  @click="startNewChat"
>
  {{ creatingChat ? '创建中…' : '新建会话' }}
</button>
<section>
  <h2>会话列表</h2>

  <p v-if="loadingChats">正在读取会话…</p>

  <p v-else-if="chats.length === 0">
    暂无会话
  </p>

  <ul v-else>
    <li v-for="chat in chats" :key="chat.id">
      <button
        type="button"
        :disabled="loadingMessages || sendingMessage || creatingChat || deletingChatId !== null || renamingChatId !== null"
        @click="openChat(chat)"
      >
        {{ currentChat?.id === chat.id ? '当前：' : '' }}
        {{ chat.title }}
      </button>
      <button
        type="button"
        :disabled="deletingChatId !== null || sendingMessage || creatingChat || loadingMessages || restoringChat || renamingChatId !== null"
        @click="removeChat(chat)"
      >
        {{ deletingChatId === chat.id ? '删除中…' : '删除' }}
      </button>
      <button
        type="button"
        :disabled="renamingChatId !== null || deletingChatId !== null || sendingMessage || creatingChat || loadingMessages || restoringChat"
        @click="editChatTitle(chat)"
      >
        {{ renamingChatId === chat.id ? '保存中…' : '重命名' }}
      </button>
    </li>
  </ul>
</section>
<p v-if="currentChat">
  当前会话：{{ currentChat.title }}
  <br />
  ID：{{ currentChat.id }}
</p>

<p v-if="errorMessage" role="alert">
  {{ errorMessage }}
</p>
    <section ref="messageListElement" class="message-list" @scroll="updateScrollFollow">
      <p v-if="messages.length === 0">还没有消息，发送一句话试试。</p>

      <div
        v-for="message in messages"
        :key="message.id"
        class="message"
      >
        <strong>{{ message.role === 'user' ? '我' : '助手' }}：</strong>
        <span>{{ message.content }}</span>

        <small v-if="message.status === 'generating'">
          正在生成…
        </small>
        <small v-if="message.status === 'cancelled'">已停止</small>

        <small v-if="message.status === 'failed'">
          {{ message.error }}
        </small>
      </div>
    </section>
    <button type="button" :disabled="replyController === null" @click="stopReply">
      停止生成
    </button>
    <button
      v-if="canRetryReply"
      type="button"
      :disabled="!currentChat || sendingMessage || creatingChat || loadingMessages || restoringChat || deletingChatId !== null || renamingChatId !== null"
      @click="retryReply"
    >
      重试回答
    </button>
    <form class="input-area" @submit.prevent="sendMessage">
      <input
        v-model="inputText"
        :disabled="sendingMessage || creatingChat || deletingChatId !== null || renamingChatId !== null"
        placeholder="输入你的问题"
      />

      <button
        type="submit"
        :disabled="!currentChat || !inputText.trim() || sendingMessage || creatingChat || deletingChatId !== null || renamingChatId !== null"
      >
        {{ sendingMessage ? '处理中…' : '发送' }}
      </button>
    </form>
  </main>
</template>

<style scoped>
.chat-page {
  width: min(760px, 100%);
  margin: 0 auto;
  padding: 24px;
  box-sizing: border-box;
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

.message {
  margin-bottom: 16px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

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
