<script setup lang="ts">
/**
 * M2 售后笔记（FR-NOTE / FR-PROJ）：
 * - 客户分组列表（note_file 扫描缓存，GET /api/notes 触发增量重扫保证与磁盘一致）；
 * - 编辑器（textarea 起步，CodeMirror 6 视需要引入）：保存带 base_hash，409 冲突三选（FR-NOTE-05）；
 * - 新建记录（FR-NOTE-02/10）：客户 + 项目名 + 开始日期，命名规范落 vault；
 * - 归档/恢复（FR-NOTE-09，PATCH /api/projects/{path}）；
 * - 打开笔记时上报 context（FR-RAG-18 自动感知联动）。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { eventsSocket, notesApi, projectsApi } from '@/api'
import { useRagStore } from '@/stores/rag'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const rag = useRagStore()
const route = useRoute()
const router = useRouter()

interface NoteRow {
  path: string
  customer: string | null
  project_name: string | null
  status: string
  start_date: string | null
  updated_date: string | null
  head_ok: number
  indexed_status: string
}

const notes = ref<NoteRow[]>([])
const keyword = ref('')
const selectedPath = ref('')
const content = ref('')
const baseHash = ref<string | null>(null)
const fields = ref<Record<string, string>>({})
const headOk = ref(true)
const missingFields = ref<string[]>([])
const dirty = ref(false)
const saving = ref(false)
const loading = ref(false)

// 新建对话框
const showCreate = ref(false)
const newCustomer = ref('')
const newProject = ref('')
const newStart = ref(new Date().toISOString().slice(0, 10))

// 冲突对话框（FR-NOTE-05 三选）
const showConflict = ref(false)
const saveAsName = ref('')

const errorMsg = ref('')

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return notes.value
  return notes.value.filter(
    (n) =>
      (n.project_name ?? '').toLowerCase().includes(kw) ||
      (n.customer ?? '').toLowerCase().includes(kw) ||
      n.path.toLowerCase().includes(kw),
  )
})

const grouped = computed(() => {
  const map = new Map<string, NoteRow[]>()
  for (const n of filtered.value) {
    const key = n.customer || '（未识别客户）'
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(n)
  }
  return [...map.entries()]
})

const currentNote = computed(() => notes.value.find((n) => n.path === selectedPath.value))

async function refreshList() {
  try {
    notes.value = (await notesApi.list()) as NoteRow[]
    errorMsg.value = ''
  } catch (e) {
    errorMsg.value = (e as Error).message
  }
}

async function openNote(path: string) {
  if (dirty.value && !window.confirm('当前有未保存修改，切换笔记将丢弃，确定？')) return
  loading.value = true
  try {
    const info = await notesApi.get(path)
    selectedPath.value = path
    content.value = info.content
    baseHash.value = info.content_hash
    fields.value = info.fields ?? {}
    headOk.value = info.head_ok
    missingFields.value = info.missing_fields ?? []
    dirty.value = false
    // FR-RAG-18：上报当前打开笔记上下文，供 RAG 面板自动感知
    // 走 store 而非直接 WS：面板的"范围"提示与请求 scope 都读同一份状态，避免前后不一致
    rag.setContext({
      notePath: path,
      customer: info.fields?.name ?? undefined,
      project: info.fields?.project ?? undefined,
    })
  } catch (e) {
    ui.toast('error', `打开笔记失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

async function doSave(force = false) {
  if (!selectedPath.value) return
  saving.value = true
  try {
    const resp = await notesApi.save(selectedPath.value, { content: content.value, base_hash: baseHash.value }, { force })
    baseHash.value = resp.content_hash
    dirty.value = false
    ui.toast('success', '已保存（增量索引将由知识库自动处理）')
    await refreshList()
  } catch (e) {
    const err = e as Error & { code?: string }
    if (err.code === 'edit_conflict' || err.message.includes('外部修改')) {
      showConflict.value = true
      saveAsName.value = selectedPath.value
    } else {
      ui.toast('error', `保存失败：${err.message}`)
    }
  } finally {
    saving.value = false
  }
}

async function conflictReload() {
  showConflict.value = false
  try {
    // 重新加载 = 以服务器当前内容替换编辑区（FR-NOTE-05 三选之一）
    const resp = await notesApi.save(selectedPath.value, { content: '', base_hash: null }, { reload: true })
    content.value = resp.content
    baseHash.value = resp.content_hash
    fields.value = resp.fields ?? {}
    headOk.value = resp.head_ok
    missingFields.value = resp.missing_fields ?? []
    dirty.value = false
    ui.toast('success', '已重新加载服务器当前内容')
  } catch (e) {
    ui.toast('error', `重新加载失败：${(e as Error).message}`)
  }
}

async function conflictOverwrite() {
  showConflict.value = false
  await doSave(true)
}

async function conflictSaveAs() {
  showConflict.value = false
  try {
    await notesApi.saveAs(selectedPath.value, content.value, saveAsName.value)
    ui.toast('success', '已另存副本（原文件未改动）')
    dirty.value = false
    await refreshList()
  } catch (e) {
    ui.toast('error', `另存副本失败：${(e as Error).message}`)
  }
}

async function createNote() {
  const customer = newCustomer.value.trim()
  const project = newProject.value.trim()
  if (!customer || !project) {
    ui.toast('warn', '客户与项目名为必填')
    return
  }
  try {
    const row = await notesApi.create({ customer, project_name: project, start_date: newStart.value })
    ui.toast('success', '已创建售后记录（模板含固定头部与日期小节）')
    showCreate.value = false
    newProject.value = ''
    await refreshList()
    await openNote(row.path)
  } catch (e) {
    const err = e as Error & { code?: string }
    if (err.code === 'path_conflict') ui.toast('error', '同名笔记已存在（一项目一笔记，请在既有笔记续写）')
    else ui.toast('error', `创建失败：${err.message}`)
  }
}

async function toggleArchive() {
  if (!currentNote.value) return
  const next = currentNote.value.status === 'active' ? 'archived' : 'active'
  try {
    await projectsApi.patchStatus(selectedPath.value, next as 'active' | 'archived')
    ui.toast('success', next === 'archived' ? '已归档' : '已恢复为进行中')
    await refreshList()
  } catch (e) {
    ui.toast('error', `状态变更失败：${(e as Error).message}`)
  }
}

let unsubNoteChanged: (() => void) | null = null

onMounted(async () => {
  await refreshList()
  // 首页等外部入口：?path= 打开指定笔记；?new=1 唤起新建对话框（FR-HOME-03）
  const q = route.query
  if (typeof q.path === 'string' && q.path) {
    await openNote(q.path)
    router.replace({ query: {} })
  } else if (q.new === '1') {
    showCreate.value = true
    router.replace({ query: {} })
  }
  // FR-NOTE-06：外部修改推送 → 刷新列表；正在编辑的笔记提示冲突
  unsubNoteChanged = eventsSocket.on('note.changed', (payload: unknown) => {
    const p = payload as { path: string; change: string }
    if (p.path === selectedPath.value && p.change !== 'deleted') {
      // 仅提示：保存时仍会以 base_hash 拦截静默覆盖
      ui.toast('warn', `笔记「${p.path}」被外部修改，保存前请注意`)
    }
    refreshList()
  })
})

onUnmounted(() => {
  unsubNoteChanged?.()
})
</script>

<template>
  <div class="notes-page">
    <!-- 左侧列表 -->
    <aside class="notes-list">
      <div class="list-head">
        <input v-model="keyword" class="search" placeholder="搜索客户 / 项目 / 文件名" />
        <button class="btn primary" @click="showCreate = true">+ 新建记录</button>
      </div>
      <div v-if="errorMsg" class="list-error">
        {{ errorMsg }}
        <router-link to="/settings">前往系统设置配置 vault 路径 →</router-link>
      </div>
      <div v-else class="groups">
        <div v-for="[customer, items] in grouped" :key="customer" class="group">
          <div class="group-title">
            {{ customer }}
            <span class="count">{{ items.length }}</span>
          </div>
          <div
            v-for="n in items"
            :key="n.path"
            class="note-item"
            :class="{ active: n.path === selectedPath }"
            @click="openNote(n.path)"
          >
            <div class="note-title">
              {{ n.project_name || n.path }}
              <span v-if="n.head_ok === 0" class="badge warn" title="头部字段缺失（整理向导可补齐）">缺头</span>
            </div>
            <div class="note-meta">
              <span class="status-dot" :class="n.status === 'active' ? 'ok' : 'warn'" />
              {{ n.status === 'active' ? '进行中' : '已归档' }}
              · 更新 {{ n.updated_date || '—' }}
            </div>
          </div>
        </div>
        <div v-if="!grouped.length" class="empty">暂无售后笔记</div>
      </div>
    </aside>

    <!-- 右侧编辑器 -->
    <section class="editor-pane">
      <template v-if="selectedPath">
        <div class="editor-head">
          <div>
            <h2>{{ currentNote?.project_name || selectedPath }}</h2>
            <div class="editor-sub">
              <span class="status-dot" :class="currentNote?.status === 'active' ? 'ok' : 'warn'" />
              {{ currentNote?.status === 'active' ? '进行中' : '已归档' }}
              · {{ currentNote?.customer || '未识别客户' }}
              · 开始 {{ currentNote?.start_date || '—' }}
            </div>
          </div>
          <div class="editor-actions">
            <button class="btn" @click="toggleArchive">
              {{ currentNote?.status === 'active' ? '归档' : '恢复' }}
            </button>
            <button class="btn primary" :disabled="!dirty || saving" @click="doSave(false)">
              {{ saving ? '保存中…' : dirty ? '保存' : '已保存' }}
            </button>
          </div>
        </div>
        <div v-if="!headOk" class="head-warn">
          头部字段缺失：{{ missingFields.join('、') }}（保存不受影响，可由三期存量整理补齐）
        </div>
        <textarea v-model="content" class="md-editor" spellcheck="false" @input="dirty = true" />
      </template>
      <div v-else class="editor-empty">
        <p>从左侧选择一份售后记录，或新建一条。</p>
        <p class="hint">一项目一份主笔记，后续处理按日期小节续写（FR-NOTE-08）。</p>
      </div>
    </section>

    <!-- 新建对话框 -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal">
        <h3>新建售后记录</h3>
        <label>客户（frontmatter name）<input v-model="newCustomer" placeholder="如：星河纸业" /></label>
        <label>项目名<input v-model="newProject" placeholder="如：复卷机张力控制异常" /></label>
        <label>开始日期<input v-model="newStart" type="date" /></label>
        <p class="hint">文件将写入 notes_folder：{{ newCustomer }}-{{ newProject }}.md</p>
        <div class="modal-actions">
          <button class="btn" @click="showCreate = false">取消</button>
          <button class="btn primary" @click="createNote">创建</button>
        </div>
      </div>
    </div>

    <!-- 冲突三选对话框（FR-NOTE-05） -->
    <div v-if="showConflict" class="modal-mask">
      <div class="modal">
        <h3>检测到外部修改</h3>
        <p>
          笔记「{{ selectedPath }}」在您编辑期间被外部（如 Obsidian）修改过。
          为避免静默覆盖，请选择处理方式：
        </p>
        <div class="modal-actions column">
          <button class="btn" @click="conflictReload">重新加载（丢弃我的修改）</button>
          <button class="btn warn" @click="conflictOverwrite">用我的覆盖外部修改</button>
          <div class="save-as-row">
            <input v-model="saveAsName" placeholder="副本文件名" />
            <button class="btn" @click="conflictSaveAs">另存副本</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.notes-page { display: flex; gap: 14px; height: 100%; min-height: 0; }
.notes-list { width: 280px; flex-shrink: 0; display: flex; flex-direction: column; background: var(--panel-bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.list-head { padding: 10px; display: flex; flex-direction: column; gap: 8px; border-bottom: 1px solid var(--border); }
.search { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.list-error { padding: 14px; font-size: 13px; color: #b45309; background: #fffbeb; }
.list-error a { color: var(--accent); }
.groups { flex: 1; overflow-y: auto; padding: 8px; }
.group-title { font-size: 12px; color: var(--text-muted); margin: 10px 4px 4px; font-weight: 600; }
.group-title .count { font-weight: 400; margin-left: 4px; }
.note-item { padding: 8px 10px; border-radius: 6px; cursor: pointer; }
.note-item:hover { background: var(--bg); }
.note-item.active { background: #eff6ff; }
.note-title { font-size: 13px; font-weight: 500; }
.badge.warn { font-size: 10px; background: #fef3c7; color: #b45309; border-radius: 4px; padding: 1px 4px; margin-left: 6px; }
.note-meta { font-size: 11px; color: var(--text-muted); margin-top: 3px; }
.empty { padding: 20px; text-align: center; color: var(--text-muted); font-size: 13px; }

.editor-pane { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--panel-bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.editor-head { display: flex; justify-content: space-between; align-items: flex-start; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.editor-head h2 { font-size: 16px; margin: 0; }
.editor-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.editor-actions { display: flex; gap: 8px; }
.head-warn { padding: 8px 16px; font-size: 12px; background: #fffbeb; color: #b45309; border-bottom: 1px solid var(--border); }
.md-editor { flex: 1; width: 100%; border: none; outline: none; resize: none; padding: 16px; font-family: Consolas, 'Courier New', monospace; font-size: 13px; line-height: 1.7; }
.editor-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); font-size: 14px; }
.editor-empty .hint { font-size: 12px; }

.btn { padding: 6px 12px; border: 1px solid var(--border); background: var(--panel-bg); border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn:hover { background: var(--bg); }
.btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn.primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.warn { background: #fef3c7; border-color: #f59e0b; }

.modal-mask { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.35); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: var(--panel-bg); border-radius: 10px; padding: 20px; width: 420px; }
.modal h3 { margin: 0 0 12px; font-size: 15px; }
.modal label { display: block; font-size: 12px; color: var(--text-muted); margin: 10px 0 4px; }
.modal input { width: 100%; padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; margin-top: 4px; }
.modal .hint { font-size: 11px; color: var(--text-muted); margin: 8px 0 0; word-break: break-all; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }
.modal-actions.column { flex-direction: column; align-items: stretch; }
.save-as-row { display: flex; gap: 8px; }
</style>
