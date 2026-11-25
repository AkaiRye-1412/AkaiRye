/** UI 状态：toast、全局 loading。业务实现阶段补充弹窗/快捷键管理。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Toast {
  id: number
  kind: 'info' | 'success' | 'warn' | 'error'
  message: string
}

export const useUiStore = defineStore('ui', () => {
  const toasts = ref<Toast[]>([])

  function toast(kind: Toast['kind'], message: string, ttl = 4000) {
    const t: Toast = { id: ++privateSeq, kind, message }
    toasts.value.push(t)
    window.setTimeout(() => {
      toasts.value = toasts.value.filter((x) => x.id !== t.id)
    }, ttl)
  }

  return { toasts, toast }
})

let privateSeq = 0
