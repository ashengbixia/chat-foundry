<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const registering = ref(false) // 首次初始化固定显示管理员创建表单
const isSignup = computed(() => auth.setupRequired || registering.value)
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const requestAdmin = ref(false) // 申请只进入审核，不会自行提升权限
const submitting = ref(false)
const error = ref('')
async function submit() {
  if (submitting.value) return
  error.value = ''
  if (isSignup.value && password.value !== confirmPassword.value) { error.value = '两次输入的密码不一致'; return }
  submitting.value = true
  try {
    if (isSignup.value) await auth.register(username.value.trim(), password.value, requestAdmin.value)
    else await auth.signIn(username.value.trim(), password.value)
    password.value = ''
    confirmPassword.value = ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '登录失败，请稍后重试'
  } finally { submitting.value = false }
}
function switchMode() { registering.value = !registering.value; password.value = ''; confirmPassword.value = ''; error.value = '' }
</script>

<template>
  <main class="auth-page">
    <div class="auth-brand"><span>✦</span> ChatFoundry</div>
    <section class="auth-card" aria-labelledby="auth-title">
      <small>你的思考工作空间</small>
      <h1 id="auth-title">{{ auth.setupRequired ? '建立你的工作空间' : isSignup ? '创建账号' : '欢迎回来' }}</h1>
      <p>{{ auth.setupRequired ? '设置初始管理员账号。已有会话和资料会归入这个账号。' : isSignup ? '注册后即可使用，聊天和资料仅属于你。' : '登录，继续你的对话与探索。' }}</p>
      <form @submit.prevent="submit">
        <label>用户名<input v-model="username" type="text" :maxlength="isSignup ? 80 : 254" pattern="\S+" autocomplete="username" placeholder="输入用户名" required :disabled="submitting" /></label>
        <label>密码<input v-model="password" type="password" minlength="8" maxlength="128" :autocomplete="isSignup ? 'new-password' : 'current-password'" required :disabled="submitting" placeholder="至少8个字符" /></label>
        <label v-if="isSignup">确认密码<input v-model="confirmPassword" type="password" minlength="8" maxlength="128" autocomplete="new-password" required :disabled="submitting" /></label>
        <label v-if="isSignup && !auth.setupRequired" class="admin-request"><input v-model="requestAdmin" type="checkbox" :disabled="submitting" />申请管理员权限<small>批准前仍为普通用户</small></label>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <button type="submit" :disabled="submitting">{{ submitting ? '正在处理…' : auth.setupRequired ? '创建管理员并开始使用' : isSignup ? '注册并进入' : '登录' }}</button>
      </form>
      <button v-if="!auth.setupRequired" type="button" class="switch-mode" :disabled="submitting" @click="switchMode">{{ isSignup ? '已有账号？去登录' : '还没有账号？注册' }}</button>
    </section>
    <footer>让复杂的事情，变得清楚一点。</footer>
  </main>
</template>

<style scoped>
.auth-page { min-height: 100dvh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 32px 20px; background: radial-gradient(ellipse at top, #f0f7f2, #fff 65%); }
.auth-brand { margin-bottom: 32px; font-weight: 650; font-size: 20px; letter-spacing: -.5px; }.auth-brand span { color: #52765d; margin-right: 6px; }
.auth-card { width: min(100%, 400px); padding: 30px; border: 1px solid #e4ebe6; border-radius: 18px; background: #fff; box-shadow: 0 16px 60px #2c503908; }
.auth-card > small { color: #8b9b90; font-size: 11px; }h1 { font-size: 25px; margin: 12px 0; font-weight: 550; }.auth-card > p { color: #8c9890; line-height: 1.8; font-size: 12px; margin-bottom: 25px; }
label { display: block; font-size: 12px; color: #637569; margin: 15px 0; }input:not([type='checkbox']) { display: block; width: 100%; padding: 11px 12px; margin-top: 7px; border: 1px solid #dde6df; border-radius: 8px; background: #fbfcfb; font-size: 14px; }
button[type='submit'] { width: 100%; padding: 12px; margin-top: 8px; background: #365c45; color: white; border: 0; border-radius: 9px; font-size: 13px; }.switch-mode { display: block; margin: 20px auto 0; background: none; border: 0; color: #6e8b78; font-size: 12px; }
.admin-request { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }.admin-request small { font-size: 10px; color: #99a49c; }.error { color: #b35b5b; font-size: 12px; }footer { margin-top: 26px; font-size: 11px; color: #a2aca5; }
</style>
