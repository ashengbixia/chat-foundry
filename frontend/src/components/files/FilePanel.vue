<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { deleteFile, getFile, getFileContent, listFiles, rebuildFileIndex, uploadFile, validateTextFile } from '../../api/files'
import type { FileInfo } from '../../types/file'

const props = defineProps<{ attachDisabled?: boolean; deleteDisabled?: boolean; attachmentSlots?: number; selectedIds?: string[] }>() // 输入框上传与管理面板共用同一套文件操作
const emit = defineEmits<{ attach: [file: FileInfo]; removed: [fileId: string]; attachmentUpload: [active: boolean]; attachmentError: [message: string] }>()
const attachPicker = ref<HTMLInputElement | null>(null) // 输入框“＋”直接打开的本地文件选择器
const selectedFile = ref<File | null>(null) // 当前选择的本地文件
const uploadedFile = ref<FileInfo | null>(null) // 当前查看的后端文件，可以是新上传或历史文件
const files = ref<FileInfo[]>([]) // 已保存的文件列表，不包含正文
const search = ref('')
const filteredFiles = computed(() => files.value.filter((file) => file.filename.toLowerCase().includes(search.value.trim().toLowerCase())))
function canAttach(file: FileInfo) { return !busy.value && !props.attachDisabled && file.status === 'completed' && !props.selectedIds?.includes(file.id) && (props.attachmentSlots ?? 3) > 0 }
function startDrag(event: DragEvent, file: FileInfo) { // 只传文件ID，接收时从当前账号的文件列表重新确认
  if (!canAttach(file) || !event.dataTransfer) { event.preventDefault(); return }
  event.dataTransfer.setData('application/x-chatfoundry-file', file.id)
  event.dataTransfer.effectAllowed = 'copy'
}
function attachById(fileId: string) {
  const file = files.value.find((item) => item.id === fileId)
  if (!file) { attachmentError('该文件已不在资料列表中，请刷新后重试'); return }
  if (!canAttach(file)) { attachmentError(props.selectedIds?.includes(file.id) ? '这份资料已在待发送附件或会话资料中，无需重复添加' : '文件暂不可添加，请检查处理状态和附件数量'); return }
  emit('attach', file)
}
const loadingFiles = ref(false) // 是否正在读取列表
const deletingFileId = ref<string | null>(null) // 正在删除的文件，用于禁用操作和显示进度
const uploading = ref(false) // 是否正在上传并等待后端处理
const refreshing = ref(false) // 是否正在刷新处理状态
const rebuildingIndex = ref(false) // 是否正在提交索引请求，不代表后台索引已经完成
const loadingContent = ref(false) // 是否正在读取正文
const textContent = ref('') // 已提取的正文
const contentLoaded = ref(false) // 是否已成功读取本文件正文
const previewOpen = ref(false) // 是否展开正文预览
const errorMessage = ref('') // 文件区域自己的错误提示
const pollingError = ref('') // 自动刷新失败单独提示，不覆盖上传、删除等操作的错误
const busy = computed(() => uploading.value || refreshing.value || rebuildingIndex.value || loadingContent.value || loadingFiles.value || deletingFileId.value !== null) // 文件操作互斥
const statusText = computed(() => { // 将后端状态转换为中文
  if (uploadedFile.value?.status === 'completed') return '处理完成'
  if (uploadedFile.value?.status === 'failed') return '处理失败'
  return '处理中'
})
let requestController: AbortController | null = null // 组件销毁时取消当前上传或查询请求
let pollingTimer: ReturnType<typeof setTimeout> | null = null // 每次请求结束后再安排下一次，避免请求重叠
let pollingController: AbortController | null = null // 后台刷新独立取消，不占用页面操作状态
let mounted = false // 仅在组件挂载后安排刷新，卸载后不再启动请求
const hasPendingFiles = computed(() => files.value.some((file) => file.status === 'processing' || file.index_busy || file.index_status === 'indexing')) // 待手动重建的旧索引不需要持续查询
function attachmentError(message: string) { errorMessage.value = message; emit('attachmentError', message) } // 面板隐藏时也能在聊天区看见失败原因
function pickAttachments() {
  if (busy.value || props.attachDisabled) { attachmentError('文件区域正在处理其他操作，请稍后再试'); return }
  if ((props.attachmentSlots ?? 3) <= 0) { attachmentError('每条消息最多添加3份附件，请先移除一份'); return }
  attachPicker.value?.click()
}
async function uploadAttachments(event: Event) { // 选择后立即上传，成功的文件直接加入本次问题
  const input = event.target as HTMLInputElement
  const selected = Array.from(input.files ?? [])
  input.value = ''
  await uploadSelectedFiles(selected)
}
async function uploadSelectedFiles(selected: File[]) { // 文件选择、拖放和粘贴共用校验与上传逻辑
  if (!selected.length || busy.value || props.attachDisabled) return
  if (selected.length > (props.attachmentSlots ?? 3)) { attachmentError(`本次还可添加${props.attachmentSlots ?? 3}份附件，请减少选择数量`); return }
  for (const file of selected) { const error = validateTextFile(file); if (error) { attachmentError(`${file.name}：${error}`); return } }
  const controller = new AbortController()
  requestController = controller
  uploading.value = true
  errorMessage.value = ''
  emit('attachmentUpload', true)
  try {
    for (const file of selected) {
      const record = await uploadFile(file, controller.signal)
      if (controller.signal.aborted) return
      files.value = [record, ...files.value.filter((item) => item.id !== record.id)]
      if (!uploadedFile.value) displayFile(record)
      emit('attach', record)
    }
  } catch (error) {
    if (!controller.signal.aborted) attachmentError(error instanceof Error ? error.message : '上传附件失败')
  } finally {
    uploading.value = false
    requestController = null
    emit('attachmentUpload', false)
  }
}
defineExpose({ pickAttachments, uploadSelectedFiles, attachById, refreshFiles: loadFiles })
onMounted(async () => { mounted = true; await loadFiles() }) // 页面打开后恢复文件列表，默认查看最近一份
onUnmounted(() => { // 离开页面时同时停止计时器、后台查询及当前操作
  mounted = false
  stopPolling()
  requestController?.abort()
})
watch([hasPendingFiles, busy], schedulePolling, { flush: 'sync' }) // 状态完成后停止，操作期间暂停，操作结束后自动恢复

function stopPolling() { // 停止等待和正在进行的后台查询
  if (pollingTimer !== null) clearTimeout(pollingTimer)
  pollingTimer = null
  pollingController?.abort()
}
function schedulePolling() { // 有后台任务时每2秒刷新，不因为自动查询禁用按钮
  if (pollingTimer !== null) clearTimeout(pollingTimer)
  pollingTimer = null
  if (!hasPendingFiles.value) pollingError.value = ''
  if (!mounted || busy.value || !hasPendingFiles.value) {
    pollingController?.abort()
    return
  }
  if (pollingController === null) pollingTimer = setTimeout(pollFileStatuses, 2000)
}
async function pollFileStatuses() { // 只刷新文件基本信息，保留当前选择和已展开的正文
  pollingTimer = null
  if (!mounted || busy.value || !hasPendingFiles.value || pollingController !== null) return
  const controller = new AbortController() // 当前后台查询，用户操作开始时会取消它
  pollingController = controller
  try {
    const records = await listFiles(controller.signal)
    if (!mounted || controller.signal.aborted || busy.value) return
    const currentId = uploadedFile.value?.id // 采用响应到达时的选择，不能跳回查询发起时的旧文件
    files.value = records
    displayFile(records.find((item) => item.id === currentId) ?? records[0] ?? null)
    pollingError.value = ''
  } catch (error) {
    if (mounted && !controller.signal.aborted) pollingError.value = '状态自动刷新失败，稍后会重试，也可以手动刷新。'
  } finally {
    pollingController = null
    schedulePolling()
  }
}

function indexStatusText(file: FileInfo): string { // 旧响应缺少字段时按待建立索引处理
  if (file.index_status === 'ready') return '可检索'
  if (file.index_status === 'indexing') return '正在建立索引'
  if (file.index_status === 'failed') return '索引失败'
  return file.index_busy ? '等待建立索引' : '待建立索引'
}
async function rebuildIndex() { // 仅提交后台任务，原文和预览保持不变
  const file = uploadedFile.value
  if (!file || file.status !== 'completed' || file.index_busy || file.index_status === 'indexing' || busy.value || props.deleteDisabled) return
  const controller = new AbortController()
  requestController = controller
  rebuildingIndex.value = true
  errorMessage.value = ''
  try {
    const record = await rebuildFileIndex(file.id, controller.signal)
    if (controller.signal.aborted) return
    uploadedFile.value = record
    files.value = files.value.map((item) => item.id === record.id ? record : item)
  } catch (error) {
    if (!controller.signal.aborted) errorMessage.value = error instanceof Error ? error.message : '建立索引失败'
  } finally {
    rebuildingIndex.value = false
    requestController = null
  }
}

function displayFile(file: FileInfo | null) { // 切换文件时清理旧预览，同一份已完成文件保留预览
  const changed = uploadedFile.value?.id !== file?.id // 判断文件是否已经切换
  uploadedFile.value = file
  if (changed || file?.status !== 'completed') {
    textContent.value = ''
    contentLoaded.value = false
    previewOpen.value = false
  }
}
function attachFile() { // 只有处理完成的文件才能加入本次问题
  const file = uploadedFile.value
  if (file && canAttach(file)) emit('attach', file)
}
function selectSavedFile(fileId: string) { // 点击文件名选中，同名文件用完整ID区分
  if (busy.value) return
  const file = files.value.find((item) => item.id === fileId) ?? null
  errorMessage.value = ''
  displayFile(file)
}
async function removeFile(file: FileInfo) { // 删除一份历史文件，失败时保留列表和预览
  if (busy.value || props.deleteDisabled || file.status === 'processing' || file.index_busy || file.index_status === 'indexing') return
  if (!window.confirm(`确定永久删除文件“${file.filename}”及其切片吗？`)) return
  const controller = new AbortController()
  requestController = controller
  deletingFileId.value = file.id
  errorMessage.value = ''
  try {
    await deleteFile(file.id, controller.signal)
    if (controller.signal.aborted) return
    files.value = files.value.filter((item) => item.id !== file.id)
    if (uploadedFile.value?.id === file.id) displayFile(files.value[0] ?? null)
    emit('removed', file.id)
  } catch (error) {
    if (!controller.signal.aborted) errorMessage.value = error instanceof Error ? error.message : '删除文件失败'
  } finally {
    deletingFileId.value = null
    requestController = null
  }
}
async function loadFiles() { // 初始化和手动刷新共用，失败时保留已有列表和预览
  if (busy.value) return
  const controller = new AbortController() // 当前列表请求的取消控制器
  requestController = controller
  loadingFiles.value = true
  errorMessage.value = ''
  try {
    const records = await listFiles(controller.signal)
    if (controller.signal.aborted) return
    const currentId = uploadedFile.value?.id // 刷新时优先保留正在查看的文件
    files.value = records
    displayFile(records.find((item) => item.id === currentId) ?? records[0] ?? null)
  } catch (error) {
    if (!controller.signal.aborted) errorMessage.value = error instanceof Error ? error.message : '读取文件列表失败'
  } finally {
    loadingFiles.value = false
    requestController = null
  }
}
function formatSize(size: number): string { // 将字节转换成容易阅读的大小
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}
function selectFile(event: Event) { // 选中文件时先检查扩展名和大小，不立即上传
  if (busy.value) return
  const input = event.target as HTMLInputElement // 当前文件选择框
  const file = input.files?.[0] ?? null // 用户取消选择时可能没有文件
  selectedFile.value = null
  errorMessage.value = ''
  if (!file) return
  const error = validateTextFile(file) // 前端检查不能替代后端编码校验
  if (error) {
    errorMessage.value = error
    input.value = ''
    return
  }
  selectedFile.value = file
}
async function submitFile() { // 上传成功才替换现有展示，失败时保留上一份文件及预览
  const file = selectedFile.value // 本次准备上传的文件
  if (!file || busy.value) return
  const controller = new AbortController() // 本次上传的取消控制器
  requestController = controller
  uploading.value = true
  errorMessage.value = ''
  try {
    const record = await uploadFile(file, controller.signal)
    if (controller.signal.aborted) return
    uploadedFile.value = record
    files.value = [record, ...files.value.filter((item) => item.id !== record.id)] // 上传成功后立即加入列表，避免重复
    textContent.value = ''
    contentLoaded.value = false
    previewOpen.value = false
  } catch (error) {
    if (!controller.signal.aborted) errorMessage.value = error instanceof Error ? error.message : '上传文件失败'
  } finally {
    uploading.value = false
    requestController = null
  }
}
async function refreshFile() { // 手动查看所选文件最新状态，后台任务仍由自动刷新跟进
  const file = uploadedFile.value // 本次查询的文件
  if (!file || busy.value) return
  const controller = new AbortController()
  requestController = controller
  refreshing.value = true
  errorMessage.value = ''
  try {
    const record = await getFile(file.id, controller.signal)
    if (controller.signal.aborted) return
    uploadedFile.value = record
    files.value = files.value.map((item) => item.id === record.id ? record : item) // 同步列表中的状态
    if (record.status !== 'completed') {
      textContent.value = ''
      contentLoaded.value = false
      previewOpen.value = false
    }
  } catch (error) {
    if (!controller.signal.aborted) errorMessage.value = error instanceof Error ? error.message : '读取文件信息失败'
  } finally {
    refreshing.value = false
    requestController = null
  }
}
async function togglePreview() { // 按需读取正文，再次展开同一文件时直接使用已读取的内容
  const file = uploadedFile.value
  if (!file || file.status !== 'completed' || busy.value) return
  if (contentLoaded.value) {
    previewOpen.value = !previewOpen.value
    return
  }
  const controller = new AbortController()
  requestController = controller
  loadingContent.value = true
  errorMessage.value = ''
  try {
    const content = await getFileContent(file.id, controller.signal)
    if (controller.signal.aborted) return
    textContent.value = content.text_content
    contentLoaded.value = true
    previewOpen.value = true
  } catch (error) {
    if (!controller.signal.aborted) errorMessage.value = error instanceof Error ? error.message : '读取文件正文失败'
  } finally {
    loadingContent.value = false
    requestController = null
  }
}
</script>

<template>
  <section class="file-panel" aria-labelledby="file-panel-title" :aria-busy="busy">
    <input ref="attachPicker" type="file" accept=".txt" multiple hidden aria-label="直接上传聊天附件" @change="uploadAttachments" />
    <h2 id="file-panel-title">把资料带进对话</h2>
    <p class="hint">支持 UTF-8 TXT，最大 5MB。上传后自动建立向量索引，状态会自动更新。</p>
    <form class="upload-form" @submit.prevent="submitFile">
      <label class="file-picker" :class="{ disabled: busy }"><span class="upload-symbol" aria-hidden="true">＋</span><strong>{{ selectedFile ? '重新选择文件' : '选择 TXT 文件' }}</strong><small>每次问题最多添加 3 份资料</small><input type="file" accept=".txt" :disabled="busy" aria-label="选择TXT文件" @change="selectFile" /></label>
      <button type="submit" :disabled="!selectedFile || busy">{{ uploading ? '正在上传并处理…' : '上传' }}</button>
    </form>
    <p v-if="selectedFile">待上传：{{ selectedFile.name }}（{{ formatSize(selectedFile.size) }}）</p>
    <div class="file-actions">
      <span class="list-heading">已保存的资料 <small>{{ files.length }}</small></span>
      <button class="refresh-files" type="button" :disabled="busy" @click="loadFiles">{{ loadingFiles ? '加载中…' : '刷新列表' }}</button>
    </div>
    <input v-if="files.length" v-model="search" class="file-search" type="search" placeholder="搜索已上传资料…" aria-label="搜索已上传资料" />
    <p v-if="files.length" class="hint">拖到聊天区即可添加，也可以点击文件旁的 ＋。</p>
    <ul v-if="files.length" class="saved-files" aria-label="历史文件">
      <li v-for="file in filteredFiles" :key="file.id" :class="{ current: uploadedFile?.id === file.id }" :draggable="canAttach(file)" @dragstart="startDrag($event, file)">
        <button type="button" class="file-name" :disabled="busy" :aria-pressed="uploadedFile?.id === file.id" :title="`${file.filename} · ${file.id}`" @click="selectSavedFile(file.id)"><span class="file-type">TXT</span><span>{{ file.filename }}<small>{{ formatSize(file.size) }} · {{ file.status === 'completed' ? indexStatusText(file) : file.status === 'failed' ? '处理失败' : '处理中' }}</small></span></button>
        <button type="button" class="add-file" :disabled="!canAttach(file)" :aria-label="props.selectedIds?.includes(file.id) ? `已添加 ${file.filename}` : `添加 ${file.filename} 到问题`" :title="props.selectedIds?.includes(file.id) ? '已添加到本次问题' : '添加到本次问题'" @click="attachById(file.id)">{{ props.selectedIds?.includes(file.id) ? '✓' : '+' }}</button>
        <button type="button" class="delete-file" :disabled="busy || props.deleteDisabled || file.status === 'processing' || file.index_busy || file.index_status === 'indexing'" :aria-label="`删除文件 ${file.filename}`" :title="file.index_busy || file.index_status === 'indexing' ? '索引任务完成后可删除' : `删除 ${file.filename}`" @click="removeFile(file)">{{ deletingFileId === file.id ? '…' : '×' }}</button>
      </li>
    </ul>
    <p v-if="files.length && !filteredFiles.length" class="hint">没有找到匹配的资料，试试其他关键词。</p>
    <div v-else-if="!loadingFiles" class="empty-files">这里还没有资料<small>上传第一份文件，建立你的对话资料库。</small></div>
    <p v-if="errorMessage" role="alert">{{ errorMessage }}</p>
    <p v-if="uploadedFile?.reused" role="status">同内容资料已存在，已复用“{{ uploadedFile.filename }}”及其索引。</p>
    <p v-if="pollingError" class="hint" role="status">{{ pollingError }}</p>
    <div v-if="uploadedFile" class="file-result">
      <p><strong>{{ uploadedFile.filename }}</strong> · {{ formatSize(uploadedFile.size) }} · <span role="status">{{ statusText }}</span></p>
      <details class="file-id"><summary>文件信息</summary><p>文件ID：{{ uploadedFile.id }}</p></details>
      <p v-if="uploadedFile.error" role="alert">{{ uploadedFile.error }}</p>
      <p v-if="uploadedFile.status === 'completed'" class="index-status" role="status">向量索引：{{ indexStatusText(uploadedFile) }}</p>
      <p v-if="uploadedFile.index_error" role="alert">{{ uploadedFile.index_error }}</p>
      <p v-if="uploadedFile.index_busy || uploadedFile.index_status === 'indexing'" class="hint">索引在后台执行，状态每2秒自动更新。首次使用可能需要下载模型。</p>
      <div class="file-actions">
        <button type="button" :disabled="!canAttach(uploadedFile)" @click="attachFile">{{ props.selectedIds?.includes(uploadedFile.id) ? '已添加到问题' : '添加到本次问题' }}</button>
        <button type="button" :disabled="busy" @click="refreshFile">{{ refreshing ? '正在刷新…' : '刷新状态' }}</button>
        <button type="button" :disabled="busy || uploadedFile.status !== 'completed'" @click="togglePreview">{{ loadingContent ? '正在读取正文…' : previewOpen ? '收起正文' : '查看正文' }}</button>
        <button type="button" :disabled="busy || props.deleteDisabled || uploadedFile.status !== 'completed' || uploadedFile.index_busy || uploadedFile.index_status === 'indexing'" @click="rebuildIndex">{{ rebuildingIndex ? '正在提交…' : uploadedFile.index_status === 'ready' ? '重建索引' : '建立 / 重试索引' }}</button>
      </div>
      <pre v-if="previewOpen" class="file-preview" aria-label="提取出的文件正文">{{ textContent }}</pre>
    </div>
  </section>
</template>

<style scoped>
.file-panel { padding-top: 12px; }
.file-search { width: 100%; margin-top: 12px; padding: 9px 11px; border: 1px solid #dce5df; border-radius: 8px; background: #fff; font-size: 12px; }
.saved-files li[draggable='true'], .saved-files li[draggable='true'] .file-name { cursor: grab; }
.add-file { border: 0; background: transparent; padding: 7px; font-size: 17px; }
h2 { margin: 0 0 8px; font-size: 12px; font-weight: 500; color: #5f6b63; }
p { margin: 10px 0; overflow-wrap: anywhere; font-size: 11px; line-height: 1.8; }
.hint { color: #969ea5; font-size: 11px; }
.file-id { color: #9aa39c; font-size: 10px; margin: 12px 0; }
.file-id summary { cursor: pointer; }
.upload-form, .file-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.upload-form { margin: 17px 0 24px; }
.file-picker { position: relative; display: flex; flex-direction: column; align-items: center; gap: 8px; width: 100%; padding: 20px 10px; border: 1px dashed #d7dfd9; border-radius: 12px; background: #f7faf7; cursor: pointer; }
.file-picker:hover { border-color: #97b6a1; }
.file-picker:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.file-picker.disabled { opacity: .5; cursor: not-allowed; }
.file-picker strong { color: #607a68; font-size: 12px; font-weight: 500; }
.file-picker small { color: #a0aaa3; font-size: 10px; }
.upload-symbol { font-size: 23px; color: #8da693; }
.file-picker input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.upload-form > button { width: 100%; background: #edf4ef; color: #52725c; border-color: #e0e9e2; }
.list-heading { flex: 1; color: #868f88; font-size: 10px; }
.list-heading small { margin-left: 5px; color: #a7afa9; }
.refresh-files { padding: 4px 0; border: 0; background: transparent; font-size: 10px; color: #94a198; }
.saved-files { list-style: none; max-height: 280px; overflow-y: auto; margin: 12px 0; padding: 0; }
.saved-files li { display: flex; align-items: center; gap: 3px; margin-bottom: 7px; border: 1px solid #eaedeb; border-radius: 10px; background: #fff; }
.saved-files li.current { border-color: #c6d7cb; background: #f3f8f4; }
.file-name { flex: 1; min-width: 0; display: flex; align-items: center; gap: 9px; text-align: left; overflow-wrap: anywhere; background: transparent; border: 0; padding: 12px 9px; font-size: 11px; color: #5e6d63; }
.file-name > span:last-child { min-width: 0; }
.file-name small { display: block; color: #9ca69f; font-size: 9px; margin-top: 5px; }
.file-type { padding: 9px 4px; background: #edf0ed; border: 1px solid #e2e8e3; border-radius: 5px; color: #84958a; font-size: 8px; flex-shrink: 0; }
.delete-file { margin-right: 5px; color: #a7aaa8; font-size: 16px; padding: 5px; background: transparent; border: 0; }
.delete-file:hover:not(:disabled) { color: #b45353; background: #f9eded; }
button { padding: 8px 10px; border: 1px solid #e4e9e5; border-radius: 7px; background: #fff; color: #79877e; font-size: 10px; }
button:hover:not(:disabled) { background: #f0f5f1; }
.empty-files { text-align: center; padding: 32px 6px; font-size: 11px; color: #a1a8a3; }
.empty-files small { display: block; margin-top: 7px; font-size: 10px; color: #b0b5b1; }
.file-result { padding-top: 12px; margin-top: 18px; border-top: 1px solid var(--border); }
.file-result > p:first-child { color: #7c887f; }
.file-actions { margin-top: 10px; }
.file-preview { max-height: 320px; overflow-y: auto; margin: 12px 0 0; padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; background: #f2f5f2; color: #637168; border-radius: 8px; font-size: 11px; line-height: 1.8; }
[role='alert'] { color: #b56c6c; }
.file-name { font-size: 12px; }.file-name small { font-size: 11px; color: #7c8b81; }
.hint { font-size: 12px; color: #7f8c84; }h2 { font-size: 14px; }button { font-size: 12px; }
.add-file { font-size: 17px; }.delete-file { font-size: 16px; }
</style>
