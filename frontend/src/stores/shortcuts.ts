/** 快捷键绑定状态：真相源是后端 config.shortcuts（DEFAULT_CONFIG 里已有该键）。 */

import { computed } from 'vue'
import { defineStore } from 'pinia'
import { useConfigStore } from '@/stores/config'
import {
  DEFAULT_BINDINGS,
  SHORTCUT_ACTIONS,
  type ShortcutActionId,
  type ShortcutBindings,
} from '@/utils/shortcuts'

export const useShortcutsStore = defineStore('shortcuts', () => {
  const config = useConfigStore()

  /**
   * 生效中的绑定 = 默认值 ← 后端配置。
   * 逐条按 SHORTCUT_ACTIONS 取，是为了老库里缺新动作时能自动落回默认，
   * 也顺带过滤掉后端遗留的未接线键（new_note / send）。
   */
  const bindings = computed<ShortcutBindings>(() => {
    const raw = (config.config?.shortcuts ?? {}) as Record<string, unknown>
    const out: ShortcutBindings = { ...DEFAULT_BINDINGS }
    for (const a of SHORTCUT_ACTIONS) {
      const v = raw[a.id]
      if (typeof v === 'string') out[a.id] = v
    }
    // 冲突收敛：按 SHORTCUT_ACTIONS 的顺序，靠前的那条保留，后面的同键位清空。
    // 老配置里残留的键位、或用户手工改成重复值时，靠这一层保证
    // "一次按键只触发一个动作"，而不是两个动作同时跳。
    const used = new Set<string>()
    for (const a of SHORTCUT_ACTIONS) {
      const combo = out[a.id]
      if (!combo) continue
      if (used.has(combo)) out[a.id] = ''
      else used.add(combo)
    }
    return out
  })

  /** 该组合键已被哪个动作占用（无冲突返回 null） */
  function conflictOf(id: ShortcutActionId, combo: string): ShortcutActionId | null {
    if (!combo) return null
    for (const a of SHORTCUT_ACTIONS) {
      if (a.id !== id && bindings.value[a.id] === combo) return a.id
    }
    return null
  }

  function labelOf(id: ShortcutActionId): string {
    return SHORTCUT_ACTIONS.find((a) => a.id === id)?.label ?? id
  }

  async function saveAll(next: ShortcutBindings) {
    // 保留后端已有、前端暂未接线的键（new_note / send），一次保存别把它们抹掉
    const raw = (config.config?.shortcuts ?? {}) as Record<string, unknown>
    await config.save({ shortcuts: { ...raw, ...next } })
  }

  async function setBinding(id: ShortcutActionId, combo: string) {
    await saveAll({ ...bindings.value, [id]: combo })
  }

  async function resetOne(id: ShortcutActionId) {
    await setBinding(id, DEFAULT_BINDINGS[id])
  }

  async function resetAll() {
    await saveAll({ ...DEFAULT_BINDINGS })
  }

  return { bindings, conflictOf, labelOf, setBinding, resetOne, resetAll }
})
