<script setup lang="ts">
/**
 * C1 RAG 常驻面板（Q-02：全局常驻，所有页面共享同一实例）。
 * 能力：会话管理（FR-RAG-25/26）、范围切换（FR-RAG-17/18）、流式回答（Q-14）、
 * 引用来源层（FR-RAG-21）、笔记来源跳转（FR-RAG-24 笔记侧）。
 *
 * 2026-11-26 修订：会话操作改为显式按键（新建会话 / 清空当前会话），
 * 范围切换改分段控件并显式携带上下文（store.context），窗口可收起为窄轨。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '@/components/Icon.vue'
import type { RagCitation } from '@/api'
import { useRagStore, type ScopeMode } from '@/stores/rag'
import { useUiStore } from '@/stores/ui'
import { copyText } from '@/utils/clipboard'
import { renderMarkdown } from './md'

const rag = useRagStore()
const ui = useUiStore()
const router = useRouter()

const draft = ref('')
const listEl = ref<HTMLElement | null>(null)
const menuOpen = ref(false)
const menuEl = ref<HTMLElement | null>(null)
// 会话选择用自绘下拉：原生 <select> 的弹层由浏览器绘制，
// 在本工作台（预览面板 iframe / 缩放）里会飘到页面左上角。
const sessOpen = ref(false)
const sessEl = ref<HTMLElement | null>(null)

// 在 PDF 手册页打开某本手册时，「当前笔记」这一档实际指向的是这本手册（PdfManuals
// 会上报 manualId），标签跟着换，否则面板显示"当前笔记"却没有任何笔记，容易误会。
const SCOPES = computed<{ value: ScopeMode; label: string }[]>(() => [
  { value: 'current', label: rag.context.manualId ? '当前手册' : '当前笔记' },
  { value: 'project', label: '当前项目' },
  { value: 'all', label: '全部' },
])

const EXAMPLES = [
  '星河纸业的张力问题怎么处理的？',
  '介绍下 Python 集合',
  '__main__ 是什么意思',
]

// 思考计时：思考型模型（glm 等）先产出 reasoning_content，正文可能迟 30-60s 才出现，
// 期间气泡为空会让用户以为卡死 → 显示"深度思考中 + 秒数"
const thinkSecs = ref(0)
let thinkTimer: ReturnType<typeof setInterval> | null = null
const stopThinkTimer = () => {
  if (thinkTimer) {
    clearInterval(thinkTimer)
    thinkTimer = null
  }
}
watch(
  () => [rag.streaming?.id, rag.streaming?.content?.length ?? 0],
  ([sid, len]) => {
    const streaming = !!sid
    const isEmpty = (len as number) === 0
    if (streaming && isEmpty && !thinkTimer) {
      thinkSecs.value = 0
      thinkTimer = setInterval(() => {
        thinkSecs.value += 1
        if (thinkSecs.value > 300) stopThinkTimer()
      }, 1000)
    } else if (!streaming || !isEmpty) {
      stopThinkTimer()
    }
  },
)
onUnmounted(stopThinkTimer)

// 消费全局提问（FR-HOME-02：首页顶部提问框回车 → 唤起面板并执行）
watch(
  () => rag.pendingAsk,
  async (q) => {
    if (!q) return
    const question = rag.consumePendingAsk() ?? ''
    if (question) {
      await rag.send(question)
      scrollToBottom()
    }
  },
)

// 点击空白处关闭浮层（会话下拉 / 更多菜单）
function onDocClick(e: MouseEvent) {
  const t = e.target as Node
  if (sessOpen.value && sessEl.value && !sessEl.value.contains(t)) sessOpen.value = false
  if (menuOpen.value && menuEl.value && !menuEl.value.contains(t)) menuOpen.value = false
}
onMounted(async () => {
  document.addEventListener('click', onDocClick)
  try {
    await rag.loadSessions()
    // 刷新后自动进入最近的会话：否则下拉一直显示"选择会话…"，
    // 已有历史看起来像丢了（会话列表按 updated_at 倒序）。
    if (!rag.currentSessionId && rag.sessions.length) {
      await rag.switchSession(rag.sessions[0].id)
    }
  } catch {
    /* 面板常驻，后端未就绪时静默（重连对账兜底） */
  }
  // 面板由收起态被全局提问唤起时，pendingAsk 在挂载前已置位，
  // watch 不会触发 → 这里补消费一次，避免问题被吞掉。
  if (rag.pendingAsk) {
    const q = rag.consumePendingAsk()
    if (q) {
      await rag.send(q)
      scrollToBottom()
    }
  }
})
onUnmounted(() => document.removeEventListener('click', onDocClick))

async function onSend() {
  const q = draft.value.trim()
  if (!q || rag.generating) return
  draft.value = ''
  await rag.send(q)
  scrollToBottom()
}

async function quickAsk(q: string) {
  if (rag.generating) return
  await rag.send(q)
  scrollToBottom()
}

function scrollToBottom() {
  nextTick(() => listEl.value?.scrollTo({ top: listEl.value.scrollHeight, behavior: 'smooth' }))
}

/** 自绘下拉：点选会话（生成中禁止切换，避免流式消息串到别的会话）。 */
async function pickSession(id: number) {
  if (rag.generating) {
    ui.toast('info', '回答生成中，暂不能切换会话')
    return
  }
  sessOpen.value = false
  if (id === rag.currentSessionId) return
  await rag.switchSession(id)
  scrollToBottom()
}

const currentTitle = computed(() => {
  const s = rag.sessions.find((x) => x.id === rag.currentSessionId)
  if (s) return s.title
  return rag.sessions.length ? '选择会话…' : '暂无会话'
})

/** ISO(UTC) → MM-DD HH:mm（本地时区）；解析失败返回空，不显示脏字符串。 */
function shortTime(iso?: string): string {
  if (!iso) return ''
  const hasZone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso)
  const d = new Date(hasZone ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function onNewSession() {
  if (rag.generating) return
  await rag.newSession()
  menuOpen.value = false
  sessOpen.value = false
  draft.value = ''
  ui.toast('success', '已新建会话')
}

/** 清空"当前会话"的消息（保留会话，可继续追问）。 */
async function onClearCurrent() {
  if (!rag.currentSessionId) {
    ui.toast('info', '当前没有会话')
    return
  }
  if (!rag.messages.length && !rag.streaming) {
    ui.toast('info', '当前会话还没有消息')
    return
  }
  if (rag.generating) {
    ui.toast('info', '回答生成中，请稍后再清空')
    return
  }
  if (!window.confirm('清空当前会话的全部消息？会话本身保留，此操作不可恢复。')) return
  const cleared = await rag.clearCurrentSession()
  ui.toast('success', `已清空当前会话（${cleared} 条消息）`)
}

async function onRenameSession() {
  const s = rag.sessions.find((x) => x.id === rag.currentSessionId)
  if (!s) {
    ui.toast('info', '请先选择要重命名的会话')
    return
  }
  const title = window.prompt('重命名会话', s.title)
  if (title && title.trim()) {
    await rag.renameSession(s.id, title.trim())
    ui.toast('success', '已重命名')
  }
  menuOpen.value = false
}

async function onDeleteSession() {
  const s = rag.sessions.find((x) => x.id === rag.currentSessionId)
  if (!s) {
    ui.toast('info', '请先选择要删除的会话')
    return
  }
  menuOpen.value = false
  if (window.confirm(`删除会话「${s.title}」及其全部消息？`)) {
    await rag.deleteSession(s.id)
    ui.toast('success', '会话已删除')
  }
}

async function onClearAll() {
  menuOpen.value = false
  if (window.confirm('清空全部会话？此操作不可恢复。')) {
    await rag.clearAllSessions()
    ui.toast('success', '已清空全部会话')
  }
}

function setScope(mode: ScopeMode) {
  if (rag.scopeMode === mode) return
  rag.scopeMode = mode
}

/** 笔记来源跳转（FR-RAG-24 笔记侧）：工作台内打开 M2 编辑器。 */
function openNote(notePath: string) {
  router.push({ path: '/notes', query: { path: notePath } })
}

/** 范围提示与真实生效口径保持一致：解析不到上下文时后端按全库检索。 */
const scopeHint = computed(() => {
  const c = rag.context
  if (rag.scopeMode === 'all') return { text: '检索全部已索引笔记与文件夹', warn: false }
  if (rag.scopeMode === 'project') {
    // 「当前项目」按客户（name）聚合：该客户名下的卡片与项目笔记都会纳入
    const name = c.customer || c.project
    return name
      ? { text: `本项目：${name}`, warn: false }
      : { text: '未感知到项目，已按全库检索', warn: true }
  }
  // 手册页：current 档就是这一本 PDF（后端按 manualId 过滤，不会混进笔记块）
  if (c.manualName) {
    if (c.manualIndexed === false) {
      return { text: `《${c.manualName}》尚未入库，检索为空`, warn: true }
    }
    return {
      text: `当前手册《${c.manualName}》`,
      warn: false,
    }
  }
  // 笔记页：「当前笔记」= 笔记标题，后面挂客户/项目便于确认范围
  const note = c.noteTitle || c.notePath?.replace(/\.md$/i, '')
  if (note) {
    // 项目型笔记的标题常与项目名同名（玻璃切割），去重避免「玻璃切割 · 易高 · 玻璃切割」
    const sub = [c.customer, c.project].filter((x) => x && x !== note).join(' · ')
    return { text: `当前笔记：${note}${sub ? ` · ${sub}` : ''}`, warn: false }
  }
  const parts = [c.customer && `客户 ${c.customer}`, c.project && `项目 ${c.project}`].filter(Boolean)
  return parts.length
    ? { text: `当前笔记：${parts.join(' · ')}`, warn: false }
    : { text: '未感知到笔记，已按全库检索', warn: true }
})

/** 点来源标签跳转：优先落到仍有效的引用所指向的笔记。 */
/**
 * 引用跳转（FR-RAG-24）：
 * - 笔记来源 → 打开工作台编辑器；
 * - 手册来源 → 跳 M9 PDF 手册页并直接定位到引用页（#page）。
 */
function openCitation(g: CitGroup) {
  const note =
    g.items.find((c) => c.note_path && c.chunk_valid) || g.items.find((c) => !!c.note_path)
  if (note?.note_path) {
    openNote(note.note_path)
    return
  }
  const manual =
    g.items.find((c) => c.manual_id && c.chunk_valid) || g.items.find((c) => !!c.manual_id)
  if (manual?.manual_id) {
    router.push({
      path: '/pdf',
      query: { manual: String(manual.manual_id), page: String(manual.page || 1) },
    })
  }
}

/**
 * 代码块复制：按钮由 md.ts 生成在 v-html 里（拿不到 Vue 事件），
 * 统一在消息列表做事件委托。
 */
async function onCopyCode(e: MouseEvent) {
  const btn = (e.target as HTMLElement | null)?.closest('.code-copy') as HTMLButtonElement | null
  if (!btn) return
  const codeEl = btn.closest('.code-wrap')?.querySelector('pre')
  // marked 的 <pre> 末尾常带一个换行，复制时去掉，粘到终端/编辑器里不会多空一行
  const text = (codeEl?.textContent ?? '').replace(/\s+$/, '')
  const label = btn.querySelector('span')
  const ok = await copyText(text)
  btn.classList.toggle('done', ok)
  if (label) label.textContent = ok ? '已复制' : '复制失败'
  window.setTimeout(() => {
    // 流式回答每帧都会重渲染，旧按钮可能已脱离文档，脱离就不用还原了
    if (!btn.isConnected) return
    btn.classList.remove('done')
    if (label) label.textContent = '复制'
  }, 1600)
}

/** 来源只展示文档名（用户口径：能看出出自哪篇即可）；章节与片段放 tooltip。 */
function docName(ct: RagCitation): string {
  if (ct.note_path) return ct.note_path.split('/').pop() || ct.source_name
  return ct.source_name
}

/** 按文档归并引用：同一篇被引多次只出现一次，编号并入同一标签（[1][2]…）。 */
interface CitGroup {
  doc: string
  nums: number[]
  items: RagCitation[]
}
function groupCitations(list: RagCitation[]): CitGroup[] {
  const map = new Map<string, CitGroup>()
  list.forEach((ct, i) => {
    const doc = docName(ct)
    const hit = map.get(doc)
    if (hit) {
      hit.nums.push(i + 1)
      hit.items.push(ct)
    } else {
      map.set(doc, { doc, nums: [i + 1], items: [ct] })
    }
  })
  return [...map.values()]
}

function groupTip(g: CitGroup): string {
  const lines = [`${g.doc}（引用 ${g.nums.length} 处）`]
  const chapters = g.items.map((c) => c.chapter).filter(Boolean) as string[]
  lines.push(...chapters.slice(0, 6))
  if (chapters.length > 6) lines.push(`…等 ${chapters.length} 个章节`)
  return lines.join('\n')
}

const canClear = computed(() => !!rag.messages.length || !!rag.streaming)
</script>

<template>
  <div class="rag-panel">
    <header class="rag-head">
      <div class="rag-head-top">
        <span class="rag-title">
          <Icon name="sparkle" :size="15" />
          知识助手
          <span v-if="rag.generating" class="busy-dot" title="正在生成" />
        </span>
        <div class="head-right">
          <div ref="menuEl" class="more-wrap">
            <button class="icon-btn" title="更多会话操作" @click.stop="menuOpen = !menuOpen">
              <Icon name="more" :size="16" />
            </button>
            <div v-if="menuOpen" class="more-menu" @click.stop>
              <button @click="onRenameSession">
                <Icon name="edit" :size="14" />重命名当前会话
              </button>
              <button @click="onDeleteSession">
                <Icon name="trash" :size="14" />删除当前会话
              </button>
              <button class="danger" @click="onClearAll">
                <Icon name="eraser" :size="14" />清空全部会话
              </button>
            </div>
          </div>
          <button class="icon-btn" title="收起面板" @click="rag.panelOpen = false">
            <Icon name="chevronRight" :size="16" />
          </button>
        </div>
      </div>

      <div class="rag-ops">
        <div ref="sessEl" class="sess-wrap">
          <button
            class="sess-btn"
            :class="{ open: sessOpen }"
            :disabled="rag.generating"
            :title="rag.generating ? '生成中，暂不能切换会话' : currentTitle"
            @click.stop="sessOpen = !sessOpen"
          >
            <Icon name="layers" :size="13" class="sess-ic" />
            <span class="sess-name">{{ currentTitle }}</span>
            <Icon name="chevronDown" :size="12" class="caret" :class="{ open: sessOpen }" />
          </button>

          <div v-if="sessOpen" class="sess-menu" @click.stop>
            <button
              v-for="s in rag.sessions"
              :key="s.id"
              class="sess-item"
              :class="{ active: s.id === rag.currentSessionId }"
              @click="pickSession(s.id)"
            >
              <span class="sess-item-main">
                <span class="t">{{ s.title }}</span>
                <span class="d">{{ shortTime(s.updated_at) }}</span>
              </span>
              <Icon
                v-if="s.id === rag.currentSessionId"
                name="checkCircle"
                :size="13"
                class="check"
              />
            </button>
            <p v-if="!rag.sessions.length" class="sess-empty">
              还没有会话，点右侧「新建会话」开始
            </p>
          </div>
        </div>
        <button
          class="btn sm primary"
          :disabled="rag.generating"
          title="新建会话"
          @click="onNewSession"
        >
          <Icon name="plus" :size="14" />新建会话
        </button>
        <button
          class="btn sm ghost"
          :disabled="!canClear || rag.generating"
          title="清空当前会话的消息"
          @click="onClearCurrent"
        >
          <Icon name="eraser" :size="14" />清空
        </button>
      </div>

      <div class="scope-block">
        <div class="seg sm scope-seg">
          <button
            v-for="s in SCOPES"
            :key="s.value"
            class="seg-btn"
            :class="{ active: rag.scopeMode === s.value }"
            @click="setScope(s.value)"
          >
            {{ s.label }}
          </button>
        </div>
        <span class="ctx-hint" :class="{ warn: scopeHint.warn }" :title="scopeHint.text">
          {{ scopeHint.text }}
        </span>
      </div>
    </header>

    <div ref="listEl" class="msg-list" @click="onCopyCode">
      <div v-if="!rag.messages.length && !rag.streaming" class="rag-empty">
        <div class="empty-ic"><Icon name="sparkle" :size="20" /></div>
        <p class="empty-title">向知识库提问</p>
        <p class="empty-sub">检索范围由上方切换；回答带来源角标，可点开对应笔记。</p>
        <div class="chips">
          <button v-for="q in EXAMPLES" :key="q" class="chip" @click="quickAsk(q)">{{ q }}</button>
        </div>
      </div>

      <template v-for="m in rag.messages" :key="m.id">
        <div v-if="m.role === 'user'" class="msg user">{{ m.content }}</div>
        <div v-else class="msg assistant">
          <div class="content md" v-html="renderMarkdown(m.content)" />
          <div v-if="m.citations && m.citations.length" class="citations">
            <div class="cit-title">
              来源<template v-if="groupCitations(m.citations).length > 1">
                （{{ groupCitations(m.citations).length }}）</template
              >
            </div>
            <div class="cit-list">
              <span
                v-for="g in groupCitations(m.citations)"
                :key="g.doc"
                class="cit"
                :class="{ link: (g.items[0].note_path || g.items[0].manual_id) && g.items.some((c) => c.chunk_valid) }"
                :title="groupTip(g)"
                @click="openCitation(g)"
              >
                <span class="cit-name">{{ g.doc }}</span>
                <span
                  v-if="g.items[0].note_path && !g.items.some((c) => c.chunk_valid)"
                  class="cit-stale"
                  >失效</span
                >
              </span>
            </div>
          </div>
        </div>
      </template>

      <div v-if="rag.streaming" class="msg assistant streaming">
        <div v-if="!rag.streaming.content" class="thinking">
          <span class="dots"><i /><i /><i /></span>
          深度思考中{{ thinkSecs ? ` ${thinkSecs}s` : '' }}（检索完成，等待模型推理）
        </div>
        <div v-else class="content md" v-html="renderMarkdown(rag.streaming.content)" />
      </div>

      <p v-if="rag.lastError" class="err">{{ rag.lastError }}</p>
    </div>

    <div class="input-row">
      <textarea
        v-model="draft"
        class="ask-input"
        rows="2"
        placeholder="输入问题，Enter 发送（Shift+Enter 换行）"
        :disabled="rag.generating"
        @keydown.enter.exact.prevent="onSend"
      />
      <button class="btn primary send-btn" :disabled="rag.generating || !draft.trim()" @click="onSend">
        <Icon name="send" :size="14" />
        {{ rag.generating ? '生成中' : '发送' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.rag-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  width: 100%;
  /* 底色交给外层 .rag-dock（半透明磨砂），面板自身透明才能透出壁纸 */
  background: transparent;
}

/* ---- 头部 ---- */
.rag-head {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 12px 14px 11px;
  border-bottom: 1px solid var(--line-soft);
  flex: none;
}
.rag-head-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rag-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13.5px;
  font-weight: 650;
  letter-spacing: -0.1px;
  color: var(--ink);
}
.rag-title :deep(svg) { color: var(--blue); }
.busy-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--blue);
  margin-left: 2px;
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: 0.25; } 50% { opacity: 1; } }
.head-right { display: flex; align-items: center; gap: 2px; }

.rag-ops { display: flex; align-items: center; gap: 6px; }

/* 会话选择：自绘下拉（原生 select 弹层会飘到页面左上角） */
.sess-wrap { position: relative; flex: 1; min-width: 0; }
.sess-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 29px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  font-size: 12.5px;
  color: var(--ink-2);
  text-align: left;
  transition: border-color 0.14s, box-shadow 0.14s;
}
.sess-btn:hover:not(:disabled) { border-color: #dcd9cf; }
.sess-btn.open,
.sess-btn:focus-visible {
  outline: none;
  border-color: var(--blue-line);
  box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.1);
}
.sess-btn:disabled { background: #f8f8f4; color: var(--muted); cursor: not-allowed; }
.sess-ic { color: var(--blue); flex: none; }
.sess-btn:disabled .sess-ic { color: var(--muted); }
.sess-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.caret { flex: none; color: var(--muted); transition: transform 0.15s; }
.caret.open { transform: rotate(180deg); }

.sess-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 40;
  width: max(100%, 220px);
  max-height: 260px;
  overflow-y: auto;
  padding: 4px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: var(--shadow-pop);
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.sess-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 7px;
  border: 0;
  background: transparent;
  border-radius: 7px;
  font-size: 12.5px;
  color: var(--ink-2);
  text-align: left;
}
.sess-item:hover { background: #f4f3ee; color: var(--ink); }
.sess-item.active { background: var(--blue-soft); color: var(--blue-ink); }
.sess-item-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.sess-item .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-item .d { font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
.sess-item .check { color: var(--blue); flex: none; }
.sess-empty { margin: 0; padding: 8px 7px; font-size: 11.5px; color: var(--muted); }

/* 更多菜单 */
.more-wrap { position: relative; }
.more-menu {
  position: absolute;
  top: 34px;
  right: 0;
  z-index: 30;
  min-width: 168px;
  padding: 5px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: var(--shadow-pop);
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.more-menu button {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 8px;
  border: 0;
  background: transparent;
  border-radius: 7px;
  font-size: 12.5px;
  color: var(--ink-2);
  text-align: left;
}
.more-menu button:hover { background: #f4f3ee; color: var(--ink); }
.more-menu button.danger { color: var(--red); }
.more-menu button.danger:hover { background: var(--red-soft); }

/* ---- 范围切换 ---- */
.scope-block { display: flex; flex-direction: column; align-items: flex-start; gap: 5px; min-width: 0; }
.scope-seg { flex: none; }
.ctx-hint {
  font-size: 11px;
  line-height: 1.4;
  color: var(--muted);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctx-hint.warn { color: var(--amber-ink); }

/* ---- 消息区 ---- */
.msg-list { flex: 1; overflow-y: auto; min-height: 0; padding: 12px 14px 4px; }

.rag-empty { display: flex; flex-direction: column; align-items: center; text-align: center; padding: 26px 4px 10px; }
.empty-ic {
  width: 42px;
  height: 42px;
  border-radius: 13px;
  display: grid;
  place-items: center;
  background: var(--blue-soft);
  color: var(--blue);
  margin-bottom: 10px;
}
.empty-title { font-size: 13px; font-weight: 620; margin: 0 0 3px; color: var(--ink); }
.empty-sub { font-size: 11.5px; line-height: 1.6; color: var(--muted); margin: 0 0 12px; max-width: 260px; }
.chips { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.chip {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink-2);
  border-radius: 9px;
  padding: 7px 10px;
  font-size: 12px;
  text-align: left;
  transition: border-color 0.14s, background 0.14s, color 0.14s;
}
.chip:hover { border-color: var(--blue-line); background: var(--blue-soft); color: var(--blue-ink); }

.msg { border-radius: 10px; padding: 8px 11px; margin-bottom: 9px; line-height: 1.6; word-break: break-word; font-size: 12.5px; }
.msg.user {
  background: rgba(42, 120, 214, 0.78);
  color: #fff;
  margin-left: 34px;
  border-bottom-right-radius: 4px;
  white-space: pre-wrap;
  backdrop-filter: var(--glass-blur);
}
.msg.assistant {
  background: rgba(255, 255, 255, 0.48);
  border: 1px solid rgba(255, 255, 255, 0.35);
  margin-right: 10px;
  border-bottom-left-radius: 4px;
  backdrop-filter: var(--glass-blur);
}
.msg.streaming .content { color: var(--ink-2); }

/* 思考中提示（思考型模型 reasoning 阶段无正文） */
.thinking { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12.5px; padding: 2px 0; }
.thinking .dots { display: inline-flex; gap: 3px; }
.thinking .dots i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--blue);
  opacity: 0.35;
  animation: think-bounce 1.2s infinite ease-in-out;
}
.thinking .dots i:nth-child(2) { animation-delay: 0.15s; }
.thinking .dots i:nth-child(3) { animation-delay: 0.3s; }
@keyframes think-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
  40% { transform: translateY(-4px); opacity: 1; }
}
/* 流式光标：贴在渲染内容末尾 */
.msg.streaming .content::after {
  content: '▍';
  color: var(--blue);
  animation: blink 1s step-start infinite;
}
@keyframes blink { 50% { opacity: 0; } }

/* Markdown 渲染样式（v-html 内容需 :deep） */
.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) {
  font-size: 13px;
  margin: 10px 0 4px;
  padding-bottom: 2px;
  border-bottom: 1px solid var(--line-soft);
  font-weight: 620;
}
.md :deep(h1) { font-size: 14px; }
.md :deep(p) { margin: 4px 0; }
.md :deep(ul), .md :deep(ol) { margin: 4px 0; padding-left: 20px; }
.md :deep(li) { margin: 2px 0; }
.md :deep(code) {
  font-family: var(--mono);
  font-size: 11.5px;
  background: #f1f0eb;
  border-radius: 4px;
  padding: 1px 4px;
}
.md :deep(pre) {
  background: #f7f6f2;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  padding: 8px 10px;
  overflow-x: auto;
  margin: 6px 0;
}
.md :deep(pre code) { background: none; padding: 0; }

/* 代码块右上角复制按钮（按钮由 md.ts 插在 .code-wrap 内，点击走列表事件委托） */
.md :deep(.code-wrap) { position: relative; }
.md :deep(.code-wrap pre) { padding-right: 60px; }
.md :deep(.code-copy) {
  position: absolute;
  top: 6px;
  right: 6px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 22px;
  padding: 0 7px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.9);
  color: var(--muted);
  font-family: var(--font);
  font-size: 10.5px;
  line-height: 1;
  cursor: pointer;
  opacity: 0.85;
  transition: color 0.15s, border-color 0.15s, background 0.15s, opacity 0.15s;
}
.md :deep(.code-wrap:hover .code-copy) { opacity: 1; }
.md :deep(.code-copy:hover) {
  color: var(--blue-ink);
  border-color: var(--blue-line);
  background: #fff;
}
.md :deep(.code-copy.done) {
  color: var(--green-ink);
  border-color: var(--green-line);
  background: var(--green-soft);
  opacity: 1;
}
.md :deep(.code-copy svg) { width: 11px; height: 11px; flex: none; }
.md :deep(table) { border-collapse: collapse; margin: 6px 0; font-size: 11.5px; width: 100%; }
.md :deep(th), .md :deep(td) { border: 1px solid var(--line); padding: 3px 6px; text-align: left; }
.md :deep(th) { background: #f4f3ee; font-weight: 600; }
.md :deep(blockquote) {
  border-left: 3px solid var(--blue-line);
  margin: 6px 0;
  padding: 2px 8px;
  color: var(--ink-2);
  background: var(--blue-soft);
  border-radius: 0 6px 6px 0;
}
.md :deep(hr) { border: none; border-top: 1px solid var(--line-soft); margin: 8px 0; }
.md :deep(a) { color: var(--blue-ink); }
.md :deep(.katex) { font-size: 1.02em; }
.md :deep(.katex-display) { margin: 8px 0; padding: 2px 0; overflow-x: auto; overflow-y: hidden; }

/* 引用：每个文档只出一个标签，仅显示文档名；章节细节放 tooltip */
.citations { margin-top: 8px; border-top: 1px dashed var(--line); padding-top: 7px; }
.cit-title { font-size: 10.5px; color: var(--muted); margin-bottom: 5px; }
.cit-list { display: flex; flex-wrap: wrap; gap: 4px 5px; }
.cit {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  padding: 2px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 999px;
  background: #fff;
  font-size: 11px;
  color: var(--ink-2);
  transition: border-color 0.14s, background 0.14s, color 0.14s;
}
.cit.link { cursor: pointer; }
.cit.link:hover { border-color: var(--blue-line); background: var(--blue-soft); color: var(--blue-ink); }
.cit-name { max-width: 210px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cit-stale { font-size: 10px; color: var(--amber-ink); }
.err { color: var(--red); font-size: 12px; }

/* ---- 输入区 ---- */
.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding: 10px 14px 12px;
  border-top: 1px solid var(--line-soft);
  background: rgba(255, 255, 255, 0.48);
  backdrop-filter: var(--glass-blur);
  flex: none;
}
.ask-input {
  flex: 1;
  min-width: 0;
  resize: none;
  font-size: 12.5px;
  font-family: inherit;
  line-height: 1.5;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 7px 9px;
  background: #fff;
  color: var(--ink);
  transition: border-color 0.14s, box-shadow 0.14s;
}
.ask-input:focus {
  outline: none;
  border-color: var(--blue-line);
  box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.12);
}
.ask-input:disabled { background: #f8f8f4; color: var(--muted); }
.send-btn { height: 34px; flex: none; }
</style>
