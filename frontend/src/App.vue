<script setup lang="ts">
/**
 * 应用外壳（对齐 prototype/index.html）：
 * 左侧个人信息卡 + 导航 + Vault 卡 → 顶栏（页面标题 / 终端 / 新建笔记）
 * → 主区（路由视图）+ 右侧 C1 RAG 常驻面板（Q-02：所有页面常驻）。
 */
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { eventsSocket } from '@/api/ws'
import { useConfigStore } from '@/stores/config'
import { useDraftsStore } from '@/stores/drafts'
import { useRagStore } from '@/stores/rag'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'
import DraftsPanel from '@/components/DraftsPanel.vue'
import RagPanel from '@/rag/RagPanel.vue'
import { USER_NAME } from '@/utils/profile'

const route = useRoute()
const configStore = useConfigStore()
const rag = useRagStore()
const drafts = useDraftsStore()
const ui = useUiStore()

const NAV_MAIN = [
  { to: '/', label: '首页', icon: 'home', name: 'home' },
  { to: '/notes', label: '售后笔记', icon: 'notes', name: 'notes' },
  { to: '/memos', label: '个人笔记', icon: 'memo', name: 'memos' },
  { to: '/kb', label: '知识库', icon: 'book', name: 'kb' },
  { to: '/terminal', label: '终端', icon: 'terminal', name: 'terminal' },
  { to: '/repos', label: '项目开发', icon: 'git', name: 'repos' },
  { to: '/settings', label: '设置', icon: 'settings', name: 'settings' },
]
const NAV_UTIL = [
  { to: '/cleanup', label: '存量整理', icon: 'scissors', name: 'cleanup' },
  { to: '/pdf', label: 'PDF 手册', icon: 'file', name: 'pdf' },
]

const PAGE_NAMES: Record<string, string> = {
  home: '首页台面',
  notes: '售后笔记',
  memos: '个人笔记',
  kb: '知识库',
  pdf: 'PDF 手册',
  terminal: '终端',
  repos: '项目开发',
  cleanup: '存量整理',
  settings: '系统设置',
}

const pageName = computed(() => PAGE_NAMES[String(route.name)] ?? '首页台面')
const configOk = computed(() => !!configStore.status?.config_complete)

// 首页/终端铺满到窗口底（与知识助手面板底边齐平），主区不保留底部内边距。
// 终端页必须铺满：.main 默认的 padding+overflow:auto 会压缩终端可视高度并叠加外层
// 滚动条，导致 xterm fit 出的 rows 偏小、提示符贴底、滚动条行为异常（与 xterm 内层
// 滚动条冲突）。终端铺满后 .term-page 精确填满可视高度，fit 才能算准。
const isHome = computed(() => route.name === 'home' || route.name === 'terminal')

/**
 * 右侧 dock 只有一个槽位：知识助手与草稿箱互斥，开一个必须收另一个。
 * （互斥同时用 watch 兜底：全局提问 / 快捷键等任何来源打开知识助手，草稿箱都会让位。）
 */
function openDock(which: 'rag' | 'drafts') {
  if (which === 'rag') {
    drafts.panelOpen = false
    rag.panelOpen = true
  } else {
    rag.panelOpen = false
    drafts.panelOpen = true
  }
}
watch(
  () => rag.panelOpen,
  (open) => {
    if (open) drafts.panelOpen = false
  },
)
watch(
  () => drafts.panelOpen,
  (open) => {
    if (open) rag.panelOpen = false
  },
)

/**
 * 设置页改了「草稿箱索引位置」：先把当前这张纸存回旧目录，再换成空白纸。
 * 否则面板还指着旧目录，之后每次写盘都会被后端按"路径越出草稿箱目录"拒掉。
 * （首次读到配置只记基线，不搬动纸面——那时面板正在决定显示哪张纸。）
 */
const draftRoot = computed(() => configStore.config?.paths?.draft_roots?.[0] ?? '')
let knownDraftRoot: string | null = null
watch(
  [() => configStore.loaded, draftRoot] as const,
  ([loaded, now]) => {
    if (!loaded) return
    if (knownDraftRoot === null) {
      knownDraftRoot = now
      return
    }
    if (knownDraftRoot !== now) {
      knownDraftRoot = now
      void drafts.relocate()
    }
  },
  { immediate: true },
)

onMounted(() => {
  eventsSocket.connect()
  // 重连后 REST 全量对账（api-rules 第 4 节：推送不作唯一数据来源）
  eventsSocket.onReconnected(() => configStore.load())
  configStore.load()
  // 刷新前停在草稿箱：知识助手默认展开的状态要让位（否则两个都算"打开"）
  if (drafts.panelOpen) rag.panelOpen = false
})

/**
 * 上下文随页面走：离开笔记页即清空（否则"当前笔记/客户"范围会静默沿用上一篇笔记，
 * 表现为"切了范围却查不到/查到的不是眼前的东西"）。
 */
watch(
  () => route.path,
  (p) => {
    if (
      !p.startsWith('/notes') &&
      (rag.context.notePath || rag.context.noteTitle || rag.context.customer || rag.context.project)
    ) {
      rag.clearContext()
    }
  },
)
</script>

<template>
  <div class="app">
    <!-- 背景壁纸：底层图片虚化，上层蒙层压住亮度以保证浅色主题可读性（样式见 main.css） -->
    <div class="app-bg" aria-hidden="true" />
    <div class="app-scrim" aria-hidden="true" />

    <!-- 左侧：个人信息 / 导航 / Vault 状态 -->
    <aside class="sidebar">
      <div class="profile-card">
        <div class="profile-head">
          <span class="profile-title">个人信息</span>
          <span class="profile-status" :class="{ warn: !configOk }">
            <span class="dot" :class="configOk ? 'ok' : 'warn'" />{{ configOk ? '本机' : '待配置' }}
          </span>
        </div>
        <div class="profile-body">
          <div class="profile-avatar">
            <span class="avatar-fallback">售</span>
            <img
              src="/avatar.jpg"
              alt="个人头像"
              @error="(e) => ((e.target as HTMLImageElement).style.display = 'none')"
            />
          </div>
          <div class="profile-detail">
            <div class="profile-name-line">
              <strong>{{ USER_NAME }}</strong>
              <span>售后工作台 · 本地</span>
            </div>
          </div>
        </div>
      </div>

      <nav class="nav">
        <router-link v-for="item in NAV_MAIN" :key="item.name" class="nav-item" :to="item.to">
          <span class="nav-ic"><Icon :name="item.icon" :size="17" /></span>
          <span class="nav-label">{{ item.label }}</span>
        </router-link>
        <div class="nav-sep" />
        <router-link
          v-for="item in NAV_UTIL"
          :key="item.name"
          class="nav-item nav-util"
          :to="item.to"
        >
          <span class="nav-ic"><Icon :name="item.icon" :size="16" /></span>
          <span class="nav-label">{{ item.label }}</span>
        </router-link>
      </nav>

      <div class="sidebar-foot">
        <div class="side-links">
          <button
            class="side-link"
            @click="
              ui.toast(
                'info',
                '笔记切块内容会发送至 embedding 厂商，问答命中片段会发送至 LLM 厂商（已确认的数据外传边界）',
              )
            "
          >
            数据外传说明
          </button>
          <button class="side-link" @click="ui.toast('info', '编辑器中 Ctrl+S 保存笔记')">
            快捷键
          </button>
        </div>
      </div>
    </aside>

    <!-- 右侧：顶栏 + 主区 + RAG 常驻面板 -->
    <div class="shell">
      <header class="topbar">
        <div class="topbar-title">
          <span class="crumb">售后工作台</span>
          <span class="page-name">{{ pageName }}</span>
        </div>

        <div class="topbar-actions">
        </div>
      </header>

      <div class="workspace">
        <!-- 首页铺满到窗口底（与知识助手面板底边齐平），故首页不保留主区底部内边距 -->
        <main class="main" :class="{ 'main-flush': isHome }">
          <router-view />
        </main>
        <aside class="rag-dock" :class="{ collapsed: !rag.panelOpen && !drafts.panelOpen }">
          <RagPanel v-if="rag.panelOpen" />
          <DraftsPanel v-else-if="drafts.panelOpen" />
          <div v-else class="dock-rail">
            <button class="rag-rail" title="展开知识助手" @click="openDock('rag')">
              <Icon name="sparkle" :size="17" />
              <span class="rail-label">知识助手</span>
            </button>
            <button class="rag-rail" title="展开草稿箱" @click="openDock('drafts')">
              <Icon name="memo" :size="17" />
              <span class="rail-label">草稿箱</span>
            </button>
          </div>
        </aside>
      </div>
    </div>

    <!-- 全局轻提示：ui.toast() 的渲染宿主（此前只写状态、无处渲染，导致提示静默丢失） -->
    <div v-if="ui.toasts.length" class="toast-host" aria-live="polite">
      <div v-for="t in ui.toasts" :key="t.id" class="toast" :class="t.kind">
        <span class="toast-dot" />{{ t.message }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.btn-label { font-size: 13px; }
</style>
