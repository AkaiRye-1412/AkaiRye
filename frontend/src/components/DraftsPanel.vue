<script setup lang="ts">
/**
 * 草稿箱面板（右侧 dock，与知识助手同一个位置，互斥展示）。
 *
 * 一张自动保存的"大纸"：停笔 0.8s 写盘，收起时强制 flush；
 * 历史速记从右上「层叠」按钮展开，点一条即切过去，删除需二次确认。
 * 纸面是单栏「即时渲染」：写成什么样就直接显示成什么样（LiveMarkdown 组件）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useDraftsStore } from '@/stores/drafts'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'
import LiveMarkdown from '@/components/LiveMarkdown.vue'

const drafts = useDraftsStore()
const ui = useUiStore()
const { content, items, currentPath, savedAt, saving, error, loading } = storeToRefs(drafts)

const historyOpen = ref(false)
const confirmPath = ref<string | null>(null)
const editorEl = ref<InstanceType<typeof LiveMarkdown> | null>(null)

/** 占位提示（换行写在脚本里，避免模板属性里 &#10; 被解码成真换行） */
const placeholderText =
  '随手写点什么…\n\n# 标题   **加粗**   - 列表   > 引用\n写成什么样就显示成什么样，停笔自动保存。'

const count = computed(() => content.value.length)

function hhmm(ms: number): string {
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 列表时间：今天只显示时分，跨天补月日，跨年补年份 */
function stamp(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const hm = hhmm(d.getTime())
  if (d.toDateString() === now.toDateString()) return hm
  const md = `${d.getMonth() + 1}-${d.getDate()}`
  if (d.getFullYear() === now.getFullYear()) return `${md} ${hm}`
  return `${d.getFullYear()}-${md} ${hm}`
}

const statusText = computed(() => {
  if (saving.value) return '保存中…'
  if (error.value) return error.value
  if (savedAt.value) return `已保存 ${hhmm(savedAt.value)}`
  if (!content.value.trim()) return '空白页 · 开始写会自动保存'
  return '待保存'
})

onMounted(async () => {
  await drafts.openPanel()
  await nextTick()
  editorEl.value?.focus()
})

/** 被知识助手顶掉/切页导致卸载时，把防抖里还没写盘的最后一句话补上 */
onBeforeUnmount(() => {
  void drafts.flush()
})

async function onNew() {
  historyOpen.value = false
  await drafts.newSheet()
  ui.toast('info', '已换一张空白纸，上一页已存进历史')
  await nextTick()
  editorEl.value?.focus()
}

async function pick(path: string) {
  historyOpen.value = false
  confirmPath.value = null
  try {
    await drafts.switchTo(path)
    await nextTick()
    editorEl.value?.focus()
  } catch (e) {
    ui.toast('error', `打开草稿失败：${(e as Error).message}`)
  }
}

async function remove(path: string) {
  try {
    await drafts.removeDraft(path)
    confirmPath.value = null
    ui.toast('success', '草稿已删除')
  } catch (e) {
    ui.toast('error', `删除失败：${(e as Error).message}`)
  }
}

function toggleHistory() {
  historyOpen.value = !historyOpen.value
  confirmPath.value = null
  // 每次展开历史都拉一次最新（别的窗口/磁盘上新增的速记也能立刻看到）
  if (historyOpen.value) void drafts.syncList()
}

async function close() {
  await drafts.closePanel()
}
</script>

<template>
  <div class="draft-panel">
    <header class="draft-head">
      <div class="draft-head-top">
        <span class="draft-title">
          <Icon name="memo" :size="15" />
          草稿箱
        </span>
        <div class="head-right">
          <button
            class="icon-btn"
            :class="{ active: historyOpen }"
            title="历史速记"
            @click.stop="toggleHistory"
          >
            <Icon name="layers" :size="16" />
          </button>
          <button class="icon-btn" title="新建一页" @click.stop="onNew">
            <Icon name="plus" :size="16" />
          </button>
          <button class="icon-btn" title="收起面板（Alt+2）" @click.stop="close">
            <Icon name="chevronRight" :size="16" />
          </button>
        </div>
      </div>

      <div class="draft-meta">
        <span class="status" :class="{ err: !!error, ok: !!savedAt && !error && !saving }">
          <span v-if="saving" class="spin-dot" />
          {{ statusText }}
        </span>
        <span class="count">{{ count }} 字</span>
      </div>

      <!-- 历史速记：浮层，不挤压纸面 -->
      <div v-if="historyOpen" class="history" @click.stop>
        <p v-if="loading" class="hist-empty">加载中…</p>
        <p v-else-if="!items.length" class="hist-empty">还没有历史速记，写几个字就会自动存一条。</p>
        <div
          v-for="it in items"
          :key="it.path"
          class="hist-item"
          :class="{ active: it.path === currentPath }"
        >
          <button class="hist-main" @click="pick(it.path)">
            <span class="hist-title">{{ it.title }}</span>
            <span class="hist-sub">
              <span class="hist-time">{{ stamp(it.updated_at) }}</span>
              <span v-if="it.preview" class="hist-preview">{{ it.preview }}</span>
            </span>
          </button>
          <div class="hist-actions">
            <button
              v-if="confirmPath !== it.path"
              class="hist-del"
              title="删除这条草稿"
              @click.stop="confirmPath = it.path"
            >
              <Icon name="trash" :size="13" />
            </button>
            <template v-else>
              <button class="hist-confirm" @click.stop="remove(it.path)">删除</button>
              <button class="hist-cancel" @click.stop="confirmPath = null">取消</button>
            </template>
          </div>
        </div>
      </div>
    </header>

    <div class="draft-body">
      <LiveMarkdown
        ref="editorEl"
        v-model="content"
        :placeholder="placeholderText"
        @save="drafts.flush()"
      />
    </div>
  </div>
</template>

<style scoped>
.draft-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  width: 100%;
  /* 底色交给外层 .rag-dock（半透明磨砂），面板自身透明才能透出壁纸 */
  background: transparent;
}

/* ---- 头部 ---- */
.draft-head {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--line-soft);
  flex: none;
}
.draft-head-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.draft-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13.5px;
  font-weight: 650;
  letter-spacing: -0.1px;
  color: var(--ink);
}
.draft-title :deep(svg) {
  color: var(--blue);
}
.head-right {
  display: flex;
  align-items: center;
  gap: 2px;
}
.icon-btn.active {
  background: var(--blue-soft);
  color: var(--blue-ink);
}

.draft-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11.5px;
  color: var(--muted);
  min-height: 15px;
}
.status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status.ok {
  color: #2f7a52;
}
.status.err {
  color: #b3443c;
}
.spin-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--blue);
  animation: pulse 1.2s ease-in-out infinite;
  flex: none;
}
@keyframes pulse {
  0%,
  100% {
    opacity: 0.25;
  }
  50% {
    opacity: 1;
  }
}
.count {
  flex: none;
  font-variant-numeric: tabular-nums;
}

/* ---- 历史浮层 ---- */
.history {
  position: absolute;
  top: calc(100% - 4px);
  left: 8px;
  right: 8px;
  z-index: 45;
  max-height: 340px;
  overflow: auto;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  box-shadow: var(--shadow-pop);
}
.hist-empty {
  margin: 0;
  padding: 10px 9px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
}
.hist-item {
  display: flex;
  align-items: center;
  gap: 4px;
  border-radius: 8px;
  padding: 2px 3px 2px 0;
}
.hist-item:hover {
  background: #f6f6f1;
}
.hist-item.active {
  background: var(--blue-soft);
}
.hist-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  border: 0;
  background: transparent;
  text-align: left;
  border-radius: 8px;
}
.hist-title {
  font-size: 12.5px;
  font-weight: 550;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hist-sub {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
  min-width: 0;
}
.hist-time {
  flex: none;
  font-variant-numeric: tabular-nums;
}
.hist-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hist-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: none;
}
.hist-del {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--muted);
}
.hist-del:hover {
  background: #f0e6e4;
  color: #b3443c;
}
.hist-confirm,
.hist-cancel {
  height: 24px;
  padding: 0 7px;
  border-radius: 7px;
  border: 1px solid var(--line);
  background: #fff;
  font-size: 11.5px;
  color: var(--ink-2);
}
.hist-confirm {
  border-color: #e0b6b2;
  color: #b3443c;
}
.hist-confirm:hover {
  background: #fdf2f1;
}

/* ---- 纸面容器（渲染与排版样式在 LiveMarkdown 组件里） ---- */
.draft-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 10px 12px 12px;
}
</style>
