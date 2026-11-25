<script setup lang="ts">
import PagePlaceholder from '@/components/PagePlaceholder.vue'
</script>

<template>
  <PagePlaceholder
    title="存量整理"
    module="M7（FR-CLEAN，三期）"
    :points="[
      '三期实现：扫描 head_ok=false 笔记 → 预览补全 → 写入 → 回滚',
      '整理前自动备份（复用备份服务，kind=cleanup_rollback）',
    ]"
  />
</template>
