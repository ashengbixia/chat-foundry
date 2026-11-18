<script setup lang="ts">
import { ref, watch } from 'vue'
import { createApiError } from '../../api/errors'
import { validateTextFile, UPLOAD_ACCEPT } from '../../api/files'
const props = defineProps<{ fileId: string; readonly?: boolean; disabled?: boolean }>()
const emit = defineEmits<{ changed: [fileId: string]; select: [fileId: string] }>()
const versions = ref<{ file_id: string; filename: string; revision: number; created_at: number }[]>([])
const error = ref(''), notice = ref(''), busy = ref(false)
const picker = ref<HTMLInputElement | null>(null)
let revision = 0
watch(() => props.fileId, async id => {
  const request = ++revision; versions.value = []; error.value = notice.value = ''
  try { const result = await fetch(`/api/files/${encodeURIComponent(id)}/versions`); if (!result.ok) throw await createApiError(result, '读取版本失败'); const items = await result.json(); if (request === revision) versions.value = items }
  catch (e) { if (request === revision) error.value = e instanceof Error ? e.message : '读取版本失败' }
}, { immediate: true })
async function upload(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]; input.value = ''
  if (!file || busy.value || props.readonly || props.disabled) return
  const invalid = validateTextFile(file); if (invalid) { error.value = invalid; return }
  const id = props.fileId; busy.value = true; error.value = notice.value = ''
  try { const form = new FormData(); form.append('file', file); const result = await fetch(`/api/files/${encodeURIComponent(id)}/versions`, { method: 'POST', body: form }); if (!result.ok) throw await createApiError(result, '上传新版本失败'); const data = await result.json(); if (props.fileId !== id) return; notice.value = data.unchanged ? '内容相同，已保留当前版本' : `新版本已保存，已更新 ${data.replaced_libraries} 个知识库；历史消息仍引用旧版本`; emit('changed', data.file_id) }
  catch (e) { if (props.fileId === id) error.value = e instanceof Error ? e.message : '上传失败' }
  finally { busy.value = false }
}
</script>
<template>
  <div class="document-versions">
    <div class="actions"><a :href="`/api/files/${encodeURIComponent(fileId)}/original`" target="_blank" rel="noopener">打开原文件</a><a :href="`/api/files/${encodeURIComponent(fileId)}/original?download=true`">下载原文件</a><button v-if="!readonly" type="button" :disabled="busy || disabled || (versions.length > 0 && versions.at(-1)?.file_id !== fileId)" @click="picker?.click()">{{ busy ? '正在上传…' : '上传新版本' }}</button><input ref="picker" hidden type="file" :accept="UPLOAD_ACCEPT" @change="upload" /></div>
    <small>PDF 可由浏览器预览；其他格式下载查看。上传新版本会更新知识库成员，不改写历史引用或当前会话附件。</small>
    <details v-if="versions.length > 1"><summary>版本记录 · {{ versions.length }}</summary><div v-for="item in versions" :key="item.file_id"><button type="button" :disabled="busy || disabled || item.file_id === fileId" @click="emit('select', item.file_id)">V{{ item.revision }} · {{ item.filename }}</button><a :href="`/api/files/${encodeURIComponent(item.file_id)}/original?download=true`">下载</a></div></details>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </div>
</template>
<style scoped>
.document-versions { padding:12px 0; font-size:11px; }.actions { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }a { color:#4b7b60; }button { border:1px solid #e1e8e3; background:#fff; color:#617768; border-radius:6px; padding:6px; }small { display:block; color:#8c9890; line-height:1.8; margin-top:8px; }details { margin-top:10px; }details div { display:flex; gap:12px; margin:6px 0; }[role=alert] { color:#a55; }
</style>
