/**
 * Markdown 渲染（C1 RAG 回答区 + M2 笔记预览共用）：
 * - marked 解析 + DOMPurify 消毒（回答内容来自 LLM、正文来自 vault，必须防注入）；
 * - 流式过程中半截 markdown 直接渲染，收尾帧后 REST 补拉完整内容自然修正；
 * - 代码块统一包一层 .code-wrap 并附右上角「复制」按钮（点击由面板事件委托处理，
 *   v-html 里拿不到 Vue 事件，故按钮只带 class，不做行内 onclick）；
 * - 数学公式（$$…$$ / \[…\] / $…$ / \(…\)）走 KaTeX：先抽成占位符，
 *   消毒完成后再把「本地生成的」数学 HTML 填回去 —— KaTeX 的内联样式与 MathML
 *   会被 DOMPurify 削掉，所以不能直接把公式丢给消毒器。
 */

import { marked } from 'marked'
import DOMPurify from 'dompurify'
import katex from 'katex'
import 'katex/dist/katex.min.css'

marked.setOptions({
  gfm: true, // 表格/删除线等 GFM 扩展
  breaks: true, // 单换行即 <br>，贴近聊天习惯
})

const COPY_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>'

let sandbox: HTMLDivElement | null = null

/** 给每个 <pre> 包一层定位容器 + 右上角复制按钮。 */
function decorateCodeBlocks(html: string): string {
  if (!html.includes('<pre')) return html
  if (!sandbox) sandbox = document.createElement('div')
  sandbox.innerHTML = html

  sandbox.querySelectorAll('pre').forEach((pre) => {
    const parent = pre.parentElement
    // 幂等：已经是包装结构就跳过（消毒后重入时不会重复套壳）
    if (parent?.classList.contains('code-wrap')) return

    const wrap = document.createElement('div')
    wrap.className = 'code-wrap'
    pre.replaceWith(wrap)

    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = 'code-copy'
    btn.title = '复制代码'
    btn.setAttribute('aria-label', '复制代码')
    btn.innerHTML = `${COPY_ICON}<span>复制</span>`

    wrap.appendChild(btn)
    wrap.appendChild(pre)
  })

  const out = sandbox.innerHTML
  sandbox.innerHTML = '' // 释放引用，不让旧节点挂在模块变量上
  return out
}

/* ---------------- 数学公式（KaTeX）---------------- */

/** 占位符：纯大写字母 + 数字，marked / DOMPurify 都不会改写它。 */
const MATH_TOKEN = (i: number) => `KATEXSTASH${i}END`
const MATH_TOKEN_RE = /KATEXSTASH(\d+)END/g

/** 代码块与行内代码：里面的 $ 是字面量，不参与公式提取。 */
const CODE_SEGMENT_RE = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g

/**
 * $$…$$ / \[…\] → 行间公式；\(…\) / $…$ → 行内公式。
 * $ 要求首尾都非空白，避免把「价格 $5 到手 $10」这类文本吃成公式。
 */
const MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(\S(?:[^$\n]*?\S)?)\$/g

/** 把一段（非代码）文本里的公式替换成占位符，成功渲染的 HTML 存进 store。 */
function stashMathIn(text: string, store: string[]): string {
  return text.replace(MATH_RE, (m, b1, b2, i1, i2) => {
    const tex = String(b1 ?? b2 ?? i1 ?? i2 ?? '').trim()
    if (!tex) return m
    const displayMode = b1 !== undefined || b2 !== undefined
    try {
      const html = katex.renderToString(tex, {
        displayMode,
        throwOnError: true, // 语法错了就保留原文，而不是渲染成红色报错
        strict: 'ignore', // 允许中文 \text、兼容写法，不刷控制台警告
        trust: false, // 禁 \href 等可执行/外链指令
      })
      store.push(html)
      return MATH_TOKEN(store.length - 1)
    } catch {
      return m
    }
  })
}

function extractMath(text: string): { text: string; math: string[] } {
  const math: string[] = []
  let out = ''
  let last = 0
  for (const seg of text.matchAll(CODE_SEGMENT_RE)) {
    const start = seg.index ?? 0
    out += stashMathIn(text.slice(last, start), math) + seg[0]
    last = start + seg[0].length
  }
  out += stashMathIn(text.slice(last), math)
  return { text: out, math }
}

function restoreMath(html: string, math: string[]): string {
  if (!math.length) return html
  return html.replace(MATH_TOKEN_RE, (m, i) => math[Number(i)] ?? m)
}

export function renderMarkdown(text: string): string {
  if (!text) return ''
  const { text: stashed, math } = extractMath(text)
  const html = marked.parse(stashed, { async: false })
  const safe = DOMPurify.sanitize(html, {
    FORBID_TAGS: ['style', 'script', 'iframe', 'form', 'input'],
    ADD_ATTR: ['target'],
  })
  // 消毒之后再拼按钮与公式：这两块结构都由本地代码生成，不经过第三方内容
  return decorateCodeBlocks(restoreMath(safe, math))
}
