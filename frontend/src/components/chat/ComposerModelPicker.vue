<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { discoverModels, getModelSettings, selectModel } from '../../api/modelSettings'
import type { ModelServiceConfig } from '../../api/modelSettings'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ currentModel: string; disabled?: boolean }>()
const emit = defineEmits<{ changed: [model: string]; busy: [active: boolean]; openSettings: [] }>()
const root = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const busy = ref(false)
const saving = ref(false)
const error = ref('')
const models = ref<string[]>([])
const catalogLoaded = ref(false) // 区分未获取、获取失败和服务返回空列表
const catalogNotice = ref('')
const capabilities = ref<Record<string, string[]>>({})
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
  catalogNotice.value = ''
  capabilities.value = {}
  try {
    const response = await getModelSettings(request.signal)
    if (request.signal.aborted) return
    config.value = response.config
    const { key_configured, ...fields } = response.config
    const available = await discoverModels({ ...fields, api_key: null }, request.signal)
    if (!request.signal.aborted) {
      models.value = [...new Set(available.models)]
      catalogNotice.value = available.notice ?? ''
      capabilities.value = available.capabilities ?? {}
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
  await nextTick()
  if (open.value) searchInput.value?.focus()
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
    <button type="button" class="model-trigger" :disabled="disabled || saving" :aria-expanded="open" aria-haspopup="dialog" :title="currentModel || '选择模型'" @click="toggle"><span>{{ saving ? '正在切换…' : currentModel || '选择模型' }}</span><AppIcon name="chevron" class="down-chevron" :size="13" /></button>
    <div v-if="open" class="model-popup" role="dialog" aria-label="聊天模型">
      <div class="model-heading"><label class="model-search"><AppIcon name="search" :size="17" /><input ref="searchInput" v-model="search" type="search" placeholder="搜索模型" aria-label="搜索可用模型" /></label><button type="button" class="refresh-models" :disabled="busy || saving" title="刷新模型列表" aria-label="刷新模型列表" @click="load"><AppIcon name="refresh" :size="15" /></button></div>
      <p v-if="busy" role="status">正在获取模型…</p>
      <p v-if="error" class="model-error" role="alert">{{ error }}</p>
      <p v-if="unavailableSelection" class="model-warning" role="status">已保存的 {{ currentModel }} 不在服务返回的模型列表中，请重新选择，或到设置中核对手动配置。</p>
      <p v-if="catalogNotice" role="status">{{ catalogNotice }}</p>
      <div class="model-options"><button v-for="model in filteredModels" :key="model" type="button" :aria-pressed="currentModel === model" :disabled="busy || saving" @click="choose(model)"><span class="model-avatar" aria-hidden="true">AI</span><span class="model-name">{{ model }}<small v-if="capabilities[model]?.includes('tools') || capabilities[model]?.includes('thinking')" class="capability">{{ [capabilities[model]?.includes('tools') ? '工具调用' : '', capabilities[model]?.includes('thinking') ? '深度思考' : ''].filter(Boolean).join(' · ') }}</small></span><AppIcon v-if="currentModel === model" name="check" :size="16" /></button></div>
      <p v-if="catalogLoaded && !models.length">服务没有返回可选模型，可到设置中手动填写。</p>
      <p v-else-if="models.length && !filteredModels.length">没有匹配的模型。</p>
      <div class="model-footer"><span>{{ config?.provider === 'server' ? '服务器默认服务' : config?.provider === 'custom' ? '自定义服务' : config?.provider || '当前模型服务' }}</span><button type="button" :disabled="saving" @click="settings">管理模型服务<AppIcon name="chevron" :size="12" /></button></div>
    </div>
  </div>
</template>

<style scoped>
.composer-model-picker { position: relative; flex-shrink: 1; min-width: 0; }
.model-trigger { display: flex; align-items: center; gap: 9px; max-width: 220px; padding: 7px 9px; border: 0; border-radius: 9px; background: transparent; color: #777; font-size: 13px; }
.model-trigger > span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }.down-chevron { transform: rotate(90deg); flex-shrink: 0; }.model-trigger:hover:not(:disabled), .model-trigger[aria-expanded='true'] { background: #f4f4f4; color: #333; }
.model-popup { position: absolute; right: 0; bottom: calc(100% + 12px); width: min(380px, calc(100vw - 44px)); padding: 6px; background: white; border: 1px solid #e9e9e9; border-radius: 16px; box-shadow: 0 8px 32px #00000012, 0 2px 5px #00000008; z-index: 25; }
.model-heading { display: flex; gap: 12px; align-items: center; padding: 6px 8px 9px; }.model-search { display: flex; gap: 9px; align-items: center; flex: 1; min-width: 0; color: #555; }.model-search input { width: 100%; min-width: 0; padding: 3px 0; border: 0; outline: 0; background: transparent; font-size: 14px; color: #333; }.model-search input::placeholder { color: #aaa; }
.refresh-models { display: grid; place-items: center; width: 25px; height: 25px; border: 0; border-radius: 6px; background: transparent; color: #999; }.refresh-models:hover { background: #f3f3f3; }
.model-popup p { margin: 8px 10px; font-size: 12px; line-height: 1.7; color: #999; }.model-popup .model-error { color: #b26868; }.model-popup .model-warning { color: #967244; }
.model-options { max-height: min(300px, 38dvh); overflow-y: auto; }.model-options button { display: flex; align-items: center; gap: 11px; width: 100%; padding: 10px; border: 0; border-radius: 9px; background: transparent; text-align: left; font-size: 14px; color: #555; }.model-options button:hover:not(:disabled), .model-options button[aria-pressed='true'] { background: #f6f6f6; }
.model-avatar { display: grid; place-items: center; width: 22px; height: 22px; flex-shrink: 0; border: 1px solid #efefef; border-radius: 6px; font-size: 9px; font-weight: 650; color: #444; }.model-name { flex: 1; min-width: 0; overflow-wrap: anywhere; }.capability { display: block; margin-top: 3px; color: #a0a0a0; font-size: 10px; }
.model-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 9px 3px; border-top: 1px solid #f3f3f3; margin-top: 5px; color: #a0a0a0; font-size: 10px; }.model-footer button { display: flex; gap: 3px; align-items: center; padding: 3px 0; border: 0; background: transparent; color: #888; font-size: 11px; }
@media (max-width: 600px) { .model-trigger { max-width: 170px; font-size: 12px; } .model-popup { right: -46px; } }
</style>
