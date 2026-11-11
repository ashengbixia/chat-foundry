<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { discoverModels, getModelSettings, selectModel } from '../../api/modelSettings'
import type { ModelServiceConfig } from '../../api/modelSettings'

const props = defineProps<{ currentModel: string; disabled?: boolean }>()
const emit = defineEmits<{ changed: [model: string]; busy: [active: boolean]; openSettings: [] }>()
const root = ref<HTMLElement | null>(null)
const open = ref(false)
const busy = ref(false)
const saving = ref(false)
const error = ref('')
const models = ref<string[]>([])
const catalogLoaded = ref(false) // 区分未获取、获取失败和服务返回空列表
const search = ref('')
const filteredModels = computed(() => models.value.filter((model) => model.toLowerCase().includes(search.value.trim().toLowerCase()))) // 只搜索真实返回的模型
const unavailableSelection = computed(() => catalogLoaded.value && !!props.currentModel && !models.value.includes(props.currentModel)) // 旧配置单独提示，不能混入可选模型列表
const config = ref<ModelServiceConfig | null>(null)
let controller: AbortController | null = null
function close() { open.value = false; if (!saving.value) { controller?.abort(); controller = null; busy.value = false } }
function closeOutside(event: PointerEvent) { if (event.target instanceof Node && !root.value?.contains(event.target)) close() }
onMounted(() => document.addEventListener('pointerdown', closeOutside))
onUnmounted(() => { controller?.abort(); document.removeEventListener('pointerdown', closeOutside); emit('busy', false) })
watch(() => props.currentModel, () => { if (!saving.value) { config.value = null; models.value = []; catalogLoaded.value = false; close() } })
watch(() => props.disabled, (disabled) => { if (disabled) close() })
async function load() { // 只获取当前服务的模型列表，不消耗聊天生成额度
  if (busy.value || saving.value) return
  const request = new AbortController()
  controller = request
  busy.value = true
  error.value = ''
  models.value = []
  catalogLoaded.value = false
  try {
    const response = await getModelSettings(request.signal)
    if (request.signal.aborted) return
    config.value = response.config
    const { key_configured, ...fields } = response.config
    const available = await discoverModels({ ...fields, api_key: null }, request.signal)
    if (!request.signal.aborted) {
      models.value = [...new Set(available.models)]
      catalogLoaded.value = true
    }
  } catch (cause) { if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : '获取模型失败' }
  finally { if (controller === request) { busy.value = false; controller = null } }
}
async function toggle() {
  if (props.disabled || saving.value) return
  if (open.value) { close(); return }
  open.value = true
  search.value = ''
  await load()
}
async function choose(model: string) { // 服务器确认切换后再更新标签，避免界面已变而请求仍使用旧模型
  if (!config.value || busy.value || saving.value || props.disabled || !models.value.includes(model)) return
  if (model === props.currentModel) { close(); return }
  const request = new AbortController()
  controller = request
  saving.value = true
  emit('busy', true)
  error.value = ''
  try {
    const response = await selectModel(model, config.value.base_url, request.signal)
    if (request.signal.aborted) return
    config.value = response.config
    emit('changed', response.config.model)
    open.value = false
  } catch (cause) { if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : '切换模型失败' }
  finally { saving.value = false; emit('busy', false); if (controller === request) controller = null }
}
function settings() { close(); emit('openSettings') }
</script>

<template>
  <div ref="root" class="composer-model-picker" @keydown.esc.stop="close">
    <button type="button" class="model-trigger" :disabled="disabled || saving" :aria-expanded="open" aria-haspopup="menu" :title="currentModel || '选择模型'" @click="toggle"><span>{{ saving ? '正在切换…' : currentModel || '选择模型' }}</span><span aria-hidden="true">⌄</span></button>
    <div v-if="open" class="model-popup" role="menu" aria-label="聊天模型">
      <div class="model-heading"><strong>选择模型</strong><button type="button" :disabled="busy || saving" @click="load">刷新</button></div>
      <p v-if="busy" role="status">正在获取模型…</p>
      <p v-if="error" class="model-error" role="alert">{{ error }}</p>
      <p v-if="unavailableSelection" class="model-warning" role="status">已保存的 {{ currentModel }} 不在服务返回的模型列表中，请重新选择，或到设置中核对手动配置。</p>
      <input v-if="models.length" v-model="search" class="model-search" type="search" placeholder="搜索模型" aria-label="搜索可用模型" />
      <div class="model-options"><button v-for="model in filteredModels" :key="model" type="button" role="menuitemradio" :aria-checked="currentModel === model" :disabled="busy || saving" @click="choose(model)"><span>{{ model }}</span><span v-if="currentModel === model" aria-hidden="true">✓</span></button></div>
      <p v-if="catalogLoaded && !models.length">服务没有返回可选模型，可到设置中手动填写。</p>
      <p v-else-if="models.length && !filteredModels.length">没有匹配的模型。</p>
      <button type="button" class="model-settings" :disabled="saving" @click="settings">配置模型服务 / 手动填写模型</button>
    </div>
  </div>
</template>

<style scoped>
.composer-model-picker { position: relative; flex-shrink: 1; min-width: 0; }.model-trigger { display: flex; align-items: center; gap: 8px; max-width: 190px; padding: 6px 10px; border: 1px solid #dce5e1; border-radius: 18px; background: white; color: #617469; font-size: 11px; }.model-trigger > span:first-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }.model-trigger:hover:not(:disabled) { border-color: #8eb5a0; background: #f3f8f5; }.model-popup { position: absolute; right: 0; bottom: calc(100% + 12px); width: min(300px, calc(100vw - 45px)); padding: 9px; background: white; border: 1px solid #dce5df; border-radius: 13px; box-shadow: 0 12px 42px #233d2f24; z-index: 15; }.model-heading { display: flex; justify-content: space-between; align-items: center; padding: 5px; font-size: 12px; color: #6f8074; }.model-heading button { border: 0; background: transparent; color: #8b9c91; font-size: 11px; }.model-popup p { margin: 8px 5px; font-size: 11px; line-height: 1.7; color: #98a39c; }.model-popup .model-error { color: #b26868; }.model-options { max-height: min(280px, 35vh); overflow-y: auto; }.model-options button { display: flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; padding: 10px; border: 0; border-radius: 7px; background: none; text-align: left; overflow-wrap: anywhere; font-size: 12px; color: #5b7161; }.model-options button:hover, .model-options button[aria-checked='true'] { background: #eff6f1; }.model-settings { width: 100%; margin-top: 6px; padding: 10px 5px 3px; border: 0; border-top: 1px solid #edf1ee; background: none; text-align: left; font-size: 11px; color: #85998c; }
@media (max-width: 600px) { .model-trigger { max-width: 160px; } }
.model-search { width: 100%; margin: 5px 0; padding: 8px 10px; border: 1px solid #e1e9e3; border-radius: 7px; background: #f8faf8; color: #536a5c; font-size: 12px; }
.model-popup .model-warning { color: #967244; }
</style>
