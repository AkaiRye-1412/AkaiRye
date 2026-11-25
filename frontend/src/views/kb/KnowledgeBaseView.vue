<script setup lang="ts">
/**
 * M3 知识库（FR-RAG-01~03 / Q-01 / Q-05）：
 * - 检索库统计（笔记/手册/切块/向量模型标识，R4）；
 * - 索引白名单可视化管理（一期文件夹级，Q-05）；
 * - 笔记索引状态总览：ready/pending/failed/excluded，失败可见、单篇重试；
 * - 全量重建（幂等）；手册队列管理随 M3 手册链路补充。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { http } from '@/api'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

interface Stats {
  notes: number
  manuals: number
  chunks: number
  vector_model: { model_id: string | null; dim: number | null }
}
interface KbNote {
  path: string
  customer: string | null
  project_name: string | null
  head_ok: number
  indexed_status: string | null
  last_error: string | null
  chunks: number
}

interface WhitelistResp {
  folders: string[]
  invalid?: string[]
  excluded?: number
  indexed?: number
}

const stats = ref<Stats | null>(null)
const whitelist = ref<string[]>([])
const notes = ref<KbNote[]>([])
const statusFilter = ref('')
const newFolder = ref('')
const reindexing = ref(false)
const errorMsg = ref('')

/** 已在库中的子文件夹建议（来自笔记路径的目录部分），供输入框自动补全。 */
const folderSuggestions = computed(() => {
  const dirs = new Set<string>()
  for (const n of notes.value) {
    const i = n.path.indexOf('/')
    if (i > 0) dirs.add(n.path.slice(0, i))
  }
  return [...dirs].filter((d) => !whitelist.value.includes(d))
})

let unsub: (() => void) | null = null

const STATUS_LABEL: Record<string, string> = {
  ready: '已索引',
  pending: '待索引',
  failed: '失败',
  excluded: '白名单外',
}

async function refresh() {
  try {
    stats.value = await http.get<Stats>('/api/kb/stats')
    whitelist.value = (await http.get<{ folders: string[] }>('/api/kb/whitelist')).folders
    const q = statusFilter.value ? `?indexed_status=${statusFilter.value}` : ''
    notes.value = await http.get<KbNote[]>(`/api/kb/notes${q}`)
    errorMsg.value = ''
  } catch (e) {
    errorMsg.value = (e as Error).message
  }
}

function addFolder() {
  const f = newFolder.value.trim()
  if (!f) {
    ui.toast('warn', '请先输入 vault 根目录下的子文件夹名')
    return
  }
  if (whitelist.value.includes(f)) {
    ui.toast('info', `「${f}」已在白名单中`)
    newFolder.value = ''
    return
  }
  whitelist.value.push(f)
  newFolder.value = ''
  void saveWhitelist()
}

async function removeFolder(f: string) {
  whitelist.value = whitelist.value.filter((x) => x !== f)
  await saveWhitelist()
}

async function saveWhitelist() {
  try {
    const r = await http.put<WhitelistResp>('/api/kb/whitelist', { folders: whitelist.value })
    if (r.invalid?.length) {
      ui.toast('warn', `以下目录不存在（保留配置，待目录创建后生效）：${r.invalid.join('、')}`)
    } else {
      ui.toast('success', `白名单已更新（已索引 ${r.indexed ?? 0} 篇，移出删块 ${r.excluded ?? 0} 篇）`)
    }
    await refresh()
  } catch (e) {
    ui.toast('error', `白名单更新失败：${(e as Error).message}`)
  }
}

async function reindexAll() {
  reindexing.value = true
  try {
    const r = await http.post<{ done: number; failed: number; total: number }>('/api/kb/reindex')
    ui.toast('success', `全量重建完成：${r.done}/${r.total} 成功，${r.failed} 失败`)
    await refresh()
  } catch (e) {
    ui.toast('error', `全量重建失败：${(e as Error).message}`)
  } finally {
    reindexing.value = false
  }
}

async function reindexOne(path: string) {
  try {
    const r = await http.post<{ status: string; chunks: number }>('/api/kb/notes/reindex', { path })
    ui.toast('success', `「${path}」重索引完成：${r.chunks} 块`)
    await refresh()
  } catch (e) {
    ui.toast('error', `重索引失败：${(e as Error).message}`)
  }
}

onMounted(async () => {
  await refresh()
  // index.updated 推送 → 状态自动刷新（api-rules：推送不作唯一来源，REST 全量对账）
  const { eventsSocket } = await import('@/api')
  unsub = eventsSocket.on('index.updated', () => refresh())
})

onUnmounted(() => {
  unsub?.()
})
</script>

<template>
  <div class="kb-page">
    <div v-if="errorMsg" class="error-box">{{ errorMsg }}</div>

    <!-- 检索库统计（R4） -->
    <div v-if="stats" class="stat-row">
      <div class="stat"><span class="num">{{ stats.notes }}</span><span class="lbl">笔记</span></div>
      <div class="stat"><span class="num">{{ stats.manuals }}</span><span class="lbl">手册</span></div>
      <div class="stat"><span class="num">{{ stats.chunks }}</span><span class="lbl">切块</span></div>
      <div class="stat">
        <span class="num small">{{ stats.vector_model.model_id || '—' }}</span>
        <span class="lbl">向量模型{{ stats.vector_model.dim ? ` · ${stats.vector_model.dim} 维` : '（未配置，仅关键词检索）' }}</span>
      </div>
      <button class="btn primary" :disabled="reindexing" @click="reindexAll">
        {{ reindexing ? '重建中…' : '全量重建索引' }}
      </button>
    </div>

    <!-- 白名单（Q-05 一期文件夹级） -->
    <div class="card">
      <div class="card-head"><b>索引白名单（文件夹级）</b></div>
      <div class="wl-body">
        <div class="wl-tags">
          <span v-for="f in whitelist" :key="f" class="wl-tag">
            {{ f }}
            <button class="wl-x" title="移出白名单（将删除其索引块）" @click="removeFolder(f)">×</button>
          </span>
          <span v-if="!whitelist.length" class="muted">（空）</span>
        </div>
        <div class="wl-add">
          <input
            v-model="newFolder"
            placeholder="文件夹名 / vault 相对路径 / 绝对路径（如 D:\\Notes\\xx）"
            list="kb-folder-suggestions"
            @keyup.enter="addFolder"
          />
          <datalist id="kb-folder-suggestions">
            <option v-for="d in folderSuggestions" :key="d" :value="d" />
          </datalist>
          <button class="btn" @click="addFolder">添加</button>
        </div>
        <p class="hint">
          售后笔记文件夹根下的文件始终参与索引；子文件夹需加入白名单。也支持添加任意文件夹的绝对路径（如
          D:\Notes\20 Areas\...\Python）作为知识库来源——外部文件夹仅进检索，不进售后看板。移出后其索引块将被删除。
        </p>
      </div>
    </div>

    <!-- 索引状态总览 -->
    <div class="card">
      <div class="card-head">
        <b>笔记索引状态</b>
        <select v-model="statusFilter" class="filter" @change="refresh">
          <option value="">全部</option>
          <option value="ready">已索引</option>
          <option value="pending">待索引</option>
          <option value="failed">失败</option>
          <option value="excluded">白名单外</option>
        </select>
      </div>
      <table class="kb-table">
        <thead>
          <tr><th>笔记</th><th>客户</th><th>状态</th><th>块数</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="n in notes" :key="n.path">
            <td class="mono">{{ n.path }}<span v-if="n.head_ok === 0" class="badge warn">缺头</span></td>
            <td>{{ n.customer || '—' }}</td>
            <td>
              <span class="badge" :class="n.indexed_status === 'ready' ? 'ok' : n.indexed_status === 'failed' ? 'bad' : 'warn'">
                {{ STATUS_LABEL[n.indexed_status ?? ''] ?? (n.indexed_status || '未索引') }}
              </span>
              <div v-if="n.last_error" class="err-text">{{ n.last_error }}</div>
            </td>
            <td>{{ n.chunks }}</td>
            <td><button class="btn mini" @click="reindexOne(n.path)">重建</button></td>
          </tr>
          <tr v-if="!notes.length"><td colspan="5" class="muted">暂无笔记</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.kb-page { display: flex; flex-direction: column; gap: 12px; height: 100%; overflow-y: auto; }
.error-box { padding: 12px 16px; background: #fffbeb; color: #b45309; border-radius: 8px; font-size: 13px; }

.stat-row { display: flex; gap: 10px; align-items: stretch; }
.stat {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: var(--panel-bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px 8px;
}
.stat .num { font-size: 22px; font-weight: 700; }
.stat .num.small { font-size: 13px; word-break: break-all; }
.stat .lbl { font-size: 11px; color: var(--text-muted); margin-top: 4px; text-align: center; }

.card { background: var(--panel-bg); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.card-head { display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; border-bottom: 1px solid var(--border); }

.wl-body { padding: 12px 14px; }
.wl-tags { display: flex; flex-wrap: wrap; gap: 8px; }
.wl-tag { display: inline-flex; align-items: center; gap: 4px; background: #eff6ff; color: #1d4ed8; border-radius: 6px; padding: 4px 8px; font-size: 13px; }
.wl-x { border: none; background: none; cursor: pointer; color: #1d4ed8; font-size: 14px; padding: 0 2px; }
.wl-add { display: flex; gap: 8px; margin-top: 10px; }
.wl-add input { flex: 1; padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.hint { font-size: 11px; color: var(--text-muted); margin: 8px 0 0; }

.filter { padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.kb-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.kb-table th, .kb-table td { text-align: left; padding: 8px 14px; border-bottom: 1px solid var(--border); }
.kb-table th { font-size: 11px; color: var(--text-muted); font-weight: 600; }
.mono { font-family: Consolas, monospace; font-size: 12px; }
.badge { font-size: 11px; border-radius: 4px; padding: 2px 6px; }
.badge.ok { background: #dcfce7; color: #15803d; }
.badge.warn { background: #fef3c7; color: #b45309; margin-left: 6px; }
.badge.bad { background: #fee2e2; color: #b91c1c; }
.err-text { font-size: 11px; color: #b91c1c; margin-top: 4px; max-width: 420px; word-break: break-all; }
.muted { color: var(--text-muted); font-size: 12px; }

.btn { padding: 6px 12px; border: 1px solid var(--border); background: var(--panel-bg); border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.mini { padding: 3px 10px; font-size: 12px; }
.btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
