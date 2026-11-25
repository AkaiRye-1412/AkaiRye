import { createRouter, createWebHistory } from 'vue-router'

/**
 * 页面路由：与 M1~M9 模块一一对应（02 文档 5 章）。
 * 三期页面（terminal/repos/cleanup）路由保留，组件为占位。
 */
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/home/HomeView.vue'), meta: { module: 'M1' } },
    { path: '/notes', name: 'notes', component: () => import('@/views/notes/NotesView.vue'), meta: { module: 'M2' } },
    { path: '/kb', name: 'kb', component: () => import('@/views/kb/KnowledgeBaseView.vue'), meta: { module: 'M3' } },
    { path: '/pdf', name: 'pdf', component: () => import('@/views/pdf/PdfManualView.vue'), meta: { module: 'M9' } },
    { path: '/terminal', name: 'terminal', component: () => import('@/views/terminal/TerminalView.vue'), meta: { module: 'M5', phase: 3 } },
    { path: '/repos', name: 'repos', component: () => import('@/views/repos/ReposView.vue'), meta: { module: 'M6', phase: 3 } },
    { path: '/cleanup', name: 'cleanup', component: () => import('@/views/cleanup/CleanupView.vue'), meta: { module: 'M7', phase: 3 } },
    { path: '/settings', name: 'settings', component: () => import('@/views/settings/SettingsView.vue'), meta: { module: 'M8' } },
  ],
})

export default router
