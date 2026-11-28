<script setup lang="ts">
/**
 * 全局提问输入框（FR-HOME-02）：回车交给右侧知识助手作答。
 *
 * 顶栏与首页看板共用同一份实现，避免"两处逻辑各自漂移"。
 * 注意：这里**不再绑快捷键**——Ctrl+K 已让给「切换检索范围」（见 utils/shortcuts.ts），
 * 想用键盘聚焦提问框，可在左下角「快捷键」里给其它动作分配组合键。
 */
import { ref } from 'vue'
import Icon from '@/components/Icon.vue'
import { useRagStore } from '@/stores/rag'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{ inline?: boolean }>()

const rag = useRagStore()
const ui = useUiStore()
const question = ref('')
const inputEl = ref<HTMLInputElement | null>(null)

function submitAsk() {
  const q = question.value.trim()
  if (!q) return
  rag.askFromGlobal(q)
  question.value = ''
  ui.toast('info', '已发送到右侧知识助手')
}
</script>

<template>
  <div class="ask-box" :class="{ 'ask-inline': props.inline }">
    <Icon name="search" :size="15" />
    <input
      ref="inputEl"
      v-model="question"
      type="text"
      autocomplete="off"
      placeholder="提问或搜索历史处理记录…"
      aria-label="全局提问"
      @keyup.enter="submitAsk"
    />
  </div>
</template>

<style scoped>
/* 内嵌到卡片里时不再参与顶栏的居中分配，直接铺满所在行 */
.ask-box.ask-inline {
  flex: none;
  width: 100%;
  max-width: none;
  height: 38px;
  margin: 0;
}
</style>
