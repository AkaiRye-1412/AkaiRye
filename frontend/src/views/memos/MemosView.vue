<script setup lang="ts">
/**
 * 随手笔记（「1」顶栏入口的对应页面）：独立于售后笔记。
 * 简单格式（标题 + 正文），存放在电脑任意绝对路径，通过 note_type='memo' 区分。
 *
 * 版式与售后笔记一致：左侧列表常驻，点行在右栏展开阅读（只读渲染为默认），
 * 需要改内容时切到「编辑」写 Markdown，改完自动保存（1500ms 防抖）。
 * 「笔记信息」里的标题、标签可编辑，写回文件 frontmatter（其余字段如 author/cover
 * 原样保留）；正文只改 frontmatter 之后的 Markdown。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { eventsSocket, memosApi, notesApi } from '@/api'
import { ApiError } from '@/api/http'
import { useUiStore } from '@/stores/ui'
import { renderMarkdown } from '@/rag/md'
import { copyText } from '@/utils/clipboard'
import Icon from '@/components/Icon.vue'
import MemoCreateModal from '@/components/MemoCreateModal.vue'

interface MemoRow {
  path: string
  note_title: string | null
  summary: string | null
  updated_date: string | null
  file_mtime: number
}

interface MemoDetail {
  path: string
  content: string
  fields: Record<string, string>
  content_hash: string
  note_title: string
  summary: string
  tags: string[]
}

const route = useRoute()
const router = useRouter()
const ui = useUiStore()

/* ---------------- 列表 ---------------- */
const memos = ref<MemoRow[]>([])
const loading = ref(false)
const keyword = ref('')
const showCreate = ref(false)

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return memos.value
  return memos.value.filter((m) =>
    [m.note_title, m.summary, m.path].some((x) => (x || '').toLowerCase().includes(kw)),
  )
})

function rowTitle(m: MemoRow | null): string {
  if (!m) return ''
  return m.note_title || m.path.split('/').pop()?.replace(/\.md$/, '') || '未命名'
}

function relDays(date?: string | null): string {
  if (!date) return ''
  const then = new Date(`${date.slice(0, 10)}T00:00:00`).getTime()
  if (Number.isNaN(then)) return ''
  const d = Math.max(0, Math.floor((Date.now() - then) / 86_400_000))
  if (d === 0) return '今天'
  if (d === 1) return '昨天'
  if (d < 7) return `${d} 天前`
  if (d < 30) return `${Math.floor(d / 7)} 周前`
  return `${Math.floor(d / 30)} 个月前`
}

/** 文件夹（分组键）：笔记路径去掉最后一段文件名。 */
function folderKey(m: MemoRow): string {
  const i = m.path.lastIndexOf('/')
  return i > 0 ? m.path.slice(0, i) : ''
}

/** 分组标题：列表窄，只留末尾两级目录（有辨识度的一段），前面用 … 表示省略；
 *  完整目录在 title 里。 */
function folderLabel(dir: string): string {
  const parts = dir.split('/').filter(Boolean)
  if (parts.length <= 2) return dir || '（根目录）'
  return `…/${parts.slice(-2).join('/')}`
}

/** 按文件夹分组：组内按最近更新倒序；组之间按「该组最新一篇」倒序——
 *  随手笔记的主要用法是「接着上次的看」，这样刚改过的目录永远在最上面，
 *  同一时间点再按目录名排，顺序稳定。
 *  为什么要分组：笔记散落在各个目录，平铺时只能靠每行的灰色目录行辨认，
 *  列表一长就分不清边界；分组后目录名提到组头，行内只剩标题，反而更清爽。 */
const grouped = computed(() => {
  const map = new Map<string, MemoRow[]>()
  for (const m of filtered.value) {
    const dir = folderKey(m)
    if (!map.has(dir)) map.set(dir, [])
    map.get(dir)!.push(m)
  }
  return [...map.entries()]
    .map(([dir, items]) => {
      const sorted = [...items].sort((a, b) => (b.updated_date || '').localeCompare(a.updated_date || ''))
      return { dir, label: folderLabel(dir), items: sorted, latest: sorted[0]?.updated_date || '' }
    })
    .sort(
      (a, b) =>
        (b.latest || '').localeCompare(a.latest || '') || a.dir.localeCompare(b.dir, 'zh-Hans-CN'),
    )
})

/** 文件夹折叠：只记「收起的目录」，默认全展开；搜索时清空（否则结果会被旧收起状态挡住）。 */
const collapsedFolders = ref<Set<string>>(new Set())

function isFolderCollapsed(dir: string): boolean {
  return collapsedFolders.value.has(dir)
}

function toggleFolder(dir: string) {
  const next = new Set(collapsedFolders.value)
  if (next.has(dir)) next.delete(dir)
  else next.add(dir)
  collapsedFolders.value = next
}

watch(keyword, () => {
  if (collapsedFolders.value.size) collapsedFolders.value = new Set()
})

/** 列表刷新：自动保存后也会调用，故只在首次加载时显示「加载中」，避免列表闪一下。 */
async function refreshList(showLoading = false) {
  if (showLoading) loading.value = true
  try {
    memos.value = (await memosApi.list()) as MemoRow[]
  } catch {
    memos.value = []
  } finally {
    if (showLoading) loading.value = false
  }
}

/* ---------------- 右栏：阅读 / 编辑 ---------------- */
const selectedPath = ref('') // 空 = 纯列表
const detail = ref<MemoDetail | null>(null)
/** 原文的 frontmatter 前缀：编辑器里不展示，保存时原样拼回 */
const frontPrefix = ref('')
/** 右栏编辑区的内容（正文，不含 frontmatter） */
const body = ref('')
const baseHash = ref<string | null>(null)
const dirty = ref(false)
const saving = ref(false)
const deleting = ref(false)
const conflict = ref(false)
const saveState = ref<'saved' | 'dirty' | 'saving' | 'conflict'>('saved')
const savedAt = ref('')
const loadingNote = ref(false)
const editorEl = ref<HTMLTextAreaElement | null>(null)

/** 「笔记信息」里可编辑的两项（写回 frontmatter） */
const titleDraft = ref('')
const metaTags = ref<string[]>([])
const tagInput = ref('')
/** 打开时的标题：用于判断正文首个 H1 是否就是「标题」，需要跟着改 */
const baseTitle = ref('')

const RIGHT_MODE_KEY = 'akairy.memos.rightMode'
const rightMode = ref<'preview' | 'edit'>(
  localStorage.getItem(RIGHT_MODE_KEY) === 'edit' ? 'edit' : 'preview',
)
function setRightMode(m: 'preview' | 'edit') {
  rightMode.value = m
  try {
    localStorage.setItem(RIGHT_MODE_KEY, m)
  } catch {
    /* 隐私模式下写入失败，忽略即可 */
  }
}

const selected = computed(() => memos.value.find((m) => m.path === selectedPath.value) || null)
const previewHtml = computed(() => renderMarkdown(body.value))
const wordCount = computed(() => {
  const chars = body.value.replace(/\s/g, '').length
  const lines = body.value.split('\n').length
  return `${chars} 字 · ${lines} 行`
})
const saveText = computed(() => {
  if (saveState.value === 'saving') return '保存中…'
  if (saveState.value === 'dirty') return '未保存'
  if (saveState.value === 'conflict') return '有冲突待处理'
  return savedAt.value ? `已保存 ${savedAt.value}` : '已保存'
})
/** 保存时写回文件的完整内容 = 原 frontmatter + 编辑后的正文 */
const contentText = computed(() => `${frontPrefix.value}${body.value}`)
const folderOfSelected = computed(() => {
  const i = selectedPath.value.lastIndexOf('/')
  return i > 0 ? selectedPath.value.slice(0, i) : selectedPath.value
})

function stampSaved() {
  const d = new Date()
  savedAt.value = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

const FRONTMATTER_RE = /^---\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?/

/* ---------------- frontmatter 读写（标题 / 标签） ---------------- */
/** 需要加引号的 YAML 标量（含 : # 等特殊字符时），后端解析会去掉引号 */
function yamlScalar(v: string): string {
  return /[:#\[\]{}"'\n]|^\s|\s$/.test(v) ? `"${v.replace(/"/g, '\\"')}"` : v
}

/** 在 frontmatter 里替换（或插入）一个 key 的行；block 为 null 表示删除该 key。 */
function setFmBlock(lines: string[], keys: string[], block: string[] | null): string[] {
  const out: string[] = []
  let placed = false
  for (let i = 0; i < lines.length; i++) {
    const key = lines[i].match(/^([A-Za-z_][\w-]*)\s*:/)?.[1]?.toLowerCase()
    if (key && keys.includes(key)) {
      // 块列表的续行（缩进项）一并吃掉
      while (i + 1 < lines.length && /^[ \t]+\S/.test(lines[i + 1])) i++
      if (!placed && block) out.push(...block)
      placed = true
      continue
    }
    out.push(lines[i])
  }
  if (!placed && block) out.push(...block)
  return out
}

/** 把标题 / 标签写回前缀，其余字段与换行风格保持不变。 */
function withMeta(prefix: string, title: string, tags: string[]): string {
  const eol = prefix.includes('\r\n') ? '\r\n' : '\n'
  const titleBlock = title ? [`title: ${yamlScalar(title)}`] : null
  const tagsBlock = tags.length ? ['tags:', ...tags.map((t) => `  - ${yamlScalar(t)}`)] : null
  if (!titleBlock && !tagsBlock && !prefix) return prefix

  const seg = /^---[ \t]*\r?\n([\s\S]*?)^---[ \t]*(?:\r?\n|$)/m.exec(prefix)
  if (!seg) {
    // 原本没有 frontmatter：按需新建一块，后面空一行再接正文
    const block = [...(titleBlock ?? []), ...(tagsBlock ?? [])]
    if (!block.length) return prefix
    return ['---', ...block, '---'].join(eol) + eol + eol
  }
  const lines = seg[1].split(/\r?\n/)
  if (lines.length && lines[lines.length - 1] === '') lines.pop()
  const merged = setFmBlock(setFmBlock(lines, ['title'], titleBlock), ['tags', 'tag'], tagsBlock)
  const head = merged.length ? eol + merged.join(eol) : ''
  return `---${head}${eol}---${eol}${prefix.slice(seg[0].length)}`
}

/** 正文首个非空行若是 H1，返回标题文字及其在正文中的位置（标题编辑要联动它）。 */
function leadingH1(text: string): { start: number; end: number; text: string } | null {
  let offset = 0
  for (const m of text.matchAll(/([^\r\n]*)(\r?\n|$)/g)) {
    const line = m[1]
    if (!line.trim()) {
      offset += m[0].length
      continue
    }
    const h = /^#(?!#)[ \t]+(.+?)[ \t]*$/.exec(line)
    if (!h) return null
    const start = offset + line.indexOf(h[1])
    return { start, end: start + h[1].length, text: h[1] }
  }
  return null
}

function parseTagText(raw: string): string[] {
  return raw
    .split(/[,，]/)
    .map((t) => t.trim().replace(/^#+/, ''))
    .filter(Boolean)
}

/** 标题 / 标签改动：同步前缀（必要时也把正文 H1 改掉），再走自动保存。 */
function onMetaEdit() {
  const title = titleDraft.value.trim()
  const h1 = leadingH1(body.value)
  // 列表标题优先取正文 H1：H1 就是当前标题时跟着改，否则只改 frontmatter
  if (h1 && title && h1.text === baseTitle.value && h1.text !== title) {
    body.value = body.value.slice(0, h1.start) + title + body.value.slice(h1.end)
    baseTitle.value = title
  }
  frontPrefix.value = withMeta(frontPrefix.value, title, metaTags.value)
  markDirty()
}

function commitTag() {
  const add = parseTagText(tagInput.value)
  tagInput.value = ''
  if (!add.length) return
  const next = [...metaTags.value]
  for (const t of add) if (!next.includes(t)) next.push(t)
  if (next.length === metaTags.value.length) return
  metaTags.value = next
  onMetaEdit()
}

function removeTag(i: number) {
  if (i < 0 || i >= metaTags.value.length) return
  metaTags.value.splice(i, 1)
  onMetaEdit()
}

function onTagKey(e: KeyboardEvent) {
  if (e.key === 'Enter' || e.key === ',' || e.key === '，') {
    e.preventDefault()
    commitTag()
  } else if (e.key === 'Backspace' && !tagInput.value && metaTags.value.length) {
    e.preventDefault()
    removeTag(metaTags.value.length - 1)
  }
}

function applyDetail(info: MemoDetail) {
  detail.value = info
  const m = info.content.match(FRONTMATTER_RE)
  let prefix = m ? m[0] : ''
  let rest = m ? info.content.slice(m[0].length) : info.content
  // frontmatter 后的空行归到前缀：编辑区从第一行有效正文开始，保存时拼回即为原文
  const lead = rest.match(/^(?:\r?\n)+/)
  if (lead) {
    prefix += lead[0]
    rest = rest.slice(lead[0].length)
  }
  frontPrefix.value = prefix
  body.value = rest
  // 随手笔记以 frontmatter 的 title 作为标题（列表同口径），没有才回退列表标题
  titleDraft.value = (info.fields?.title || info.note_title || '').trim()
  baseTitle.value = titleDraft.value
  metaTags.value = [...(info.tags || [])]
  tagInput.value = ''
  baseHash.value = info.content_hash
  dirty.value = false
  conflict.value = false
  saveState.value = 'saved'
  stampSaved()
}

async function openMemo(path: string) {
  if (path === selectedPath.value) return
  if (dirty.value && !window.confirm('当前随手记有未保存修改，切换将丢弃，确定？')) return
  loadingNote.value = true
  try {
    const info = (await notesApi.get(path)) as MemoDetail
    selectedPath.value = path
    applyDetail(info)
    if (route.query.path !== path) router.replace({ path: '/memos', query: { path } })
    await nextTick()
    if (rightMode.value === 'edit') editorEl.value?.focus()
  } catch (e) {
    ui.toast('error', `打开随手记失败：${(e as Error).message}`)
  } finally {
    loadingNote.value = false
  }
}

function closeMemo(confirmDirty = true) {
  if (confirmDirty && dirty.value && !window.confirm('当前随手记有未保存修改，返回列表将丢弃，确定？')) return
  dirty.value = false
  conflict.value = false
  selectedPath.value = ''
  detail.value = null
  frontPrefix.value = ''
  body.value = ''
  baseHash.value = null
  if (route.query.path) router.replace({ path: '/memos' })
}

async function doSave(silent = false): Promise<boolean> {
  if (!selectedPath.value || saving.value) return false
  if (conflict.value) {
    if (!silent) ui.toast('warn', '存在文件冲突，请先选择「加载文件内容」或「用我的覆盖」')
    return false
  }
  if (!dirty.value) {
    if (!silent) ui.toast('info', '没有需要保存的修改')
    return true
  }
  saving.value = true
  saveState.value = 'saving'
  try {
    const resp = await notesApi.save(selectedPath.value, {
      content: contentText.value,
      base_hash: baseHash.value,
    })
    baseHash.value = resp.content_hash
    dirty.value = false
    saveState.value = 'saved'
    stampSaved()
    if (!silent) ui.toast('success', '已写入文件')
    await refreshList()
    return true
  } catch (e) {
    const err = e as ApiError
    if (err.code === 'edit_conflict') {
      // 文件在工作台外（Obsidian / 编辑器）被改过：停止自动保存，交给用户选边
      conflict.value = true
      saveState.value = 'conflict'
      ui.toast('warn', '文件已在工作台外被修改，自动保存已暂停')
      return false
    }
    saveState.value = dirty.value ? 'dirty' : 'saved'
    ui.toast('error', `保存失败：${err.message}`)
    return false
  } finally {
    saving.value = false
  }
}

function scheduleSaveNow() {
  if (saveTimer) window.clearTimeout(saveTimer)
  void doSave(false)
}

/* ---------------- 自动保存 ---------------- */
let saveTimer: number | null = null

/** 正文 / 标题 / 标签任何一处改动都走这里：标脏 + 1.5s 防抖落盘 */
function markDirty() {
  if (!selectedPath.value) return
  dirty.value = true
  saveState.value = 'dirty'
  if (saveTimer) window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => void doSave(true), 1500)
}

function onEdit() {
  markDirty()
}

/* ---------------- 冲突：加载文件最新内容 / 用我的覆盖 ---------------- */
async function reloadFromServer() {
  if (!selectedPath.value) return
  try {
    const info = (await notesApi.get(selectedPath.value)) as MemoDetail
    applyDetail(info)
    await refreshList()
    ui.toast('success', '已加载文件最新内容')
  } catch (e) {
    ui.toast('error', `读取失败：${(e as Error).message}`)
  }
}

async function overwriteWithMine() {
  if (!selectedPath.value) return
  saving.value = true
  saveState.value = 'saving'
  try {
    const resp = await notesApi.save(
      selectedPath.value,
      { content: contentText.value, base_hash: null },
      { force: true },
    )
    baseHash.value = resp.content_hash
    dirty.value = false
    conflict.value = false
    saveState.value = 'saved'
    stampSaved()
    await refreshList()
    ui.toast('success', '已用工作台内容覆盖文件')
  } catch (e) {
    ui.toast('error', `覆盖失败：${(e as Error).message}`)
    saveState.value = 'conflict'
  } finally {
    saving.value = false
  }
}

/* ---------------- 删除 / 复制路径 ---------------- */
async function removeMemo(path: string) {
  if (!path || deleting.value) return
  const row = memos.value.find((m) => m.path === path) || null
  if (!window.confirm(`确定删除「${rowTitle(row) || path}」？（文件会移入 vault 回收目录，可还原）`)) return
  deleting.value = true
  try {
    await notesApi.remove(path)
    ui.toast('success', '已删除')
    // 删的正是右栏打开的那篇 → 先关右栏，避免详情指向已不存在的文件
    if (selectedPath.value === path) closeMemo(false)
    await refreshList()
  } catch (e) {
    const err = e as ApiError
    ui.toast('error', `删除失败：${err.message}`)
  } finally {
    deleting.value = false
  }
}

async function copyPath() {
  const ok = await copyText(selectedPath.value)
  ui.toast(ok ? 'success' : 'warn', ok ? '路径已复制' : '复制失败，请手动选择路径')
}

/** 预览区代码块复制（renderMarkdown 生成的是纯按钮，靠事件委托接管）。 */
async function onPreviewClick(ev: MouseEvent) {
  const btn = (ev.target as HTMLElement).closest('.code-copy') as HTMLElement | null
  if (!btn) return
  const code = btn.closest('.code-wrap')?.querySelector('pre')?.textContent ?? ''
  const ok = await copyText(code.replace(/\n$/, ''))
  btn.classList.toggle('done', ok)
  const label = btn.querySelector('span')
  if (label) {
    const old = label.textContent
    label.textContent = ok ? '已复制' : '复制失败'
    window.setTimeout(() => {
      btn.classList.remove('done')
      label.textContent = old || '复制'
    }, 1600)
  }
}

/* ---------------- 路由 / 外部变化 ---------------- */
const unsubs: (() => void)[] = []

function syncFromRoute() {
  const q = route.query
  if (typeof q.path === 'string' && q.path) void openMemo(q.path)
}

watch(
  () => route.query.path,
  (v) => {
    const p = typeof v === 'string' ? v : ''
    if (p && p !== selectedPath.value) void openMemo(p)
    else if (!p && selectedPath.value) closeMemo(false)
  },
)

/** 文件被外部改动（Obsidian 保存 / 其他编辑器）：列表刷新，右栏按需重读。 */
async function onNoteChanged(p: { path: string; change: string }) {
  if (p.path === selectedPath.value) {
    if (p.change === 'deleted') {
      if (!dirty.value) {
        ui.toast('warn', '这篇随手记已被删除')
        closeMemo(false)
      }
    } else if (!dirty.value) {
      try {
        const h = await notesApi.history(p.path)
        if (h.content_hash !== baseHash.value) {
          const info = (await notesApi.get(p.path)) as MemoDetail
          applyDetail(info)
          ui.toast('info', '文件已被外部修改，已刷新为最新内容')
        }
      } catch {
        /* 文件正在被改写时读取可能失败，等下一次推送 */
      }
    }
  }
  await refreshList()
}

function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    if (selectedPath.value) scheduleSaveNow()
  }
}

onMounted(async () => {
  await refreshList(true)
  syncFromRoute()
  unsubs.push(
    eventsSocket.on('note.changed', (payload: unknown) => {
      void onNoteChanged(payload as { path: string; change: string })
    }),
  )
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  if (saveTimer) window.clearTimeout(saveTimer)
  unsubs.forEach((fn) => fn())
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="page page-memos" :class="{ split: !!selectedPath }">
    <!-- ==================== 左：列表（常驻，点行在右栏打开） ==================== -->
    <section class="card memos-card">
      <header class="card-head memos-head">
        <div class="card-title">
          <h2>随手笔记</h2>
          <span class="count-pill">{{ filtered.length }}</span>
        </div>
        <div class="memos-tools">
          <div class="search-field">
            <Icon name="search" :size="15" />
            <input v-model="keyword" placeholder="搜索标题或路径…" autocomplete="off" />
            <button v-if="keyword" class="search-clear" title="清空" @click="keyword = ''">
              <Icon name="x" :size="12" />
            </button>
          </div>
          <button class="btn primary sm" @click="showCreate = true">
            <Icon name="plus" :size="14" /><span>新建随手记</span>
          </button>
        </div>
      </header>

      <div class="memos-table-head">
        <span>笔记</span><span>最近更新</span><span></span>
      </div>

      <div class="memos-list">
        <div v-if="loading" class="empty-block">
          <p>加载中…</p>
        </div>
        <div v-else-if="!filtered.length" class="empty-block">
          <div class="empty-ic"><Icon :name="memos.length ? 'search' : 'notes'" :size="26" /></div>
          <p>{{ memos.length ? '没有匹配的随手记' : '还没有随手记' }}</p>
          <span class="empty-sub">点右上角「新建随手记」，写到电脑任意位置</span>
        </div>
        <template v-else>
          <template v-for="g in grouped" :key="g.dir">
            <div
              class="memos-group-label"
              :class="{ collapsed: isFolderCollapsed(g.dir) }"
              role="button"
              tabindex="0"
              :aria-expanded="!isFolderCollapsed(g.dir)"
              :title="`${isFolderCollapsed(g.dir) ? '展开' : '收起'} ${g.dir}`"
              @click="toggleFolder(g.dir)"
              @keydown.enter.prevent="toggleFolder(g.dir)"
              @keydown.space.prevent="toggleFolder(g.dir)"
            >
              <Icon
                class="mg-caret"
                :name="isFolderCollapsed(g.dir) ? 'chevronRight' : 'chevronDown'"
                :size="12"
              />
              <Icon name="folder" :size="13" />
              <span class="mg-name">{{ g.label }}</span>
              <span class="mg-count">{{ g.items.length }}</span>
            </div>
            <template v-if="!isFolderCollapsed(g.dir)">
              <div
                v-for="m in g.items"
                :key="m.path"
                class="memo-row"
                :class="{ active: m.path === selectedPath }"
                @click="openMemo(m.path)"
              >
                <div class="mr-main">
                  <span class="mr-title">
                    <Icon name="file" :size="13" />
                    <span class="mr-title-text">{{ rowTitle(m) }}</span>
                  </span>
                </div>
                <div class="mr-date">
                  <strong>{{ m.updated_date || '—' }}</strong>
                  <em>{{ relDays(m.updated_date) }}</em>
                </div>
                <div class="mr-act" @click.stop>
                  <Icon class="mr-go-ic" name="chevronRight" :size="15" />
                  <button class="mr-del" title="删除随手记（移入回收目录，可还原）" @click.stop="removeMemo(m.path)">
                    <Icon name="trash" :size="14" />
                  </button>
                </div>
              </div>
            </template>
          </template>
        </template>
      </div>

      <footer class="card-foot">
        <span class="foot-item"><Icon name="database" :size="13" /> 数据直接读写本地 .md · 不建立副本</span>
        <span class="foot-dot" />
        <span>点击任意行在右侧阅读</span>
        <span class="foot-right">{{ memos.length }} 篇</span>
      </footer>
    </section>

    <!-- ==================== 右：当前随手记 ==================== -->
    <section v-if="selectedPath" class="memo-detail">
      <div class="note-topbar">
        <div class="note-crumb">
          <button class="icon-btn" title="关闭右栏（回到纯列表）" @click="closeMemo()">
            <Icon name="x" :size="16" />
          </button>
          <span class="crumb-note">随手笔记</span>
          <Icon name="chevronRight" :size="12" />
          <span class="crumb-current" :title="selectedPath">{{ titleDraft.trim() || rowTitle(selected) || selectedPath }}</span>
        </div>
        <div class="note-actions">
          <span class="save-status" :class="saveState"><span class="dot" />{{ saveText }}</span>
          <button class="btn sm ghost" :disabled="!dirty || saving" @click="scheduleSaveNow">
            <Icon name="save" :size="14" /><span>保存</span>
          </button>
          <button class="btn sm ghost danger-ghost" :disabled="deleting" @click="removeMemo(selectedPath)">
            <Icon name="trash" :size="14" /><span>{{ deleting ? '删除中…' : '删除' }}</span>
          </button>
        </div>
      </div>

      <div v-if="loadingNote" class="note-loading">正在读取随手记…</div>

      <template v-else-if="detail">
        <div v-if="conflict" class="conflict-bar">
          <Icon name="alert" :size="14" />
          <span>文件已在工作台外被修改，自动保存已暂停</span>
          <span class="spacer" />
          <button class="btn ghost sm" @click="reloadFromServer">加载文件内容</button>
          <button class="btn ghost sm" @click="overwriteWithMine">用我的覆盖</button>
        </div>

        <!-- 头部信息：与售后笔记的「头部字段」同位同形；标题 / 标签可直接改，写回 frontmatter -->
        <section class="card note-fields">
          <div class="nf-head">
            <span class="nf-title"><Icon name="notes" :size="15" /> 笔记信息</span>
            <span class="nf-hint">标题 / 标签写入 frontmatter</span>
          </div>
          <div class="nf-grid">
            <div class="nf-field">
              <span>标题</span>
              <input
                v-model="titleDraft"
                class="nf-input"
                placeholder="未命名"
                spellcheck="false"
                title="改完自动保存；若正文首行是 H1，会一起改"
                @input="onMetaEdit"
                @keydown.enter.prevent="scheduleSaveNow"
              />
            </div>
            <div class="nf-field">
              <span>标签</span>
              <div class="nf-tags edit" :title="metaTags.join(' / ')">
                <span v-for="(t, i) in metaTags" :key="t" class="tag">
                  {{ t }}
                  <button class="tag-x" title="移除标签" @click="removeTag(i)">
                    <Icon name="x" :size="9" />
                  </button>
                </span>
                <input
                  v-model="tagInput"
                  class="tag-input"
                  placeholder="加标签…"
                  spellcheck="false"
                  @keydown="onTagKey"
                  @blur="commitTag"
                />
              </div>
            </div>
            <div class="nf-field">
              <span>最近更新</span>
              <span class="nf-readonly">{{ selected?.updated_date || '—' }}</span>
            </div>
            <div class="nf-field">
              <span>所在目录</span>
              <span class="nf-readonly mono" :title="folderOfSelected">{{ folderOfSelected }}</span>
            </div>
          </div>
        </section>

        <section class="card editor-card">
          <div class="editor-toolbar">
            <span class="editor-mode">{{ rightMode === 'edit' ? 'Markdown 原文' : '只读渲染' }}</span>
            <div class="pane-switch" title="右栏呈现方式">
              <button :class="{ active: rightMode === 'preview' }" @click="setRightMode('preview')">预览</button>
              <button :class="{ active: rightMode === 'edit' }" @click="setRightMode('edit')">编辑</button>
            </div>
            <div class="editor-meta">{{ wordCount }}</div>
          </div>
          <div class="editor-area" :class="rightMode === 'edit' ? 'mode-edit' : 'mode-preview'">
            <div
              v-if="rightMode === 'preview'"
              class="editor-preview md"
              @click="onPreviewClick"
              v-html="previewHtml"
            />
            <textarea
              v-else
              ref="editorEl"
              v-model="body"
              class="editor-input"
              spellcheck="false"
              placeholder="直接编辑这份 .md 的正文，Ctrl+S 立即保存…"
              @input="onEdit"
            />
          </div>
          <footer class="editor-foot">
            <span class="editor-path" :title="selectedPath" @click="copyPath">
              <Icon name="file" :size="12" /> {{ selectedPath }}
            </span>
            <button class="link-btn" @click="copyPath">复制路径</button>
            <span class="editor-foot-right">编辑后自动保存</span>
          </footer>
        </section>
      </template>
    </section>

    <MemoCreateModal :open="showCreate" @close="showCreate = false" />
  </div>
</template>

<style scoped>
/* ==================== 页面两栏 ==================== */
.page-memos {
  display: flex;
  flex-direction: column;
  gap: 11px;
  height: 100%;
  min-height: 0;
  container-type: inline-size;
  container-name: memopage;
}
.page-memos.split { flex-direction: row; align-items: stretch; }
/* 主区太窄（右侧助手面板展开 + 小屏）时列表让位、详情占满，用 × 回到列表 */
@container memopage (max-width: 720px) {
  .page-memos.split > .memos-card { display: none; }
}
.page-memos.split > .memos-card { width: clamp(320px, 38%, 430px); flex: none; }

.memo-detail {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 11px;
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 2px;
  /* 右栏自身作为容器：字段卡的列数按右栏实际宽度决定，而不是整页宽度 */
  container-type: inline-size;
  container-name: memodetail;
}

/* ==================== 列表 ==================== */
.memos-card { display: flex; flex-direction: column; overflow: hidden; flex: 1 1 auto; min-height: 0; }
.memos-head { align-items: center; padding-bottom: 12px; }
.memos-head .card-title { margin-right: auto; }
.memos-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.memos-tools .search-field { flex: 1 1 140px; max-width: 220px; }
.card-title h2 { font-size: 16px; margin: 0; font-weight: 650; letter-spacing: -0.2px; white-space: nowrap; }
.count-pill {
  font-size: 11px;
  color: var(--ink-2);
  background: #f2f1ec;
  border-radius: 999px;
  padding: 1px 7px;
  min-width: 20px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.page-memos.split .memos-head { flex-wrap: wrap; gap: 8px; }
.page-memos.split .memos-head .card-title { margin-right: 0; flex: none; }
.page-memos.split .memos-tools { width: 100%; gap: 6px; }
.page-memos.split .search-field { flex: 1 1 100%; min-width: 0; max-width: none; }

.memos-table-head,
.memo-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 92px 22px;
  gap: 10px;
  align-items: center;
  padding: 0 18px;
}
.page-memos.split .memos-table-head,
.page-memos.split .memo-row { gap: 10px; padding: 0 12px; grid-template-columns: minmax(0, 1fr) 88px 20px; }
.memos-table-head {
  height: 32px;
  font-size: 11.5px;
  font-weight: 650;
  color: var(--ink);
  border-top: 1px solid var(--line-soft);
  border-bottom: 1px solid rgba(226, 223, 214, 0.95);
  /* 表头是一条贯穿的窄带，底色比卡片略实一点才能从下面透出的壁纸里立起来 */
  background: rgba(255, 255, 255, 0.58);
  letter-spacing: 0.3px;
}
.memos-list { display: flex; flex-direction: column; flex: 1; overflow: auto; }

/* 文件夹分组带：与售后笔记的客户分组同一套语言（同色系底带 + 计数胶囊），
   整条可点，收起时只剩组头 */
.memos-group-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px 5px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.2px;
  color: var(--blue-ink);
  background: #f4f8fe;
  border-top: 1px solid #e4edfb;
  cursor: pointer;
  user-select: none;
  transition: background 0.12s;
}
.page-memos:not(.split) .memos-group-label { padding: 6px 18px 5px; }
.memos-group-label:hover { background: #eaf2fe; }
.memos-group-label:active { background: #e2edfd; }
.memos-group-label:focus-visible { outline: 2px solid var(--blue); outline-offset: -2px; }
/* 收起时压暗一档 + 补一条底线，视觉上像合上的抽屉 */
.memos-group-label.collapsed { background: #f0f5fd; border-bottom: 1px solid #e4edfb; }
.memos-group-label svg { color: var(--blue); opacity: 0.62; }
.mg-name { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mg-count {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 15px;
  padding: 0 5px;
  border-radius: 999px;
  background: #fff;
  border: 1px solid var(--blue-line);
  color: var(--blue-ink);
  font-size: 9.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}
/* 行高从 9px 加到 11px：标题 + 路径两行内容本来就要呼吸感，
   30 篇挤在一屏时 9px 的上下留白会让行与行黏在一起 */
.memo-row { padding: 9px 18px 10px; border-top: 1px solid #f4f3ee; cursor: pointer; transition: background 0.1s; }
.page-memos.split .memo-row { padding: 8px 12px 9px; }
.memo-row:first-child { border-top: 0; }
.memo-row:hover { background: #f8f9fb; }
.memo-row.active { background: #eef4fb; box-shadow: inset 2px 0 0 var(--blue); }
.memo-row.active:hover { background: #e9f1fa; }

.mr-main { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.mr-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 620;
  color: var(--ink);
  line-height: 1.45;
  min-width: 0;
}
.mr-title :deep(svg) { flex: none; color: #b0aca2; }
.mr-title-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 路径缩进到标题文字的左边缘（图标 13 + 间距 7），两行成为一个整体而不是各说各话；
   字号 10 → 10.5、颜色退到 muted，长路径靠省略号收尾，不再顶到日期列 */
.mr-date { font-size: 11px; color: var(--ink); font-variant-numeric: tabular-nums; line-height: 1.35; }
.mr-date strong { font-weight: 620; display: block; letter-spacing: -0.1px; }
.mr-date em { display: block; font-style: normal; font-size: 10px; color: var(--muted); margin-top: 4px; }

.mr-act { position: relative; width: 22px; height: 22px; display: grid; place-items: center; }
.mr-go-ic { color: #b7b3a9; }
/* 悬停时箭头让位给删除按钮（占同一格，不改变行高/列宽） */
.mr-del {
  position: absolute;
  inset: 0;
  display: none;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #8b8880;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}
.memo-row:hover .mr-go-ic { display: none; }
.memo-row:hover .mr-del { display: grid; }
.mr-del:hover { background: var(--red-soft); color: var(--red); }

.foot-item { display: inline-flex; align-items: center; gap: 5px; }
.foot-right { margin-left: auto; font-family: var(--mono); font-size: 10px; color: #a6a29a; }

/* ==================== 右栏顶栏 ==================== */
.note-topbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.note-crumb { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); min-width: 0; }
.crumb-note { white-space: nowrap; }
.crumb-current {
  color: var(--ink-2);
  font-weight: 550;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 320px;
}
.note-actions { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; justify-content: flex-end; }
.save-status { font-size: 11px; color: var(--muted); display: inline-flex; align-items: center; gap: 5px; }
.save-status .dot { width: 6px; height: 6px; background: var(--green); }
.save-status.dirty .dot { background: var(--amber); }
.save-status.saving .dot { background: var(--blue); animation: pulseDot 1s infinite; }
.save-status.conflict .dot { background: var(--red); }
@keyframes pulseDot { 50% { opacity: 0.35; } }
.note-loading { padding: 40px; text-align: center; color: var(--muted); font-size: 13px; }

.conflict-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 12px;
  border-radius: 9px;
  background: var(--amber-soft);
  border: 1px solid var(--amber-line);
  color: var(--amber-ink);
  font-size: 11.5px;
}
.conflict-bar .spacer { flex: 1; }
.conflict-bar .btn { color: var(--amber-ink); border-color: var(--amber-line); }

/* ==================== 头部信息（同售后笔记的字段卡） ==================== */
.note-fields { padding: 13px 17px 14px; flex: none; }
.nf-head { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; }
.nf-title { font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 6px; flex: none; white-space: nowrap; }
.nf-title :deep(svg) { color: var(--muted); }
.nf-hint { font-size: 10.5px; color: var(--muted); margin-left: auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nf-grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1.5fr) minmax(0, 0.85fr) minmax(0, 1.05fr); gap: 9px; }
/* 右栏偏窄时回到两列，标题/标签不至于被挤成几像素 */
@container memodetail (max-width: 620px) {
  .nf-grid { grid-template-columns: 1fr 1fr; }
}
.nf-field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.nf-field > span { font-size: 10.5px; color: var(--muted); }
/* 长文本（标题 / 目录）用 block + 行高实现省略号：flex 容器里的文字裁不出「…」 */
.nf-readonly {
  height: 34px;
  display: block;
  padding: 0 10px;
  line-height: 32px;
  background: #f6f5f0;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  font-size: 12px;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.nf-readonly.mono { font-family: var(--mono); font-size: 10.5px; }

/* 可编辑的标题框：白底 + 聚焦蓝环，和只读框同高同圆角 */
.nf-input {
  width: 100%;
  height: 34px;
  padding: 0 10px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 8px;
  font-size: 12px;
  font-weight: 550;
  color: var(--ink);
  outline: 0;
  transition: border-color 0.12s, box-shadow 0.12s;
}
.nf-input:hover { border-color: #cfcbc0; }
.nf-input:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(47, 111, 179, 0.12); }
.nf-input::placeholder { color: #b3afa6; font-weight: 400; }

.nf-tags {
  height: 34px;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 8px;
  background: #f6f5f0;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  overflow: hidden;
}
.nf-tags.edit { background: #fff; border-color: var(--line); padding: 0 6px 0 7px; overflow-x: auto; overflow-y: hidden; scrollbar-width: none; }
.nf-tags.edit::-webkit-scrollbar { display: none; }
.nf-tags.edit:focus-within { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(47, 111, 179, 0.12); }
.tag-input {
  flex: 1 1 46px;
  min-width: 46px;
  height: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  font-size: 10.5px;
  color: var(--ink);
  padding: 0 2px;
}
.tag-input::placeholder { color: #b3afa6; }
.nf-empty { font-size: 10.5px; color: var(--muted); }
.tag {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 5px;
  background: #fff;
  border: 1px solid var(--line-soft);
  color: var(--ink-2);
  font-size: 10px;
  white-space: nowrap;
  flex: none;
}
.tag-x {
  display: inline-grid;
  place-items: center;
  width: 12px;
  height: 12px;
  margin-left: 2px;
  padding: 0;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: #a6a29a;
  cursor: pointer;
}
.tag-x:hover { background: var(--red-soft); color: var(--red); }

/* ==================== 阅读 / 编辑卡 ==================== */
.editor-card { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 300px; overflow: hidden; }
.editor-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 13px;
  border-bottom: 1px solid var(--line-soft);
  background: #fcfcfa;
  flex-wrap: wrap;
  flex: none;
}
.editor-mode { font-size: 10.5px; color: var(--muted); letter-spacing: 0.2px; }
.editor-meta { margin-left: auto; font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.pane-switch {
  flex: none;
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: #fff;
  overflow: hidden;
}
.pane-switch button {
  border: 0;
  background: none;
  padding: 0 9px;
  height: 20px;
  line-height: 20px;
  font-size: 10.5px;
  color: var(--muted);
  cursor: pointer;
}
.pane-switch button + button { border-left: 1px solid var(--line-soft); }
.pane-switch button.active { background: #eef4fb; color: #2f6fb3; }
.pane-switch button:not(.active):hover { background: #f6f5f1; color: #3a3f47; }

.editor-area { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; background: #fff; }
.editor-input {
  flex: 1;
  width: 100%;
  border: 0;
  outline: 0;
  resize: none;
  padding: 14px 18px 28px;
  font-family: var(--mono);
  font-size: 12.6px;
  line-height: 1.9;
  color: #24262b;
  background: #fffefb;
  tab-size: 2;
  min-height: 0;
}
.editor-input::selection { background: #d8e9fb; }
.editor-preview { flex: 1; min-height: 0; overflow: auto; padding: 16px 22px 30px; font-size: 13px; }
.editor-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 13px;
  border-top: 1px solid var(--line-soft);
  background: #fbfaf7;
  font-size: 10.5px;
  color: var(--muted);
  flex-wrap: wrap;
  flex: none;
}
.editor-path {
  display: flex;
  align-items: center;
  gap: 5px;
  font-family: var(--mono);
  font-size: 10px;
  cursor: pointer;
  max-width: 55%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.editor-path:hover { color: var(--blue-ink); }
.editor-foot-right { margin-left: auto; }

/* 预览区 markdown 排版：与售后笔记预览同一口径 */
.md :deep(h1) { font-size: 19px; margin: 4px 0 10px; }
.md :deep(h2) { font-size: 15.5px; margin: 18px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--line-soft); }
.md :deep(h3) { font-size: 13.5px; margin: 14px 0 6px; }
.md :deep(p) { margin: 6px 0; line-height: 1.8; color: var(--ink-2); }
.md :deep(ul),
.md :deep(ol) { margin: 6px 0; padding-left: 20px; line-height: 1.85; color: var(--ink-2); }
.md :deep(li) { margin: 2px 0; }
.md :deep(strong) { color: var(--ink); font-weight: 600; }
.md :deep(a) { color: var(--blue-ink); }
.md :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--blue-line);
  background: #f8fafd;
  color: var(--ink-2);
  font-size: 12.5px;
}
.md :deep(code) {
  font-family: var(--mono);
  font-size: 11.5px;
  background: #f4f3ee;
  border-radius: 4px;
  padding: 1px 4px;
}
.md :deep(table) { border-collapse: collapse; margin: 8px 0; font-size: 12px; width: 100%; }
.md :deep(th),
.md :deep(td) { border: 1px solid var(--line-soft); padding: 5px 8px; text-align: left; }
.md :deep(th) { background: #faf9f5; font-weight: 600; }
.md :deep(img) { max-width: 100%; border-radius: 8px; }
.md :deep(.katex) { font-size: 1.04em; }
.md :deep(.katex-display) { margin: 10px 0; padding: 2px 0; overflow-x: auto; overflow-y: hidden; }
.md :deep(hr) { border: 0; border-top: 1px solid var(--line-soft); margin: 14px 0; }
.md :deep(.code-wrap) { position: relative; margin: 8px 0; }
.md :deep(pre) {
  background: #f7f6f2;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 11.5px;
  line-height: 1.75;
}
.md :deep(pre code) { background: none; padding: 0; }
.md :deep(.code-copy) {
  position: absolute;
  top: 6px;
  right: 6px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 20px;
  padding: 0 6px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: #fff;
  color: var(--muted);
  font-size: 10px;
  opacity: 0;
  transition: opacity 0.12s, color 0.12s, border-color 0.12s;
}
.md :deep(.code-wrap:hover .code-copy) { opacity: 1; }
.md :deep(.code-copy svg) { width: 11px; height: 11px; }
.md :deep(.code-copy.done) { color: var(--green-ink); border-color: var(--green-line); background: var(--green-soft); }

.danger-ghost { color: var(--red); border-color: var(--line); }
.danger-ghost:hover { background: var(--red-soft); border-color: var(--red-line); color: var(--red); }
</style>
