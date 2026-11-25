/** 核心类型占位：与 04.API设计.md 的响应契约对齐，业务实现阶段逐步补全。 */

export type ManualStatus =
  | 'discovered' | 'queue' | 'parsing' | 'rebuilding'
  | 'ready' | 'failed' | 'update' | 'invalid'

export interface SystemStatus {
  db_ready: boolean
  config_complete: boolean
  missing_config_keys: string[]
  vault_root_valid: boolean
  pdf_root_valid: boolean
  counts: { notes: number; manuals_ready: number; chunks_ready: number }
  last_backup_at: string | null
  ws_available: boolean
}

/** WS 事件包络（api-rules 第 4 节） */
export interface WsFrame<T = unknown> {
  topic:
    | 'note.changed' | 'index.updated' | 'ingest.progress'
    | 'manual.updated' | 'rag.stream' | 'config.updated'
    | 'backup.done' | 'context.report'
  type: 'update' | 'snapshot'
  payload: T
}
