<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { discoverModels, getModelSettings, resetModelSettings, saveModelSettings, testModelService } from '../../api/modelSettings'
import type { ModelPreset, ModelServiceConfig, ModelServiceDraft } from '../../api/modelSettings'
import { createDialogBackdrop } from '../../utils/dialog'

const emit = defineEmits<{ modelChanged: [model: string] }>() // 只有服务器确认配置后，才更新聊天顶部的模型名称
const dialog = ref<HTMLDialogElement | null>(null)
const busy = ref(false)
const action = ref('') // 当前操作说明
const error = ref('')
const notice = ref('')
const testLimited = ref(false) // 思考阶段耗尽预算不当作认证失败，也不能标成已验证最终正文
const presets = ref<ModelPreset[]>([])
const models = ref<string[]>([])
const modelSection = ref<HTMLElement | null>(null) // 获取成功后滚动到实际可选择的位置
const modelSelect = ref<HTMLSelectElement | null>(null) // 明确的模型下拉框，不依赖浏览器隐蔽的输入候选提示
const saved = ref<ModelServiceConfig | null>(null)
const apiKey = ref('') // 只在当前表单短暂保留用户输入，不写入浏览器存储
const inherited = ref(true)
const draft = reactive<Omit<ModelServiceConfig, 'key_configured'>>({ provider: 'server', base_url: '', model: '', timeout: 60, key_source: 'environment' })
const selectedModel = computed({ // 列表选择与手动输入使用同一个实际模型字段
  get: () => models.value.includes(draft.model) ? draft.model : '',
  set: (value: string) => { if (value) draft.model = value },
})
const hasSavedKey = computed(() => saved.value?.key_source === 'personal' && saved.value.base_url === draft.base_url.trim().replace(/\/+$/, '') && saved.value.key_configured) // 换地址后不能悄悄复用旧Key
let controller: AbortController | null = null
const backdrop = createDialogBackdrop(close) // 点击遮罩关闭，弹窗内部点击和拖动不误关
onMounted(loadSettings) // 登录后加载当前账号配置，让顶部显示实际模型
onUnmounted(close)
function applyConfig(config: ModelServiceConfig, usingDefaults: boolean) {
  saved.value = config
  Object.assign(draft, { provider: config.provider, base_url: config.base_url, model: config.model, timeout: config.timeout, key_source: config.key_source })
  inherited.value = usingDefaults
  apiKey.value = ''
  emit('modelChanged', config.model)
}
async function run(label: string, operation: (signal: AbortSignal) => Promise<void>) { // 操作串行，关闭弹窗后忽略迟到响应
  if (busy.value) return
  const request = new AbortController()
  controller = request
  busy.value = true
  action.value = label
  error.value = notice.value = ''
  testLimited.value = false
  try { await operation(request.signal) }
  catch (cause) { if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : '模型服务操作失败' }
  finally { if (controller === request) { controller = null; busy.value = false; action.value = '' } }
}
async function loadSettings() {
  await run('正在加载设置…', async (signal) => {
    const response = await getModelSettings(signal)
    if (signal.aborted) return
    presets.value = response.presets
    models.value = [] // 重新打开设置时清除旧服务的候选列表，避免选到上次未保存服务的模型
    applyConfig(response.config, response.inherited)
  })
}
function open() { dialog.value?.showModal(); void loadSettings() }
function close() {
  controller?.abort()
  controller = null
  busy.value = false
  apiKey.value = ''
  if (dialog.value?.open) dialog.value.close()
}
function selectProvider() { // 提供方只预填地址，模型由服务列表或用户明确填写，不能猜测可用型号
  const preset = presets.value.find((item) => item.id === draft.provider)
  if (!preset) return
  draft.base_url = preset.base_url
  draft.model = preset.id === 'server' ? preset.model : ''
  draft.key_source = preset.id === 'server' ? 'environment' : 'personal'
  apiKey.value = ''
  models.value = []
  error.value = notice.value = ''
}
function changeAddress() { // 自定义地址只能使用个人Key或无需Key，不能转发服务器共用Key
  draft.provider = 'custom'
  if (draft.key_source === 'environment') draft.key_source = 'personal'
  models.value = []
}
function payload(): ModelServiceDraft { return { ...draft, base_url: draft.base_url.trim(), model: draft.model.trim(), api_key: draft.key_source === 'personal' ? apiKey.value.trim() || null : null } }
async function save() {
  await run('正在保存…', async (signal) => {
    const response = await saveModelSettings(payload(), signal)
    if (signal.aborted) return
    applyConfig(response.config, response.inherited)
    notice.value = '已保存，仅对当前账号的新回答生效；正在进行的回答继续使用原配置。'
  })
}
async function fetchModels() {
  await run('正在获取模型列表…', async (signal) => {
    const response = await discoverModels(payload(), signal)
    if (signal.aborted) return
    models.value = response.models
    notice.value = response.models.length ? `已获取 ${response.models.length} 个模型，请在下方“可用模型”中选择，再点击“保存配置”。` : '服务没有返回模型，可以手动填写模型名称。'
  })
  if (!busy.value && !error.value && models.value.length && dialog.value?.open) {
    await nextTick() // 等查询结束、下拉框解除禁用后再定位
    if (!dialog.value?.open) return
    modelSection.value?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    modelSelect.value?.focus({ preventScroll: true })
  }
}
async function test() {
  await run('正在测试连接…', async (signal) => {
    const response = await testModelService(payload(), signal)
    if (!signal.aborted) {
      testLimited.value = response.status === 'limited'
      notice.value = `${response.message}。测试不会保存配置。`
    }
  })
}
async function reset() {
  if (!window.confirm('确定删除当前账号保存的模型配置和个人Key，并恢复服务器默认配置吗？')) return
  await run('正在恢复默认配置…', async (signal) => {
    await resetModelSettings(signal)
    const response = await getModelSettings(signal)
    if (signal.aborted) return
    presets.value = response.presets
    applyConfig(response.config, response.inherited)
    models.value = []
    notice.value = '已恢复服务器默认配置。'
  })
}
defineExpose({ open })
</script>

<template>
  <dialog ref="dialog" class="settings-dialog" aria-label="设置" @cancel.prevent="close" @close="close" @pointerdown="backdrop.pointerdown" @click="backdrop.click">
    <header><div><small>个人设置</small><h2>模型服务</h2></div><button type="button" class="close" aria-label="关闭设置" @click="close">×</button></header>
    <p class="hint">为当前账号选择模型服务。支持官方预设及兼容 OpenAI 接口的自定义服务。</p>
    <p v-if="inherited" class="hint">当前使用服务器默认配置，保存后将使用你的个人配置。</p>
    <p v-if="busy" class="hint" role="status">{{ action }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" :class="testLimited ? 'test-warning' : 'success'" role="status">{{ notice }}</p>
    <form @submit.prevent="save">
      <fieldset :disabled="busy">
        <label>服务提供方<select v-model="draft.provider" @change="selectProvider"><option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.name }}</option></select></label>
        <label>API 基础地址<input v-model="draft.base_url" type="url" required maxlength="1000" placeholder="https://api.example.com/v1" @input="changeAddress" /></label>
        <p class="hint">填写基础地址，不要附加 /chat/completions。预设地址可按需修改。</p>
        <label>密钥来源<select v-model="draft.key_source"><option value="personal">我的 API Key</option><option v-if="draft.provider === 'server'" value="environment">服务器默认环境变量</option><option value="none">无需 Key（例如本地服务）</option></select></label>
        <label v-if="draft.key_source === 'personal'">API Key<input v-model="apiKey" type="password" maxlength="4096" autocomplete="off" :placeholder="hasSavedKey ? '已加密保存，留空保持原 Key' : '填写当前服务的个人 Key'" /></label>
        <p v-if="draft.key_source === 'personal'" class="hint">Key 加密保存在服务器，不会回显。更换服务地址后需要重新填写。</p>
        <p v-else-if="draft.key_source === 'environment'" class="hint">由服务器环境提供，只能用于服务器默认地址，页面不会读取 Key。</p>
        <div ref="modelSection" class="model-section">
          <label v-if="models.length">可用模型（{{ models.length }}）<select ref="modelSelect" v-model="selectedModel" aria-label="选择可用模型"><option value="" disabled>{{ draft.model ? '当前为手动填写的模型，可重新选择' : '请选择一个模型' }}</option><option v-for="model in models" :key="model" :value="model">{{ model }}</option></select></label>
          <label>模型 ID（支持手动填写）<input v-model="draft.model" required maxlength="200" placeholder="输入服务提供的模型 ID" /></label>
          <p class="hint">{{ models.length ? '下拉选择会自动填入模型 ID；也可以直接修改。' : '点击“获取模型列表”后，这里会显示可选择的模型；也可以直接输入模型 ID。' }}选择或修改后需保存配置才会生效。</p>
        </div>
        <label>请求超时（秒）<input v-model.number="draft.timeout" type="number" min="5" max="600" step="1" required /></label>
        <div class="actions"><button type="button" @click="fetchModels">获取模型列表</button><button type="button" @click="test">测试连接</button><button type="submit" class="primary">保存配置</button></div>
        <p class="hint">测试连接会发送一条简短的模型请求，可能产生少量费用。保存配置本身不调用模型。</p>
        <button type="button" class="reset" :disabled="inherited" @click="reset">恢复服务器默认</button>
      </fieldset>
    </form>
    <button v-if="error && !busy" class="reload" type="button" @click="loadSettings">重新加载已保存配置</button>
  </dialog>
</template>

<style scoped>
.settings-dialog { width: min(650px, calc(100vw - 28px)); max-height: 88dvh; padding: 26px; border: 1px solid #dce5df; border-radius: 16px; color: #405146; overflow-y: auto; }.settings-dialog::backdrop { background: #15291e66; }header { display: flex; justify-content: space-between; gap: 15px; align-items: center; }header small { color: #94a197; font-size: 11px; }h2 { margin: 5px 0; font-size: 21px; font-weight: 550; }.close { border: 0; background: transparent; font-size: 25px; }fieldset { margin: 0; padding: 0; border: 0; min-width: 0; }label { display: block; margin: 17px 0 8px; font-size: 12px; color: #637a69; }input, select { width: 100%; padding: 10px 11px; margin-top: 7px; border: 1px solid #dce5df; border-radius: 8px; background: #fcfdfc; font-size: 13px; }.hint { color: #96a198; font-size: 11px; line-height: 1.8; }.error { color: #b65d5d; font-size: 12px; }.success { color: #518365; font-size: 12px; line-height: 1.8; }.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 22px; }button { padding: 8px 12px; border: 1px solid #d9e5dc; border-radius: 7px; background: #f4f8f5; color: #58715f; font-size: 12px; }button.primary { margin-left: auto; background: #365c45; border-color: #365c45; color: white; }.reset, .reload { background: transparent; margin-top: 10px; font-size: 11px; }
.test-warning { color: #967244; font-size: 12px; line-height: 1.8; }
</style>
