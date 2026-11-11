<script setup lang="ts">
import { getActivePinia } from 'pinia'
import { onMounted, onUnmounted, watch } from 'vue'
import App from './App.vue'
import AuthPage from './components/auth/AuthPage.vue'
import { useAuthStore } from './stores/auth'
import { useChatStore } from './stores/chat'

const auth = useAuthStore() // 登录确认前不挂载聊天和文件组件
const pinia = getActivePinia()! // 退出后移除上一个账号的聊天仓库，防止页面残留
watch(() => auth.user?.id, (next, previous) => {
  if (!previous || previous === next) return
  const chat = useChatStore()
  chat.stopReply()
  chat.$dispose()
  delete pinia.state.value.chat
}, { flush: 'sync' })
function handleExpired() { void auth.initialize() } // 401后重新确认身份，旧请求不能误退出刚登录的新账号
function handleStorage(event: StorageEvent) { // 同一浏览器其他标签页切换账号时，立即隐藏旧数据并重新确认
  if (event.key !== 'chatfoundry:auth-change') return
  auth.user = null
  void auth.initialize()
}
onMounted(() => {
  window.addEventListener('auth-expired', handleExpired)
  window.addEventListener('storage', handleStorage)
  void auth.initialize()
})
onUnmounted(() => {
  window.removeEventListener('auth-expired', handleExpired)
  window.removeEventListener('storage', handleStorage)
})
</script>

<template>
  <div v-if="auth.loading" class="auth-loading" role="status">正在确认登录状态…</div>
  <div v-else-if="auth.error" class="auth-loading" role="alert"><p>{{ auth.error }}</p><button type="button" @click="auth.initialize">重新连接</button></div>
  <App v-else-if="auth.user" :key="auth.user.id" />
  <AuthPage v-else />
</template>

<style scoped>
.auth-loading { min-height: 100dvh; display: flex; flex-direction: column; gap: 12px; align-items: center; justify-content: center; color: #7a897f; font-size: 14px; }
.auth-loading button { padding: 8px 18px; border: 1px solid #cfdcd3; background: #f1f7f3; border-radius: 8px; }
</style>
