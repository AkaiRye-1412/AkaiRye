<script setup lang="ts">
/**
 * M2 售后笔记（FR-NOTE-01~11 / FR-PROJ-02）：
 *
 * 列表（默认）：卡片表格 + 客户分组 + 全部/进行中/已归档 + 关键词搜索（标题/客户/标签/摘要）
 * 编辑器（?path=）：头部字段卡 + 编辑/分栏/预览 + 自动保存 + 冲突三选 + 归档 + 索引状态
 *
 * 口径（v2，兼容真实 vault）：
 * - 项目笔记：frontmatter 有 project / start_date（name/project/status/start_date/updated）；
 * - 售后卡片：只有 name/time/tags/description（按日卡片），缺 status 视为「进行中」，
 *   标题取正文首个 H1、摘要取 description、标签取 tags、日期取文件名或 time 前缀。
 * 数据始终直接读写 vault 同一份 .md，不建副本（FR-NOTE-01）。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { eventsSocket, notesApi } from '@/api'
import { ApiError } from '@/api/http'
import { useConfigStore } from '@/stores/config'
import { useRagStore } from '@/stores/rag'
import { useShortcutsStore } from '@/stores/shortcuts'
import { useUiStore } from '@/stores/ui'
import { renderMarkdown } from '@/rag/md'
import { copyText } from '@/utils/clipboard'
import { matchesCombo } from '@/utils/shortcuts'
import Icon from '@/components/Icon.vue'

interface NoteRow {
  path: string
  customer: string | null
  project_name: string | null
  status: 'active' | 'archived'
  start_date: string | null
  updated_date: string | null
  head_ok: number
  indexed_status: 'ready' | 'pending' | 'failed' | 'excluded' | null
  note_title: string | null
  summary: string | null
  tags: string | null
  note_mode: 'project' | 'card' | null
}

interface NoteDetail {
  path: string
  content: string
  fields: Record<string, string>
  head_ok: boolean
  missing_fields: string[]
  content_hash: string
  note_title: string
  summary: string
  tags: string[]
  note_mode: 'project' | 'card'
}

const ui = useUiStore()
const rag = useRagStore()
const config = useConfigStore()
const shortcuts = useShortcutsStore()
const route = useRoute()
const router = useRouter()

const notesFolder = computed(() => (config.config.paths?.notes_folder as string) || '客户售后问题')
const followUpDays = computed(() => Number(config.config.dashboard?.follow_up_days ?? 7))

/* ---------------- 列表 ---------------- */
const notes = ref<NoteRow[]>([])
const keyword = ref('')
const filter = ref<'all' | 'active' | 'archived'>('all')
const listError = ref('')

const filters = [
  { key: 'all' as const, label: '全部' },
  { key: 'active' as const, label: '进行中' },
  { key: 'archived' as const, label: '已归档' },
]

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return notes.value.filter((n) => {
    if (filter.value !== 'all' && n.status !== filter.value) return false
    if (!kw) return true
    const hay = [n.note_title, n.project_name, n.customer, n.tags, n.summary, n.path]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(kw)
  })
})

/** 客户分组：组内按最近更新倒序（FR-NOTE-07 / FR-PROJ-02）。 */
const grouped = computed(() => {
  const map = new Map<string, NoteRow[]>()
  for (const n of filtered.value) {
    const key = n.customer || '（未识别客户）'
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(n)
  }
  for (const items of map.values()) {
    items.sort((a, b) => (b.updated_date || '').localeCompare(a.updated_date || ''))
  }
  return [...map.entries()]
    .map(([customer, items]) => ({ customer, items }))
    .sort((a, b) => a.customer.localeCompare(b.customer, 'zh-Hans-CN'))
})

/* 客户分组折叠：点分组带收/展该客户下的笔记。
   只记「收起的客户名」——新客户默认展开；搜索或切换筛选时自动全部展开，
   避免搜出来的结果被一个此前收起的组头挡住。 */
const collapsedGroups = ref<Set<string>>(new Set())

function isGroupCollapsed(name: string): boolean {
  return collapsedGroups.value.has(name)
}

function toggleGroup(name: string) {
  const next = new Set(collapsedGroups.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  collapsedGroups.value = next
}

/** 是否「已全部收起」：表头那枚图标据此切换箭头方向与文案；空列表不算全收。 */
const allGroupsCollapsed = computed(
  () => grouped.value.length > 0 && grouped.value.every((g) => collapsedGroups.value.has(g.customer)),
)

/** 一键折叠：全展开 → 全收起；已全收起 → 全展开。 */
function toggleAllGroups() {
  if (allGroupsCollapsed.value) {
    collapsedGroups.value = new Set()
    return
  }
  collapsedGroups.value = new Set(grouped.value.map((g) => g.customer))
}

watch([keyword, filter], () => {
  if (collapsedGroups.value.size) collapsedGroups.value = new Set()
})

const statusCount = computed(() => ({
  all: notes.value.length,
  active: notes.value.filter((n) => n.status === 'active').length,
  archived: notes.value.filter((n) => n.status === 'archived').length,
}))

function todayStr(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function daysSince(date?: string | null): number {
  if (!date) return 0
  const then = new Date(`${date.slice(0, 10)}T00:00:00`).getTime()
  if (Number.isNaN(then)) return 0
  return Math.max(0, Math.floor((Date.now() - then) / 86_400_000))
}

function relDays(date?: string | null): string {
  if (!date) return ''
  const d = daysSince(date)
  if (d === 0) return '今天'
  if (d === 1) return '昨天'
  if (d < 7) return `${d} 天前`
  if (d < 30) return `${Math.floor(d / 7)} 周前`
  return `${Math.floor(d / 30)} 个月前`
}

function rowBadge(n: NoteRow): { cls: string; text: string } {
  if (n.status === 'archived') return { cls: 'muted', text: '已归档' }
  if (daysSince(n.updated_date) > followUpDays.value) return { cls: 'warn', text: '待跟进' }
  return { cls: 'ok-soft', text: '进行中' }
}

function rowTags(n: NoteRow): string[] {
  return (n.tags || '')
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean)
}

function rowTitle(n: NoteRow | null): string {
  if (!n) return ''
  return n.note_title || n.project_name || n.path
}

async function refreshList() {
  try {
    notes.value = (await notesApi.list()) as NoteRow[]
    listError.value = ''
  } catch (e) {
    notes.value = []
    listError.value = (e as Error).message
  }
}

/* ---------------- 编辑器 ---------------- */
const selectedPath = ref('') // 空 = 列表模式
const detail = ref<NoteDetail | null>(null)
const content = ref('')
const baseHash = ref<string | null>(null)
const dirty = ref(false)
const saving = ref(false)
const saveState = ref<'saved' | 'dirty' | 'saving' | 'conflict'>('saved')
const savedAt = ref('')
const loadingNote = ref(false)
const editorEl = ref<HTMLTextAreaElement | null>(null)

/* 右栏内容区：预览（默认，只读渲染）/ 编辑（Markdown 原文）—— 记住上次选择 */
const RIGHT_MODE_KEY = 'akairy.notes.rightMode'
const rightMode = ref<'preview' | 'edit'>(
  localStorage.getItem(RIGHT_MODE_KEY) === 'edit' ? 'edit' : 'preview',
)
function setRightMode(m: 'preview' | 'edit') {
  rightMode.value = m
  try {
    localStorage.setItem(RIGHT_MODE_KEY, m)
  } catch {
    /* 隐私模式下写入失败，忽略即可，不影响本次会话 */
  }
}

const current = computed(() => notes.value.find((n) => n.path === selectedPath.value) || null)
const isCard = computed(() => (current.value?.note_mode ?? detail.value?.note_mode) === 'card')
const isArchived = computed(() => current.value?.status === 'archived')

const saveText = computed(() => {
  if (saveState.value === 'saving') return '保存中…'
  if (saveState.value === 'dirty') return '未保存'
  if (saveState.value === 'conflict') return '有冲突待处理'
  return savedAt.value ? `已保存 ${savedAt.value}` : '已保存'
})

/** 预览去掉 frontmatter 块（头部字段已由上方卡片呈现，避免重复渲染一段 YAML）。 */
function stripFrontmatter(text: string): string {
  return text.replace(/^---\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?/, '')
}

const previewHtml = computed(() => renderMarkdown(stripFrontmatter(content.value)))

const wordCount = computed(() => {
  const chars = content.value.replace(/\s/g, '').length
  const lines = content.value.split('\n').length
  return `${chars} 字 · ${lines} 行`
})

/* 索引状态：初始取 note_file.indexed_status，保存后等 index.updated 回执 */
const indexState = ref<'ready' | 'pending' | 'failed' | 'excluded'>('ready')
const indexText = computed(
  () =>
    ({ ready: '已索引', pending: '等待索引', failed: '索引失败', excluded: '未纳入索引' })[indexState.value],
)
let indexTimer: number | null = null

function setIndexState(v: NoteRow['indexed_status']) {
  indexState.value = (v || 'ready') as typeof indexState.value
}

function markIndexPending() {
  indexState.value = 'pending'
  if (indexTimer) window.clearInterval(indexTimer)
  // 兜底：8s 后后端未回执也回到「已索引」（正常由 index.updated 帧先到）
  indexTimer = window.setInterval(() => setIndexState('ready'), 9000)
}

function applyDetail(info: NoteDetail) {
  detail.value = info
  content.value = info.content
  baseHash.value = info.content_hash
  dirty.value = false
  saveState.value = 'saved'
  const d = new Date()
  savedAt.value = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

async function openNote(path: string) {
  if (path === selectedPath.value) return
  if (dirty.value && !window.confirm('当前笔记有未保存修改，切换将丢弃，确定？')) return
  loadingNote.value = true
  try {
    const info = (await notesApi.get(path)) as NoteDetail
    selectedPath.value = path
    applyDetail(info)
    setIndexState(current.value?.indexed_status ?? null)
    // FR-RAG-18：上报当前笔记上下文，RAG 面板据此自动感知范围。两档口径：
    // - 「当前笔记」= project（项目名；售后卡没有 project 字段，用库里的 project_name，
    //   它在卡片上回落成日期 20260825，与笔记一一对应，等价于锁定这一篇）
    // - 「当前项目」= name（客户，如「安图生物」）→ 该客户名下所有笔记
    rag.setContext({
      notePath: path,
      noteTitle: info.note_title || (info.fields?.title as string) || path.replace(/\.md$/, ''),
      customer: info.fields?.name ?? undefined,
      project: (info.fields?.project as string) || current.value?.project_name || undefined,
    })
    if (route.query.path !== path) router.replace({ path: '/notes', query: { path } })
    await nextTick()
    if (rightMode.value === 'edit') editorEl.value?.focus()
  } catch (e) {
    ui.toast('error', `打开笔记失败：${(e as Error).message}`)
  } finally {
    loadingNote.value = false
  }
}

function closeNote(confirmDirty = true) {
  if (confirmDirty && dirty.value && !window.confirm('当前笔记有未保存修改，返回列表将丢弃，确定？')) return
  dirty.value = false
  selectedPath.value = ''
  detail.value = null
  content.value = ''
  baseHash.value = null
  rag.setContext({})
  if (route.query.path) router.replace({ path: '/notes' })
}

async function doSave(silent = false): Promise<boolean> {
  if (!selectedPath.value || saving.value) return false
  if (!dirty.value) {
    if (!silent) ui.toast('info', '没有需要保存的修改')
    return true
  }
  saving.value = true
  saveState.value = 'saving'
  try {
    const resp = await notesApi.save(selectedPath.value, { content: content.value, base_hash: baseHash.value })
    baseHash.value = resp.content_hash
    dirty.value = false
    saveState.value = 'saved'
    const d = new Date()
    savedAt.value = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    markIndexPending()
    if (!silent) ui.toast('success', '已写入 vault（增量索引稍后自动处理）')
    await refreshList()
    return true
  } catch (e) {
    await handleSaveError(e)
    return false
  } finally {
    saving.value = false
  }
}

async function handleSaveError(e: unknown) {
  const err = e as ApiError
  if (err.code === 'edit_conflict') {
    await openConflict()
    return
  }
  saveState.value = dirty.value ? 'dirty' : 'saved'
  ui.toast('error', `保存失败：${err.message}`)
}

/* ---------------- 自动保存 ---------------- */
let saveTimer: number | null = null

function onEdit() {
  dirty.value = true
  saveState.value = 'dirty'
  if (saveTimer) window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => void doSave(true), 1200)
}

function scheduleSaveNow() {
  if (saveTimer) window.clearTimeout(saveTimer)
  void doSave(false)
}

/* ---------------- 头部字段 ---------------- */
/** 后台自动保存进行中时等它落定，避免头部改写被静默放弃（最多等 1.6s）。 */
async function waitForIdle() {
  for (let i = 0; i < 8 && saving.value; i++) {
    await new Promise((r) => window.setTimeout(r, 200))
  }
}

async function patchMeta(patch: Record<string, string>) {
  if (!selectedPath.value) return
  await waitForIdle()
  if (dirty.value) {
    const saved = await doSave(true)
    if (!saved || dirty.value) return // 冲突未解决，先不写头部
  }
  try {
    const info = (await notesApi.meta(selectedPath.value, { ...patch, base_hash: baseHash.value })) as NoteDetail
    applyDetail(info)
    await refreshList()
    ui.toast('success', '头部字段已更新')
  } catch (e) {
    await handleSaveError(e)
    await refreshList()
  }
}

function onField(key: 'customer' | 'project_name' | 'start_date', ev: Event) {
  const value = (ev.target as HTMLInputElement).value.trim()
  if (!value) {
    ui.toast('warn', `${key === 'customer' ? '客户' : key === 'project_name' ? '项目名' : '开始日期'}不能为空`)
    void refreshList()
    return
  }
  const row = current.value
  const old =
    key === 'customer' ? (row?.customer ?? '') : key === 'project_name' ? (row?.project_name ?? '') : (row?.start_date ?? '')
  if (value === old) return
  void patchMeta({ [key]: value })
}

function onStatus(ev: Event) {
  const value = (ev.target as HTMLSelectElement).value as 'active' | 'archived'
  void patchMeta({ status: value })
}

async function toggleArchive() {
  if (!current.value) return
  const next = current.value.status === 'active' ? 'archived' : 'active'
  // 走 meta 接口而非 projects：拿回改写后的最新正文，避免编辑器里的旧 status 在下次保存时把它撤回
  await patchMeta({ status: next })
  if (current.value?.status === next) {
    ui.toast('success', next === 'archived' ? '已归档（不自动改变历史记录）' : '已恢复为进行中')
  }
}

/* ---------------- 删除（FR-NOTE-12：软删除 → vault .trash） ---------------- */
const deleteTarget = ref<NoteRow | null>(null)
const deleting = ref(false)

function askDelete(n: NoteRow) {
  deleteTarget.value = n
}

function cancelDelete() {
  if (deleting.value) return
  deleteTarget.value = null
}

async function confirmDelete() {
  const target = deleteTarget.value
  if (!target || deleting.value) return
  deleting.value = true
  try {
    const resp = await notesApi.remove(target.path)
    deleteTarget.value = null
    // 删除的正是右栏打开的那篇 → 先关右栏，避免详情指向已不存在的文件
    if (selectedPath.value === target.path) closeNote(false)
    await refreshList()
    ui.toast('success', `已移入 ${resp.trashed_to || '.trash'}（可还原）`)
  } catch (e) {
    ui.toast('error', `删除失败：${(e as Error).message}`)
  } finally {
    deleting.value = false
  }
}

/* ---------------- 编辑动作 ---------------- */
function insertToday() {
  const tpl = `## ${todayStr()} 新问题标题\n**现象**：\n\n**排查**：\n\n**处理**：\n\n**结果**：\n\n**待跟进**：\n`
  // 追加到笔记末尾：新小节写在最下方，不打乱已有「现象→配置→排查」的叙述顺序
  const body = content.value.replace(/\s+$/, '')
  const next = body ? `${body}\n\n${tpl}` : tpl
  content.value = next
  onEdit()
  setRightMode('edit')
  void nextTick(() => {
    const el = editorEl.value
    if (!el) return
    el.focus()
    const start = next.length - tpl.length + 3 + todayStr().length + 1
    el.setSelectionRange(start, start + '新问题标题'.length)
    el.scrollTop = el.scrollHeight
  })
  ui.toast('success', '已在末尾插入今日小节模板')
}

async function reindex() {
  if (!selectedPath.value) return
  try {
    const r = await notesApi.reindex(selectedPath.value)
    setIndexState(r.status === 'failed' ? 'failed' : 'ready')
    ui.toast(r.status === 'failed' ? 'error' : 'success', `索引${r.status === 'failed' ? '失败' : '完成'}：${r.chunks ?? 0} 块`)
    await refreshList()
  } catch (e) {
    ui.toast('error', `重建索引失败：${(e as Error).message}`)
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

/* ---------------- 冲突三选（FR-NOTE-05） ---------------- */
const showConflict = ref(false)
const serverContent = ref('')
const serverHash = ref('')
const saveAsPath = ref('')

async function openConflict() {
  saveState.value = 'conflict'
  try {
    const info = (await notesApi.get(selectedPath.value)) as NoteDetail
    serverContent.value = info.content
    serverHash.value = info.content_hash
    saveAsPath.value = selectedPath.value.replace(/\.md$/, '') + '-副本.md'
    showConflict.value = true
  } catch (e) {
    ui.toast('error', `读取文件最新内容失败：${(e as Error).message}`)
  }
}

function conflictReload() {
  content.value = serverContent.value
  baseHash.value = serverHash.value
  dirty.value = false
  saveState.value = 'saved'
  showConflict.value = false
  ui.toast('success', '已重新加载文件最新内容')
}

async function conflictOverwrite() {
  showConflict.value = false
  saving.value = true
  saveState.value = 'saving'
  try {
    const resp = await notesApi.save(selectedPath.value, { content: content.value, base_hash: null }, { force: true })
    baseHash.value = resp.content_hash
    dirty.value = false
    saveState.value = 'saved'
    markIndexPending()
    ui.toast('success', '已用工作台内容覆盖文件')
    await refreshList()
  } catch (e) {
    ui.toast('error', `覆盖失败：${(e as Error).message}`)
    saveState.value = 'conflict'
  } finally {
    saving.value = false
  }
}

async function conflictSaveAs() {
  try {
    const r = await notesApi.saveAs(selectedPath.value, content.value, saveAsPath.value.trim() || undefined)
    showConflict.value = false
    dirty.value = false
    saveState.value = 'saved'
    ui.toast('success', `已另存副本：${r.path}（原文件未改动）`)
    await refreshList()
  } catch (e) {
    ui.toast('error', `另存副本失败：${(e as Error).message}`)
  }
}

/* ---------------- 新建笔记（FR-NOTE-02/10） ---------------- */
const showCreate = ref(false)
const newCustomer = ref('')
const newProject = ref('')
const newStart = ref(todayStr())
const creating = ref(false)

function openCreate() {
  showCreate.value = true
}

const newPathPreview = computed(() => {
  const c = newCustomer.value.trim()
  const p = newProject.value.trim()
  return c && p ? `${notesFolder.value}/${c}-${p}.md` : `${notesFolder.value}/{客户}-{项目名}.md`
})

/**
 * 「将写入」的正文预览：必须与后端 new_note_template() 逐字一致
 * （backend/app/services/vault.py），改模板时两边一起改。
 * 正文结构与 vault 里的售后卡片同构（`# 标题` + `> 售后支持卡片 |` + `## 小节`），
 * 这样新建笔记的预览和 Obsidian 老笔记观感一致。
 */
const newContentPreview = computed(() => {
  const project = newProject.value.trim() || '项目名'
  const start = newStart.value
  return [
    '---',
    `name: ${newCustomer.value.trim() || '客户'}`,
    `project: ${project}`,
    'status: 进行中',
    `start_date: ${start}`,
    `updated: ${start}`,
    '---',
    `# ${project}`,
    '',
    '> 售后支持卡片 |',
    '## 现象',
    '',
    '## 配置',
    '',
    '## 排查步骤',
    '',
    '## 注意：',
    '',
    '## 后续：',
    '',
  ].join('\n')
})

async function createNote() {
  const customer = newCustomer.value.trim()
  const project = newProject.value.trim()
  if (!customer || !project) {
    ui.toast('warn', '客户与项目名为必填')
    return
  }
  creating.value = true
  try {
    const row = (await notesApi.create({
      customer,
      project_name: project,
      start_date: newStart.value,
    })) as NoteRow
    ui.toast('success', '已创建售后记录（固定头部 + 日期小节模板）')
    showCreate.value = false
    newProject.value = ''
    await refreshList()
    await openNote(row.path)
    router.replace({ path: '/notes', query: { path: row.path } })
  } catch (e) {
    const err = e as ApiError
    if (err.code === 'path_conflict') ui.toast('error', '同名笔记已存在（一项目一笔记，请在既有笔记续写）')
    else if (err.code === 'vault_not_configured' || err.code === 'path_invalid')
      ui.toast('error', 'vault 目录未配置或不存在，请先到系统设置指定 vault_root 与 notes_folder')
    else ui.toast('error', `创建失败：${err.message}`)
  } finally {
    creating.value = false
  }
}

/* ---------------- 路由 / 事件 ---------------- */
const unsubs: (() => void)[] = []

async function syncFromRoute() {
  const q = route.query
  if (typeof q.path === 'string' && q.path) {
    await openNote(q.path)
  } else if (selectedPath.value) {
    selectedPath.value = ''
    detail.value = null
  }
  if (q.new === '1') {
    openCreate()
    router.replace({ path: '/notes', query: q.path ? { path: q.path as string } : {} })
  }
}

// 顶栏「新建笔记」（?new=1）在已停留本页时也要生效
watch(
  () => route.query.new,
  (v) => {
    if (v === '1') {
      openCreate()
      router.replace({ path: '/notes', query: route.query.path ? { path: route.query.path as string } : {} })
    }
  },
)

/** FR-NOTE-06：vault 变化推送。自己刚保存的写入用 hash 比对识别出来，不误报「外部修改」。 */
async function onNoteChanged(p: { path: string; change: string }) {
  if (p.path === selectedPath.value) {
    if (p.change === 'deleted') {
      ui.toast('warn', `笔记「${p.path}」已被删除`)
      closeNote(false)
      await refreshList()
      return
    }
    try {
      const h = await notesApi.history(p.path)
      if (h.content_hash !== baseHash.value) {
        if (dirty.value) {
          ui.toast('warn', `「${p.path}」被外部修改，保存时会提示冲突`)
        } else {
          const info = (await notesApi.get(p.path)) as NoteDetail
          applyDetail(info)
          setIndexState(current.value?.indexed_status ?? null)
          ui.toast('info', '笔记已被外部修改，已自动刷新为文件最新内容')
        }
      }
    } catch {
      /* 文件正在被改写时读取可能失败，等下一次推送 */
    }
  }
  await refreshList()
}

watch(
  () => route.query.path,
  (v) => {
    const p = typeof v === 'string' ? v : ''
    if (p && p !== selectedPath.value) void openNote(p)
    else if (!p && selectedPath.value) closeNote(false)
  },
)

function onKeydown(e: KeyboardEvent) {
  // 保存键默认 Ctrl+S，可在左下角「快捷键」里改
  if (matchesCombo(e, shortcuts.bindings.save_note)) {
    e.preventDefault()
    if (selectedPath.value) scheduleSaveNow()
  }
  if (e.key === 'Escape' && showConflict.value) showConflict.value = false
}

onMounted(async () => {
  if (!config.loaded) await config.load()
  await refreshList()
  await syncFromRoute()

  unsubs.push(
    eventsSocket.on('note.changed', (payload: unknown) => {
      void onNoteChanged(payload as { path: string; change: string })
    }),
    eventsSocket.on('index.updated', (payload: unknown) => {
      const p = payload as { note_path?: string; status?: string }
      if (p.note_path && p.note_path === selectedPath.value) {
        if (indexTimer) window.clearInterval(indexTimer)
        setIndexState(p.status === 'failed' ? 'failed' : 'ready')
      }
    }),
  )
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  if (saveTimer) window.clearTimeout(saveTimer)
  if (indexTimer) window.clearInterval(indexTimer)
  unsubs.forEach((fn) => fn())
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="page page-notes" :class="{ split: !!selectedPath }">
    <!-- ==================== 左：列表（常驻，点行在右栏展开） ==================== -->
    <section class="card notes-card">
      <header class="card-head notes-head">
        <div class="card-title">
          <h2>售后笔记</h2>
          <span class="count-pill">{{ filtered.length }}</span>
        </div>
        <div class="notes-tools">
          <div class="search-field">
            <Icon name="search" :size="15" />
            <input v-model="keyword" placeholder="搜索项目、客户或标签…" autocomplete="off" />
            <button v-if="keyword" class="search-clear" title="清空" @click="keyword = ''">
              <Icon name="x" :size="12" />
            </button>
          </div>
          <div class="seg sm">
            <button
              v-for="f in filters"
              :key="f.key"
              class="seg-btn"
              :class="{ active: filter === f.key }"
              @click="filter = f.key"
            >
              {{ f.label }}<em>{{ statusCount[f.key] }}</em>
            </button>
          </div>
          <button class="btn ghost sm cleanup-btn" @click="router.push('/cleanup')">
            <Icon name="scissors" :size="14" /><span>存量整理</span>
          </button>
          <button class="btn primary sm" @click="openCreate()">
            <Icon name="plus" :size="14" /><span>新建笔记</span>
          </button>
        </div>
      </header>

      <div class="notes-table-head">
        <span class="th-collapse-wrap">
          <span>项目 / 客户</span>
          <button
            v-if="grouped.length"
            class="th-collapse"
            :class="{ on: allGroupsCollapsed }"
            type="button"
            :title="allGroupsCollapsed ? '展开全部分组' : '收起全部分组'"
            :aria-label="allGroupsCollapsed ? '展开全部分组' : '收起全部分组'"
            @click="toggleAllGroups"
          >
            <Icon :name="allGroupsCollapsed ? 'chevronsUpDown' : 'chevronsDownUp'" :size="15" />
          </button>
        </span>
        <span>状态</span><span>{{ notes.some((n) => n.note_mode === 'card') ? '日期' : '开始' }}</span>
        <span>最近更新</span><span>标签</span><span></span>
      </div>

      <div class="notes-list">
        <div v-if="listError" class="list-error">
          <div class="err-ic"><Icon name="alert" :size="20" /></div>
          <p>{{ listError }}</p>
          <p class="err-sub">售后笔记直接读写 vault，请先在系统设置中指定 vault_root 与 notes_folder。</p>
          <button class="btn primary sm" @click="router.push('/settings')">前往系统设置</button>
        </div>
        <div v-else-if="!filtered.length" class="empty-block">
          <div class="empty-ic"><Icon :name="notes.length ? 'search' : 'notes'" :size="26" /></div>
          <p>{{ notes.length ? '没有匹配的笔记' : '暂无售后笔记' }}</p>
          <span class="empty-sub">
            {{ notes.length ? '试试更换关键词或筛选条件' : `当前目录：${notesFolder}（可在设置页调整）` }}
          </span>
        </div>
        <template v-else>
          <template v-for="g in grouped" :key="g.customer">
            <div
              class="notes-group-label"
              :class="{ collapsed: isGroupCollapsed(g.customer) }"
              role="button"
              tabindex="0"
              :aria-expanded="!isGroupCollapsed(g.customer)"
              :title="isGroupCollapsed(g.customer) ? `展开「${g.customer}」` : `收起「${g.customer}」`"
              @click="toggleGroup(g.customer)"
              @keydown.enter.prevent="toggleGroup(g.customer)"
              @keydown.space.prevent="toggleGroup(g.customer)"
            >
              <Icon :name="isGroupCollapsed(g.customer) ? 'chevronRight' : 'chevronDown'" :size="12" class="ngl-caret" />
              <Icon name="folder" :size="13" /> {{ g.customer }}
              <span>{{ g.items.length }}</span>
            </div>
            <template v-if="!isGroupCollapsed(g.customer)">
              <div
                v-for="n in g.items"
                :key="n.path"
                class="note-row"
                :class="{ active: n.path === selectedPath }"
                @click="openNote(n.path)"
              >
                <div class="nr-main">
                  <span class="nr-title">
                    {{ rowTitle(n) }}
                    <span v-if="n.head_ok === 0" class="badge warn" title="缺少 name 头部字段（存量整理可补齐）">缺头</span>
                  </span>
                  <span class="nr-summary">{{ n.summary || '—' }}</span>
                </div>
                <div class="nr-status">
                  <span class="badge" :class="rowBadge(n).cls">{{ rowBadge(n).text }}</span>
                </div>
                <div class="nr-date nr-start">{{ n.start_date || '—' }}</div>
                <div class="nr-date nr-upd">
                  <strong>{{ n.updated_date || '—' }}</strong>
                  <em>{{ relDays(n.updated_date) }}</em>
                </div>
                <div class="nr-tags">
                  <span v-for="t in rowTags(n).slice(0, 2)" :key="t" class="tag">{{ t }}</span>
                </div>
                <div class="nr-act" @click.stop>
                  <Icon class="nr-go-ic" name="chevronRight" :size="15" />
                  <button class="nr-del" title="删除笔记（移入 vault 的 .trash，可还原）" @click.stop="askDelete(n)">
                    <Icon name="trash" :size="14" />
                  </button>
                </div>
              </div>
            </template>
          </template>
        </template>
      </div>

      <footer class="card-foot">
        <span class="foot-item"><Icon name="database" :size="13" /> 数据直接读写 Obsidian vault · 不建立副本</span>
        <span class="foot-dot" />
        <span>点击任意行打开笔记</span>
        <span class="foot-right">{{ notesFolder }}</span>
      </footer>
    </section>

    <!-- ==================== 右：当前笔记详情 ==================== -->
    <section v-if="selectedPath" class="note-detail">
      <div class="note-topbar">
      <div class="note-crumb">
        <button class="icon-btn" title="关闭右栏（回到纯列表）" @click="closeNote()">
          <Icon name="x" :size="16" />
        </button>
        <span class="crumb-note">{{ current?.customer || detail?.fields?.name || '未识别客户' }}</span>
        <Icon name="chevronRight" :size="12" />
        <span class="crumb-current" :title="`${notesFolder} / ${selectedPath}`">{{ rowTitle(current) || selectedPath }}</span>
      </div>
      <div class="note-actions">
        <span class="save-status" :class="saveState"><span class="dot" />{{ saveText }}</span>
        <span class="index-chip" :class="indexState">
          <Icon :name="indexState === 'ready' ? 'checkCircle' : indexState === 'failed' ? 'alert' : 'clock'" :size="13" />
          {{ indexText }}
        </span>
        <button class="btn sm ghost" :disabled="!dirty || saving" @click="scheduleSaveNow">
          <Icon name="save" :size="14" /><span>保存</span>
        </button>
        <button class="btn sm" :class="isArchived ? 'primary' : 'ghost'" @click="toggleArchive">
          <Icon name="archive" :size="14" /><span>{{ isArchived ? '取消归档' : '归档' }}</span>
        </button>
      </div>
    </div>

    <div v-if="loadingNote" class="note-loading">正在读取笔记…</div>

    <template v-else-if="detail">
      <div v-if="!detail.head_ok" class="head-warn">
        <Icon name="alert" :size="14" />
        头部字段缺失：{{ detail.missing_fields.join('、') }}（不影响编辑保存，可由「存量整理」批量补齐）
      </div>

      <section class="card note-fields">
        <div class="nf-head">
          <span class="nf-title"><Icon name="notes" :size="15" /> 头部字段</span>
          <span class="nf-hint">frontmatter 固定字段，用于看板统计与检索范围</span>
        </div>
        <div class="nf-grid">
          <label class="nf-field">
            <span>客户</span>
            <input
              class="input"
              :value="current?.customer || ''"
              placeholder="frontmatter name"
              @change="onField('customer', $event)"
            />
          </label>
          <label v-if="!isCard" class="nf-field">
            <span>项目名</span>
            <input class="input" :value="current?.project_name || ''" @change="onField('project_name', $event)" />
          </label>
          <div v-else class="nf-field">
            <span>主题 / 标签</span>
            <div class="nf-tags" :title="detail.tags.join(' / ') || detail.note_title">
              <span v-for="t in detail.tags" :key="t" class="tag">{{ t }}</span>
              <span v-if="!detail.tags.length" class="nf-empty">正文 H1 作为标题，标签取自 frontmatter tags</span>
            </div>
          </div>
          <label class="nf-field">
            <span>状态</span>
            <select class="input" :value="current?.status || 'active'" @change="onStatus">
              <option value="active">进行中</option>
              <option value="archived">已归档</option>
            </select>
          </label>
          <label v-if="!isCard" class="nf-field">
            <span>开始日期</span>
            <input class="input" type="date" :value="current?.start_date || ''" @change="onField('start_date', $event)" />
          </label>
          <div v-else class="nf-field">
            <span>记录日期</span>
            <span class="nf-readonly">{{ current?.start_date || '—' }}</span>
          </div>
          <div class="nf-field">
            <span>最近更新</span>
            <span class="nf-readonly">{{ current?.updated_date || '—' }}</span>
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
          <div class="editor-tools">
            <button class="btn ghost sm" @click="insertToday">
              <Icon name="plus" :size="13" /><span>插入今日小节</span>
            </button>
            <button class="btn ghost sm" @click="reindex">
              <Icon name="refresh" :size="13" /><span>重建索引</span>
            </button>
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
            v-model="content"
            class="editor-input"
            spellcheck="false"
            placeholder="直接编辑这份 .md，Ctrl+S 立即保存…"
            @input="onEdit"
          />
        </div>
        <footer class="editor-foot">
          <span class="editor-path" :title="selectedPath" @click="copyPath">
            <Icon name="file" :size="12" /> {{ selectedPath }}
          </span>
          <button class="link-btn" @click="copyPath">复制路径</button>
          <span class="editor-foot-right">编辑后自动保存 · 约 8 秒后增量索引</span>
        </footer>
      </section>
    </template>
    </section>
  </div>

  <!-- ==================== 新建笔记 ==================== -->
  <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
    <div class="modal nn-modal">
      <h3>新建售后记录</h3>
      <p class="modal-sub">写入现有售后笔记目录，自动带固定头部骨架与日期小节格式（一项目一笔记）。</p>
      <div class="nn-grid">
        <label class="modal-field">
          <span>客户（frontmatter name）</span>
          <input v-model="newCustomer" class="input" placeholder="如：长峰机电210" />
        </label>
        <label class="modal-field">
          <span>项目名</span>
          <input v-model="newProject" class="input" placeholder="如：复卷机张力控制异常" />
        </label>
      </div>
      <label class="modal-field">
        <span>开始日期</span>
        <input v-model="newStart" class="input" type="date" />
      </label>
      <div class="nn-template">
        <div class="nn-template-head"><Icon name="file" :size="12" /> 将写入</div>
        <div class="nn-path"><code>{{ newPathPreview }}</code></div>
        <pre class="nn-body-preview">{{ newContentPreview }}</pre>
      </div>
      <div class="modal-actions">
        <button class="btn ghost" @click="showCreate = false">取消</button>
        <button class="btn primary" :disabled="creating" @click="createNote">
          {{ creating ? '创建中…' : '创建并打开' }}
        </button>
      </div>
    </div>
  </div>

  <!-- ==================== 删除确认（软删除 → vault .trash） ==================== -->
  <div v-if="deleteTarget" class="modal-mask" @click.self="cancelDelete">
    <div class="modal del-modal">
      <h3>删除这篇笔记？</h3>
      <p class="modal-sub">
        文件不会被真正抹除：会移入 vault 根目录的 <code>.trash/</code>，在 Obsidian 或资源管理器里拖回原目录即可还原。
      </p>
      <div class="del-target">
        <div class="del-name"><Icon name="file" :size="13" /> {{ rowTitle(deleteTarget) }}</div>
        <div class="del-path">{{ notesFolder }} / {{ deleteTarget.path }}</div>
      </div>
      <div class="del-tip"><Icon name="alert" :size="14" /> 列表与检索索引会同步移除{{ selectedPath === deleteTarget.path ? '，正在打开的详情将被关闭' : '' }}</div>
      <div class="modal-actions">
        <button class="btn ghost" :disabled="deleting" @click="cancelDelete">取消</button>
        <button class="btn danger" :disabled="deleting" @click="confirmDelete">
          {{ deleting ? '删除中…' : '删除并移入回收目录' }}
        </button>
      </div>
    </div>
  </div>

  <!-- ==================== 冲突三选（FR-NOTE-05） ==================== -->
  <div v-if="showConflict" class="modal-mask" @click.self="showConflict = false">
    <div class="modal conflict-modal">
      <h3>检测到文件冲突</h3>
      <p class="modal-sub">
        该笔记在工作台之外（如 Obsidian）被修改过，而工作台里还有未保存内容。为避免静默覆盖，请选择处理方式。
      </p>
      <div class="conflict-tip"><Icon name="alert" :size="16" /> 文件指纹不匹配：保存被拦截，需要你明确选择</div>
      <div class="conflict-grid">
        <div class="conflict-pane">
          <h4>工作台中的未保存内容</h4>
          <pre>{{ content }}</pre>
        </div>
        <div class="conflict-pane external">
          <h4>文件当前内容</h4>
          <pre>{{ serverContent }}</pre>
        </div>
      </div>
      <div class="conflict-actions">
        <button class="btn ghost" @click="conflictReload">重新加载文件版本</button>
        <button class="btn ghost" @click="conflictOverwrite">用我的覆盖</button>
        <div class="save-as-row">
          <input v-model="saveAsPath" class="input" placeholder="副本路径" />
          <button class="btn ghost" @click="conflictSaveAs">另存副本</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ==================== 通用 ==================== */
.page { min-width: 0; }
/* 页面级两栏：左列表常驻，点行在右侧展开详情（未选中时列表占满） */
.page-notes { display: flex; flex-direction: column; gap: 11px; height: 100%; min-height: 0; container-type: inline-size; container-name: notepage; }
.page-notes.split { flex-direction: row; align-items: stretch; }
/* 主区太窄（右侧助手面板展开 + 小屏）时，列表让位、详情占满，用 × 返回列表 */
@container notepage (max-width: 760px) {
  .page-notes.split > .notes-card { display: none; }
}
.page-notes.split > .notes-card { width: clamp(360px, 40%, 480px); flex: none; }
.note-detail {
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
}
.input {
  height: 34px;
  width: 100%;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  font-size: 12.5px;
  color: var(--ink);
  font-family: inherit;
}
.input:focus { outline: none; border-color: var(--blue-line); box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.1); }
.tag {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 5px;
  background: #f2f1ec;
  border: 1px solid var(--line-soft);
  color: var(--ink-2);
  font-size: 10px;
  white-space: nowrap;
}

/* ==================== 列表 ==================== */
.notes-card { display: flex; flex-direction: column; overflow: hidden; }
.page-notes.split > .notes-card { container-type: inline-size; container-name: noteslist; }
.notes-head { align-items: center; padding-bottom: 12px; }
.notes-head .card-title { margin-right: auto; }
.notes-tools { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.search-field {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--muted);
  width: 230px;
  transition: border-color 0.14s, box-shadow 0.14s;
}
.search-field:focus-within { border-color: var(--blue-line); box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.1); }
.search-field input { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; font-size: 12.5px; color: var(--ink); }
.search-field input::placeholder { color: var(--muted); }
.search-clear { border: 0; background: transparent; color: var(--muted); display: grid; place-items: center; padding: 2px; border-radius: 4px; }
.search-clear:hover { background: #f1f0eb; color: var(--ink); }

.notes-table-head,
.note-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 76px 78px 92px 96px 22px;
  gap: 10px;
  align-items: center;
  padding: 0 18px;
}
/* 两栏模式：列表缩窄 → 先去掉标签列，末尾动作列（悬停删除）保留 */
.page-notes.split .notes-table-head,
.page-notes.split .note-row {
  grid-template-columns: minmax(0, 1fr) 58px 74px 82px 22px;
  gap: 8px;
  padding: 0 12px;
}
/* 两栏模式下每行留出上下呼吸空间，避免两行文字贴在一起 */
.page-notes.split .note-row { padding: 9px 12px; }
.page-notes.split .nr-main { gap: 4px; }
.page-notes.split .nr-summary { font-size: 11px; line-height: 1.5; }
.page-notes.split .nr-date { line-height: 1.35; }
.page-notes.split .nr-date em { margin-top: 2px; }
.page-notes.split .notes-group-label { padding: 8px 12px 6px; }
.page-notes.split .notes-table-head span:nth-child(5),
.page-notes.split .nr-tags,
.page-notes.split .nr-go-ic { display: none; }
@container noteslist (max-width: 402px) {
  .page-notes.split .notes-table-head,
  .page-notes.split .note-row { grid-template-columns: minmax(0, 1fr) 58px 82px 22px; }
  .page-notes.split .notes-table-head span:nth-child(3),
  .page-notes.split .nr-start { display: none; }
}
.page-notes.split .notes-head { flex-wrap: wrap; gap: 8px; }
.page-notes.split .notes-head .card-title { margin-right: 0; flex: none; }
.page-notes.split .notes-tools { width: 100%; gap: 6px; }
.page-notes.split .search-field { flex: 1 1 100%; min-width: 0; }
@container noteslist (max-width: 420px) {
  .page-notes.split .cleanup-btn { display: none; }
}
.page-notes.split .notes-list { max-height: none; flex: 1; }
.page-notes.split .note-row.active { background: #eef4fb; box-shadow: inset 2px 0 0 var(--blue); }
.page-notes.split .note-row.active:hover { background: #e9f1fa; }
.notes-table-head {
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
.notes-list { display: flex; flex-direction: column; max-height: calc(100vh - 300px); overflow: auto; }
/* 客户分组带：用页面主色系（蓝）替代原来的暖灰，
   在长列表里能一眼抓住分组边界；数量做成同色系胶囊，与「进行中/待跟进」徽标区分开 */
.notes-group-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 18px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.2px;
  color: var(--blue-ink);
  background: #f4f8fe;
  border-top: 1px solid #e4edfb;
  /* 整条组头即折叠开关 */
  cursor: pointer;
  user-select: none;
  transition: background 0.12s;
}
.notes-group-label:hover { background: #eaf2fe; }
.notes-group-label:active { background: #e2edfd; }
.notes-group-label:focus-visible { outline: 2px solid var(--blue); outline-offset: -2px; }
/* 收起时组头压暗一档 + 保留一个底部细线，视觉上像「合上的抽屉」 */
.notes-group-label.collapsed { background: #f0f5fd; border-bottom: 1px solid #e4edfb; }
.notes-group-label.collapsed > span { opacity: 0.75; }
.notes-group-label .ngl-caret { color: var(--blue); opacity: 0.65; }
.notes-group-label svg { color: var(--blue); opacity: 0.7; }
.notes-group-label > span {
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
.note-row { padding: 10px 18px; border-top: 1px solid #f4f3ee; cursor: pointer; transition: background 0.1s; }
.note-row:hover { background: #f8f9fb; }
.nr-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.nr-title {
  font-size: 13.5px;
  font-weight: 640;
  color: var(--ink);
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: flex;
  align-items: center;
  gap: 6px;
}
.nr-title .badge { flex: none; }
/* 模块更透之后，摘要这行最容易被背景纹理吃掉，从 muted 抬到 ink-2（仍明显弱于标题） */
.nr-summary { font-size: 10.5px; line-height: 1.45; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nr-status { font-size: 11px; }
/* 日期两列（开始 / 最近更新）：字号和字重抬一档，时间信息本来就是要快速扫的字段 */
.nr-date { font-size: 11.5px; color: var(--ink); font-variant-numeric: tabular-nums; }
.nr-date em { display: block; font-style: normal; font-size: 10px; color: var(--ink-2); margin-top: 2px; }
.nr-date strong { font-weight: 640; }
.nr-tags { display: flex; gap: 3px; flex-wrap: wrap; overflow: hidden; max-height: 20px; }
.nr-act {
  position: relative;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
}
.nr-go-ic { color: #b7b3a9; }
/* 悬停时箭头让位给删除按钮（占同一格，不改变行高/列宽） */
.nr-del {
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
.note-row:hover .nr-go-ic { display: none; }
.note-row:hover .nr-del { display: grid; }
.nr-del:hover { background: var(--red-soft); color: var(--red); }

.list-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 34px 20px;
  text-align: center;
  color: var(--amber-ink);
}
.err-ic { width: 44px; height: 44px; border-radius: 50%; background: var(--amber-soft); display: grid; place-items: center; }
.list-error p { margin: 0; font-size: 13px; }
.list-error .err-sub { font-size: 11.5px; color: var(--muted); max-width: 460px; line-height: 1.6; margin-bottom: 6px; }
.foot-item { display: inline-flex; align-items: center; gap: 5px; }
.foot-right { margin-left: auto; font-family: var(--mono); font-size: 10px; color: #a6a29a; }

/* ==================== 编辑器 ==================== */
.note-topbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.note-crumb { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); min-width: 0; }
.crumb-note { white-space: nowrap; }
.crumb-current {
  color: var(--ink-2);
  font-weight: 550;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}
.note-actions { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; justify-content: flex-end; }
.save-status { font-size: 11px; color: var(--muted); display: inline-flex; align-items: center; gap: 5px; }
.save-status .dot { width: 6px; height: 6px; background: var(--green); }
.save-status.dirty .dot { background: var(--amber); }
.save-status.saving .dot { background: var(--blue); animation: pulseDot 1s infinite; }
.save-status.conflict .dot { background: var(--red); }
@keyframes pulseDot { 50% { opacity: 0.35; } }
.index-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--green-soft);
  border: 1px solid var(--green-line);
  color: var(--green-ink);
  font-size: 10.5px;
}
.index-chip.pending { background: var(--amber-soft); border-color: var(--amber-line); color: var(--amber-ink); }
.index-chip.failed { background: var(--red-soft); border-color: var(--red-line); color: var(--red); }
.index-chip.excluded { background: #f2f1ec; border-color: var(--line); color: var(--muted); }

.head-warn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 9px;
  background: var(--amber-soft);
  border: 1px solid var(--amber-line);
  color: var(--amber-ink);
  font-size: 11.5px;
}
.note-loading { padding: 40px; text-align: center; color: var(--muted); font-size: 13px; }

.note-fields { padding: 13px 17px 14px; flex: none; }
.nf-head { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; }
.nf-title { font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 6px; flex: none; white-space: nowrap; }
.nf-title .ic { color: var(--muted); }
.nf-hint { font-size: 10.5px; color: var(--muted); margin-left: auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 9px; }
.nf-field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.nf-field > span { font-size: 10.5px; color: var(--muted); }
.nf-readonly {
  height: 34px;
  display: flex;
  align-items: center;
  padding: 0 10px;
  background: #f6f5f0;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  font-size: 12px;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}
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
.nf-empty { font-size: 10.5px; color: var(--muted); }

.editor-card { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 260px; overflow: hidden; }
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
.editor-tools { display: flex; gap: 4px; }
.editor-meta { margin-left: auto; font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
/* 右栏内容区：单栏，靠 toolbar 上的「预览 / 编辑」切换，不再在栏内再分左右 */
.editor-area { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; background: #fff; }
.editor-mode { font-size: 10.5px; color: var(--muted); letter-spacing: 0.2px; }
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

/* 预览区 markdown 排版（与 RAG 回答区保持同一口径） */
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
/* KaTeX：行间公式可横向滚动，长公式不撑破预览栏 */
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

/* ==================== 弹窗 ==================== */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(23, 24, 26, 0.32);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 120;
  padding: 20px;
}
.modal {
  background: var(--card);
  border-radius: 14px;
  box-shadow: var(--shadow-pop);
  padding: 20px 22px;
  width: 460px;
  max-height: 88vh;
  overflow: auto;
}
.modal h3 { margin: 0 0 6px; font-size: 15.5px; font-weight: 650; }
.modal-sub { margin: 0 0 14px; font-size: 11.5px; color: var(--muted); line-height: 1.7; }
.modal-field { display: flex; flex-direction: column; gap: 5px; }
.modal-field > span { font-size: 10.5px; color: var(--muted); }
.nn-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 11px; margin-bottom: 11px; }
.nn-template { margin-top: 14px; border: 1px solid var(--line-soft); border-radius: 10px; overflow: hidden; background: #fcfbf7; }
.nn-template-head { padding: 7px 11px; font-size: 10.5px; color: var(--muted); display: flex; align-items: center; gap: 5px; border-bottom: 1px solid var(--line-soft); }
.nn-path { padding: 8px 11px; font-size: 11px; color: var(--muted); border-bottom: 1px solid var(--line-soft); }
.nn-path code { font-family: var(--mono); color: var(--blue-ink); background: var(--blue-soft); padding: 2px 6px; border-radius: 5px; font-size: 10.5px; }
.nn-body-preview {
  margin: 0;
  padding: 10px 12px;
  font-family: var(--mono);
  font-size: 10.5px;
  line-height: 1.7;
  color: var(--ink-2);
  overflow: auto;
  max-height: 220px;
}
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }

.conflict-modal { width: 780px; max-width: 96vw; }
.conflict-tip {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 11px;
  border-radius: 8px;
  background: var(--amber-soft);
  border: 1px solid var(--amber-line);
  color: var(--amber-ink);
  font-size: 11.5px;
  margin-bottom: 12px;
}
.conflict-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.conflict-pane { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.conflict-pane h4 {
  margin: 0;
  padding: 8px 11px;
  font-size: 11px;
  background: #f7f6f1;
  border-bottom: 1px solid var(--line-soft);
  color: var(--ink-2);
  font-weight: 600;
}
.conflict-pane.external h4 { background: #f1f7ff; color: var(--blue-ink); }
.conflict-pane pre {
  margin: 0;
  padding: 11px;
  background: #fffdf8;
  max-height: 260px;
  overflow: auto;
  font-family: var(--mono);
  font-size: 10.5px;
  line-height: 1.75;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
}
.conflict-actions { display: flex; align-items: center; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.save-as-row { display: flex; gap: 6px; margin-left: auto; }
.save-as-row .input { height: 30px; width: 190px; }

/* ==================== 删除确认 ==================== */
.btn.danger {
  background: var(--red);
  border-color: var(--red);
  color: #fff;
  box-shadow: 0 1px 2px rgba(208, 59, 59, 0.22);
}
.btn.danger:hover { background: #b93232; border-color: #b93232; }
.del-modal { max-width: 460px; }
.del-modal code {
  font-family: var(--mono);
  font-size: 11.5px;
  padding: 1px 4px;
  border-radius: 4px;
  background: #f3f1ea;
  color: var(--ink-2);
}
.del-target {
  margin: 12px 0 10px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 9px;
  background: #fbfaf6;
}
.del-name { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 560; color: var(--ink); }
.del-path {
  margin-top: 4px;
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--muted);
  word-break: break-all;
}
.del-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--amber-soft);
  border: 1px solid var(--amber-line);
  color: var(--amber-ink);
  font-size: 11.5px;
}
.del-modal .modal-actions { margin-top: 14px; }

@media (max-width: 1200px) {
  .page-notes.split > .notes-card { width: 320px; }
}
@media (max-width: 1180px) {
  .notes-table-head, .note-row { grid-template-columns: minmax(0, 1fr) 70px 78px 90px 22px; }
  .notes-table-head span:nth-child(5), .nr-tags { display: none; }
}
</style>
