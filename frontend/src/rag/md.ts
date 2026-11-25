/**
 * Markdown 渲染（C1 RAG 回答区，参考 CLI 输出方式）：
 * - marked 解析 + DOMPurify 消毒（回答内容来自 LLM，必须防注入）；
 * - 流式过程中半截 markdown 直接渲染，收尾帧后由 REST 补拉完整内容自然修正。
 */

import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({
  gfm: true, // 表格/删除线等 GFM 扩展
  breaks: true, // 单换行即 <br>，贴近聊天习惯
})

export function renderMarkdown(text: string): string {
  if (!text) return ''
  const html = marked.parse(text, { async: false })
  return DOMPurify.sanitize(html, {
    FORBID_TAGS: ['style', 'script', 'iframe', 'form', 'input'],
    ADD_ATTR: ['target'],
  })
}
