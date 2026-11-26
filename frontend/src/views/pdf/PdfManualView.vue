<script setup lang="ts">
/**
 * M9 PDF 手册（FR-RAG-04 / 09 / 12 / 13，Q-03 独立模块）
 *
 * 双列布局（与售后笔记一致）：左栏常驻手册列表，点任意一本在右栏内嵌阅读，
 * 不再弹层——这样查看手册时列表与筛选条件不会丢，可连续翻阅多本对比。
 * 知识库页里那份仍是 compact 单列 + 弹层（空间小，弹层更合适）。
 */
import PdfManuals from '@/components/PdfManuals.vue'
</script>

<template>
  <div class="page page-pdf">
    <PdfManuals split />
  </div>
</template>

<style scoped>
.page-pdf {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  min-width: 0;
}
.page-pdf :deep(.pdf-wrap) {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
