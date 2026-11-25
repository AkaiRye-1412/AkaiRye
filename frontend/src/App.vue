<script setup lang="ts">
/**
 * 应用外壳（对齐 prototype/index.html）：
 * 左侧个人信息卡 + 导航 + Vault 卡 → 顶栏（页面标题 / 全局提问 / 终端 / 新建笔记）
 * → 主区（路由视图）+ 右侧 C1 RAG 常驻面板（Q-02：所有页面常驻）。
 */
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { eventsSocket } from '@/api/ws'
import { useConfigStore } from '@/stores/config'
import { useRagStore } from '@/stores/rag'
import { useUiStore } from '@/stores/ui'
import GlobalAsk from '@/components/GlobalAsk.vue'
import Icon from '@/components/Icon.vue'
import RagPanel from '@/rag/RagPanel.vue'
import { USER_NAME } from '@/utils/profile'

const route = useRoute()
const router = useRouter()
const configStore = useConfigStore()
const rag = useRagStore()
const ui = useUiStore()

const NAV_MAIN = [
  { to: '/', label: '首页', icon: 'home', name: 'home' },
  { to: '/notes', label: '售后笔记', icon: 'notes', name: 'notes' },
  { to: '/kb', label: '知识库', icon: 'book', name: 'kb' },
  { to: '/terminal', label: '终端', icon: 'terminal', name: 'terminal' },
  { to: '/repos', label: '代码仓库', icon: 'git', name: 'repos' },
  { to: '/settings', label: '设置', icon: 'settings', name: 'settings' },
]
const NAV_UTIL = [
  { to: '/cleanup', label: '存量整理', icon: 'scissors', name: 'cleanup' },
  { to: '/pdf', label: 'PDF 手册', icon: 'file', name: 'pdf' },
]

const PAGE_NAMES: Record<string, string> = {
  home: '首页台面',
  notes: '售后笔记',
  kb: '知识库',
  pdf: 'PDF 手册',
  terminal: '终端',
  repos: '代码仓库',
  cleanup: '存量整理',
  settings: '系统设置',
}

const pageName = computed(() => PAGE_NAMES[String(route.name)] ?? '首页台面')
const vaultPath = computed(() => configStore.config?.paths?.vault_root || '未配置 vault 路径')
const vaultOk = computed(() => !!configStore.status?.vault_root_valid)
const configOk = computed(() => !!configStore.status?.config_complete)
const indexedCount = computed(() => configStore.status?.counts?.notes ?? 0)

// ---- 全局提问（FR-HOME-02）：由 GlobalAsk 组件承载，Ctrl+K 聚焦 ----
// 首页把提问框移到看板内（时效提醒上方），顶栏在首页不再重复展示。
const isHome = computed(() => route.name === 'home')

function newNote() {
  router.push({ path: '/notes', query: { new: '1' } })
}

onMounted(() => {
  eventsSocket.connect()
  // 重连后 REST 全量对账（api-rules 第 4 节：推送不作唯一数据来源）
  eventsSocket.onReconnected(() => configStore.load())
  configStore.load()
})

/**
 * 上下文随页面走：离开笔记页即清空（否则"当前笔记/客户"范围会静默沿用上一篇笔记，
 * 表现为"切了范围却查不到/查到的不是眼前的东西"）。
 */
watch(
  () => route.path,
  (p) => {
    if (!p.startsWith('/notes') && (rag.context.notePath || rag.context.customer || rag.context.project)) {
      rag.clearContext()
    }
  },
)
</script>

<template>
  <div class="app">
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
        <div class="vault-card">
          <div class="vault-card-top">
            <Icon name="database" :size="14" />
            <span>Obsidian Vault</span>
            <em :class="{ warn: !vaultOk }">{{ vaultOk ? '监听中' : '未连接' }}</em>
          </div>
          <div class="vault-path" :title="vaultPath">{{ vaultPath }}</div>
          <div class="vault-meta">
            <span class="dot" :class="vaultOk ? 'ok' : 'warn'" />
            已索引 {{ indexedCount }} 篇
          </div>
        </div>
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
          <button class="side-link" @click="ui.toast('info', 'Ctrl+K 聚焦全局提问')">
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

        <GlobalAsk v-if="!isHome" />

        <div class="topbar-actions">
          <button class="btn ghost" @click="router.push('/terminal')">
            <Icon name="terminal" :size="15" /><span class="btn-label">终端</span>
          </button>
          <button class="btn primary" @click="newNote">
            <Icon name="plus" :size="15" /><span class="btn-label">新建笔记</span>
          </button>
        </div>
      </header>

      <div class="workspace">
        <!-- 首页铺满到窗口底（与知识助手面板底边齐平），故首页不保留主区底部内边距 -->
        <main class="main" :class="{ 'main-flush': isHome }">
          <router-view />
        </main>
        <aside class="rag-dock" :class="{ collapsed: !rag.panelOpen }">
          <RagPanel v-if="rag.panelOpen" />
          <button v-else class="rag-rail" title="展开知识助手" @click="rag.panelOpen = true">
            <Icon name="sparkle" :size="17" />
            <span class="rail-label">知识助手</span>
          </button>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
.btn-label { font-size: 13px; }
</style>
