<script setup lang="ts">
/**
 * 单栏「即时渲染」Markdown 编辑区（草稿箱纸面用）。
 *
 * 交互对齐 Obsidian 的 Live Preview：整张纸就是一份文档，没有分屏。
 * - 按空行切块（代码围栏内的空行不切），**非编辑中的块直接显示渲染结果**；
 * - 光标落在哪一块，那一块就临时显示 Markdown 原文（带底色的"源码态"），
 *   光标离开或按回车另起一段，立刻排版回渲染结果；
 * - 回车＝另起一段（列表/引用/代码块内仍是换行），退格在块首可合并到上一段。
 *
 * 渲染复用 rag/md 的 marked + DOMPurify + KaTeX 管线，和笔记预览同一套口径。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { renderMarkdown } from '@/rag/md'
import { useShortcutsStore } from '@/stores/shortcuts'
import { copyText } from '@/utils/clipboard'
import { matchesCombo } from '@/utils/shortcuts'

const props = defineProps<{
  modelValue: string
  placeholder?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [string]
  save: []
}>()

const shortcuts = useShortcutsStore()

interface Block {
  id: number
  text: string
}

let seq = 0
const blocks = ref<Block[]>([])
const editingId = ref<number | null>(null)
const wrapEl = ref<HTMLElement | null>(null)

/** 最近一次向父组件发出的串，用来区分「自己发的」和「外部换纸」 */
let lastEmitted = ''
/** 进入某块编辑时光标应落在的位置 */
let pendingCaret = -1

/** 渲染结果按原文缓存：改一段只重算那一段，长草稿也不卡 */
const htmlCache = new Map<string, string>()
function htmlOf(text: string): string {
  if (!text) return ''
  const hit = htmlCache.get(text)
  if (hit !== undefined) return hit
  const html = renderMarkdown(text)
  if (htmlCache.size > 400) htmlCache.clear()
  htmlCache.set(text, html)
  return html
}

const rendered = computed(() =>
  blocks.value.map((b) => (b.id === editingId.value ? '' : htmlOf(b.text))),
)
const isEmpty = computed(() => !blocks.value.some((b) => b.text.trim()))

/** 按空行切块；``` / ~~~ 围栏内的空行不切，保证代码块完整。 */
function splitBlocks(src: string): string[] {
  if (!src.trim()) return ['']
  const out: string[] = []
  let cur: string[] = []
  let fence: string | null = null

  for (const line of src.split('\n')) {
    const m = line.match(/^\s*(```+|~~~+)/)
    if (fence) {
      cur.push(line)
      if (m && line.trim().startsWith(fence)) fence = null
      continue
    }
    if (m) {
      fence = m[1]
      cur.push(line)
      continue
    }
    if (line.trim() === '') {
      if (cur.length) {
        out.push(cur.join('\n'))
        cur = []
      }
    } else {
      cur.push(line)
    }
  }
  if (cur.length) out.push(cur.join('\n'))
  return out.length ? out : ['']
}

function parse(src: string) {
  const list = splitBlocks(src)
  blocks.value = list.map((text) => ({ id: ++seq, text }))
}

/** 落盘串：空块不入盘，块之间用一个空行分隔。 */
function serialize(): string {
  return blocks.value
    .map((b) => b.text.replace(/\s+$/, ''))
    .filter((t) => t.trim() !== '')
    .join('\n\n')
}

function emitValue() {
  const next = serialize()
  if (next === lastEmitted) return
  lastEmitted = next
  emit('update:modelValue', next)
}

onMounted(() => {
  lastEmitted = props.modelValue
  parse(props.modelValue)
})

// 外部换纸 / 重新载入：整篇替换，退出编辑态（光标交给父组件的 focus()）
watch(
  () => props.modelValue,
  (v) => {
    if (v === lastEmitted) return
    lastEmitted = v
    editingId.value = null
    parse(v)
  },
)

/* ---------------- 编辑态 ---------------- */

function autosize(el: HTMLTextAreaElement) {
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

function focusInput() {
  const ta = wrapEl.value?.querySelector<HTMLTextAreaElement>('textarea.blk-input')
  if (!ta) return
  ta.focus()
  const pos = pendingCaret < 0 ? ta.value.length : Math.min(pendingCaret, ta.value.length)
  ta.setSelectionRange(pos, pos)
  pendingCaret = -1
  autosize(ta)
}

async function enterEdit(i: number, caret = -1) {
  const block = blocks.value[i]
  if (!block) return
  editingId.value = block.id
  pendingCaret = caret
  await nextTick()
  focusInput()
}

/** 把点击位置换算成源码里的光标位置（渲染后标记符号消失，只能按文本片段对位）。 */
function caretByPoint(block: Block, el: HTMLElement, x: number, y: number): number {
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
  }
  let node: Node | null = null
  let offset = 0
  const range = doc.caretRangeFromPoint?.(x, y)
  if (range) {
    node = range.startContainer
    offset = range.startOffset
  } else {
    const pos = doc.caretPositionFromPoint?.(x, y)
    if (pos) {
      node = pos.offsetNode
      offset = pos.offset
    }
  }
  if (!node || !el.contains(node)) return -1

  // 元素起点到点击处的纯文本
  const pre = document.createRange()
  pre.selectNodeContents(el)
  pre.setEnd(node, offset)
  const before = pre.toString()
  if (!before) return 0

  // 用递增的文本片段在原文里定位（渲染时去掉了 ** # > 等标记，片段越长越稳）
  for (let n = Math.min(before.length, 10); n >= 2; n--) {
    const frag = before.slice(before.length - n)
    const at = block.text.lastIndexOf(frag)
    if (at >= 0) return at + n
  }
  return -1
}

async function onRenderClick(ev: MouseEvent, i: number) {
  const target = ev.target as HTMLElement

  // 代码块「复制」按钮
  const copyBtn = target.closest('.code-copy') as HTMLElement | null
  if (copyBtn) {
    const code = copyBtn.closest('.code-wrap')?.querySelector('pre')?.textContent ?? ''
    const ok = await copyText(code.replace(/\n$/, ''))
    copyBtn.classList.toggle('done', ok)
    const label = copyBtn.querySelector('span')
    if (label) {
      const old = label.textContent
      label.textContent = ok ? '已复制' : '复制失败'
      window.setTimeout(() => {
        copyBtn.classList.remove('done')
        label.textContent = old || '复制'
      }, 1600)
    }
    return
  }

  // 链接照常打开，不进入编辑
  const link = target.closest('a') as HTMLAnchorElement | null
  const href = link?.getAttribute('href')
  if (href) {
    ev.preventDefault()
    window.open(href, '_blank', 'noopener')
    return
  }

  const el = ev.currentTarget as HTMLElement
  await enterEdit(i, caretByPoint(blocks.value[i], el, ev.clientX, ev.clientY))
}

function onInput(ev: Event, i: number) {
  const ta = ev.target as HTMLTextAreaElement
  blocks.value[i].text = ta.value
  autosize(ta)
  emitValue()
}

/**
 * 失焦后延迟一帧再判断：回车另起一段时会先把旧 textarea 卸载、再聚焦新块，
 * 那次"假失焦"必须忽略，否则刚开的新块立刻又被关掉（焦点丢失、后续打字无响应）。
 */
function onBlur() {
  window.setTimeout(() => {
    const active = document.activeElement
    if (active && wrapEl.value?.contains(active)) return
    if (editingId.value === null) return
    editingId.value = null
    emitValue()
  }, 0)
}

async function insertAfter(i: number, text: string) {
  const nb: Block = { id: ++seq, text }
  blocks.value.splice(i + 1, 0, nb)
  editingId.value = nb.id
  pendingCaret = 0
  emitValue()
  await nextTick()
  focusInput()
}

/** 光标之前是否还在未闭合的代码围栏里 */
function insideFence(src: string, pos: number): boolean {
  const head = src.slice(0, pos)
  const n = (head.match(/^\s*(```+|~~~+)/gm) || []).length
  return n % 2 === 1
}

/**
 * 当前行是不是列表/引用：回车要自动续上同样的标记（和 Obsidian 一致）。
 * 有序列表下一个序号 +1；任务清单续一个未勾选的新项。
 */
function listContext(line: string): { indent: string; prefix: string; rest: string } | null {
  let m = line.match(/^(\s*)([-*+])(\s+\[[ xX]\])?(\s+)(.*)$/)
  if (m) {
    return { indent: m[1], prefix: `${m[2]} ${m[3] ? '[ ] ' : ''}`, rest: m[5] }
  }
  m = line.match(/^(\s*)(\d+)([.)])(\s+)(.*)$/)
  if (m) return { indent: m[1], prefix: `${Number(m[2]) + 1}${m[3]} `, rest: m[5] }
  m = line.match(/^(\s*)>\s?(.*)$/)
  if (m) return { indent: m[1], prefix: '> ', rest: m[2] }
  // 表格：回车只在同一块里换行（不补管道符，避免和用户输入的「|」重复），
  // 否则三行被拆成三个块，表格永远拼不起来。空行再回车才另起一段。
  m = line.match(/^(\s*)\|(.*)$/)
  if (m) return { indent: m[1], prefix: '', rest: line.trim() }
  return null
}

function onKeydown(ev: KeyboardEvent, i: number) {
  // 保存键（默认 Ctrl+S，可在「快捷键」里改）：立刻落盘（保存仍由 store 防抖负责）
  if (matchesCombo(ev, shortcuts.bindings.save_note)) {
    ev.preventDefault()
    emit('save')
    return
  }

  const ta = ev.currentTarget as HTMLTextAreaElement
  const start = ta.selectionStart
  const end = ta.selectionEnd
  const text = ta.value

  // 回车＝另起一段；列表/引用内续行并自动接标记，代码块内换行
  if (ev.key === 'Enter' && !ev.shiftKey && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
    if (insideFence(text, start)) return
    const before = text.slice(0, start)
    const lineStart = before.lastIndexOf('\n') + 1
    const line = before.slice(lineStart)
    const ctx = listContext(line)

    if (ctx) {
      ev.preventDefault()
      if (ctx.rest.trim() === '') {
        // 空的列表项 → 结束列表，另起一段
        blocks.value[i].text = before.slice(0, lineStart).replace(/\n+$/, '')
        emitValue()
        void insertAfter(i, '')
        return
      }
      const insert = `\n${ctx.indent}${ctx.prefix}`
      const next = before + insert + text.slice(start)
      ta.value = next
      blocks.value[i].text = next
      const pos = start + insert.length
      ta.setSelectionRange(pos, pos)
      autosize(ta)
      emitValue()
      return
    }

    ev.preventDefault()
    blocks.value[i].text = before.replace(/[ \t]+$/, '')
    const right = text.slice(start).replace(/^[ \t]+/, '')
    emitValue()
    void insertAfter(i, right)
    return
  }

  // 块首退格 → 并入上一段
  if (ev.key === 'Backspace' && start === 0 && end === 0 && i > 0) {
    ev.preventDefault()
    const prev = blocks.value[i - 1]
    const caret = prev.text.length
    prev.text = prev.text.replace(/\s+$/, '') + text
    blocks.value.splice(i, 1)
    editingId.value = prev.id
    pendingCaret = caret
    emitValue()
    void nextTick().then(focusInput)
    return
  }

  // 上下箭头跨块移动
  if (ev.key === 'ArrowUp' && start === 0 && i > 0) {
    ev.preventDefault()
    void enterEdit(i - 1, blocks.value[i - 1].text.length)
    return
  }
  if (ev.key === 'ArrowDown' && start === text.length && i < blocks.value.length - 1) {
    ev.preventDefault()
    void enterEdit(i + 1, 0)
  }
}

/** 点纸面空白处（最后一块下方）：落到最后一段继续写 */
function onWrapClick(ev: MouseEvent) {
  if (ev.target !== wrapEl.value) return
  const last = blocks.value.length - 1
  if (last < 0) return
  if (editingId.value === blocks.value[last].id) return
  void enterEdit(last, blocks.value[last].text.length)
}

/** 供父组件调用：聚焦纸面（面板展开、换纸、切草稿之后） */
function focus() {
  if (!blocks.value.length) parse('')
  const last = blocks.value.length - 1
  const tail = blocks.value[last]
  // 末尾已是空行：直接在那写；否则补一个空块，让整篇保持渲染态（只把这一行留作输入位）
  if (tail.text.trim() === '') {
    void enterEdit(last, tail.text.length)
    return
  }
  void insertAfter(last, '')
}

defineExpose({ focus })
</script>

<template>
  <div ref="wrapEl" class="live-md" @click="onWrapClick">
    <div v-for="(b, i) in blocks" :key="b.id" class="blk">
      <textarea
        v-if="editingId === b.id"
        class="blk-input"
        rows="1"
        spellcheck="false"
        :placeholder="isEmpty ? placeholder : ''"
        :value="b.text"
        @input="onInput($event, i)"
        @keydown="onKeydown($event, i)"
        @blur="onBlur"
      />
      <div
        v-else-if="b.text.trim()"
        class="blk-render md"
        @click="onRenderClick($event, i)"
        v-html="rendered[i]"
      />
      <div
        v-else
        class="blk-empty"
        @click="enterEdit(i, 0)"
      >
        <span v-if="isEmpty" class="blk-hint">{{ placeholder }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.live-md {
  flex: 1;
  min-height: 0;
  overflow: auto;
  width: 100%;
  border: 1px solid rgba(231, 229, 221, 0.9);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.86);
  padding: 14px 16px 22px;
  font-family: var(--font);
  font-size: 13.5px;
  line-height: 1.85;
  color: var(--ink);
  transition: border-color 0.14s, box-shadow 0.14s;
}
.live-md:focus-within {
  border-color: var(--blue-line);
  box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.09);
}

.blk + .blk {
  margin-top: 7px;
}

/* ---- 编辑态：只有这一块露出 Markdown 原文 ---- */
.blk-input {
  display: block;
  width: 100%;
  min-height: 0;
  resize: none;
  overflow: hidden;
  box-sizing: border-box;
  border: 0;
  outline: none;
  border-radius: 6px;
  background: rgba(244, 244, 237, 0.92);
  box-shadow: inset 2px 0 0 rgba(42, 120, 214, 0.35);
  padding: 2px 7px 2px 9px;
  margin: 0 -7px;
  font-family: var(--font);
  font-size: 13.5px;
  line-height: 1.85;
  color: var(--ink);
  caret-color: var(--blue);
}
.blk-input::placeholder {
  color: #a9a79f;
}

/* ---- 空块：留一条可点击的线 ---- */
.blk-empty {
  min-height: 26px;
  border-radius: 7px;
  cursor: text;
  display: flex;
  align-items: center;
}
.blk-empty:hover {
  background: rgba(246, 246, 241, 0.7);
}
.blk-hint {
  color: #a9a79f;
  white-space: pre-line;
  font-size: 13px;
  line-height: 1.9;
  pointer-events: none;
}

/* ---- 渲染态（v-html，需要 :deep） ---- */
.blk-render {
  cursor: text;
  word-break: break-word;
}
.blk-render :deep(h1) {
  font-size: 18px;
  margin: 6px 0 10px;
  line-height: 1.5;
}
.blk-render :deep(h2) {
  font-size: 15.5px;
  margin: 12px 0 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--line-soft);
}
.blk-render :deep(h3) {
  font-size: 13.5px;
  margin: 11px 0 5px;
}
.blk-render :deep(h4),
.blk-render :deep(h5),
.blk-render :deep(h6) {
  font-size: 13px;
  margin: 9px 0 4px;
}
.blk-render :deep(p) {
  margin: 0 0 6px;
  line-height: 1.85;
  color: var(--ink-2);
}
.blk-render :deep(p:last-child) {
  margin-bottom: 0;
}
.blk-render :deep(ul),
.blk-render :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
  line-height: 1.85;
  color: var(--ink-2);
}
.blk-render :deep(li) {
  margin: 1px 0;
}
.blk-render :deep(li > input[type='checkbox']) {
  margin-right: 5px;
}
.blk-render :deep(strong) {
  color: var(--ink);
  font-weight: 600;
}
.blk-render :deep(em) {
  color: var(--ink);
}
.blk-render :deep(a) {
  color: var(--blue-ink);
}
.blk-render :deep(del) {
  color: var(--muted);
}
.blk-render :deep(blockquote) {
  margin: 4px 0;
  padding: 2px 0 2px 10px;
  border-left: 2px solid var(--line);
  color: var(--muted);
}
.blk-render :deep(blockquote p) {
  margin: 2px 0;
  color: var(--muted);
}
.blk-render :deep(code) {
  font-family: var(--mono, ui-monospace, SFMono-Regular, Consolas, monospace);
  font-size: 12px;
  padding: 1px 4px;
  border-radius: 4px;
  background: #f3f2ec;
  color: #8a5a2b;
}
.blk-render :deep(table) {
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 12px;
  width: 100%;
}
.blk-render :deep(th),
.blk-render :deep(td) {
  border: 1px solid var(--line-soft);
  padding: 5px 8px;
  text-align: left;
}
.blk-render :deep(th) {
  background: #faf9f5;
  font-weight: 600;
}
.blk-render :deep(img) {
  max-width: 100%;
  border-radius: 8px;
}
.blk-render :deep(hr) {
  border: 0;
  border-top: 1px solid var(--line-soft);
  margin: 12px 0;
}
.blk-render :deep(.katex) {
  font-size: 1.04em;
}
.blk-render :deep(.katex-display) {
  margin: 8px 0;
  padding: 2px 0;
  overflow-x: auto;
  overflow-y: hidden;
}
.blk-render :deep(.code-wrap) {
  position: relative;
  margin: 6px 0;
}
.blk-render :deep(pre) {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f7f6f1;
  border: 1px solid var(--line-soft);
  overflow: auto;
  font-size: 12px;
  line-height: 1.7;
}
.blk-render :deep(pre code) {
  background: none;
  padding: 0;
  color: var(--ink-2);
}
.blk-render :deep(.code-copy) {
  position: absolute;
  top: 6px;
  right: 6px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 7px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: #fff;
  color: var(--muted);
  font-size: 10.5px;
  opacity: 0;
  transition: opacity 0.12s;
}
.blk-render :deep(.code-wrap:hover .code-copy) {
  opacity: 1;
}
.blk-render :deep(.code-copy svg) {
  width: 11px;
  height: 11px;
}
.blk-render :deep(.code-copy.done) {
  color: var(--green-ink);
  border-color: var(--green-line);
  background: var(--green-soft);
}
</style>
