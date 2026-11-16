<script setup lang="ts">
import { computed, reactive } from 'vue'
import type { QuestionCall } from '../../utils/userQuestions'
import type { ClarificationReply } from '../../api/chats'
const props = defineProps<{ call: QuestionCall; messageId: string; active: boolean; busy?: boolean }>()
const emit = defineEmits<{ reply: [value: { text: string; clarification: ClarificationReply }] }>()
const selected = reactive<Record<string, string>>(Object.create(null)), other = reactive<Record<string, string>>(Object.create(null))
const pending = computed(() => props.call.status === 'awaiting_user' && props.active)
const complete = computed(() => props.call.questions.every(q => selected[q.id] !== undefined && (selected[q.id] !== 'other' || !!other[q.id]?.trim())))
function submit(cancelled = false) {
  if (props.busy || !pending.value || (!cancelled && !complete.value)) return
  const answers = cancelled ? {} : Object.fromEntries(props.call.questions.map(q => [q.id, selected[q.id] === 'other' ? other[q.id]!.trim() : q.options[Number(selected[q.id])]!.label]))
  const text = cancelled ? '跳过这次追问，请根据已知信息继续；不确定的地方请明确说明，不要代我选择。' : props.call.questions.map(q => `${q.question}\n我的回答：${answers[q.id]}`).join('\n\n')
  emit('reply', { text, clarification: { message_id: props.messageId, call_id: props.call.callId, answers, cancelled } })
}
</script>
<template>
  <section class="user-question" aria-label="助手追问">
    <template v-if="pending">
      <p class="eyebrow">需要你补充一点信息</p>
      <fieldset v-for="q in call.questions" :key="q.id" :disabled="busy"><legend>{{ q.question }}</legend>
        <label v-for="(option, index) in q.options" :key="index" :class="{ selected: selected[q.id] === String(index) }"><input v-model="selected[q.id]" type="radio" :name="`${call.callId}-${q.id}`" :value="String(index)" /><span>{{ option.label }} <small v-if="index === 0">推荐</small><em>{{ option.description }}</em></span></label>
        <label v-if="q.allow_other !== false"><input v-model="selected[q.id]" type="radio" :name="`${call.callId}-${q.id}`" value="other" /><span>自己填写</span></label>
        <textarea v-if="selected[q.id] === 'other'" v-model="other[q.id]" :aria-label="q.question" maxlength="2000" rows="2" placeholder="输入你的想法" />
      </fieldset>
      <div class="question-actions"><button type="button" :disabled="busy || !complete" @click="submit()">提交并继续</button><button type="button" :disabled="busy" @click="submit(true)">跳过</button></div>
      <small>也可以直接在输入框回答；问题已保存，稍后回来仍可继续。</small>
    </template>
    <details v-else><summary>{{ call.status === 'answered' ? '已回答追问' : call.status === 'cancelled' ? '已跳过追问' : '此前的追问' }}</summary><p v-for="q in call.questions" :key="q.id">{{ q.question }}<br /><span>{{ call.answers[q.id] ?? call.answers.free_text ?? '这段对话已继续，或本轮已停止。' }}</span></p></details>
  </section>
</template>
<style scoped>
.user-question { margin: 12px 0 20px; padding: 10px 0 10px 14px; border-left: 2px solid #c8dace; font-size: 13px; color: #516459; }.eyebrow { font-size: 11px; color: #8c9b91; }fieldset { border: 0; padding: 8px 0 12px; min-width: 0; }legend { font-weight: 550; line-height: 1.8; }label { display: flex; gap: 9px; align-items: flex-start; padding: 9px 8px; cursor: pointer; border-radius: 7px; }label.selected { background: #f0f6f1; }input { margin-top: 4px; }em { display: block; font-style: normal; color: #939e96; font-size: 11px; margin-top: 4px; }small { color: #97a29a; font-size: 10px; line-height: 1.8; }textarea { width: 100%; box-sizing: border-box; border: 1px solid #dfe7e1; border-radius: 7px; padding: 9px; font: inherit; }.question-actions { display: flex; gap: 8px; margin-bottom: 7px; }button { border: 1px solid #dce5df; padding: 7px 12px; border-radius: 7px; background: #f5f9f5; color: #526b59; cursor: pointer; }button:disabled { opacity: .45; cursor: default; }summary { cursor: pointer; color: #8b978f; }details p { line-height: 1.8; }details p span { color: #93a097; }
</style>
