<script setup lang="ts">
/**
 * PDF 手册（M9 浏览 + M3 手册侧入库队列）
 *
 * 数据流：GET /api/pdf/tree（后端顺带增量同步磁盘）→ 分组列表；
 * 入库/重建走队列（POST /api/manuals/ingest），进度来自 WS ingest.progress + 轮询兜底；
 * 打开阅读直接复用文件流端点（FileResponse 支持 Range），带 #page=N 跳页。
 *
 * 与笔记索引的区别：PDF 是「引用不复制」——移出手册只删索引，绝不删磁盘文件。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { eventsSocket, pdfApi, type IngestState, type PdfManual, type PdfStatus } from '@/api'
import { useUiStore } from '@/stores/ui'
import { useRagStore } from '@/stores/rag'
import Icon from '@/components/Icon.vue'

const props = withDefaults(defineProps<{ compact?: boolean; split?: boolean }>(), {
  compact: false,
  split: false,
})

const ui = useUiStore()
const rag = useRagStore()
const router = useRouter()
const route = useRoute()

const groups = ref<{ name: string; items: PdfManual[] }[]>([])
const total = ref(0)
const rootPath = ref('')
const loading = ref(false)
const loadError = ref('')
const rootMissing = ref(false)
const keyword = ref('')
const filter = ref<'' | 'ready' | 'unindexed' | 'processing' | 'error'>('')
const actingId = ref(0)
const collapsed = ref<Set<string>>(new Set())

const ingest = ref<IngestState>({
  running: false,
  manual_id: null,
  name: '',
  stage: '',
  progress: 0,
  started_at: null,
  finished_at: null,
  error: '',
})
/** 入库刚结束的 2 秒内继续显示进度条（避免瞬间消失看不到结果） */
const justFinished = ref(false)

const STATUS: Record<PdfStatus, { label: string; cls: string }> = {
  discovered: { label: '待入库', cls: 'muted' },
  queue: { label: '排队中', cls: 'warn' },
  parsing: { label: '解析中', cls: 'warn' },
  rebuilding: { label: '重建中', cls: 'warn' },
  ready: { label: '已入库', cls: 'ok' },
  update: { label: '有更新', cls: 'warn' },
  failed: { label: '失败', cls: 'bad' },
  invalid: { label: '已失效', cls: 'bad' },
}

const FILTERS: { key: '' | 'ready' | 'unindexed' | 'processing' | 'error'; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'ready', label: '已入库' },
  { key: 'unindexed', label: '待入库' },
  { key: 'processing', label: '处理中' },
  { key: 'error', label: '异常' },
]

const GROUP_OF: Record<string, '' | 'ready' | 'unindexed' | 'processing' | 'error'> = {
  discovered: 'unindexed',
  queue: 'processing',
  parsing: 'processing',
  rebuilding: 'processing',
  ready: 'ready',
  update: 'error',
  failed: 'error',
  invalid: 'error',
}

const all = computed<PdfManual[]>(() => groups.value.flatMap((g) => g.items))

const counts = computed(() => {
  const c = { '': all.value.length, ready: 0, unindexed: 0, processing: 0, error: 0 }
  for (const m of all.value) {
    const key = GROUP_OF[m.status]
    if (key) c[key] += 1
  }
  return c
})

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return all.value.filter((m) => {
    if (filter.value && GROUP_OF[m.status] !== filter.value) return false
    if (!kw) return true
    return [m.name, m.rel_path, m.brand ?? ''].some((v) => v.toLowerCase().includes(kw))
  })
})

const visibleGroups = computed(() => {
  const byPath = new Map(filtered.value.map((m) => [m.path, m]))
  return groups.value
    .map((g) => ({ name: g.name, items: g.items.filter((m) => byPath.has(m.path)) }))
    .filter((g) => g.items.length > 0)
})

const pendingIds = computed(() =>
  all.value.filter((m) => m.status === 'discovered' || (m.status === 'failed' && !m.chunks)).map((m) => m.id),
)

const pct = computed(() => {
  const t = ingest.value.progress || 0
  return ingest.value.running ? Math.max(3, Math.min(100, t)) : 100
})

const STAGE_LABEL: Record<string, string> = {
  queued: '排队中',
  parsing: '解析文本',
  chunking: '章节切块',
  embedding: '向量化',
  writing: '写入索引',
}

function statusOf(m: PdfManual) {
  return STATUS[m.status] ?? { label: m.status, cls: 'muted' }
}

function fmtSize(bytes: number | null): string {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function toggleGroup(name: string) {
  const next = new Set(collapsed.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  collapsed.value = next
}

// ---- 加载 ------------------------------------------------------------------

const ingestWs = ref<IngestState | null>(null)
/** 首次进入自动打开第一本；用户主动关过之后就不再替他做决定 */
const autoOpened = ref(false)

async function refresh(showError = true) {
  loading.value = true
  try {
    const data = await pdfApi.tree()
    rootPath.value = data.root
    groups.value = data.groups
    total.value = data.total
    rootMissing.value = false
    loadError.value = ''
  } catch (e) {
    const err = e as ApiError
    if (err instanceof ApiError && err.code === 'path_invalid') {
      rootMissing.value = true
      loadError.value = ''
    } else if (showError) {
      loadError.value = err.message
    }
  } finally {
    loading.value = false
  }
  autoOpenFirst()
}

let pollTimer: number | null = null
let debounceTimer: number | null = null

function startPolling() {
  if (pollTimer !== null) return
  pollTimer = window.setInterval(async () => {
    try {
      ingest.value = await pdfApi.state()
      if (!ingest.value.running) stopPolling()
      void refresh(false)
    } catch {
      /* 轮询失败静默，下一轮再试 */
    }
  }, 1800)
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function onIngestEvent(raw: unknown) {
  const payload = raw as IngestState
  if (payload && typeof payload.running === 'boolean') {
    ingest.value = payload
    ingestWs.value = payload
    if (payload.running) {
      justFinished.value = false
      startPolling()
    } else {
      stopPolling()
      justFinished.value = true
      window.setTimeout(() => (justFinished.value = false), 2000)
      if (payload.error) ui.toast('warn', `入库失败：${payload.error}`)
    }
  }
  if (debounceTimer !== null) window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => {
    debounceTimer = null
    void refresh(false)
  }, 700)
}

// ---- 动作 ------------------------------------------------------------------

async function rescan() {
  actingId.value = -1
  try {
    const r = await pdfApi.rescan()
    const bits = [
      r.added ? `新增 ${r.added}` : '',
      r.updated ? `更新 ${r.updated}` : '',
      r.invalid ? `失效 ${r.invalid}` : '',
      r.removed ? `清理 ${r.removed}` : '',
    ].filter(Boolean)
    ui.toast('success', bits.length ? `扫描完成：${bits.join('，')}` : `扫描完成：${r.total} 本，无变化`)
    await refresh()
  } catch (e) {
    ui.toast('error', `扫描失败：${(e as Error).message}`)
  } finally {
    actingId.value = 0
  }
}

async function ingestOne(m: PdfManual) {
  actingId.value = m.id
  try {
    const r = await pdfApi.ingest([m.id])
    if (!r.started && r.skipped.length) ui.toast('info', '该手册已在队列中')
    else ui.toast('info', `已加入队列：${m.name}`)
    await refresh(false)
    startPolling()
  } catch (e) {
    ui.toast('error', `入库失败：${(e as Error).message}`)
  } finally {
    actingId.value = 0
  }
}

async function ingestAll() {
  actingId.value = -1
  try {
    const r = await pdfApi.ingest(pendingIds.value.length ? pendingIds.value : undefined)
    if (!r.started && !r.queued.length) ui.toast('info', r.reason || '没有待入库的 PDF')
    else ui.toast('info', `已排入 ${r.queued.length} 本，开始解析入库`)
    await refresh(false)
    startPolling()
  } catch (e) {
    ui.toast('error', `批量入库失败：${(e as Error).message}`)
  } finally {
    actingId.value = 0
  }
}

async function rebuild(m: PdfManual) {
  actingId.value = m.id
  try {
    await pdfApi.rebuild(m.id)
    ui.toast('info', `已开始重建：${m.name}（旧索引在替换前仍可检索）`)
    await refresh(false)
    startPolling()
  } catch (e) {
    ui.toast('warn', (e as Error).message)
  } finally {
    actingId.value = 0
  }
}

async function remove(m: PdfManual) {
  if (!window.confirm(`从知识库移除《${m.name}》的索引？\n\n只删除切块与向量，不会删除磁盘上的 PDF 文件。`)) return
  actingId.value = m.id
  try {
    const r = await pdfApi.remove(m.id)
    ui.toast('success', `已移出知识库（清理 ${r.deleted_chunks} 块，PDF 文件保留）`)
    await refresh()
  } catch (e) {
    ui.toast('error', `移除失败：${(e as Error).message}`)
  } finally {
    actingId.value = 0
  }
}

async function ignoreUpdate(m: PdfManual) {
  actingId.value = m.id
  try {
    await pdfApi.ignoreUpdate(m.id)
    ui.toast('success', `已忽略本次文件变化：${m.name}`)
    await refresh()
  } catch (e) {
    ui.toast('error', `操作失败：${(e as Error).message}`)
  } finally {
    actingId.value = 0
  }
}

// ---- 阅读器 ----------------------------------------------------------------

const viewer = ref<{ id: number; name: string; page: number; indexed?: boolean } | null>(null)
const viewerUrl = computed(() =>
  viewer.value ? pdfApi.fileUrl(viewer.value.id, viewer.value.page) : '',
)

/** 双列模式下右栏展示的当前手册（用于品牌/页数/状态与操作按钮） */
const currentManual = computed<PdfManual | null>(() =>
  viewer.value ? all.value.find((m) => m.id === viewer.value!.id) ?? null : null,
)

/** 已入库手册的章节目录（未入库时为空，右栏只显示阅读器） */
const chapters = ref<{ title_path: string; page: number; chunks: number }[]>([])
const metaLoading = ref(false)

async function loadChapters(id: number) {
  chapters.value = []
  metaLoading.value = true
  try {
    const meta = await pdfApi.meta(id)
    chapters.value = meta.chapters ?? []
  } catch {
    /* 未入库/接口失败时没有目录，不影响阅读 */
  } finally {
    metaLoading.value = false
  }
}

function openManual(m: PdfManual, page = 1) {
  viewer.value = { id: m.id, name: m.name, page, indexed: (m.chunks ?? 0) > 0 }
  peek.value = false // 抽屉里选完就收起来，别挡着刚打开的 PDF
  if (props.split) void loadChapters(m.id)
}

/**
 * 双列模式默认态：进页面就把第一本摊开，右栏不留空白。
 * 三个前置条件缺一不可——只在双列页、只做一次、已有引用参数时让给 openFromQuery。
 */
function autoOpenFirst() {
  if (!props.split || autoOpened.value || viewer.value) return
  if (route.query.manual) return
  // 跳过已失效（磁盘文件被删）的条目，否则自动打开会撞 404
  const first = all.value.find((m) => m.status !== 'invalid')
  if (!first) return
  autoOpened.value = true
  openManual(first, 1)
}

function jumpTo(page: number) {
  if (!viewer.value || page < 1) return
  viewer.value = { ...viewer.value, page }
}

function onChapterChange(e: Event) {
  const el = e.target as HTMLSelectElement
  const i = Number(el.value)
  if (el.value !== '' && !Number.isNaN(i) && chapters.value[i]) jumpTo(chapters.value[i].page)
  el.value = ''
}

function closeViewer() {
  viewer.value = null
  chapters.value = []
  focused.value = false
  peek.value = false
  // 用户主动关掉右栏后，后续 refresh/轮询不再自动摊开第一本
  autoOpened.value = true
  // 从 RAG 引用（?manual=&page=）进来时，关闭右栏同步清掉 query，避免刷新又弹回
  if (route.query.manual) router.replace({ path: '/pdf' })
}

/** 专注阅读：临时收起左栏，把宽度全给 PDF（窄屏看 A4 手册的唯一办法） */
const focused = ref(false)

function toggleFocus() {
  focused.value = !focused.value
}

/**
 * 知识助手展开时会吃掉主区一大块宽度（min 300px），双列挤在一起 PDF 只剩一条。
 * 此时左栏自动让出位置，改成「按需唤出的浮层抽屉」：点顶栏列表按钮滑出，
 * 选完手册自动收起，不长期占宽度。
 */
const ragNarrow = computed(() => props.split && rag.panelOpen)
const peek = ref(false)

function toggleList() {
  // focused 时左栏是 display:none，先退出专注，否则点了看不到抽屉
  if (focused.value) focused.value = false
  peek.value = !peek.value
}

watch(
  () => rag.panelOpen,
  (open) => {
    peek.value = false
    // 展开知识助手后列表改由抽屉承载，专注态没有意义，退出避免抽屉被 display:none 挡住
    if (open) focused.value = false
  },
)

function onKey(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  if (peek.value) peek.value = false
  else if (focused.value) focused.value = false
  else closeViewer()
}

/** RAG 引用跳转入口：/pdf?manual=<id>&page=<n> → 直接打开该手册对应页 */
async function openFromQuery() {
  const mid = Number(route.query.manual)
  if (!mid) return
  const page = Number(route.query.page) || 1
  const found = all.value.find((m) => m.id === mid)
  if (found) {
    openManual(found, page)
    return
  }
  try {
    const meta = await pdfApi.meta(mid)
    viewer.value = { id: mid, name: meta.name, page, indexed: meta.status === 'ready' }
    chapters.value = meta.chapters ?? []
  } catch {
    ui.toast('warn', '引用的手册不存在或已被移除')
  }
}

watch(
  () => route.query,
  () => void openFromQuery(),
)

onMounted(async () => {
  await refresh()
  await openFromQuery()
  try {
    ingest.value = await pdfApi.state()
    if (ingest.value.running) startPolling()
  } catch {
    /* 状态接口失败不影响列表 */
  }
  eventsSocket.on('ingest.progress', onIngestEvent)
  window.addEventListener('keydown', onKey)
})

/**
 * 把「当前打开的手册」交给知识助手：手册页没有笔记上下文，若不报，RagPanel 会一直
 * 显示"未感知到笔记"，提问也只能全库检索——在翻手册时这几乎等于答非所问。
 * 只在双列页（M9）上报；知识库页那份 compact 不打开阅读器，不参与。
 */
watch(
  () => viewer.value,
  (v) => {
    if (!props.split) return
    if (v)
      rag.setContext({
        manualId: v.id,
        manualName: v.name,
        manualPage: v.page,
        manualIndexed: v.indexed,
      })
    else rag.clearContext()
  },
  { immediate: true },
)

onUnmounted(() => {
  stopPolling()
  if (debounceTimer !== null) window.clearTimeout(debounceTimer)
  window.removeEventListener('keydown', onKey)
  // 离开手册页要撤掉手册上下文，否则回到笔记页后范围提示还停在上一本 PDF
  if (props.split) rag.clearContext()
})
</script>

<template>
  <div class="pdf-wrap" :class="{ split: props.split, focused, 'rag-narrow': ragNarrow, peek }">
  <section class="card pdf-card" :class="{ compact: props.compact }">
    <header class="card-head">
      <div class="card-title">
        <h2>PDF 手册</h2>
        <span class="count-pill">{{ total }} 本</span>
      </div>
      <div class="pdf-actions">
        <button class="btn ghost sm" :disabled="rootMissing || loading || actingId !== 0" @click="rescan">
          <Icon name="refresh" :size="14" /><span>重新扫描</span>
        </button>
        <button
          class="btn primary sm"
          :disabled="rootMissing || !pendingIds.length || actingId !== 0"
          :title="pendingIds.length ? `把 ${pendingIds.length} 本未入库 PDF 排入队列` : '没有待入库的 PDF'"
          @click="ingestAll"
        >
          <Icon name="database" :size="14" /><span>全部入库</span>
        </button>
      </div>
    </header>

    <!-- 未配置 pdf_root -->
    <div v-if="rootMissing" class="pdf-hint">
      <Icon name="folder" :size="18" />
      <div>
        <strong>还没有配置 PDF 根目录</strong>
        <p>指定一个放手册的文件夹，这里会列出全部 PDF 并支持按章节解析入库（不会复制文件）。</p>
      </div>
      <button class="btn sm" @click="router.push('/settings')">去设置</button>
    </div>

    <template v-else>
      <div class="pdf-toolbar">
        <div class="search-field">
          <Icon name="search" :size="15" />
          <input v-model="keyword" placeholder="搜索手册名、路径或品牌…" autocomplete="off" />
          <button v-if="keyword" class="search-clear" title="清空" @click="keyword = ''">
            <Icon name="x" :size="12" />
          </button>
        </div>
        <div class="seg sm">
          <button
            v-for="f in FILTERS"
            :key="f.key"
            class="seg-btn"
            :class="{ active: filter === f.key }"
            @click="filter = f.key"
          >
            {{ f.label }}<em>{{ counts[f.key] }}</em>
          </button>
        </div>
      </div>

      <!-- 入库进度 -->
      <div v-if="ingest.running || justFinished" class="pdf-progress">
        <div class="pp-head">
          <span class="pp-label">
            <Icon name="refresh" :size="13" />
            {{ ingest.running ? STAGE_LABEL[ingest.stage] || '解析入库' : '入库完成' }}
          </span>
          <span class="pp-name" :title="ingest.name">{{ ingest.name || '等待任务…' }}</span>
          <span class="pp-num">{{ ingest.running ? `${pct}%` : '100%' }}</span>
        </div>
        <div class="pp-bar"><i :style="{ width: `${pct}%` }" :class="{ done: !ingest.running }" /></div>
      </div>

      <div class="pdf-table-head">
        <span>手册</span><span>状态</span><span>块数</span><span></span>
      </div>

      <div class="pdf-list" :class="{ loading }">
        <template v-for="g in visibleGroups" :key="g.name">
          <button
            class="pdf-group"
            type="button"
            :aria-expanded="!collapsed.has(g.name)"
            @click="toggleGroup(g.name)"
          >
            <Icon name="chevronRight" :size="12" class="pg-chev" :class="{ folded: collapsed.has(g.name) }" />
            <Icon name="folder" :size="12" />
            <span>{{ g.name }}</span>
            <em>{{ g.items.length }}</em>
          </button>

          <template v-if="!collapsed.has(g.name)">
            <div
              v-for="m in g.items"
              :key="m.path"
              class="pdf-row"
              :class="{ off: m.status === 'invalid', active: viewer?.id === m.id }"
              @click="openManual(m)"
            >
              <div class="pr-main">
                <div class="pr-title">
                  <span class="pr-name" :title="m.path">{{ m.name }}</span>
                  <span v-if="m.brand" class="badge brand-tag">{{ m.brand }}</span>
                  <span v-if="m.status === 'update'" class="pr-flag">文件有变化</span>
                </div>
                <div class="pr-sub">
                  {{ m.rel_path }} · {{ m.pages ?? '?' }} 页 · {{ fmtSize(m.size) }}
                  <template v-if="props.split"> · {{ m.chunks || 0 }} 块</template>
                  <template v-if="m.error"> · <span class="pr-err" :title="m.error">{{ m.error }}</span></template>
                </div>
              </div>

              <div class="pr-status">
                <span
                  class="pdf-badge"
                  :class="statusOf(m).cls"
                  :title="m.error || statusOf(m).label"
                >
                  {{ statusOf(m).label }}
                </span>
              </div>

              <div class="pr-chunks">{{ m.chunks || '—' }}</div>

              <div class="pr-acts" @click.stop>
                <button
                  v-if="m.status === 'discovered' || (m.status === 'failed' && !m.chunks)"
                  class="pa-act"
                  title="加入知识库（解析 + 切块 + 向量化）"
                  :disabled="actingId === m.id"
                  @click="ingestOne(m)"
                >
                  <Icon name="plusCircle" :size="13" />
                </button>
                <button class="pa-act" title="打开阅读" @click="openManual(m)">
                  <Icon name="eye" :size="13" />
                </button>
                <button
                  v-if="m.status === 'update'"
                  class="pa-act"
                  title="忽略本次文件变化（保持现有索引）"
                  :disabled="actingId === m.id"
                  @click="ignoreUpdate(m)"
                >
                  <Icon name="checkCircle" :size="13" />
                </button>
                <button
                  v-if="m.chunks"
                  class="pa-act"
                  title="重建索引（旧索引保留到替换完成）"
                  :disabled="actingId === m.id"
                  @click="rebuild(m)"
                >
                  <Icon name="refresh" :size="13" />
                </button>
                <button
                  v-if="m.chunks"
                  class="pa-act remove"
                  title="从知识库移除（只删索引，不删 PDF）"
                  :disabled="actingId === m.id"
                  @click="remove(m)"
                >
                  <Icon name="trash" :size="13" />
                </button>
              </div>
            </div>
          </template>
        </template>

        <div v-if="!filtered.length" class="pdf-empty">
          <Icon name="file" :size="22" />
          <p v-if="!total">这个目录里还没有 PDF</p>
          <p v-else-if="keyword || filter">没有符合条件的手册</p>
          <p v-else>暂无手册</p>
        </div>
      </div>

      <!-- 手册少时用一块提示占位，避免整页留白显得像没加载出来 -->
      <div v-if="total && total <= 8 && !keyword && !filter" class="pdf-tip">
        <Icon name="folder" :size="16" />
        <p>把新的 PDF 放进根目录后点「重新扫描」即可登记；入库后可按章节与页码被问答引用。</p>
      </div>

      <div v-if="loadError" class="pdf-error"><Icon name="alert" :size="13" />{{ loadError }}</div>

      <footer class="card-foot">
        <span class="foot-dot" />PDF 只记录原路径不复制 · 解析与切块在本地完成，仅切块文本送云端向量化
        <span v-if="rootPath" class="pdf-root" :title="rootPath">{{ rootPath }}</span>
      </footer>
    </template>

    <!-- 阅读器（C2 简化版：原生 PDF 视图 + 页码跳转） -->
    <div v-if="viewer && !props.split" class="pdf-viewer" @click.self="closeViewer">
      <div class="pv-shell">
        <header class="pv-head">
          <Icon name="book" :size="14" />
          <strong :title="viewer.name">{{ viewer.name }}</strong>
          <span class="pv-page">第 {{ viewer.page }} 页</span>
          <button class="pv-close" title="关闭（Esc）" @click="closeViewer">
            <Icon name="x" :size="14" />
          </button>
        </header>
        <iframe :src="viewerUrl" title="PDF 预览" />
      </div>
    </div>
  </section>

  <!-- ==================== 右栏：手册阅读（双列模式） ==================== -->
  <section v-if="props.split" class="pdf-pane" :class="{ 'has-doc': !!viewer }">
    <template v-if="viewer">
      <header class="pane-head">
        <button class="pane-close" title="关闭右栏（Esc）" @click="closeViewer">
          <Icon name="x" :size="15" />
        </button>
        <div class="pane-title">
          <strong :title="viewer.name">{{ viewer.name }}</strong>
          <span class="pane-sub">
            <span v-if="currentManual?.brand" class="badge brand-tag">{{ currentManual.brand }}</span>
            <span>{{ currentManual?.pages ?? '?' }} 页</span>
            <span v-if="currentManual?.chunks">· {{ currentManual.chunks }} 块</span>
            <span>· 第 {{ viewer.page }} 页</span>
          </span>
        </div>
        <div v-if="currentManual" class="pane-acts" @click.stop>
          <button
            v-if="currentManual.status === 'discovered' || (currentManual.status === 'failed' && !currentManual.chunks)"
            class="pane-act"
            title="加入知识库（解析 + 切块 + 向量化）"
            :disabled="actingId === currentManual.id"
            @click="ingestOne(currentManual)"
          >
            <Icon name="plusCircle" :size="14" />
          </button>
          <button
            v-if="currentManual.status === 'update'"
            class="pane-act"
            title="忽略本次文件变化（保持现有索引）"
            :disabled="actingId === currentManual.id"
            @click="ignoreUpdate(currentManual)"
          >
            <Icon name="checkCircle" :size="14" />
          </button>
          <button
            v-if="currentManual.chunks"
            class="pane-act"
            title="重建索引（旧索引保留到替换完成）"
            :disabled="actingId === currentManual.id"
            @click="rebuild(currentManual)"
          >
            <Icon name="refresh" :size="14" />
          </button>
          <button
            v-if="currentManual.chunks"
            class="pane-act remove"
            title="从知识库移除（只删索引，不删 PDF）"
            :disabled="actingId === currentManual.id"
            @click="remove(currentManual)"
          >
            <Icon name="trash" :size="14" />
          </button>
          <a
            class="pane-act"
            :href="pdfApi.fileUrl(viewer.id, viewer.page)"
            target="_blank"
            rel="noopener"
            title="在新窗口打开（可用浏览器自带的缩放/打印）"
          >
            <Icon name="external" :size="14" />
          </a>
          <button
            v-if="ragNarrow"
            class="pane-act"
            :class="{ on: peek }"
            :title="peek ? '收起手册列表（Esc）' : '显示手册列表'"
            @click="toggleList"
          >
            <Icon name="notes" :size="14" />
          </button>
          <button
            v-else
            class="pane-act"
            :class="{ on: focused }"
            :title="focused ? '退出专注阅读（Esc）' : '专注阅读（收起左栏，PDF 铺满）'"
            @click="toggleFocus"
          >
            <Icon name="columns" :size="14" />
          </button>
        </div>
      </header>

      <div v-if="chapters.length" class="pane-chapters">
        <Icon name="layers" :size="13" />
        <select class="pane-select" @change="onChapterChange">
          <option value="">跳转到章节（{{ chapters.length }} 节）…</option>
          <option v-for="(c, i) in chapters" :key="i" :value="i">
            {{ c.title_path }} · p{{ c.page }}
          </option>
        </select>
        <span class="pane-chapter-hint">来自入库切块，点选直接跳页</span>
      </div>

      <iframe :key="viewerUrl" :src="viewerUrl" title="PDF 预览" class="pane-frame" />
    </template>

    <div v-else class="pane-empty">
      <div class="pe-ic"><Icon name="book" :size="30" /></div>
      <p>选一本手册开始阅读</p>
      <span class="pe-sub">
        左侧点任意一本即可在这里打开，{{ rootPath ? '路径：' + rootPath : '全文不离开本页' }}<br />
        已入库的手册还能按章节跳页，问答复用同一份页码引用
      </span>
    </div>
  </section>
  </div>
</template>

<style scoped>
/* ==================== 双列骨架（知识库页用 compact 单列，不受影响） ==================== */
.pdf-wrap { min-width: 0; }
.pdf-wrap:not(.split) { display: block; }
.pdf-wrap.split {
  display: flex;
  flex-direction: row;
  align-items: stretch;
  gap: 11px;
  height: 100%;
  min-height: 0;
  position: relative;
  container-type: inline-size;
  container-name: pdfpage;
}
.pdf-wrap.split > .pdf-card {
  width: clamp(268px, 34%, 430px);
  flex: none;
  min-height: 0;
}
/* 知识助手展开 → 左栏自动让位，改成按需滑出的浮层抽屉（不挤压 PDF 宽度）。
   抽屉默认移出可视区，点顶栏列表按钮滑入；选完手册自动收起。 */
.pdf-wrap.split.rag-narrow > .pdf-card {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  z-index: 6;
  width: min(320px, 88%);
  box-shadow: 0 8px 28px rgba(24, 22, 18, 0.18);
  transform: translateX(calc(-100% - 12px));
  opacity: 0;
  pointer-events: none;
  transition: transform 0.18s ease, opacity 0.18s ease;
}
.pdf-wrap.split.rag-narrow.peek > .pdf-card {
  transform: none;
  opacity: 1;
  pointer-events: auto;
}
/* 专注阅读：收起列表，PDF 独占整行（放最后，优先级压过抽屉） */
.pdf-wrap.split.focused > .pdf-card { display: none; }

.pdf-card { display: flex; flex-direction: column; min-height: 0; }
.pdf-actions { display: flex; gap: 6px; flex: none; }

.pdf-hint {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 18px 14px;
  padding: 12px 14px;
  border: 1px dashed var(--line);
  border-radius: 9px;
  background: #fbfaf6;
  color: var(--muted);
}
.pdf-hint > svg { color: var(--blue); opacity: 0.65; flex: none; }
.pdf-hint div { min-width: 0; }
.pdf-hint strong { display: block; font-size: 12.5px; color: var(--ink); font-weight: 600; }
.pdf-hint p { margin: 2px 0 0; font-size: 11px; line-height: 1.5; }
.pdf-hint .btn { margin-left: auto; flex: none; }

.pdf-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 18px 10px;
  flex-wrap: wrap;
}
.pdf-toolbar .search-field { flex: 1 1 220px; min-width: 180px; }
.pdf-toolbar .seg { flex: none; }

.pdf-progress { padding: 0 18px 10px; }
.pp-head { display: flex; align-items: center; gap: 8px; font-size: 11px; margin-bottom: 5px; }
.pp-label { display: inline-flex; align-items: center; gap: 5px; color: var(--blue-ink); font-weight: 600; white-space: nowrap; }
.pp-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
}
.pp-num { font-variant-numeric: tabular-nums; color: var(--muted); }
.pp-bar { height: 4px; border-radius: 2px; background: var(--line-soft); overflow: hidden; }
.pp-bar i {
  display: block;
  height: 100%;
  background: var(--blue);
  transition: width 0.35s ease;
}
.pp-bar i.done { background: var(--green); }

.pdf-table-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 76px 56px 108px;
  gap: 10px;
  padding: 6px 18px 6px;
  border-top: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  background: #faf9f6;
  font-size: 10px;
  letter-spacing: 0.3px;
  color: var(--muted);
}
.pdf-table-head span:nth-child(n + 2) { text-align: center; }

.pdf-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
/* 嵌在知识库页里的紧凑模式：列表限高，避免手册多时把页面拉得很长 */
.pdf-card.compact .pdf-list { max-height: 430px; }
.pdf-list.loading { opacity: 0.6; }

.pdf-group {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 7px 18px 5px;
  border: 0;
  border-bottom: 1px solid #eff5fd;
  background: #f4f8fe;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--blue-ink);
  text-align: left;
  cursor: pointer;
}
.pdf-group:hover { background: #eef5fe; }
.pdf-group svg { color: var(--blue); opacity: 0.72; flex: none; }
.pdf-group em {
  font-style: normal;
  margin-left: auto;
  font-weight: 500;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.pg-chev { transition: transform 0.15s ease; transform: rotate(90deg); }
.pg-chev.folded { transform: rotate(0deg); }

.pdf-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 76px 56px 108px;
  gap: 10px;
  align-items: center;
  padding: 8px 18px;
  border-bottom: 1px solid #f6f4ef;
}
.pdf-row:hover { background: #fbfaf7; }
.pdf-row.off { opacity: 0.62; }

/* ---- 双列：行变可点导航，操作按钮收进右栏 ---- */
.pdf-wrap.split .pdf-row { cursor: pointer; padding-left: 12px; padding-right: 12px; }
.pdf-wrap.split .pdf-table-head,
.pdf-wrap.split .pdf-row { grid-template-columns: minmax(0, 1fr) 66px; gap: 8px; }
.pdf-wrap.split .pdf-table-head { padding-left: 12px; padding-right: 12px; }
.pdf-wrap.split .pdf-table-head span:nth-child(3),
.pdf-wrap.split .pdf-table-head span:nth-child(4),
.pdf-wrap.split .pr-chunks,
.pdf-wrap.split .pr-acts { display: none; }
.pdf-wrap.split .pdf-row.active {
  background: var(--blue-soft);
  box-shadow: inset 2px 0 0 var(--blue);
}
.pdf-wrap.split .pdf-row.active .pr-name { color: var(--blue-ink); }
.pdf-wrap.split .pdf-group { padding-left: 14px; padding-right: 14px; }

/* 窄列里筛选条竖排，五个筛选项均分宽度，避免被裁切 */
.pdf-wrap.split .pdf-toolbar {
  flex-direction: column;
  align-items: stretch;
  gap: 7px;
  padding-left: 14px;
  padding-right: 14px;
}
.pdf-wrap.split .pdf-toolbar .search-field { flex: 1 1 auto; }
.pdf-wrap.split .seg { display: flex; width: 100%; }
.pdf-wrap.split .seg-btn {
  flex: 1 1 0;
  min-width: 0;
  height: 25px;
  padding: 0 2px;
  font-size: 10px;
  justify-content: center;
}
.pdf-wrap.split .seg-btn em { font-size: 9px; margin-left: 2px; }
.pdf-wrap.split .pdf-progress { padding-left: 14px; padding-right: 14px; }
/* 提示与根路径在窄列里只会挤压列表，隐藏 */
.pdf-wrap.split .pdf-tip,
.pdf-wrap.split .pdf-root { display: none; }

.pr-main { min-width: 0; }
.pr-title { display: flex; align-items: center; gap: 6px; min-width: 0; }
.pr-name {
  font-size: 12.5px;
  font-weight: 550;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.pr-name:hover { color: var(--blue-ink); text-decoration: underline; }
.pr-flag {
  flex: none;
  font-size: 9.5px;
  padding: 1px 4px;
  border-radius: 3px;
  color: var(--amber-ink);
  background: var(--amber-soft);
  border: 1px solid var(--amber-line);
}
.pr-sub {
  margin-top: 2px;
  font-size: 10px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pr-err { color: #b4483f; }
.pr-status { text-align: center; }
.pdf-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 5px;
  border: 1px solid var(--line-soft);
  white-space: nowrap;
}
.pdf-badge.ok { background: var(--green-soft); color: var(--green-ink); border-color: var(--green-line); }
.pdf-badge.warn { background: var(--amber-soft); color: var(--amber-ink); border-color: var(--amber-line); }
.pdf-badge.bad { background: var(--red-soft); color: var(--red); border-color: var(--red-line); }
.pdf-badge.muted { background: #f4f4f1; color: var(--muted); border-color: var(--line-soft); }
.pr-chunks {
  text-align: center;
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.pr-acts { display: flex; justify-content: flex-end; gap: 2px; }
.pa-act {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  color: #b9b5ab;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}
.pa-act:disabled { opacity: 0.45; cursor: default; }
.pdf-row:hover .pa-act { color: var(--blue-ink); }
.pa-act:hover { background: var(--blue-soft); border-color: var(--blue-line); color: var(--blue-ink); }
.pa-act.remove:hover { background: var(--red-soft); border-color: var(--red-line); color: var(--red); }

.pdf-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 26px 18px;
  color: var(--muted);
  font-size: 11.5px;
}
.pdf-empty svg { opacity: 0.4; }
.pdf-empty p { margin: 0; }

.pdf-tip {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 18px;
  padding: 11px 14px;
  border: 1px dashed var(--line);
  border-radius: 9px;
  background: #fcfbf8;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
}
.pdf-tip svg { flex: none; opacity: 0.45; }
.pdf-tip p { margin: 0; }

.pdf-error { display: flex; align-items: center; gap: 6px; padding: 8px 18px; font-size: 11px; color: #b4483f; }

.card-foot { position: relative; }
.pdf-root {
  margin-left: auto;
  max-width: 42%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #b6b2a8;
  font-size: 9.5px;
}

/* ---------------- 阅读器 ---------------- */
.pdf-viewer {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px;
  background: rgba(28, 26, 22, 0.42);
  backdrop-filter: blur(2px);
}
.pv-shell {
  display: flex;
  flex-direction: column;
  width: min(980px, 100%);
  height: min(88vh, 900px);
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.18);
  overflow: hidden;
}
.pv-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--line-soft);
  background: #fbfaf7;
}
.pv-head strong {
  font-size: 12.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pv-page { margin-left: auto; font-size: 10.5px; color: var(--muted); }
.pv-close {
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 6px;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  color: var(--muted);
  cursor: pointer;
}
.pv-close:hover { color: var(--ink); border-color: var(--line-strong, #ddd8cd); }
.pv-shell iframe { flex: 1 1 auto; width: 100%; border: 0; background: #525659; }

/* ==================== 右栏阅读面板 ==================== */
.pdf-pane {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.pane-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 11px;
  flex: none;
  border-bottom: 1px solid var(--line-soft);
  background: #fbfaf7;
}
.pane-close {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  flex: none;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--muted);
  cursor: pointer;
}
.pane-close:hover { color: var(--ink); border-color: var(--line-strong, #ddd8cd); }
.pane-title { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 auto; }
.pane-title strong {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pane-sub {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--muted);
  overflow: hidden;
  white-space: nowrap;
}
.pane-acts { display: flex; align-items: center; gap: 2px; flex: none; }
.pane-act {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  color: #b9b5ab;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}
.pane-act:hover { background: var(--blue-soft); border-color: var(--blue-line); color: var(--blue-ink); }
.pane-act.on { background: var(--blue-soft); border-color: var(--blue-line); color: var(--blue-ink); }
.pane-act.remove:hover { background: var(--red-soft); border-color: var(--red-line); color: var(--red); }
.pane-act:disabled { opacity: 0.45; cursor: default; }

.pane-chapters {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 11px;
  flex: none;
  border-bottom: 1px solid var(--line-soft);
  background: #f8f7f3;
  color: var(--muted);
}
.pane-select {
  flex: 1 1 auto;
  min-width: 0;
  height: 26px;
  padding: 0 6px;
  font-size: 11px;
  color: var(--ink-2);
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 6px;
  cursor: pointer;
}
.pane-chapter-hint { font-size: 10px; color: #b6b2a8; flex: none; }

.pane-frame {
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
  border: 0;
  /* 与 Chrome PDF 画布同色，加载期间不会闪一下白 */
  background: #525659;
}

.pane-empty {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  text-align: center;
  color: var(--muted);
}
.pane-empty .pe-ic { opacity: 0.3; }
.pane-empty p { margin: 0; font-size: 13px; font-weight: 550; color: var(--ink-2); }
.pane-empty .pe-sub { font-size: 11px; line-height: 1.7; }

/* ---------------- 打开文档后：整体转为深色阅读器 ----------------
   Chrome 内置 PDF 查看器的画布固定是深色，页面 CSS 改不动。
   与其在旁边留一块突兀的黑，不如把右栏整体做成深色阅读区：
   顶栏、章节条、边框一起变深，黑底就成了「看文档的地方」而不是「破洞」。 */
.pdf-pane.has-doc {
  background: #2b2b2d;
  border-color: #3a3a3c;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.16);
}
/* 注：Chrome PDF 查看器内部那圈浅色滚动条属于 iframe 自己的文档，父页面既改不了
   颜色也关不掉。曾试过裁边 + 同色遮罩，代价是要盖掉底部 15px 正文，得不偿失，
   已撤回——滚动条就留着，内容完整优先。 */
.pdf-pane.has-doc .pane-head {
  background: #323234;
  border-bottom-color: #3f3f42;
}
.pdf-pane.has-doc .pane-title strong { color: #ecebe7; }
.pdf-pane.has-doc .pane-sub { color: #a3a19b; }
.pdf-pane.has-doc .badge.brand-tag {
  background: rgba(120, 170, 240, 0.16);
  color: #9dc3f0;
  border-color: rgba(120, 170, 240, 0.32);
}
.pdf-pane.has-doc .pane-close {
  background: #3a3a3c;
  border-color: #4a4a4d;
  color: #b9b6ae;
}
.pdf-pane.has-doc .pane-close:hover { color: #f2f0ea; border-color: #5e5e62; }
.pdf-pane.has-doc .pane-act { color: #8e8b84; }
.pdf-pane.has-doc .pane-act:hover {
  background: #3f3f42;
  border-color: #55555a;
  color: #ecebe7;
}
.pdf-pane.has-doc .pane-act.on {
  background: rgba(120, 170, 240, 0.18);
  border-color: rgba(120, 170, 240, 0.35);
  color: #b6d2f2;
}
.pdf-pane.has-doc .pane-act.remove:hover {
  background: rgba(226, 75, 74, 0.18);
  border-color: rgba(226, 75, 74, 0.42);
  color: #f09595;
}
.pdf-pane.has-doc .pane-chapters {
  background: #2f2f31;
  border-bottom-color: #3f3f42;
  color: #8e8b84;
}
.pdf-pane.has-doc .pane-select {
  background: #3a3a3c;
  border-color: #4a4a4d;
  color: #d6d3cb;
}
.pdf-pane.has-doc .pane-chapter-hint { color: #74716b; }

@container pdfpage (max-width: 730px) {
  .pdf-wrap.split .pr-sub { font-size: 9.5px; }
  .pane-chapter-hint { display: none; }
}
/* split 模式列表底栏信息密度高，缩小一档避免三行换行 */
.pdf-wrap.split .card-foot { padding: 8px 12px; font-size: 10px; gap: 5px; }

@media (max-width: 900px) {
  .pdf-table-head { display: none; }
  .pdf-row { grid-template-columns: minmax(0, 1fr) auto; }
  .pr-chunks { display: none; }
}
</style>
