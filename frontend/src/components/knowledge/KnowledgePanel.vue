<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { addKnowledgeFiles, createKnowledge, deleteKnowledge, listKnowledge, listKnowledgeFiles, removeKnowledgeFile, updateKnowledge, updateKnowledgeIndex } from '../../api/knowledge'
import type { KnowledgeBase, KnowledgeReference } from '../../api/knowledge'
import { listFiles, rebuildFileIndex, uploadFile, validateTextFile } from '../../api/files'
import type { FileInfo } from '../../types/file'
import { createDialogBackdrop } from '../../utils/dialog'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ selected: KnowledgeReference[]; selectionDisabled: boolean; applySelection: (items: KnowledgeBase[]) => Promise<void> }>()
const emit = defineEmits<{ changed: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const uploadInput = ref<HTMLInputElement | null>(null)
const bases = ref<KnowledgeBase[]>([])
const allFiles = ref<FileInfo[]>([])
const members = ref<FileInfo[]>([])
const activeId = ref('')
const name = ref('')
const description = ref('')
const creating = ref(false)
const choosing = ref(false)
const chosen = ref<string[]>([])
const selectedFiles = ref<string[]>([])
const showFilePicker = ref(false)
const search = ref('')
const fileSearch = ref('')
const busy = ref(false)
const action = ref('')
const error = ref('')
const notice = ref('')
const dragDepth = ref(0)
const canDrop = computed(() => !!activeId.value && !creating.value && !busy.value)
const visibleBases = computed(() => bases.value.filter(item => item.name.toLowerCase().includes(search.value.trim().toLowerCase())))
const availableFiles = computed(() => allFiles.value.filter(file => !members.value.some(member => member.id === file.id) && file.filename.toLowerCase().includes(fileSearch.value.trim().toLowerCase())))
let controller: AbortController | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null
const backdrop = createDialogBackdrop(close)

function cancelDialog(event: Event) { // 文件选择器取消也会冒泡cancel，只有弹窗自身的Esc事件才关闭知识库
  if (event.target !== dialog.value) return
  event.preventDefault()
  close()
}
function close() {
  dragDepth.value = 0
  controller?.abort()
  controller = null
  busy.value = false
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  if (dialog.value?.open) dialog.value.close()
}
onUnmounted(close)
function schedulePolling() { // 只在当前库有后台任务时更新，不读取正文
  if (pollTimer) clearTimeout(pollTimer)
  if (!dialog.value?.open || !members.value.some(file => file.status === 'processing' || file.index_busy || file.index_status === 'indexing')) return
  pollTimer = setTimeout(() => { if (!busy.value && !dragDepth.value) void run('正在更新处理状态…', refresh, true); else schedulePolling() }, 2500)
}
async function run(label: string, operation: (signal: AbortSignal) => Promise<void>, quiet = false) {
  if (busy.value) return
  const request = new AbortController()
  controller = request
  busy.value = true
  action.value = label
  if (!quiet) error.value = notice.value = ''
  try { await operation(request.signal) }
  catch (cause) { if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : '知识库操作失败' }
  finally { if (controller === request) { busy.value = false; controller = null; action.value = ''; schedulePolling() } }
}
async function refresh(signal: AbortSignal) {
  const [catalog, files] = await Promise.all([listKnowledge(signal), listFiles(signal)])
  if (signal.aborted) return
  bases.value = catalog
  allFiles.value = files
  if (activeId.value && !catalog.some(item => item.id === activeId.value)) setActive(catalog[0] ?? null)
  const currentFiles = activeId.value ? await listKnowledgeFiles(activeId.value, signal) : []
  if (signal.aborted) return
  members.value = currentFiles
  chosen.value = chosen.value.filter(id => catalog.some(item => item.id === id))
}
function setActive(item: KnowledgeBase | null) {
  dragDepth.value = 0
  activeId.value = item?.id ?? ''
  name.value = item?.name ?? ''
  description.value = item?.description ?? ''
  members.value = []
  selectedFiles.value = []
  showFilePicker.value = false
  creating.value = !item
}
async function open(select = false) {
  if (dialog.value?.open) return
  choosing.value = select
  chosen.value = props.selected.map(item => item.id)
  search.value = fileSearch.value = ''
  setActive(null)
  dialog.value?.showModal()
  await run('正在加载知识库…', async signal => {
    await refresh(signal)
    if (signal.aborted) return
    setActive(bases.value.find(item => chosen.value.includes(item.id)) ?? bases.value[0] ?? null)
    if (activeId.value) members.value = await listKnowledgeFiles(activeId.value, signal)
  })
}
async function selectBase(item: KnowledgeBase) {
  if (busy.value) return
  setActive(item)
  await run('正在读取资料…', async signal => { const files = await listKnowledgeFiles(item.id, signal); if (!signal.aborted) members.value = files })
}
function toggleChoice(id: string) {
  if (props.selectionDisabled || busy.value) return
  if (chosen.value.includes(id)) chosen.value = chosen.value.filter(item => item !== id)
  else if (chosen.value.length < 5) chosen.value.push(id)
  else error.value = '每个会话最多选择5个知识库'
}
async function saveBase() {
  await run(creating.value ? '正在创建…' : '正在保存…', async signal => {
    const item = creating.value ? await createKnowledge(name.value.trim(), description.value.trim(), signal) : await updateKnowledge(activeId.value, name.value.trim(), description.value.trim(), signal)
    if (signal.aborted) return
    setActive(item)
    await refresh(signal)
    if (!signal.aborted) { notice.value = '已保存。可以上传资料或加入已有文件。'; emit('changed') }
  })
}
async function removeBase() {
  if (!window.confirm(`删除“${name.value}”？原文件和历史引用会保留，所有会话将解除对这个库的选择。`)) return
  await run('正在删除…', async signal => {
    await deleteKnowledge(activeId.value, signal)
    if (signal.aborted) return
    setActive(null)
    await refresh(signal)
    if (!signal.aborted) { emit('changed'); notice.value = '知识库已删除，原文件已保留。' }
  })
}
async function addExisting() {
  await run('正在加入资料…', async signal => {
    await addKnowledgeFiles(activeId.value, selectedFiles.value, signal)
    if (signal.aborted) return
    selectedFiles.value = []
    showFilePicker.value = false
    await refresh(signal)
    if (!signal.aborted) { emit('changed'); notice.value = '已加入知识库，继续复用原文件索引。' }
  })
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  await uploadFiles(files)
}
function isFileDrag(event: DragEvent) { return Array.from(event.dataTransfer?.types ?? []).some(type => type === 'Files' || type === 'application/x-chatfoundry-file') }
function dragEnter(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  dragDepth.value++
}
function dragOver(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = canDrop.value ? 'copy' : 'none'
}
function dragLeave() { dragDepth.value = Math.max(0, dragDepth.value - 1) }
async function dropFiles(event: DragEvent) { // 整个弹窗都可接收文件，避免浏览器直接打开文件导致离开页面
  dragDepth.value = 0
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (!event.dataTransfer) return
  if (!activeId.value || creating.value) { error.value = '请先创建或选择一个知识库，再拖入资料'; return }
  if (busy.value) { error.value = '正在处理资料，请完成后再拖入文件'; return }
  const fileId = event.dataTransfer.getData('application/x-chatfoundry-file')
  if (fileId) {
    if (!allFiles.value.some(file => file.id === fileId)) { error.value = '资料不在当前账号的文件列表中，请刷新后重试'; return }
    if (members.value.some(file => file.id === fileId)) { notice.value = '这份资料已在当前知识库中'; error.value = ''; return }
    if (members.value.length >= 200) { error.value = '每个知识库最多200份文件'; return }
    selectedFiles.value = [fileId]
    await addExisting()
    return
  }
  await uploadFiles(Array.from(event.dataTransfer.files))
}
function dragSavedFile(event: DragEvent, file: FileInfo) {
  if (busy.value || !event.dataTransfer) { event.preventDefault(); return }
  event.dataTransfer.setData('application/x-chatfoundry-file', file.id)
  event.dataTransfer.effectAllowed = 'copy'
}
async function uploadFiles(files: File[]) { // 点选和拖放共用校验、上传、加入知识库流程
  if (!files.length) return
  if (!activeId.value || creating.value) { error.value = '请先创建或选择一个知识库，再上传资料'; return }
  if (busy.value) { error.value = '正在处理资料，请稍后再试'; return }
  if (files.length > 200) { error.value = '每次最多上传200份文件'; return } // 最终容量由服务端按去重后的文件ID计算
  for (const file of files) { const issue = validateTextFile(file); if (issue) { error.value = `${file.name}：${issue}`; return } }
  const targetId = activeId.value // 本次批量上传固定目标，刷新或删除不能把后续文件加入另一个库
  await run('正在上传并加入知识库…', async signal => {
    let reused = 0
    for (const [index, file] of files.entries()) {
      action.value = `正在上传 ${index + 1}/${files.length}：${file.name}`
      const record = await uploadFile(file, signal)
      if (record.reused) reused++
      if (signal.aborted) return
      try { await addKnowledgeFiles(targetId, [record.id], signal) }
      catch (cause) { if (!signal.aborted) { emit('changed'); await refresh(signal); throw new Error(`${record.filename} 已上传到文件管理，但加入知识库失败：${cause instanceof Error ? cause.message : '请稍后重试'}。可从已有资料中重新加入。`) }; throw cause }
      if (signal.aborted) return
      emit('changed')
      await refresh(signal)
      if (signal.aborted) return
      if (activeId.value !== targetId) throw new Error('原知识库已不存在，已停止继续上传；已上传文件可在文件管理中查看。')
    }
    if (!signal.aborted) notice.value = reused ? `资料已加入，其中 ${reused} 份内容相同，已复用原资料及其索引；需要更新的索引在后台处理。` : '资料已加入，索引在后台自动建立。'
  })
}
async function removeMember(file: FileInfo) {
  await run('正在移出知识库…', async signal => {
    await removeKnowledgeFile(activeId.value, file.id, signal)
    if (signal.aborted) return
    await refresh(signal)
    if (!signal.aborted) { emit('changed'); notice.value = '已移出知识库，原文件仍在文件管理中。' }
  })
}
async function retryIndex(file: FileInfo) {
  await run('正在安排索引…', async signal => { await rebuildFileIndex(file.id, signal); if (!signal.aborted) { await refresh(signal); emit('changed') } })
}
async function updateAllIndexes() {
  await run('正在安排本库索引更新…', async signal => {
    const result = await updateKnowledgeIndex(activeId.value, signal)
    if (signal.aborted) return
    await refresh(signal)
    if (!signal.aborted) { emit('changed'); notice.value = `已安排 ${result.queued} 份更新，${result.ready} 份已就绪，${result.busy} 份处理中${result.failed ? `，${result.failed} 份文字未处理成功` : ''}。` }
  })
}
function fileStatus(file: FileInfo) {
  if (file.status === 'processing') return '正在提取文字'
  if (file.status === 'failed') return '文字处理失败'
  if (file.index_busy || file.index_status === 'indexing') return '正在建立索引'
  if (file.index_status === 'failed') return '索引失败'
  return file.index_status === 'ready' ? '可检索' : '待建立索引'
}
async function apply() {
  if (props.selectionDisabled) return
  await run('正在应用到会话…', async signal => {
    await props.applySelection(bases.value.filter(item => chosen.value.includes(item.id)))
    if (!signal.aborted) close()
  })
}
defineExpose({ open })
</script>

<template>
  <dialog ref="dialog" class="knowledge-dialog" :class="{ 'is-dragging': dragDepth > 0 }" aria-label="个人知识库" @cancel="cancelDialog" @close="close" @pointerdown="backdrop.pointerdown" @click="backdrop.click" @dragenter="dragEnter" @dragover="dragOver" @dragleave="dragLeave" @drop="dropFiles" @dragend="dragDepth = 0">
    <header class="dialog-header"><div><small>个人资料空间</small><h2>{{ choosing ? '为对话选择知识库' : '知识库' }}</h2></div><button class="icon-button" type="button" aria-label="关闭知识库" @click="close"><AppIcon name="close" /></button></header>
    <p class="intro">整理一套资料，在多个会话里持续使用。仅你可见，不会自动搜索未选择的知识库。</p>
    <div class="feedback" aria-live="polite"><span v-if="busy">{{ action }}</span><span v-else-if="error" class="error" role="alert">{{ error }}</span><span v-else>{{ notice }}</span></div>
    <div class="knowledge-body">
      <nav class="library-nav" aria-label="我的知识库">
        <button class="new-base" type="button" :disabled="busy" @click="setActive(null)"><AppIcon name="plus" :size="16" />新建知识库</button>
        <input v-model="search" type="search" placeholder="搜索知识库" aria-label="搜索知识库" />
        <div v-for="item in visibleBases" :key="item.id" class="library-item" :class="{ active: activeId === item.id }">
          <input v-if="choosing" type="checkbox" :checked="chosen.includes(item.id)" :disabled="busy || selectionDisabled || (!chosen.includes(item.id) && chosen.length >= 5)" :aria-label="`选择知识库 ${item.name}`" @change="toggleChoice(item.id)" />
          <button type="button" :disabled="busy" @click="selectBase(item)"><strong>{{ item.name }}</strong><small>{{ item.file_count }} 份资料</small></button>
        </div>
        <p v-if="!visibleBases.length && !busy" class="empty">{{ bases.length ? '没有匹配的知识库' : '从第一套资料开始' }}</p>
      </nav>
      <section class="library-detail" :aria-busy="busy">
        <form class="base-form" @submit.prevent="saveBase">
          <h3>{{ creating ? '新建知识库' : '知识库信息' }}</h3>
          <label>名称<input v-model="name" required maxlength="100" placeholder="例如：离心泵维护资料" :disabled="busy" /></label>
          <label>简介<textarea v-model="description" rows="2" maxlength="1000" placeholder="这套资料包含什么？（选填）" :disabled="busy"></textarea></label>
          <div class="form-actions"><button v-if="!creating" type="button" class="danger" :disabled="busy" @click="removeBase">删除知识库</button><button class="primary" type="submit" :disabled="busy || !name.trim()">{{ creating ? '创建知识库' : '保存信息' }}</button></div>
        </form>
        <template v-if="activeId && !creating">
          <button type="button" :disabled="busy || !members.length" @click="updateAllIndexes">更新本库索引</button>
          <button class="upload-dropzone" :class="{ 'drag-active': dragDepth > 0 }" type="button" :disabled="busy" @click="uploadInput?.click()">
            <AppIcon name="attach" :size="25" /><strong>{{ dragDepth ? (canDrop ? '松开，加入当前知识库' : '当前暂不可上传') : busy ? action : '把 TXT 文件拖到这里，或点击上传' }}</strong><small>支持一次拖入多份 · 单份最大 5 MB · 上传后自动建立索引</small>
          </button>
          <div class="member-heading"><div><h3>库内资料 <small>{{ members.length }} / 200</small></h3><p>移出仅解除关联，原文件和历史引用保留。</p></div><button type="button" :disabled="busy" @click="run('正在刷新…', refresh)">刷新</button></div>
          <div class="member-actions"><button type="button" :disabled="busy || members.length >= 200" @click="uploadInput?.click()">上传新资料</button><button type="button" :disabled="busy || members.length >= 200" @click="showFilePicker = !showFilePicker">{{ showFilePicker ? '收起文件选择' : '加入已有资料' }}</button><input ref="uploadInput" hidden type="file" accept=".txt" multiple aria-label="上传知识库资料" @change="upload" /></div>
          <div v-if="showFilePicker" class="existing-files">
            <input v-model="fileSearch" type="search" placeholder="搜索已有资料" aria-label="搜索可加入资料" />
            <p class="existing-hint">可勾选批量加入，也可把文件拖到上方上传区，直接复用已有索引。</p>
            <div class="existing-options"><label v-for="file in availableFiles" :key="file.id" :draggable="!busy" @dragstart="dragSavedFile($event, file)"><input v-model="selectedFiles" type="checkbox" :value="file.id" :disabled="busy" /><span>{{ file.filename }}<small>{{ fileStatus(file) }}</small></span></label><p v-if="!availableFiles.length" class="empty">没有可加入的资料，可以先上传。</p></div>
            <button class="primary" type="button" :disabled="busy || !selectedFiles.length" @click="addExisting">加入选中的 {{ selectedFiles.length }} 份资料</button>
          </div>
          <ul class="member-files"><li v-for="file in members" :key="file.id"><AppIcon name="file" :size="19" /><div><strong>{{ file.filename }}</strong><small :class="{ error: file.status === 'failed' || file.index_status === 'failed' }">{{ fileStatus(file) }}{{ file.index_error || file.error ? ` · ${file.index_error || file.error}` : '' }}</small></div><button v-if="file.status === 'completed' && !file.index_busy && file.index_status !== 'ready'" type="button" :disabled="busy" @click="retryIndex(file)">重试索引</button><button type="button" :disabled="busy" :aria-label="`移出 ${file.filename}`" @click="removeMember(file)">移出</button></li></ul>
          <div v-if="!members.length && !busy" class="empty-state"><AppIcon name="folder" :size="30" /><strong>把资料放进来</strong><p>可以直接拖入 TXT，也可以加入已有文件。空知识库不会提供检索依据。</p></div>
        </template>
      </section>
    </div>
    <footer class="dialog-footer"><small>{{ choosing ? `已选 ${chosen.length} / 5 · 应用于整个会话，随时可移除` : '单份 TXT 最大 5 MB，文件索引在各知识库之间复用' }}</small><button type="button" @click="close">{{ choosing ? '取消' : '完成' }}</button><button v-if="choosing" class="primary" type="button" :disabled="busy || selectionDisabled" @click="apply">应用到会话</button></footer>
  </dialog>
</template>

<style scoped>
.knowledge-dialog.is-dragging { border-color: #73a48a; box-shadow: 0 0 0 3px #73a48a40; }
.upload-dropzone { display: flex; flex-direction: column; align-items: center; gap: 9px; width: 100%; margin-bottom: 20px; padding: 24px 14px; border: 1px dashed #b5ccbd; border-radius: 12px; background: #f6faf7; text-align: center; color: #5b7d67; }
.upload-dropzone strong { font-size: 13px; font-weight: 500; overflow-wrap: anywhere; }.upload-dropzone small { line-height: 1.7; }.upload-dropzone.drag-active { border: 2px dashed #639b79; background: #eaf5ee; }
.existing-hint { color: #809086; font-size: 11px; line-height: 1.7; }.existing-options label[draggable='true'] { cursor: grab; }
.knowledge-dialog { width: min(960px, calc(100vw - 24px)); max-height: 90dvh; padding: 24px; border: 1px solid #dce5df; border-radius: 20px; color: #405146; overflow-y: auto; }.knowledge-dialog::backdrop { background: #15291e66; }
.dialog-header, .dialog-footer, .member-heading, .form-actions, .member-actions { display: flex; align-items: center; gap: 10px; }.dialog-header { justify-content: space-between; }.dialog-header small { font-size: 11px; color: #86958b; }h2 { margin: 6px 0; font-size: 23px; }h3 { margin: 0 0 12px; font-size: 14px; font-weight: 550; }.intro, .member-heading p { color: #809086; font-size: 12px; line-height: 1.7; }.feedback { min-height: 28px; color: #568067; font-size: 12px; line-height: 1.7; }.error { color: #b65d5d; }
.knowledge-body { display: grid; grid-template-columns: 225px minmax(0, 1fr); min-height: 400px; border-top: 1px solid #e9eeeb; }.library-nav { padding: 16px 16px 16px 0; border-right: 1px solid #e9eeeb; }.new-base { display: flex; justify-content: center; align-items: center; gap: 7px; width: 100%; margin-bottom: 12px; }.library-item { display: flex; align-items: center; gap: 6px; margin-top: 7px; padding: 2px 6px; border: 1px solid transparent; border-radius: 10px; }.library-item.active { background: #eff6f1; border-color: #d6e5db; }.library-item > button { flex: 1; min-width: 0; border: 0; background: transparent; text-align: left; overflow-wrap: anywhere; }.library-item strong { display: block; font-size: 13px; font-weight: 500; }small { color: #86958b; font-size: 11px; }.library-item small { display: block; margin-top: 6px; }.library-detail { padding: 20px 0 16px 24px; min-width: 0; }.base-form { margin-bottom: 24px; }.base-form label { display: block; margin-bottom: 12px; font-size: 12px; color: #6e8275; }input:not([type='checkbox']), textarea { width: 100%; padding: 10px; border: 1px solid #dce5df; border-radius: 8px; background: #fcfdfc; color: #405146; font-size: 13px; }.base-form input, textarea { margin-top: 7px; }textarea { resize: vertical; }.form-actions { justify-content: space-between; }button { padding: 9px 12px; border: 1px solid #dce5df; border-radius: 8px; background: #fff; color: #617768; font-size: 12px; }button:hover:not(:disabled) { background: #f0f6f2; }button.primary { background: #365c45; border-color: #365c45; color: #fff; }.danger { color: #b65d5d; }.member-heading { justify-content: space-between; border-top: 1px solid #e9eeeb; padding-top: 18px; }.member-heading h3 { margin-bottom: 5px; }.member-heading p { margin: 0 0 12px; }.member-actions { flex-wrap: wrap; }.existing-files { padding: 12px; margin-top: 12px; border: 1px solid #e1e9e4; border-radius: 10px; background: #f7faf8; }.existing-options { max-height: 180px; overflow-y: auto; padding: 8px 0; }.existing-options label { display: flex; align-items: center; gap: 8px; padding: 8px 0; font-size: 12px; overflow-wrap: anywhere; }.existing-options small { display: block; margin-top: 4px; }input[type='checkbox'] { accent-color: #365c45; flex-shrink: 0; }.member-files { list-style: none; padding: 0; }.member-files li { display: flex; align-items: center; gap: 9px; padding: 14px 0; border-bottom: 1px solid #edf1ee; }.member-files li > div { flex: 1; min-width: 0; overflow-wrap: anywhere; }.member-files strong { font-size: 12px; font-weight: 500; }.member-files small { display: block; margin-top: 5px; line-height: 1.6; }.member-files button { padding: 6px 8px; flex-shrink: 0; }.empty, .empty-state { color: #91a096; font-size: 12px; line-height: 1.8; text-align: center; }.empty-state { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 34px 12px; }.empty-state p { margin: 0; }.dialog-footer { position: sticky; bottom: -24px; justify-content: flex-end; border-top: 1px solid #e9eeeb; padding: 14px 0; background: #fff; }.dialog-footer small { flex: 1; line-height: 1.7; }
@media (max-width: 600px) { .knowledge-dialog { padding: 16px; }.knowledge-body { display: block; }.library-nav { border-right: 0; border-bottom: 1px solid #e9eeeb; padding: 12px 0; max-height: 230px; overflow-y: auto; }.library-detail { padding: 18px 0; }.dialog-footer { bottom: -16px; flex-wrap: wrap; }.dialog-footer small { flex-basis: 100%; }.member-files li { flex-wrap: wrap; }.member-files li > div { min-width: 60%; } }
</style>
