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
 * - follow-ups ← /api/dashboard/follow-ups（FR-DASH-04/06 时效与待跟进）
 * - 项目列表 ← /api/projects（FR-PROJ-01，一项目一笔记）
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])
import { dashboardApi, eventsSocket, projectsApi } from '@/api'
import GlobalAsk from '@/components/GlobalAsk.vue'
import { useConfigStore } from '@/stores/config'
import { USER_NAME } from '@/utils/profile'

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

/** 时效提醒：未更新天数倒序（越久越靠前，FR-DASH-04）。 */
const agingList = computed(() => [...followUps.value].sort((a, b) => b.days - a.days))

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
  // 外部修改 → note.changed → 自动刷新（NFR-PERF-04：轻量防抖，不实时全量扫描）
  eventsSocket.on('note.changed', () => {
    window.clearTimeout(refreshTimer)
    refreshTimer = window.setTimeout(refreshAll, 1500)
  })
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.clearTimeout(refreshTimer)
  if (clockTimer) clearInterval(clockTimer)
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
      <section class="card dash-card">
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

        <!-- 全局提问（从顶栏移入看板：位于时效提醒上方，顺手就能问当前看板看到的东西） -->
        <div class="ask-slot">
          <GlobalAsk inline />
        </div>

        <!-- 时效提醒（FR-DASH-04） -->
        <div class="aging-block">
          <div class="aging-head">
            <h3><span class="aging-ic">⏱</span>时效提醒</h3>
            <span>
              平均处理 {{ summary.avg_handle_days ?? '—' }} 天 · 阈值 {{ summary.follow_up_days }} 天
            </span>
          </div>
          <div v-if="agingList.length" class="aging-list">
            <button v-for="f in agingList" :key="f.path" class="aging-row" @click="openNote(f.path)">
              <span class="aging-days">{{ f.days }} 天</span>
              <span class="aging-main">
                <strong>{{ f.customer || '（未知客户）' }} · {{ f.project_name || '（未命名项目）' }}</strong>
                <em>最近更新 {{ fullDate(f.updated_date) }} · 状态进行中</em>
              </span>
              <span class="aging-go">›</span>
            </button>
          </div>
          <div v-else class="aging-empty">
            <span class="ok-mark">✓</span> 所有进行中项目都在 {{ summary.follow_up_days }} 天内有过更新
          </div>
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
  /* 内容不足一屏时撑满主区：项目卡被拉到底，底边与右侧知识助手面板齐平 */
  min-height: 100%;
}
.dash-card { flex: none; }
/* 项目卡吃掉剩余高度，内部列表滚动、底部状态栏钉在卡片底 */
.projects-card {
  flex: 1 1 auto;
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
  font-size: 11.5px;
  color: var(--muted);
}

.stat-row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; padding: 0 18px 14px; }
.stat-tile {
  border: 1px solid var(--line);
  border-radius: 11px;
  background: #fcfcfa;
  padding: 11px 13px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.stat-tile.warn { background: #fffaf0; border-color: var(--amber-line); }
.stat-label { font-size: 11px; color: var(--ink-2); display: flex; align-items: center; }
.stat-value { font-size: 27px; font-weight: 650; letter-spacing: -0.5px; line-height: 1.25; color: var(--ink); }
.stat-tile.warn .stat-value { color: var(--amber-ink); }
.stat-sub { font-size: 10px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.dash-grid { display: grid; grid-template-columns: 1.5fr 1fr; gap: 10px; padding: 0 18px 12px; }
.chart-card {
  border: 1px solid var(--line-soft);
  border-radius: 11px;
  background: #fcfcfb;
  padding: 11px 13px 8px;
  min-width: 0;
  /* 定位上下文：卡内空态 .chart-empty 是 inset:0 绝对定位，缺这行会以 .main 为基准铺满主区 */
  position: relative;
}
.chart-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
.chart-head h3 { font-size: 12.5px; font-weight: 600; margin: 0 0 1px; }
.chart-head span { font-size: 10.5px; color: var(--muted); }
.trend-box { position: relative; }
.trend-wrap { width: 100%; height: 200px; margin-top: 2px; }
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

.hbar-list { display: flex; flex-direction: column; gap: 11px; padding: 10px 2px 4px; }
.hbar-row { display: grid; grid-template-columns: 64px 1fr 52px; align-items: center; gap: 8px; }
.hbar-name { font-size: 11.5px; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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
  font-size: 10.5px;
  font-weight: 600;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}
.hbar-open { font-size: 10px; color: var(--muted); text-align: right; white-space: nowrap; }

/* 全局提问（看板内嵌，时效提醒上方） */
.ask-slot { padding: 0 18px 10px; }

/* 时效提醒 */
.aging-block { margin: 2px 18px 16px; padding-top: 12px; border-top: 1px solid var(--line-soft); }
.aging-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 6px; }
.aging-head h3 { font-size: 12.5px; font-weight: 600; margin: 0; display: flex; align-items: center; gap: 5px; }
.aging-ic { color: var(--amber-ink); }
.aging-head > span { font-size: 10.5px; color: var(--muted); }
.aging-list { display: flex; flex-direction: column; gap: 1px; max-height: 232px; overflow-y: auto; }
.aging-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 9px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 8px;
  text-align: left;
  width: 100%;
}
.aging-row:hover { background: #fffaf0; border-color: var(--amber-line); }
.aging-days {
  flex: none;
  min-width: 46px;
  text-align: center;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--amber-ink);
  background: var(--amber-soft);
  border: 1px solid var(--amber-line);
  border-radius: 5px;
  padding: 2px 5px;
  font-variant-numeric: tabular-nums;
}
.aging-main { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.aging-main strong { font-size: 12px; font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.aging-main em { font-style: normal; font-size: 10px; color: var(--muted); }
.aging-go { margin-left: auto; color: #b7b3a9; font-size: 15px; }
.aging-empty { display: flex; align-items: center; gap: 7px; padding: 8px 4px 4px; font-size: 11.5px; color: var(--muted); }
.ok-mark { color: var(--green); }

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
  background: #faf9f5;
  color: var(--ink-2);
  text-align: left;
}
.pgroup-head:hover { background: #f4f3ed; }
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
.pgroup-count { font-size: 10.5px; color: var(--muted); background: #efeee8; padding: 1px 6px; border-radius: 999px; }
.pgroup-latest { margin-left: auto; font-size: 10.5px; color: var(--muted); }
.pgroup-body { display: flex; flex-direction: column; }
.project-row {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 10px 18px 10px 21px;
  border-top: 1px solid #f4f3ee;
  cursor: pointer;
  transition: background 0.12s;
  min-width: 0;
}
.project-row:hover { background: #f8f9fb; }
.project-row .p-dot { width: 7px; height: 7px; border-radius: 50%; background: #c8c4b9; }
.project-row .p-dot.active { background: var(--blue); }
.project-row .p-dot.stale { background: var(--amber); box-shadow: 0 0 0 3px rgba(250, 178, 25, 0.15); }
.project-row .p-dot.archived { background: #bab6ab; }
.p-main { min-width: 0; }
.p-title-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; min-width: 0; }
.p-title {
  font-size: 13.5px;
  font-weight: 560;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.p-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 10.5px; color: var(--muted); margin-top: 3px; }
.p-open { display: grid; place-items: center; width: 18px; color: #b7b3a9; font-size: 15px; }

/* ============ 异常 / 加载 ============ */
.error-card { padding: 20px 22px; display: flex; flex-direction: column; gap: 6px; }
.error-title { font-size: 14px; font-weight: 650; color: var(--red); }
.error-msg { font-size: 12.5px; color: var(--ink-2); }
.error-actions { display: flex; gap: 8px; margin-top: 6px; }
.loading-card { padding: 40px; text-align: center; color: var(--muted); font-size: 13px; }

@media (max-width: 1100px) {
  .dash-grid { grid-template-columns: 1fr; }
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
