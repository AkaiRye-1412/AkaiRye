/** 笔记编辑器状态（M2）：当前打开笔记、脏标记、冲突状态。业务实现阶段补充。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useEditorStore = defineStore('editor', () => {
  const currentPath = ref<string | null>(null)
  const dirty = ref(false)
  /** 冲突检测状态（FR-NOTE-05）：none / conflict / resolved。 */
  const conflictState = ref<'none' | 'conflict' | 'resolved'>('none')

  return { currentPath, dirty, conflictState }
})
