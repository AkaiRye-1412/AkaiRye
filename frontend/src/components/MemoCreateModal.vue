<script setup lang="ts">
/**
 * 全局「新建随手记」弹窗（「1」顶栏入口）。
 * 随手笔记：写到电脑任意绝对路径，简单格式（标题 + 正文），独立于售后笔记。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { configApi, memosApi } from '@/api'
import { ApiError } from '@/api/http'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/Icon.vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const router = useRouter()
const ui = useUiStore()

const title = ref('')
const body = ref('')
const folder = ref('')
const saving = ref(false)
const picking = ref(false)

/** 用后端弹系统「选择文件夹」，省得手敲完整路径；不支持时保留手输 */
async function pickFolder() {
  if (picking.value) return
  picking.value = true
  try {
    const r = await configApi.pickFolder()
    if (!r.cancelled && r.path) folder.value = r.path
  } catch (e) {
    const err = e as ApiError
    ui.toast(err.code === 'picker_unavailable' ? 'warn' : 'error', err.message)
  } finally {
    picking.value = false
  }
}

function reset() {
  title.value = ''
  body.value = ''
  folder.value = ''
}

function close() {
  reset()
  emit('close')
}

async function submit() {
  const t = title.value.trim()
  const f = folder.value.trim()
  if (!t) {
    ui.toast('warn', '标题为必填')
    return
  }
  if (!f) {
    ui.toast('warn', '请填写存放目录（完整路径）')
    return
  }
  saving.value = true
  try {
    const row = (await memosApi.create({ title: t, body: body.value, folder: f })) as Record<string, any>
    ui.toast('success', '已创建随手记')
    close()
    router.push({ path: '/memos', query: { path: row.path } })
  } catch (e) {
    const err = e as { message?: string; code?: string }
    if (err.code === 'path_conflict') ui.toast('error', '同名文件已存在')
    else ui.toast('error', `创建失败：${err.message || '未知错误'}`)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="open" class="modal-mask" @click.self="close">
    <div class="modal memo-modal">
      <h3>新建随手记</h3>
      <p class="modal-sub">写到电脑任意位置，不进入售后笔记列表，仅供随手记录。</p>

      <label class="modal-field">
        <span>标题</span>
        <input v-model="title" class="input" placeholder="如：会议要点、灵感、待办" autofocus />
      </label>

      <label class="modal-field" style="margin-top: 11px">
        <span>正文（Markdown）</span>
        <textarea
          v-model="body"
          class="input memo-textarea"
          rows="6"
          placeholder="随便写点什么…"
        ></textarea>
      </label>

      <label class="modal-field" style="margin-top: 11px">
        <span>存放目录（完整路径，可任意磁盘位置）</span>
        <div class="memo-dir-row">
          <input
            v-model="folder"
            class="input mono"
            placeholder="如：D:/我的笔记 或 C:/Users/我/Desktop/随手记"
            spellcheck="false"
            autocomplete="off"
          />
          <button class="btn ghost sm" type="button" :disabled="picking" @click="pickFolder">
            <Icon name="folder" :size="13" />选择…
          </button>
        </div>
      </label>

      <div class="modal-actions">
        <button class="btn ghost" @click="close">取消</button>
        <button class="btn primary" :disabled="saving" @click="submit">
          {{ saving ? '创建中…' : '创建' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(23, 24, 26, 0.32);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 130;
  padding: 20px;
}
.modal {
  background: var(--card);
  border-radius: 14px;
  box-shadow: var(--shadow-pop);
  padding: 20px 22px;
  width: 480px;
  max-height: 88vh;
  overflow: auto;
}
.modal h3 { margin: 0 0 6px; font-size: 15.5px; font-weight: 650; }
.modal-sub { margin: 0 0 14px; font-size: 11.5px; color: var(--muted); line-height: 1.7; }
.modal-field { display: flex; flex-direction: column; gap: 5px; }
.modal-field > span { font-size: 10.5px; color: var(--muted); }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }

.input {
  height: 34px;
  width: 100%;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  font-size: 12.5px;
  color: var(--ink);
  font-family: inherit;
}
.input:focus { outline: none; border-color: var(--blue-line); box-shadow: 0 0 0 3px rgba(42, 120, 214, 0.1); }
.memo-textarea {
  height: auto;
  padding: 9px 10px;
  resize: vertical;
  line-height: 1.6;
}
.input.mono { font-family: var(--mono); font-size: 11.5px; letter-spacing: -0.1px; }
/* 存放目录：输入框 + 系统选择框按钮同行 */
.memo-dir-row { display: flex; gap: 6px; align-items: center; }
.memo-dir-row .input { flex: 1; min-width: 0; }
.memo-dir-row .btn { flex: none; }
</style>
