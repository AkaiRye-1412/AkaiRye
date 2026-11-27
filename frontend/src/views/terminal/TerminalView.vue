<script setup lang="ts">
/**
 * 终端（M5 / FR-TERM · 三期）：xterm.js 多标签 + ConPTY 真实 shell。
 *
 * 设计要点：
 * - 每标签一条 WS（/ws/terminal/{tab_id}），PTY 进程随标签常驻（后端），刷新/重连后 attach 回同一进程；
 * - 标签元数据（cwd/标题）持久化后端 terminal_tab 表，进入页面时 REST 全量对账；
 * - 内置命令（FR-TERM-02）：以 / 开头走后端拦截，回显 builtin 帧；
 * - 关闭运行中标签二次确认（FR-TERM-07）：DELETE 带 force（前端先 confirm）。
 */
import { computed, onBeforeUnmount, onMounted, ref, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { terminalApi, type TerminalTab } from '@/api'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'

const ui = useUiStore()

interface TabRuntime {
  tab: TerminalTab
  term: Terminal
  fit: FitAddon
  ws: WebSocket | null
  reconnectTimer: number | null
  alive: boolean
  closed: boolean
  opened: boolean
}

const tabs = ref<TabRuntime[]>([])
const activeId = ref<string | null>(null)
const loading = ref(true)
// 每个标签的 DOM 容器（tab_id → element）
const hostEls = new Map<string, HTMLElement>()
// 关闭确认弹窗：用自定义 UI 替代 window.confirm（webview 中 confirm 可能被禁用/静默返回 false）
const confirmTarget = ref<TabRuntime | null>(null)

const active = computed(() => tabs.value.find((t) => t.tab.tab_id === activeId.value) ?? null)

// ---------- 初始化 ----------

async function loadTabs() {
  try {
    const list = await terminalApi.list()
    tabs.value = list.map((tab) => buildRuntime(tab))
    // 首次进入无标签时，自动新建一个默认标签（默认 cwd 为用户主目录）
    if (!tabs.value.length) {
      await newTab(undefined, true)
    }
    if (!activeId.value && tabs.value.length) {
      activeId.value = tabs.value[0].tab.tab_id
    }
  } catch (e) {
    ui.toast('error', `加载终端标签失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

function buildRuntime(tab: TerminalTab): TabRuntime {
  const term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'Cascadia Code, Consolas, monospace',
    theme: {
      background: '#141518',
      foreground: '#d7d6d2',
      cursor: '#2a78d6',
      cursorAccent: '#141518',
      selectionBackground: 'rgba(42,120,214,0.35)',
    },
    scrollback: 5000,
    convertEol: true,
  })
  const fit = new FitAddon()
  term.loadAddon(fit)
  term.onData((data) => {
    if (rt.ws && rt.ws.readyState === WebSocket.OPEN) {
      rt.ws.send(JSON.stringify({ type: 'input', data }))
    }
  })
  const rt: TabRuntime = {
    tab,
    term,
    fit,
    ws: null,
    reconnectTimer: null,
    alive: tab.alive,
    closed: false,
    opened: false,
  }
  return rt
}

function wsUrl(tabId: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/terminal/${tabId}`
}

// ---------- 标签激活 / 附着 ----------

async function activate(rt: TabRuntime) {
  activeId.value = rt.tab.tab_id
  await nextTick()
  if (!rt.opened) {
    const host = hostEls.get(rt.tab.tab_id)
    if (host) {
      rt.term.open(host)
      rt.opened = true
    }
  }
  // 只有 xterm 已 open 到 DOM 时才做 fit/focus；未 open（如首次自动建标签、
  // host 尚未渲染）时交给 onHostMounted 处理，避免对未初始化的 xterm 操作
  if (rt.opened) {
    rt.fit.fit()
    rt.term.focus()
  }
  ensureWs(rt)
}

function ensureWs(rt: TabRuntime) {
  if (rt.closed || (rt.ws && rt.ws.readyState <= WebSocket.OPEN)) return
  if (rt.reconnectTimer !== null) {
    clearTimeout(rt.reconnectTimer)
    rt.reconnectTimer = null
  }
  let ws: WebSocket
  try {
    ws = new WebSocket(wsUrl(rt.tab.tab_id))
  } catch {
    return
  }
  rt.ws = ws
  ws.onopen = () => {
    rt.alive = true
  }
  ws.onmessage = (ev) => {
    let frame: { type: string; data?: string; cwd?: string }
    try {
      frame = JSON.parse(ev.data)
    } catch {
      return
    }
    switch (frame.type) {
      case 'output':
        rt.term.write(frame.data ?? '')
        break
      case 'builtin':
        rt.term.write(frame.data ?? '')
        if (frame.cwd) rt.tab.cwd = frame.cwd
        break
      case 'cwd':
        if (frame.cwd) rt.tab.cwd = frame.cwd
        break
      case 'exit':
        rt.alive = false
        break
      case 'error':
        rt.term.write(`\r\n\x1b[31m[错误] ${frame.data ?? ''}\x1b[0m\r\n`)
        break
      default:
        break
    }
  }
  ws.onclose = () => {
    rt.alive = false
    rt.ws = null
    // 自动重连（PTY 常驻，重连后附着回同一进程）
    if (!rt.closed && activeId.value === rt.tab.tab_id) {
      rt.reconnectTimer = window.setTimeout(() => ensureWs(rt), 1000)
    }
  }
  ws.onerror = () => {
    ws.close()
  }
}

function teardownWs(rt: TabRuntime) {
  if (rt.reconnectTimer !== null) {
    clearTimeout(rt.reconnectTimer)
    rt.reconnectTimer = null
  }
  if (rt.ws) {
    rt.ws.onclose = null
    rt.ws.close()
    rt.ws = null
  }
}

// ---------- 标签操作 ----------

async function newTab(cwd?: string, silent = false) {
  try {
    const tab = await terminalApi.create({ cwd })
    const rt = buildRuntime(tab)
    tabs.value.push(rt)
    await activate(rt)
    if (!silent) ui.toast('success', '已新建终端标签')
  } catch (e) {
    ui.toast('error', `新建标签失败：${(e as Error).message}`)
  }
}

function closeTab(rt: TabRuntime) {
  // 运行中标签先弹自定义确认框（避免 window.confirm 在 webview 中不可靠）
  if (rt.alive) {
    confirmTarget.value = rt
    return
  }
  doClose(rt)
}

async function doClose(rt: TabRuntime) {
  try {
    await terminalApi.close(rt.tab.tab_id, true)
  } catch (e) {
    ui.toast('error', `关闭标签失败：${(e as Error).message}`)
    return
  }
  rt.closed = true
  teardownWs(rt)
  rt.term.dispose()
  const idx = tabs.value.indexOf(rt)
  tabs.value.splice(idx, 1)
  hostEls.delete(rt.tab.tab_id)
  if (activeId.value === rt.tab.tab_id) {
    const next = tabs.value.length ? tabs.value[Math.max(0, idx - 1)] : null
    activeId.value = next ? next.tab.tab_id : null
    if (next) activate(next)
  }
}

function confirmClose() {
  const rt = confirmTarget.value
  confirmTarget.value = null
  if (rt) doClose(rt)
}

function cancelClose() {
  confirmTarget.value = null
}

function onHostMounted(tabId: string, el: unknown) {
  if (el instanceof HTMLElement) {
    hostEls.set(tabId, el)
    // 若该标签是当前激活标签，立即 open
    const rt = tabs.value.find((t) => t.tab.tab_id === tabId)
    if (rt && activeId.value === tabId && !rt.opened) {
      rt.term.open(el)
      rt.opened = true
      rt.fit.fit()
      rt.term.focus()
      ensureWs(rt)
    }
  }
}

// ---------- 生命周期 ----------

onMounted(() => {
  loadTabs()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  tabs.value.forEach((rt) => {
    teardownWs(rt)
    rt.term.dispose()
  })
})

function onResize() {
  const rt = active.value
  if (rt && rt.opened) rt.fit.fit()
}
</script>

<template>
  <div class="term-page">
    <!-- 标签栏 -->
    <div class="term-tabs">
      <div
        v-for="rt in tabs"
        :key="rt.tab.tab_id"
        class="term-tab"
        :class="{ active: rt.tab.tab_id === activeId }"
        @click="activate(rt)"
      >
        <span class="tab-dot" :class="rt.alive ? 'ok' : ''" />
        <span class="tab-title" :title="rt.tab.cwd">{{ rt.tab.title }}</span>
        <button class="tab-close" title="关闭标签" @click.stop="closeTab(rt)">
          <Icon name="x" :size="12" />
        </button>
      </div>
      <button class="term-add" title="新建标签" @click="newTab()">
        <Icon name="plus" :size="15" />
      </button>
      <div v-if="active" class="term-meta">
        <Icon name="folder" :size="13" />
        <span class="term-cwd" :title="active.tab.cwd">{{ active.tab.cwd }}</span>
      </div>
    </div>

    <!-- 终端主体：每个标签一个独立容器 -->
    <div class="term-body">
      <div v-if="loading" class="term-loading">正在加载终端…</div>
      <div v-else-if="!tabs.length" class="term-empty">
        <div class="empty-ic"><Icon name="terminal" :size="26" /></div>
        <p>还没有终端标签</p>
        <p class="empty-sub">点击左上角「+」新建一个真实 shell（本机 cmd.exe）</p>
      </div>
      <div
        v-for="rt in tabs"
        :key="rt.tab.tab_id"
        :ref="(el) => onHostMounted(rt.tab.tab_id, el)"
        class="term-host"
        :class="{ hidden: rt.tab.tab_id !== activeId }"
      />
    </div>

    <!-- 关闭确认弹窗（自定义，替代 window.confirm） -->
    <div v-if="confirmTarget" class="confirm-overlay" @click.self="cancelClose">
      <div class="confirm-dialog">
        <div class="confirm-title">关闭终端标签</div>
        <div class="confirm-body">
          标签「{{ confirmTarget.tab.title }}」中仍有进程在运行，关闭将终止该进程。
        </div>
        <div class="confirm-actions">
          <button class="btn-cancel" @click="cancelClose">取消</button>
          <button class="btn-confirm" @click="confirmClose">确定关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.term-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
}

.term-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line-soft);
  background: rgba(247, 246, 242, 0.6);
  flex-wrap: wrap;
}

.term-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 8px 0 10px;
  border-radius: 8px;
  background: transparent;
  color: var(--ink-2);
  font-size: 12.5px;
  cursor: pointer;
  border: 1px solid transparent;
  white-space: nowrap;
}
.term-tab:hover { background: #f1f0eb; }
.term-tab.active {
  background: #fff;
  color: var(--ink);
  border-color: var(--line);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(23, 24, 26, 0.06);
}
.tab-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex: none; }
.tab-dot.ok { background: var(--green); }
.tab-title { max-width: 160px; overflow: hidden; text-overflow: ellipsis; }
.tab-close {
  width: 18px;
  height: 18px;
  border: 0;
  background: transparent;
  border-radius: 5px;
  display: grid;
  place-items: center;
  color: var(--muted);
  padding: 0;
}
.tab-close:hover { background: #e8e6df; color: var(--ink); }
.term-add {
  width: 28px;
  height: 28px;
  border: 0;
  background: transparent;
  border-radius: 7px;
  display: grid;
  place-items: center;
  color: var(--ink-2);
}
.term-add:hover { background: #f1f0eb; color: var(--ink); }

.term-meta {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 11.5px;
  min-width: 0;
}
.term-cwd {
  font-family: var(--mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 360px;
}

.term-body {
  flex: 1;
  min-height: 0;
  position: relative;
  background: #141518;
  display: flex;
  flex-direction: column;
}
.term-host {
  flex: 1;
  min-height: 0;
  padding: 10px 12px;
}
.term-host.hidden { display: none; }
.term-host :deep(.xterm) { height: 100%; }

.term-loading,
.term-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #8b8880;
  gap: 4px;
}
.term-empty p { margin: 3px 0; font-size: 13.5px; color: #c9c7c1; }
.term-empty .empty-sub { font-size: 11.5px; color: #8b8880; }
.term-empty .empty-ic {
  width: 58px;
  height: 58px;
  border-radius: 50%;
  background: #232428;
  display: grid;
  place-items: center;
  color: #8b8880;
  margin-bottom: 5px;
}

/* 关闭确认弹窗 */
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: grid;
  place-items: center;
  z-index: 1000;
}
.confirm-dialog {
  width: 360px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.24);
  padding: 20px;
  color: var(--ink);
}
.confirm-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 8px;
}
.confirm-body {
  font-size: 13px;
  color: var(--ink-2);
  line-height: 1.6;
  margin-bottom: 18px;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.btn-cancel,
.btn-confirm {
  height: 32px;
  padding: 0 16px;
  border-radius: 7px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid var(--line);
}
.btn-cancel {
  background: #fff;
  color: var(--ink-2);
}
.btn-cancel:hover { background: #f5f4ef; }
.btn-confirm {
  background: #c0392b;
  color: #fff;
  border-color: #c0392b;
}
.btn-confirm:hover { background: #a93226; }
</style>
