<script setup lang="ts">
import { siteInfo } from '../stores/site'
</script>

<template>
  <nav v-if="siteInfo.icp_record || siteInfo.gongan_record" class="site-records" aria-label="网站备案信息">
    <a v-if="siteInfo.icp_record" :href="siteInfo.icp_record_url" target="_blank" rel="noopener noreferrer">{{ siteInfo.icp_record }}</a>
    <a v-if="siteInfo.gongan_record" :href="siteInfo.gongan_record_url" target="_blank" rel="noopener noreferrer">
      <!-- 官方备案编号图标：https://beian.mps.gov.cn/static/national%20emblem.png；本地提供避免依赖外部加载。 -->
      <img src="/gongan-record.png" class="gongan-icon" alt="" width="20" height="20" />
      <span>{{ siteInfo.gongan_record }}</span>
    </a>
  </nav>
</template>

<style scoped>
.site-records { display: flex; flex-wrap: wrap; justify-content: center; gap: 4px 14px; margin-top: 8px; font-size: 11px; line-height: 1.6; text-align: center; }
.site-records a { display: inline-flex; align-items: center; justify-content: center; gap: 4px; color: #7b877f; text-decoration: none; overflow-wrap: anywhere; }
.gongan-icon { flex: 0 0 20px; object-fit: contain; }
.site-records a:hover { color: #365c45; text-decoration: underline; }
</style>
