<script setup lang="ts">
import type { Chat } from '../../api/chats'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ chats: Chat[]; currentChatId: string | null; loading: boolean; creating: boolean; deletingChatId: string | null; renamingChatId: string | null; actionDisabled: boolean; openDisabled: boolean }>() // 接收会话列表、当前会话和按钮状态
const emit = defineEmits<{ create: []; open: [chat: Chat]; remove: [chat: Chat]; rename: [chat: Chat]; download: [chat: Chat] }>() // 只通知父组件操作意图，不在此处请求后端
const expandedChatId = ref<string | null>(null) // 同时只展开一条会话的操作
const historyRoot = ref<HTMLElement | null>(null)
const actionMenu = ref<HTMLElement | null>(null)
const position = ref({ left: '0px', top: '0px' })
const expandedChat = computed(() => props.chats.find(chat => chat.id === expandedChatId.value))
function close() { expandedChatId.value = null }
watch(() => props.currentChatId, close)
watch(() => props.actionDisabled, value => { if (value) close() })
function outside(event: PointerEvent) { if (event.target instanceof Node && !historyRoot.value?.contains(event.target) && !actionMenu.value?.contains(event.target)) close() }
function onScroll(event: Event) { if (!(event.target instanceof Node) || !actionMenu.value?.contains(event.target)) close() }
onMounted(() => { document.addEventListener('pointerdown', outside); document.addEventListener('scroll', onScroll, true); window.addEventListener('resize', close) })
onUnmounted(() => { document.removeEventListener('pointerdown', outside); document.removeEventListener('scroll', onScroll, true); window.removeEventListener('resize', close) })
function toggle(event: MouseEvent, chat: Chat) {
  if (props.actionDisabled) return
  if (expandedChatId.value === chat.id) { close(); return }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  position.value = { left: `${Math.max(8, Math.min(rect.right + 5, window.innerWidth - 228))}px`, top: `${Math.max(8, Math.min(rect.top, window.innerHeight - 150))}px` }
  expandedChatId.value = chat.id
}
function runAction(action: 'rename' | 'remove' | 'download', chat: Chat) {
  if (props.actionDisabled) return
  close()
  if (action === 'rename') emit('rename', chat)
  else if (action === 'download') emit('download', chat)
  else emit('remove', chat)
}
</script>

<template>
  <button class="new-chat" type="button" :disabled="actionDisabled" @click="emit('create')"><AppIcon name="plus" :size="19" />{{ creating ? '正在创建…' : '新建对话' }}<span>开始</span></button>
  <section ref="historyRoot" class="history" aria-label="历史会话" @keydown.esc="expandedChatId = null">
    <div class="section-heading"><span>最近对话</span><small>{{ chats.length }}</small></div>
    <p v-if="loading" class="empty-history">正在读取会话…</p>
    <p v-else-if="chats.length === 0" class="empty-history">还没有对话<br /><span>从一个问题开始，灵感会留在这里。</span></p>
    <ul v-else class="chat-list">
      <li v-for="chat in chats" :key="chat.id" class="chat-row" :class="{ current: currentChatId === chat.id }">
        <button class="chat-link" type="button" :disabled="openDisabled" :title="chat.title" :aria-current="currentChatId === chat.id ? 'true' : undefined" @click="emit('open', chat)"><AppIcon name="chat" :size="16" /><span>{{ chat.title }}</span></button>
        <button class="icon-button chat-more" type="button" :disabled="actionDisabled" :aria-label="`管理会话 ${chat.title}`" :aria-expanded="expandedChatId === chat.id" @click="toggle($event, chat)"><AppIcon name="more" :size="17" /></button>
      </li>
    </ul>
  </section>
  <Teleport to="body">
    <div v-if="expandedChat" ref="actionMenu" class="chat-actions" :style="position" role="menu" :aria-label="`管理会话 ${expandedChat.title}`" @keydown.esc.stop="close">
      <button type="button" role="menuitem" :disabled="actionDisabled" @click="runAction('download', expandedChat)"><AppIcon name="download" :size="17" />下载对话</button>
      <button type="button" role="menuitem" :disabled="actionDisabled" @click="runAction('rename', expandedChat)"><AppIcon name="edit" :size="17" />重命名</button>
      <div class="action-separator"></div>
      <button class="danger" type="button" role="menuitem" :disabled="actionDisabled" @click="runAction('remove', expandedChat)"><AppIcon name="trash" :size="17" />删除</button>
    </div>
  </Teleport>
</template>

<style scoped>
.new-chat { display: flex; align-items: center; gap: 10px; width: 100%; padding: 12px; border: 1px solid #e0e3e6; border-radius: 11px; background: #fff; font-size: 13px; font-weight: 600; box-shadow: 0 2px 3px #172b3a03; }
.new-chat:hover:not(:disabled) { border-color: #bbcfc6; background: #fafcfb; }
.new-chat > span { margin-left: auto; font-size: 10px; color: #91969e; font-weight: 400; }
.history { margin-top: 28px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; padding: 0 10px 12px; color: #8b9099; font-size: 11px; letter-spacing: .05em; }
.section-heading small { font-size: 10px; }
.chat-list { list-style: none; margin: 0; padding: 0; }
.chat-row { display: flex; flex-wrap: wrap; align-items: center; border-radius: 9px; margin: 3px 0; }
.chat-row:hover { background: #eceef1; }
.chat-row.current { background: #ededed; }
.chat-link { flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px; padding: 11px 9px; border: 0; background: transparent; text-align: left; font-size: 12px; color: #606771; }
.chat-link svg { flex-shrink: 0; opacity: .65; }
.chat-link span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.current .chat-link { color: #273f35; font-weight: 500; }
.chat-more { width: 26px; height: 28px; margin-right: 4px; opacity: 0; }
.chat-row:hover .chat-more, .chat-row:focus-within .chat-more, .chat-more[aria-expanded='true'] { opacity: 1; }
.chat-actions { position: fixed; z-index: 70; width: 220px; padding: 5px; border: 1px solid #e8e8e8; border-radius: 14px; background: white; box-shadow: 0 8px 30px #00000012, 0 2px 5px #00000008; }
.chat-actions button { display: flex; align-items: center; gap: 11px; width: 100%; padding: 10px 12px; border: 0; border-radius: 9px; background: transparent; color: #333; font-size: 14px; text-align: left; }
.chat-actions button:hover:not(:disabled), .chat-actions button:focus-visible { background: #f5f5f5; }
.chat-actions .danger { color: #b45353; }.action-separator { height: 1px; margin: 4px 8px; background: #efefef; }
.empty-history { padding: 8px 10px; color: #747b85; font-size: 12px; line-height: 1.9; }
.empty-history span { color: #9a9fa6; font-size: 11px; }
@media (hover: none) { .chat-more { opacity: 1; } }
</style>
