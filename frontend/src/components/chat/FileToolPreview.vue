<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ result: string | null }>()
const data = computed(() => {
  try {
    const value = JSON.parse(props.result ?? '{}')
    if (!value.display_file || !Array.isArray(value.chunks)) return null
    return { chunks: value.chunks.filter((item: any) => typeof item?.content === 'string' && typeof item?.filename === 'string'), hasMore: value.has_more === true }
  } catch { return null }
})
</script>
<template>
  <details v-if="data" class="file-tool-preview" open><summary>提取正文预览</summary>
    <section v-for="(chunk, index) in data.chunks" :key="index"><strong>{{ chunk.filename }}</strong><small>字符 {{ chunk.start_offset }}～{{ chunk.end_offset ?? chunk.start_offset + Array.from(chunk.content).length }}</small><pre>{{ chunk.content }}</pre></section>
    <small v-if="data.hasMore">仅展示部分正文，可继续让助手翻页读取。</small>
  </details>
</template>
<style scoped>
.file-tool-preview { margin: 14px 0; border-left: 2px solid #c8dace; padding-left: 12px; color: #52675a; }.file-tool-preview summary { cursor: pointer; font-size: 12px; }strong { display: block; font-size: 12px; margin-top: 10px; }small { display: block; color: #8e9b93; font-size: 11px; margin: 6px 0; }pre { max-height: 360px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 13px; line-height: 1.8; }
</style>
