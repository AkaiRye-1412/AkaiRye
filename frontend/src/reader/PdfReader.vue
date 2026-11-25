<script setup lang="ts">
/**
 * C2 PDF 阅读器组件（统一 PDF 呈现层，Q-04 双模式）：
 * - 已入库：RAG 来源跳转（跳页 + 高亮，FR-RAG-24）；
 * - 未入库：M9 手册页预览。
 * 业务实现阶段引入 pdf.js；字节流端点 GET /api/reader/pdf/{manual_id}（支持 Range）。
 */
defineProps<{ manualId: number | null }>()
</script>

<template>
  <div class="pdf-reader">
    <p v-if="manualId === null" class="pdf-hint">未选择 PDF</p>
    <object v-else :data="`/api/reader/pdf/${manualId}`" type="application/pdf" class="pdf-object">
      <!-- TODO(业务实现): 替换为 pdf.js 渲染（跳页/高亮/多标签/并排对比） -->
      <p class="pdf-hint">pdf.js 阅读器在业务实现阶段接入</p>
    </object>
  </div>
</template>

<style scoped>
.pdf-reader, .pdf-object { width: 100%; height: 100%; }
.pdf-hint { color: var(--text-muted); font-size: 13px; padding: 16px; }
</style>
