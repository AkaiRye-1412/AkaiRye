<script setup lang="ts">
/**
 * M1 首页台面（FR-HOME / FR-DASH / FR-PROJ）— 对齐 prototype/js/views-home.js
 *
 * 页面结构（FR-HOME-01）：数据看板（通栏）→ 进行中项目（通栏，按客户分组）。
 * 代码仓库折叠条（FR-HOME-06 / M6 P2）已按需求从本页移除，入口保留在侧栏。
 * 顶部全局提问/终端/新建笔记由 App.vue 顶栏统一提供（FR-HOME-02/03，全页面可用）。
 *
 * 数据来源（FR-DASH-07，本页不写统计口径）：
 * - summary ← /api/dashboard/summary（进度 / 合计 / 平均处理天数 / 客户分布）
 * - trend   ← /api/dashboard/trend（FR-DASH-05 三档，Q-06）
 * - follow-ups ← /api/dashboard/follow-ups（FR-DASH-06 待跟进标记）
 * - 项目列表 ← /api/projects（FR-PROJ-01，一项目一笔记）
 * - 翻译     ← /api/translate（英中双向翻译 + 中文讲解，复用 LLM 配置）
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])
import { dashboardApi, eventsSocket, projectsApi, reposApi, translateApi } from '@/api'
import type { GitRepo, TranslateResult } from '@/api'
import { ApiError } from '@/api/http'
import { renderMarkdown } from '@/rag/md'
import { useConfigStore } from '@/stores/config'
import { USER_NAME } from '@/utils/profile'
import Icon from '@/components/Icon.vue'

const router = useRouter()
const configStore = useConfigStore()

// ---- 类型 ----
interface ProjectRow {
  customer: string | null
  project_name: string
  status: string
  start_date: string | null
  updated_date: string | null
  note_path: string
  head_ok: number
}
interface FollowUp {
  path: string
  customer: string | null
  project_name: string | null
  updated_date: string | null
  days: number
}
type Range = 'month' | '30d' | '6m'

const RANGES: { key: Range; label: string }[] = [
  { key: 'month', label: '本月' },
  { key: '30d', label: '近 30 天' },
  { key: '6m', label: '近 6 月' },
]
const RANGE_TITLE: Record<Range, string> = {
  month: '本月 · 按周',
  '30d': '近 5 周 · 按周',
  '6m': '近 6 月 · 按月',
}

// ---- 状态 ----
const summary = ref<Awaited<ReturnType<typeof dashboardApi.summary>> | null>(null)
const followUps = ref<FollowUp[]>([])
const projects = ref<ProjectRow[]>([])
const range = ref<Range>('month')
const trendSeries = ref<{ bucket: string; count: number }[]>([])
const trendTotal = ref(0)
const projectFilter = ref<'active' | 'archived'>('active')
const expanded = ref<Record<string, boolean>>({}) // 客户分组默认折叠（空表 = 全收起），点标题行才铺开
const loading = ref(false)
const refreshing = ref(false)
const errorMsg = ref('')

// ---- 代码仓库（FR-GIT-06：首页顶部折叠展示，只读） ----
const repos = ref<GitRepo[]>([])
const reposLoading = ref(true)

/** 未提交总数（跨全部可见仓库求和，用于卡片头部黄点提示） */
const repoDirtyTotal = computed(() => repos.value.reduce((s, r) => s + (r.dirty ?? 0), 0))

async function loadRepos() {
  try {
    repos.value = await reposApi.list(false)
  } catch {
    repos.value = []
  } finally {
    reposLoading.value = false
  }
}

/** 仓库状态文案（与 ReposView 保持一致） */
function repoStateText(r: GitRepo): string {
  if (!r.exists) return '目录缺失'
  if (r.error) return '读取失败'
  if (r.dirty) return `${r.dirty} 处未提交`
  return '干净'
}

/** 仓库状态配色档位 */
function repoStateKind(r: GitRepo): 'ok' | 'warn' | 'err' | 'missing' {
  if (!r.exists) return 'missing'
  if (r.error) return 'err'
  if (r.dirty) return 'warn'
  return 'ok'
}

function openRepoTerminal(repo: GitRepo) {
  router.push({ path: '/terminal', query: { cwd: repo.path } })
}

// ---- 英中翻译（替代原「时效提醒」区块） ----
// 方向固定自动识别（不再提供手动切换），输入后回车即翻译。
const trText = ref('')
const trLoading = ref(false)
const trError = ref('')
const trErrorCode = ref('')
const trCopied = ref(false)
const trResult = ref<TranslateResult | null>(null)
let trCopyTimer: ReturnType<typeof setTimeout> | null = null

const trDirLabel = computed(() =>
  trResult.value?.direction === 'zh-en' ? '中 → 英' : '英 → 中',
)

/** 词条模式（按场景列义项）还是句子模式（分点讲解）。 */
const trIsWord = computed(() => trResult.value?.mode === 'word' && !!trResult.value.senses.length)

async function runTranslate() {
  const text = trText.value.trim()
  if (!text || trLoading.value) return
  trLoading.value = true
  trError.value = ''
  trErrorCode.value = ''
  try {
    trResult.value = await translateApi.run({ text, direction: 'auto' })
    // 看板高度受限时卡片内部会滚动：把翻译区顶端对齐到卡片可视区顶部，
    // 保证用完后输入框和译文都在视线里（用 scrollTop 精确对齐，避免残留半截图表）
    // 等两帧让结果区完成布局（图表 resize 会带一次回流），再精确对齐滚动位置
    await nextTick()
    await new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())))
    const card = document.querySelector('.dash-card') as HTMLElement | null
    const block = document.querySelector('.trans-block') as HTMLElement | null
    if (card && block && card.scrollHeight > card.clientHeight) {
      card.scrollTop += Math.round(block.getBoundingClientRect().top - card.getBoundingClientRect().top)
    }
  } catch (e) {
    trResult.value = null
    trError.value = (e as Error).message || '翻译失败'
    trErrorCode.value = e instanceof ApiError ? e.code : ''
  } finally {
    trLoading.value = false
  }
}

/** 回车直接翻译；输入法组词中的回车不拦截，Shift+Enter 仍可换行。 */
function onTransEnter(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  e.preventDefault()
  runTranslate()
}

async function copyTranslation() {
  if (!trResult.value) return
  try {
    await navigator.clipboard.writeText(trResult.value.translation)
    trCopied.value = true
    if (trCopyTimer) clearTimeout(trCopyTimer)
    trCopyTimer = setTimeout(() => (trCopied.value = false), 1200)
  } catch {
    trCopied.value = false
  }
}

function clearTranslate() {
  trText.value = ''
  trResult.value = null
  trError.value = ''
  trErrorCode.value = ''
}

// ---- 日期展示工具（仅展示，不重算统计口径） ----
function parseDay(value: string | null | undefined): Date | null {
  if (!value) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!m) return null
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}

function daysSince(value: string | null | undefined): number | null {
  const d = parseDay(value)
  if (!d) return null
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((today.getTime() - d.getTime()) / 86_400_000)
}

function fullDate(value: string | null | undefined): string {
  return parseDay(value) ? String(value).slice(0, 10) : '—'
}

function relDays(value: string | null | undefined): string {
  const n = daysSince(value)
  if (n === null) return '—'
  if (n <= 0) return '今天'
  if (n === 1) return '昨天'
  return `${n} 天前`
}

const scannedLabel = computed(() => {
  const v = summary.value?.scanned_at
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '—'
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})

// ---- 招呼语 / 日期（每分钟自更新，跨零点也不会停在旧日期） ----
const now = ref(new Date())
let clockTimer: ReturnType<typeof setInterval> | null = null

const greeting = computed(() => {
  const h = now.value.getHours()
  if (h < 5) return '夜深了'
  if (h < 9) return '早上好'
  if (h < 12) return '上午好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  if (h < 23) return '晚上好'
  return '夜深了'
})

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']
const dateLabel = computed(() => {
  const d = now.value
  return `${d.getFullYear()} 年 ${d.getMonth() + 1} 月 ${d.getDate()} 日 · 星期${WEEKDAYS[d.getDay()]}`
})

/** 客户头像底色（原型 U.avatarColor 的轻量替代：按名称散列取色板）。 */
const AVATAR_COLORS = ['#2a78d6', '#1baf7a', '#eb6834', '#6250d6', '#c2410c', '#0e7490']
function avatarColor(name: string): string {
  let h = 0
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 997
  return AVATAR_COLORS[h % AVATAR_COLORS.length]
}

// ---- 派生数据 ----
const followUpMap = computed(() => {
  const map = new Map<string, number>()
  for (const f of followUps.value) map.set(f.path, f.days)
  return map
})

/** 时效提醒已取消：follow-ups 仅用于项目列表的「N 天未更新」标记。 */

const groupedProjects = computed(() => {
  const map = new Map<string, ProjectRow[]>()
  for (const p of projects.value) {
    const key = p.customer || '（未识别客户）'
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(p)
  }
  const groups = [...map.entries()].map(([customer, items]) => {
    const sorted = [...items].sort((a, b) => (b.updated_date || '').localeCompare(a.updated_date || ''))
    const latest = items.reduce((acc, x) => (x.updated_date && x.updated_date > acc ? x.updated_date : acc), '')
    return { customer, items: sorted, latest }
  })
  // 客户组按最近更新倒序（原型 noteGroups 口径）
  return groups.sort((a, b) => b.latest.localeCompare(a.latest))
})

/** 客户分布横条：以最大记录数归一化（原型 Charts.clients 口径）。 */
const customerRows = computed(() => {
  const top = summary.value?.customer_top ?? []
  const max = Math.max(1, ...top.map((x) => x.total))
  return top.map((x) => ({ ...x, pct: Math.max(4, Math.round((x.total / max) * 100)) }))
})

function toggleGroup(customer: string) {
  expanded.value = { ...expanded.value, [customer]: !expanded.value[customer] }
}

// ---- 拉取 ----
let reqSeq = 0 // 并发防护：筛选/范围快速切换时只采纳最后一次请求

async function refreshAll() {
  const seq = ++reqSeq
  refreshing.value = true
  loading.value = summary.value === null
  try {
    const [s, f, p, t] = await Promise.all([
      dashboardApi.summary(),
      dashboardApi.followUps(),
      projectsApi.list(`?status=${projectFilter.value}`),
      dashboardApi.trend(range.value),
    ])
    if (seq !== reqSeq) return
    summary.value = s
    followUps.value = f as FollowUp[]
    projects.value = p as unknown as ProjectRow[]
    trendSeries.value = t.series
    trendTotal.value = t.total
    errorMsg.value = ''
    await nextTick()
    renderTrend()
  } catch (e) {
    if (seq === reqSeq) errorMsg.value = (e as Error).message || '看板数据加载失败'
  } finally {
    if (seq === reqSeq) {
      loading.value = false
      refreshing.value = false
    }
  }
}

// ---- 趋势图（ECharts；视觉对齐原型 Charts.trend：蓝柱 + 末柱深蓝 + 数值直标） ----
// 容器常驻（不再用 v-show）：隐藏时初始化会拿到 0 宽度，之后不再 resize，
// 表现为柱子和坐标轴全挤在左侧一小块。空态改为覆盖层。
const trendEl = ref<HTMLElement | null>(null)
let trendChart: echarts.ECharts | null = null
let trendObserver: ResizeObserver | null = null

/** 分桶标签压缩：周区间只留起始日、月区间只留月份，tooltip 仍用完整桶名。 */
function bucketLabel(b: string): string {
  if (b.includes('~')) return b.split('~')[0]
  const m = /^(\d{4})-(\d{2})$/.exec(b)
  if (m) return `${Number(m[2])}月`
  return b
}

function initTrend() {
  const el = trendEl.value
  if (!el || trendChart) return
  if (!el.clientWidth) return // 宽度未就绪时等 ResizeObserver 再初始化
  trendChart = echarts.init(el)
  trendObserver = new ResizeObserver(() => trendChart?.resize())
  trendObserver.observe(el)
  renderTrend()
}

function renderTrend() {
  if (!trendEl.value) return
  if (!trendChart) {
    initTrend()
    if (!trendChart) return
  }
  const values = trendSeries.value.map((x) => x.count)
  const last = values.length - 1
  trendChart.setOption(
    {
      grid: { left: 34, right: 14, top: 26, bottom: 24 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: '#202124',
        borderWidth: 0,
        textStyle: { color: '#f7f7f3', fontSize: 11 },
        formatter: (ps: any) => {
          const p = ps[0]
          return `${p.name}<br/>新增售后记录：<b>${p.value}</b> 条`
        },
      },
      xAxis: {
        type: 'category',
        data: trendSeries.value.map((x) => x.bucket),
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#e9e7df' } },
        axisLabel: {
          fontSize: 10,
          color: '#8b8880',
          interval: 0,
          hideOverlap: true,
          formatter: (v: string) => bucketLabel(v),
        },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitNumber: 4,
        splitLine: { lineStyle: { color: '#e9e7df' } },
        axisLabel: { fontSize: 10, color: '#827e75' },
      },
      series: [
        {
          type: 'bar',
          data: values.map((v, i) => ({
            value: v,
            itemStyle: { color: i === last ? '#1c5cab' : '#2a78d6', borderRadius: [4, 4, 0, 0] },
          })),
          barMaxWidth: 26,
          label: {
            show: true,
            position: 'top',
            fontSize: 11,
            fontWeight: 650,
            color: '#17181a',
            formatter: (p: any) => (p.value > 0 ? p.value : ''),
          },
        },
      ],
    },
    true,
  )
  trendChart.resize()
}

function onResize() {
  trendChart?.resize()
}

// ---- 交互 ----
function openNote(path: string) {
  router.push({ path: '/notes', query: { path } })
}

function newNote() {
  router.push({ path: '/notes', query: { new: '1' } })
}

function switchFilter(status: 'active' | 'archived') {
  if (projectFilter.value === status) return
  projectFilter.value = status
  refreshAll()
}

watch(range, () => refreshAll())

let refreshTimer: number | undefined
onMounted(async () => {
  window.addEventListener('resize', onResize)
  clockTimer = setInterval(() => (now.value = new Date()), 60_000)
  await configStore.load()
  const dr = configStore.config?.dashboard?.default_range
  if (dr === 'month' || dr === '30d' || dr === '6m') range.value = dr
  await refreshAll()
  void loadRepos() // 仓库状态独立拉取，不阻塞看板渲染
  // 外部修改 → note.changed → 自动刷新（NFR-PERF-04：轻量防抖，不实时全量扫描）
  eventsSocket.on('note.changed', () => {
    window.clearTimeout(refreshTimer)
    refreshTimer = window.setTimeout(refreshAll, 1500)
  })
  // 代码仓库有改动 → repo.changed → 自动重拉仓库状态（首页仓库条实时更新）
  offRepoChanged = eventsSocket.on('repo.changed', () => {
    void loadRepos()
  })
})

let offRepoChanged: (() => void) | null = null
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.clearTimeout(refreshTimer)
  if (clockTimer) clearInterval(clockTimer)
  if (trCopyTimer) clearTimeout(trCopyTimer)
  offRepoChanged?.()
  trendObserver?.disconnect()
  trendObserver = null
  trendChart?.dispose()
  trendChart = null
})
</script>

<template>
  <div class="page-home">
    <div v-if="errorMsg" class="card error-card">
      <div class="error-title">看板数据加载失败</div>
      <div class="error-msg">{{ errorMsg }}</div>
      <div class="error-actions">
        <button class="btn ghost sm" @click="refreshAll">重试</button>
        <button class="btn ghost sm" @click="router.push('/settings')">检查 vault 配置</button>
      </div>
    </div>

    <template v-else-if="summary">
      <!-- ============ 数据看板（FR-DASH） ============ -->
      <section class="card dash-card" :class="{ 'tr-open': !!trResult }">
        <header class="dash-greet">
          <div class="greet-main">
            <h2 class="greet-title">{{ greeting }}，{{ USER_NAME }}</h2>
            <p class="greet-sub">{{ dateLabel }}</p>
          </div>
          <div class="seg sm">
            <button
              v-for="r in RANGES"
              :key="r.key"
              class="seg-btn"
              :class="{ active: range === r.key }"
              @click="range = r.key"
            >
              {{ r.label }}
            </button>
          </div>
        </header>

        <div class="stat-row">
          <div class="stat-tile">
            <span class="stat-label"><span class="status-dot blue" />进行中</span>
            <span class="stat-value">{{ summary.active_count }}</span>
            <span class="stat-sub">个项目待处理</span>
          </div>
          <div class="stat-tile warn">
            <span class="stat-label"><span class="status-dot amber" />待跟进</span>
            <span class="stat-value">{{ summary.follow_up_count }}</span>
            <span class="stat-sub">超过 {{ summary.follow_up_days }} 天未更新</span>
          </div>
          <div class="stat-tile">
            <span class="stat-label"><span class="status-dot green" />已归档</span>
            <span class="stat-value">{{ summary.archived_count }}</span>
            <span class="stat-sub">手动归档项目</span>
          </div>
          <div class="stat-tile">
            <span class="stat-label"><span class="status-dot violet" />合计记录</span>
            <span class="stat-value">{{ summary.total_count }}</span>
            <span class="stat-sub">全部客户与项目</span>
          </div>
        </div>

        <div class="dash-grid">
          <div class="chart-card">
            <div class="chart-head">
              <div>
                <h3>新增售后记录</h3>
                <span>{{ RANGE_TITLE[range] }} · 共 {{ trendTotal }} 条</span>
              </div>
              <button v-if="!trendTotal && range !== '6m'" class="link-btn" @click="range = '6m'">
                切到近 6 月 →
              </button>
            </div>
            <div class="trend-box">
              <div ref="trendEl" class="trend-wrap" />
              <div v-if="!trendTotal" class="chart-empty">该区间暂无新增记录</div>
            </div>
          </div>

          <div class="chart-card">
            <div class="chart-head">
              <div>
                <h3>客户分布</h3>
                <span>按记录数 · 含未结项目</span>
              </div>
            </div>
            <div v-if="customerRows.length" class="hbar-list">
              <div v-for="c in customerRows" :key="c.customer" class="hbar-row">
                <span class="hbar-name" :title="c.customer">{{ c.customer }}</span>
                <span class="hbar-track">
                  <span class="hbar-fill" :style="{ width: c.pct + '%' }">
                    <span class="hbar-value">{{ c.total }}</span>
                  </span>
                </span>
                <span class="hbar-open">{{ c.open }} 未结</span>
              </div>
            </div>
            <div v-else class="chart-empty chart-empty-block">暂无客户数据</div>
          </div>
        </div>

        <!-- 英中翻译（替代原「时效提醒」区块） -->
        <div class="trans-block">
          <div class="trans-head">
            <h3><span class="trans-ic">译</span>英中翻译<em>译文 + 讲解</em></h3>
          </div>

          <div class="trans-input">
            <textarea
              v-model="trText"
              rows="2"
              maxlength="5000"
              placeholder="粘贴英文或中文，自动识别方向…"
              @keydown.enter.exact="onTransEnter"
            />
            <div class="trans-bar">
              <span class="trans-count">
                {{ trText.length }} / 5000
                <span class="trans-kbd">Enter</span>
              </span>
              <div class="trans-btns">
                <button v-if="trText || trResult" class="link-btn" @click="clearTranslate">清空</button>
                <button class="btn primary sm" :disabled="!trText.trim() || trLoading" @click="runTranslate">
                  {{ trLoading ? '翻译中…' : '翻译并解释' }}
                </button>
              </div>
            </div>
          </div>

          <div v-if="trError" class="trans-error">
            <span>{{ trError }}</span>
            <button
              v-if="trErrorCode === 'llm_not_configured'"
              class="link-btn"
              @click="router.push('/settings')"
            >
              去设置 →
            </button>
          </div>

          <div v-if="trResult" class="trans-result">
            <div class="trans-result-head">
              <span class="trans-tag">{{ trDirLabel }}</span>
              <span class="trans-model" :title="trResult.model">{{ trResult.model }}</span>
              <button class="link-btn trans-copy" @click="copyTranslation">
                {{ trCopied ? '已复制' : '复制译文' }}
              </button>
            </div>
            <div class="trans-body">
              <p class="trans-text">{{ trResult.translation }}</p>

              <!-- 讲解（参考有道词典：单词按场景列义项，句子分点讲解） -->
              <div v-if="trIsWord" class="trans-explain">
                <div class="trans-explain-title">释义 · 按场景</div>
                <div class="sense-list">
                  <div v-for="(s, i) in trResult.senses" :key="i" class="sense">
                    <div class="sense-head">
                      <span class="sense-meaning">{{ s.meaning }}</span>
                      <span v-if="s.pos" class="sense-pos">{{ s.pos }}</span>
                      <span v-if="s.tag" class="sense-tag">{{ s.tag }}</span>
                    </div>
                    <p v-if="s.scene" class="sense-scene">{{ s.scene }}</p>
                    <ul v-if="s.examples.length" class="sense-egs">
                      <li v-for="(eg, j) in s.examples" :key="j">
                        <span class="eg-src">{{ eg.src }}</span>
                        <span class="eg-arrow">→</span>
                        <span class="eg-dst">{{ eg.dst }}</span>
                      </li>
                    </ul>
                  </div>
                </div>
                <p v-if="trResult.tips" class="trans-tip">{{ trResult.tips }}</p>
              </div>

              <div v-else-if="trResult.points.length || trResult.tips" class="trans-explain">
                <div class="trans-explain-title">讲解</div>
                <div class="point-list">
                  <div v-for="(p, i) in trResult.points" :key="i" class="point">
                    <span class="point-label">{{ p.label }}</span>
                    <span class="point-text">{{ p.text }}</span>
                  </div>
                </div>
                <p v-if="trResult.tips" class="trans-tip">{{ trResult.tips }}</p>
              </div>

              <div v-else-if="trResult.explanation" class="trans-explain">
                <div class="trans-explain-title">讲解</div>
                <div class="trans-md" v-html="renderMarkdown(trResult.explanation)" />
              </div>
            </div>
          </div>
          <div v-else-if="!trLoading" class="trans-empty">
            支持英文 ⇄ 中文：单词按场景列释义，句子给术语、语法与语气讲解。
          </div>
        </div>
      </section>

      <!-- ============ 代码仓库（FR-GIT-06：折叠条，置于进行中项目上方） ============ -->
      <section class="card repos-card">
        <header class="card-head">
          <div class="card-title">
            <span class="repos-title-ic"><Icon name="git" :size="15" /></span>
            <h2>代码仓库</h2>
            <span class="count-pill">{{ repos.length }}</span>
            <span v-if="repoDirtyTotal" class="repos-dirty-pill">⏳ {{ repoDirtyTotal }} 处未提交</span>
          </div>
          <button class="btn ghost sm" @click="router.push('/repos')">全部仓库 ›</button>
        </header>

        <!-- 加载中 -->
        <div v-if="reposLoading" class="repos-skeleton">
          <span>正在读取仓库状态…</span>
        </div>

        <!-- 未配置 / 无仓库 -->
        <div v-else-if="!repos.length" class="repos-empty">
          <span class="repos-empty-ic"><Icon name="git" :size="18" /></span>
          <span>尚未发现代码仓库，可在「设置 → 代码仓库根目录」配置</span>
          <button class="link-btn" @click="router.push('/settings')">去设置 ›</button>
        </div>

        <!-- 仓库列表（横向滚动的紧凑卡片） -->
        <div v-else class="repos-strip">
          <button
            v-for="r in repos"
            :key="r.path"
            class="repo-chip"
            :class="[`st-${repoStateKind(r)}`, { missing: !r.exists }]"
            @click="openRepoTerminal(r)"
            :title="r.path"
          >
            <span class="repo-chip-head">
              <span class="repo-state-dot" />
              <span class="repo-chip-name">{{ r.name }}</span>
            </span>
            <span class="repo-chip-branch">
              <Icon name="git" :size="10" />
              <code>{{ r.branch || '—' }}</code>
            </span>
            <span class="repo-chip-state">{{ repoStateText(r) }}</span>
          </button>
        </div>
      </section>

      <!-- ============ 项目列表（FR-HOME-04 / FR-PROJ） ============ -->
      <section class="card projects-card">
        <header class="card-head">
          <div class="card-title">
            <h2>{{ projectFilter === 'active' ? '进行中项目' : '已归档项目' }}</h2>
            <span class="count-pill">{{ projects.length }}</span>
          </div>
          <div class="seg sm">
            <button
              class="seg-btn"
              :class="{ active: projectFilter === 'active' }"
              @click="switchFilter('active')"
            >
              进行中 <em>{{ summary.active_count }}</em>
            </button>
            <button
              class="seg-btn"
              :class="{ active: projectFilter === 'archived' }"
              @click="switchFilter('archived')"
            >
              已归档 <em>{{ summary.archived_count }}</em>
            </button>
          </div>
          <button class="btn ghost sm" @click="router.push('/notes')">全部笔记 ›</button>
        </header>

        <div class="project-groups">
          <template v-if="groupedProjects.length">
            <div v-for="g in groupedProjects" :key="g.customer" class="pgroup">
              <button class="pgroup-head" @click="toggleGroup(g.customer)">
                <span class="pgroup-caret">{{ expanded[g.customer] ? '▾' : '▸' }}</span>
                <span class="customer-avatar" :style="{ '--av': avatarColor(g.customer) }">
                  {{ g.customer.slice(0, 1) }}
                </span>
                <span class="pgroup-name">{{ g.customer }}</span>
                <span class="pgroup-count">{{ g.items.length }}</span>
                <span class="pgroup-latest">最近更新 {{ fullDate(g.latest) }}</span>
              </button>
              <div v-show="expanded[g.customer]" class="pgroup-body">
                <div
                  v-for="p in g.items"
                  :key="p.note_path"
                  class="project-row"
                  @click="openNote(p.note_path)"
                >
                  <span
                    class="p-dot"
                    :class="
                      followUpMap.has(p.note_path) ? 'stale' : p.status === 'archived' ? 'archived' : 'active'
                    "
                  />
                  <div class="p-main">
                    <div class="p-title-line">
                      <span class="p-title">{{ p.project_name }}</span>
                      <span v-if="followUpMap.has(p.note_path)" class="badge warn">
                        ⏱ {{ followUpMap.get(p.note_path) }} 天未更新
                      </span>
                      <span v-if="p.status === 'archived'" class="badge muted">已归档</span>
                      <span
                        v-if="p.head_ok === 0"
                        class="badge muted"
                        title="缺少结构化头部字段，可到「存量整理」补齐"
                      >
                        缺头
                      </span>
                    </div>
                    <div class="p-meta">
                      <span>{{ p.customer || '（未知客户）' }}</span>
                      <span class="sep-dot" />
                      <span>最近更新 {{ fullDate(p.updated_date) }}（{{ relDays(p.updated_date) }}）</span>
                      <span class="sep-dot" />
                      <span>开始 {{ fullDate(p.start_date) }}</span>
                    </div>
                  </div>
                  <span class="p-open">›</span>
                </div>
              </div>
            </div>
          </template>

          <div v-else class="empty-block">
            <div class="empty-ic">
              <span style="font-size: 22px">{{ projectFilter === 'active' ? '✓' : '▣' }}</span>
            </div>
            <p>{{ projectFilter === 'active' ? '没有进行中的项目' : '还没有归档项目' }}</p>
            <button v-if="projectFilter === 'active'" class="btn primary sm" @click="newNote">
              新建售后笔记
            </button>
          </div>
        </div>

        <footer class="card-foot">
          <span>由 {{ summary.total_count }} 篇售后笔记扫描生成</span>
          <span class="foot-dot" />
          <span>扫描于 {{ scannedLabel }}</span>
          <span class="foot-dot" />
          <span>待跟进阈值 {{ summary.follow_up_days }} 天</span>
          <button class="link-btn" @click="router.push('/cleanup')">整理存量笔记 ›</button>
        </footer>
      </section>
    </template>

    <div v-else class="card loading-card">{{ loading ? '加载看板数据…' : '暂无数据' }}</div>
  </div>
</template>

<style scoped>
.page-home {
  display: flex;
  flex-direction: column;
  gap: 14px;
  /* 不再限宽居中：知识助手收起后内容区直接铺满，只保留 .main 的左右 18px 边距
     （与卡片间距 14px 同量级，视觉上不空也不贴边） */
  min-width: 0;
  /* 首页整屏定高（.main-flush 为 flex 容器）：项目卡永远贴住窗口底，
     底边与右侧知识助手面板同一条水平线，不随内容多少上下浮动 */
  flex: 1 1 auto;
  min-height: 0;
  /* 正常高度下内容正好铺满（项目卡底边 = 知识助手底边）；
     窗口过矮时整体滚动兜底，避免把卡片内容切成半行 */
  overflow-y: auto;
  overflow-x: hidden;
}
/* 看板区吃掉除项目卡以外的全部高度；内部按「图表让位、翻译优先」分配：
   窗口变矮时先压图表，翻译输入/结果保持可见；实在放不下才整卡滚动 */
/* 看板按内容取自然高度：图表固定高度，剩余空间给下方的项目卡。
   不做卡内滚动——否则矮窗口会把客户分布切成半行，视觉上像坏了 */
.dash-card {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
}
.dash-card .dash-greet,
.dash-card .stat-row,
.dash-card .trans-block { flex: none; }
/* 翻译展开时，月报图表（新增记录 / 客户分布）临时让位给译文：
   译文再长也不会把「进行中项目」挤出屏幕，项目卡底边始终与知识助手齐平；
   清空译文后图表原样回来 */
.dash-card.tr-open .dash-grid { display: none; }
/* 项目卡吃掉剩余高度（内容多，适合承载弹性空间）：内部列表滚动、底部状态栏钉在卡片底，
   底边始终等于 .page-home 底边 = 知识助手面板底边 */
.projects-card {
  /* basis 必须为 0：项目列表内容很长，用 auto 会让 flex 误判为溢出而先压缩看板 */
  flex: 1 1 0;
  min-height: 212px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.projects-card .card-head { flex: none; }
.projects-card .card-foot { flex: none; }

/* ============ 数据看板 ============ */
/* 招呼语区（对齐参考图：大字招呼 + 日期行，右侧放区间切换） */
.dash-greet {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px 13px;
  flex-wrap: wrap;
}
.greet-main { min-width: 0; }
.greet-title {
  margin: 0;
  font-size: 21px;
  font-weight: 680;
  line-height: 1.25;
  letter-spacing: -0.4px;
  color: var(--ink);
}
.greet-sub {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin: 5px 0 0;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ink-2);
}

.stat-row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; padding: 0 18px 14px; }
.stat-tile {
  border: 1px solid rgba(231, 229, 221, 0.75);
  border-radius: 11px;
  /* 内嵌块不再自带磨砂（外层 .card 已经 backdrop 过了），只做半透明白叠色，
     两层磨砂嵌套会明显拖慢滚动 */
  background: rgba(255, 255, 255, 0.26);
  padding: 11px 13px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.stat-tile.warn { background: rgba(255, 250, 240, 0.42); border-color: var(--amber-line); }
.stat-label { font-size: 11.5px; font-weight: 560; color: var(--ink-2); display: flex; align-items: center; }
.stat-value { font-size: 27px; font-weight: 630; letter-spacing: -0.5px; line-height: 1.25; color: var(--ink-2); }
.stat-tile.warn .stat-value { color: var(--amber-ink); }
.stat-sub { font-size: 11px; color: #6a675f; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.dash-grid {
  display: grid;
  grid-template-columns: 1.5fr 1fr;
  gap: 10px;
  padding: 0 18px 12px;
  /* 固定高度：图表区不再被拉伸成一大片空白（数据少时尤其明显），
     高度随窗口在 200-250 之间浮动，保证图表本身可读 */
  flex: none;
  height: clamp(212px, 24vh, 262px);
}
.chart-card {
  border: 1px solid rgba(239, 238, 232, 0.8);
  border-radius: 11px;
  background: rgba(255, 255, 255, 0.22);
  padding: 11px 13px 8px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  /* 图表被压矮时 ECharts canvas 可能还按旧高度绘制，裁掉溢出避免压到下方翻译区 */
  overflow: hidden;
  /* 定位上下文：卡内空态 .chart-empty 是 inset:0 绝对定位，缺这行会以 .main 为基准铺满主区 */
  position: relative;
}
.chart-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
.chart-head h3 { font-size: 13px; font-weight: 650; color: var(--ink); margin: 0 0 1px; }
.chart-head span { font-size: 11.5px; color: var(--ink-2); }
.trend-box { position: relative; flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.trend-wrap { width: 100%; height: 100%; min-height: 96px; margin-top: 2px; }
.chart-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: #fcfcfb;
  color: var(--muted);
  font-size: 12px;
}
/* 客户分布空态：无图表容器可覆盖，改为卡内静态占位（高度对齐有数据时的横条区） */
.chart-empty-block {
  position: static;
  inset: auto;
  min-height: 150px;
  background: transparent;
}

.hbar-list { display: flex; flex-direction: column; gap: 11px; padding: 10px 2px 4px; flex: 1 1 auto; min-height: 0; overflow-y: auto; }
.hbar-row { display: grid; grid-template-columns: 64px 1fr 52px; align-items: center; gap: 8px; }
/* 客户名与条尾数字是这一块的「辅助信息」，不能盖过上面的图表标题：
   名字用 ink-2（不抢标题的近黑），数字退到 ink-2 与 muted 之间的中间灰 */
.hbar-name { font-size: 12px; font-weight: 500; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hbar-track {
  height: 12px;
  display: flex;
  align-items: center;
  position: relative;
  border-radius: 2px;
  background: linear-gradient(#f3f2ec, #f3f2ec) center/100% 1px no-repeat;
  min-width: 0;
}
.hbar-fill {
  height: 10px;
  background: var(--blue);
  border-radius: 0 4px 4px 0;
  min-width: 4px;
  position: relative;
  transition: width 0.4s ease;
}
.hbar-value {
  position: absolute;
  left: calc(100% + 6px);
  top: 50%;
  transform: translateY(-50%);
  font-size: 11px;
  font-weight: 600;
  color: #6a675f;
  font-variant-numeric: tabular-nums;
}
/* 「N 未结」与条尾数字同属计数层级，用同一档中间灰——比客户名浅、又不像 muted 那样发虚 */
.hbar-open { font-size: 10.5px; color: #6a675f; font-weight: 500; text-align: right; white-space: nowrap; }

/* 英中翻译（替代时效提醒） */
.trans-block { margin: 2px 18px 16px; padding-top: 12px; border-top: 1px solid var(--line-soft); }
.trans-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.trans-head h3 { font-size: 12.5px; font-weight: 600; margin: 0; display: flex; align-items: center; gap: 6px; }
.trans-head h3 em { font-style: normal; font-size: 10px; font-weight: 500; color: var(--muted); }
.trans-ic {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 5px;
  background: var(--blue-soft);
  border: 1px solid var(--blue-line);
  color: var(--blue-ink);
  font-size: 10.5px;
  font-weight: 700;
}
.trans-input {
  border: 1px solid rgba(231, 229, 221, 0.85);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.36);
  overflow: hidden;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.trans-input:focus-within {
  border-color: var(--blue-line);
  box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.08);
}
.trans-input textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  min-height: 66px;
  max-height: 180px;
  padding: 9px 11px 4px;
  border: 0;
  outline: 0;
  resize: vertical;
  background: transparent;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--ink);
}
.trans-input textarea::placeholder { color: var(--muted); }
.trans-bar { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 5px 8px 7px 11px; }
.trans-count { display: flex; align-items: center; gap: 8px; font-size: 10px; color: var(--muted); font-variant-numeric: tabular-nums; }
.trans-kbd { border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; background: #fafaf7; font-size: 9px; }
.trans-btns { display: flex; align-items: center; gap: 8px; }
.trans-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 8px;
  padding: 7px 10px;
  border: 1px solid #f0c9c4;
  border-radius: 7px;
  background: #fdf3f1;
  color: #a33227;
  font-size: 11px;
}
.trans-result { margin-top: 10px; padding: 9px 11px 10px; border: 1px solid rgba(231, 229, 221, 0.85); border-radius: 9px; background: rgba(255, 255, 255, 0.26); }
/* 译文/讲解随内容自然展开，超出屏幕的高度由看板卡片统一滚动（只保留一层滚动条） */
.trans-body {
  padding-right: 4px;
  margin-top: 1px;
  /* 结果过长时在区域内滚动（单层，不叠加整页滚动） */
  max-height: clamp(150px, 24vh, 238px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.trans-body::-webkit-scrollbar { width: 8px; }
.trans-result-head { display: flex; align-items: center; gap: 7px; }
.trans-tag {
  flex: none;
  padding: 1px 5px;
  border: 1px solid var(--blue-line);
  border-radius: 4px;
  background: var(--blue-soft);
  color: var(--blue-ink);
  font-size: 9.5px;
  font-weight: 600;
}
.trans-model { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 9.5px; color: var(--muted); }
.trans-copy { margin-left: auto; }
.trans-text { margin: 7px 0 0; font-size: 13px; line-height: 1.6; color: var(--ink); white-space: pre-wrap; }
.trans-explain { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--line-soft); }
.trans-explain-title { margin-bottom: 5px; font-size: 11px; font-weight: 650; letter-spacing: 0.4px; color: var(--ink-2); }
.trans-md { font-size: 11.5px; line-height: 1.62; color: var(--ink-2); }
.trans-md :deep(p) { margin: 3px 0; }
.trans-md :deep(ul), .trans-md :deep(ol) { margin: 3px 0; padding-left: 17px; }
.trans-md :deep(li) { margin: 2px 0; }
.trans-md :deep(strong) { color: var(--ink); }
.trans-md :deep(code) { padding: 1px 4px; border-radius: 3px; background: #f1f0ea; font-size: 10.5px; }
.trans-md :deep(h1), .trans-md :deep(h2), .trans-md :deep(h3), .trans-md :deep(h4) { margin: 6px 0 3px; font-size: 12px; }

/* 词条释义：按场景列义项（参考有道词典） */
.sense-list { display: flex; flex-direction: column; gap: 7px; }
.sense { min-width: 0; }
.sense + .sense { padding-top: 7px; border-top: 1px dashed var(--line-soft); }
.sense-head { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
.sense-meaning { font-size: 12.5px; font-weight: 650; color: var(--ink); }
.sense-pos {
  padding: 0 4px;
  border-radius: 3px;
  background: var(--blue-soft);
  color: var(--blue-ink);
  font-size: 9.5px;
  line-height: 15px;
}
.sense-tag { font-size: 9.5px; color: var(--amber-ink); }
.sense-scene { margin: 3px 0 0; font-size: 12px; line-height: 1.55; color: var(--ink-2); }
.sense-egs { margin: 4px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 2px; }
.sense-egs li { display: flex; align-items: baseline; gap: 5px; font-size: 12px; line-height: 1.55; color: var(--ink-2); }
.sense-egs li::before {
  content: "例：";
  flex: none;
  color: var(--ink-2);
  font-size: 10.5px;
}
.eg-src { color: var(--ink); font-weight: 500; }
.eg-arrow { color: #a8a49a; font-size: 10.5px; }
.eg-dst { color: var(--ink); font-weight: 600; }

/* 句子讲解：标签 + 一句话 */
.point-list { display: flex; flex-direction: column; gap: 3px; }
.point { display: flex; gap: 6px; font-size: 11.5px; line-height: 1.6; }
.point-label { flex: none; font-size: 11px; font-weight: 650; color: var(--blue-ink); }
.point-text { color: var(--ink-2); }

.trans-tip {
  margin: 7px 0 0;
  padding: 5px 7px;
  border-radius: 5px;
  background: #f5f6fa;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--muted);
}
.trans-empty { margin-top: 8px; font-size: 11px; color: var(--muted); }

/* ============ 代码仓库折叠条（FR-GIT-06） ============ */
/* 紧凑展示，不参与 flex 弹性伸缩：只占自然高度，剩余空间仍由项目卡吃掉 */
.repos-card {
  flex: none;
  display: flex;
  flex-direction: column;
}
.repos-card .card-head { padding-bottom: 10px; }
.repos-title-ic {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--violet-soft);
  color: var(--violet);
  display: grid;
  place-items: center;
  flex: none;
}
.repos-dirty-pill {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--amber-ink);
  background: rgba(250, 240, 220, 0.5);
  border: 1px solid var(--amber-line);
  padding: 1px 7px;
  border-radius: 999px;
  white-space: nowrap;
}

.repos-skeleton {
  padding: 12px 18px 14px;
  font-size: 11.5px;
  color: var(--muted);
}

.repos-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 18px 14px;
  font-size: 11.5px;
  color: var(--muted);
}
.repos-empty-ic { color: #a6a29a; display: grid; place-items: center; }

/* 仓库横向滚动条：紧凑卡片横排，超出水平滚动 */
.repos-strip {
  display: flex;
  gap: 9px;
  padding: 0 18px 14px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
}
.repos-strip::-webkit-scrollbar { height: 8px; }
.repos-strip::-webkit-scrollbar-thumb { background: rgba(180, 177, 168, 0.5); border-radius: 4px; }

.repo-chip {
  flex: none;
  min-width: 168px;
  max-width: 220px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 9px 11px 10px;
  border: 1px solid rgba(231, 229, 221, 0.8);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.24);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.12s, background 0.12s;
}
.repo-chip:hover { background: rgba(255, 255, 255, 0.4); border-color: var(--blue-line); }
.repo-chip.missing { opacity: 0.6; }
.repo-chip-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.repo-state-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: #c8c4b9; }
.repo-chip.st-ok .repo-state-dot { background: var(--green); }
.repo-chip.st-warn .repo-state-dot { background: var(--amber); }
.repo-chip.st-err .repo-state-dot { background: var(--red); }
.repo-chip.st-missing .repo-state-dot { background: var(--muted); }
.repo-chip-name {
  font-size: 12.5px;
  font-weight: 620;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.repo-chip-branch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--ink-2);
  background: rgba(242, 241, 236, 0.7);
  border-radius: 5px;
  padding: 1px 6px;
  white-space: nowrap;
  overflow: hidden;
  max-width: 100%;
}
.repo-chip-branch svg { color: var(--muted); flex: none; }
.repo-chip-branch code { font-family: var(--mono); font-size: 10.5px; }
.repo-chip-state { font-size: 10.5px; font-weight: 500; color: var(--ink-2); }
.repo-chip.st-warn .repo-chip-state { color: var(--amber-ink); }
.repo-chip.st-err .repo-chip-state { color: var(--red); }
.repo-chip.st-missing .repo-chip-state { color: var(--muted); }

/* ============ 项目列表 ============ */
/* 客户分组默认折叠：卡片头保持常规大小，只有分组体按 expanded 展开 */
.projects-card .card-head { padding-bottom: 10px; }
.projects-card .card-title { margin-right: auto; }
.project-groups { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; overflow-y: auto; }
.pgroup + .pgroup { border-top: 1px solid var(--line-soft); }
.pgroup-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 18px;
  border: 0;
  background: rgba(250, 249, 245, 0.32);
  color: var(--ink-2);
  text-align: left;
}
.pgroup-head:hover { background: rgba(244, 243, 237, 0.48); }
.pgroup-caret { color: var(--muted); font-size: 10px; width: 10px; flex: none; }
.customer-avatar {
  width: 22px;
  height: 22px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 650;
  flex: none;
  background: color-mix(in srgb, var(--av) 13%, white);
  color: var(--av);
}
.pgroup-name { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.pgroup-count {
  font-size: 11px;
  color: var(--ink-2);
  background: rgba(239, 238, 232, 0.72);
  padding: 1px 6px;
  border-radius: 999px;
}
/* 分组头的「最近更新」是扫读这一列时最先看的信息；分组头底色很透(0.32)，
   原来的 10.5px 浅灰压在壁纸上几乎读不出来，提到与其它列表日期列一档 */
.pgroup-latest {
  margin-left: auto;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.pgroup-body { display: flex; flex-direction: column; }
.project-row {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 10px 18px 10px 21px;
  border-top: 1px solid rgba(244, 243, 238, 0.9);
  cursor: pointer;
  transition: background 0.12s;
  min-width: 0;
}
.project-row:hover { background: rgba(248, 249, 251, 0.45); }
.project-row .p-dot { width: 7px; height: 7px; border-radius: 50%; background: #c8c4b9; }
.project-row .p-dot.active { background: var(--blue); }
.project-row .p-dot.stale { background: var(--amber); box-shadow: 0 0 0 3px rgba(250, 178, 25, 0.15); }
.project-row .p-dot.archived { background: #bab6ab; }
.p-main { min-width: 0; }
.p-title-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; min-width: 0; }
.p-title {
  font-size: 13.5px;
  font-weight: 620;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.p-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 11.5px; color: var(--ink-2); margin-top: 3px; }
.p-open { display: grid; place-items: center; width: 18px; color: #b7b3a9; font-size: 15px; }

/* ============ 异常 / 加载 ============ */
.error-card { padding: 20px 22px; display: flex; flex-direction: column; gap: 6px; }
.error-title { font-size: 14px; font-weight: 650; color: var(--red); }
.error-msg { font-size: 12.5px; color: var(--ink-2); }
.error-actions { display: flex; gap: 8px; margin-top: 6px; }
.loading-card { padding: 40px; text-align: center; color: var(--muted); font-size: 13px; }

/* 矮窗口（≈900 及以下的笔记本视口）：收紧纵向留白，
   保证「看板 + 项目卡」仍能一屏放下，项目卡底边继续与知识助手底边齐平 */
@media (max-height: 880px) {
  .page-home { gap: 10px; }
  .dash-greet { padding: 11px 18px 9px; }
  .stat-row { gap: 8px; padding-bottom: 10px; }
  .stat-tile { padding: 9px 12px; gap: 1px; }
  .dash-grid { height: 200px; }
  .dash-card.tr-open .trans-body { max-height: 172px; }
  .hbar-list { gap: 8px; padding: 7px 2px 2px; }
  .hbar-row { gap: 7px; }
  .trans-block { margin: 0 18px 10px; padding-top: 9px; }
  .trans-input textarea { min-height: 58px; }
  .projects-card { min-height: 176px; }
}

@media (max-width: 1100px) {
  .dash-grid { grid-template-columns: 1fr; }
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
