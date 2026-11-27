<script setup lang="ts">
/**
 * M6 代码仓库（FR-GIT-01~06 · 三期）：
 *
 * 只读展示本地 git 仓库状态：自动发现、置顶/隐藏、分支/未提交/领先落后/最近提交。
 * - 不提供 commit / push / pull / 切分支（FR-GIT-05）：写操作走「在此目录打开终端」；
 * - 状态由后端后台线程定期刷新（FR-GIT-03，每 30 分钟），本页也提供手动刷新；
 * - 仓库根目录在「设置」页配置（1~3 个，`paths.code_roots`）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { eventsSocket, reposApi, type GitRepo } from '@/api'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'

const ui = useUiStore()
const router = useRouter()

const repos = ref<GitRepo[]>([])
const hiddenRepos = ref<GitRepo[]>([])
const loading = ref(true)
const refreshing = ref(false)
const showHidden = ref(true)
const loadError = ref('')
const roots = ref<string[]>([])

/* ---------------- 派生 ---------------- */

const visible = computed(() => repos.value)
const hiddenCount = computed(() => hiddenRepos.value.length)

const dirtyTotal = computed(() => repos.value.reduce((s, r) => s + (r.dirty ?? 0), 0))
const pinCount = computed(() => repos.value.filter((r) => r.display === 'pin').length)

/* ---------------- 数据加载 ---------------- */

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [list, hidden] = await Promise.all([
      reposApi.list(false),
      reposApi.list(true).then((all) => all.filter((r) => r.display === 'hidden')),
    ])
    repos.value = list
    hiddenRepos.value = hidden
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const r = await reposApi.refresh()
    roots.value = r.roots
    await load()
    ui.toast('success', `已刷新，发现 ${r.total} 个仓库`)
  } catch (e) {
    ui.toast('error', `刷新失败：${(e as Error).message}`)
  } finally {
    refreshing.value = false
  }
}

/* ---------------- 操作 ---------------- */

async function setDisplay(repo: GitRepo, display: GitRepo['display']) {
  try {
    await reposApi.setDisplay(repo.path, display)
    await load()
  } catch (e) {
    ui.toast('error', `操作失败：${(e as Error).message}`)
  }
}

function openTerminal(repo: GitRepo) {
  // FR-TERM-06 / FR-GIT：在仓库目录打开终端（终端页读 ?cwd= 新建标签）
  router.push({ path: '/terminal', query: { cwd: repo.path } })
}

function openSettings() {
  router.push('/settings')
}

/* ---------------- 展示辅助 ---------------- */

function shortPath(p: string): string {
  return p.replace(/[\\/]+$/, '')
}

function fmtRemoteUrl(url?: string | null): string {
  if (!url) return ''
  // 去掉 git 用户名前缀（如 https://user@github.com/... 或 git@github.com:owner/repo.git）
  let u = url.trim()
  u = u.replace(/^git@([^:]+):/, 'https://$1/')
  u = u.replace(/^https?:\/\/[^@/]+@/, (m) => m.replace(/[^@/]+@/, ''))
  // 去掉末尾 .git
  u = u.replace(/\.git$/, '')
  return u
}

function commitCountText(r: GitRepo): string {
  if (r.commit_count == null) return '—'
  return `${r.commit_count} 次`
}

function fmtCommitTime(s?: string | null): string {
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  const now = Date.now()
  const diff = now - d.getTime()
  const m = 60 * 1000
  const h = 60 * m
  const day = 24 * h
  if (diff < m) return '刚刚'
  if (diff < h) return `${Math.floor(diff / m)} 分钟前`
  if (diff < day) return `${Math.floor(diff / h)} 小时前`
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function fmtRefreshTime(s?: string | null): string {
  if (!s) return '尚未刷新'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

function repoStateText(r: GitRepo): string {
  if (!r.exists) return '目录缺失'
  if (r.error) return '读取失败'
  if (r.dirty) return `${r.dirty} 处未提交`
  return '干净'
}

function repoStateKind(r: GitRepo): 'ok' | 'warn' | 'err' | 'missing' {
  if (!r.exists) return 'missing'
  if (r.error) return 'err'
  if (r.dirty) return 'warn'
  return 'ok'
}

onMounted(() => {
  void load()
  // 代码有改动 → 后端 watchdog 自动刷新并推送 repo.changed → 前端自动重拉，无需手动点「刷新」
  offRepoChanged = eventsSocket.on('repo.changed', () => {
    void load()
  })
})

let offRepoChanged: (() => void) | null = null
onBeforeUnmount(() => {
  offRepoChanged?.()
})
</script>

<template>
  <div class="page page-repos">
    <!-- ==================== 页头 ==================== -->
    <section class="card rp-hero">
      <header class="rp-hero-head">
        <div class="rp-hero-title">
          <span class="rp-hero-ic"><Icon name="git" :size="17" /></span>
          <div class="rp-hero-text">
            <h1>代码仓库</h1>
            <p>只读展示本地 Git 仓库状态 · 写操作走终端</p>
          </div>
        </div>
        <div class="rp-hero-meta">
          <span class="rp-meta"><b>{{ repos.length }}</b> 个仓库</span>
          <span class="sep-dot" />
          <span class="rp-meta" :class="{ warn: dirtyTotal > 0 }">
            <b>{{ dirtyTotal }}</b> 处未提交
          </span>
          <button class="btn ghost sm" :disabled="refreshing" @click="refresh">
            <Icon name="refresh" :size="13" :class="{ spin: refreshing }" />
            <span>{{ refreshing ? '刷新中…' : '刷新' }}</span>
          </button>
        </div>
      </header>
    </section>

    <!-- 配置未就绪 -->
    <section v-if="loadError" class="card rp-empty">
      <div class="empty-block">
        <span class="empty-ic"><Icon name="alert" :size="24" /></span>
        <p>代码仓库暂时不可用</p>
        <span class="empty-sub">{{ loadError }}</span>
        <div class="rp-empty-actions">
          <button class="btn ghost sm" @click="load">重试</button>
          <button class="btn ghost sm" @click="openSettings">去设置代码根目录</button>
        </div>
      </div>
    </section>

    <!-- 加载中 -->
    <section v-else-if="loading" class="card rp-empty">
      <div class="empty-block">
        <span class="empty-ic"><Icon name="refresh" :size="22" class="spin" /></span>
        <p>加载中…</p>
      </div>
    </section>

    <!-- 无仓库（真一个都没发现；若还有隐藏仓库则仍进列表区，保证能恢复） -->
    <section v-else-if="!repos.length && !hiddenCount" class="card rp-empty">
      <div class="empty-block">
        <span class="empty-ic"><Icon name="git" :size="24" /></span>
        <p>还没有发现代码仓库</p>
        <span class="empty-sub">
          在「设置 → 代码仓库根目录」里添加 1~3 个目录，这里会自动发现其中的 Git 仓库
        </span>
        <div class="rp-empty-actions">
          <button class="btn ghost sm" :disabled="refreshing" @click="refresh">
            <Icon name="refresh" :size="13" />重新扫描
          </button>
          <button class="btn primary sm" @click="openSettings">
            <Icon name="settings" :size="13" />去设置根目录
          </button>
        </div>
      </div>
    </section>

    <!-- ==================== 仓库列表 ==================== -->
    <template v-else>
      <div class="rp-list">
        <article
          v-for="r in visible"
          :key="r.path"
          class="card rp-card"
          :class="[`st-${repoStateKind(r)}`, { pinned: r.display === 'pin', missing: !r.exists }]"
        >
          <header class="rp-card-head">
            <div class="rp-card-title">
              <span v-if="r.display === 'pin'" class="rp-pin-ic" title="已置顶">
                <Icon name="zap" :size="11" />
              </span>
              <Icon name="folder" :size="15" class="rp-folder-ic" />
              <span class="rp-name">{{ r.name }}</span>
              <span class="rp-branch">
                <Icon name="git" :size="11" />
                <code>{{ r.branch || '—' }}</code>
              </span>
            </div>

            <div class="rp-head-right">
              <div class="rp-state" :class="repoStateKind(r)">
                <span class="rp-state-dot" />
                <span>{{ repoStateText(r) }}</span>
              </div>
              <div class="rp-actions">
                <button class="btn ghost sm" title="在此目录打开终端" @click="openTerminal(r)">
                  <Icon name="terminal" :size="13" />
                  <span>打开终端</span>
                </button>
                <button
                  v-if="r.display !== 'pin'"
                  class="btn ghost sm"
                  title="置顶常用仓库"
                  @click="setDisplay(r, 'pin')"
                >
                  <Icon name="zap" :size="13" /><span>置顶</span>
                </button>
                <button
                  v-else
                  class="btn ghost sm"
                  title="取消置顶"
                  @click="setDisplay(r, 'normal')"
                >
                  <Icon name="minusCircle" :size="13" /><span>取消置顶</span>
                </button>
                <button
                  class="rp-hide"
                  title="隐藏该仓库（不影响磁盘文件）"
                  @click="setDisplay(r, 'hidden')"
                >
                  <Icon name="eyeOff" :size="13" />
                </button>
              </div>
            </div>
          </header>

          <div class="rp-path" :title="r.path">
            <code>{{ shortPath(r.path) }}</code>
          </div>

          <!-- 远端仓库地址（无远端则显示本地仓库提示） -->
          <div v-if="r.remote_url" class="rp-remote">
            <Icon name="external" :size="12" />
            <a
              class="rp-remote-link"
              :href="fmtRemoteUrl(r.remote_url)"
              :title="r.remote_url"
              target="_blank"
              rel="noopener noreferrer"
              @click.stop
            >{{ fmtRemoteUrl(r.remote_url) }}</a>
          </div>

          <!-- 状态摘要（FR-GIT-04） -->
          <div class="rp-metrics">
            <div class="rp-metric" :class="{ warn: (r.dirty ?? 0) > 0 }">
              <span class="rp-metric-label">未提交</span>
              <b>{{ r.dirty ?? '—' }}</b>
            </div>
            <div class="rp-metric" :class="{ ahead: (r.ahead ?? 0) > 0 }">
              <span class="rp-metric-label">领先</span>
              <b>{{ r.ahead ?? 0 }}</b>
            </div>
            <div class="rp-metric" :class="{ behind: (r.behind ?? 0) > 0 }">
              <span class="rp-metric-label">落后</span>
              <b>{{ r.behind ?? 0 }}</b>
            </div>
            <div class="rp-metric">
              <span class="rp-metric-label">提交次数</span>
              <b>{{ r.commit_count ?? '—' }}</b>
            </div>
            <div class="rp-metric rp-last">
              <span class="rp-metric-label">最近提交</span>
              <span class="rp-commit">
                <code v-if="r.last_commit">{{ r.last_commit }}</code>
                <span v-else class="rp-none">无</span>
                <em v-if="r.last_commit_at">{{ fmtCommitTime(r.last_commit_at) }}</em>
              </span>
            </div>
          </div>

          <footer class="rp-card-foot">
            <span class="rp-refresh" :title="r.refreshed_at || ''">
              刷新于 {{ fmtRefreshTime(r.refreshed_at) }}
            </span>
          </footer>
        </article>
      </div>

      <!-- 全部隐藏时的引导提示 -->
      <p v-if="!repos.length && hiddenCount" class="rp-all-hidden-tip">
        <Icon name="eyeOff" :size="13" />
        所有仓库都已隐藏，下面是隐藏的仓库，可点「恢复显示」重新放回列表。
      </p>

      <!-- 隐藏的仓库 -->
      <section v-if="hiddenCount" class="card rp-hidden">
        <button class="rp-hidden-toggle" @click="showHidden = !showHidden">
          <Icon :name="showHidden ? 'chevronDown' : 'chevronRight'" :size="13" />
          <span>已隐藏的仓库（{{ hiddenCount }}）</span>
        </button>

        <div v-if="showHidden" class="rp-hidden-rows">
          <div v-for="r in hiddenRepos" :key="r.path" class="rp-hidden-row">
            <Icon name="folder" :size="13" class="rp-folder-ic" />
            <span class="rp-name">{{ r.name }}</span>
            <code class="rp-hidden-path" :title="r.path">{{ r.path }}</code>
            <button
              class="rp-hide rp-restore"
              title="恢复显示"
              @click="setDisplay(r, 'normal')"
            >
              <Icon name="eye" :size="13" />
            </button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page-repos {
  display: flex;
  flex-direction: column;
  gap: 11px;
  container-type: inline-size;
  container-name: repospage;
}

/* ---------------- 页头 ---------------- */
.rp-hero { padding: 15px 18px 13px; }
.rp-hero-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px 18px;
  flex-wrap: wrap;
}
.rp-hero-title { display: flex; align-items: center; gap: 9px; min-width: 0; }
.rp-hero-ic {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--violet-soft);
  color: var(--violet);
  display: grid;
  place-items: center;
  flex: none;
}
.rp-hero-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.rp-hero-text h1 { margin: 0; font-size: 15px; font-weight: 650; letter-spacing: -0.1px; }
.rp-hero-text p { margin: 0; font-size: 10.5px; color: #6a675f; line-height: 1.45; }
.rp-hero-meta { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.rp-meta { font-size: 10.5px; color: #6a675f; white-space: nowrap; }
.rp-meta b {
  font-size: 13px;
  font-weight: 650;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  margin-right: 2px;
}
.rp-meta.warn b { color: var(--amber-ink); }

/* ---------------- 空状态 ---------------- */
.rp-empty { padding: 8px; }
.rp-empty-actions { display: flex; gap: 6px; margin-top: 8px; }

/* ---------------- 列表 ---------------- */
.rp-list { display: flex; flex-direction: column; gap: 10px; }

.rp-card { padding: 14px 16px 10px; transition: border-color 0.14s, box-shadow 0.14s; }
.rp-card.pinned { border-color: rgba(98, 80, 214, 0.34); }
.rp-card.missing { opacity: 0.62; }

.rp-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px 14px;
  flex-wrap: wrap;
}
.rp-card-title { display: flex; align-items: center; gap: 7px; min-width: 0; }
/* 头部右侧：状态文字 + 操作按钮组（打开终端/置顶/隐藏） */
.rp-head-right {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  flex-wrap: wrap;
}
.rp-pin-ic {
  width: 18px;
  height: 18px;
  border-radius: 5px;
  background: var(--violet-soft);
  color: var(--violet);
  display: grid;
  place-items: center;
  flex: none;
}
.rp-folder-ic { color: #a6a29a; flex: none; }
.rp-name {
  font-size: 14px;
  font-weight: 650;
  letter-spacing: -0.1px;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rp-branch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--ink-2);
  background: #f2f1ec;
  border-radius: 6px;
  padding: 2px 7px;
  white-space: nowrap;
  flex: none;
}
.rp-branch svg { color: var(--muted); }
.rp-branch code { font-family: var(--mono); font-size: 11px; }

.rp-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}
.rp-state-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.rp-state.ok { color: var(--green-ink); }
.rp-state.ok .rp-state-dot { background: var(--green); }
.rp-state.warn { color: var(--amber-ink); }
.rp-state.warn .rp-state-dot { background: var(--amber); }
.rp-state.err { color: var(--red); }
.rp-state.err .rp-state-dot { background: var(--red); }
.rp-state.missing { color: var(--muted); }
.rp-state.missing .rp-state-dot { background: var(--muted); }

.rp-path { margin-top: 7px; }
.rp-path code {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--muted);
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 远端仓库地址 */
.rp-remote {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 5px;
  min-width: 0;
}
.rp-remote svg { color: #a6a29a; flex: none; }
.rp-remote-link {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--blue-ink);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rp-remote-link:hover { text-decoration: underline; }

/* 状态摘要 */
.rp-metrics {
  display: flex;
  gap: 8px;
  margin-top: 11px;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
}
.rp-metric {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 9px;
  border-radius: 8px;
  background: rgba(247, 246, 242, 0.5);
}
.rp-metric-label { font-size: 9.5px; color: var(--muted); }
.rp-metric b {
  font-size: 15px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  color: var(--ink-2);
}
.rp-metric.warn b { color: var(--amber-ink); }
.rp-metric.ahead b { color: var(--blue-ink); }
.rp-metric.behind b { color: var(--red); }
.rp-metric.rp-last { flex: 2.2; }
.rp-commit {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.rp-commit code { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.rp-commit em { font-style: normal; font-size: 10px; color: var(--muted); white-space: nowrap; }
.rp-none { font-size: 11px; color: var(--muted); }

/* 卡片脚 */
.rp-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(239, 238, 232, 0.7);
  flex-wrap: wrap;
}
.rp-refresh { font-size: 10px; color: var(--muted); }
.rp-actions { display: flex; align-items: center; gap: 5px; }
/* 仓库卡片操作按钮：把全局 .btn.ghost 的纯白底改成半透明玻璃，与毛玻璃卡片协调、不刺眼 */
.rp-actions .btn.ghost,
.rp-hero-meta .btn.ghost {
  background: rgba(255, 255, 255, 0.42);
  border-color: rgba(220, 217, 207, 0.7);
  color: var(--ink-2);
}
.rp-actions .btn.ghost:hover,
.rp-hero-meta .btn.ghost:hover {
  background: rgba(255, 255, 255, 0.6);
  border-color: #dcd9cf;
  color: var(--ink);
}
.rp-hide {
  width: 29px;
  height: 29px;
  border: 1px solid rgba(220, 217, 207, 0.7);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.42);
  color: var(--ink-2);
  display: inline-grid;
  place-items: center;
  cursor: pointer;
  transition: color 0.12s, background 0.12s, border-color 0.12s;
}
.rp-hide:hover { color: var(--ink); background: rgba(255, 255, 255, 0.6); border-color: #dcd9cf; }

/* ---------------- 隐藏仓库 ---------------- */
.rp-hidden { overflow: hidden; }
.rp-all-hidden-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 9px 13px;
  border-radius: 8px;
  background: rgba(250, 240, 220, 0.4);
  border: 1px solid var(--amber-line);
  font-size: 11.5px;
  color: var(--amber-ink);
  line-height: 1.5;
}
.rp-all-hidden-tip svg { flex: none; }
.rp-hidden-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 11px 16px;
  border: 0;
  background: transparent;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
}
.rp-hidden-toggle:hover { color: var(--ink-2); background: rgba(247, 246, 242, 0.4); }
.rp-hidden-rows { border-top: 1px solid var(--line-soft); }
.rp-hidden-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(239, 238, 232, 0.7);
}
.rp-hidden-row:last-child { border-bottom: 0; }
.rp-hidden-path {
  flex: 1;
  min-width: 0;
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rp-hidden-row .rp-name { font-size: 12.5px; font-weight: 600; }

/* 隐藏列表里的「恢复显示」眼睛图标：与卡片上的隐藏按钮同款，仅颜色语义不同（恢复=绿色系） */
.rp-restore {
  flex: none;
  margin-left: auto;
  color: var(--green-ink);
  border: 1px solid rgba(98, 176, 118, 0.28);
  background: rgba(235, 247, 238, 0.55);
}
.rp-restore:hover {
  color: #fff;
  background: var(--green);
  border-color: var(--green);
}

.spin { animation: rp-spin 1s linear infinite; }
@keyframes rp-spin {
  to { transform: rotate(360deg); }
}

/* ---------------- 窄容器 ---------------- */
@container repospage (max-width: 560px) {
  .rp-metrics { flex-wrap: wrap; }
  .rp-metric { flex: 1 1 40%; }
  .rp-metric.rp-last { flex: 1 1 100%; }
}
</style>
