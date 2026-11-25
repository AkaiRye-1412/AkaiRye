<script setup lang="ts">
import PagePlaceholder from '@/components/PagePlaceholder.vue'
</script>

<template>
  <PagePlaceholder
    title="PDF 手册"
    module="M9（FR-RAG-04 / 09 / 12 / 13，Q-03 独立模块）"
    :points="[
      '浏览 pdf_root 全量 PDF（manual 镜像，含 discovered 未入库，Q-15）',
      '按文件夹分组 / 品牌（高创/ACS/Agito 自动识别）/ 状态筛选',
      '点击在页内预览（复用 C2 阅读器，Q-04 双模式）',
      '未入库一键入库（触发后到 M3 知识库页管理队列）',
    ]"
  />
</template>
