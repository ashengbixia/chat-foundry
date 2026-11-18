<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { listConnections, activateConnection, deleteConnection, listAssistants, saveAssistant, deleteAssistant } from '../../api/workspace'
import type { Assistant, AssistantDraft, Connection } from '../../api/workspace'
import { memoryRequest } from '../../api/memories'
import type { ToolEntry } from '../../api/memories'
import { listKnowledge } from '../../api/knowledge'
import type { KnowledgeBase } from '../../api/knowledge'

const emit = defineEmits<{ changed: []; editConnection: [] }>()
const connections = ref<Connection[]>([]), assistants = ref<Assistant[]>([]), bases = ref<KnowledgeBase[]>([])
const busy = ref(false), error = ref(''), editing = ref(''), showEditor = ref(false), tools = ref<string[]>([]), catalog = ref<ToolEntry[]>([]), allTools = ref(true)
const draft = reactive<AssistantDraft>({ name: '', system_prompt: '', connection_id: null, model: '', tool_names: null, knowledge_ids: [] })
async function refresh() { const [saved, profiles, knowledge, available] = await Promise.all([listConnections(), listAssistants(), listKnowledge(), memoryRequest<{ tools: ToolEntry[] }>('tools')]); connections.value = saved; assistants.value = profiles; bases.value = knowledge; catalog.value = available.tools }
async function run(operation: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await operation() } catch (e) { error.value = e instanceof Error ? e.message : '操作失败' } finally { busy.value = false } }
function edit(item?: Assistant) { editing.value = item?.id ?? ''; Object.assign(draft, { name: '', system_prompt: '', connection_id: null, model: '', tool_names: null, knowledge_ids: [] }, item ? { ...item, knowledge_ids: [...item.knowledge_ids] } : {}); tools.value = [...(item?.tool_names ?? [])]; allTools.value = item?.tool_names == null; showEditor.value = true }
async function save() { await run(async () => { const { name, system_prompt, connection_id, model, knowledge_ids } = draft; await saveAssistant({ name, system_prompt, connection_id, model, knowledge_ids, tool_names: allTools.value ? null : tools.value }, editing.value || undefined); await refresh(); showEditor.value = false; emit('changed') }) }
async function activate(item: Connection) { await run(async () => { await activateConnection(item.id); await refresh(); emit('changed'); emit('editConnection') }) }
async function removeConnection(item: Connection) { if (!confirm(`删除连接“${item.name}”？`)) return; await run(async () => { await deleteConnection(item.id); await refresh(); emit('changed') }) }
async function removeAssistant(item: Assistant) { if (!confirm(`删除助手“${item.name}”？已有对话保留，后续使用普通模式。`)) return; await run(async () => { await deleteAssistant(item.id); await refresh(); emit('changed') }) }
onMounted(() => run(refresh))
</script>

<template>
  <section class="workspace-panel">
    <p v-if="error" role="alert">{{ error }}</p>
    <h3>已保存的模型连接</h3><small>在“模型服务”填写配置后，可另存为命名连接。点击连接切换并编辑。</small>
    <div v-for="item in connections" :key="item.id" class="row"><button :disabled="busy" @click="activate(item)">{{ item.name }}{{ item.active ? ' · 当前' : '' }}<small>{{ item.model }} · {{ item.base_url }}</small></button><button :disabled="busy" @click="removeConnection(item)">删除</button></div>
    <p v-if="!connections.length">尚未保存连接。</p>
    <h3>我的助手 <button :disabled="busy" @click="edit()">新建助手</button></h3>
    <small>保存提示词、模型连接、工具范围和默认知识库；新对话选择后使用。</small>
    <div v-for="item in assistants" :key="item.id" class="row"><button :disabled="busy" @click="edit(item)">{{ item.name }}<small>{{ item.model || '沿用连接模型' }}</small></button><button :disabled="busy" @click="removeAssistant(item)">删除</button></div>
    <form v-if="showEditor" @submit.prevent="save"><fieldset :disabled="busy">
      <label>助手名称<input v-model="draft.name" required maxlength="80" /></label>
      <label>系统提示词<textarea v-model="draft.system_prompt" rows="5" maxlength="8000" placeholder="角色、任务和回答要求" /></label>
      <label>模型连接<select v-model="draft.connection_id"><option :value="null">跟随当前模型服务</option><option v-for="item in connections" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <label>模型 ID（留空沿用连接）<input v-model="draft.model" maxlength="200" /></label>
      <label class="check"><input v-model="allTools" type="checkbox" />使用默认工具范围</label>
      <div v-if="!allTools" class="tool-choices"><small>仅允许所选工具；全部取消表示禁用工具。账号开关和资料权限仍然生效。</small><label v-for="tool in catalog.filter(item => !item.alias_of || tools.includes(item.name))" :key="tool.name" class="check" :title="tool.description"><input v-model="tools" type="checkbox" :value="tool.name" />{{ tool.label }}<small>{{ tool.name }}</small></label></div>
      <label>默认知识库（最多 5 个）<span class="knowledge-choices"><label v-for="base in bases" :key="base.id" class="check"><input v-model="draft.knowledge_ids" type="checkbox" :value="base.id" :disabled="!draft.knowledge_ids.includes(base.id) && draft.knowledge_ids.length >= 5" />{{ base.name }}</label></span></label>
      <div class="row"><button type="button" @click="showEditor = false">取消</button><button type="submit">保存助手</button></div>
    </fieldset></form>
  </section>
</template>

<style scoped>
.tool-choices { max-height:240px; overflow:auto; border-top:1px solid #e5eae6; }.tool-choices .check { margin:9px 0; }.knowledge-choices { display:block; }

h3 { margin-top: 24px; font-size: 14px; }h3 button { float:right; }small { display:block; color:#8c9890; font-size:11px; line-height:1.8; }.row { display:flex; gap:8px; justify-content:space-between; margin:10px 0; }.row > button:first-child { flex:1; text-align:left; overflow-wrap:anywhere; }button { border:1px solid #e2e9e4; border-radius:7px; padding:8px 10px; background:#fafcfb; color:#536b5d; }label { display:block; margin:14px 0; font-size:12px; }input,textarea,select { display:block; width:100%; margin-top:6px; padding:9px; border:1px solid #dce5df; border-radius:7px; }label.check { display:flex; align-items:center; gap:8px; }.check input { width:auto; }fieldset { border:0; padding:0; }[role=alert] { color:#a44; }
</style>
