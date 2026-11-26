/**
 * C1 RAG 常驻面板全局状态（Q-02：所有页面常驻，全局单实例）。
 *
 * 依据 04 文档 Q-14：提问走 REST（POST /api/rag/ask），token 流经 WS rag.stream
 * 推送，done=true 后 REST 补拉完整回答与引用（推送不作唯一数据来源）。
 */

import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { eventsSocket, ragApi } from '@/api'
import type { RagCitation, RagMessage, RagSession } from '@/api'

export type ScopeMode = 'current' | 'project' | 'brand' | 'all'

/** 流式中的临时消息（id 为后端 message_id，content 随 delta 增长）。 */
export interface StreamingMessage {
  id: number
  role: 'assistant'
  content: string
  done: boolean
}

export const useRagStore = defineStore('rag', () => {
  // 面板开合状态跨刷新保留（用户收起后刷新不会被强行展开）
  const savedPanel = typeof localStorage !== 'undefined' ? localStorage.getItem('rag.panel') : null
  const panelOpen = ref(savedPanel !== 'closed')
  if (typeof localStorage !== 'undefined') {
    watch(panelOpen, (v) => localStorage.setItem('rag.panel', v ? 'open' : 'closed'))
  }
  const sessions = ref<RagSession[]>([])
  const currentSessionId = ref<number | null>(null)
  const messages = ref<RagMessage[]>([])
  /** 正在流式生成的消息（rag.stream delta 追加；done 后并入 messages）。 */
  const streaming = ref<StreamingMessage | null>(null)
  const generating = ref(false)
  const scopeMode = ref<ScopeMode>('current')
  const lastError = ref('')

  /**
   * 自动感知上下文（FR-RAG-18）：由页面切换/笔记打开时经 context.report 上报。
   * PDF 手册页上报 manualId/manualName，让「当前笔记」这一档在手册页等价于
   * 「当前手册」——范围提示与检索过滤都跟着这本手册走。
   */
  const context = ref<{
    notePath?: string
    /** 笔记标题（= note_title），面板上显示「当前笔记：20260825」 */
    noteTitle?: string
    customer?: string
    project?: string
    manualId?: number
    manualName?: string
    manualPage?: number
    /** 手册是否已入库：false 时面板要提示"检索为空"，否则用户会以为问答坏了 */
    manualIndexed?: boolean
  }>({})

  function setContext(ctx: typeof context.value, report = true) {
    const snap: typeof context.value = {
      notePath: ctx.notePath || undefined,
      noteTitle: ctx.noteTitle || undefined,
      customer: ctx.customer || undefined,
      project: ctx.project || undefined,
      manualId: ctx.manualId || undefined,
      manualName: ctx.manualName || undefined,
      manualPage: ctx.manualPage || undefined,
      // false 是有效值（未入库），不能用 || 兜底吞掉
      manualIndexed: typeof ctx.manualIndexed === 'boolean' ? ctx.manualIndexed : undefined,
    }
    context.value = snap
    if (report) eventsSocket.reportContext({ ...snap })
  }

  /** 离开笔记页时清空上下文（否则"当前笔记/客户"会静默沿用上一篇笔记的范围）。 */
  function clearContext(report = true) {
    setContext({}, report)
  }

  /** 当前范围是否真的能落到具体笔记/客户（用于面板提示与按钮禁用）。 */
  const scopeResolved = computed(() => {
    const c = context.value
    if (scopeMode.value === 'all') return true
    if (scopeMode.value === 'current') return !!(c.notePath || c.customer || c.project || c.manualId)
    return !!(c.project || c.customer)
  })

  /** 全局提问待办（FR-HOME-02）：RagPanel watch 到即唤起面板并真实发送。 */
  const pendingAsk = ref<string | null>(null)

  function askFromGlobal(question: string) {
    pendingAsk.value = question
    panelOpen.value = true
  }

  function consumePendingAsk(): string | null {
    const q = pendingAsk.value
    pendingAsk.value = null
    return q
  }

  // ---- 会话 ----

  async function loadSessions() {
    sessions.value = await ragApi.sessions()
  }

  async function switchSession(id: number) {
    currentSessionId.value = id
    messages.value = await ragApi.messages(id)
    streaming.value = null
  }

  async function newSession(): Promise<RagSession> {
    const s = await ragApi.createSession()
    sessions.value = [s, ...sessions.value]
    currentSessionId.value = s.id
    messages.value = []
    streaming.value = null
    return s
  }

  async function renameSession(id: number, title: string) {
    const s = await ragApi.renameSession(id, title)
    sessions.value = sessions.value.map((x) => (x.id === id ? s : x))
  }

  async function deleteSession(id: number) {
    await ragApi.deleteSession(id)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    if (currentSessionId.value === id) {
      currentSessionId.value = null
      messages.value = []
    }
  }

  async function clearAllSessions() {
    await ragApi.clearSessions()
    sessions.value = []
    currentSessionId.value = null
    messages.value = []
  }

  /** 清空"当前会话"的消息（保留会话本身；FR-RAG-25）。 */
  async function clearCurrentSession(): Promise<number> {
    if (!currentSessionId.value) {
      messages.value = []
      streaming.value = null
      return 0
    }
    const { cleared } = await ragApi.clearSessionMessages(currentSessionId.value)
    messages.value = []
    streaming.value = null
    lastError.value = ''
    return cleared
  }

  // ---- 流式接收（rag.stream） ----

  let streamHandlerBound = false

  function bindStream() {
    if (streamHandlerBound) return
    streamHandlerBound = true
    eventsSocket.on('rag.stream', (payload) => {
      const p = payload as { message_id: number; delta: string; done: boolean }
      if (p.message_id !== streaming.value?.id) return
      if (p.delta) streaming.value.content += p.delta
      if (p.done) {
        // done 后 REST 补拉完整回答与引用（Q-14 对账口径）
        ragApi
          .message(p.message_id)
          .then((msg) => {
            messages.value = [...messages.value, msg]
            streaming.value = null
            generating.value = false
            touchSession()
          })
          .catch(() => {
            // 补拉失败：用已累积的流式内容兜底展示
            messages.value = [
              ...messages.value,
              {
                id: streaming.value!.id,
                session_id: currentSessionId.value!,
                role: 'assistant',
                content: streaming.value!.content,
                scope: null,
                context: null,
                model_id: null,
                created_at: '',
                citations: [],
              },
            ]
            streaming.value = null
            generating.value = false
          })
      }
    })
  }

  function touchSession() {
    const idx = sessions.value.findIndex((s) => s.id === currentSessionId.value)
    if (idx >= 0) {
      const updated = { ...sessions.value[idx], updated_at: new Date().toISOString() }
      const rest = sessions.value.filter((_, i) => i !== idx)
      sessions.value = [updated, ...rest] // updated_at 倒序置顶
    }
  }

  // ---- 提问（POST /api/rag/ask） ----

  function scopePayload(): Record<string, any> {
    // 显式携带上下文：后端虽有 current_context 兜底，但那是全进程快照，
    // 页面切走后可能已过期 → 前端自己算准，避免"切了范围结果却没变/变错"。
    const mode = scopeMode.value
    const payload: Record<string, any> = { mode }
    const ctx = context.value
    if (mode === 'current' || mode === 'project') {
      // 显式带上（未知时传 null）：后端见到这些键就不再回落到进程级快照，
      // 保证"面板提示的范围 = 实际检索范围"。
      if (mode === 'current') {
        // 「当前笔记」= 按 project 算（项目名；售后卡是卡片日期，与笔记一一对应）。
        // 有 project 就交给后端按 project 过滤；实在没有才退到 notePath 精确匹配。
        payload.notePath = ctx.project ? null : (ctx.notePath ?? null)
        payload.customer = null
        payload.project = ctx.project ?? null
      } else {
        // 「当前项目」= 按客户聚合（frontmatter 的 name，如「易高」）：
        // 一个客户名下常有多张售后卡 + 项目笔记，用户期望的是"这个客户的全部资料"。
        // 客户缺失时才退到项目名。
        payload.notePath = null
        payload.customer = ctx.customer ?? null
        payload.project = ctx.customer ? null : (ctx.project ?? null)
      }
    }
    // PDF 手册页：当前范围就是这一本手册（后端据此只在该 manual 的块里检索）
    if (mode === 'current' && ctx.manualId) payload.manualId = ctx.manualId
    return payload
  }

  async function send(question: string) {
    const q = question.trim()
    if (!q || generating.value) return
    lastError.value = ''
    bindStream()

    if (!currentSessionId.value) await newSession()
    generating.value = true

    // 本地乐观插入 user 消息（REST 对账由重连后 loadSessions/messages 兜底）
    messages.value = [
      ...messages.value,
      {
        id: -Date.now(),
        session_id: currentSessionId.value!,
        role: 'user',
        content: q,
        scope: null,
        context: null,
        model_id: null,
        created_at: '',
      },
    ]

    try {
      const { message_id, title } = await ragApi.ask({
        session_id: currentSessionId.value!,
        question: q,
        scope: scopePayload(),
      })
      // 首次提问：后端用问题内容自动命名会话，这里同步下拉里的标题（无需再拉列表）
      if (title) {
        sessions.value = sessions.value.map((s) =>
          s.id === currentSessionId.value ? { ...s, title } : s,
        )
      }
      streaming.value = { id: message_id, role: 'assistant', content: '', done: false }
    } catch (e) {
      generating.value = false
      messages.value = messages.value.filter((m) => m.id > 0)
      lastError.value = (e as Error).message || '提问失败'
    }
  }

  return {
    panelOpen,
    sessions,
    currentSessionId,
    messages,
    streaming,
    generating,
    scopeMode,
    lastError,
    context,
    scopeResolved,
    pendingAsk,
    setContext,
    clearContext,
    askFromGlobal,
    consumePendingAsk,
    loadSessions,
    switchSession,
    newSession,
    renameSession,
    deleteSession,
    clearAllSessions,
    clearCurrentSession,
    send,
  }
})
