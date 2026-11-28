/**
 * 草稿箱（右侧 dock 的第二块面板）：一张自动保存的"大纸"。
 *
 * 设计取舍：
 * - 面板开合跨刷新保留（和知识助手一致），但两者互斥由 App.vue 的 watch 保证；
 * - 当前这张纸的路径记在 localStorage（'drafts.last'），空串代表"新开一张空白纸"，
 *   所以收起再展开不会把用户刚新建的空白页换成旧速记；
 * - 写盘 0.8s 防抖 + 收起时强制 flush：输入过程不打断，也不会把最后一段话丢掉。
 */

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { draftsApi } from '@/api'
import type { DraftItem } from '@/api'
import { ApiError } from '@/api/http'

const LAST_KEY = 'drafts.last'
const OPEN_KEY = 'drafts.panel'

export const useDraftsStore = defineStore('drafts', () => {
  const panelOpen = ref(typeof localStorage !== 'undefined' && localStorage.getItem(OPEN_KEY) === 'open')

  const items = ref<DraftItem[]>([])
  const currentPath = ref<string | null>(null)
  const content = ref('')
  const loading = ref(false)
  const saving = ref(false)
  const dirty = ref(false)
  /** 最近一次成功写盘的时间戳（ms）；null = 这张纸还没落过盘 */
  const savedAt = ref<number | null>(null)
  const error = ref('')

  /** 程序赋值 content（切换草稿/新建）时置 true，避免 watch 把加载误判成"用户输入" */
  let applying = false
  /** 本次展开后用户是否已经动过笔：加载慢于输入时，不能拿旧草稿覆盖刚写的字 */
  let touched = false
  let timer: number | null = null
  let booted = false

  if (typeof localStorage !== 'undefined') {
    watch(panelOpen, (v) => localStorage.setItem(OPEN_KEY, v ? 'open' : 'closed'))
  }

  function remember(path: string | null) {
    if (typeof localStorage === 'undefined') return
    if (path === null) localStorage.setItem(LAST_KEY, '')
    else localStorage.setItem(LAST_KEY, path)
  }

  function setContent(text: string) {
    applying = true
    content.value = text
    // watch 是同步触发的，置回 false 放到微任务后即可
    void Promise.resolve().then(() => {
      applying = false
    })
  }

  function cancelTimer() {
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
  }

  function scheduleSave(delay = 800) {
    cancelTimer()
    timer = window.setTimeout(() => {
      timer = null
      void flush()
    }, delay)
  }

  watch(content, () => {
    if (applying) return
    touched = true
    dirty.value = true
    error.value = ''
    scheduleSave()
  })

  async function doFlush() {
    cancelTimer()
    const text = content.value
    // 空白且从未落盘：不建文件，避免草稿箱里堆满空纸
    if (!currentPath.value && !text.trim()) return
    if (!dirty.value) return

    saving.value = true
    error.value = ''
    try {
      // 后端会按纸上的「一级标题 + 日期」给文件命名，保存后返回的 path 才是真相：
      // 标题一改名字就跟着变，手里这条路径必须立刻换成新的，否则下次写盘就是 404。
      const row = !currentPath.value
        ? await draftsApi.create(text)
        : await draftsApi.save(currentPath.value, text)
      if (row.path !== currentPath.value) {
        currentPath.value = row.path
        remember(row.path)
      }
      // 保存期间又改了内容 → 保持 dirty，下次防抖再存一次
      if (content.value === text) {
        dirty.value = false
        savedAt.value = Date.now()
      }
      // 落盘后同步历史列表：新建的那条要立刻出现在「历史」里，
      // 已存在的那条要跟着标题/时间重排（这是列表"实时"的唯一来源）
      void syncList()
    } catch (e) {
      // 文件被外部删掉：忘掉路径，下次按新纸重建（内容还在编辑框里，不丢）
      if (e instanceof ApiError && e.code === 'draft_not_found') {
        currentPath.value = null
        error.value = '草稿文件已不存在，停笔后会重新保存'
      } else {
        error.value = (e as Error).message || '保存失败'
      }
    } finally {
      saving.value = false
    }
  }

  /**
   * 写盘串行化：保存会按一级标题改名，旧路径随之失效，
   * 两次保存叠在一起（防抖 + Ctrl+S）会把同一张纸存成两份。
   */
  let writeChain: Promise<void> = Promise.resolve()
  function flush(): Promise<void> {
    writeChain = writeChain.then(doFlush, doFlush)
    return writeChain
  }

  async function load(path: string | null) {
    if (path) {
      const row = await draftsApi.read(path)
      currentPath.value = row.path
      setContent(row.content ?? '')
      savedAt.value = row.updated_at ? Date.parse(row.updated_at) : null
    } else {
      currentPath.value = null
      setContent('')
      savedAt.value = null
    }
    dirty.value = false
    error.value = ''
    remember(path)
  }

  async function refreshList() {
    items.value = await draftsApi.list()
    return items.value
  }

  /** 静默同步列表：列表只是辅助信息，拉失败不能污染「已保存」状态，也不该打断写字。 */
  async function syncList() {
    try {
      await refreshList()
    } catch {
      /* 忽略：下次保存/展开历史还会再拉 */
    }
  }

  /** 展开面板时调用：拉最新列表，并决定这张纸显示什么（上次那张 / 最新一张 / 空白）。 */
  async function openPanel() {
    panelOpen.value = true
    touched = false
    loading.value = true
    try {
      const list = await refreshList()
      // 列表请求还没回来，用户已经在纸上写字了：保留他写的内容，别用旧草稿盖掉
      if (touched) return
      const last = typeof localStorage !== 'undefined' ? localStorage.getItem(LAST_KEY) : null

      if (last === null) {
        await load(list[0]?.path ?? null)
      } else if (last === '') {
        // 上次是"新开一张空白纸"：保持空白
        currentPath.value = null
        setContent('')
        dirty.value = false
        savedAt.value = null
      } else if (list.some((i) => i.path === last)) {
        await load(last)
      } else {
        await load(list[0]?.path ?? null)
      }
      booted = true
    } catch (e) {
      error.value = (e as Error).message || '草稿箱加载失败'
    } finally {
      loading.value = false
    }
  }

  async function closePanel() {
    await flush()
    panelOpen.value = false
  }

  /** 新建一页：先把当前纸存好，再清空纸面（空白纸不立刻建文件）。 */
  async function newSheet() {
    await flush()
    currentPath.value = null
    setContent('')
    dirty.value = false
    savedAt.value = null
    error.value = ''
    remember(null)
  }

  async function switchTo(path: string) {
    await flush()
    await load(path)
  }

  /**
   * 草稿箱位置被改了（设置页「草稿箱索引位置」保存后）：
   * 先把当前纸存回旧位置，再换一张空白纸 + 重拉列表。
   * 不这么做的话，面板还拿着旧路径，之后每次写盘都会被后端拒掉。
   */
  async function relocate() {
    await flush()
    currentPath.value = null
    setContent('')
    dirty.value = false
    savedAt.value = null
    error.value = ''
    remember(null)
    await syncList()
  }

  async function removeDraft(path: string) {
    await draftsApi.remove(path)
    if (currentPath.value === path) {
      currentPath.value = null
      setContent('')
      dirty.value = false
      savedAt.value = null
      remember(null)
    }
    await refreshList()
  }

  return {
    panelOpen,
    items,
    currentPath,
    content,
    loading,
    saving,
    dirty,
    savedAt,
    error,
    booted: () => booted,
    openPanel,
    closePanel,
    newSheet,
    switchTo,
    relocate,
    removeDraft,
    refreshList,
    syncList,
    flush,
  }
})
