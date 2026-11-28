/**
 * 快捷键：动作清单 + 组合键的解析/格式化/匹配。
 *
 * 组合键统一存成规范字符串（修饰键固定顺序 Ctrl → Alt → Shift → Meta，末段是主键），
 * 例如 "Ctrl+K"、"Alt+1"、"Ctrl+Shift+S"。存字符串而不是位掩码，是为了直接写进
 * config.shortcuts（JSON 可读、可手改、跨版本不失效）。
 *
 * 主键一律优先取 e.code（物理键位）：中文/俄文等布局下 e.key 会变成别的字符，
 * 按 e.key 存会让"我明明按的是 K"在换输入法后失效。
 */

export type ShortcutActionId = 'cycle_scope' | 'open_terminal' | 'toggle_dock' | 'close_dock' | 'save_note'

export interface ShortcutAction {
  id: ShortcutActionId
  label: string
  desc: string
  /** 默认组合键；空串 = 默认不绑定 */
  def: string
}

/**
 * 目前真正接线的动作（新增动作时：这里加一条 + 在对应位置用 matchesCombo 判定）。
 *
 * 顺序即优先级：同一组合键被两个动作占用时，**靠前的那条生效**（见 stores/shortcuts.ts
 * 的冲突收敛）。
 */
export const SHORTCUT_ACTIONS: readonly ShortcutAction[] = [
  { id: 'cycle_scope', label: '切换检索范围', desc: '知识助手：当前笔记 → 当前项目 → 全部，依次循环', def: 'Ctrl+K' },
  { id: 'open_terminal', label: '打开终端', desc: '跳转到终端页面（多标签 shell）', def: 'Alt+3' },
  { id: 'toggle_dock', label: '知识助手 / 草稿箱', desc: '右侧面板在两者之间来回切换', def: 'Alt+1' },
  { id: 'close_dock', label: '收起右侧面板', desc: '不分当前是哪个面板，一律收起', def: 'Alt+2' },
  { id: 'save_note', label: '保存笔记', desc: '笔记 / 个人笔记编辑器内立即落盘', def: 'Ctrl+S' },
]

export type ShortcutBindings = Record<ShortcutActionId, string>

export const DEFAULT_BINDINGS: ShortcutBindings = SHORTCUT_ACTIONS.reduce(
  (acc, a) => ({ ...acc, [a.id]: a.def }),
  {} as ShortcutBindings,
)

const MODIFIER_KEYS = new Set(['Control', 'Alt', 'Shift', 'Meta', 'CapsLock', 'Dead', 'Process'])

/** 特殊键的显示名（同时作为存储 token） */
const KEY_ALIAS: Record<string, string> = {
  ' ': 'Space',
  Escape: 'Esc',
  ArrowUp: 'Up',
  ArrowDown: 'Down',
  ArrowLeft: 'Left',
  ArrowRight: 'Right',
  PageUp: 'PgUp',
  PageDown: 'PgDn',
  Insert: 'Ins',
  Delete: 'Del',
  Backspace: 'Backspace',
  Enter: 'Enter',
  Tab: 'Tab',
  Home: 'Home',
  End: 'End',
}

/** 单个按键的 token：字母/数字取物理键位，其余取别名或原字符 */
export function keyTokenOf(e: KeyboardEvent): string {
  const code = e.code || ''
  if (/^Key[A-Z]$/.test(code)) return code.slice(3)
  if (/^Digit[0-9]$/.test(code)) return code.slice(5)
  // 小键盘的数字键与主键盘同义（都当 '1'），运算符等保留区分
  if (code.startsWith('Numpad') && !/^Numpad[0-9]$/.test(code)) return code
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(e.key)) return e.key
  const alias = KEY_ALIAS[e.key]
  if (alias) return alias
  return e.key.length === 1 ? e.key.toUpperCase() : e.key
}

/**
 * 从键盘事件取组合键；只按了修饰键时返回 null（录制中途不该定稿）。
 * e.code 拿不到（老 WebView）时退回 e.key，不致于录不上。
 */
export function comboFromEvent(e: KeyboardEvent): string | null {
  if (!e.key || MODIFIER_KEYS.has(e.key)) return null
  const key = keyTokenOf(e)
  if (!key || MODIFIER_KEYS.has(key)) return null
  const parts: string[] = []
  if (e.ctrlKey) parts.push('Ctrl')
  if (e.altKey) parts.push('Alt')
  if (e.shiftKey) parts.push('Shift')
  if (e.metaKey) parts.push('Meta')
  parts.push(key)
  return parts.join('+')
}

/** 事件是否命中某个组合键（修饰键必须完全一致，多按少按都不算） */
export function matchesCombo(e: KeyboardEvent, combo: string): boolean {
  if (!combo) return false
  const parts = combo.split('+')
  if (parts.length < 2) return false
  const key = parts[parts.length - 1]
  if (e.ctrlKey !== parts.includes('Ctrl')) return false
  if (e.altKey !== parts.includes('Alt')) return false
  if (e.shiftKey !== parts.includes('Shift')) return false
  if (e.metaKey !== parts.includes('Meta')) return false
  return keyTokenOf(e) === key
}

/**
 * 组合键里必须带 Ctrl / Alt / Meta 之一。
 * 只有 Shift 或干脆裸按键的组合，会在打字时把手感毁掉（敲个 '1' 就切面板），
 * 所以录制和保存时统一拦掉。
 */
export function hasCommandModifier(combo: string): boolean {
  const parts = combo.split('+')
  return parts.includes('Ctrl') || parts.includes('Alt') || parts.includes('Meta')
}

/** 展示用：把 token 拼成 Ctrl+Shift+K；空串显示"未绑定" */
export function formatCombo(combo: string): string {
  return combo ? combo : '未绑定'
}
