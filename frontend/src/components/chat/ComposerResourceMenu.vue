<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { listFiles } from '../../api/files'
import { listKnowledge, listKnowledgeFiles } from '../../api/knowledge'
import type { KnowledgeBase, KnowledgeReference } from '../../api/knowledge'
import type { FileInfo, MessageAttachment } from '../../types/file'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ disabled?: boolean; uploading?: boolean; knowledgeDisabled?: boolean; attachments?: MessageAttachment[]; conversationFiles?: MessageAttachment[]; selectedKnowledge?: KnowledgeReference[]; webSearchEnabled?: boolean; webSearchDisabled?: boolean }>()
const emit = defineEmits<{ upload: []; attach: [file: FileInfo]; toggleKnowledge: [base: KnowledgeBase]; manageFiles: []; manageKnowledge: []; webLink: [url: string]; toggleWebSearch: [] }>()
const root = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const page = ref<'main' | 'files' | 'knowledge' | 'members' | 'web'>('main')
const webUrl = ref('')
const webInput = ref<HTMLInputElement | null>(null)
const query = ref('')
const files = ref<FileInfo[]>([])
const bases = ref<KnowledgeBase[]>([])
const activeBase = ref<KnowledgeBase | null>(null)
const loading = ref(false)
const error = ref('')
let controller: AbortController | null = null
const selectedIds = computed(() => new Set([...(props.attachments ?? []), ...(props.conversationFiles ?? [])].filter(file => !file.deleted).map(file => file.file_id)))
const visibleFiles = computed(() => files.value.filter(file => file.filename.toLowerCase().includes(query.value.trim().toLowerCase())))
const visibleBases = computed(() => bases.value.filter(base => base.name.toLowerCase().includes(query.value.trim().toLowerCase())))
const title = computed(() => page.value === 'files' ? '文件' : page.value === 'members' ? activeBase.value?.name : '知识库')
function close() { open.value = false; controller?.abort(); controller = null; loading.value = false }
function outside(event: PointerEvent) { if (event.target instanceof Node && !root.value?.contains(event.target)) close() }
onMounted(() => document.addEventListener('pointerdown', outside))
onUnmounted(() => { close(); document.removeEventListener('pointerdown', outside) })
watch(() => props.disabled || props.uploading, value => { if (value) close() })
function toggle() { if (props.disabled || props.uploading) return; if (open.value) close(); else { open.value = true; page.value = 'main'; error.value = '' } }
async function openWeb() { page.value = 'web'; webUrl.value = error.value = ''; await nextTick(); webInput.value?.focus() }
function toggleWebSearch() {
  if (props.disabled || props.webSearchDisabled) return
  emit('toggleWebSearch')
  close()
}
function addWebLink() {
  if (props.disabled) return
  try {
    const url = new URL(webUrl.value.trim())
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !['', '80', '443'].includes(url.port)) throw new Error()
    emit('webLink', url.href)
    close()
  } catch { error.value = '请填写不含登录凭据、使用80或443端口的完整HTTP或HTTPS链接' }
}
async function navigate(target: 'files' | 'knowledge' | 'members', base?: KnowledgeBase) {
  controller?.abort()
  const request = new AbortController()
  controller = request
  page.value = target
  activeBase.value = base ?? null
  query.value = error.value = ''
  files.value = []; bases.value = []
  loading.value = true
  await nextTick()
  searchInput.value?.focus()
  try {
    if (target === 'knowledge') { const result = await listKnowledge(request.signal); if (!request.signal.aborted) bases.value = result }
    else { const result = target === 'members' && base ? await listKnowledgeFiles(base.id, request.signal) : await listFiles(request.signal); if (!request.signal.aborted) files.value = result }
  } catch (cause) { if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : '读取资料失败' }
  finally { if (controller === request) { loading.value = false; controller = null } }
}
function back() { if (page.value === 'members') void navigate('knowledge'); else { controller?.abort(); page.value = 'main'; error.value = ''; loading.value = false } }
function choose(file: FileInfo) {
  if (props.disabled || file.status !== 'completed' || selectedIds.value.has(file.id)) return
  if ((props.attachments?.length ?? 0) >= 3) { error.value = '本次最多添加3份附件，请先移除一份'; return }
  emit('attach', file)
}
function selected(base: KnowledgeReference) { return props.selectedKnowledge?.some(item => item.id === base.id) }
function chooseBase(base: KnowledgeBase) {
  if (props.knowledgeDisabled) return
  if (!selected(base) && (props.selectedKnowledge?.length ?? 0) >= 5) { error.value = '每个会话最多选择5个知识库'; return }
  emit('toggleKnowledge', base)
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.stopPropagation(); close(); return }
  if (event.target instanceof HTMLInputElement) return
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  const items = Array.from(root.value?.querySelectorAll<HTMLButtonElement>('.resource-popup button:not(:disabled)') ?? [])
  if (!items.length) return
  event.preventDefault()
  const index = items.indexOf(document.activeElement as HTMLButtonElement)
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
  items[next]?.focus()
}
</script>

<template>
  <div ref="root" class="composer-resource-menu" @keydown="keydown">
    <button class="resource-trigger" type="button" :disabled="disabled || uploading" aria-label="添加资料和工具" title="添加资料或联网搜索" aria-haspopup="dialog" :aria-expanded="open" @click="toggle"><AppIcon name="plus" :size="21" /></button>
    <div v-if="open" class="resource-popup" role="dialog" aria-label="添加资料">
      <template v-if="page === 'main'">
        <button type="button" @click="close(); emit('upload')"><AppIcon name="attach" /><span>上传文件</span><small>TXT</small></button>
        <button type="button" @click="openWeb"><AppIcon name="globe" /><span>引用网页</span></button>
        <button type="button" :disabled="webSearchDisabled" :aria-pressed="!!webSearchEnabled" @click="toggleWebSearch"><AppIcon name="globe" /><span>联网搜索</span><AppIcon v-if="webSearchEnabled" name="check" :size="16" /><small v-else>开启</small></button>
        <div class="menu-separator"></div>
        <button type="button" @click="navigate('files')"><AppIcon name="file" /><span>添加附件</span><AppIcon name="chevron" :size="15" /></button>
        <button type="button" :disabled="knowledgeDisabled" @click="navigate('knowledge')"><AppIcon name="database" /><span>引用知识库</span><AppIcon name="chevron" :size="15" /></button>
        <div class="menu-separator"></div>
        <button type="button" class="secondary" @click="close(); emit('manageFiles')"><AppIcon name="folder" /><span>管理文件</span></button>
        <button type="button" class="secondary" @click="close(); emit('manageKnowledge')"><AppIcon name="settings" /><span>管理知识库</span></button>
      </template>
      <template v-else-if="page === 'web'">
        <button type="button" class="resource-heading" @click="back"><AppIcon name="back" :size="17" /><span>引用网页</span></button>
        <div class="web-link-form"><label for="composer-web-url">网页地址</label><input id="composer-web-url" ref="webInput" v-model="webUrl" type="url" placeholder="https://example.com/article" autocomplete="off" @keydown.enter.stop.prevent="addWebLink" /><p>先加入消息，发送后读取公开网页正文。</p><p v-if="error" class="resource-error" role="alert">{{ error }}</p><button type="button" :disabled="disabled || !webUrl.trim()" @click="addWebLink">添加到消息</button></div>
      </template>
      <template v-else>
        <button type="button" class="resource-heading" @click="back"><AppIcon name="back" :size="17" /><span>{{ title }}</span></button>
        <label class="resource-search"><AppIcon name="search" :size="17" /><input ref="searchInput" v-model="query" type="search" :placeholder="page === 'knowledge' ? '搜索知识库' : '搜索文件'" :aria-label="page === 'knowledge' ? '搜索知识库' : '搜索文件'" /></label>
        <p v-if="loading" class="resource-empty" role="status">正在加载…</p>
        <p v-if="error" class="resource-error" role="alert">{{ error }}</p>
        <div v-if="!loading" class="resource-list">
          <template v-if="page === 'knowledge'">
            <div v-for="base in visibleBases" :key="base.id" class="resource-base">
              <button type="button" :disabled="knowledgeDisabled" :aria-pressed="!!selected(base)" :title="selected(base) ? '从当前会话移除知识库' : '引用整个知识库'" @click="chooseBase(base)"><AppIcon name="database" :size="17" /><span>{{ base.name }}<small>{{ base.file_count }} 份文件</small></span><AppIcon v-if="selected(base)" name="check" :size="17" /></button>
              <button type="button" class="browse-base" :aria-label="`浏览 ${base.name} 的文件`" @click="navigate('members', base)"><AppIcon name="chevron" :size="16" /></button>
            </div>
            <p v-if="!visibleBases.length && !error" class="resource-empty">{{ query ? '没有匹配的知识库' : '还没有知识库，可先创建一套资料' }}</p>
          </template>
          <template v-else>
            <button v-for="file in visibleFiles" :key="file.id" type="button" :disabled="disabled || file.status !== 'completed' || selectedIds.has(file.id)" :class="{ selected: selectedIds.has(file.id) }" :title="file.filename" @click="choose(file)"><AppIcon name="file" :size="17" /><span>{{ file.filename }}</span><AppIcon v-if="selectedIds.has(file.id)" name="check" :size="16" /><small v-else-if="file.status !== 'completed'">{{ file.status === 'failed' ? '处理失败' : '处理中' }}</small></button>
            <p v-if="!visibleFiles.length && !error" class="resource-empty">{{ query ? '没有匹配的文件' : '还没有文件，上传后即可在这里选择' }}</p>
          </template>
        </div>
        <div class="resource-footer"><span>{{ page === 'knowledge' ? '点击引用整库 · 右侧浏览文件' : '点击添加，可连续选择' }}</span><button type="button" @click="close">完成</button></div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.composer-resource-menu { position: relative; flex-shrink: 0; }
.resource-trigger { display: grid; place-items: center; width: 34px; height: 34px; border: 0; border-radius: 9px; background: transparent; color: #555; }
.resource-trigger:hover:not(:disabled), .resource-trigger[aria-expanded='true'] { background: #f0f0f0; }
.resource-popup { position: absolute; left: 0; bottom: calc(100% + 12px); z-index: 24; width: min(350px, calc(100vw - 48px)); max-height: min(460px, 65dvh); overflow-y: auto; padding: 6px; border: 1px solid #e9e9e9; border-radius: 16px; background: #fff; box-shadow: 0 8px 32px #00000012, 0 2px 5px #00000008; color: #292929; }
.resource-popup button { display: flex; align-items: center; gap: 11px; width: 100%; min-height: 39px; padding: 9px 11px; border: 0; border-radius: 9px; background: transparent; color: inherit; text-align: left; font-size: 14px; }
.resource-popup button:hover:not(:disabled), .resource-popup button:focus-visible { background: #f5f5f5; }
.resource-popup button:disabled { opacity: .5; }.resource-popup button.selected { opacity: 1; background: #f6f6f6; color: #858585; }
.resource-popup svg { flex-shrink: 0; }.resource-popup button > span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.resource-popup small { color: #a0a0a0; font-size: 11px; }.resource-base small { display: block; margin-top: 3px; }
.menu-separator { height: 1px; margin: 5px 8px; background: #efefef; }.resource-popup button.secondary { font-size: 12px; color: #888; }
.resource-heading { font-weight: 500; }.resource-search { display: flex; gap: 10px; align-items: center; margin: 5px 10px 8px; color: #888; }
.resource-search input { width: 100%; min-width: 0; padding: 7px 0; border: 0; outline: 0; background: transparent; font-size: 14px; color: #333; }.resource-search input::placeholder { color: #aaa; }
.resource-list { max-height: min(280px, 35dvh); overflow-y: auto; }.resource-base { display: flex; align-items: center; }.resource-base > button:first-child { flex: 1; min-width: 0; }.resource-base .browse-base { width: 34px; flex-shrink: 0; padding: 9px; }
.resource-empty, .resource-error { padding: 12px; margin: 0; font-size: 12px; line-height: 1.7; color: #999; }.resource-error { color: #b25050; }
.resource-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 6px 8px 0; border-top: 1px solid #f2f2f2; margin-top: 6px; }.resource-footer > span { font-size: 10px; color: #999; }.resource-footer button { width: auto; min-height: 30px; font-size: 12px; color: #555; }
.web-link-form { padding: 10px; }.web-link-form label { display: block; margin-bottom: 8px; font-size: 12px; color: #777; }.web-link-form input { width: 100%; padding: 10px; border: 1px solid #e5e5e5; border-radius: 8px; font-size: 13px; }.web-link-form p { margin: 9px 0; font-size: 11px; line-height: 1.6; color: #999; }.web-link-form button { justify-content: center; margin-top: 12px; background: #f2f2f2; }.web-link-form .resource-error { padding: 0; color: #b25050; }
</style>
