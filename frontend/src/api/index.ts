/** REST 端点封装（按 04.API设计.md 资源组分组）；业务实现阶段按需补全方法。 */

import { http } from './http'
import { eventsSocket } from './ws'
import type { SystemStatus } from '@/types/api'

export { eventsSocket, http }

export const systemApi = {
  status: () => http.get<SystemStatus>('/api/system/status'),
}

export const configApi = {
  getAll: () => http.get<Record<string, any>>('/api/config'),
  save: (patch: Record<string, any>) => http.put<{ saved: string[] }>('/api/config', patch),
  brands: () => http.get<{ name: string; sort: number; enabled: number }[]>('/api/brands'),
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

export const backupApi = {
  list: () => http.get<Record<string, any>[]>('/api/backups'),
  create: () => http.post<Record<string, any>>('/api/backups'),
}

export const manualsApi = {
  list: (query = '') => http.get<Record<string, any>[]>(`/api/manuals${query}`),
  pdfUrl: (manualId: number) => `/api/reader/pdf/${manualId}`,
  meta: (manualId: number) => http.get<Record<string, any>>(`/api/reader/meta/${manualId}`),
}
