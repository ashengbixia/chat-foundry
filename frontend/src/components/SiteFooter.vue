<script setup lang="ts">
import { siteInfo } from '../stores/site'
</script>

<template>
  <nav v-if="siteInfo.icp_record || siteInfo.gongan_record" class="site-records" aria-label="网站备案信息">
    <a v-if="siteInfo.icp_record" :href="siteInfo.icp_record_url" target="_blank" rel="noopener noreferrer">{{ siteInfo.icp_record }}</a>
    <a v-if="siteInfo.gongan_record" :href="siteInfo.gongan_record_url" target="_blank" rel="noopener noreferrer">{{ siteInfo.gongan_record }}</a>
  </nav>
</template>

<style scoped>
.site-records { display: flex; flex-wrap: wrap; justify-content: center; gap: 4px 14px; margin-top: 8px; font-size: 11px; line-height: 1.6; text-align: center; }
.site-records a { color: #7b877f; text-decoration: none; overflow-wrap: anywhere; }
.site-records a:hover { color: #365c45; text-decoration: underline; }
</style>
