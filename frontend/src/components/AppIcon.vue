<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 18 }) // 图标名称与尺寸，统一使用线条图标
const paths: Record<string, string> = { // 本地 SVG 路径，不依赖图标服务
  plus: 'M12 5v14M5 12h14',
  search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  back: 'm15 5-7 7 7 7',
  database: 'M20 6c0 2-3.6 4-8 4S4 8 4 6s3.6-4 8-4 8 2 8 4ZM4 6v12c0 2 3.6 4 8 4s8-2 8-4V6M4 12c0 2 3.6 4 8 4s8-2 8-4',
  download: 'M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z',
  chat: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z',
  panel: 'M9 3v18M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h5',
  folder: 'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2',
  arrow: 'M12 19V5M5 12l7-7 7 7',
  close: 'M6 6l12 12M6 18 18 6',
  more: 'M5 11v2M12 11v2M19 11v2',
  edit: 'm16 3 5 5-12 12-6 1 1-6ZM14 5l5 5',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
  attach: 'm8 13 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9a2 2 0 0 1 7 7l-9 9a1 1 0 0 1-3-3l8-8',
  stop: 'M6 6h12v12H6Z',
  refresh: 'M20 7a9 9 0 1 0 1 9M20 2v6h-6',
  chevron: 'm9 5 7 7-7 7',
  check: 'm5 12 4 4L19 6',
  copy: 'M9 9h12v12H9ZM15 9V3H3v12h6',
}
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path :d="paths[name] ?? paths.chat" /></svg>
</template>
