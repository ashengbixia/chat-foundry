import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { createChat, deleteChat, getChat, listChats, listMessages, renameChat, saveUserMessage } from '../api/chats'
import { streamChatReply } from '../api/stream'
import type { Chat } from '../api/chats'
import type { DisplayMessage } from '../types/chat'

export interface ChatInitializationResult { // 启动结果：只有 missing 才表示应清除浏览器中的失效 ID
  status: 'empty' | 'restored' | 'missing' | 'failed'
  chat: Chat | null
}

export const useChatStore = defineStore('chat', () => { // 集中保存聊天状态和数据操作，不直接操作页面元素
  const inputText = ref('') // 当前输入内容
  const messages = ref<DisplayMessage[]>([]) // 当前会话的页面消息
  const currentChat = ref<Chat | null>(null) // 当前打开的会话
  const chats = ref<Chat[]>([]) // 会话列表
  const errorMessage = ref('') // 页面错误说明
  const sendingMessage = ref(false) // 是否正在保存问题或生成回答
  const generatingReply = ref(false) // 是否正在执行模型流式请求，供停止按钮和页面跟随使用
  let replyController: AbortController | null = null // 内部请求控制器，不作为可展示或持久化的状态返回
  const creatingChat = ref(false) // 是否正在创建会话
  const loadingChats = ref(false) // 是否正在读取会话列表
  const loadingMessages = ref(false) // 是否正在读取消息
  const hasOlderMessages = ref(false) // 当前会话是否还有更早消息
  const nextBeforeSequence = ref<number | null>(null) // 下一批旧消息的查询边界
  const loadingOlderMessages = ref(false) // 是否正在加载更早消息
  const restoringChat = ref(true) // 是否正在恢复上次会话
  const deletingChatId = ref<string | null>(null) // 正在删除的会话 ID
  const renamingChatId = ref<string | null>(null) // 正在重命名的会话 ID
  const operationBusy = computed(() => sendingMessage.value || creatingChat.value || loadingMessages.value || loadingOlderMessages.value || deletingChatId.value !== null || renamingChatId.value !== null) // 是否正在执行会话或消息操作
  const pageBusy = computed(() => operationBusy.value || restoringChat.value) // 页面是否忙碌，包含启动恢复阶段
  const inputDisabled = computed(() => sendingMessage.value || creatingChat.value || loadingOlderMessages.value || deletingChatId.value !== null || renamingChatId.value !== null) // 输入框禁用规则
  const sendDisabled = computed(() => !currentChat.value || !inputText.value.trim() || inputDisabled.value) // 发送按钮禁用规则
  const canRetryReply = computed(() => { // 根据最后一条页面消息判断是否提供重试入口
    const latest = messages.value[messages.value.length - 1] // 最后一条页面消息
    if (!latest) return false
    if (latest.role === 'user') return latest.status === 'completed'
    return latest.status === 'failed' || latest.status === 'cancelled'
  })
  async function loadChats(): Promise<boolean> { // 查询会话列表，返回是否成功，供启动流程判断能否继续
    loadingChats.value = true
    try {
      chats.value = await listChats()
      return true
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '读取会话列表失败'
      return false
    } finally {
      loadingChats.value = false
    }
  }
  async function createNewChat(): Promise<Chat | null> { // 创建会话并更新聊天状态，失败或忙碌时返回 null
    if (pageBusy.value) return null
    creatingChat.value = true
    errorMessage.value = ''
    try {
      const chat = await createChat() // 请求后端创建会话
      currentChat.value = chat
      chats.value.unshift(chat)
      messages.value = []
      hasOlderMessages.value = false
      nextBeforeSequence.value = null
      inputText.value = ''
      return chat
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '创建会话失败'
      return null
    } finally {
      creatingChat.value = false
    }
  }
  async function openChatSession(chat: Chat): Promise<boolean> { // 读取并切换会话，成功返回 true
    if (operationBusy.value) return false // 启动恢复也要打开会话，因此不拦截 restoringChat
    loadingMessages.value = true
    errorMessage.value = ''
    try {
      const page = await listMessages(chat.id) // 首次打开取最近一批消息
      currentChat.value = chat
      messages.value = page.items
      hasOlderMessages.value = page.has_more
      nextBeforeSequence.value = page.next_before_sequence
      inputText.value = ''
      return true
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '读取消息失败'
      return false
    } finally {
      loadingMessages.value = false
    }
  }
  async function initializeChat(lastChatId: string | null): Promise<ChatInitializationResult> { // 初始化会话列表和历史，只处理数据，不直接操作浏览器记录
    restoringChat.value = true
    errorMessage.value = ''
    try {
      const loaded = await loadChats() // 先确认会话列表读取成功
      if (!loaded) return { status: 'failed', chat: null }
      if (!lastChatId) return { status: 'empty', chat: null }
      const chat = await getChat(lastChatId) // 404 才返回 null；网络和服务器错误会抛出异常
      if (chat === null) return { status: 'missing', chat: null }
      if (!chats.value.some((item) => item.id === chat.id)) chats.value.unshift(chat) // 两次请求间新出现的会话补入列表，避免重复
      const opened = await openChatSession(chat) // 只读取一次消息，成功后才切换当前会话
      if (!opened) return { status: 'failed', chat: null }
      return { status: 'restored', chat }
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '恢复会话失败'
      return { status: 'failed', chat: null }
    } finally {
      restoringChat.value = false
    }
  }
  async function loadOlderMessages(): Promise<boolean> { // 前插更早消息并更新查询边界，不操作页面滚动
    const chat = currentChat.value // 本次请求所属会话
    const cursor = nextBeforeSequence.value // 发起请求时的分页边界
    if (!chat || pageBusy.value || !hasOlderMessages.value || cursor === null) return false
    loadingOlderMessages.value = true
    errorMessage.value = ''
    try {
      const page = await listMessages(chat.id, cursor) // 请求当前边界之前的一批消息
      if (currentChat.value?.id !== chat.id || nextBeforeSequence.value !== cursor) return false // 忽略不再属于当前分页状态的结果
      const loadedIds = new Set(messages.value.map((message) => message.id)) // 已展示消息的 ID
      const olderMessages = page.items.filter((message) => { // 去掉跨批或同批中的重复消息
        if (loadedIds.has(message.id)) return false
        loadedIds.add(message.id)
        return true
      })
      messages.value = [...olderMessages, ...messages.value]
      hasOlderMessages.value = page.has_more
      nextBeforeSequence.value = page.next_before_sequence
      return true
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '读取更早消息失败'
      return false
    } finally {
      loadingOlderMessages.value = false
    }
  }
  async function renameChatSession(chatId: string, title: string): Promise<boolean> { // 修改标题并同步会话状态
    if (pageBusy.value) return false
    title = title.trim()
    if (!title || title.length > 200) {
      errorMessage.value = '标题必须为 1～200 个字符'
      return false
    }
    renamingChatId.value = chatId
    errorMessage.value = ''
    try {
      const updatedChat = await renameChat(chatId, title) // 获取后端保存后的会话
      chats.value = chats.value.map((item) => item.id === updatedChat.id ? updatedChat : item)
      if (currentChat.value?.id === updatedChat.id) {
        currentChat.value = updatedChat
      }
      return true
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '修改标题失败'
      return false
    } finally {
      renamingChatId.value = null
    }
  }
  async function deleteChatSession(chatId: string): Promise<boolean> { // 删除会话，并清理相关页面数据
    if (pageBusy.value) return false
    deletingChatId.value = chatId
    errorMessage.value = ''
    try {
      await deleteChat(chatId)
      chats.value = chats.value.filter((item) => item.id !== chatId)
      if (currentChat.value?.id === chatId) {
        currentChat.value = null
        messages.value = []
        hasOlderMessages.value = false
        nextBeforeSequence.value = null
        inputText.value = ''
      }
      return true
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '删除会话失败'
      return false
    } finally {
      deletingChatId.value = null
    }
  }
  function stopReply() { // 中止本次流程；保存问题尚未结束时，阻止后续模型请求启动
    replyController?.abort()
  }
  async function generateAssistantReply(chatId: string, controller: AbortController): Promise<void> { // 发送和重试共用的内部生成流程，不重复保存问题
    if (controller.signal.aborted) return
    generatingReply.value = true
    const draft = reactive<DisplayMessage>({ // 当前回答的页面占位消息
      id: `pending-${crypto.randomUUID()}`,
      role: 'assistant',
      content: '',
      output: [], // 接收实时工具记录；重新打开会话时读取数据库中的完整 output
      status: 'generating',
      error: null,
    })
    messages.value.push(draft)
    try {
      const messageId = await streamChatReply(chatId, (text) => { // 接收并追加本次回答的正文片段
        if (!controller.signal.aborted) draft.content += text
      }, controller.signal, (event) => { // 工具事件直接更新当前助手消息，无需重新打开会话
        if (controller.signal.aborted) return
        if (event.type === 'tool_call') {
          draft.output.push({ type: 'function_call', call_id: event.callId, name: event.name, arguments: event.arguments })
        } else {
          draft.output.push({ type: 'function_call_output', call_id: event.callId, output: event.output })
        }
      })
      draft.id = messageId
      draft.status = 'completed'
    } catch (error) {
      if (controller.signal.aborted) {
        draft.status = 'cancelled'
      } else {
        draft.status = 'failed'
        draft.error = error instanceof Error ? error.message : '生成回答失败'
        throw error
      }
    } finally {
      generatingReply.value = false
    }
  }
  async function sendMessage(): Promise<void> { // 保存新问题，然后生成回答
    const text = inputText.value.trim() // 本次问题正文
    const chat = currentChat.value // 本次所属会话
    if (!text || pageBusy.value) return
    if (!chat) {
      errorMessage.value = '请先新建一个会话'
      return
    }
    const controller = new AbortController() // 覆盖保存问题和生成回答两阶段的取消标记
    replyController = controller
    sendingMessage.value = true
    errorMessage.value = ''
    try {
      const userMessage = await saveUserMessage(chat.id, text) // 正常发送才保存用户问题
      if (controller.signal.aborted) return // 页面已退出时不继续启动模型请求，已保存的问题仍在数据库中
      messages.value.push(userMessage)
      inputText.value = ''
      await generateAssistantReply(chat.id, controller)
    } catch (error) {
      if (!controller.signal.aborted) errorMessage.value = error instanceof Error ? error.message : '发送或生成失败'
    } finally {
      replyController = null
      sendingMessage.value = false
    }
  }
  async function retryReply(): Promise<void> { // 根据已保存的问题重试，不再次保存用户消息
    const chat = currentChat.value // 本次重试所属会话
    if (!chat || !canRetryReply.value || pageBusy.value) return
    const controller = new AbortController() // 本次重试的取消控制器
    replyController = controller
    sendingMessage.value = true
    errorMessage.value = ''
    try {
      await generateAssistantReply(chat.id, controller)
    } catch (error) {
      if (!controller.signal.aborted) errorMessage.value = error instanceof Error ? error.message : '重试回答失败'
    } finally {
      replyController = null
      sendingMessage.value = false
    }
  }
  return { inputText, messages, currentChat, chats, errorMessage, sendingMessage, generatingReply, creatingChat, loadingChats, loadingMessages, hasOlderMessages, nextBeforeSequence, loadingOlderMessages, restoringChat, deletingChatId, renamingChatId, operationBusy, pageBusy, inputDisabled, sendDisabled, canRetryReply, loadChats, createNewChat, openChatSession, initializeChat, loadOlderMessages, renameChatSession, deleteChatSession, sendMessage, retryReply, stopReply }
})
