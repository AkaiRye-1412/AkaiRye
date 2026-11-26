<script setup lang="ts">
/**
 * M8 系统设置（FR-CFG / NFR-SEC-03 / NFR-SEC-05 / OQ-09 / OQ-10）。
 * - api_key 掩码语义：输入框留空 = 不修改；保存与测试连接均由后端回退到已存密钥；
 * - 配置分节保存（PUT /api/config 局部更新，热生效广播 config.updated）；
 * - 测试连接不落库（04 文档 3.1）。
 */
import { onMounted, reactive, ref } from 'vue'
import { backupApi, configApi, kbApi } from '@/api'
import { useConfigStore } from '@/stores/config'
import { useUiStore } from '@/stores/ui'

const configStore = useConfigStore()
const ui = useUiStore()

// ---- 表单状态（从 store 同步） ----
const llm = reactive({ base_url: '', api_key: '', model: '', temperature: 0.7 })
const embedding = reactive({ base_url: '', api_key: '', model: '' })
const paths = reactive({ vault_root: '', notes_folder: '', pdf_root: '', code_roots_text: '' })
const whitelistFolders = ref<string[]>([])
const newFolder = ref('')
const dashboardCfg = reactive({ follow_up_days: 7, default_range: 'month' })
const shortcuts = reactive({ global_ask: '', new_note: '', terminal: '', send: '' })
const backupKeep = ref(7)

const testingLlm = ref(false)
const testingEmb = ref(false)
const backupList = ref<Record<string, any>[]>([])
const backingUp = ref(false)

function syncFromStore() {
  const c = configStore.config
  Object.assign(llm, {
    base_url: c.llm?.base_url ?? '',
    api_key: '',
    model: c.llm?.model ?? '',
    temperature: c.llm?.temperature ?? 0.7,
  })
  Object.assign(embedding, {
    base_url: c.embedding?.provider ?? '',
    api_key: '',
    model: c.embedding?.model ?? '',
  })
  const p = c.paths ?? {}
  Object.assign(paths, {
    vault_root: p.vault_root ?? '',
    notes_folder: p.notes_folder ?? '',
    pdf_root: p.pdf_root ?? '',
    code_roots_text: (p.code_roots ?? []).join('\n'),
  })
  whitelistFolders.value = [...(c['index.whitelist']?.folders ?? [])]
  Object.assign(dashboardCfg, {
    follow_up_days: c.dashboard?.follow_up_days ?? 7,
    default_range: c.dashboard?.default_range ?? 'month',
  })
  Object.assign(shortcuts, c.shortcuts ?? {})
  backupKeep.value = c.backup?.keep ?? 7
}

onMounted(async () => {
  await configStore.load()
  syncFromStore()
  await refreshBackups()
})

async function refreshBackups() {
  backupList.value = (await backupApi.list()).slice(0, 5)
}

function keyPlaceholder(masked: string | undefined, set: boolean | undefined) {
  return set ? `已设置（${masked}，留空 = 不修改）` : '尚未设置'
}

function toastResult(label: string, err: unknown) {
  if (err) ui.toast('error', `${label}失败：${(err as Error).message}`)
}

// ---- 分节保存 ----
async function saveLlm() {
  try {
    await configStore.save({ llm: { base_url: llm.base_url, api_key: llm.api_key, model: llm.model, temperature: Number(llm.temperature) } })
    ui.toast('success', 'LLM 配置已保存（热生效）')
    llm.api_key = ''
  } catch (e) { toastResult('保存', e) }
}

async function saveEmbedding() {
  try {
    await configStore.save({ embedding: { mode: 'cloud', provider: embedding.base_url, api_key: embedding.api_key, model: embedding.model } })
    ui.toast('success', 'Embedding 配置已保存（热生效）')
    embedding.api_key = ''
  } catch (e) { toastResult('保存', e) }
}

async function savePaths() {
  try {
    await configStore.save({
      paths: {
        vault_root: paths.vault_root.trim(),
        notes_folder: paths.notes_folder.trim(),
        pdf_root: paths.pdf_root.trim(),
        code_roots: paths.code_roots_text.split('\n').map((s) => s.trim()).filter(Boolean),
      },
    })
    // 换了 vault 等于换了一批笔记：显式触发后台补齐索引（FR-RAG-01）
    const r = await kbApi.autofill()
    ui.toast(
      'success',
      r.started ? `路径已保存，已排入 ${r.pending} 篇待索引（后台执行）` : '路径已保存（热生效）',
    )
  } catch (e) { toastResult('保存', e) }
}

async function saveWhitelist() {
  try {
    // 走 /api/kb/whitelist：后端会「移出 → 删块三件套；纳入 → 增量索引」并广播 index.updated。
    // 直接写 config 只会存字符串，索引不会跟着变（2026-11-26 修复）。
    const r = await kbApi.saveWhitelist(whitelistFolders.value)
    whitelistFolders.value = r.folders
    if (r.invalid?.length) {
      ui.toast('warn', `目录不存在（已保留配置）：${r.invalid.join('、')}`)
    } else if (r.indexing) {
      ui.toast('info', `白名单已保存，${r.pending} 篇转入后台索引`)
    } else {
      ui.toast('success', `白名单已保存（索引 ${r.indexed} 篇，移出 ${r.excluded} 篇）`)
    }
  } catch (e) { toastResult('保存', e) }
}

function addFolder() {
  const name = newFolder.value.trim()
  if (!name) return
  if (whitelistFolders.value.includes(name)) {
    ui.toast('warn', '该文件夹已在白名单中')
    return
  }
  whitelistFolders.value.push(name)
  newFolder.value = ''
}

function removeFolder(name: string) {
  whitelistFolders.value = whitelistFolders.value.filter((f) => f !== name)
}

async function saveDashboard() {
  try {
    await configStore.save({ dashboard: { follow_up_days: Number(dashboardCfg.follow_up_days), default_range: dashboardCfg.default_range } })
    ui.toast('success', '看板参数已保存')
  } catch (e) { toastResult('保存', e) }
}

async function saveShortcuts() {
  try {
    await configStore.save({ shortcuts: { ...shortcuts } })
    ui.toast('success', '快捷键已保存')
  } catch (e) { toastResult('保存', e) }
}

async function resetShortcuts() {
  // OQ-09：可恢复默认快捷键（后端 DEFAULT_CONFIG 为准）
  try {
    await configStore.save({ shortcuts: { global_ask: 'Ctrl+K', new_note: 'Ctrl+N', terminal: 'Ctrl+T', send: 'Enter' } })
    syncFromStore()
    ui.toast('success', '快捷键已恢复默认')
  } catch (e) { toastResult('恢复默认', e) }
}

// ---- 测试连接（不落库） ----
async function doTestLlm() {
  testingLlm.value = true
  try {
    const r = await configApi.testLlm({ base_url: llm.base_url, api_key: llm.api_key, model: llm.model, temperature: Number(llm.temperature) })
    if (r.ok) ui.toast('success', `LLM 连通正常：${r.latency_ms}ms（模型 ${r.model}）`)
    else ui.toast('error', `LLM 连通失败：${r.error}`)
  } catch (e) { toastResult('测试连接', e) } finally { testingLlm.value = false }
}

async function doTestEmbedding() {
  testingEmb.value = true
  try {
    const r = await configApi.testEmbedding({ base_url: embedding.base_url, api_key: embedding.api_key, model: embedding.model })
    if (r.ok) ui.toast('success', `Embedding 连通正常：${r.latency_ms}ms，维度 dim=${r.dim}`)
    else ui.toast('error', `Embedding 连通失败：${r.error}`)
  } catch (e) { toastResult('测试连接', e) } finally { testingEmb.value = false }
}

// ---- 备份 ----
async function doBackup() {
  backingUp.value = true
  try {
    const r = await backupApi.create()
    if (r.status === 'ok') ui.toast('success', '备份完成')
    else ui.toast('error', `备份失败：${r.error}`)
    await refreshBackups()
  } catch (e) { toastResult('备份', e) } finally { backingUp.value = false }
}

async function saveBackup() {
  try {
    await configStore.save({ backup: { keep: Number(backupKeep.value) } })
    ui.toast('success', '备份保留份数已保存')
  } catch (e) { toastResult('保存', e) }
}
</script>

<template>
  <div class="settings-page">
    <h1>系统设置</h1>
    <p class="hint">配置保存后热生效，无需重启；API Key 只写不读（留空 = 不修改）。</p>

    <!-- LLM -->
    <section>
      <h2>LLM 问答模型</h2>
      <div class="grid">
        <label>Base URL（OpenAI 兼容）<input v-model="llm.base_url" placeholder="https://ark.cn-beijing.volces.com/api/v3" /></label>
        <label>API Key<input v-model="llm.api_key" type="password" :placeholder="keyPlaceholder(configStore.config.llm?.api_key_masked, configStore.config.llm?.api_key_set)" autocomplete="new-password" /></label>
        <label>模型名<input v-model="llm.model" placeholder="doubao-pro-32k / deepseek-chat 等" /></label>
        <label>Temperature<input v-model.number="llm.temperature" type="number" step="0.1" min="0" max="2" /></label>
      </div>
      <div class="actions">
        <button :disabled="testingLlm" @click="doTestLlm">{{ testingLlm ? '测试中…' : '测试连接' }}</button>
        <button class="primary" @click="saveLlm">保存</button>
      </div>
    </section>

    <!-- Embedding -->
    <section>
      <h2>Embedding 向量模型</h2>
      <div class="grid">
        <label>Base URL（OpenAI 兼容）<input v-model="embedding.base_url" /></label>
        <label>API Key<input v-model="embedding.api_key" type="password" :placeholder="keyPlaceholder(configStore.config.embedding?.api_key_masked, configStore.config.embedding?.api_key_set)" autocomplete="new-password" /></label>
        <label>模型名<input v-model="embedding.model" /></label>
        <label>向量维度（实测回填）<input :value="configStore.config.embedding?.dim ?? '—'" disabled /></label>
      </div>
      <div class="actions">
        <button :disabled="testingEmb" @click="doTestEmbedding">{{ testingEmb ? '测试中…' : '测试连接' }}</button>
        <button class="primary" @click="saveEmbedding">保存</button>
      </div>
    </section>

    <!-- 路径 -->
    <section>
      <h2>路径配置</h2>
      <div class="grid">
        <label>Obsidian Vault 根目录<input v-model="paths.vault_root" placeholder="D:\Notes\MyVault" /></label>
        <label>售后笔记文件夹名<input v-model="paths.notes_folder" /></label>
        <label>PDF 手册根目录<input v-model="paths.pdf_root" placeholder="D:\Manuals" /></label>
      </div>
      <label class="full">代码仓库根目录（每行一个，三期使用）<textarea v-model="paths.code_roots_text" rows="3" /></label>
      <div class="actions">
        <button class="primary" @click="savePaths">保存</button>
      </div>
      <p v-if="configStore.status" class="hint">
        校验：vault_root <i class="status-dot" :class="configStore.status.vault_root_valid ? 'ok' : 'err'" />
        pdf_root <i class="status-dot" :class="configStore.status.pdf_root_valid ? 'ok' : 'err'" />
        （保存后自动校验目录有效性）
      </p>
    </section>

    <!-- 白名单 -->
    <section>
      <h2>索引白名单（一期文件夹级，Q-05）</h2>
      <p class="hint">纳入白名单的文件夹中的笔记才会建立索引并参与 RAG 检索（FR-RAG-02）。</p>
      <div class="folders">
        <span v-for="f in whitelistFolders" :key="f" class="folder-chip">
          {{ f }}
          <button class="chip-x" @click="removeFolder(f)">×</button>
        </span>
        <span v-if="!whitelistFolders.length" class="hint">（空——暂无纳入索引的文件夹）</span>
      </div>
      <div class="actions">
        <input v-model="newFolder" placeholder="vault 内相对文件夹名，如：客户售后问题" @keyup.enter="addFolder" />
        <button @click="addFolder">添加</button>
        <button class="primary" @click="saveWhitelist">保存</button>
      </div>
    </section>

    <!-- 看板 / 快捷键 / 备份 -->
    <section>
      <h2>看板参数</h2>
      <div class="grid">
        <label>待跟进判定天数<input v-model.number="dashboardCfg.follow_up_days" type="number" min="1" /></label>
        <label>默认时间范围
          <select v-model="dashboardCfg.default_range">
            <option value="month">本月</option>
            <option value="30d">近 30 天</option>
            <option value="6m">近 6 月</option>
          </select>
        </label>
      </div>
      <div class="actions"><button class="primary" @click="saveDashboard">保存</button></div>
    </section>

    <section>
      <h2>快捷键（OQ-09）</h2>
      <div class="grid">
        <label>全局提问<input v-model="shortcuts.global_ask" /></label>
        <label>新建记录<input v-model="shortcuts.new_note" /></label>
        <label>终端<input v-model="shortcuts.terminal" /></label>
        <label>发送<input v-model="shortcuts.send" /></label>
      </div>
      <div class="actions">
        <button @click="resetShortcuts">恢复默认</button>
        <button class="primary" @click="saveShortcuts">保存</button>
      </div>
    </section>

    <section>
      <h2>备份（OQ-10）</h2>
      <div class="grid">
        <label>保留份数<input v-model.number="backupKeep" type="number" min="1" max="30" /></label>
      </div>
      <div class="actions">
        <button class="primary" :disabled="backingUp" @click="doBackup">{{ backingUp ? '备份中…' : '立即备份' }}</button>
        <button @click="saveBackup">保存保留份数</button>
      </div>
      <table class="backup-table">
        <thead><tr><th>时间</th><th>类型</th><th>大小</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in backupList" :key="b.id">
            <td>{{ b.created_at?.replace('T', ' ').replace('Z', '') }}</td>
            <td>{{ b.kind === 'daily' ? '每日' : '整理回滚' }}</td>
            <td>{{ b.size ? (b.size / 1024).toFixed(0) + ' KB' : '—' }}</td>
            <td>{{ b.status === 'ok' ? '✅' : '❌' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 数据外传说明（NFR-SEC-05 / AC-10） -->
    <section>
      <h2>数据外传说明</h2>
      <p class="hint">
        使用 RAG 检索与问答时，笔记/手册<strong>片段内容</strong>会发送至你配置的云端 LLM / Embedding 服务用于计算；
        API Key 仅保存在本机数据库，不写入日志、不出后端。请确认你接受后再使用问答功能。
      </p>
    </section>
  </div>
</template>

<style scoped>
.settings-page { max-width: 720px; }
h1 { font-size: 18px; margin-bottom: 4px; }
h2 { font-size: 15px; margin-bottom: 10px; }
.hint { color: var(--text-muted); font-size: 12px; margin-bottom: 10px; line-height: 1.7; }

section {
  background: var(--panel-bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 14px;
}

.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); }
label.full { margin-top: 10px; }

input, select, textarea {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
  font-size: 13px;
  font-family: inherit;
  background: #fff;
  color: var(--text);
}
input:focus, select:focus, textarea:focus { outline: 2px solid color-mix(in srgb, var(--accent) 40%, transparent); border-color: var(--accent); }
input[disabled] { background: var(--bg); color: var(--text-muted); }

.actions { display: flex; gap: 8px; margin-top: 12px; }
.actions input { flex: 1; }
button {
  border: 1px solid var(--border);
  background: var(--bg);
  border-radius: 6px;
  padding: 7px 16px;
  font-size: 13px;
  cursor: pointer;
}
button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.primary:hover:not(:disabled) { opacity: 0.9; color: #fff; }
button:disabled { opacity: 0.5; cursor: not-allowed; }

.folders { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.folder-chip {
  display: inline-flex; align-items: center; gap: 4px;
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  color: var(--accent);
  border-radius: 12px; padding: 3px 6px 3px 10px; font-size: 12px;
}
.chip-x { border: none; background: none; cursor: pointer; color: var(--accent); padding: 0 2px; font-size: 14px; }

.backup-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }
.backup-table th, .backup-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
.backup-table th { color: var(--text-muted); font-weight: 500; }
</style>
