<script setup lang="ts">
import { reactive, ref } from 'vue'
import { memoryRequest } from '../../api/memories'
import type { MemoryRecord, ToolEntry, ToolFeatures } from '../../api/memories'
defineProps<{ expanded?: boolean }>()
const features = reactive<ToolFeatures>({ memory_enabled: false, history_enabled: true, knowledge_enabled: true, follow_ups_enabled: true })
const records = ref<MemoryRecord[]>([]), tools = ref<ToolEntry[]>([])
const query = ref(''), toolQuery = ref(''), error = ref(''), notice = ref(''), busy = ref(false), loaded = ref(false), offset = ref(0), more = ref(false)
const draft = reactive({ id: '', content: '', path: '/', type: 'user' as 'user' | 'context', revision: 0 })
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await action() } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败' }
  finally { busy.value = false }
}
async function list(append = false) {
  const page = await memoryRequest<{ memories: MemoryRecord[]; has_more: boolean; next_offset: number | null }>(`memories?count=20&offset=${append ? offset.value : 0}&query=${encodeURIComponent(query.value)}`)
  records.value = append ? [...records.value, ...page.memories] : page.memories
  more.value = page.has_more; offset.value = page.next_offset ?? 0
}
async function catalog() { const value = await memoryRequest<{ tools: ToolEntry[] }>('tools'); tools.value = value.tools }
async function refresh() { Object.assign(features, await memoryRequest<ToolFeatures>('settings/tools')); await list(); await catalog(); loaded.value = true }
function open(event: Event) {
  if (!(event.target as HTMLDetailsElement).open) return
  void run(refresh)
}
function clear() { Object.assign(draft, { id: '', content: '', path: '/', type: 'user', revision: 0 }) }
function edit(row: MemoryRecord) { Object.assign(draft, row) }
function saveFeatures() { void run(async () => { await memoryRequest('settings/tools', 'PUT', features); window.dispatchEvent(new Event('chatfoundry:tool-settings')); await catalog(); notice.value = '工具设置已保存。关闭长期记忆不会删除已有记录。' }) }
function saveMemory() {
  void run(async () => {
    const data = { content: draft.content, path: draft.path, type: draft.type }
    await memoryRequest(draft.id ? `memories/${encodeURIComponent(draft.id)}` : 'memories', draft.id ? 'PUT' : 'POST', draft.id ? { ...data, revision: draft.revision } : data)
    clear(); await list(); notice.value = '记忆已保存。'
  })
}
function remove(row: MemoryRecord) {
  if (!window.confirm('删除这条长期记忆？原会话记录会保留。')) return
  void run(async () => { await memoryRequest(`memories/${encodeURIComponent(row.id)}?revision=${row.revision}`, 'DELETE'); if (draft.id === row.id) clear(); await list() })
}
function visibleTools() { const q = toolQuery.value.toLowerCase(); return tools.value.filter(tool => `${tool.name} ${tool.label} ${tool.description}`.toLowerCase().includes(q)) }
</script>

<template>
  <details class="account-tools" :open="expanded" @toggle="open">
    <summary>长期记忆与工具清单</summary>
    <p>长期记忆供本账号的新旧会话按需查阅，与单个会话摘要分开保存。默认关闭；开启后模型可以保存稳定偏好和背景，请勿保存密钥。</p>
    <p v-if="error" class="error" role="alert">{{ error }} <button type="button" @click="run(refresh)">重新加载</button></p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <fieldset :disabled="busy">
      <label><input v-model="features.memory_enabled" type="checkbox" />允许模型读写长期记忆</label>
      <label><input v-model="features.history_enabled" type="checkbox" />允许查找本账号的其他历史会话</label>
      <label><input v-model="features.knowledge_enabled" type="checkbox" />允许主动发现未选入会话的本人知识库</label>
      <label><input v-model="features.follow_ups_enabled" type="checkbox" />回答后生成推荐追问</label>
      <p>推荐追问会额外调用一次当前模型，不影响正文完成时间，可随时关闭。</p>
      <p>关闭知识库发现后，当前会话已明确添加的资料仍可通过原有检索和读取工具使用。</p>
      <button type="button" @click="saveFeatures">保存工具设置</button>
      <h3>管理记忆</h3>
      <div class="row"><input v-model="query" aria-label="搜索记忆" placeholder="搜索内容或分组" @keydown.enter.prevent="run(() => list())" /><button type="button" @click="run(() => list())">搜索</button></div>
      <p v-if="!records.length && loaded">暂无记忆。可手动添加，或开启后在聊天中说“记住我的偏好”。</p>
      <article v-for="record in records" :key="record.id" class="memory">
        <small>{{ record.path }} · {{ record.type === 'user' ? '用户偏好' : '持续背景' }} · {{ new Date(record.updated_at * 1000).toLocaleDateString() }}</small>
        <p>{{ record.content }}</p><button type="button" @click="edit(record)">编辑</button><button type="button" @click="remove(record)">删除</button>
      </article>
      <button v-if="more" type="button" @click="run(() => list(true))">加载更多</button>
      <h4>{{ draft.id ? '编辑记忆' : '添加记忆' }}</h4>
      <label>分组路径<input v-model="draft.path" maxlength="300" placeholder="/偏好/回答方式" /></label>
      <label>类型<select v-model="draft.type"><option value="user">用户偏好</option><option value="context">持续背景</option></select></label>
      <textarea v-model="draft.content" aria-label="记忆正文" rows="3" maxlength="8000" placeholder="值得在未来会话继续使用的信息" />
      <button type="button" :disabled="!draft.content.trim()" @click="saveMemory">{{ draft.id ? '保存修改' : '添加记忆' }}</button><button v-if="draft.id" type="button" @click="clear">取消编辑</button>
      <h3>已注册工具 · {{ tools.length }}</h3>
      <input v-model="toolQuery" aria-label="筛选工具清单" placeholder="搜索工具名称或用途" />
      <p>附带资料、联网开关和模型工具能力会影响实际可用清单；扩展工具通过“发现可用工具”按需启用。</p>
      <details v-for="tool in visibleTools()" :key="tool.name" class="tool"><summary>{{ tool.label }} <small>{{ tool.on_demand ? '按需发现' : '基础工具' }} · {{ tool.available ? '按权限可用' : '需开启功能或提供资料' }}</small></summary><code>{{ tool.name }}</code><p>{{ tool.description }}</p></details>
    </fieldset>
  </details>
</template>

<style scoped>
.account-tools { margin-top: 22px; padding-top: 16px; border-top: 1px solid #e6ece8; font-size: 12px; }.account-tools > summary { cursor: pointer; font-weight: 550; }p { color: #7c8b81; font-size: 12px; line-height: 1.8; }fieldset { border: 0; padding: 0; min-width: 0; }label { display: block; margin: 12px 0; }input:not([type=checkbox]),select,textarea { display: block; width: 100%; box-sizing: border-box; margin: 7px 0; border: 1px solid #dce5df; padding: 9px; border-radius: 7px; background: white; color: inherit; }button { padding: 6px 10px; margin: 3px 7px 3px 0; border: 1px solid #dce5df; border-radius: 6px; background: #f8faf8; cursor: pointer; }button:disabled { opacity: .5; }h3 { font-size: 13px; margin-top: 22px; }.memory { padding: 12px 0; border-bottom: 1px solid #edf1ee; }.memory p { white-space: pre-wrap; overflow-wrap: anywhere; color: #4e6154; }.row { display: flex; gap: 8px; align-items: center; }.tool { padding: 8px 0; border-bottom: 1px solid #edf1ee; }.tool summary { cursor: pointer; }.tool small,small { color: #92a096; }code { display: inline-block; margin-top: 9px; font-size: 11px; }.error { color: #b65d5d; }
</style>
