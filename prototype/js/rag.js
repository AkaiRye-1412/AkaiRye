(function () {
  const rag = {
    context: null, tab: 'answer', mode: 'auto', brands: ['高创', 'ACS'],
    draft: '', pending: null, sessionsOpen: false, scopeOpen: false,
    compare: { brands: ['高创', 'ACS'], generated: false, loading: false },
    fullscreen: false, collapsed: false, width: 400, focusComposer: false
  };

  const panel = () => document.getElementById('ragPanel');
  const inner = () => document.getElementById('ragInner');
  const S = () => U.Store.state;
  const session = () => {
    const st = S();
    const s = st.sessions.find((x) => x.id === st.activeSessionId);
    return s || st.sessions[0];
  };

  function ensureSession() {
    const st = S();
    if (!st.sessions || !st.sessions.length) {
      st.sessions = [{ id: 's1', name: '新会话', updated: '刚刚', messages: [] }];
    }
    if (!st.sessions.some((x) => x.id === st.activeSessionId)) st.activeSessionId = st.sessions[0].id;
    return session();
  }

  function noteCount() { try { return (window.RagEngine && RagEngine.vaultSelected) ? RagEngine.vaultSelected().length : 0; } catch (e) { return 0; } }
  function scopeMeta() {
    const ctx = rag.context;
    const nc = noteCount();
    if (rag.mode === 'auto') {
      return ctx
        ? { kind: 'project', label: '当前项目 · ' + ctx.title, short: '当前项目', hint: '自动感知 · 已跟随当前打开的笔记' }
        : { kind: 'notes', label: '自动 · 全部笔记', short: '自动 · 笔记', hint: `未打开笔记，默认检索全部笔记（已纳入 ${nc} 篇）` };
    }
    if (rag.mode === 'project') {
      return ctx
        ? { kind: 'project', label: '当前项目 · ' + ctx.title, short: '当前项目', hint: '仅检索当前客户 / 项目的笔记' }
        : { kind: 'notes', label: '当前项目（未打开笔记）', short: '当前项目', hint: '先打开一篇笔记，再使用当前项目范围' };
    }
    if (rag.mode === 'notes') return { kind: 'notes', label: '全部笔记', short: '全部笔记', hint: `仅检索已纳入知识库的笔记（当前 ${nc} 篇 · 「所有笔记」勾选范围）` };
    if (rag.mode === 'manuals') return { kind: 'manuals', label: '全部手册', short: '全部手册', hint: '仅检索已完成入库的 PDF 手册' };
    if (rag.mode === 'brands') {
      const b = rag.brands.length ? rag.brands.join(' / ') : '未选择品牌';
      return { kind: 'brands', label: '品牌 · ' + b, short: b, hint: '按品牌筛选手册（可多选）', brands: [...rag.brands] };
    }
    return { kind: 'all', label: '全部知识库', short: '全库', hint: '笔记 + 手册混合检索' };
  }

  function applyLayout() {
    const p = panel();
    if (!p) return;
    p.classList.toggle('collapsed', rag.collapsed);
    p.classList.toggle('fullscreen', rag.fullscreen);
    if (!rag.collapsed) p.style.width = rag.width + 'px';
    const fullBtnIcon = inner().querySelector('#ragFull');
    if (fullBtnIcon) fullBtnIcon.innerHTML = icon(rag.fullscreen ? 'minimize' : 'expand', 16);
  }

  function scopeOptionsHtml(meta) {
    const ctx = rag.context;
    const option = (key, title, desc, disabled) => `
      <button class="scope-opt ${rag.mode === key ? 'active' : ''}" data-scope="${key}" ${disabled ? 'disabled' : ''}>
        <span class="scope-radio">${rag.mode === key ? icon('check', 12) : ''}</span>
        <span class="scope-opt-main"><strong>${title}</strong><em>${desc}</em></span>
      </button>`;
    return `<div class="scope-pop" id="scopePop">
      <div class="scope-pop-head"><strong>检索范围</strong><span>决定回答检索哪部分知识库</span></div>
      ${option('auto', '自动感知', ctx ? '跟随当前打开的笔记（推荐）' : '未打开笔记时检索全部笔记')}
      ${option('project', '当前项目 / 客户', ctx ? U.esc(ctx.customer + ' · ' + ctx.title) : '请先打开一篇笔记', !ctx)}
      ${option('notes', '全部笔记', '仅售后笔记，不含手册')}
      ${option('manuals', '全部手册', '仅已入库 PDF，不含笔记')}
      <button class="scope-opt ${rag.mode === 'brands' ? 'active' : ''}" data-scope="brands">
        <span class="scope-radio">${rag.mode === 'brands' ? icon('check', 12) : ''}</span>
        <span class="scope-opt-main"><strong>按品牌筛选</strong><em>可多选，仅手册</em></span>
      </button>
      <div class="scope-brand-row">${S().config.brands.map((b) => `
        <button class="scope-brand ${rag.brands.includes(b) ? 'on' : ''}" data-sbrand="${U.esc(b)}">${U.esc(b)}</button>`).join('')}</div>
      ${option('all', '全部知识库', '笔记 + 手册（范围最大）')}
      <div class="scope-pop-foot"><span>${icon('info', 12)} 只有完成入库的手册参与检索</span>
        <button class="btn primary sm" id="scopeDone">完成</button></div>
    </div>`;
  }

  function render() {
    const p = panel();
    if (!p) return;
    ensureSession();
    const ses = session();
    const meta = scopeMeta();
    const st = S();
    const lastBot = [...(ses.messages || [])].reverse().find((m) => m.role === 'assistant');
    const fragCount = lastBot ? (lastBot.fragments || []).length : 0;
    const pending = rag.pending;

    inner().innerHTML = `
      <div class="rag-head">
        <div class="rag-head-title">${icon('sparkle', 15)}<span>知识助手</span><em>可溯源</em></div>
        <div class="rag-head-actions">
          <button class="icon-btn" id="ragSessions" title="会话管理">${icon('history', 16)}</button>
          <button class="icon-btn" id="ragFull" title="${rag.fullscreen ? '退出全屏' : '全屏'}">${icon(rag.fullscreen ? 'minimize' : 'expand', 16)}</button>
          <button class="icon-btn" id="ragCollapseBtn" title="收起面板">${icon('panel', 16)}</button>
        </div>
      </div>
      <div class="rag-scope-row">
        <button class="scope-btn ${rag.scopeOpen ? 'open' : ''}" id="scopeBtn">
          ${icon('database', 13)}<span class="scope-label">${U.esc(meta.label)}</span>${icon('chevronDown', 12)}
        </button>
        <span class="scope-hint">${U.esc(meta.hint)}</span>
      </div>
      ${rag.scopeOpen ? '<div class="scope-shade" data-scope-close></div>' + scopeOptionsHtml(meta) : ''}
      <div class="rag-tabs" role="tablist">
        <button class="rag-tab ${rag.tab === 'answer' ? 'active' : ''}" data-rtab="answer">回答</button>
        <button class="rag-tab ${rag.tab === 'fragments' ? 'active' : ''}" data-rtab="fragments">命中片段 ${fragCount ? `<em>${fragCount}</em>` : ''}</button>
        <button class="rag-tab ${rag.tab === 'compare' ? 'active' : ''}" data-rtab="compare">对比</button>
      </div>
      <div class="rag-body" id="ragBody">
        ${rag.sessionsOpen ? sessionsBodyHtml() : rag.tab === 'fragments' ? fragmentsBodyHtml() : rag.tab === 'compare' ? compareBodyHtml() : answerBodyHtml()}
      </div>
      <div class="rag-composer">
        <div class="composer-box ${pending ? 'pending' : ''}">
          <textarea id="ragInput" rows="1" placeholder="${rag.tab === 'compare' ? '直接提问，或使用上方对比模式…' : '基于笔记与手册提问…'}" ${pending ? 'disabled' : ''}>${U.esc(rag.draft)}</textarea>
          <div class="composer-foot">
            <span class="composer-scope" title="${U.esc(meta.label)}">${icon('database', 11)} ${U.esc(meta.short)}</span>
            <span class="composer-hint">${pending ? '正在检索并生成…' : 'Enter 发送 · Shift+Enter 换行'}</span>
            <button class="rag-send" id="ragSend" ${pending ? 'disabled' : ''} title="发送">${icon(pending ? 'refresh' : 'send', 15, pending ? 'spin' : '')}</button>
          </div>
        </div>
        <div class="rag-disclaimer">${icon('shield', 11)} 回答仅基于检索内容，无依据时明确说明「未找到」</div>
      </div>`;
    applyLayout();
    bind();
    if (rag.focusComposer) {
      rag.focusComposer = false;
      setTimeout(() => inner().querySelector('#ragInput')?.focus(), 60);
    }
  }

  function openPanel(focus) {
    rag.collapsed = false;
    S().ui.ragCollapsed = false;
    U.Store.save();
    rag.focusComposer = !!focus;
    applyLayout();
    render();
  }

  function collapsePanel() {
    rag.collapsed = true;
    rag.scopeOpen = false;
    S().ui.ragCollapsed = true;
    U.Store.save();
    applyLayout();
  }

  function init() {
    const st = S();
    rag.width = st.ui.ragWidth || 400;
    rag.collapsed = st.ui.ragCollapsed == null ? (window.innerWidth < 1320) : st.ui.ragCollapsed;
    rag.tab = st.ui.ragTab || 'answer';
    applyLayout();
    render();

    document.getElementById('ragRail').addEventListener('click', () => openPanel(true));
    const resizer = document.getElementById('ragResizer');
    resizer.addEventListener('mousedown', (e) => {
      if (rag.collapsed || rag.fullscreen) return;
      e.preventDefault();
      const startX = e.clientX;
      const startW = rag.width;
      document.body.classList.add('resizing-rag');
      const move = (ev) => {
        const w = Math.max(320, Math.min(720, startW + (startX - ev.clientX)));
        rag.width = w;
        panel().style.width = w + 'px';
      };
      const up = () => {
        document.body.classList.remove('resizing-rag');
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        S().ui.ragWidth = rag.width;
        U.Store.save();
        if (currentTabIsChart()) render();
      };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  }
  function currentTabIsChart() { return false; }

  function recentQueries() {
    const st = S();
    const all = [];
    (st.sessions || []).forEach((s) => {
      (s.messages || []).forEach((m) => {
        if (m.role === 'user' && m.text) all.push({ text: m.text, time: m.time || '', session: s.name });
      });
    });
    return all.slice(-3).reverse();
  }

  window.Rag = {
    init, render, open: openPanel, collapse: collapsePanel,
    ask, recentQueries, setContext(note) {
      rag.context = note;
      render();
    },
    scopeMeta,
    onManualRemoved(id) {
      Reader.removeManual(id);
      if (rag.mode === 'brands') render();
    },
    state: rag
  };

  const nowTime = () => new Date().toTimeString().slice(0, 5);

  function sessionsBodyHtml() {
    const st = S();
    const active = ensureSession();
    return `<div class="sessions-panel">
      <div class="sess-head">
        <div><h3>会话</h3><span>仅保存在本机 · 永久保留至手动删除</span></div>
        <button class="btn primary sm" data-sess-new>${icon('plus', 13)} 新建</button>
      </div>
      <div class="sess-list">
        ${st.sessions.map((s) => `<div class="sess-item ${s.id === active.id ? 'active' : ''}" data-sess="${s.id}">
          <button class="sess-main">
            <span class="sess-ic">${icon(s.id === active.id ? 'message' : 'history', 14)}</span>
            <span class="sess-text"><strong>${U.esc(s.name)}</strong><em>${U.esc(s.updated || '—')} · ${s.messages.length} 条</em></span>
          </button>
          <button class="icon-btn sess-act" data-sess-rename="${s.id}" title="重命名">${icon('edit', 13)}</button>
          <button class="icon-btn sess-act danger" data-sess-del="${s.id}" title="删除">${icon('trash', 13)}</button>
        </div>`).join('')}
      </div>
      <div class="sess-foot">
        <button class="link-btn" data-sess-back>${icon('left', 12)} 返回对话</button>
        <button class="link-btn danger" data-sess-clear>清空全部会话</button>
      </div>
    </div>`;
  }

  function typingHtml() {
    return `<div class="msg bot typing">
      <div class="bot-avatar">${icon('sparkle', 14)}</div>
      <div class="msg-main"><div class="bubble typing-bubble">
        <span class="typing-dots"><i></i><i></i><i></i></span>
        <span class="typing-text">正在检索笔记与手册…</span>
      </div></div>
    </div>`;
  }

  function emptyHtml() {
    const ctx = rag.context;
    const sugg = ctx
      ? ['这个项目最后是怎么处理的？', '同类张力问题一般先查什么？', '高创手册里张力锥度参数怎么设置？']
      : ['复卷机张力波动怎么排查？', 'E-07 报警是什么含义？', '对比高创与 ACS 的通讯配置'];
    return `<div class="rag-empty">
      <div class="rag-orb">${icon('sparkle', 22)}</div>
      <h3>${ctx ? '继续追问这个项目' : '问点什么？'}</h3>
      <p>${ctx ? `已自动聚焦 <strong>${U.esc(ctx.customer)} / ${U.esc(ctx.title)}</strong>，回答会优先引用该项目的处理记录。` : '从售后笔记和已入库手册中检索，回答逐条附带来源角标。'}</p>
      <div class="suggest-list">
        ${sugg.map((s) => `<button class="suggest" data-suggest="${U.esc(s)}">${icon('search', 12)} ${U.esc(s)}</button>`).join('')}
      </div>
      <div class="rag-empty-foot">${icon('shield', 11)} 未找到依据时会明确说明，不编造结论</div>
    </div>`;
  }

  function answerBodyHtml() {
    const ses = ensureSession();
    const msgs = ses.messages || [];
    if (!msgs.length && !rag.pending) return emptyHtml();
    return `<div class="msg-list">${msgs.map(messageHtml).join('')}${rag.pending ? typingHtml() : ''}</div>`;
  }

  function renderAnswerText(text, ids) {
    const list = (ids || []).map((id) => DATA.sources[id]).filter(Boolean);
    let html = U.md(text || '');
    html = html.replace(/\{\{(\d+)\}\}/g, (m, d) => {
      const s = list[Number(d)];
      return s ? `<button class="cite" data-src-id="${s.id}" title="${U.esc(s.meta)}">${Number(d) + 1}</button>` : '';
    });
    return html;
  }

  function messageHtml(m) {
    if (m.role === 'user') {
      return `<div class="msg user">
        <div class="msg-main"><div class="bubble">${U.esc(m.text).replace(/\n/g, '<br>')}</div>
        <div class="msg-meta"><span>${U.esc(m.time || '')}</span></div></div>
      </div>`;
    }
    return `<div class="msg bot ${m.noResult ? 'no-result' : ''}">
      <div class="bot-avatar">${icon('sparkle', 14)}</div>
      <div class="msg-main">
        ${m.noResult ? `<div class="no-result-flag">${icon('alertCircle', 13)} 未找到可依据的内容</div>` : ''}
        <div class="bubble md-body">${renderAnswerText(m.text, m.sources)}</div>
        ${sourcesHtml(m.sources)}
        <div class="msg-meta">
          <span>${m.scope ? '范围 · ' + U.esc(m.scope) : ''}${m.scope ? ' · ' : ''}${U.esc(m.time || '')}</span>
          <span class="msg-acts">
            ${(m.fragments || []).length ? `<button class="link-btn" data-frag-view>${icon('search', 11)} 命中片段</button>` : ''}
            <button class="link-btn" data-copy-msg>${icon('copy', 11)} 复制</button>
          </span>
        </div>
      </div>
    </div>`;
  }

  function sourcesHtml(ids) {
    if (!ids || !ids.length) return '';
    return `<div class="src-block">
      <div class="src-head">${icon('link', 12)} 来源引用 <em>${ids.length}</em></div>
      ${ids.map((id) => {
        const s = DATA.sources[id];
        if (!s) return '';
        return `<details class="src-item">
          <summary>
            <span class="src-type ${s.type}">${icon(s.type === 'note' ? 'notes' : 'book', 13)}</span>
            <span class="src-main"><strong>${U.esc(s.title)}</strong><em>${U.esc(s.section || '')} · ${U.esc(s.meta || '')}</em></span>
            <span class="src-caret">${icon('chevronDown', 13)}</span>
          </summary>
          <div class="src-detail"><p>${U.esc(s.snippet)}</p>
            <button class="link-btn" data-open-src="${s.id}">${s.type === 'note' ? '打开笔记' : '在内置阅读器定位'} ${icon('external', 11)}</button></div>
        </details>`;
      }).join('')}
    </div>`;
  }

  function fragmentsBodyHtml() {
    const ses = ensureSession();
    const last = [...ses.messages].reverse().find((m) => m.role === 'assistant');
    if (!last || !(last.fragments || []).length) {
      return `<div class="rag-tab-empty">
        <div class="rag-orb">${icon('search', 20)}</div>
        <h3>命中片段</h3>
        <p>提出一个问题后，这里会展示 Top-K 原文片段，按来源分组，不经模型改写。</p>
        <button class="btn ghost sm" data-goto-answer>去提问 ${icon('chevronRight', 12)}</button>
      </div>`;
    }
    const groups = {};
    last.fragments.forEach((f) => { (groups[f.sid] = groups[f.sid] || []).push(f.text); });
    return `<div class="frags-wrap">
      <div class="frags-head"><div><h3>命中片段</h3><span>Top ${last.fragments.length} · 原文直出，未经模型改写</span></div>
        ${last.scope ? `<em>${U.esc(last.scope)}</em>` : ''}</div>
      ${Object.keys(groups).map((sid) => {
        const s = DATA.sources[sid];
        if (!s) return '';
        return `<div class="frag-group">
          <button class="frag-source" data-open-src="${sid}">
            <span class="src-type ${s.type}">${icon(s.type === 'note' ? 'notes' : 'book', 13)}</span>
            <span class="frag-source-main"><strong>${U.esc(s.title)}</strong><em>${U.esc(s.section || '')} · ${U.esc(s.meta || '')}</em></span>
            ${icon('chevronRight', 13)}
          </button>
          ${groups[sid].map((t) => `<div class="frag-text">${U.esc(t)}</div>`).join('')}
        </div>`;
      }).join('')}
      <div class="frags-foot">${icon('info', 12)} 未入库或已删除的手册内容不会出现在片段中</div>
    </div>`;
  }

  function compareBodyHtml() {
    const brands = S().config.brands;
    const readyBrands = brands.filter((b) => S().manuals.some((m) => m.brand === b && m.status === 'ready'));
    const sel = rag.compare.brands;
    const canCompare = sel.length >= 2 && !rag.compare.loading;
    if (!rag.compare.generated) {
      return `<div class="compare-intro">
        <div class="rag-orb">${icon('columns', 20)}</div>
        <h3>跨厂家对比</h3>
        <p>勾选多个品牌，分别检索手册后按维度对齐输出对比表。每个结论都带来源，可跳转原手册页。</p>
        <div class="compare-brand-pick">${brands.map((b) => {
          const ready = readyBrands.includes(b);
          return `<label class="cmp-brand ${sel.includes(b) ? 'on' : ''} ${ready ? '' : 'disabled'}">
            <input type="checkbox" data-cbrand="${U.esc(b)}" ${sel.includes(b) ? 'checked' : ''} ${ready ? '' : 'disabled'} />
            <span>${U.esc(b)}</span>${ready ? '' : '<em>未入库</em>'}
          </label>`;
        }).join('')}</div>
        <div class="compare-preview-note">${icon('info', 12)} ${readyBrands.length} 个品牌有已入库手册；Agito 手册完成入库后可参与对比。</div>
        <button class="btn primary sm" data-compare-start ${sel.length < 2 ? 'disabled' : ''}>${icon('columns', 13)} 开始对比（${sel.length} 个品牌）</button>
        ${sel.length < 2 ? '<span class="compare-need">至少选择 2 个品牌</span>' : ''}
      </div>`;
    }
    if (rag.compare.loading) {
      return `<div class="compare-loading">
        <div class="cmp-spin">${icon('refresh', 22, 'spin')}</div>
        <h3>正在分别检索 ${sel.join(' / ')} 手册…</h3>
        <p>按维度对齐结论并保留来源，通常需要几秒钟。</p>
        <div class="cmp-skeleton"><span></span><span></span><span></span></div>
      </div>`;
    }
    const data = RagEngine.compareData();
    const cols = data.columns.map((c, i) => ({ ...c, i })).filter((c) => sel.includes(c.brand));
    const srcOrder = [];
    const cellHtml = (cell, ci) => {
      if (!cell) return '<span class="cmp-missing">—</span>';
      if (!srcOrder.includes(cell.sid)) srcOrder.push(cell.sid);
      return `<span>${U.esc(cell.text)}</span><button class="cite" data-src-id="${cell.sid}">${srcOrder.indexOf(cell.sid) + 1}</button>`;
    };
    return `<div class="compare-result">
      <div class="compare-result-head"><div><h3>${U.esc(data.title)}</h3><span>分源检索后按维度对齐 · 每格结论带来源</span></div>
        <button class="icon-btn" data-compare-reset title="重新选择">${icon('refresh', 15)}</button></div>
      <div class="compare-scroll">
        <table class="compare-table">
          <thead><tr><th>对比维度</th>${cols.map((c) => `<th>${U.esc(c.brand)}<em>${U.esc(c.manual)}</em></th>`).join('')}</tr></thead>
          <tbody>${data.rows.map((row) => `<tr><th>${U.esc(row.dim)}</th>${cols.map((c) => `<td>${cellHtml(row.cells[c.i], c.i)}</td>`).join('')}</tr>`).join('')}</tbody>
        </table>
      </div>
      <div class="compare-foot">${icon('link', 12)} 角标可点击，在内置阅读器中定位到对应页码
        <button class="link-btn" data-compare-reset>重新选择品牌</button></div>
      <div class="compare-hint">${icon('info', 12)} 对比仅基于已入库手册；缺失维度会以「—」标记，不使用未入库内容。</div>
    </div>`;
  }

  function bind() {
    const root = inner();
    const input = root.querySelector('#ragInput');
    if (input) {
      input.addEventListener('input', () => {
        rag.draft = input.value;
        input.style.height = 'auto';
        input.style.height = Math.min(120, input.scrollHeight) + 'px';
      });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          ask(input.value);
        }
      });
      if (rag.draft) {
        input.style.height = 'auto';
        input.style.height = Math.min(120, input.scrollHeight) + 'px';
      }
    }
    root.querySelector('#ragSend')?.addEventListener('click', () => { if (input) ask(input.value); });

    root.querySelector('#ragSessions')?.addEventListener('click', () => { rag.sessionsOpen = !rag.sessionsOpen; render(); });
    root.querySelector('#ragFull')?.addEventListener('click', () => { rag.fullscreen = !rag.fullscreen; render(); });
    root.querySelector('#ragCollapseBtn')?.addEventListener('click', collapsePanel);

    root.querySelector('#scopeBtn')?.addEventListener('click', () => { rag.scopeOpen = !rag.scopeOpen; render(); });
    root.querySelector('[data-scope-close]')?.addEventListener('click', () => { rag.scopeOpen = false; render(); });
    root.querySelectorAll('[data-scope]').forEach((b) => b.addEventListener('click', () => {
      const key = b.dataset.scope;
      if (key === 'brands') { rag.mode = 'brands'; render(); return; }
      rag.mode = key; rag.scopeOpen = false; render();
      U.toast('检索范围：' + scopeMeta().label, 'info');
    }));
    root.querySelectorAll('[data-sbrand]').forEach((b) => b.addEventListener('click', () => {
      const bd = b.dataset.sbrand;
      rag.mode = 'brands';
      rag.brands = rag.brands.includes(bd) ? rag.brands.filter((x) => x !== bd) : [...rag.brands, bd];
      render();
    }));
    root.querySelector('#scopeDone')?.addEventListener('click', () => {
      rag.scopeOpen = false;
      render();
      U.toast('检索范围：' + scopeMeta().label, 'info');
    });

    root.querySelectorAll('[data-rtab]').forEach((b) => b.addEventListener('click', () => {
      rag.tab = b.dataset.rtab;
      rag.sessionsOpen = false;
      S().ui.ragTab = rag.tab;
      U.Store.save();
      render();
    }));
    root.querySelectorAll('[data-suggest]').forEach((b) => b.addEventListener('click', () => ask(b.dataset.suggest)));
    root.querySelector('[data-goto-answer]')?.addEventListener('click', () => { rag.tab = 'answer'; S().ui.ragTab = 'answer'; render(); });

    root.querySelectorAll('[data-open-src],[data-src-id]').forEach((b) => b.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      const id = b.dataset.openSrc || b.dataset.srcId;
      Reader.openSource(DATA.sources[id]);
    }));
    root.querySelectorAll('[data-frag-view]').forEach((b) => b.addEventListener('click', () => {
      rag.tab = 'fragments'; S().ui.ragTab = 'fragments'; render();
    }));
    root.querySelectorAll('[data-copy-msg]').forEach((b) => b.addEventListener('click', () => {
      const bub = b.closest('.msg-main')?.querySelector('.bubble');
      if (bub) U.copy(bub.innerText);
    }));

    root.querySelector('[data-sess-new]')?.addEventListener('click', newSession);
    root.querySelectorAll('.sess-item').forEach((item) => {
      item.addEventListener('click', (e) => {
        if (e.target.closest('[data-sess-rename],[data-sess-del]')) return;
        S().activeSessionId = item.dataset.sess;
        rag.sessionsOpen = false;
        U.Store.save(); render();
      });
    });
    root.querySelectorAll('[data-sess-rename]').forEach((b) => b.addEventListener('click', () => renameSession(b.dataset.sessRename)));
    root.querySelectorAll('[data-sess-del]').forEach((b) => b.addEventListener('click', () => deleteSession(b.dataset.sessDel)));
    root.querySelector('[data-sess-clear]')?.addEventListener('click', clearAllSessions);
    root.querySelector('[data-sess-back]')?.addEventListener('click', () => { rag.sessionsOpen = false; render(); });

    root.querySelectorAll('[data-cbrand]').forEach((cb) => cb.addEventListener('change', () => {
      rag.compare.brands = Array.from(root.querySelectorAll('[data-cbrand]'))
        .filter((x) => x.checked).map((x) => x.dataset.cbrand);
      render();
    }));
    root.querySelector('[data-compare-start]')?.addEventListener('click', startCompare);
    root.querySelectorAll('[data-compare-reset]').forEach((b) => b.addEventListener('click', () => {
      rag.compare.generated = false; rag.compare.loading = false; render();
    }));
  }

  function scrollBottom() {
    requestAnimationFrame(() => {
      const body = document.getElementById('ragBody');
      if (body) body.scrollTop = body.scrollHeight;
    });
  }

  function ask(q) {
    q = (q || '').trim();
    if (!q) { openPanel(true); return; }
    if (rag.pending) { U.toast('请等待当前回答完成', 'info'); return; }
    const ses = ensureSession();
    const res = scopeMeta();
    const ctx = rag.context;
    const scopeLabel = res.label;
    ses.messages.push({ role: 'user', text: q, time: nowTime() });
    if (/^新会话/.test(ses.name) && ses.messages.length === 1) {
      ses.name = q.slice(0, 16) + (q.length > 16 ? '…' : '');
    }
    rag.pending = { q };
    rag.tab = 'answer';
    rag.sessionsOpen = false;
    rag.scopeOpen = false;
    rag.draft = '';
    U.Store.save();
    openPanel(false);
    scrollBottom();

    setTimeout(() => {
      const out = RagEngine.build(q, res, ctx);
      ses.messages.push({
        role: 'assistant', text: out.text, sources: out.sources, fragments: out.fragments || [],
        topic: out.topic, noResult: !!out.noResult, scope: scopeLabel, time: nowTime()
      });
      ses.updated = '今天 ' + nowTime();
      rag.pending = null;
      U.Store.save();
      render();
      scrollBottom();
      if (out.noResult) U.toast('当前范围内未找到依据，已明确说明', 'warn');
    }, 1100);
  }

  function newSession() {
    const st = S();
    const s = { id: U.uid('s'), name: '新会话 ' + (st.sessions.length + 1), updated: '刚刚', messages: [] };
    st.sessions.push(s);
    st.activeSessionId = s.id;
    rag.sessionsOpen = false;
    U.Store.save();
    render();
    rag.focusComposer = true;
    setTimeout(() => inner().querySelector('#ragInput')?.focus(), 50);
  }

  function renameSession(id) {
    const s = S().sessions.find((x) => x.id === id);
    if (!s) return;
    U.prompt({ title: '重命名会话', label: '会话名称', value: s.name }).then((v) => {
      if (!v) return;
      s.name = v;
      U.Store.save(); render();
      U.toast('会话已重命名', 'success');
    });
  }

  function deleteSession(id) {
    const st = S();
    const s = st.sessions.find((x) => x.id === id);
    if (!s) return;
    U.confirm({
      title: '删除这个会话？',
      message: `「${s.name}」中的 ${s.messages.length} 条问答记录将从本机永久删除，无法恢复。`,
      confirmText: '删除会话', danger: true
    }).then((ok) => {
      if (!ok) return;
      st.sessions = st.sessions.filter((x) => x.id !== id);
      if (!st.sessions.length) st.sessions = [{ id: 's1', name: '新会话', updated: '刚刚', messages: [] }];
      if (!st.sessions.some((x) => x.id === st.activeSessionId)) st.activeSessionId = st.sessions[0].id;
      U.Store.save(); render();
      U.toast('会话已删除', 'success');
    });
  }

  function clearAllSessions() {
    U.confirm({
      title: '清空全部会话？',
      message: `全部 ${S().sessions.length} 个会话及其问答记录将从本机永久删除，无法恢复。`,
      confirmText: '全部清空', danger: true
    }).then((ok) => {
      if (!ok) return;
      const st = S();
      st.sessions = [{ id: 's1', name: '新会话', updated: '刚刚', messages: [] }];
      st.activeSessionId = 's1';
      rag.sessionsOpen = false;
      U.Store.save(); render();
      U.toast('已清空全部会话', 'success');
    });
  }

  function startCompare() {
    if (rag.compare.brands.length < 2) { U.toast('至少选择两个品牌', 'warn'); return; }
    rag.compare.loading = true;
    render();
    setTimeout(() => {
      rag.compare.loading = false;
      rag.compare.generated = true;
      render();
      U.toast('对比表已生成，每个结论均带来源角标', 'success');
    }, 1200);
  }
})();
