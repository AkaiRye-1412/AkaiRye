<script setup lang="ts">
/**
 * M3 知识库（FR-RAG-01~03 / Q-01 / Q-05）
 * - 索引概览：已索引/待索引/切块/向量模型 + 后台任务进度条（embedding 节流，必须异步看进度）；
 * - 笔记索引状态：按来源分组、搜索、状态筛选、单篇重建、单篇移出/恢复、补齐缺失、全量重建。
 * 索引范围（白名单）的配置入口只在设置页，本页不再重复编辑。
 * 「移出知识库」= 删块 + 记入 excluded 名单，**不删除磁盘文件**，之后可一键恢复。
 *
 * 数据流：REST 全量对账为主，WS index.updated 只做"触发刷新"（推送不作唯一来源）。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { eventsSocket, kbApi, type IndexProgress, type KbNote, type KbStats } from '@/api'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'

const ui = useUiStore()

const stats = ref<KbStats | null>(null)
const notes = ref<KbNote[]>([])

const keyword = ref('')
const statusFilter = ref<'' | 'ready' | 'pending' | 'failed' | 'excluded'>('')
const loadError = ref('')
const busy = ref(false)
const loadNotes = ref(false)
/** 正在移出/恢复的笔记路径（行内按钮 loading 态，避免重复点击） */
const actingPath = ref('')
/** 任务刚从 running 变完成时，进度条留 1.5s 展示结果，避免瞬间消失 */
const justFinished = ref(false)

const prog = computed<IndexProgress>(
  () =>
    stats.value?.indexing ?? {
      running: false,
      mode: '',
      total: 0,
      done: 0,
      failed: 0,
      current: '',
      started_at: null,
      finished_at: null,
    },
)

const pct = computed(() => {
  const t = prog.value.total
  if (!t) return prog.value.running ? 8 : 0
  return Math.min(100, Math.round((prog.value.done / t) * 100))
})

const counts = computed(
  () => stats.value?.notes_by_status ?? { ready: 0, pending: 0, failed: 0, excluded: 0, unindexed: 0 },
)

/** 过滤后的笔记（后端已按状态/关键词粗筛，这里再按本地输入精确过滤一次） */
const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return notes.value
  return notes.value.filter((n) =>
    [n.display_name, n.note_title, n.customer, n.project_name, n.group]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(kw)),
  )
})

interface Group {
  name: string
  items: KbNote[]
}

/** 按来源分组：售后卡片在前，其余按分组名排序 */
const groups = computed<Group[]>(() => {
  const map = new Map<string, KbNote[]>()
  for (const n of filtered.value) {
    const key = n.group || '其他'
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(n)
  }
  return [...map.entries()]
    .map(([name, items]) => ({ name, items }))
    .sort((a, b) => (a.name === '售后卡片' ? -1 : b.name === '售后卡片' ? 1 : a.name.localeCompare(b.name)))
})

const filters: { key: '' | 'ready' | 'pending' | 'failed' | 'excluded'; label: string; n: number }[] = [
  { key: '', label: '全部', n: 0 },
  { key: 'ready', label: '已索引', n: 0 },
  { key: 'pending', label: '待索引', n: 0 },
  { key: 'failed', label: '失败', n: 0 },
  { key: 'excluded', label: '已移出', n: 0 },
]

const filterCounts = computed(() => ({
  '': stats.value?.notes ?? 0,
  ready: counts.value.ready,
  pending: counts.value.pending + counts.value.unindexed,
  failed: counts.value.failed,
  excluded: counts.value.excluded,
}))

const STATUS: Record<string, { label: string; cls: string }> = {
  ready: { label: '已索引', cls: 'ok' },
  pending: { label: '待索引', cls: 'warn' },
  failed: { label: '失败', cls: 'bad' },
  excluded: { label: '已移出', cls: 'muted' },
}

function statusOf(n: KbNote) {
  return STATUS[n.indexed_status ?? ''] ?? { label: '未索引', cls: 'muted' }
}

/** 标题行：正文 H1 > 文件名（去掉 .md，日期卡片转 2026-08-25） */
function titleOf(n: KbNote): string {
  if (n.note_title) return n.note_title
  const base = n.display_name.split('/').pop() ?? n.display_name
  return base.replace(/\.md$/, '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (x: number) => String(x).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

const MODE_LABEL: Record<string, string> = { autofill: '补齐缺失', full: '全量重建' }

// ---- 数据加载 --------------------------------------------------------------

async function refreshStats() {
  try {
    stats.value = await kbApi.stats()
    loadError.value = ''
  } catch (e) {
    loadError.value = (e as Error).message
  }
}

async function refreshNotes() {
  loadNotes.value = true
  try {
    const q: string[] = []
    if (statusFilter.value) q.push(`indexed_status=${statusFilter.value}`)
    notes.value = await kbApi.notes(q.length ? `?${q.join('&')}` : '')
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loadNotes.value = false
  }
}

async function refreshAll() {
  await Promise.all([refreshStats(), refreshNotes()])
}

// ---- 轮询：任务运行中时刷进度 ------------------------------------------------

let pollTimer: number | null = null
let unsub: (() => void) | null = null
let debounceTimer: number | null = null

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling() {
  if (pollTimer !== null) return
  pollTimer = window.setInterval(async () => {
    await refreshStats()
    // 当前文件变化时列表块数也在变，低频同步一次
    if (prog.value.done % 5 === 0) void refreshNotes()
  }, 1500)
}

/** WS 事件密集（每篇一次），合并成一次刷新 */
function scheduleRefresh() {
  if (debounceTimer !== null) window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => {
    debounceTimer = null
    void refreshStats()
    void refreshNotes()
  }, 700)
}

watch(
  () => prog.value.running,
  (running, was) => {
    if (running) {
      justFinished.value = false
      startPolling()
    } else {
      stopPolling()
      if (was) {
        justFinished.value = true
        window.setTimeout(() => (justFinished.value = false), 1600)
        void refreshNotes()
        const p = prog.value
        ui.toast(
          p.failed > 0 ? 'warn' : 'success',
          `${MODE_LABEL[p.mode] ?? '索引任务'}完成：${p.done - p.failed}/${p.total} 篇${p.failed ? `，${p.failed} 篇失败` : ''}`,
        )
      }
    }
  },
)

// ---- 动作 ------------------------------------------------------------------

async function reindexAll() {
  busy.value = true
  try {
    const r = await kbApi.reindexAll({ force: true, background: true })
    if (!r.started) ui.toast('warn', r.reason || '已有索引任务进行中')
    else {
      ui.toast('info', '已开始全量重建，进度见上方进度条')
      await refreshStats()
    }
  } catch (e) {
    ui.toast('error', `全量重建失败：${(e as Error).message}`)
  } finally {
    busy.value = false
  }
}

async function autofill() {
  busy.value = true
  try {
    const r = await kbApi.autofill()
    if (!r.started) ui.toast('info', r.reason || '没有待补齐的笔记')
    else {
      ui.toast('info', `已排入 ${r.pending} 篇待索引笔记`)
      await refreshStats()
    }
  } catch (e) {
    ui.toast('error', `补齐失败：${(e as Error).message}`)
  } finally {
    busy.value = false
  }
}

async function reindexOne(n: KbNote) {
  if (n.indexed_status === 'excluded') {
    ui.toast('info', '该篇已移出知识库，先「恢复」再重建')
    return
  }
  try {
    const r = await kbApi.reindexNote(n.path)
    ui.toast('success', `已重建：${r.chunks} 块`)
    await Promise.all([refreshNotes(), refreshStats()])
  } catch (e) {
    ui.toast('error', `重建失败：${(e as Error).message}`)
  }
}

/**
 * 单篇移出 / 恢复知识库（不动磁盘文件）。
 * 移出：删块 + 进 excluded 名单，此后自动补齐不会把它拉回；恢复：立即重建该篇。
 */
async function toggleExcluded(n: KbNote) {
  const next = n.indexed_status !== 'excluded'
  actingPath.value = n.path
  try {
    const r = await kbApi.excludeNote(n.path, next)
    if (next) {
      ui.toast('success', `已移出知识库：${titleOf(n)}（笔记文件未删除）`)
    } else if (r.status === 'ready' || r.status === 'skipped') {
      ui.toast('success', `已恢复并重新索引：${r.chunks} 块`)
    } else if (!r.in_whitelist) {
      ui.toast('warn', '该篇所在目录不在索引范围内，需先在设置页把目录加回白名单')
    } else {
      ui.toast('warn', `恢复失败：${r.reason || r.status}`)
    }
    await Promise.all([refreshNotes(), refreshStats()])
  } catch (e) {
    ui.toast('error', `操作失败：${(e as Error).message}`)
  } finally {
    actingPath.value = ''
  }
}

onMounted(async () => {
  await refreshAll()
  if (prog.value.running) startPolling()
  unsub = eventsSocket.on('index.updated', () => scheduleRefresh())
})

onUnmounted(() => {
  unsub?.()
  stopPolling()
  if (debounceTimer !== null) window.clearTimeout(debounceTimer)
})
</script>

<template>
  <div class="page page-kb">
    <!-- ==================== 概览 ==================== -->
    <section class="card kb-overview">
      <header class="card-head">
        <div class="card-title">
          <h2>知识库</h2>
          <span class="count-pill">{{ stats?.notes ?? 0 }} 篇</span>
        </div>
        <div class="kb-actions">
          <button class="btn ghost sm" :disabled="busy || prog.running" @click="autofill">
            <Icon name="refresh" :size="14" /><span>补齐缺失</span>
          </button>
          <button class="btn primary sm" :disabled="busy || prog.running" @click="reindexAll">
            <Icon name="database" :size="14" /><span>{{ prog.running ? '索引中…' : '全量重建' }}</span>
          </button>
        </div>
      </header>

      <div class="kb-metrics">
        <div class="metric">
          <b>{{ counts.ready }}</b>
          <span>已索引</span>
          <em>共 {{ stats?.notes ?? 0 }} 篇</em>
        </div>
        <div class="metric" :class="{ warn: counts.pending + counts.unindexed > 0 }">
          <b>{{ counts.pending + counts.unindexed }}</b>
          <span>待索引</span>
          <em>{{ counts.failed ? `${counts.failed} 篇失败` : '可自动补齐' }}</em>
        </div>
        <div class="metric">
          <b>{{ stats?.chunks ?? 0 }}</b>
          <span>切块</span>
          <em>{{ stats?.manuals ? `含 ${stats.manuals} 本手册` : '检索最小单元' }}</em>
        </div>
        <div class="metric">
          <b class="model">{{ stats?.vector_model.model_id || '未配置' }}</b>
          <span>向量模型{{ stats?.vector_model.dim ? ` · ${stats.vector_model.dim} 维` : '' }}</span>
          <em v-if="stats?.vector_model.model_id">最近索引 {{ fmtTime(stats?.last_indexed_at ?? null) }}</em>
          <em v-else>未配置时仅关键词检索</em>
        </div>
      </div>

      <!-- 索引进度（后台任务） -->
      <div v-if="prog.running || justFinished" class="kb-progress">
        <div class="kp-head">
          <span class="kp-label">
            <Icon name="refresh" :size="13" />
            {{ MODE_LABEL[prog.mode] || '索引任务' }}
            <template v-if="prog.running">进行中</template>
            <template v-else>已完成</template>
          </span>
          <span class="kp-num">{{ prog.done }} / {{ prog.total }}</span>
        </div>
        <div class="kp-bar"><i :style="{ width: `${prog.running ? pct : 100}%` }" :class="{ done: !prog.running }" /></div>
        <div class="kp-foot">
          <span class="kp-cur">{{ prog.running ? prog.current || '准备中…' : `耗时至 ${fmtTime(prog.finished_at)}` }}</span>
          <span v-if="prog.failed" class="kp-fail">{{ prog.failed }} 篇失败</span>
        </div>
      </div>

      <div v-if="loadError" class="kb-error">
        <Icon name="alert" :size="14" />{{ loadError }}
      </div>
    </section>

    <!-- ==================== 笔记索引状态 ==================== -->
    <section class="card kb-notes">
      <header class="card-head">
        <div class="card-title">
          <h2>笔记索引</h2>
          <span class="count-pill">{{ filtered.length }}</span>
        </div>
        <div class="kb-tools">
          <div class="search-field">
            <Icon name="search" :size="15" />
            <input v-model="keyword" placeholder="搜索标题、客户或路径…" autocomplete="off" />
            <button v-if="keyword" class="search-clear" title="清空" @click="keyword = ''">
              <Icon name="x" :size="12" />
            </button>
          </div>
          <div class="seg sm">
            <button
              v-for="f in filters"
              :key="f.key"
              class="seg-btn"
              :class="{ active: statusFilter === f.key }"
              @click="statusFilter = f.key; refreshNotes()"
            >
              {{ f.label }}<em>{{ filterCounts[f.key] }}</em>
            </button>
          </div>
        </div>
      </header>

      <div class="kb-table-head">
        <span>笔记</span><span>状态</span><span>块数</span><span>最近更新</span><span></span>
      </div>

      <div class="kb-list" :class="{ loading: loadNotes }">
        <template v-for="g in groups" :key="g.name">
          <div class="kb-group">
            <Icon name="folder" :size="12" />
            <span>{{ g.name }}</span>
            <em>{{ g.items.length }}</em>
          </div>
          <div v-for="n in g.items" :key="n.path" class="kb-row" :class="{ off: n.indexed_status === 'excluded' }">
            <div class="kr-main">
              <div class="kr-title">
                {{ titleOf(n) }}
                <span v-if="n.head_ok === 0" class="kr-flag" title="缺少头部字段（name/status 等）">缺头</span>
              </div>
              <div class="kr-sub" :title="n.path">
                {{ n.display_name }}<template v-if="n.customer"> · {{ n.customer }}</template>
              </div>
            </div>
            <div class="kr-status">
              <span class="badge kb-badge" :class="statusOf(n).cls" :title="n.last_error || statusOf(n).label">
                {{ statusOf(n).label }}
              </span>
            </div>
            <div class="kr-chunks">{{ n.chunks || '—' }}</div>
            <div class="kr-date">{{ n.updated_date || '—' }}</div>
            <div class="kr-acts">
              <button
                v-if="n.indexed_status === 'excluded'"
                class="kr-act"
                title="恢复到知识库（重新切块索引）"
                :disabled="actingPath === n.path"
                @click="toggleExcluded(n)"
              >
                <Icon name="plusCircle" :size="13" />
              </button>
              <button
                v-else
                class="kr-act remove"
                title="移出知识库（只删索引，不删除笔记文件）"
                :disabled="actingPath === n.path"
                @click="toggleExcluded(n)"
              >
                <Icon name="minusCircle" :size="13" />
              </button>
              <button
                class="kr-act"
                title="重建这篇的索引"
                :disabled="actingPath === n.path"
                @click="reindexOne(n)"
              >
                <Icon name="refresh" :size="13" />
              </button>
            </div>
          </div>
        </template>

        <div v-if="!filtered.length" class="kb-empty">
          <Icon name="layers" :size="22" />
          <p v-if="keyword || statusFilter">没有符合条件的笔记</p>
          <p v-else-if="!stats?.notes">还没有扫描到笔记——先在设置页配置 vault 根目录</p>
          <p v-else>暂无笔记</p>
        </div>
      </div>

      <footer class="card-foot">
        <span class="foot-dot" />索引构建在本地完成，只把笔记切块文本发送到云端 embedding
      </footer>
    </section>
  </div>
</template>

<style scoped>
.page-kb {
  display: flex;
  flex-direction: column;
  gap: 11px;
  height: 100%;
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  container-type: inline-size;
  container-name: kbpage;
}

/* ---------------- 概览 ---------------- */
.kb-overview .card-head { padding-bottom: 10px; }
.kb-overview .card-title { margin-right: auto; }
.kb-notes .card-title { margin-right: auto; }
.kb-actions { display: flex; gap: 6px; flex: none; }

.kb-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  background: var(--line-soft);
  border-top: 1px solid var(--line-soft);
}
.metric {
  background: var(--card);
  padding: 10px 14px 11px;
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.metric b {
  font-size: 19px;
  font-weight: 640;
  line-height: 1.25;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.metric b.model {
  font-size: 11.5px;
  font-weight: 600;
  line-height: 1.45;
  color: var(--ink-2);
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.metric span { font-size: 11.5px; color: var(--ink-2); }
.metric em { font-size: 10.5px; color: var(--muted); font-style: normal; }
.metric.warn b { color: var(--amber-ink); }

/* ---------------- 进度条 ---------------- */
.kb-progress {
  padding: 10px 14px 11px;
  border-top: 1px solid var(--line-soft);
  background: #fbfcfe;
}
.kp-head { display: flex; align-items: center; justify-content: space-between; font-size: 11.5px; }
.kp-label { display: inline-flex; align-items: center; gap: 5px; color: var(--blue-ink); font-weight: 600; }
.kp-label svg { animation: kp-spin 1.4s linear infinite; }
.kp-num { color: var(--ink-2); font-variant-numeric: tabular-nums; }
.kp-bar {
  position: relative;
  height: 5px;
  margin: 7px 0 5px;
  border-radius: 99px;
  background: #e8eaf0;
  overflow: hidden;
}
.kp-bar i {
  display: block;
  height: 100%;
  border-radius: 99px;
  background: linear-gradient(90deg, var(--blue), #5aa0ee);
  transition: width 0.4s ease;
}
.kp-bar i.done { background: var(--green); }
.kp-foot { display: flex; justify-content: space-between; gap: 10px; font-size: 10.5px; color: var(--muted); }
.kp-cur { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.kp-fail { color: var(--red); flex: none; }
@keyframes kp-spin { to { transform: rotate(360deg); } }

.kb-error {
  display: flex; align-items: center; gap: 6px;
  padding: 9px 14px;
  border-top: 1px solid var(--red-line);
  background: var(--red-soft);
  color: var(--red);
  font-size: 12px;
}

/* ---------------- 索引状态 ---------------- */
.kb-notes { display: flex; flex-direction: column; min-height: 0; flex: 1; }
.kb-tools {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 7px;
  flex: 1 1 320px;
  min-width: 0;
  flex-wrap: wrap;
}
/* 搜索框在工具条里按剩余空间伸缩，避免把分段按钮挤到第二行 */
.kb-tools .search-field { flex: 1 1 160px; min-width: 110px; max-width: 280px; }

.kb-table-head,
.kb-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 66px 48px 78px 62px;
  gap: 10px;
  align-items: center;
  padding: 0 14px;
}
.kb-table-head {
  height: 27px;
  font-size: 10.5px;
  color: var(--muted);
  background: #fafaf7;
  border-bottom: 1px solid var(--line-soft);
}

.kb-list { flex: 1; min-height: 0; overflow-y: auto; }
.kb-list.loading { opacity: 0.72; }

.kb-group {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 7px 14px 5px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.2px;
  color: var(--blue-ink);
  background: #f4f8fe;
  border-top: 1px solid #e4edfb;
  border-bottom: 1px solid #eff5fd;
}
.kb-group svg { color: var(--blue); opacity: 0.7; }
.kb-group em {
  font-style: normal;
  font-weight: 500;
  font-size: 9.5px;
  color: var(--blue-ink);
  background: #fff;
  border: 1px solid var(--blue-line);
  border-radius: 99px;
  padding: 0 5px;
  font-variant-numeric: tabular-nums;
}

.kb-row {
  padding-top: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f5f4f0;
  font-size: 12px;
  transition: background 0.1s;
}
.kb-row:hover { background: #f8f9fb; }

.kr-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.kr-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 560;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kr-flag {
  flex: none;
  font-size: 9.5px;
  font-weight: 500;
  color: var(--amber-ink);
  background: var(--amber-soft);
  border: 1px solid var(--amber-line);
  border-radius: 4px;
  padding: 0 4px;
}
.kr-sub {
  font-size: 10.5px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kr-status { display: flex; }
.kb-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 5px;
  border: 1px solid transparent;
  white-space: nowrap;
}
.kb-badge.ok { background: var(--green-soft); color: var(--green-ink); border-color: var(--green-line); }
.kb-badge.warn { background: var(--amber-soft); color: var(--amber-ink); border-color: var(--amber-line); }
.kb-badge.bad { background: var(--red-soft); color: var(--red); border-color: var(--red-line); }
.kb-badge.muted { background: #f4f4f1; color: var(--muted); border-color: var(--line-soft); }

.kr-chunks,
.kr-date { font-size: 11px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.kr-date { color: var(--muted); }

.kr-act {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  color: #b9b5ab;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}
/* 行内双操作：移出/恢复 + 重建，末尾列 62px 并排 */
.kr-acts { display: flex; gap: 2px; justify-content: flex-end; }
.kr-acts .kr-act { width: 22px; height: 22px; }
.kr-acts .kr-act:disabled { opacity: 0.45; }
.kb-row:hover .kr-act { color: var(--blue-ink); border-color: var(--line); background: #fff; }
.kr-act:hover { background: var(--blue-soft) !important; border-color: var(--blue-line) !important; }
/* 移出按钮：hover 才转红，避免被误读成「删除文件」 */
.kr-act.remove { color: #c3bfb5; }
.kb-row:hover .kr-act.remove { color: var(--red); }
.kr-act.remove:hover {
  background: var(--red-soft) !important;
  border-color: var(--red-line) !important;
  color: var(--red) !important;
}
/* 已移出的行整体减淡，但仍可读（保留恢复入口） */
.kb-row.off .kr-title { color: var(--ink-2); font-weight: 520; }
.kb-row.off .kr-sub { opacity: 0.72; }

.kb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 44px 20px;
  color: var(--muted);
}
.kb-empty p { margin: 0; font-size: 12px; }

.card-foot { display: flex; align-items: center; gap: 6px; }

/* 窄主区（知识助手展开时）：指标改 2×2，表格先隐日期再隐块数 */
@container kbpage (max-width: 760px) {
  .kb-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric b { font-size: 17px; }
  /* 卡片头改两行：标题一行、工具一条（避免标题被垂直居中夹在两行之间） */
  .kb-notes .card-head { flex-direction: column; align-items: stretch; gap: 8px; padding-bottom: 11px; }
  .kb-notes .card-title { margin-right: 0; }
  /* column 方向下 flex:1 1 320px 会被当成高度，必须复位为按内容 */
  .kb-tools { flex: 0 0 auto; justify-content: flex-start; flex-wrap: nowrap; }
  .kb-tools .search-field { flex: 1 1 auto; min-width: 110px; max-width: none; }
  .kb-notes .seg { flex: none; }
  .kb-table-head,
  .kb-row { grid-template-columns: minmax(0, 1fr) 66px 48px 62px; }
  .kb-table-head span:nth-child(4),
  .kr-date { display: none; }
}
/* 更窄（如笔记本 + 助手展开）：连块数也隐掉，只留笔记/状态/重建 */
@container kbpage (max-width: 560px) {
  .kb-table-head,
  .kb-row { grid-template-columns: minmax(0, 1fr) 62px 62px; }
  .kb-table-head span:nth-child(3),
  .kr-chunks { display: none; }
}
</style>
