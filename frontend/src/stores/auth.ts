import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as api from '../api/auth'
import type { UserInfo } from '../api/auth'

export const useAuthStore = defineStore('auth', () => { // 登录身份以服务器为准，刷新页面后重新检查Cookie
  const user = ref<UserInfo | null>(null)
  const loading = ref(true)
  const setupRequired = ref(false)
  const registrationEnabled = ref(true)
  const bootstrapRequired = ref(false)
  const error = ref('')
  let revision = 0 // 多个失效通知同时到达时，只接受最新一次身份检查
  function notifyTabs() { // 只通知其他标签页身份变化，不保存密码、Token或个人资料
    try { localStorage.setItem('chatfoundry:auth-change', `${Date.now()}:${Math.random()}`) } catch { /* 禁用本地存储不影响登录 */ }
  }
  async function initialize() {
    const current = ++revision
    loading.value = true
    error.value = ''
    try {
      const account = await api.getCurrentUser()
      const config = await api.getAuthConfig()
      const setup = config?.setup_required ?? false
      if (current !== revision) return
      user.value = account
      setupRequired.value = setup
      registrationEnabled.value = config?.registration_enabled ?? true
      bootstrapRequired.value = config?.bootstrap_required ?? false
    } catch (cause) {
      if (current === revision) error.value = cause instanceof Error ? cause.message : '检查登录状态失败'
    } finally { if (current === revision) loading.value = false }
  }
  async function signIn(username: string, password: string) {
    const account = await api.login(username, password)
    revision++
    user.value = account
    loading.value = false
    error.value = ''
    notifyTabs()
  }
  async function register(username: string, password: string, requestAdmin: boolean, bootstrapToken = '') {
    const account = await api.signup(username, password, setupRequired.value, requestAdmin, bootstrapToken)
    revision++
    setupRequired.value = false
    user.value = account
    loading.value = false
    error.value = ''
    notifyTabs()
  }
  async function signOut() {
    await api.logout()
    revision++
    user.value = null
    loading.value = false
    error.value = ''
    notifyTabs()
  }
  return { registrationEnabled, bootstrapRequired, user, loading, setupRequired, error, initialize, signIn, register, signOut }
})
