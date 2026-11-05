<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { deleteFile, getFile, getFileContent, listFiles, uploadFile, validateTextFile } from '../../api/files'
import type { FileInfo } from '../../types/file'

const props = defineProps<{ attachDisabled?: boolean; deleteDisabled?: boolean }>() // 聊天正在操作时禁止删除文件
const emit = defineEmits<{ attach: [file: FileInfo]; removed: [fileId: string] }>() // 删除成功后通知父页面清理待发送附件
const selectedFile = ref<File | null>(null) // 当前选择的本地文件
const uploadedFile = ref<FileInfo | null>(null) // 当前查看的后端文件，可以是新上传或历史文件
const files = ref<FileInfo[]>([]) // 已保存的文件列表，不包含正文
const loadingFiles = ref(false) // 是否正在读取列表
const deletingFileId = ref<string | null>(null) // 正在删除的文件，用于禁用操作和显示进度
const uploading = ref(false) // 是否正在上传并等待后端处理
const refreshing = ref(false) // 是否正在刷新处理状态
const loadingContent = ref(false) // 是否正在读取正文
const textContent = ref('') // 已提取的正文
const contentLoaded = ref(false) // 是否已成功读取本文件正文
const previewOpen = ref(false) // 是否展开正文预览
const errorMessage = ref('') // 文件区域自己的错误提示
const busy = computed(() => uploading.value || refreshing.value || loadingContent.value || loadingFiles.value || deletingFileId.value !== null) // 文件操作互斥，防止重复删除
const statusText = computed(() => { // 将后端状态转换为中文
  if (uploadedFile.value?.status === 'completed') return '处理完成'
  if (uploadedFile.value?.status === 'failed') return '处理失败'
  return '处理中'
})
let requestController: AbortController | null = null // 组件销毁时取消当前上传或查询请求
onMounted(loadFiles) // 页面打开后恢复文件列表，默认查看最近一份
onUnmounted(() => requestController?.abort()) // 不让已离开的页面继续等待请求

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
  if (!file || file.status !== 'completed' || busy.value || props.attachDisabled) return
  emit('attach', file)
}
function selectSavedFile(fileId: string) { // 点击文件名选中，同名文件用完整ID区分
  if (busy.value) return
  const file = files.value.find((item) => item.id === fileId) ?? null
  errorMessage.value = ''
  displayFile(file)
}
async function removeFile(file: FileInfo) { // 删除一份历史文件，失败时保留列表和预览
  if (busy.value || props.deleteDisabled || file.status === 'processing') return
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
async function refreshFile() { // 手动查看后端最新状态，不启动轮询
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
    <h2 id="file-panel-title">文件资料</h2>
    <p class="hint">支持UTF-8编码的TXT文件，最大5MB。可添加到本次问题，最多3份。发送后助手可按需读取附件内容。</p>
    <form class="upload-form" @submit.prevent="submitFile">
      <label>选择TXT文件 <input type="file" accept=".txt" :disabled="busy" @change="selectFile" /></label>
      <button type="submit" :disabled="!selectedFile || busy">{{ uploading ? '正在上传并处理…' : '上传' }}</button>
    </form>
    <p v-if="selectedFile">待上传：{{ selectedFile.name }}（{{ formatSize(selectedFile.size) }}）</p>
    <div class="file-actions">
      <button type="button" :disabled="busy" @click="loadFiles">{{ loadingFiles ? '正在加载文件列表…' : '刷新文件列表' }}</button>
      <span v-if="!files.length && !loadingFiles">暂无文件记录</span>
    </div>
    <ul v-if="files.length" class="saved-files" aria-label="历史文件">
      <li v-for="file in files" :key="file.id" :class="{ current: uploadedFile?.id === file.id }">
        <button type="button" class="file-name" :disabled="busy" :aria-pressed="uploadedFile?.id === file.id" @click="selectSavedFile(file.id)">{{ file.filename }} · {{ file.id.slice(0, 8) }}</button>
        <button type="button" class="delete-file" :disabled="busy || props.deleteDisabled || file.status === 'processing'" :aria-label="`删除文件 ${file.filename}`" :title="`删除 ${file.filename}`" @click="removeFile(file)">{{ deletingFileId === file.id ? '…' : '×' }}</button>
      </li>
    </ul>
    <p v-if="errorMessage" role="alert">{{ errorMessage }}</p>
    <div v-if="uploadedFile" class="file-result">
      <p><strong>{{ uploadedFile.filename }}</strong> · {{ formatSize(uploadedFile.size) }} · <span role="status">{{ statusText }}</span></p>
      <p class="file-id">文件ID：{{ uploadedFile.id }}</p>
      <p v-if="uploadedFile.error" role="alert">{{ uploadedFile.error }}</p>
      <div class="file-actions">
        <button type="button" :disabled="busy || props.attachDisabled || uploadedFile.status !== 'completed'" @click="attachFile">添加到本次问题</button>
        <button type="button" :disabled="busy" @click="refreshFile">{{ refreshing ? '正在刷新…' : '刷新状态' }}</button>
        <button type="button" :disabled="busy || uploadedFile.status !== 'completed'" @click="togglePreview">{{ loadingContent ? '正在读取正文…' : previewOpen ? '收起正文' : '查看正文' }}</button>
      </div>
      <pre v-if="previewOpen" class="file-preview" aria-label="提取出的文件正文">{{ textContent }}</pre>
    </div>
  </section>
</template>

<style scoped>
.file-panel { margin-top: 24px; padding: 16px; border: 1px solid #ccc; border-radius: 8px; }
h2 { margin: 0 0 12px; }
p { margin: 10px 0; overflow-wrap: anywhere; }
.hint, .file-id { color: #666; font-size: 14px; }
.upload-form, .file-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
input { max-width: 100%; }
.saved-files { list-style: none; max-height: 240px; overflow-y: auto; margin: 12px 0; padding: 0; }
.saved-files li { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; border: 1px solid #ddd; border-radius: 6px; }
.saved-files li.current { border-color: #2563eb; }
.file-name { flex: 1; min-width: 0; text-align: left; overflow-wrap: anywhere; background: transparent; border: 0; }
.delete-file { margin-right: 4px; color: #b91c1c; font-size: 20px; background: transparent; border: 0; }
button { padding: 8px 12px; }
.file-preview { max-height: 320px; overflow-y: auto; margin: 12px 0 0; padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; background: #f4f4f4; color: #222; border-radius: 6px; }
</style>
