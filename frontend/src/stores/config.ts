/** 系统配置状态（M8）：启动加载、热生效订阅（config.updated topic）。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { configApi, systemApi } from '@/api'
import type { SystemStatus } from '@/types/api'

export const useConfigStore = defineStore('config', () => {
  const config = ref<Record<string, any>>({})
  const status = ref<SystemStatus | null>(null)
  const loaded = ref(false)

  async function load() {
    ;[config.value, status.value] = await Promise.all([configApi.getAll(), systemApi.status()])
    loaded.value = true
  }

  async function save(patch: Record<string, any>) {
    await configApi.save(patch)
    await load()
  }

  return { config, status, loaded, load, save }
})
