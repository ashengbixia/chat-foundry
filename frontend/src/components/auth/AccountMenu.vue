<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useChatStore } from '../../stores/chat'
import { changePassword, getCurrentUser, listUsers, requestAdmin, setUserRole, updateProfile } from '../../api/auth'
import type { UserInfo } from '../../api/auth'
import { createDialogBackdrop } from '../../utils/dialog'

const auth = useAuthStore()
const chat = useChatStore()
const accountId = auth.user!.id // 异步操作完成时不能把上一个账号的资料写进新登录账号
const menu = ref<HTMLDetailsElement | null>(null)
const dialog = ref<HTMLDialogElement | null>(null)
const tab = ref<'profile' | 'members'>('profile')
const name = ref('') // 名称可以修改，用户名暂时只读
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const members = ref<UserInfo[]>([])
const busy = ref(false)
const error = ref('')
const notice = ref('')
let closed = false // 关闭后忽略迟到的成员列表，不重新打开弹窗
const backdrop = createDialogBackdrop(closeAccount)
function closeMenuOutside(event: PointerEvent) { if (event.target instanceof Node && !menu.value?.contains(event.target) && menu.value) menu.value.open = false }
onMounted(() => document.addEventListener('pointerdown', closeMenuOutside))
onUnmounted(() => { closed = true; document.removeEventListener('pointerdown', closeMenuOutside) })
async function openAccount(next: 'profile' | 'members') {
  if (busy.value) return
  if (menu.value) menu.value.open = false
  closed = false
  tab.value = next
  name.value = auth.user?.name ?? ''
  currentPassword.value = newPassword.value = confirmPassword.value = ''
  error.value = notice.value = ''
  await nextTick()
  dialog.value?.showModal()
  if (next === 'members') await act(async () => { const records = await listUsers(); if (!closed) members.value = records })
}
async function act(action: () => Promise<void>, message = '') { // 统一串行处理账号操作，错误在当前面板展示
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try { await action(); if (!closed) notice.value = message }
  catch (cause) { if (!closed) error.value = cause instanceof Error ? cause.message : '账号操作失败' }
  finally { busy.value = false }
}
async function saveName() { await act(async () => { const account = await updateProfile(name.value.trim()); if (auth.user?.id === accountId) auth.user = account }, '名称已更新') }
async function savePassword() {
  if (newPassword.value !== confirmPassword.value) { error.value = '两次输入的新密码不一致'; return }
  await act(async () => { await changePassword(currentPassword.value, newPassword.value); currentPassword.value = newPassword.value = confirmPassword.value = '' }, '密码已更新，其他设备的旧登录已失效')
}
async function applyAdmin() { await act(async () => { const account = await requestAdmin(); if (auth.user?.id === accountId) auth.user = account }, '申请已提交，等待管理员批准') }
async function refreshAccount() { await act(async () => { const account = await getCurrentUser(); if (auth.user?.id === accountId) auth.user = account }, '账号状态已刷新') }
async function updateRole(member: UserInfo, role: UserInfo['role']) {
  if (!window.confirm(`确定${role === 'admin' ? '批准管理员申请' : role === 'disabled' ? '停用账号' : '设为普通用户或拒绝管理员申请'}：${member.username}？`)) return
  await act(async () => { const record = await setUserRole(member.id, role); members.value = members.value.map((item) => item.id === record.id ? record : item) }, '账号权限已更新')
}
async function signOut() {
  closed = false // 即使此前关闭过弹窗，退出失败也要在菜单里显示错误
  chat.stopReply() // 退出前中止当前浏览器正在进行的回答
  await act(async () => { await auth.signOut() })
}
function closeAccount() {
  closed = true
  dialog.value?.close()
  currentPassword.value = newPassword.value = confirmPassword.value = ''
}
</script>

<template>
  <div v-if="auth.user" class="account-menu">
    <details ref="menu">
      <summary><span class="avatar">{{ auth.user.name.slice(0, 1).toUpperCase() }}</span><span><strong>{{ auth.user.name }}</strong><small>{{ auth.user.role === 'admin' ? '管理员' : '个人工作空间' }}</small></span><span class="chevron">⌃</span></summary>
      <div class="account-options">
        <small>{{ auth.user.username }}</small>
        <button type="button" :disabled="busy" @click="openAccount('profile')">我的账号</button>
        <button v-if="auth.user.role === 'admin'" type="button" :disabled="busy" @click="openAccount('members')">用户管理</button>
        <button type="button" :disabled="busy" @click="signOut">退出登录</button>
        <p v-if="error && !dialog?.open" role="alert">{{ error }}</p>
      </div>
    </details>
    <dialog ref="dialog" class="account-dialog" aria-label="账号管理" @cancel.prevent="closeAccount" @close="closed = true" @pointerdown="backdrop.pointerdown" @click="backdrop.click">
      <header><h2>{{ tab === 'profile' ? '我的账号' : '用户管理' }}</h2><button type="button" aria-label="关闭账号面板" @click="closeAccount">×</button></header>
      <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <template v-if="tab === 'profile'">
        <p class="muted">{{ auth.user.username }} · {{ auth.user.role === 'admin' ? '管理员' : '普通用户' }}</p>
        <form @submit.prevent="saveName"><label>显示名称<input v-model="name" required maxlength="80" :disabled="busy" autocomplete="name" /></label><button :disabled="busy || !name.trim()" type="submit">保存名称</button></form>
        <form @submit.prevent="savePassword"><h3>修改密码</h3><label>当前密码<input v-model="currentPassword" type="password" required maxlength="128" autocomplete="current-password" :disabled="busy" /></label><label>新密码<input v-model="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" :disabled="busy" /></label><label>确认新密码<input v-model="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" :disabled="busy" /></label><button type="submit" :disabled="busy">更新密码</button></form>
        <section v-if="auth.user.role === 'user'" class="admin-application"><h3>管理员权限</h3><p class="muted">普通账号可正常使用聊天和资料功能。管理员申请需要现有管理员批准。</p><button type="button" :disabled="busy || auth.user.admin_requested" @click="applyAdmin">{{ auth.user.admin_requested ? '申请已提交，等待批准' : '申请管理员权限' }}</button> <button v-if="auth.user.admin_requested" type="button" :disabled="busy" @click="refreshAccount">刷新账号状态</button></section>
      </template>
      <template v-else>
        <p class="muted">管理员只能管理账号权限，不能通过此入口查看他人的私人会话和资料。</p>
        <p v-if="busy" role="status">正在处理…</p>
        <ul class="member-list"><li v-for="member in members" :key="member.id"><div><strong>{{ member.name }}</strong><small>{{ member.username }}</small><small>{{ member.role === 'admin' ? '管理员' : member.role === 'disabled' ? '已停用' : '普通用户' }}{{ member.admin_requested ? ' · 申请管理员中' : '' }}</small></div><div v-if="member.id !== auth.user.id" class="member-actions"><button v-if="member.admin_requested && member.role !== 'disabled'" type="button" :disabled="busy" @click="updateRole(member, 'admin')">批准</button><button v-if="member.admin_requested || member.role !== 'user'" type="button" :disabled="busy" @click="updateRole(member, 'user')">{{ member.admin_requested ? '拒绝申请' : '设为普通用户' }}</button><button v-if="member.role !== 'disabled'" type="button" :disabled="busy" @click="updateRole(member, 'disabled')">停用</button></div><small v-else class="muted">当前账号</small></li></ul>
      </template>
    </dialog>
  </div>
</template>

<style scoped>
.account-menu { position: relative; margin-top: 10px; }summary { display: flex; align-items: center; gap: 10px; padding: 7px; cursor: pointer; border-radius: 8px; list-style: none; }summary::-webkit-details-marker { display: none; }summary:hover { background: #ebefec; }summary strong, summary small { display: block; }summary strong { font-size: 12px; font-weight: 500; }summary small { color: #929f96; font-size: 10px; margin-top: 4px; }.avatar { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%; background: #e4e9e4; color: #667d6c; font-size: 13px; }.chevron { margin-left: auto; color: #8f9d94; }
.account-options { position: absolute; bottom: calc(100% + 8px); left: 0; right: 0; padding: 10px; border: 1px solid #dfe6e1; border-radius: 10px; background: white; box-shadow: 0 8px 25px #20382818; z-index: 35; }.account-options > small { display: block; padding: 6px; font-size: 10px; color: #93a096; overflow-wrap: anywhere; }.account-options > button { display: block; width: 100%; padding: 9px; background: transparent; border: 0; text-align: left; font-size: 12px; }.account-options > button:hover { background: #f1f6f2; }
.account-dialog { width: min(620px, calc(100vw - 30px)); max-height: 85dvh; padding: 24px; border: 1px solid #dce5df; border-radius: 15px; color: #405146; overflow-y: auto; }.account-dialog::backdrop { background: #15291e66; }header { display: flex; justify-content: space-between; align-items: center; }h2 { font-size: 19px; margin: 0; }h3 { font-size: 13px; margin: 18px 0 10px; }header button { font-size: 24px; border: 0; background: none; }.muted { color: #93a096; font-size: 11px; line-height: 1.8; }form, .admin-application { padding: 12px 0; border-bottom: 1px solid #edf0ee; }label { display: block; margin-bottom: 12px; font-size: 12px; }input { display: block; width: 100%; padding: 9px; margin-top: 6px; border: 1px solid #dce5df; border-radius: 7px; }.account-dialog button:not(header button) { padding: 7px 11px; border: 1px solid #d6e3da; background: #f0f6f2; color: #52725e; border-radius: 6px; font-size: 11px; }.error, [role='alert'] { color: #b25b5b; font-size: 12px; }.notice { color: #4b8660; font-size: 12px; }.member-list { padding: 0; list-style: none; }.member-list li { display: flex; gap: 15px; justify-content: space-between; align-items: center; padding: 14px 0; border-bottom: 1px solid #edf0ee; overflow-wrap: anywhere; }.member-list strong { font-size: 12px; }.member-list small { display: block; margin-top: 5px; color: #8a9b8f; font-size: 10px; }.member-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
</style>
