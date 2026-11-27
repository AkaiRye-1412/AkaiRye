<script setup lang="ts">
/**
 * M8 系统设置（FR-CFG / NFR-SEC-03 / NFR-SEC-05 / OQ-09 / OQ-10）。
 *
 * 交互约定：
 * - 整页一份「未保存修改」状态：任一字段与上次落库快照不一致即算脏，顶部统一保存；
 * - 分节提交：只有脏的节才发 PUT /api/config，互不牵连；api_key 留空 = 不修改；
 * - 路径保存后触发后台补齐索引；白名单走 /api/kb/whitelist（移出要删块，不能只写配置）；
 * - 测试连接不落库，Embedding 实测维度回填到表单（保存后生效，与索引维度不一致会提示）；
 * - 保存后热生效（config.updated 广播）。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { configApi, kbApi, reposApi, type Brand, type KbStats } from '@/api'
import { useConfigStore } from '@/stores/config'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'

const configStore = useConfigStore()
const ui = useUiStore()

// ---- 常量 ------------------------------------------------------------------

/** 常见 OpenAI 兼容厂商：选择后自动填 base_url / model，仍可手改 */
const CUSTOM_PROVIDER = '自定义 / OpenAI 兼容'
const PRESETS: Record<string, { base_url: string; model: string }> = {
  '火山方舟（Agent Plan）': {
    base_url: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-seed-1.6-pro',
  },
  DeepSeek: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  阿里云百炼: {
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
  },
  [CUSTOM_PROVIDER]: { base_url: '', model: '' },
}

const RANGE_LABEL: Record<string, string> = { month: '本月', '30d': '近 30 天', '6m': '近 6 个月' }

// ---- 表单状态 --------------------------------------------------------------

const llm = reactive({
  provider: CUSTOM_PROVIDER,
  base_url: '',
  api_key: '',
  model: '',
  temperature: 0.7,
})
const embedding = reactive({ mode: 'cloud', provider: '', api_key: '', model: '', dim: 0 })
const paths = reactive({
  vault_root: '',
  notes_folder: '',
  pdf_root: '',
  code_roots: [] as string[],
})
const whitelist = ref<string[]>([])
const dashboard = reactive({ follow_up_days: 7, default_range: 'month' })

const showLlmKey = ref(false)
const showEmbKey = ref(false)

// ---- 页面数据 --------------------------------------------------------------

const brands = ref<Brand[]>([])
const kbStats = ref<KbStats | null>(null)

const loading = ref(true)
const saving = ref(false)
const loadError = ref('')

const newFolder = ref('')
const addingFolder = ref(false)
const newCodeRoot = ref('')
const addingCodeRoot = ref(false)
const newBrand = ref('')
const addingBrand = ref(false)
const indexing = ref(false)

let indexTimer: number | null = null

const status = computed(() => configStore.status)
const cfg = computed(() => configStore.config)
const vaultOk = computed(() => !!status.value?.vault_root_valid)
const pdfOk = computed(() => !!status.value?.pdf_root_valid)
const configComplete = computed(() => !!status.value?.config_complete)

const missingText = computed(() => {
  const map: Record<string, string> = { llm: 'LLM', embedding: 'Embedding', vault_root: 'Vault 路径' }
  return (status.value?.missing_config_keys ?? []).map((k) => map[k] ?? k).join('、')
})

const embDimMismatch = computed(() => {
  const actual = kbStats.value?.vector_model?.dim
  const configured = Number(embedding.dim) || 0
  return !!actual && !!configured && actual !== configured
})

// ---- 快照 / 脏检查 ---------------------------------------------------------

function llmPayload() {
  const temp = Number(llm.temperature)
  const p: Record<string, any> = {
    // 「自定义」不落厂商名，避免下次打开时被当成未知预设
    provider: llm.provider === CUSTOM_PROVIDER ? '' : llm.provider,
    base_url: llm.base_url.trim(),
    model: llm.model.trim(),
    temperature: Number.isFinite(temp) ? Math.min(2, Math.max(0, temp)) : 0.7,
  }
  // 空 = 不修改：把空串发给后端会被当成"清空"，这里直接不带该字段
  if (llm.api_key.trim()) p.api_key = llm.api_key.trim()
  return p
}

function embeddingPayload() {
  // 维度可手填：留空按 0（未知）处理，越界钳制，避免写进脏数据
  const dim = Math.round(Number(embedding.dim) || 0)
  const p: Record<string, any> = {
    mode: embedding.mode,
    provider: embedding.provider.trim(),
    model: embedding.model.trim(),
    dim: Math.min(8192, Math.max(0, dim)),
  }
  if (embedding.api_key.trim()) p.api_key = embedding.api_key.trim()
  return p
}

function pathsPayload() {
  return {
    vault_root: paths.vault_root.trim(),
    notes_folder: paths.notes_folder.trim(),
    pdf_root: paths.pdf_root.trim(),
    code_roots: paths.code_roots.map((s) => s.trim()).filter(Boolean),
  }
}

function dashboardPayload() {
  return {
    follow_up_days: Math.min(90, Math.max(1, Number(dashboard.follow_up_days) || 7)),
    default_range: dashboard.default_range,
  }
}

function snapshot() {
  return {
    llm: JSON.stringify(llmPayload()),
    embedding: JSON.stringify(embeddingPayload()),
    paths: JSON.stringify(pathsPayload()),
    whitelist: JSON.stringify([...whitelist.value].map((s) => s.trim()).filter(Boolean)),
    dashboard: JSON.stringify(dashboardPayload()),
  }
}

type SectionKey = keyof ReturnType<typeof snapshot>
const applied = ref<Record<SectionKey, string>>({
  llm: '',
  embedding: '',
  paths: '',
  whitelist: '',
  dashboard: '',
})

const dirty = computed(() => {
  const now = snapshot()
  return {
    llm: now.llm !== applied.value.llm,
    embedding: now.embedding !== applied.value.embedding,
    paths: now.paths !== applied.value.paths,
    whitelist: now.whitelist !== applied.value.whitelist,
    dashboard: now.dashboard !== applied.value.dashboard,
  }
})

const dirtyCount = computed(() => Object.values(dirty.value).filter(Boolean).length)
const anyDirty = computed(() => dirtyCount.value > 0)

// ---- 读配置 ----------------------------------------------------------------

function syncFromStore() {
  const c = cfg.value
  Object.assign(llm, {
    provider: c.llm?.provider || CUSTOM_PROVIDER,
    base_url: c.llm?.base_url ?? '',
    api_key: '',
    model: c.llm?.model ?? '',
    temperature: c.llm?.temperature ?? 0.7,
  })
  Object.assign(embedding, {
    mode: c.embedding?.mode ?? 'cloud',
    provider: c.embedding?.provider ?? '',
    api_key: '',
    model: c.embedding?.model ?? '',
    dim: c.embedding?.dim ?? 0,
  })
  Object.assign(paths, {
    vault_root: c.paths?.vault_root ?? '',
    notes_folder: c.paths?.notes_folder ?? '',
    pdf_root: c.paths?.pdf_root ?? '',
    code_roots: [...(c.paths?.code_roots ?? [])],
  })
  whitelist.value = [...(c['index.whitelist']?.folders ?? [])]
  Object.assign(dashboard, {
    follow_up_days: c.dashboard?.follow_up_days ?? 7,
    default_range: c.dashboard?.default_range ?? 'month',
  })
  applied.value = snapshot()
  showLlmKey.value = false
  showEmbKey.value = false
}

async function loadPage() {
  loading.value = true
  loadError.value = ''
  try {
    await configStore.load()
    syncFromStore()
    await Promise.all([refreshSide(), refreshBrands()])
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function refreshSide() {
  const s = await Promise.allSettled([kbApi.stats()])
  if (s[0].status === 'fulfilled') kbStats.value = s[0].value
}

async function refreshBrands() {
  try {
    brands.value = await configApi.brands()
  } catch {
    brands.value = []
  }
}

onMounted(loadPage)

// ---- 保存 ------------------------------------------------------------------

/**
 * 整页保存：按节提交，任何一节失败立即停止并保留表单内容（不清快照），
 * 避免"部分写入却显示已保存"。已成功的节重复提交是幂等的。
 */
async function saveAll() {
  if (!anyDirty.value || saving.value) return
  saving.value = true
  const done: string[] = []
  let saveNote = ''

  const step = async (label: string, run: () => Promise<void>) => {
    await run()
    done.push(label)
  }

  try {
    if (dirty.value.llm) {
      await step('LLM', async () => {
        await configStore.save({ llm: llmPayload() })
        llm.api_key = ''
      })
    }
    if (dirty.value.embedding) {
      await step('Embedding', async () => {
        await configStore.save({ embedding: embeddingPayload() })
        embedding.api_key = ''
      })
    }
    if (dirty.value.paths) {
      await step('路径', async () => {
        const prev = JSON.parse(applied.value.paths || '{}') as Record<string, any>
        const next = pathsPayload()
        await configStore.save({ paths: next })
        // 只有换了 vault / 笔记目录才需要重扫；只改代码根目录不该触发全库补齐
        if (prev.vault_root !== next.vault_root || prev.notes_folder !== next.notes_folder) {
          const r = await kbApi.autofill()
          if (r.started) saveNote = `已排入 ${r.pending} 篇待索引`
        }
      })
    }
    if (dirty.value.whitelist) {
      await step('索引范围', async () => {
        const r = await kbApi.saveWhitelist(
          whitelist.value.map((s) => s.trim()).filter(Boolean),
        )
        whitelist.value = r.folders
        if (r.invalid?.length) saveNote = `目录不存在：${r.invalid.join('、')}（已保留配置）`
        else if (r.indexing) saveNote = `${r.pending} 篇转入后台索引`
      })
    }
    if (dirty.value.dashboard) {
      await step('看板', async () => {
        await configStore.save({ dashboard: dashboardPayload() })
      })
    }

    // 以服务端返回为准重建表单（掩码密钥会被清掉，维度等读回）
    await configStore.load()
    syncFromStore()
    await Promise.all([refreshSide(), refreshBrands()])

    const tail = saveNote ? `；${saveNote}` : ''
    ui.toast(
      'success',
      done.length ? `已保存 ${done.join(' / ')}（热生效）${tail}` : '配置已保存',
    )
  } catch (e) {
    ui.toast('error', `保存失败：${(e as Error).message}`)
  } finally {
    saving.value = false
  }
}

/** 丢弃未保存修改：以服务端配置重建表单 */
function discardChanges() {
  syncFromStore()
  ui.toast('info', '已放弃未保存的修改')
}

// ---- LLM / Embedding -------------------------------------------------------

function onProviderChange() {
  const preset = PRESETS[llm.provider]
  if (!preset || (!preset.base_url && !preset.model)) return
  llm.base_url = preset.base_url
  llm.model = preset.model
  ui.toast('info', `已填入 ${llm.provider} 的默认接口与模型`)
}

const llmTest = reactive({ running: false, ok: null as boolean | null, text: '' })
const embTest = reactive({ running: false, ok: null as boolean | null, text: '' })

async function testLlm() {
  llmTest.running = true
  llmTest.ok = null
  llmTest.text = ''
  try {
    const r = await configApi.testLlm({
      base_url: llm.base_url.trim(),
      api_key: llm.api_key.trim(),
      model: llm.model.trim(),
      temperature: Number(llm.temperature),
    })
    if (r.ok) {
      llmTest.ok = true
      llmTest.text = `连通正常 · ${r.latency_ms} ms · ${r.model}`
    } else {
      llmTest.ok = false
      llmTest.text = r.error || '连通失败'
    }
  } catch (e) {
    llmTest.ok = false
    llmTest.text = (e as Error).message
  } finally {
    llmTest.running = false
  }
}

async function testEmbedding() {
  embTest.running = true
  embTest.ok = null
  embTest.text = ''
  try {
    const r = await configApi.testEmbedding({
      base_url: embedding.provider.trim(),
      api_key: embedding.api_key.trim(),
      model: embedding.model.trim(),
    })
    if (r.ok) {
      embTest.ok = true
      const before = Number(embedding.dim) || 0
      if (r.dim) embedding.dim = r.dim
      embTest.text = `连通正常 · ${r.latency_ms} ms · dim=${r.dim}`
      if (r.dim && before && before !== r.dim) {
        ui.toast('warn', `实测维度 ${r.dim} 与已存 ${before} 不一致，保存后需重建全部索引`)
      }
    } else {
      embTest.ok = false
      embTest.text = r.error || '连通失败'
    }
  } catch (e) {
    embTest.ok = false
    embTest.text = (e as Error).message
  } finally {
    embTest.running = false
  }
}

// ---- 路径 / 白名单 / 索引 --------------------------------------------------

async function addCodeRoot() {
  const v = newCodeRoot.value.trim()
  if (!v) return
  if (paths.code_roots.includes(v)) {
    ui.toast('warn', '该目录已在列表中')
    return
  }
  if (paths.code_roots.length >= 3) {
    ui.toast('warn', '最多添加 3 个代码根目录')
    return
  }
  paths.code_roots.push(v)
  newCodeRoot.value = ''
  addingCodeRoot.value = false
  await saveCodeRoots('已添加并保存目录')
}

async function removeCodeRoot(v: string) {
  paths.code_roots = paths.code_roots.filter((x) => x !== v)
  await saveCodeRoots('已移除并保存目录')
}

/** 代码根目录增删后立即落库并触发仓库扫描（免手动点「保存设置」） */
async function saveCodeRoots(successText: string) {
  try {
    await configStore.save({ paths: pathsPayload() })
    await configStore.load()
    syncFromStore()
    // 触发仓库后台扫描，让代码仓库页即时发现新目录
    try {
      await reposApi.refresh()
    } catch {
      /* 扫描失败不阻断保存提示 */
    }
    ui.toast('success', successText)
  } catch (e) {
    ui.toast('error', `保存失败：${(e as Error).message}`)
  }
}

function addFolder() {
  const v = newFolder.value.trim()
  if (!v) return
  if (whitelist.value.some((f) => f.toLowerCase() === v.toLowerCase())) {
    ui.toast('warn', '该目录已在索引范围内')
    return
  }
  whitelist.value.push(v)
  newFolder.value = ''
  addingFolder.value = false
}

function removeFolder(v: string) {
  whitelist.value = whitelist.value.filter((f) => f !== v)
}

async function reindexAll() {
  indexing.value = true
  try {
    const r = await kbApi.reindexAll({ force: true, background: true })
    if (!r.started) {
      ui.toast('info', r.reason || '已有索引任务进行中')
    } else {
      ui.toast('info', `已开始全量重建，共 ${r.total ?? 0} 篇（后台执行）`)
      await refreshSide()
      startIndexPolling()
    }
  } catch (e) {
    ui.toast('error', `全量重建失败：${(e as Error).message}`)
  } finally {
    indexing.value = false
  }
}

function startIndexPolling() {
  stopIndexPolling()
  indexTimer = window.setInterval(async () => {
    await refreshSide()
    if (!kbStats.value?.indexing?.running) stopIndexPolling()
  }, 2000)
}

function stopIndexPolling() {
  if (indexTimer !== null) {
    window.clearInterval(indexTimer)
    indexTimer = null
  }
}

onUnmounted(stopIndexPolling)

// ---- 品牌 ------------------------------------------------------------------

async function addBrand() {
  const v = newBrand.value.trim()
  if (!v) return
  if (brands.value.some((b) => b.name === v)) {
    ui.toast('warn', '品牌已存在')
    return
  }
  try {
    await configApi.addBrand(v)
    newBrand.value = ''
    addingBrand.value = false
    await refreshBrands()
    ui.toast('success', `已新增品牌「${v}」`)
  } catch (e) {
    ui.toast('error', `新增失败：${(e as Error).message}`)
  }
}

async function toggleBrand(b: Brand) {
  const next = !b.enabled
  try {
    await configApi.patchBrand(b.name, { enabled: next })
    await refreshBrands()
    ui.toast(
      'success',
      next ? `已启用「${b.name}」` : `已停用「${b.name}」（手册上的品牌标记仍保留）`,
    )
  } catch (e) {
    ui.toast('error', `操作失败：${(e as Error).message}`)
  }
}

// ---- 索引统计 --------------------------------------------------------------

const readyNotes = computed(() => kbStats.value?.notes_by_status?.ready ?? 0)
const totalNotes = computed(() => kbStats.value?.notes ?? 0)
const chunkCount = computed(() => kbStats.value?.chunks ?? 0)
const vectorDim = computed(() => kbStats.value?.vector_model?.dim ?? 0)
const indexRunning = computed(() => !!kbStats.value?.indexing?.running)
const indexPct = computed(() => {
  const p = kbStats.value?.indexing
  if (!p?.total) return indexRunning.value ? 8 : 0
  return Math.min(100, Math.round((p.done / p.total) * 100))
})
</script>

<template>
  <div class="page page-settings">
    <header class="settings-top">
      <div class="settings-title">
        <h2>设置</h2>
        <p>单机配置 · 密钥只存本机数据库，保存后热生效</p>
      </div>
      <div class="settings-top-actions">
        <span class="save-status" :class="{ dirty: anyDirty }">
          <span class="dot" :class="anyDirty ? 'warn' : 'ok'" />
          {{ anyDirty ? `${dirtyCount} 处未保存` : '配置已保存' }}
        </span>
        <button v-if="anyDirty" class="btn ghost" :disabled="saving" @click="discardChanges">
          放弃修改
        </button>
        <button class="btn primary" :disabled="!anyDirty || saving" @click="saveAll">
          <Icon name="save" :size="14" />
          <span>{{ saving ? '保存中…' : '保存设置' }}</span>
        </button>
      </div>
    </header>

    <div v-if="loadError" class="load-error">
      <Icon name="alert" :size="15" />
      <span>读取配置失败：{{ loadError }}</span>
      <button class="link-btn" @click="loadPage">重试</button>
    </div>

    <div class="settings-grid">
      <!-- ==================== 回答模型 ==================== -->
      <section class="card cfg-card">
        <header class="cfg-head">
          <div class="cfg-ic blue"><Icon name="sparkle" :size="17" /></div>
          <div class="cfg-head-text">
            <h3>回答模型</h3>
            <span>RAG 回答生成 · 可切换任意 OpenAI 兼容厂商</span>
          </div>
          <span class="badge" :class="cfg.llm?.api_key_set ? 'ok-soft' : 'warn'">
            {{ cfg.llm?.api_key_set ? '已配置' : '待配置' }}
          </span>
        </header>
        <div class="cfg-body">
          <label class="field">
            <span class="field-label">厂商</span>
            <select v-model="llm.provider" class="input" @change="onProviderChange">
              <option v-for="(_, name) in PRESETS" :key="name" :value="name">{{ name }}</option>
            </select>
          </label>

          <label class="field">
            <span class="field-label">接口地址 · Base URL</span>
            <input
              v-model="llm.base_url"
              class="input mono"
              placeholder="https://ark.cn-beijing.volces.com/api/v3"
              spellcheck="false"
            />
          </label>

          <label class="field">
            <span class="field-label">
              API Key
              <em class="field-badge">仅本机保存</em>
            </span>
            <div class="input-wrap">
              <input
                v-model="llm.api_key"
                class="input mono"
                :type="showLlmKey ? 'text' : 'password'"
                :placeholder="
                  cfg.llm?.api_key_set
                    ? `已设置（${cfg.llm?.api_key_masked}）· 留空不修改`
                    : '尚未设置'
                "
                autocomplete="new-password"
                spellcheck="false"
              />
              <button
                class="icon-btn inner"
                type="button"
                :title="showLlmKey ? '隐藏' : '显示'"
                @click="showLlmKey = !showLlmKey"
              >
                <Icon :name="showLlmKey ? 'eyeOff' : 'eye'" :size="15" />
              </button>
            </div>
          </label>

          <div class="cfg-row">
            <label class="field">
              <span class="field-label">模型名</span>
              <input
                v-model="llm.model"
                class="input mono"
                placeholder="deepseek-chat / doubao-…"
                spellcheck="false"
              />
            </label>
            <label class="field narrow">
              <span class="field-label">Temperature</span>
              <input
                v-model.number="llm.temperature"
                class="input mono"
                type="number"
                step="0.1"
                min="0"
                max="2"
              />
            </label>
          </div>

          <div class="cfg-actions">
            <button class="btn ghost sm" :disabled="llmTest.running" @click="testLlm">
              <Icon name="zap" :size="13" />
              <span>{{ llmTest.running ? '测试中…' : '测试连接' }}</span>
            </button>
            <span v-if="llmTest.ok !== null" class="test-result" :class="llmTest.ok ? 'ok' : 'bad'">
              <span class="dot" :class="llmTest.ok ? 'ok' : 'err'" />{{ llmTest.text }}
            </span>
            <span v-else class="cfg-hint">
              <Icon name="shield" :size="12" />Key 不写入日志、不出后端
            </span>
          </div>
        </div>
      </section>

      <!-- ==================== 向量化模型 ==================== -->
      <section class="card cfg-card">
        <header class="cfg-head">
          <div class="cfg-ic violet"><Icon name="layers" :size="17" /></div>
          <div class="cfg-head-text">
            <h3>向量化模型</h3>
            <span>笔记与 PDF 切块的索引来源</span>
          </div>
          <span class="badge" :class="cfg.embedding?.api_key_set ? 'ok-soft' : 'warn'">
            {{ cfg.embedding?.api_key_set ? '已配置' : '待配置' }}
          </span>
        </header>
        <div class="cfg-body">
          <div class="field">
            <span class="field-label">运行方式</span>
            <div class="seg sm">
              <button
                class="seg-btn"
                :class="{ active: embedding.mode === 'cloud' }"
                @click="embedding.mode = 'cloud'"
              >
                云端
              </button>
              <button class="seg-btn" disabled title="本地自托管为二期规划">
                本地自托管 <em>二期</em>
              </button>
            </div>
          </div>

          <label class="field">
            <span class="field-label">服务地址 · Base URL</span>
            <input
              v-model="embedding.provider"
              class="input mono"
              placeholder="https://ark.cn-beijing.volces.com/api/v3"
              spellcheck="false"
            />
          </label>

          <label class="field">
            <span class="field-label">
              API Key
              <em class="field-badge">仅本机保存</em>
            </span>
            <div class="input-wrap">
              <input
                v-model="embedding.api_key"
                class="input mono"
                :type="showEmbKey ? 'text' : 'password'"
                :placeholder="
                  cfg.embedding?.api_key_set
                    ? `已设置（${cfg.embedding?.api_key_masked}）· 留空不修改`
                    : '尚未设置'
                "
                autocomplete="new-password"
                spellcheck="false"
              />
              <button
                class="icon-btn inner"
                type="button"
                :title="showEmbKey ? '隐藏' : '显示'"
                @click="showEmbKey = !showEmbKey"
              >
                <Icon :name="showEmbKey ? 'eyeOff' : 'eye'" :size="15" />
              </button>
            </div>
          </label>

          <div class="cfg-row">
            <label class="field">
              <span class="field-label">模型名</span>
              <input
                v-model="embedding.model"
                class="input mono"
                placeholder="doubao-embedding-…"
                spellcheck="false"
              />
            </label>
            <label class="field narrow">
              <span class="field-label">向量维度</span>
              <input
                v-model.number="embedding.dim"
                class="input mono"
                type="number"
                min="1"
                max="8192"
                step="1"
                inputmode="numeric"
                placeholder="如 2048"
                title="「测试连接」可实测回填；手动填写须与模型实际输出维度一致"
              />
            </label>
          </div>

          <div class="cfg-actions">
            <button class="btn ghost sm" :disabled="embTest.running" @click="testEmbedding">
              <Icon name="zap" :size="13" />
              <span>{{ embTest.running ? '测试中…' : '测试连接' }}</span>
            </button>
            <span v-if="embTest.ok !== null" class="test-result" :class="embTest.ok ? 'ok' : 'bad'">
              <span class="dot" :class="embTest.ok ? 'ok' : 'err'" />{{ embTest.text }}
            </span>
            <span v-else class="cfg-hint">留空则用已存密钥测试</span>
          </div>

          <p v-if="embDimMismatch" class="cfg-note warn">
            <Icon name="alert" :size="13" />
            配置维度 {{ embedding.dim }} 与索引向量维度 {{ vectorDim }} 不一致：换模型后请重建全部索引。
          </p>
        </div>
      </section>

      <!-- ==================== 路径与索引 ==================== -->
      <section class="card cfg-card span2">
        <header class="cfg-head">
          <div class="cfg-ic amber"><Icon name="folder" :size="17" /></div>
          <div class="cfg-head-text">
            <h3>路径与索引</h3>
            <span>文件读写位置、索引范围、待跟进阈值</span>
          </div>
          <span class="badge" :class="vaultOk ? 'ok-soft' : 'warn'">
            <span class="dot" :class="vaultOk ? 'ok' : 'warn'" />
            {{ vaultOk ? 'Vault 已连接' : 'Vault 未连接' }}
          </span>
        </header>

        <div class="cfg-body cfg-grid-2">
          <div class="cfg-col">
            <label class="field">
              <span class="field-label">
                Obsidian Vault 根目录
                <span class="status-inline" :class="vaultOk ? 'ok' : 'bad'">
                  {{ vaultOk ? '有效' : '未校验' }}
                </span>
              </span>
              <input
                v-model="paths.vault_root"
                class="input mono"
                placeholder="D:\Notes"
                spellcheck="false"
              />
            </label>

            <label class="field">
              <span class="field-label">售后笔记文件夹（vault 内相对名）</span>
              <input v-model="paths.notes_folder" class="input mono" placeholder="客户售后问题" />
            </label>

            <label class="field">
              <span class="field-label">
                PDF 手册根目录
                <span class="status-inline" :class="pdfOk ? 'ok' : 'bad'">
                  {{ pdfOk ? '有效' : '未校验' }}
                </span>
              </span>
              <input
                v-model="paths.pdf_root"
                class="input mono"
                placeholder="D:\Manuals"
                spellcheck="false"
              />
            </label>

            <div class="field">
              <span class="field-label">
                代码仓库根目录
                <em class="field-badge">三期使用 · 最多 3 个</em>
              </span>
              <div class="chips">
                <span v-for="r in paths.code_roots" :key="r" class="chip" :title="r">
                  <Icon name="git" :size="12" />
                  <span class="chip-text">{{ r }}</span>
                  <button type="button" title="移除" @click="removeCodeRoot(r)">
                    <Icon name="x" :size="11" />
                  </button>
                </span>
                <span v-if="!paths.code_roots.length && !addingCodeRoot" class="chips-empty">
                  暂未添加
                </span>
                <button
                  v-if="!addingCodeRoot"
                  class="chip add"
                  type="button"
                  @click="addingCodeRoot = true"
                >
                  <Icon name="plus" :size="11" />添加目录
                </button>
                <span v-else class="chip-input">
                  <input
                    v-model="newCodeRoot"
                    class="input mono"
                    placeholder="D:\Code"
                    spellcheck="false"
                    @keyup.enter="addCodeRoot"
                    @keyup.esc="addingCodeRoot = false"
                  />
                  <button class="btn primary sm" type="button" @click="addCodeRoot">添加</button>
                </span>
              </div>
            </div>
          </div>

          <div class="cfg-col">
            <div class="field">
              <span class="field-label">
                索引范围（白名单）
                <em class="field-badge">目录级 · 一期</em>
              </span>
              <div class="chips">
                <span v-for="f in whitelist" :key="f" class="chip" :title="f">
                  <Icon name="folder" :size="12" />
                  <span class="chip-text">{{ f }}</span>
                  <button type="button" title="移出索引范围" @click="removeFolder(f)">
                    <Icon name="x" :size="11" />
                  </button>
                </span>
                <span v-if="!whitelist.length && !addingFolder" class="chips-empty">
                  空 · 暂无目录纳入索引
                </span>
                <button
                  v-if="!addingFolder"
                  class="chip add"
                  type="button"
                  @click="addingFolder = true"
                >
                  <Icon name="plus" :size="11" />添加目录
                </button>
                <span v-else class="chip-input">
                  <input
                    v-model="newFolder"
                    class="input mono"
                    placeholder="vault 内相对名或绝对路径"
                    spellcheck="false"
                    @keyup.enter="addFolder"
                    @keyup.esc="addingFolder = false"
                  />
                  <button class="btn primary sm" type="button" @click="addFolder">添加</button>
                </span>
              </div>
              <span class="field-help">
                减少目录会同时删除对应切块；新增目录保存后转后台增量索引。
              </span>
            </div>

            <div class="cfg-row">
              <label class="field narrow">
                <span class="field-label">待跟进阈值</span>
                <div class="input-suffix">
                  <input v-model.number="dashboard.follow_up_days" class="input mono" type="number" min="1" max="90" />
                  <em>天</em>
                </div>
              </label>
              <label class="field">
                <span class="field-label">看板默认范围</span>
                <select v-model="dashboard.default_range" class="input">
                  <option v-for="(label, v) in RANGE_LABEL" :key="v" :value="v">{{ label }}</option>
                </select>
              </label>
            </div>

            <div class="index-mini">
              <div class="im">
                <b>{{ readyNotes }}<em>/{{ totalNotes }}</em></b>
                <span>笔记已索引</span>
              </div>
              <div class="im">
                <b>{{ chunkCount.toLocaleString() }}</b>
                <span>切块</span>
              </div>
              <div class="im">
                <b>{{ vectorDim || '—' }}</b>
                <span>向量维度</span>
              </div>
              <button class="btn ghost sm" :disabled="indexing || indexRunning" @click="reindexAll">
                <Icon name="refresh" :size="13" />
                <span>{{ indexRunning ? '索引中…' : '重建全部索引' }}</span>
              </button>
            </div>

            <div v-if="indexRunning" class="index-progress">
              <div class="ip-head">
                <span>{{ kbStats?.indexing?.current || '正在建立索引' }}</span>
                <em>{{ indexPct }}%</em>
              </div>
              <div class="ip-bar"><i :style="{ width: indexPct + '%' }" /></div>
            </div>
          </div>
        </div>
      </section>

      <!-- ==================== 品牌枚举 ==================== -->
      <section class="card cfg-card">
        <header class="cfg-head">
          <div class="cfg-ic green"><Icon name="book" :size="17" /></div>
          <div class="cfg-head-text">
            <h3>品牌枚举</h3>
            <span>手册筛选与对比使用的固定品牌</span>
          </div>
          <span class="badge muted">{{ brands.filter((b) => b.enabled).length }} 个启用</span>
        </header>
        <div class="cfg-body">
          <div class="chips">
            <button
              v-for="b in brands"
              :key="b.name"
              class="chip brand"
              :class="{ off: !b.enabled }"
              type="button"
              :title="b.enabled ? '点击停用（筛选器中隐藏）' : '点击启用'"
              @click="toggleBrand(b)"
            >
              <span class="chip-text">{{ b.name }}</span>
              <span class="chip-state">{{ b.enabled ? '启用' : '停用' }}</span>
            </button>
            <span v-if="!brands.length && !addingBrand" class="chips-empty">
              暂无品牌 · 从 PDF 手册解析时自动积累
            </span>
            <button
              v-if="!addingBrand"
              class="chip add"
              type="button"
              @click="addingBrand = true"
            >
              <Icon name="plus" :size="11" />新增品牌
            </button>
            <span v-else class="chip-input">
              <input
                v-model="newBrand"
                class="input"
                placeholder="品牌名称"
                @keyup.enter="addBrand"
                @keyup.esc="addingBrand = false"
              />
              <button class="btn primary sm" type="button" @click="addBrand">新增</button>
            </span>
          </div>
          <p class="field-help">
            使用固定枚举而非自由输入，避免「高创 / ADVANCED / 高创科技」这类命名分裂；新增与停用即时生效，停用不会清除已有手册的品牌标记。
          </p>
        </div>
      </section>

    </div>

    <footer class="settings-foot">
      <Icon name="info" :size="13" />
      <span>
        配置保存后热生效，无需重启；当前状态：{{ configComplete ? '必备项已配置完成' : `待配置 ${missingText || '必要项'}` }}。
        索引范围与看板阈值修改后，相关页面会在下一次刷新时同步。
      </span>
    </footer>

    <div v-if="loading" class="page-loading">
      <span class="dot ok" />正在读取配置…
    </div>
  </div>
</template>

<style scoped>
.page-settings {
  container-type: inline-size;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 4px;
}

/* ---------------- 页头 ---------------- */
.settings-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  padding: 2px 2px 0;
}
.settings-title h2 {
  margin: 0 0 2px;
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.3px;
}
.settings-title p { margin: 0; font-size: 11.5px; color: var(--ink-2); }
.settings-top-actions { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.save-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: #5c5951;
  white-space: nowrap;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(231, 229, 221, 0.7);
  border-radius: 999px;
  padding: 3px 9px;
}
.save-status.dirty { color: var(--amber-ink); }

.load-error {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 12px;
  border: 1px solid var(--red-line);
  background: var(--red-soft);
  color: var(--red);
  border-radius: 10px;
  font-size: 12px;
}
.load-error .link-btn { margin-left: auto; color: var(--red); }

/* ---------------- 卡片网格 ---------------- */
.settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  align-items: start;
}
.cfg-card { overflow: hidden; }
.cfg-card.span2 { grid-column: 1 / -1; }

.cfg-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 13px 15px 12px;
  border-bottom: 1px solid var(--line-soft);
}
.cfg-head-text { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.cfg-head h3 { margin: 0; font-size: 13.5px; font-weight: 640; letter-spacing: 0.1px; }
.cfg-head-text > span { font-size: 11.5px; color: #5f5c54; line-height: 1.45; }
.cfg-head > .badge { margin-left: auto; flex: none; }
.cfg-head-right { margin-left: auto; flex: none; }

.cfg-ic {
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: var(--blue-soft);
  color: var(--blue-ink);
}
.cfg-ic.violet { background: var(--violet-soft); color: var(--violet); }
.cfg-ic.green { background: var(--green-soft); color: var(--green-ink); }
.cfg-ic.amber { background: var(--amber-soft); color: var(--amber-ink); }

.cfg-body {
  padding: 14px 15px 15px;
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.cfg-grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 22px;
}
.cfg-col { display: flex; flex-direction: column; gap: 11px; min-width: 0; }
.cfg-row { display: flex; gap: 9px; align-items: flex-start; }
.cfg-row .field { flex: 1; min-width: 0; }
.cfg-row .field.narrow { flex: 0 0 118px; }

/* ---------------- 表单 ---------------- */
.field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.field-label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ink-2);
  line-height: 1.4;
}
.field-badge {
  font-style: normal;
  font-size: 9.5px;
  font-weight: 500;
  color: var(--muted);
  background: rgba(239, 238, 232, 0.85);
  border-radius: 4px;
  padding: 1px 4px;
}
.field-help { margin: 0; font-size: 10.5px; color: #6a675f; line-height: 1.65; }
.status-inline { font-size: 9.5px; margin-left: auto; color: var(--muted); }
.status-inline.ok { color: var(--green-ink); }
.status-inline.bad { color: var(--amber-ink); }

.input {
  width: 100%;
  height: 32px;
  border: 1px solid rgba(231, 229, 221, 0.95);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.72);
  padding: 0 9px;
  font-size: 12.5px;
  color: var(--ink);
  transition: border-color 0.14s, box-shadow 0.14s, background 0.14s;
}
.input::placeholder { color: #a8a59c; }
.input:focus {
  outline: none;
  border-color: var(--blue);
  background: #fff;
  box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.12);
}
.input.mono { font-family: var(--mono); font-size: 11.5px; letter-spacing: -0.1px; }
.input[readonly] { background: rgba(246, 246, 241, 0.8); color: var(--ink-2); }
select.input { padding-right: 26px; cursor: pointer; }

.input-wrap { position: relative; }
.input-wrap .input { padding-right: 34px; }
.icon-btn.inner { position: absolute; right: 2px; top: 1px; width: 30px; height: 30px; }

.input-suffix { position: relative; }
.input-suffix .input { padding-right: 30px; }
.input-suffix em {
  position: absolute;
  right: 9px;
  top: 50%;
  transform: translateY(-50%);
  font-style: normal;
  font-size: 10.5px;
  color: var(--muted);
  pointer-events: none;
}

/* ---------------- 分段 / 动作 ---------------- */
.seg-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.cfg-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 1px; }
.cfg-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: #6a675f;
  line-height: 1.5;
}
.test-result {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  line-height: 1.5;
  max-width: 100%;
  word-break: break-word;
}
.test-result.ok { color: var(--green-ink); }
.test-result.bad { color: var(--red); }

.cfg-note {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin: 0;
  padding: 7px 9px;
  border-radius: 7px;
  background: rgba(247, 246, 241, 0.8);
  font-size: 10.5px;
  color: var(--ink-2);
  line-height: 1.6;
}
.cfg-note.warn { background: var(--amber-soft); color: var(--amber-ink); }
.cfg-note .ic { margin-top: 1px; flex: none; }

/* ---------------- chips ---------------- */
.chips { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  max-width: 100%;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.66);
  color: var(--ink-2);
  font-size: 11.5px;
  line-height: 1;
}
.chip-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px; }
.chip button {
  border: 0;
  background: none;
  padding: 0;
  display: grid;
  place-items: center;
  color: var(--muted);
  border-radius: 3px;
}
.chip button:hover { color: var(--red); }
.chip.add {
  border-style: dashed;
  border-color: var(--blue-line);
  background: rgba(234, 242, 253, 0.55);
  color: var(--blue-ink);
  cursor: pointer;
}
.chip.add:hover { background: var(--blue-soft); }
.chip.brand { cursor: pointer; font-family: inherit; }
.chip.brand:hover { border-color: var(--blue-line); }
.chip.brand .chip-state {
  font-size: 9.5px;
  color: var(--green-ink);
  background: var(--green-soft);
  border-radius: 3px;
  padding: 1px 3px;
}
.chip.brand.off { opacity: 0.55; border-style: dashed; }
.chip.brand.off .chip-state { color: var(--muted); background: rgba(239, 238, 232, 0.9); }
.chips-empty { font-size: 10.5px; color: var(--muted); }

.chip-input { display: inline-flex; align-items: center; gap: 5px; flex: 1 1 220px; min-width: 180px; }
.chip-input .input { height: 28px; font-size: 11.5px; }
.chip-input .btn { height: 28px; }

/* ---------------- 索引统计 ---------------- */
.index-mini {
  display: flex;
  align-items: flex-end;
  gap: 20px;
  padding-top: 11px;
  border-top: 1px solid var(--line-soft);
  flex-wrap: wrap;
}
.index-mini .im { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.index-mini b {
  font-size: 16px;
  font-weight: 640;
  color: var(--ink);
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.index-mini b em { font-style: normal; font-size: 10.5px; font-weight: 500; color: var(--muted); }
.index-mini span { font-size: 10.5px; color: #6a675f; }
.index-mini .btn { margin-left: auto; }

.index-progress { display: flex; flex-direction: column; gap: 5px; }
.ip-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 10.5px;
  color: var(--blue-ink);
}
.ip-head span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ip-head em { font-style: normal; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.ip-bar { height: 5px; border-radius: 99px; background: #e8eaf0; overflow: hidden; }
.ip-bar i {
  display: block;
  height: 100%;
  border-radius: 99px;
  background: linear-gradient(90deg, var(--blue), #5aa0ee);
  transition: width 0.4s ease;
}

/* ---------------- 页脚 / loading ---------------- */
.settings-foot {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 11px;
  margin: 0 2px 8px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: var(--glass-blur);
  border: 1px solid rgba(231, 229, 221, 0.6);
  font-size: 11px;
  color: var(--ink-2);
  line-height: 1.7;
}
.settings-foot .ic { flex: none; margin-top: 1px; }

.page-loading {
  position: fixed;
  right: 22px;
  bottom: 18px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--line);
  box-shadow: var(--shadow-sm);
  font-size: 11.5px;
  color: var(--ink-2);
  z-index: 20;
}

/* ---------------- 窄容器（知识助手展开时主区会变窄） ---------------- */
@container (max-width: 860px) {
  .settings-grid { grid-template-columns: minmax(0, 1fr); }
  .cfg-card.span2 { grid-column: auto; }
  .cfg-grid-2 { grid-template-columns: minmax(0, 1fr); }
}
@container (max-width: 560px) {
  .cfg-row { flex-wrap: wrap; }
  .cfg-row .field.narrow { flex: 1 1 100%; }
  .settings-top { align-items: flex-start; }
}
</style>
