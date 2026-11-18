import { computed, reactive, readonly } from 'vue'

interface SiteInfo {
  site_name: string
  icp_record: string
  icp_record_url: string
  gongan_record: string
  gongan_record_url: string
}
const state = reactive<SiteInfo>({ site_name: 'ChatFoundry', icp_record: '', icp_record_url: '', gongan_record: '', gongan_record_url: '' })
export const siteInfo = readonly(state)
export const siteName = computed(() => state.site_name)

export async function loadSiteInfo() {
  try {
    const response = await fetch('/api/site-info', { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000) })
    if (!response.ok) return
    const data: SiteInfo = await response.json()
    if (typeof data.site_name !== 'string' || !data.site_name.trim()) return
    state.site_name = data.site_name
    for (const key of ['icp_record', 'icp_record_url', 'gongan_record', 'gongan_record_url'] as const) {
      state[key] = typeof data[key] === 'string' ? data[key] : ''
    }
    document.title = state.site_name
  } catch { /* 配置接口暂不可用时保持默认品牌，不阻塞登录和聊天。 */ }
}
