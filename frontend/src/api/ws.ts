/**
 * events WS 客户端（api-rules 第 4 节契约）：
 * - 自动重连（指数退避）+ 心跳；
 * - topic 路由到订阅者；
 * - 重连后触发 onReconnected → 业务层 REST 全量对账（推送不作唯一数据来源）。
 */

import type { WsFrame } from '@/types/api'

type Handler = (payload: unknown) => void

class EventsSocket {
  private ws: WebSocket | null = null
  private handlers = new Map<string, Set<Handler>>()
  private reconnectAttempts = 0
  private heartbeatTimer: number | null = null
  private onReconnectedHandlers = new Set<() => void>()

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}/ws/events`)

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
      this.startHeartbeat()
      this.onReconnectedHandlers.forEach((h) => h())
    }

    this.ws.onmessage = (ev) => {
      try {
        const frame: WsFrame = JSON.parse(ev.data)
        this.handlers.get(frame.topic)?.forEach((h) => h(frame.payload))
        this.handlers.get('*')?.forEach((h) => h(frame))
      } catch { /* 忽略非法帧 */ }
    }

    this.ws.onclose = () => {
      this.stopHeartbeat()
      const delay = Math.min(1000 * 2 ** this.reconnectAttempts++, 30_000)
      setTimeout(() => this.connect(), delay)
    }
  }

  on(topic: string, handler: Handler): () => void {
    if (!this.handlers.has(topic)) this.handlers.set(topic, new Set())
    this.handlers.get(topic)!.add(handler)
    return () => this.handlers.get(topic)?.delete(handler)
  }

  onReconnected(handler: () => void): () => void {
    this.onReconnectedHandlers.add(handler)
    return () => this.onReconnectedHandlers.delete(handler)
  }

  /** 客户端仅允许上报上下文（FR-RAG-18 自动感知），业务动作一律走 REST。 */
  reportContext(payload: Record<string, unknown>) {
    this.ws?.send(JSON.stringify({ topic: 'context.report', type: 'update', payload }))
  }

  private startHeartbeat() {
    this.stopHeartbeat()
    this.heartbeatTimer = window.setInterval(() => {
      // TODO(业务实现): 与后端约定心跳帧（api-rules 4.3）
    }, 30_000)
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }
}

export const eventsSocket = new EventsSocket()
