<script setup lang="ts">
/**
 * 终端（M5 / FR-TERM · 三期）：xterm.js 多标签 + ConPTY 真实 shell。
 *
 * 设计要点：
 * - 每标签一条 WS（/ws/terminal/{tab_id}），PTY 进程随标签常驻（后端），刷新/重连后 attach 回同一进程；
 * - 标签元数据（cwd/标题）持久化后端 terminal_tab 表，进入页面时 REST 全量对账；
 * - 内置命令（FR-TERM-02）：以 / 开头走后端拦截，回显 builtin 帧；
 * - 关闭标签直接关闭、不再二次确认：DELETE 带 force，后端直接终止该标签的 PTY。
 */
import { computed, onBeforeUnmount, onMounted, ref, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { terminalApi, type TerminalTab } from '@/api'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'

const ui = useUiStore()
const route = useRoute()

interface TabRuntime {
  tab: TerminalTab
  term: Terminal
  fit: FitAddon
  ws: WebSocket | null
  reconnectTimer: number | null
  alive: boolean
  closed: boolean
  opened: boolean
  replaying: boolean
  // 容器尺寸观察器：fit 依赖父容器真实尺寸；flex 布局下首次 open 时高度可能未稳定，
  // 用 ResizeObserver 在容器尺寸真正确定后重新 fit，避免 rows 算错（光标贴底/滚动条异常）。
  ro: ResizeObserver | null
}

const tabs = ref<TabRuntime[]>([])
const activeId = ref<string | null>(null)
const loading = ref(true)
// 每个标签的 DOM 容器（tab_id → element）
const hostEls = new Map<string, HTMLElement>()

const active = computed(() => tabs.value.find((t) => t.tab.tab_id === activeId.value) ?? null)

// ---------- 初始化 ----------

async function loadTabs() {
  try {
    const list = await terminalApi.list()
    tabs.value = list.map((tab) => buildRuntime(tab))
    // 从「代码仓库」等入口带 ?cwd= 跳转过来：在该目录新建一个标签（FR-TERM-06）
    const wantedCwd = typeof route.query.cwd === 'string' ? route.query.cwd.trim() : ''
    if (wantedCwd) {
      await newTab(wantedCwd, true)
    } else if (!tabs.value.length) {
      // 首次进入无标签时，自动新建一个默认标签（默认 cwd 为用户主目录）
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
  // loading 已置 false、DOM 已渲染后，确保激活标签的 xterm open + WS 连接
  // （刷新进入时标签来自 DB，不能只依赖 onHostMounted 回调，需显式激活）
  await nextTick()
  const first = tabs.value.find((t) => t.tab.tab_id === activeId.value) ?? tabs.value[0]
  if (first) {
    await activate(first)
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
    // 关键：默认 true 会在「每次用户输入」时强制滚到底部，导致向上滚看历史时
    // 一敲键盘就被拉回底部（无法停留在历史位置）。设为 false，由 writeOutput 统一控制跟随。
    scrollOnUserInput: false,
    // 加宽 overview ruler：默认 14px 太窄，滚动条滑块在 webview 里很难点中/拖动。
    // 加宽后滚动条更接近 cmd 的可操作性（滑块更大、更容易上下拖动）。
    overviewRuler: { width: 16 },
  })
  const fit = new FitAddon()
  term.loadAddon(fit)
  // 尺寸同步（关键修复）：xterm fit 后把 cols/rows 通过 WS 发给后端，让 ConPTY/cmd
  // 同步窗口尺寸。否则前端列宽与 ConPTY 默认 80 列不一致时，长提示符折行会让
  // cmd 与 xterm 对「光标在哪一列」判断错位，导致光标漂移（如 git status 后光标
  // 跑到上一行中间）。onResize 在 fit/resize 时触发，这里统一发送。
  term.onResize(({ cols, rows }) => {
    if (rt.ws && rt.ws.readyState === WebSocket.OPEN) {
      rt.ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    }
  })
  term.onData((data) => {
    // 重放历史期间不转发：xterm 对历史里终端查询序列（DA1/DSR）的自动响应
    // 若转发回 shell 会被 cmd 当输入回显成 ^[[?1;2c 之类的污染，且随刷新累积。
    if (rt.replaying) return
    // 过滤 xterm 对终端查询的自动响应（DA1 \x1b[?1;2c / DSR \x1b[{row};{col}R / 其它 ? 查询）：
    // 这些是 xterm 内部生成的、回应 shell 发出的能力协商序列，不该回传给 cmd（cmd 不认识、
    // 会当作输入回显造成污染）。用户正常键入不会产生此类序列。
    const cleaned = data.replace(/\x1b\[\?[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\[[0-9]+;[0-9]+R/g, '')
    if (!cleaned) return
    if (rt.ws && rt.ws.readyState === WebSocket.OPEN) {
      rt.ws.send(JSON.stringify({ type: 'input', data: cleaned }))
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
    replaying: false,
    ro: null,
  }
  return rt
}

function wsUrl(tabId: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/terminal/${tabId}`
}

// 写入终端输出：直接交给 xterm 自然渲染。
// 关键：不要手动 scrollToBottom()。xterm 默认行为就是「光标停在内容末尾、下方留白」，
// 与 cmd 一致；手动 scrollToBottom 会把不满一屏的内容硬滚到底部（提示符贴底），
// 且滚动条会被锁死，无法像 cmd 那样自由上下滑动。配合 scrollOnUserInput:false，
// 用户向上滚看历史时也不会被新输出拉回底部。
function writeOutput(rt: TabRuntime, data: string) {
  rt.term.write(data)
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
    fitAndObserve(rt)
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
    // 连接建立后先清空 xterm，等待后端重放输出历史（重连/刷新场景由后端统一重放还原界面）。
    // 避免断线重连时旧内容与重放历史叠加重复。
    rt.term.reset()
    // 补发一次当前尺寸：首次 fit 发生在 WS 建立之前（rt.ws 为 null，onResize 没发出去），
    // 此处按当前 xterm 尺寸显式同步一次给 ConPTY，确保后端窗口尺寸正确。
    try {
      const { cols, rows } = rt.term
      if (cols > 0 && rows > 0) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }))
      }
    } catch {
      /* ignore */
    }
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
        writeOutput(rt, frame.data ?? '')
        break
      case 'replay':
        // 重放历史快照：写期间屏蔽 onData 转发，避免 xterm 对查询序列的自动响应污染 shell
        rt.replaying = true
        try {
          writeOutput(rt, frame.data ?? '')
        } finally {
          rt.replaying = false
        }
        break
      case 'builtin':
        writeOutput(rt, frame.data ?? '')
        if (frame.cwd) rt.tab.cwd = frame.cwd
        break
      case 'cwd':
        if (frame.cwd) rt.tab.cwd = frame.cwd
        break
      case 'exit':
        rt.alive = false
        break
      case 'error':
        writeOutput(rt, `\r\n\x1b[31m[错误] ${frame.data ?? ''}\x1b[0m\r\n`)
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
  // 直接关闭，不弹确认：force=1 让后端终止标签内的 PTY 进程
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
  if (rt.ro) {
    rt.ro.disconnect()
    rt.ro = null
  }
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

function onHostMounted(tabId: string, el: unknown) {
  if (el instanceof HTMLElement) {
    hostEls.set(tabId, el)
    // 若该标签是当前激活标签，立即 open
    const rt = tabs.value.find((t) => t.tab.tab_id === tabId)
    if (rt && activeId.value === tabId && !rt.opened) {
      rt.term.open(el)
      rt.opened = true
      fitAndObserve(rt)
      rt.term.focus()
      ensureWs(rt)
    }
  }
}

// fit + 建立容器尺寸观察器：flex 布局下 xterm open 后容器高度可能尚未稳定，
// fit 算出的 rows 会偏小，导致光标贴底、滚动条滚动范围异常。用 rAF 延后首次 fit，
// 再用 ResizeObserver 在容器尺寸真正确定后重 fit，确保 rows 精确。
function fitAndObserve(rt: TabRuntime) {
  requestAnimationFrame(() => {
    if (rt.closed || !rt.opened) return
    try {
      rt.fit.fit()
    } catch {
      /* fit 可能因容器尚未布局而抛错，交给 ResizeObserver 兜底 */
    }
  })
  // 容器尺寸变化时重新 fit（含首次布局稳定后的那次触发）
  if (!rt.ro && typeof ResizeObserver !== 'undefined') {
    rt.ro = new ResizeObserver(() => {
      if (rt.closed || !rt.opened) return
      try {
        rt.fit.fit()
      } catch {
        /* ignore */
      }
    })
    const host = hostEls.get(rt.tab.tab_id)
    if (host) rt.ro.observe(host)
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
    if (rt.ro) {
      rt.ro.disconnect()
      rt.ro = null
    }
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
  </div>
</template>

<style scoped>
.term-page {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  height: 100%;
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
  /* 关键：不能有 padding。FitAddon.fit() 依赖 .xterm 精确填满父容器来算 cols/rows；
     任何 padding 都会让测量偏差，导致 rows 算错 → 光标贴底、滚动条滚动范围异常、
     无法自由滑动。终端内容贴边更接近 cmd 原生态。 */
  padding: 0;
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
</style>
