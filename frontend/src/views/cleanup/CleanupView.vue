<script setup lang="ts">
/**
 * M7 存量笔记整理（FR-CLEAN-01~06）：
 *
 * 三步向导：扫描存量 → 预览确认 → 写入完成（可一键回滚）。
 * - 扫描口径是「五个头部字段缺任意一个」，不是 note_file.head_ok（卡片只要求 name，
 *   会把缺 project/status 的卡片判为完整，正好漏掉整理对象）；
 * - 客户沿用 frontmatter name；项目按 project → 文件名 → 正文 H1 推断；识别不到留空；
 * - 默认状态「已归档」，避免把历史笔记误标成进行中；
 * - 写入只改 frontmatter，正文一字不动；执行后端自动打包备份 + 存原文快照；
 * - 预览表的修改 1s 防抖自动存为草稿，离开页面再回来可继续；写入时再整体提交一次。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  cleanupApi,
  type CleanupBatch,
  type CleanupExecResult,
  type CleanupItem,
  type CleanupItemInput,
  type CleanupSummary,
} from '@/api'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'

const ui = useUiStore()
const router = useRouter()

const summary = ref<CleanupSummary | null>(null)
const batches = ref<CleanupBatch[]>([])
const batch = ref<CleanupBatch | null>(null)
const items = ref<CleanupItem[]>([])
const result = ref<CleanupExecResult | null>(null)

const stage = ref<'intro' | 'preview' | 'done'>('intro')
const includeAll = ref(false)
const busy = ref<'' | 'scan' | 'exec' | 'cancel' | 'rollback' | 'del'>('')
const loadError = ref('')
const saveState = ref<'' | 'dirty' | 'saving' | 'saved' | 'error'>('')

const stageIdx = computed(() => ({ intro: 0, preview: 1, done: 2 })[stage.value])

const willWrite = computed(
  () => items.value.filter((i) => !i.skip && i.customer.trim() && i.project_name.trim()).length,
)
const skippedCount = computed(() => items.value.filter((i) => i.skip).length)
const unresolvedCount = computed(
  () =>
    items.value.filter((i) => !i.skip && (!i.customer.trim() || !i.project_name.trim())).length,
)

const saveLabel = computed(() => {
  if (saveState.value === 'saving') return '保存中…'
  if (saveState.value === 'saved') return '草稿已保存'
  if (saveState.value === 'error') return '草稿保存失败'
  if (saveState.value === 'dirty') return '未保存'
  return ''
})

const FIELD_LABEL: Record<string, string> = {
  name: '客户',
  project: '项目',
  status: '状态',
  start_date: '开始日期',
  updated: '更新时间',
}

const STATUS_LABEL: Record<CleanupBatch['status'], string> = {
  draft: '待写入',
  done: '已写入',
  rolled_back: '已回滚',
  failed: '失败',
}

/* ---------------- 数据加载 ---------------- */

async function refreshSide() {
  const [s, bs] = await Promise.all([cleanupApi.summary(), cleanupApi.batches()])
  summary.value = s
  batches.value = bs
}

function applyBatch(b: CleanupBatch) {
  hydrating = true
  batch.value = b
  items.value = b.items ?? []
  stage.value = 'preview'
  saveState.value = 'saved'
  void nextTick(() => {
    hydrating = false
  })
}

async function openBatch(id: number, silent = false) {
  try {
    const b = await cleanupApi.get(id)
    applyBatch(b)
    if (!silent) ui.toast('info', `已载入批次 #${id}，可继续修改`)
  } catch (e) {
    ui.toast('error', `载入批次失败：${(e as Error).message}`)
  }
}

async function loadPage() {
  loadError.value = ''
  try {
    const [s, bs] = await Promise.all([cleanupApi.summary(), cleanupApi.batches()])
    summary.value = s
    batches.value = bs
    // 有未写入的草稿：直接恢复到预览（用户可能是刷新 / 切页回来）
    const draft = bs.find((b) => b.status === 'draft')
    if (draft) await openBatch(draft.id, true)
  } catch (e) {
    loadError.value = (e as Error).message
  }
}

/* ---------------- 草稿自动保存 ---------------- */

let saveTimer: number | null = null
let hydrating = false

function toInput(i: CleanupItem): CleanupItemInput {
  return {
    path: i.path,
    customer: i.customer.trim(),
    project_name: i.project_name.trim(),
    status: i.status,
    start_date: i.start_date,
    skip: i.skip,
  }
}

async function persistDraft() {
  const b = batch.value
  if (!b || b.status !== 'draft' || busy.value) return
  saveState.value = 'saving'
  try {
    await cleanupApi.saveItems(b.id, items.value.map(toInput))
    saveState.value = 'saved'
  } catch {
    saveState.value = 'error'
  }
}

watch(
  items,
  () => {
    if (hydrating || stage.value !== 'preview' || !batch.value || busy.value) return
    saveState.value = 'dirty'
    if (saveTimer !== null) window.clearTimeout(saveTimer)
    saveTimer = window.setTimeout(() => {
      saveTimer = null
      void persistDraft()
    }, 1000)
  },
  { deep: true, flush: 'sync' },
)

function cancelSaveTimer() {
  if (saveTimer !== null) {
    window.clearTimeout(saveTimer)
    saveTimer = null
  }
}

/* ---------------- 扫描 / 预览操作 ---------------- */

async function scan() {
  if (busy.value) return
  if (stage.value === 'preview' && items.value.length) {
    if (!window.confirm('重新扫描会丢弃当前预览里的修改，继续？')) return
  }
  cancelSaveTimer()
  busy.value = 'scan'
  try {
    const b = await cleanupApi.create(includeAll.value ? 'all' : 'missing')
    if (!b.items?.length) {
      await cleanupApi.remove(b.id)
      batch.value = null
      items.value = []
      stage.value = 'intro'
      ui.toast('success', '所有笔记的头部字段都已完整，没有需要整理的内容')
    } else {
      applyBatch(b)
      ui.toast('info', `扫描完成：${b.items.length} 篇待整理`)
    }
    await refreshSide()
  } catch (e) {
    ui.toast('error', `扫描失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

function skipUnresolved() {
  let n = 0
  for (const i of items.value) {
    if (!i.customer.trim() || !i.project_name.trim()) {
      if (!i.skip) n += 1
      i.skip = true
    }
  }
  if (n) ui.toast('info', `已跳过 ${n} 篇未识别的笔记`)
  else ui.toast('info', '没有未识别的笔记')
}

/* ---------------- 状态下拉（自绘） ---------------- */

/**
 * 原生 <select> 的弹层由浏览器绘制，在本工作台的预览面板（iframe / 缩放）里
 * 会飘到窗口左上角，所以状态改成自绘下拉；同一时刻只允许开一个。
 */
const STATUS_OPTIONS = [
  { value: 'archived', label: '已归档' },
  { value: 'active', label: '进行中' },
] as const

const openStatus = ref<string | null>(null)
const statusUp = ref(false)

function statusText(v: string) {
  return v === 'active' ? '进行中' : '已归档'
}

function toggleStatusMenu(path: string, ev: MouseEvent) {
  if (openStatus.value === path) {
    openStatus.value = null
    return
  }
  openStatus.value = path
  statusUp.value = false
  const btn = ev.currentTarget as HTMLElement
  void nextTick(() => {
    const menu = btn.parentElement?.querySelector<HTMLElement>('.cl-dd-menu')
    if (!menu) return
    // 预览卡 overflow:hidden 会裁掉超出卡片的弹层，下方空间不够就向上翻
    const card = btn.closest('.cl-preview') ?? document.body
    const room = card.getBoundingClientRect().bottom - btn.getBoundingClientRect().bottom
    statusUp.value = room < menu.offsetHeight + 10
  })
}

function pickStatus(it: CleanupItem, value: 'active' | 'archived') {
  it.status = value
  openStatus.value = null
}

function onDocClick(e: MouseEvent) {
  if (openStatus.value && !(e.target as HTMLElement | null)?.closest('.cl-dd')) {
    openStatus.value = null
  }
}

function setAllStatus(status: 'active' | 'archived') {
  let n = 0
  for (const i of items.value) {
    if (i.skip) continue
    if (i.status !== status) n += 1
    i.status = status
  }
  ui.toast('info', `已把 ${n} 篇的状态改为「${status === 'archived' ? '已归档' : '进行中'}」`)
}

async function cancelDraft() {
  const b = batch.value
  if (!b) return
  if (!window.confirm('放弃这次预览？已补全的字段不会被写入。')) return
  cancelSaveTimer()
  busy.value = 'cancel'
  try {
    await cleanupApi.remove(b.id)
    batch.value = null
    items.value = []
    result.value = null
    stage.value = 'intro'
    await refreshSide()
    ui.toast('info', '已放弃本次预览')
  } catch (e) {
    ui.toast('error', `取消失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

/* ---------------- 执行 / 回滚 ---------------- */

async function execute() {
  const b = batch.value
  if (!b || busy.value) return
  if (unresolvedCount.value) {
    ui.toast('warn', `还有 ${unresolvedCount.value} 篇缺少客户或项目名，请补全或跳过`)
    return
  }
  if (!willWrite.value) {
    ui.toast('info', '没有需要写入的笔记')
    return
  }
  const ok = window.confirm(
    `将为 ${willWrite.value} 篇笔记写入头部字段（跳过 ${skippedCount.value} 篇）。\n\n` +
      '只修改 frontmatter：客户 / 项目 / 状态 / 开始日期 / 更新时间；正文一字不动。\n' +
      '执行前会自动打包备份，可一键回滚。',
  )
  if (!ok) return

  cancelSaveTimer()
  busy.value = 'exec'
  try {
    const r = await cleanupApi.execute(b.id, items.value.map(toInput))
    result.value = r
    batch.value = r.batch
    stage.value = 'done'
    await refreshSide()
    const msg = `已写入 ${r.batch.applied} 篇，跳过 ${r.batch.skipped} 篇${
      r.batch.failed ? `，失败 ${r.batch.failed} 篇` : ''
    }`
    ui.toast(r.batch.failed ? 'warn' : 'success', msg)
  } catch (e) {
    ui.toast('error', `写入失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

async function rollback(id: number) {
  if (busy.value) return
  const ok = window.confirm(
    '回滚到整理前？\n\n' +
      '会用执行前的备份还原这些笔记（正文不受影响）。\n' +
      '整理之后又被外部编辑过的笔记会跳过，不会覆盖你的新内容。',
  )
  if (!ok) return
  busy.value = 'rollback'
  try {
    const r = await cleanupApi.rollback(id)
    ui.toast(
      r.partial ? 'warn' : 'success',
      r.partial
        ? `已还原 ${r.restored} 篇；${r.skipped.length + r.errors.length} 篇跳过（整理后被外部修改或文件已不存在）`
        : `已回滚 ${r.restored} 篇，笔记恢复到整理前状态`,
    )
    batch.value = null
    items.value = []
    result.value = null
    stage.value = 'intro'
    await refreshSide()
  } catch (e) {
    ui.toast('error', `回滚失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

/* ---------------- 整理记录：删除 ---------------- */

const finishedBatches = computed(() => batches.value.filter((b) => b.status !== 'draft'))

/** 删掉的正好是当前页显示的批次时，退回第一步，别停在失效的结果页 */
function resetIfShowing(id: number) {
  if (batch.value?.id !== id) return
  cancelSaveTimer()
  batch.value = null
  items.value = []
  result.value = null
  stage.value = 'intro'
}

async function removeBatch(b: CleanupBatch) {
  if (busy.value) return
  const finished = b.status !== 'draft'
  const ok = window.confirm(
    finished
      ? `删除整理记录 #${b.id}？\n\n` +
          '已写入的笔记保持现状，不会跟着还原；但删掉这条记录后就不能再一键回滚了。\n' +
          '备份包仍保留在 data/backups，需要时可手动取回。'
      : `丢弃草稿 #${b.id}？\n\n预览里还没写入的修改会一起丢掉。`,
  )
  if (!ok) return
  busy.value = 'del'
  try {
    await cleanupApi.remove(b.id, finished)
    resetIfShowing(b.id)
    await refreshSide()
    ui.toast('success', `已删除整理记录 #${b.id}`)
  } catch (e) {
    ui.toast('error', `删除失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

async function clearHistory() {
  if (busy.value) return
  const n = finishedBatches.value.length
  if (!n) {
    ui.toast('info', '没有可清理的整理记录')
    return
  }
  const ok = window.confirm(
    `清空 ${n} 条整理记录？\n\n` +
      '笔记内容保持现状，不会跟着还原；清空后这些批次不能再一键回滚。\n' +
      '备份包仍保留在 data/backups；正在编辑的草稿不受影响。',
  )
  if (!ok) return
  busy.value = 'del'
  try {
    const r = await cleanupApi.clearHistory()
    if (batch.value && batch.value.status !== 'draft') resetIfShowing(batch.value.id)
    await refreshSide()
    ui.toast('success', `已清空 ${r.deleted} 条整理记录`)
  } catch (e) {
    ui.toast('error', `清空失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

/* ---------------- 展示辅助 ---------------- */

function missingText(it: CleanupItem): string {
  return it.missing.map((k) => FIELD_LABEL[k] ?? k).join('、')
}

function isResolved(it: CleanupItem): boolean {
  return Boolean(it.customer.trim() && it.project_name.trim())
}

function inferText(it: CleanupItem): string {
  if (!it.customer.trim() && !it.project_name.trim()) return '客户 / 项目未识别'
  if (!it.customer.trim()) return '客户未识别'
  if (!it.project_name.trim()) return '项目未识别'
  const src = it.sources.project ?? ''
  if (!src || src === '头部 project') return '已识别'
  return `项目←${src}`
}

/** 窄容器里只在「真推断过 / 没认出来」时显示识别提示，避免每行都挂一个「已识别」 */
function showInlineInfer(it: CleanupItem): boolean {
  if (!isResolved(it)) return true
  const src = it.sources.project ?? ''
  return Boolean(src) && src !== '头部 project'
}

function sourcesTitle(it: CleanupItem): string {
  return Object.entries(it.sources)
    .map(([k, v]) => `${FIELD_LABEL[k] ?? k}：${v}`)
    .join('；')
}

function fmtTime(s?: string | null): string {
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function baseName(p?: string | null): string {
  if (!p) return '—'
  return p.split(/[\\/]/).pop() || p
}

function batchActionLabel(b: CleanupBatch): string {
  return b.status === 'failed' ? '失败' : STATUS_LABEL[b.status]
}

function goNotes() {
  router.push('/notes')
}

onMounted(() => {
  void loadPage()
  document.addEventListener('click', onDocClick)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  openStatus.value = null
  // 离开时把未保存的编辑补交一次；请求失败也不影响页面离开
  if (saveTimer !== null) {
    window.clearTimeout(saveTimer)
    saveTimer = null
    if (saveState.value === 'dirty') void persistDraft()
  }
})
</script>

<template>
  <div class="page page-cleanup">
    <!-- ==================== 页头 + 步骤 ==================== -->
    <section class="card cl-hero">
      <header class="cl-hero-head">
        <div class="cl-hero-title">
          <span class="cl-hero-ic"><Icon name="scissors" :size="17" /></span>
          <div class="cl-hero-text">
            <h1>存量整理</h1>
            <p>补全旧笔记缺失的头部字段 · 只改 frontmatter，正文一字不动</p>
          </div>
        </div>
        <div class="cl-hero-meta">
          <span class="cl-meta">
            <b>{{ summary?.notes_total ?? '—' }}</b> 篇售后笔记
          </span>
          <span class="sep-dot" />
          <span class="cl-meta" :class="{ warn: (summary?.head_missing ?? 0) > 0 }">
            <b>{{ summary?.head_missing ?? '—' }}</b> 篇待整理
          </span>
          <button class="btn ghost sm" :disabled="!!busy" @click="scan">
            <Icon name="refresh" :size="13" :class="{ spin: busy === 'scan' }" />
            <span>重新扫描</span>
          </button>
        </div>
      </header>

      <ol class="cl-steps">
        <template
          v-for="(s, i) in [
            { t: '扫描存量', d: '识别缺失字段' },
            { t: '预览确认', d: '补全 / 跳过' },
            { t: '写入与回滚', d: '自动备份' },
          ]"
          :key="s.t"
        >
          <li class="cl-step" :class="{ active: stageIdx === i, done: stageIdx > i }">
            <span class="cl-step-no">
              <Icon v-if="stageIdx > i" name="checkCircle" :size="13" />
              <template v-else>{{ i + 1 }}</template>
            </span>
            <div class="cl-step-text">
              <strong>{{ s.t }}</strong>
              <em>{{ s.d }}</em>
            </div>
          </li>
          <li v-if="i < 2" class="cl-step-line" aria-hidden="true" />
        </template>
      </ol>
    </section>

    <!-- 配置未就绪 -->
    <section v-if="loadError" class="card cl-empty">
      <div class="empty-block">
        <span class="empty-ic"><Icon name="alert" :size="24" /></span>
        <p>存量整理暂时不可用</p>
        <span class="empty-sub">{{ loadError }}</span>
        <div class="cl-empty-actions">
          <button class="btn ghost sm" @click="loadPage">重试</button>
          <button class="btn ghost sm" @click="router.push('/settings')">去设置路径</button>
        </div>
      </div>
    </section>

    <!-- ==================== 第一步：扫描 ==================== -->
    <section v-else-if="stage === 'intro'" class="card cl-intro">
      <div class="cl-intro-body">
        <span class="cl-intro-ic"><Icon name="scissors" :size="26" /></span>
        <h2>扫描「{{ summary?.notes_folder || '售后笔记' }}」</h2>
        <p class="cl-intro-desc">
          找出缺头部字段的笔记。客户沿用现有 frontmatter 的 <code>name</code>；项目名按
          <code>project</code> → 文件名 → 正文标题推断；识别不到的留空，等你在预览里补。
        </p>
        <ul class="cl-points">
          <li><Icon name="checkCircle" :size="13" /> 只写头部字段，<strong>正文一字不动</strong></li>
          <li><Icon name="shield" :size="13" /> 执行前自动打包备份，可一键回滚</li>
          <li><Icon name="archive" :size="13" /> 缺状态的存量笔记默认标「已归档」，避免误标进行中</li>
        </ul>

        <label class="cl-scope">
          <input v-model="includeAll" type="checkbox" />
          <span>连头部已完整的笔记一起整理（用于批量改状态 / 纠正项目名）</span>
        </label>

        <div class="cl-intro-actions">
          <button class="btn primary" :disabled="!!busy" @click="scan">
            <Icon
              :name="busy === 'scan' ? 'refresh' : 'search'"
              :size="14"
              :class="{ spin: busy === 'scan' }"
            />
            <span>{{ busy === 'scan' ? '扫描中…' : '开始扫描' }}</span>
          </button>
          <span class="cl-hint">
            当前 {{ summary?.head_missing ?? 0 }} 篇缺字段 · 共
            {{ summary?.notes_total ?? 0 }} 篇 · 几十篇量级几秒完成
          </span>
        </div>
      </div>
    </section>

    <!-- ==================== 第二步：预览 ==================== -->
    <section v-else-if="stage === 'preview'" class="card cl-preview">
      <header class="cl-preview-head">
        <div class="cl-title">
          <h2>预览确认</h2>
          <span class="count-pill">{{ items.length }} 篇</span>
          <span v-if="unresolvedCount" class="badge warn">{{ unresolvedCount }} 项待补全</span>
        </div>
        <div class="cl-tools">
          <button class="btn ghost sm" :disabled="!!busy" @click="skipUnresolved">
            <Icon name="minusCircle" :size="13" /><span>跳过未识别</span>
          </button>
          <button class="btn ghost sm" :disabled="!!busy" @click="setAllStatus('archived')">
            <Icon name="archive" :size="13" /><span>全部已归档</span>
          </button>
          <button class="btn ghost sm" :disabled="!!busy" @click="setAllStatus('active')">
            <Icon name="clock" :size="13" /><span>全部进行中</span>
          </button>
        </div>
      </header>

      <div class="cl-table">
        <div class="cl-thead">
          <span>文件</span>
          <span>客户</span>
          <span>项目名</span>
          <span>状态</span>
          <span>开始日期</span>
          <span>识别</span>
          <span class="cl-th-skip">跳过</span>
        </div>

        <div
          v-for="it in items"
          :key="it.path"
          class="cl-row"
          :class="{
            skipped: it.skip,
            unresolved: !it.skip && !isResolved(it),
            'dd-open': openStatus === it.path,
          }"
        >
          <div class="cl-file">
            <Icon name="file" :size="13" class="cl-file-ic" />
            <span class="cl-fname" :title="it.path">{{ it.file_name }}</span>
            <span v-if="it.missing.length" class="cl-miss" :title="`缺失：${missingText(it)}`">
              缺 {{ it.missing.length }} 项
            </span>
            <span
              v-if="showInlineInfer(it)"
              class="cl-infer-inline"
              :class="{ bad: !isResolved(it) }"
              :title="sourcesTitle(it)"
            >
              <Icon :name="isResolved(it) ? 'checkCircle' : 'alert'" :size="11" />
              <span>{{ inferText(it) }}</span>
            </span>
          </div>

          <div class="cl-cell" data-label="客户">
            <input
              v-model="it.customer"
              class="cl-input"
              :class="{ bad: !it.customer.trim() }"
              list="cl-customers"
              placeholder="未识别，请填写"
              spellcheck="false"
              :disabled="it.skip"
            />
          </div>

          <div class="cl-cell" data-label="项目名">
            <input
              v-model="it.project_name"
              class="cl-input"
              :class="{ bad: !it.project_name.trim() }"
              placeholder="未识别，请填写"
              spellcheck="false"
              :disabled="it.skip"
            />
          </div>

          <div class="cl-cell" data-label="状态">
            <div class="cl-dd" :class="{ open: openStatus === it.path, up: statusUp }">
              <button
                type="button"
                class="cl-input cl-dd-btn"
                :class="{ open: openStatus === it.path }"
                :disabled="it.skip"
                :aria-expanded="openStatus === it.path"
                aria-haspopup="listbox"
                @click.stop="toggleStatusMenu(it.path, $event)"
                @keydown.esc.stop="openStatus = null"
              >
                <span class="cl-dd-dot" :class="it.status" />
                <span class="cl-dd-label">{{ statusText(it.status) }}</span>
                <svg
                  class="cl-dd-caret"
                  :class="{ open: openStatus === it.path }"
                  width="9"
                  height="9"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2.6"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                >
                  <path d="m6 9 6 6 6-6" />
                </svg>
              </button>

              <div v-if="openStatus === it.path" class="cl-dd-menu" role="listbox" @click.stop>
                <button
                  v-for="o in STATUS_OPTIONS"
                  :key="o.value"
                  type="button"
                  class="cl-dd-item"
                  :class="{ active: it.status === o.value }"
                  role="option"
                  :aria-selected="it.status === o.value"
                  @click="pickStatus(it, o.value)"
                >
                  <span class="cl-dd-dot" :class="o.value" />
                  <span class="cl-dd-item-label">{{ o.label }}</span>
                  <Icon v-if="it.status === o.value" name="checkCircle" :size="12" class="cl-dd-check" />
                </button>
              </div>
            </div>
          </div>

          <div class="cl-cell" data-label="开始日期">
            <input v-model="it.start_date" type="date" class="cl-input cl-date" :disabled="it.skip" />
          </div>

          <div class="cl-infer" data-label="识别">
            <Icon :name="isResolved(it) ? 'checkCircle' : 'alert'" :size="12" />
            <span :title="sourcesTitle(it)">{{ inferText(it) }}</span>
          </div>

          <div class="cl-skip">
            <label class="cl-check" :title="it.skip ? '取消跳过' : '这篇不整理'">
              <input v-model="it.skip" type="checkbox" />
              <span />
            </label>
          </div>
        </div>
      </div>

      <datalist id="cl-customers">
        <option v-for="c in summary?.customers ?? []" :key="c" :value="c" />
      </datalist>

      <div v-if="unresolvedCount" class="cl-warn">
        <Icon name="alert" :size="13" />
        <span>{{ unresolvedCount }} 篇还缺客户或项目名：补全后一起写入，或勾选「跳过」。</span>
        <button class="link-btn" @click="skipUnresolved">全部跳过</button>
      </div>

      <footer class="cl-foot">
        <div class="cl-foot-stats">
          <span>将写入 <b>{{ willWrite }}</b> 篇</span>
          <span class="sep-dot" />
          <span>跳过 {{ skippedCount }} 篇</span>
          <span class="sep-dot" />
          <span class="cl-foot-note"><Icon name="shield" :size="12" /> 执行前自动打包备份</span>
          <span v-if="saveLabel" class="cl-save" :class="saveState">{{ saveLabel }}</span>
        </div>
        <div class="cl-foot-actions">
          <button class="btn ghost sm" :disabled="!!busy" @click="cancelDraft">取消</button>
          <button class="btn primary sm" :disabled="!!busy || !willWrite" @click="execute">
            <Icon name="edit" :size="13" />
            <span>{{ busy === 'exec' ? '写入中…' : `写入 ${willWrite} 篇头部字段` }}</span>
          </button>
        </div>
      </footer>
    </section>

    <!-- ==================== 第三步：完成 ==================== -->
    <section v-else class="card cl-done">
      <div class="cl-done-body">
        <span class="cl-done-ic"><Icon name="checkCircle" :size="30" /></span>
        <h2>存量整理完成</h2>
        <p class="cl-done-desc">
          已为 <b>{{ result?.batch.applied ?? 0 }}</b> 篇笔记写入头部字段<template
            v-if="(result?.batch.skipped ?? 0) > 0"
            >，跳过 {{ result?.batch.skipped }} 篇</template
          ><template v-if="(result?.batch.failed ?? 0) > 0"
            >，<em class="cl-bad">{{ result?.batch.failed }} 篇失败</em></template
          >。正文内容未改变。
        </p>

        <div v-if="result?.batch.backup_path" class="cl-backup">
          <Icon name="shield" :size="13" />
          <span>执行前备份</span>
          <code :title="result.batch.backup_path">{{ baseName(result.batch.backup_path) }}</code>
        </div>

        <ul v-if="result?.errors.length" class="cl-errors">
          <li v-for="e in result.errors" :key="e.path">
            <code>{{ e.path }}</code><span>{{ e.error }}</span>
          </li>
        </ul>

        <div class="cl-done-actions">
          <button
            v-if="(result?.batch.applied ?? 0) > 0"
            class="btn ghost"
            :disabled="!!busy"
            @click="result && rollback(result.batch.id)"
          >
            <Icon name="clock" :size="14" />
            <span>{{ busy === 'rollback' ? '回滚中…' : '一键回滚' }}</span>
          </button>
          <button class="btn primary" @click="goNotes">
            <Icon name="notes" :size="14" /><span>完成，查看笔记</span>
          </button>
        </div>
      </div>
    </section>

    <!-- ==================== 整理记录 ==================== -->
    <section v-if="batches.length && !loadError" class="card cl-history">
      <header class="cl-history-head">
        <div class="cl-title">
          <h2>整理记录</h2>
          <span class="count-pill">{{ batches.length }}</span>
        </div>
        <div class="cl-history-tools">
          <span class="cl-history-hint">已写入的批次可随时回滚；草稿可继续预览</span>
          <button
            v-if="finishedBatches.length"
            class="btn ghost sm cl-hclear"
            :disabled="!!busy"
            @click="clearHistory"
          >
            <Icon name="eraser" :size="12" />
            <span>清空记录</span>
          </button>
        </div>
      </header>

      <div class="cl-hrows">
        <div v-for="b in batches" :key="b.id" class="cl-hrow">
          <span class="cl-hid">#{{ b.id }}</span>
          <span class="cl-htime">{{ fmtTime(b.created_at) }}</span>
          <span class="cl-badge" :class="b.status">{{ batchActionLabel(b) }}</span>
          <span class="cl-hcount">
            <template v-if="b.status === 'draft'">共 {{ b.total }} 篇待写入</template>
            <template v-else-if="b.status === 'rolled_back'">已还原 {{ b.restored }} 篇</template>
            <template v-else>
              写入 {{ b.applied }} · 跳过 {{ b.skipped
              }}<template v-if="b.failed"> · <em class="cl-bad">失败 {{ b.failed }}</em></template>
            </template>
          </span>
          <span class="cl-hfile" :title="b.backup_path || ''">{{ baseName(b.backup_path) }}</span>
          <div class="cl-hact">
            <button
              v-if="b.status === 'draft' && b.id !== batch?.id"
              class="btn ghost sm"
              :disabled="!!busy"
              @click="openBatch(b.id)"
            >
              继续预览
            </button>
            <span v-else-if="b.status === 'draft'" class="cl-hdone">当前预览</span>
            <button
              v-else-if="b.status === 'done' || (b.status === 'failed' && b.applied > 0)"
              class="btn ghost sm"
              :disabled="!!busy"
              @click="rollback(b.id)"
            >
              回滚
            </button>
            <span v-else-if="b.status === 'rolled_back'" class="cl-hdone">
              已回滚 · {{ fmtTime(b.rolled_back_at) }}
            </span>
            <span v-else class="cl-hdone">—</span>
            <button
              class="cl-hdel"
              :class="{ danger: b.status !== 'draft' }"
              :disabled="!!busy"
              :title="b.status === 'draft' ? '丢弃这份草稿' : '删除这条记录（笔记保持现状）'"
              @click="removeBatch(b)"
            >
              <Icon name="trash" :size="13" />
            </button>
          </div>
        </div>
      </div>
    </section>

    <p class="cl-foot-tip">
      <Icon name="info" :size="12" />
      整理只补前部字段，不重排目录、不改正文；文件冲突与索引仍走原有机制（保存后 8s 内自动增量索引）。
    </p>
  </div>
</template>

<style scoped>
.page-cleanup {
  display: flex;
  flex-direction: column;
  gap: 11px;
  container-type: inline-size;
  container-name: cleanuppage;
}

/* ---------------- 页头 ---------------- */
.cl-hero { padding: 15px 18px 13px; }
.cl-hero-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px 18px;
  flex-wrap: wrap;
}
.cl-hero-title { display: flex; align-items: center; gap: 9px; min-width: 0; }
.cl-hero-ic {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--violet-soft);
  color: var(--violet);
  display: grid;
  place-items: center;
  flex: none;
}
.cl-hero-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cl-hero-text h1 { margin: 0; font-size: 15px; font-weight: 650; letter-spacing: -0.1px; }
.cl-hero-text p { margin: 0; font-size: 10.5px; color: #6a675f; line-height: 1.45; }
.cl-hero-meta { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.cl-meta { font-size: 10.5px; color: #6a675f; white-space: nowrap; }
.cl-meta b {
  font-size: 13px;
  font-weight: 650;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  margin-right: 2px;
}
.cl-meta.warn b { color: var(--amber-ink); }

/* 步骤条 */
.cl-steps {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 13px 0 0;
  padding: 10px 2px 0;
  border-top: 1px solid var(--line-soft);
  list-style: none;
}
.cl-step { display: flex; align-items: center; gap: 7px; flex: 0 1 auto; min-width: 0; }
.cl-step-no {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 10.5px;
  font-weight: 600;
  background: #f2f1ec;
  color: var(--muted);
  flex: none;
}
.cl-step.active .cl-step-no { background: var(--blue); color: #fff; }
.cl-step.done .cl-step-no { background: var(--green-soft); color: var(--green-ink); }
.cl-step-text { display: flex; flex-direction: column; gap: 0; line-height: 1.25; min-width: 0; }
.cl-step-text strong {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cl-step.active .cl-step-text strong { color: var(--ink); }
.cl-step-text em {
  font-style: normal;
  font-size: 9.5px;
  color: #9d9a92;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cl-step-line { flex: 1; height: 1px; background: var(--line-soft); min-width: 14px; }

/* ---------------- 空 / 扫描 ---------------- */
.cl-empty { padding: 8px; }
.cl-empty-actions { display: flex; gap: 6px; margin-top: 4px; }
.cl-empty-actions { display: flex; gap: 6px; margin-top: 4px; }
.cl-intro { padding: 26px 20px 24px; }
.cl-intro-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 8px;
  max-width: 620px;
  margin: 0 auto;
}
.cl-intro-ic {
  width: 52px;
  height: 52px;
  border-radius: 15px;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #f4f2fd, #eae7fb);
  color: var(--violet);
  margin-bottom: 2px;
}
.cl-intro h2 { margin: 0; font-size: 16.5px; font-weight: 650; letter-spacing: -0.2px; }
.cl-intro-desc { margin: 0; font-size: 11.5px; line-height: 1.75; color: var(--ink-2); }
.cl-intro-desc code {
  font-family: var(--mono);
  font-size: 10.5px;
  background: #f2f1ec;
  border-radius: 4px;
  padding: 1px 4px;
}
.cl-points {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 5px 16px;
  margin: 4px 0 2px;
  padding: 0;
  list-style: none;
  font-size: 11px;
  color: var(--ink-2);
}
.cl-points li { display: inline-flex; align-items: center; gap: 4px; }
.cl-points svg { color: var(--green-ink); }
.cl-points li:nth-child(2) svg { color: var(--blue); }
.cl-points li:nth-child(3) svg { color: var(--muted); }
.cl-scope {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  color: #6a675f;
  margin-top: 4px;
  cursor: pointer;
}
.cl-scope input { accent-color: var(--blue); width: 13px; height: 13px; }
.cl-intro-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  flex-wrap: wrap;
  justify-content: center;
}
.cl-hint { font-size: 10px; color: var(--muted); }

/* ---------------- 预览 ---------------- */
.cl-preview { overflow: hidden; }
.cl-preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  flex-wrap: wrap;
  padding: 13px 16px 11px;
}
.cl-title { display: flex; align-items: center; gap: 7px; min-width: 0; }
.cl-title h2 { margin: 0; font-size: 14px; font-weight: 650; letter-spacing: -0.1px; }
.cl-tools { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.cl-tools .btn { gap: 3px; }

.cl-table { border-top: 1px solid var(--line-soft); }
.cl-thead,
.cl-row {
  display: grid;
  grid-template-columns:
    minmax(150px, 1.55fr) minmax(96px, 1fr) minmax(104px, 1.1fr)
    88px 116px minmax(84px, 0.9fr) 36px;
  gap: 7px;
  align-items: center;
  padding: 6px 16px;
}
.cl-thead {
  font-size: 9.5px;
  letter-spacing: 0.2px;
  color: var(--muted);
  background: rgba(247, 246, 242, 0.6);
  border-bottom: 1px solid var(--line-soft);
}
.cl-th-skip { text-align: center; }
.cl-row { border-bottom: 1px solid rgba(239, 238, 232, 0.75); transition: background 0.12s; }
.cl-row:last-child { border-bottom: 0; }
.cl-row:hover { background: rgba(255, 255, 255, 0.5); }
.cl-row.skipped { opacity: 0.5; }
.cl-row.unresolved { background: rgba(250, 178, 25, 0.055); }

.cl-file { display: flex; align-items: center; gap: 5px; min-width: 0; font-size: 11.5px; }
/* 窄容器下「识别」并入文件行：表格里的独立列会被开关挤到边角 */
.cl-infer-inline { display: none; align-items: center; gap: 3px; min-width: 0; font-size: 10px; color: var(--muted); }
.cl-infer-inline > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cl-infer-inline svg { flex: none; color: var(--green-ink); }
.cl-infer-inline.bad { color: var(--amber-ink); }
.cl-infer-inline.bad svg { color: var(--amber-ink); }
.cl-file-ic { color: #a6a29a; flex: none; }
.cl-fname {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
}
.cl-miss {
  flex: none;
  font-size: 9px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 4px;
  background: var(--amber-soft);
  color: var(--amber-ink);
  border: 1px solid var(--amber-line);
  white-space: nowrap;
}
.cl-cell { min-width: 0; }
.cl-input {
  width: 100%;
  height: 27px;
  padding: 0 7px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  font-size: 11px;
  color: var(--ink);
  font-family: inherit;
  min-width: 0;
}
.cl-input:focus {
  outline: none;
  border-color: var(--blue-line);
  box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.1);
}
.cl-input.bad { border-color: var(--amber-line); background: #fffdf6; }
.cl-input:disabled { background: #f7f6f1; color: var(--muted); }
/* 状态下拉：自绘弹层（原生 select 的弹层会飘到窗口左上角） */
.cl-dd { position: relative; }
.cl-dd-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 6px;
  text-align: left;
  cursor: pointer;
}
.cl-dd-btn.open {
  border-color: var(--blue-line);
  box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.1);
}
.cl-dd-btn:disabled { cursor: not-allowed; }
.cl-dd-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cl-dd-caret {
  flex: none;
  color: var(--muted);
  transition: transform 0.15s;
}
.cl-dd-caret.open { transform: rotate(180deg); }
.cl-dd-dot {
  flex: none;
  width: 5px;
  height: 5px;
  border-radius: 50%;
}
.cl-dd-dot.archived { background: var(--green); }
.cl-dd-dot.active { background: var(--blue); }
.cl-dd-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 40;
  width: max(100%, 108px);
  padding: 3px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 10px 28px rgba(23, 24, 26, 0.14);
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.cl-dd.up .cl-dd-menu {
  top: auto;
  bottom: calc(100% + 4px);
}
.cl-dd-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 7px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-size: 11.5px;
  font-family: inherit;
  color: var(--ink-2);
  text-align: left;
  cursor: pointer;
}
.cl-dd-item:hover { background: #f6f5f0; }
.cl-dd-item.active { color: var(--ink); font-weight: 600; }
.cl-dd-item-label { flex: 1; }
.cl-dd-check { flex: none; color: var(--violet); }
/* 弹层要盖住后面的行 */
.cl-row.dd-open { position: relative; z-index: 12; }
.cl-date { font-variant-numeric: tabular-nums; }
.cl-date::-webkit-calendar-picker-indicator { opacity: 0.45; cursor: pointer; }

.cl-infer {
  display: flex;
  align-items: center;
  gap: 3px;
  min-width: 0;
  font-size: 10px;
  color: var(--muted);
}
.cl-infer span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cl-infer svg { flex: none; }
.cl-row.unresolved .cl-infer { color: var(--amber-ink); }
.cl-row:not(.unresolved) .cl-infer svg { color: var(--green-ink); }

.cl-skip { display: flex; justify-content: center; }
.cl-check { display: inline-flex; cursor: pointer; }
.cl-check input { position: absolute; opacity: 0; pointer-events: none; }
.cl-check span {
  width: 27px;
  height: 15px;
  border-radius: 999px;
  background: #e2e0d8;
  position: relative;
  transition: background 0.15s;
  flex: none;
}
.cl-check span::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(23, 24, 26, 0.2);
  transition: transform 0.15s;
}
.cl-check input:checked + span { background: var(--amber); }
.cl-check input:checked + span::after { transform: translateX(12px); }
.cl-check input:focus-visible + span { box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.15); }

.cl-warn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  border-top: 1px solid var(--amber-line);
  background: rgba(255, 245, 220, 0.66);
  font-size: 10.5px;
  color: var(--amber-ink);
}
.cl-warn svg { flex: none; }
.cl-warn .link-btn { margin-left: auto; color: var(--amber-ink); font-size: 10.5px; }

.cl-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px 14px;
  flex-wrap: wrap;
  padding: 10px 16px;
  border-top: 1px solid var(--line-soft);
  background: rgba(252, 252, 251, 0.55);
}
.cl-foot-stats {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  font-size: 10.5px;
  color: #6a675f;
}
.cl-foot-stats b { color: var(--ink); font-weight: 650; font-variant-numeric: tabular-nums; }
.cl-foot-note { display: inline-flex; align-items: center; gap: 3px; color: var(--muted); }
.cl-save { font-size: 9.5px; color: var(--muted); }
.cl-save.dirty { color: var(--amber-ink); }
.cl-save.error { color: var(--red); }
.cl-foot-actions { display: flex; align-items: center; gap: 6px; margin-left: auto; }

/* ---------------- 完成 ---------------- */
.cl-done { padding: 28px 20px 24px; }
.cl-done-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 8px;
  max-width: 620px;
  margin: 0 auto;
}
.cl-done-ic {
  width: 54px;
  height: 54px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--green-soft);
  color: var(--green-ink);
}
.cl-done h2 { margin: 0; font-size: 16.5px; font-weight: 650; }
.cl-done-desc { margin: 0; font-size: 11.5px; color: var(--ink-2); line-height: 1.7; }
.cl-done-desc b { color: var(--ink); font-variant-numeric: tabular-nums; }
.cl-bad { color: var(--red); font-style: normal; }
.cl-backup {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 5px 9px;
  border-radius: 7px;
  background: rgba(234, 242, 253, 0.92);
  border: 1px solid var(--blue-line);
  font-size: 10px;
  color: var(--blue-ink);
  font-weight: 500;
}
.cl-backup code {
  font-family: var(--mono);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cl-errors {
  width: 100%;
  margin: 2px 0 0;
  padding: 8px 10px;
  list-style: none;
  border: 1px solid var(--red-line);
  background: var(--red-soft);
  border-radius: 8px;
  font-size: 10px;
  color: var(--red);
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.cl-errors li { display: flex; gap: 6px; min-width: 0; }
.cl-errors code {
  font-family: var(--mono);
  flex: none;
  max-width: 48%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cl-done-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
  justify-content: center;
}

/* ---------------- 记录 ---------------- */
.cl-history { overflow: hidden; }
.cl-history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 16px 10px;
}
.cl-history-tools { display: flex; align-items: center; gap: 8px; min-width: 0; }
.cl-history-hint { font-size: 10px; color: var(--muted); }
.cl-hclear { flex: none; gap: 3px; font-size: 11px; padding: 0 8px; height: 24px; }
.cl-hrows { border-top: 1px solid var(--line-soft); }
.cl-hrow {
  display: grid;
  grid-template-columns: 40px 118px 62px minmax(120px, 1fr) minmax(90px, 1fr) auto;
  gap: 8px;
  align-items: center;
  padding: 7px 16px;
  border-bottom: 1px solid rgba(239, 238, 232, 0.75);
  font-size: 10.5px;
  color: #6a675f;
}
.cl-hrow:last-child { border-bottom: 0; }
.cl-hid { font-family: var(--mono); color: var(--muted); }
.cl-htime { font-variant-numeric: tabular-nums; white-space: nowrap; }
.cl-hcount { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cl-hfile {
  font-family: var(--mono);
  font-size: 9.5px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cl-hact { display: flex; align-items: center; justify-content: flex-end; gap: 4px; }
.cl-hdone { font-size: 9.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cl-hdel {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #a6a29a;
  cursor: pointer;
  transition: color 0.12s, background 0.12s, border-color 0.12s;
}
.cl-hdel:hover:not(:disabled) { color: var(--ink-2); background: #f2f1ec; border-color: var(--line); }
.cl-hdel.danger:hover:not(:disabled) {
  color: var(--red);
  background: var(--red-soft);
  border-color: var(--red-line);
}
.cl-hdel:disabled { opacity: 0.45; cursor: not-allowed; }
.cl-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9.5px;
  line-height: 1;
  padding: 3px 5px;
  border-radius: 5px;
  white-space: nowrap;
  border: 1px solid transparent;
}
.cl-badge.draft { background: var(--amber-soft); color: var(--amber-ink); border-color: var(--amber-line); }
.cl-badge.done { background: var(--green-soft); color: var(--green-ink); border-color: var(--green-line); }
.cl-badge.rolled_back { background: #f2f1ec; color: var(--muted); border-color: var(--line-soft); }
.cl-badge.failed { background: var(--red-soft); color: var(--red); border-color: var(--red-line); }

.cl-foot-tip {
  display: flex;
  align-items: center;
  align-self: flex-start;
  gap: 5px;
  margin: 0;
  padding: 5px 9px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid rgba(231, 229, 221, 0.6);
  font-size: 10px;
  color: #6a675f;
  line-height: 1.6;
}

.spin { animation: cl-spin 1s linear infinite; }
@keyframes cl-spin {
  to { transform: rotate(360deg); }
}

/* ---------------- 窄容器：表格转两行卡片 ---------------- */
@container cleanuppage (max-width: 860px) {
  .cl-thead { display: none; }
  .cl-steps { gap: 6px; }
  .cl-step-text em { display: none; }
  .cl-step-text strong { font-size: 11px; }
  .cl-step-line { min-width: 6px; }

  /* 一行文件 + 识别，一行四个字段：比 2×2 少一半高度 */
  .cl-row {
    grid-template-columns: minmax(0, 1.15fr) minmax(0, 1.15fr) 84px 108px;
    grid-template-rows: auto auto;
    gap: 6px 8px;
    padding: 9px 14px 10px;
    position: relative;
  }
  .cl-row > .cl-file { grid-column: 1 / -1; grid-row: 1; padding-right: 44px; }
  .cl-row > .cl-infer { display: none; }
  .cl-infer-inline { display: inline-flex; }
  .cl-row > .cl-cell:nth-child(2) { grid-column: 1; grid-row: 2; }
  .cl-row > .cl-cell:nth-child(3) { grid-column: 2; grid-row: 2; }
  .cl-row > .cl-cell:nth-child(4) { grid-column: 3; grid-row: 2; }
  .cl-row > .cl-cell:nth-child(5) { grid-column: 4; grid-row: 2; }
  .cl-cell::before {
    content: attr(data-label);
    display: block;
    font-size: 9px;
    color: #7d7a72;
    margin-bottom: 2px;
    letter-spacing: 0.1px;
  }
  .cl-skip { position: absolute; top: 10px; right: 13px; }

}

/* 记录行单独一档：主区 700 以下折成两行，避免“时间/状态/数量”各占一行 */
@container cleanuppage (max-width: 720px) {
  .cl-hrow {
    grid-template-columns: 34px 108px 58px minmax(0, 1fr) auto;
    grid-template-rows: auto auto;
    gap: 3px 8px;
    padding: 8px 14px;
  }
  .cl-hid { grid-column: 1; grid-row: 1; }
  .cl-htime { grid-column: 2; grid-row: 1; }
  .cl-badge { grid-column: 3; grid-row: 1; justify-self: start; }
  .cl-hact { grid-column: 5; grid-row: 1; }
  .cl-hcount { grid-column: 1 / 4; grid-row: 2; }
  .cl-hfile { grid-column: 4 / 6; grid-row: 2; text-align: right; }
}

@container cleanuppage (max-width: 560px) {
  .cl-row {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    grid-template-rows: auto auto auto auto;
  }
  .cl-row > .cl-file { grid-column: 1 / -1; grid-row: 1; }
  .cl-row > .cl-infer { display: none; }
  .cl-row > .cl-cell:nth-child(2) { grid-column: 1; grid-row: 2; }
  .cl-row > .cl-cell:nth-child(3) { grid-column: 2; grid-row: 2; }
  .cl-row > .cl-cell:nth-child(4) { grid-column: 1; grid-row: 3; }
  .cl-row > .cl-cell:nth-child(5) { grid-column: 2; grid-row: 3; }
  /* 2×2 下日期格只剩一格宽，日历图标会把最后一位数字挤掉 */
  .cl-date { padding-right: 3px; }
  .cl-date::-webkit-calendar-picker-indicator { display: none; }
}
</style>
