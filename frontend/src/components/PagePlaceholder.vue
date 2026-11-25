<script setup lang="ts">
/** 占位页面通用骨架：业务实现阶段逐页替换。 */
defineProps<{ title: string; module: string; points: string[] }>()
</script>

<template>
  <div class="page-placeholder">
    <h1>{{ title }}</h1>
    <p><b>{{ module }}</b> · 框架占位页面（功能实现阶段开发）</p>
    <ul style="margin-top: 12px; padding-left: 18px; color: var(--text-muted); font-size: 13px">
      <li v-for="p in points" :key="p">{{ p }}</li>
    </ul>
  </div>
</template>
