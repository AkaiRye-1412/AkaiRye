/** REST 端点封装（按 04.API设计.md 资源组分组）；业务实现阶段按需补全方法。 */

import { http } from './http'
import { eventsSocket } from './ws'
import type { SystemStatus } from '@/types/api'

export { eventsSocket, http }

export const systemApi = {
  status: () => http.get<SystemStatus>('/api/system/status'),
}

/** 品牌枚举（FR-RAG-13）：固定枚举 + 可停用，不做自由文本输入 */
export interface Brand {
  name: string
  sort: number
  enabled: number
}

export const configApi = {
  getAll: () => http.get<Record<string, any>>('/api/config'),
  save: (patch: Record<string, any>) => http.put<{ saved: string[] }>('/api/config', patch),
  brands: () => http.get<Brand[]>('/api/brands'),
  addBrand: (name: string) => http.post<Brand>('/api/brands', { name }),
  patchBrand: (name: string, body: { enabled?: boolean; sort?: number }) =>
    http.patch<Brand>(`/api/brands/${encodeURIComponent(name)}`, body),
  testLlm: (body: { base_url: string; api_key: string; model: string; temperature?: number }) =>
    http.post<{ ok: boolean; latency_ms?: number; model?: string; reply?: string; error?: string }>(
      '/api/config/test-llm',
      body,
    ),
  testEmbedding: (body: { base_url: string; api_key: string; model: string }) =>
    http.post<{ ok: boolean; latency_ms?: number; model?: string; dim?: number; error?: string }>(
      '/api/config/test-embedding',
      body,
    ),
}

export const dashboardApi = {
  summary: () =>
    http.get<{
      active_count: number
      archived_count: number
      total_count: number
      follow_up_count: number
      month_new_count: number
      avg_handle_days: number | null
      customer_top: { customer: string; total: number; open: number }[]
      follow_up_days: number
      scanned_at: string | null
    }>('/api/dashboard/summary'),
  trend: (range: 'month' | '30d' | '6m') =>
    http.get<{ range: string; series: { bucket: string; count: number }[]; total: number }>(
      `/api/dashboard/trend?range=${range}`,
    ),
  followUps: () =>
    http.get<
      { path: string; customer: string | null; project_name: string | null; updated_date: string; days: number }[]
    >('/api/dashboard/follow-ups'),
}

export const notesApi = {
  list: (query = '') => http.get<Record<string, any>[]>(`/api/notes${query}`),
  get: (path: string) => http.get<Record<string, any>>(`/api/notes/${encodeURIComponent(path)}`),
  create: (body: { customer: string; project_name: string; start_date?: string }) =>
    http.post<Record<string, any>>('/api/notes', body),
  save: (
    path: string,
    body: { content: string; base_hash: string | null },
    opts: { force?: boolean; reload?: boolean } = {},
  ) => {
    const q: string[] = []
    if (opts.force) q.push('force=true')
    if (opts.reload) q.push('reload=true')
    const qs = q.length ? `?${q.join('&')}` : ''
    return http.put<Record<string, any>>(`/api/notes/${encodeURIComponent(path)}${qs}`, body)
  },
  saveAs: (path: string, content: string, suggested_path?: string) =>
    http.post<Record<string, any>>(`/api/notes/${encodeURIComponent(path)}/save-as`, {
      content,
      suggested_path,
    }),
  history: (path: string) =>
    http.get<Record<string, any>>(`/api/notes/${encodeURIComponent(path)}/history`),
  /** 头部字段更新（FR-NOTE-03/11）：只改 frontmatter，正文不动；冲突语义同 save。 */
  meta: (
    path: string,
    body: {
      customer?: string
      project_name?: string
      status?: 'active' | 'archived'
      start_date?: string
      base_hash?: string | null
      force?: boolean
    },
  ) => http.patch<Record<string, any>>(`/api/notes/${encodeURIComponent(path)}/meta`, body),
  reindex: (path: string) =>
    http.post<Record<string, any>>(`/api/notes/${encodeURIComponent(path)}/reindex`),
  /** 删除笔记（FR-NOTE-12）：后端把文件移入 vault 的 .trash/，可还原。 */
  remove: (path: string) =>
    http.delete<{ deleted: boolean; path: string; trashed_to: string }>(
      `/api/notes/${encodeURIComponent(path)}`,
    ),
}

export const projectsApi = {
  list: (query = '') => http.get<Record<string, any>[]>(`/api/projects${query}`),
  patchStatus: (path: string, status: 'active' | 'archived') =>
    http.patch<Record<string, any>>(`/api/projects/${encodeURIComponent(path)}`, { status }),
}

export interface RagCitation {
  seq: number
  source_type: 'note' | 'manual'
  source_name: string
  chapter: string | null
  page: number | null
  snippet: string
  note_path: string | null
  manual_id: number | null
  chunk_valid: boolean
}

export interface RagMessage {
  id: number
  session_id: number
  role: 'user' | 'assistant'
  content: string
  scope: Record<string, any> | null
  context: Record<string, any> | null
  model_id: string | null
  created_at: string
  citations?: RagCitation[]
}

export interface RagSession {
  id: number
  title: string
  created_at: string
  updated_at: string
}

export const ragApi = {
  sessions: () => http.get<RagSession[]>('/api/rag/sessions'),
  createSession: (title?: string) =>
    http.post<RagSession>('/api/rag/sessions', { title }),
  renameSession: (id: number, title: string) =>
    http.patch<RagSession>(`/api/rag/sessions/${id}`, { title }),
  deleteSession: (id: number) => http.delete<{ deleted: boolean }>(`/api/rag/sessions/${id}`),
  clearSessions: () => http.delete<{ cleared: number }>('/api/rag/sessions'),
  messages: (sessionId: number) => http.get<RagMessage[]>(`/api/rag/sessions/${sessionId}/messages`),
  clearSessionMessages: (id: number) =>
    http.delete<{ cleared: number; session_id: number }>(`/api/rag/sessions/${id}/messages`),
  ask: (body: { session_id: number; question: string; scope: Record<string, any> }) =>
    // title：后端首次提问时自动命名的会话标题（未改动则 null）
    http.post<{ message_id: number; title?: string | null }>('/api/rag/ask', body),
  message: (id: number) => http.get<RagMessage>(`/api/rag/messages/${id}`),
  citations: (id: number) => http.get<RagCitation[]>(`/api/rag/citations/${id}`),
}

export interface TranslateSense {
  pos: string
  meaning: string
  tag: string
  scene: string
  examples: { src: string; dst: string }[]
}

export interface TranslateResult {
  translation: string
  /** word = 单词/短语（按场景列义项）；sentence = 句子/段落（分点讲解） */
  mode: 'word' | 'sentence'
  senses: TranslateSense[]
  points: { label: string; text: string }[]
  tips: string
  /** 结构化讲解压成的 Markdown，便于复制/兜底展示 */
  explanation: string
  direction: 'en-zh' | 'zh-en'
  model: string
}

export const translateApi = {
  run: (body: { text: string; direction: 'auto' | 'en-zh' | 'zh-en' }) =>
    http.post<TranslateResult>('/api/translate', body),
}

export const backupApi = {
  list: () => http.get<Record<string, any>[]>('/api/backups'),
  create: () => http.post<Record<string, any>>('/api/backups'),
}

/** ---------------- 存量整理（M7 / FR-CLEAN） ---------------- */

export interface CleanupItem {
  id: number
  path: string
  file_name: string
  customer: string
  project_name: string
  status: 'active' | 'archived'
  start_date: string
  /** 缺失的原始 frontmatter 键（name/project/status/start_date/updated） */
  missing: string[]
  /** 各字段识别来源，如 { project: '文件名' } */
  sources: Record<string, string>
  /** 本次将写入的键（按磁盘现状计算，执行时会重算） */
  changes: string[]
  skip: boolean
  applied: boolean
  rolled_back: boolean
  error: string | null
}

export interface CleanupBatch {
  id: number
  status: 'draft' | 'done' | 'rolled_back' | 'failed'
  scope: 'missing' | 'all'
  total: number
  applied: number
  skipped: number
  failed: number
  /** 已按快照还原的篇数（rolled_back 状态用来显示「已还原 N 篇」） */
  restored: number
  backup_path: string | null
  note: string | null
  created_at: string
  executed_at: string | null
  rolled_back_at: string | null
  items?: CleanupItem[]
}

export interface CleanupSummary {
  notes_total: number
  head_missing: number
  customers: string[]
  last_batch: CleanupBatch | null
  notes_folder: string
}

/** 预览表提交的字段子集（id/识别来源等只读字段不必回传） */
export type CleanupItemInput = Pick<
  CleanupItem,
  'path' | 'customer' | 'project_name' | 'status' | 'start_date' | 'skip'
>

export interface CleanupExecResult {
  batch: CleanupBatch
  errors: { path: string; error: string }[]
}

export interface CleanupRollbackResult {
  batch: CleanupBatch
  restored: number
  skipped: { path: string; reason: string }[]
  errors: { path: string; error: string }[]
  partial: boolean
}

export const cleanupApi = {
  /** 头部数字：总篇数 / 待整理数 / 已知客户（补全时作 datalist） */
  summary: () => http.get<CleanupSummary>('/api/cleanup/summary'),
  batches: () => http.get<CleanupBatch[]>('/api/cleanup/batches'),
  /** 扫描并生成草稿批次（会丢弃未执行的旧草稿） */
  create: (scope: 'missing' | 'all' = 'missing') =>
    http.post<CleanupBatch>('/api/cleanup/batches', { scope }),
  get: (id: number) => http.get<CleanupBatch>(`/api/cleanup/batches/${id}`),
  saveItems: (id: number, items: CleanupItemInput[]) =>
    http.put<CleanupBatch>(`/api/cleanup/batches/${id}/items`, { items }),
  /** 执行：先打包备份，再逐篇只写 frontmatter；items 为预览表最终值 */
  execute: (id: number, items: CleanupItemInput[]) =>
    http.post<CleanupExecResult>(`/api/cleanup/batches/${id}/execute`, { items }),
  /** 回滚：默认跳过整理后被外部编辑过的笔记，force 才强写 */
  rollback: (id: number, force = false) =>
    http.post<CleanupRollbackResult>(`/api/cleanup/batches/${id}/rollback`, { force }),
  /** 删除整理记录：草稿直接删；已执行 / 已回滚需 force（前端二次确认后传） */
  remove: (id: number, force = false) =>
    http.delete<{ deleted: boolean; batch_id: number; status: string }>(
      `/api/cleanup/batches/${id}${force ? '?force=true' : ''}`,
    ),
  /** 清空整理记录，默认保留正在编辑的草稿 */
  clearHistory: (keepDraft = true) =>
    http.delete<{ deleted: number }>(
      `/api/cleanup/batches${keepDraft ? '?keep_draft=true' : '?keep_draft=false'}`,
    ),
}

export const manualsApi = {
  list: (query = '') => http.get<Record<string, any>[]>(`/api/manuals${query}`),
  pdfUrl: (manualId: number) => `/api/reader/pdf/${manualId}`,
  meta: (manualId: number) => http.get<Record<string, any>>(`/api/reader/meta/${manualId}`),
}

/** ---------------- PDF 手册（M9 / M3 手册侧） ---------------- */

export type PdfStatus =
  | 'discovered'
  | 'queue'
  | 'parsing'
  | 'rebuilding'
  | 'ready'
  | 'failed'
  | 'update'
  | 'invalid'

export interface PdfManual {
  id: number
  path: string
  rel_path: string
  folder: string
  name: string
  brand: string | null
  pages: number | null
  size: number | null
  status: PdfStatus
  progress: number | null
  chunks: number
  error: string | null
  model_id: string | null
  dim: number | null
  last_parsed_at: string | null
  updated_at: string
}

export interface PdfTree {
  root: string
  groups: { name: string; items: PdfManual[] }[]
  total: number
  brands: string[]
  counts: Record<string, number>
  scan: { added: number; updated: number; invalid: number; removed: number; total: number }
}

/** 入库进度（WS ingest.progress / GET /api/ingest/state） */
export interface IngestState {
  running: boolean
  manual_id: number | null
  name: string
  stage: 'queued' | 'parsing' | 'chunking' | 'embedding' | 'writing' | ''
  progress: number
  started_at: string | null
  finished_at: string | null
  error: string
}

export interface IngestTask {
  id: number
  manual_id: number
  manual_name: string | null
  action: 'ingest' | 'rebuild' | 'relocate' | 'remove'
  status: 'queued' | 'running' | 'done' | 'failed' | 'canceled'
  progress: number | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export const pdfApi = {
  /** 目录树（后端顺带做一次增量同步） */
  tree: () => http.get<PdfTree>('/api/pdf/tree'),
  rescan: () =>
    http.post<{ added: number; updated: number; invalid: number; removed: number; total: number }>(
      '/api/pdf/rescan',
    ),
  search: (query = '') => http.get<PdfManual[]>(`/api/pdf/search${query}`),
  ingest: (manualIds?: number[], action: 'ingest' | 'rebuild' = 'ingest') =>
    http.post<{ started: boolean; queued: number[]; skipped: number[]; pending: number; reason?: string }>(
      '/api/manuals/ingest',
      { manual_ids: manualIds, action },
    ),
  rebuild: (manualId: number) =>
    http.post<{ started: boolean; queued: number[] }>(`/api/manuals/${manualId}/rebuild`),
  remove: (manualId: number) =>
    http.delete<{ manual_id: number; deleted_chunks: number }>(`/api/manuals/${manualId}`),
  ignoreUpdate: (manualId: number) =>
    http.post<{ status: string }>(`/api/manuals/${manualId}/ignore-update`),
  tasks: (query = '') => http.get<IngestTask[]>(`/api/ingest/tasks${query}`),
  state: () => http.get<IngestState>('/api/ingest/state'),
  meta: (manualId: number) =>
    http.get<{
      id: number
      name: string
      brand: string | null
      pages: number | null
      status: PdfStatus
      chapters: { title_path: string; page: number; chunks: number }[]
    }>(`/api/reader/meta/${manualId}`),
  /**
   * PDF 文件流地址。fragment 里 Chrome 只认 page（跳页）与 navpanes：
   * - page=N 跳页（RAG 引用定位）
   * - navpanes=0 尝试收起书签侧栏（几百条书签的厂商手册会占掉半屏）
   * 注意 view=Fit / zoom=page-fit 实测被 Chrome 忽略，缩放默认按宽度铺满，
   * 纵向滚动条无法通过 URL 关掉，只能接受（遮罩方案会遮掉正文，已放弃）。
   */
  fileUrl: (manualId: number, page?: number) => {
    const frag = ['navpanes=0']
    if (page) frag.unshift(`page=${page}`)
    return `/api/reader/pdf/${manualId}#${frag.join('&')}`
  },
}


/** 索引任务进度（M3）：后端进程内状态，running=false 时展示上一次的结果。 */
export interface IndexProgress {
  running: boolean
  mode: '' | 'autofill' | 'full'
  total: number
  done: number
  failed: number
  current: string
  started_at: string | null
  finished_at: string | null
}

export interface KbStats {
  notes: number
  notes_by_status: { ready: number; pending: number; failed: number; excluded: number; unindexed: number }
  manuals: number
  chunks: number
  vector_model: { model_id: string | null; dim: number | null }
  indexing: IndexProgress
  last_indexed_at: string | null
}

export interface KbNote {
  path: string
  display_name: string
  group: string
  customer: string | null
  project_name: string | null
  note_title: string | null
  summary: string | null
  updated_date: string | null
  head_ok: number
  indexed_status: 'ready' | 'pending' | 'failed' | 'excluded' | null
  last_error: string | null
  chunks: number
}

export const kbApi = {
  stats: () => http.get<KbStats>('/api/kb/stats'),
  notes: (query = '') => http.get<KbNote[]>(`/api/kb/notes${query}`),
  whitelist: () =>
    http.get<{ folders: string[]; invalid: string[]; excluded: string[]; notes_folder: string | null }>(
      '/api/kb/whitelist',
    ),
  saveWhitelist: (folders: string[]) =>
    http.put<{
      folders: string[]
      invalid: string[]
      indexed: number
      excluded: number
      pending: number
      indexing: boolean
    }>('/api/kb/whitelist', { folders }),
  /** 全量重建：后台执行（embedding 有节流，同步会超时），进度看 stats.indexing。 */
  reindexAll: (body: { force?: boolean; background?: boolean } = { background: true }) =>
    http.post<{ started: boolean; total?: number; reason?: string; done?: number; failed?: number }>(
      '/api/kb/reindex',
      body,
    ),
  /** 补齐缺失索引（FR-RAG-01）：把未索引/缺块/失败的笔记排进后台队列。 */
  autofill: () =>
    http.post<{ started: boolean; pending: number; reason?: string }>('/api/kb/autofill'),
  reindexNote: (path: string) =>
    http.post<{ status: string; chunks: number }>('/api/kb/notes/reindex', { path }),
  /**
   * 单篇移出 / 恢复知识库（不动磁盘文件）。
   * 移出后该篇保持可见（状态「已移出」），不会被自动补齐拉回；恢复会立即重建索引。
   */
  excludeNote: (path: string, excluded: boolean) =>
    http.post<{
      path: string
      excluded: boolean
      status: string
      chunks: number
      in_whitelist: boolean
      reason: string
    }>('/api/kb/notes/exclude', { path, excluded }),
}
