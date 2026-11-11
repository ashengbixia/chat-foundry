<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref } from 'vue'
import { getFile, getFileContent } from '../../api/files'
import { ApiError } from '../../api/errors'
import { getCitationSources, locateCitation } from '../../utils/citations'
import type { CitationSource } from '../../utils/citations'
import { renderMarkdown } from '../../utils/markdown'
import { createDialogBackdrop } from '../../utils/dialog'

const props = defineProps<{ content: string; output: unknown }>() // 正文与完整工具记录来自同一条助手消息
const sources = computed(() => getCitationSources(props.output)) // 不从正文中的文件名猜测来源
const html = computed(() => renderMarkdown(props.content, sources.value.map((source) => source.id))) // 只让真实编号可点击
const selected = ref<CitationSource | null>(null) // 当前查看的历史原文快照
const dialog = ref<HTMLDialogElement | null>(null) // 浏览器原生弹窗提供焦点限制及Esc关闭
const quoteElement = ref<HTMLElement | null>(null) // 全文中实际引用的位置
const checking = ref(false) // 正在检查原文件是否还存在
const available = ref(false) // 已确认原文件可以读取
const deleted = ref(false) // 只有404才显示原文件已删除，网络失败不能冒充删除
const loading = ref(false) // 正在读取全文
const notice = ref('') // 原文件状态或读取失败说明
const fullText = ref<ReturnType<typeof locateCitation> | null>(null) // 全文分为引用前、引用正文、引用后
let controller: AbortController | null = null // 关闭、切换或卸载时取消请求
const backdrop = createDialogBackdrop(closeSource)
onUnmounted(() => controller?.abort()) // 不更新已经离开的回答

function handleCitationClick(event: MouseEvent) { // v-html中的按钮通过事件委托打开来源，不执行模型提供的事件代码
  if (!(event.target instanceof Element)) return
  const button = event.target.closest<HTMLButtonElement>('button[data-citation-id]')
  const source = sources.value.find((item) => item.id === Number(button?.dataset.citationId))
  if (source) void openSource(source)
}
async function openSource(source: CitationSource) { // 快照立即展示，当前文件状态按需查询
  controller?.abort()
  const request = new AbortController() // 与本次选中来源绑定，旧响应不能覆盖新来源
  controller = request
  selected.value = source
  available.value = false
  deleted.value = false
  fullText.value = null
  notice.value = ''
  checking.value = true
  await nextTick()
  if (request.signal.aborted) return
  dialog.value?.showModal()
  try {
    const file = await getFile(source.fileId, request.signal)
    if (request.signal.aborted) return
    available.value = file.status === 'completed'
    if (!available.value) notice.value = '原文件尚未处理完成，当前仅显示引用快照。'
  } catch (error) {
    if (!request.signal.aborted) showFileError(error)
  } finally {
    if (!request.signal.aborted) checking.value = false
  }
}
function showFileError(error: unknown) { // 删除后仍保留快照，暂时无法连接则允许重试
  deleted.value = error instanceof ApiError && error.status === 404
  if (deleted.value) available.value = false
  notice.value = deleted.value ? '原文件已删除，以下为回答生成时保存的引用快照。' : error instanceof Error ? error.message : '暂时无法读取原文件'
}
async function openFullText() { // 校验快照仍对应当前原文后，再高亮并滚动到引用位置
  const source = selected.value
  if (!source || !available.value || loading.value) return
  const request = new AbortController()
  controller?.abort()
  controller = request
  loading.value = true
  notice.value = ''
  try {
    const file = await getFileContent(source.fileId, request.signal)
    if (request.signal.aborted) return
    const located = locateCitation(file.text_content, source)
    if (!located.matches) {
      notice.value = '当前原文与历史快照不一致，无法准确定位；请以引用快照为准。'
      return
    }
    fullText.value = located
    await nextTick()
    if (!request.signal.aborted) quoteElement.value?.scrollIntoView({ block: 'center' })
  } catch (error) {
    if (!request.signal.aborted) showFileError(error)
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}
function closeSource() { // 关闭弹窗时停止请求，快照仍保存在消息output中
  controller?.abort()
  if (dialog.value?.open) dialog.value.close()
  selected.value = null
  loading.value = false
  checking.value = false
}
</script>

<template>
  <div class="answer">
    <div v-html="html" @click="handleCitationClick"></div>
    <details v-if="sources.length" class="answer-sources">
      <summary>本轮资料来源 · {{ sources.length }}</summary>
      <button v-for="source in sources" :key="source.id" type="button" @click="openSource(source)">[{{ source.id }}] {{ source.filename }}{{ source.historical ? ' · 历史快照' : '' }}</button>
    </details>
    <dialog v-if="selected" ref="dialog" class="source-dialog" aria-label="引用来源" @cancel.prevent="closeSource" @close="closeSource" @pointerdown="backdrop.pointerdown" @click="backdrop.click">
      <header><div><small>来源 [{{ selected.id }}]</small><h3>{{ selected.filename }}</h3></div><button type="button" aria-label="关闭引用来源" @click="closeSource">×</button></header>
      <p class="source-position">原文字符 {{ selected.startOffset }}～{{ selected.endOffset }} · 回答生成时的快照</p>
      <p v-if="selected.historical" class="source-notice">这是此前工具返回的原文快照，本轮未重新读取。{{ selected.originCreatedAt ? `原回答时间：${new Date(selected.originCreatedAt * 1000).toLocaleString()}` : '' }}</p>
      <p v-if="checking" class="source-notice" role="status">正在检查原文件状态…</p>
      <p v-if="notice" class="source-notice" role="status">{{ notice }}</p>
      <pre class="source-snapshot">{{ selected.content }}</pre>
      <button v-if="!deleted && !fullText" type="button" :disabled="checking || loading" @click="available ? openFullText() : openSource(selected)">{{ loading ? '正在读取…' : available ? '查看全文并定位' : '重新检查原文件' }}</button>
      <div v-if="fullText" class="source-fulltext" tabindex="0" aria-label="原文件全文及引用位置"><span>{{ fullText.before }}</span><mark ref="quoteElement">{{ fullText.quote }}</mark><span>{{ fullText.after }}</span></div>
    </dialog>
  </div>
</template>

<style scoped>
.answer-sources { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--border); font-size: 11px; color: #7b8981; }
.answer-sources summary { cursor: pointer; }
.answer-sources button { display: inline-block; margin: 9px 8px 0 0; padding: 5px 8px; border: 1px solid #dce8df; border-radius: 6px; background: #f5f9f6; color: #52725c; font-size: 11px; text-align: left; overflow-wrap: anywhere; }
.answer :deep(.citation-link) { display: inline; border: 0; border-radius: 4px; padding: 1px 4px; margin: 0 2px; background: #eaf4ef; color: #267d69; font-size: .8em; vertical-align: super; line-height: 1.2; }
.source-dialog { width: min(760px, calc(100vw - 32px)); max-height: 85dvh; padding: 22px; border: 1px solid #dce5df; border-radius: 15px; color: #37473d; box-shadow: 0 18px 70px #14281b26; overflow-y: auto; white-space: normal; }
.source-dialog::backdrop { background: #14281b66; }
.source-dialog header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.source-dialog h3 { margin: 4px 0 0; font-size: 16px; overflow-wrap: anywhere; }
.source-dialog small, .source-position { font-size: 11px; color: #86938b; }
.source-dialog button { padding: 7px 12px; border: 1px solid #dce8df; border-radius: 7px; background: #f5f9f6; color: #52725c; font-size: 12px; }
.source-dialog header button { border: 0; background: transparent; font-size: 22px; }
.source-notice { font-size: 12px; color: #947343; }
.source-dialog .source-snapshot, .source-fulltext { padding: 14px; background: #f6f8f6; border-radius: 9px; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.9 'Microsoft YaHei', sans-serif; }
.source-fulltext { max-height: 42vh; overflow-y: auto; margin-top: 16px; }
.source-fulltext mark { background: #e2efb7; color: inherit; }
</style>
