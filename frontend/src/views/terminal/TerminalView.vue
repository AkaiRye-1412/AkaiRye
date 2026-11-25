<script setup lang="ts">
import PagePlaceholder from '@/components/PagePlaceholder.vue'
</script>

<template>
  <PagePlaceholder
    title="终端"
    module="M5（FR-TERM，三期）"
    :points="[
      '三期实现：ConPTY 真实 shell + xterm.js 多标签',
      '异常恢复：启动时检测上次异常退出 → 全局 toast（Q-07）',
      '「在此目录打开终端」入口仅代码仓库页（Q-08）',
    ]"
  />
</template>
