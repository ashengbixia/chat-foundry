import { computed, onScopeDispose, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { createChat, deleteChat, getChat, listChats, listMessages, renameChat, saveUserMessage, removeChatFile, setChatFeatures } from '../api/chats'
import { streamChatReply } from '../api/stream'
import type { Chat } from '../api/chats'
import type { DisplayMessage } from '../types/chat'
import type { FileInfo, MessageAttachment } from '../types/file'

export interface ChatInitializationResult { // 启动结果：只有 missing 才表示应清除浏览器中的失效 ID
  status: 'empty' | 'restored' | 'missing' | 'failed'
  chat: Chat | null
}

export const useChatStore = defineStore('chat', () => { // 集中保存聊天状态和数据操作，不直接操作页面元素
  const inputText = ref('') // 当前输入内容
  const selectedAttachments = ref<MessageAttachment[]>([]) // 待发送草稿；发送成功后进入独立的会话资料列表
  const messages = ref<DisplayMessage[]>([]) // 当前会话的页面消息
  const currentChat = ref<Chat | null>(null) // 当前打开的会话
  const pendingWebSearch = ref(false)
  const updatingFeatures = ref(false)
  const webSearchEnabled = computed(() => currentChat.value?.web_search_enabled ?? pendingWebSearch.value)
  const chats = ref<Chat[]>([]) // 会话列表
  const pendingTaskRefresh = new Set<string>()
  let disposed = false
  onScopeDispose(() => { disposed = true; pendingTaskRefresh.clear() })
  const titlePolls = new Map<string, AbortController>()
  onScopeDispose(() => { for (const request of titlePolls.values()) request.abort(); titlePolls.clear() })
  function watchAutoTitle(chatId: string) {
    if (titlePolls.has(chatId)) return
    const request = new AbortController()
    titlePolls.set(chatId, request)
    void (async () => {
      try {
        for (let attempt = 0; attempt < 24 && !request.signal.aborted; attempt++) {
          await new Promise<void>(resolve => {
            const finish = () => { clearTimeout(timer); request.signal.removeEventListener('abort', finish); resolve() }
            const timer = setTimeout(finish, 1500)
            request.signal.addEventListener('abort', finish, { once: true })
          })
          if (request.signal.aborted) return
          const updated = await getChat(chatId, request.signal)
          if (request.signal.aborted || !updated || !chats.value.some(chat => chat.id === chatId)) return
          // 仅同步标题字段，避免覆盖这期间新选的知识库、文件或搜索设置。
          chats.value = chats.value.map(chat => chat.id === chatId ? { ...chat, title: updated.title, title_status: updated.title_status } : chat)
          if (currentChat.value?.id === chatId) currentChat.value = { ...currentChat.value, title: updated.title, title_status: updated.title_status }
          if (!['pending', 'generating'].includes(updated.title_status ?? 'manual')) return
        }
      } catch { /* 标题更新失败不覆盖聊天错误；下次打开会话可读取最新结果。 */ }
      finally { if (titlePolls.get(chatId) === request) titlePolls.delete(chatId) }
    })()
  }
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
  const removingChatFile = ref(false)
  const operationBusy = computed(() => sendingMessage.value || creatingChat.value || loadingMessages.value || loadingOlderMessages.value || deletingChatId.value !== null || renamingChatId.value !== null || removingChatFile.value || updatingFeatures.value)
  const pageBusy = computed(() => operationBusy.value || restoringChat.value) // 页面是否忙碌，包含启动恢复阶段
  async function refreshSubagentMessages(chatId: string) {
    if (disposed || currentChat.value?.id !== chatId) return
    if (pageBusy.value) { pendingTaskRefresh.add(chatId); return }
    pendingTaskRefresh.delete(chatId)
    try {
      const page = await listMessages(chatId)
      if (disposed) return
      if (currentChat.value?.id !== chatId || pageBusy.value) { pendingTaskRefresh.add(chatId); return }
      const updated = new Map(page.items.map(item => [item.id, item]))
      const existing = messages.value.filter(item => !item.id.startsWith('pending-') || !page.items.some(saved => saved.role === item.role && saved.status === item.status && saved.content === item.content))
      const ids = new Set(existing.map(item => item.id))
      messages.value = existing.map(item => updated.get(item.id) ?? item).concat(page.items.filter(item => !ids.has(item.id)))
    } catch { /* 子任务刷新不覆盖输入或普通聊天错误。 */ }
  }
  watch(pageBusy, busy => { if (!busy) for (const id of [...pendingTaskRefresh]) void refreshSubagentMessages(id) })
  const inputDisabled = computed(() => sendingMessage.value || creatingChat.value || loadingOlderMessages.value || deletingChatId.value !== null || renamingChatId.value !== null) // 输入框禁用规则
  const sendDisabled = computed(() => !inputText.value.trim() || pageBusy.value) // 空白页也可发送，由页面先创建会话
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
  function startBlankChat(): boolean { // 只切回本地空白草稿，第一条消息发送时才创建数据库会话
    if (pageBusy.value) return false
    currentChat.value = null
    messages.value = []
    inputText.value = ''
    selectedAttachments.value = []
    pendingWebSearch.value = false
    errorMessage.value = ''
    hasOlderMessages.value = false
    nextBeforeSequence.value = null
    return true
  }
  async function createNewChat(knowledgeBaseIds: string[] = []): Promise<Chat | null> { // 首次发送可同时保存所选知识库
    if (pageBusy.value) return null
    creatingChat.value = true
    errorMessage.value = ''
    try {
      const chat = await createChat('新会话', knowledgeBaseIds, pendingWebSearch.value)
      currentChat.value = chat
      pendingWebSearch.value = false
      chats.value.unshift(chat)
      messages.value = []
      hasOlderMessages.value = false
      nextBeforeSequence.value = null
      inputText.value = ''
      selectedAttachments.value = [] // 新建会话成功后清理附件草稿
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
      pendingWebSearch.value = false
      messages.value = page.items
      hasOlderMessages.value = page.has_more
      nextBeforeSequence.value = page.next_before_sequence
      inputText.value = ''
      selectedAttachments.value = [] // 成功切换会话后清理原会话的附件草稿
      if (chat.title_status === 'generating' || (chat.title_status === 'pending' && page.items.some(item => item.role === 'assistant' && item.status === 'completed'))) watchAutoTitle(chat.id)
      return true
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '读取消息失败'
      return false
    } finally {
      loadingMessages.value = false
    }
  }
  async function setWebSearchEnabled(enabled: boolean): Promise<boolean> {
    if (pageBusy.value) return false
    if (!currentChat.value) { pendingWebSearch.value = enabled; return true }
    const chatId = currentChat.value.id
    updatingFeatures.value = true
    errorMessage.value = ''
    try {
      const updated = await setChatFeatures(chatId, enabled)
      if (currentChat.value?.id === chatId) currentChat.value = { ...currentChat.value, web_search_enabled: updated.web_search_enabled ?? false }
      chats.value = chats.value.map(chat => chat.id === chatId ? { ...chat, web_search_enabled: updated.web_search_enabled ?? false } : chat)
      return true
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '保存联网搜索设置失败'
      return false
    } finally { updatingFeatures.value = false }
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
      titlePolls.get(chatId)?.abort()
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
      titlePolls.get(chatId)?.abort()
      chats.value = chats.value.filter((item) => item.id !== chatId)
      if (currentChat.value?.id === chatId) {
        currentChat.value = null
        messages.value = []
        hasOlderMessages.value = false
        nextBeforeSequence.value = null
        inputText.value = ''
        selectedAttachments.value = [] // 删除当前会话时清理附件草稿
      }
      return true
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '删除会话失败'
      return false
    } finally {
      deletingChatId.value = null
    }
  }
  function addAttachment(file: FileInfo): boolean { // 添加已处理成功的文件，不读取正文
    if (pageBusy.value) return false
    if (file.status !== 'completed') {
      errorMessage.value = '只能添加处理完成的文件'
      return false
    }
    if (selectedAttachments.value.some((item) => item.file_id === file.id)) {
      errorMessage.value = '该文件已添加到本次问题'
      return false
    }
    if (currentChat.value?.files?.some(item => item.file_id === file.id && !item.deleted)) {
      errorMessage.value = '这份资料已在当前会话中，可以直接继续提问'
      return false
    }
    if (selectedAttachments.value.length >= 3) {
      errorMessage.value = '每条消息最多添加3份附件'
      return false
    }
    selectedAttachments.value.push({ file_id: file.id, filename: file.filename, size: file.size })
    errorMessage.value = ''
    return true
  }
  function removeAttachment(fileId: string, deleted: boolean = false) { // 后端已删除的文件需要清理草稿，即使此时已开始其他操作
    if (pageBusy.value && !deleted) return
    selectedAttachments.value = selectedAttachments.value.filter((item) => item.file_id !== fileId)
  }
  function markFileDeleted(fileId: string) { // 删除成功后立即更新已加载的历史消息，保留附件快照
    removeAttachment(fileId, true)
    for (const file of currentChat.value?.files ?? []) { if (file.file_id === fileId) file.deleted = true }
    for (const message of messages.value) {
      for (const file of message.attachments ?? []) {
        if (file.file_id === fileId) file.deleted = true
      }
    }
  }
  async function removeConversationFile(fileId: string) {
    const chat = currentChat.value
    if (!chat || pageBusy.value) return
    removingChatFile.value = true
    try {
      await removeChatFile(chat.id, fileId)
      chat.files = (chat.files ?? []).filter(item => item.file_id !== fileId)
      chats.value = chats.value.map(item => item.id === chat.id ? { ...item, files: chat.files } : item)
      errorMessage.value = ''
    } catch (cause) { errorMessage.value = cause instanceof Error ? cause.message : '移出会话资料失败' }
    finally { removingChatFile.value = false }
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
      attachments: [], // 当前只允许用户消息关联附件
      status: 'generating',
      error: null,
      chat_id: chatId,
      created_at: Date.now() / 1000,
      model: null,
    })
    messages.value.push(draft)
    try {
      const messageId = await streamChatReply(chatId, (text) => { // 接收并追加本次回答的正文片段
        if (!controller.signal.aborted) draft.content += text
      }, controller.signal, (event) => { // 工具事件直接更新当前助手消息，无需重新打开会话
        if (controller.signal.aborted) return
        if (event.type === 'tool_call') {
          draft.output.push({ type: 'function_call', call_id: event.callId, name: event.name, arguments: event.arguments, ...(event.displayName ? { display_name: event.displayName } : {}) })
        } else {
          draft.output.push({ type: 'function_call_output', call_id: event.callId, output: event.output, ...(event.durationMs !== undefined ? { duration_ms: event.durationMs } : {}) })
        }
      }, (event) => {
        if (controller.signal.aborted) return
        if (event.type === 'commentary') {
          if (event.text && draft.content.endsWith(event.text)) draft.content = draft.content.slice(0, -event.text.length)
          draft.output.push({ type: 'message', phase: 'commentary', role: 'assistant', content: [{ type: 'output_text', text: event.text }] })
        } else {
          const last = draft.output.at(-1) as { type?: string; text?: string } | undefined
          if (last?.type === 'reasoning') last.text = (last.text ?? '') + event.text
          else draft.output.push({ type: 'reasoning', text: event.text })
        }
      }, (durationMs) => {
        draft.output.push({ type: 'turn_metadata', duration_ms: durationMs ?? Math.round((Date.now() / 1000 - (draft.created_at ?? Date.now() / 1000)) * 1000) })
      })
      draft.id = messageId
      draft.status = 'completed'
      const chat = chats.value.find(item => item.id === chatId)
      if (chat?.title_status === 'pending' || chat?.title_status === 'generating') watchAutoTitle(chatId)
    } catch (error) {
      if (controller.signal.aborted) {
        draft.status = 'cancelled'
      } else {
        draft.status = 'failed'
        draft.error = error instanceof Error ? error.message : '生成回答失败'
        throw error
      }
    } finally {
      draft.updated_at = Date.now() / 1000
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
      const fileIds = selectedAttachments.value.map((file) => file.file_id) // 只发送文件 ID，由后端校验并保存关联
      const userMessage = await saveUserMessage(chat.id, text, fileIds)
      messages.value.push(userMessage)
      const files = new Map((chat.files ?? []).map(file => [file.file_id, file]))
      for (const file of userMessage.attachments ?? []) files.set(file.file_id, file)
      chat.files = [...files.values()] // 草稿清空后，已发送附件仍在会话资料中
      inputText.value = ''
      selectedAttachments.value = [] // 保存成功即清空；生成失败通过重试复用已保存的问题
      if (controller.signal.aborted) return // 更新已保存的问题，但不再启动模型请求，避免再次提交
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
  return { refreshSubagentMessages, inputText, selectedAttachments, messages, currentChat, chats, webSearchEnabled, updatingFeatures, setWebSearchEnabled, errorMessage, sendingMessage, generatingReply, creatingChat, loadingChats, loadingMessages, hasOlderMessages, nextBeforeSequence, loadingOlderMessages, restoringChat, deletingChatId, renamingChatId, operationBusy, pageBusy, inputDisabled, sendDisabled, canRetryReply, loadChats, startBlankChat, createNewChat, openChatSession, initializeChat, loadOlderMessages, renameChatSession, deleteChatSession, sendMessage, retryReply, stopReply, addAttachment, removeAttachment, markFileDeleted, removeConversationFile }
})
