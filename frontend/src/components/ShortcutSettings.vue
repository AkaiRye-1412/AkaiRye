<script setup lang="ts">
/**
 * 快捷键设置弹窗：查看当前绑定、点一下就能重录组合键。
 *
 * 录制用 window 的**捕获阶段**监听并 stopPropagation，否则按下的键会先被
 * App.vue 的全局监听抢走（比如正在录 Alt+1，面板就先切了）。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { useShortcutsStore } from '@/stores/shortcuts'
import { useUiStore } from '@/stores/ui'
import {
  SHORTCUT_ACTIONS,
  comboFromEvent,
  formatCombo,
  hasCommandModifier,
  type ShortcutActionId,
} from '@/utils/shortcuts'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const shortcuts = useShortcutsStore()
const ui = useUiStore()

const recording = ref<ShortcutActionId | null>(null)
const hint = ref('')
const busy = ref(false)

function startRecord(id: ShortcutActionId) {
  recording.value = id
  hint.value = ''
}

function stopRecord() {
  recording.value = null
  hint.value = ''
}

async function apply(id: ShortcutActionId, combo: string) {
  const dup = shortcuts.conflictOf(id, combo)
  if (dup) {
    ui.toast('warn', `「${formatCombo(combo)}」已被「${shortcuts.labelOf(dup)}」占用`)
    stopRecord()
    return
  }
  busy.value = true
  try {
    await shortcuts.setBinding(id, combo)
    stopRecord()
  } catch (e) {
    const err = e as ApiError
    ui.toast('error', `保存失败：${err.message || '未知错误'}`)
  } finally {
    busy.value = false
  }
}

function onCaptureKeydown(e: KeyboardEvent) {
  if (!props.open) return
  // 不在录制态：只接管 Esc 关弹窗
  if (!recording.value) {
    if (e.key === 'Escape') {
      e.stopPropagation()
      close()
    }
    return
  }
  // 录制态：吃掉这次按键，别让它漏到全局快捷键/编辑器里
  e.preventDefault()
  e.stopPropagation()
  if (e.key === 'Escape') {
    stopRecord()
    return
  }
  // 无修饰的 Backspace / Delete = 解绑
  if (!e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey && (e.key === 'Backspace' || e.key === 'Delete')) {
    void apply(recording.value, '')
    return
  }
  if (e.key === 'Backspace' || e.key === 'Delete') {
    hint.value = '解绑请直接按 Backspace（不要加 Ctrl/Alt）'
    return
  }
  const combo = comboFromEvent(e)
  if (!combo) return // 只按了修饰键，继续等主键
  if (!hasCommandModifier(combo)) {
    hint.value = '必须包含 Ctrl / Alt / Meta —— 否则正常打字就会误触'
    return
  }
  void apply(recording.value, combo)
}

async function resetOne(id: ShortcutActionId) {
  if (busy.value) return
  busy.value = true
  try {
    await shortcuts.resetOne(id)
    stopRecord()
  } finally {
    busy.value = false
  }
}

async function resetAll() {
  if (busy.value) return
  busy.value = true
  try {
    await shortcuts.resetAll()
    ui.toast('success', '已恢复默认快捷键')
    stopRecord()
  } finally {
    busy.value = false
  }
}

function close() {
  stopRecord()
  emit('close')
}

watch(
  () => props.open,
  (open) => {
    if (open) window.addEventListener('keydown', onCaptureKeydown, true)
    else window.removeEventListener('keydown', onCaptureKeydown, true)
    if (!open) stopRecord()
  },
  { immediate: true },
)

onBeforeUnmount(() => window.removeEventListener('keydown', onCaptureKeydown, true))
</script>

<template>
  <div v-if="open" class="modal-mask" @click.self="close">
    <div class="modal sc-modal">
      <h3>快捷键</h3>
      <p class="modal-sub">
        点右侧组合键即可重录：按下新的组合（须含 Ctrl / Alt / Meta），Esc 取消，Backspace 解绑。
      </p>

      <div class="sc-list">
        <div v-for="a in SHORTCUT_ACTIONS" :key="a.id" class="sc-row">
          <div class="sc-meta">
            <span class="sc-label">{{ a.label }}</span>
            <span class="sc-desc">{{ a.desc }}</span>
          </div>
          <button
            class="sc-key"
            :class="{ recording: recording === a.id, unbound: !shortcuts.bindings[a.id] }"
            :disabled="busy"
            @click="startRecord(a.id)"
          >
            <template v-if="recording === a.id">按下组合键…</template>
            <template v-else>{{ formatCombo(shortcuts.bindings[a.id]) }}</template>
          </button>
          <button
            class="sc-reset"
            title="恢复该项默认值"
            :disabled="busy || shortcuts.bindings[a.id] === a.def"
            @click="resetOne(a.id)"
          >
            默认
          </button>
        </div>
      </div>

      <p v-if="hint" class="sc-hint">{{ hint }}</p>

      <div class="modal-actions">
        <button class="btn ghost" :disabled="busy" @click="resetAll">全部恢复默认</button>
        <button class="btn primary" @click="close">完成</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(23, 24, 26, 0.32);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 140;
  padding: 20px;
}
.modal {
  background: var(--card);
  border-radius: 14px;
  box-shadow: var(--shadow-pop);
  padding: 20px 22px;
  width: 520px;
  max-height: 88vh;
  overflow: auto;
}
.modal h3 { margin: 0 0 6px; font-size: 15.5px; font-weight: 650; }
.modal-sub { margin: 0 0 14px; font-size: 11.5px; color: var(--muted); line-height: 1.7; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }

.sc-list { display: flex; flex-direction: column; border-top: 1px solid var(--line-soft); }
.sc-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid var(--line-soft);
}
.sc-meta { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.sc-label { font-size: 12.5px; color: var(--ink); font-weight: 560; }
.sc-desc { font-size: 10.5px; color: var(--muted); line-height: 1.5; }

.sc-key {
  flex: none;
  min-width: 108px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--ink-2);
  font-family: var(--mono);
  font-size: 11.5px;
  cursor: pointer;
  transition: border-color 0.14s, background 0.14s, color 0.14s;
}
.sc-key:hover { border-color: var(--blue-line); background: var(--blue-soft); color: var(--blue-ink); }
.sc-key.unbound { color: var(--muted); }
.sc-key.recording {
  border-color: var(--blue);
  background: var(--blue-soft);
  color: var(--blue-ink);
  animation: sc-pulse 1.1s ease-in-out infinite;
}
@keyframes sc-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(42, 120, 214, 0.22); }
  50% { box-shadow: 0 0 0 4px rgba(42, 120, 214, 0.12); }
}

.sc-reset {
  flex: none;
  height: 26px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--muted);
  font-size: 10.5px;
  cursor: pointer;
}
.sc-reset:hover:not(:disabled) { border-color: var(--line); color: var(--ink-2); background: var(--surface); }
.sc-reset:disabled { opacity: 0.4; cursor: default; }

.sc-hint { margin: 10px 0 0; font-size: 10.5px; color: var(--amber-ink); }
</style>
