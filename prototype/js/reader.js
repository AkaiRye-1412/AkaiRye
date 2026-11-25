(function () {
  const reader = { tabs: [], active: 0, open: false, zoom: 1, split: false, showHl: true, hl: null, bindKeys: null, localManuals: {} };

  const manualOf = (id) => reader.localManuals[id] || U.Store.manual(id);
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));

  function fallbackPage(m, no) {
    return {
      no, chapter: `第 ${Math.max(1, Math.floor(no / 20))} 章 · 技术说明`,
      heading: `${m.name.replace(/\.pdf$/i, '')} · 第 ${no} 页`,
      body: [
        `本页为演示用占位内容，摘录自《${m.name}》。实际内容由 MinerU 解析后按章节切块保留。`,
        `参数设置应在低速工况下完成；修改后需执行保存并重启控制使参数生效。若出现异常报警，应先导出诊断日志再复位。`,
        `相关章节的完整内容可在知识库中查看切块，或在检索结果中通过来源角标直接定位到本页。`
      ],
      highlight: 1
    };
  }
  function getPage(manualId, no) {
    const m = manualOf(manualId);
    if (!m) return null;
    const pages = DATA.manualPages[manualId];
    if (pages && pages[no]) return pages[no];
    if (pages) {
      const keys = Object.keys(pages).map(Number);
      let best = keys[0];
      keys.forEach((k) => { if (Math.abs(k - no) < Math.abs(best - no)) best = k; });
      const p = Object.assign({}, pages[best]);
      p.no = no;
      return p;
    }
    return fallbackPage(m, no);
  }

  function pageHtml(manualId, no, isActive) {
    const m = manualOf(manualId);
    const p = getPage(manualId, no);
    if (!m || !p) return '<div class="reader-missing">该手册已被删除或不可用</div>';
    const hl = reader.hl && reader.hl.manualId === manualId && reader.hl.page === no ? (p.highlight ?? 0) : -1;
    const paras = (p.body || []).map((t, i) => `<p class="pdf-p ${i === hl && reader.showHl ? 'hit' : ''}">${U.esc(t)}${i === hl && reader.showHl ? '<span class="pdf-hit">命中片段</span>' : ''}</p>`).join('');
    const table = p.table ? `<table class="pdf-table"><thead><tr>${p.table.head.map((h) => `<th>${U.esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${p.table.rows.map((r) => `<tr>${r.map((c) => `<td>${U.esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '';
    return `<div class="pdf-page ${isActive ? 'active-col' : ''}" data-manual="${manualId}" data-page="${no}">
      <div class="pdf-page-head"><span>${U.esc(m.name)}</span><span>${U.esc(p.chapter.split('·')[0].trim())}</span></div>
      <div class="pdf-content">
        <div class="pdf-chapter">${U.esc(p.chapter)}</div>
        <h3 class="pdf-heading">${U.esc(p.heading)}</h3>
        ${paras}${table}
        <div class="pdf-note">${icon('info', 12)} 页面内容为原型演示，来源引用原 PDF 文件，不复制文件。</div>
      </div>
      <div class="pdf-page-foot"><span>${U.esc(m.name)}</span><span>— ${no} —</span></div>
    </div>`;
  }

  function render() {
    const root = document.getElementById('readerRoot');
    if (!reader.open) { root.innerHTML = ''; return; }
    const tabs = reader.tabs;
    const activeTab = tabs[reader.active];
    const activeManual = activeTab ? manualOf(activeTab.manualId) : null;
    const canSplit = tabs.length >= 2;
    const splitTabs = canSplit ? [tabs[0], tabs[1]] : [];

    root.innerHTML = `<div class="reader-overlay">
      <div class="reader-frame">
        <header class="reader-top">
          <div class="reader-tabs">
            ${tabs.map((t, i) => {
              const m = manualOf(t.manualId);
              return `<button class="reader-tab ${i === reader.active ? 'active' : ''}" data-rtab="${i}">
                <span class="reader-tab-brand ${m && m.brand === 'ACS' ? 'acs' : m && m.brand === 'Agito' ? 'agito' : 'hk'}">${U.esc(m ? m.brand : '?')}</span>
                <span class="reader-tab-name">${U.esc((m ? m.name : '未知手册').replace(/\.pdf$/i, ''))}</span>
                <span class="reader-tab-close" data-rclose="${i}">${icon('x', 12)}</span>
              </button>`;
            }).join('')}
            <button class="reader-new" id="readerNew" title="打开其他手册">${icon('plus', 14)}</button>
          </div>
          <div class="reader-close-wrap">
            <button class="icon-btn" id="readerClose" title="关闭阅读器（Esc）">${icon('x', 18)}</button>
          </div>
        </header>
        <div class="reader-toolbar">
          <div class="reader-page-nav">
            <button class="icon-btn" id="pagePrev" title="上一页">${icon('left', 16)}</button>
            <input class="reader-page-input" id="pageInput" type="number" value="${activeTab ? activeTab.page : 1}" min="1" max="${activeTab ? (manualOf(activeTab.manualId)?.pages || 1) : 1}" />
            <span class="page-total">/ ${activeTab ? (manualOf(activeTab.manualId)?.pages || 1) : 1}</span>
            <button class="icon-btn" id="pageNext" title="下一页">${icon('chevronRight', 16)}</button>
          </div>
          <div class="reader-zoom">
            <button class="icon-btn" id="zoomOut" title="缩小">${icon('minimize', 15)}</button>
            <span class="zoom-val">${Math.round(reader.zoom * 100)}%</span>
            <button class="icon-btn" id="zoomIn" title="放大">${icon('expand', 15)}</button>
          </div>
          <div class="reader-tools">
            <button class="btn ghost sm ${reader.showHl ? 'on' : ''}" id="hlToggle">${icon('sparkle', 13)} 显示命中高亮</button>
            <button class="btn ghost sm ${reader.split ? 'on' : ''}" id="splitToggle" ${canSplit ? '' : 'disabled'} title="${canSplit ? '' : '至少打开两份手册后可并排'}">${icon('columns', 13)} 并排</button>
          </div>
          <span class="reader-source">${icon('file', 12)} 引用原文件 · 只读</span>
        </div>
        ${reader.hl ? `<div class="reader-hint">${icon('sparkle', 14)} 已定位到引用来源
          <strong>${U.esc(reader.hl.label || '')}</strong>
          <span>${U.esc(reader.hl.manualId ? (manualOf(reader.hl.manualId)?.name || '') : '')} · 第 ${reader.hl.page} 页</span>
          <button class="icon-btn" id="hintClose">${icon('x', 13)}</button></div>` : ''}
        ${activeManual && activeManual.local ? `<div class="reader-hint local">${icon('info', 14)} 本地预览
          <span>该 PDF 位于本地文件夹但尚未入库，不参与检索。可在「PDF手册」中加入知识库。</span></div>` : ''}
        <div class="reader-body ${reader.split && canSplit ? 'split' : ''}" id="readerBody" style="--zoom:${reader.zoom}">
          ${reader.split && canSplit
            ? splitTabs.map((t) => `<div class="reader-col">
                <div class="reader-col-head"><span>${U.esc(manualOf(t.manualId)?.name || '')}</span>
                  <span class="reader-col-nav">
                    <button class="icon-btn" data-cprev="${t.manualId}">${icon('left', 13)}</button>
                    <em>第 ${t.page} 页</em>
                    <button class="icon-btn" data-cnext="${t.manualId}">${icon('chevronRight', 13)}</button>
                  </span></div>
                <div class="reader-col-page">${pageHtml(t.manualId, t.page, t === activeTab)}</div>
              </div>`).join('')
            : `<div class="reader-single">${activeTab ? pageHtml(activeTab.manualId, activeTab.page, true) : '<div class="reader-missing">没有打开的手册</div>'}</div>`}
        </div>
        <footer class="reader-foot">
          <span>${icon('info', 12)} 内置 PDF 阅读器仅展示引用页；原文件由阅读器直读，不复制。</span>
          <span>页码跳转 · 命中高亮 · 多标签并排 · 来源可返回知识助手</span>
        </footer>
      </div>
    </div>`;

    bind();
    if (reader.hl && reader.showHl) {
      const hit = root.querySelector('.pdf-p.hit');
      if (hit) setTimeout(() => hit.scrollIntoView({ block: 'center', behavior: 'smooth' }), 120);
    }
  }

  function setPage(delta) {
    const t = reader.tabs[reader.active];
    if (!t) return;
    t.page = clamp(t.page + delta, 1, manualOf(t.manualId)?.pages || 1);
    if (reader.hl && reader.hl.manualId === t.manualId) {
      reader.hl = null;
    }
    render();
  }

  function bind() {
    const root = document.getElementById('readerRoot');
    root.querySelector('#readerClose').addEventListener('click', close);
    root.querySelectorAll('[data-rtab]').forEach((b) => b.addEventListener('click', (e) => {
      if (e.target.closest('[data-rclose]')) return;
      reader.active = Number(b.dataset.rtab);
      render();
    }));
    root.querySelectorAll('[data-rclose]').forEach((b) => b.addEventListener('click', (e) => {
      e.stopPropagation();
      const i = Number(b.dataset.rclose);
      reader.tabs.splice(i, 1);
      if (!reader.tabs.length) { close(); return; }
      reader.active = clamp(reader.active > i ? reader.active - 1 : reader.active, 0, reader.tabs.length - 1);
      render();
    }));
    root.querySelector('#readerNew').addEventListener('click', (e) => {
      const items = U.Store.state.manuals.filter((m) => m.status === 'ready' && !reader.tabs.some((t) => t.manualId === m.id))
        .map((m) => ({ icon: 'file', label: m.name.replace(/\.pdf$/i, ''), meta: `${m.brand} · ${m.pages} 页`, onClick: () => open(m.id, 1) }));
      if (!items.length) { U.toast('没有其他已入库手册可打开', 'warn'); return; }
      U.menu(e.currentTarget, items, { align: 'left' });
    });
    root.querySelector('#pagePrev').addEventListener('click', () => setPage(-1));
    root.querySelector('#pageNext').addEventListener('click', () => setPage(1));
    root.querySelector('#pageInput').addEventListener('change', (e) => {
      const t = reader.tabs[reader.active]; if (!t) return;
      t.page = clamp(parseInt(e.target.value, 10) || 1, 1, manualOf(t.manualId)?.pages || 1);
      reader.hl = null;
      render();
    });
    root.querySelector('#zoomIn').addEventListener('click', () => { reader.zoom = clamp(reader.zoom + 0.1, 0.7, 1.5); render(); });
    root.querySelector('#zoomOut').addEventListener('click', () => { reader.zoom = clamp(reader.zoom - 0.1, 0.7, 1.5); render(); });
    root.querySelector('#hlToggle').addEventListener('click', () => { reader.showHl = !reader.showHl; render(); });
    root.querySelector('#splitToggle').addEventListener('click', () => { reader.split = !reader.split; render(); });
    root.querySelector('#hintClose')?.addEventListener('click', () => { reader.hl = null; render(); });
    root.querySelectorAll('[data-cprev]').forEach((b) => b.addEventListener('click', () => {
      const t = reader.tabs.find((x) => x.manualId === b.dataset.cprev); if (!t) return;
      t.page = clamp(t.page - 1, 1, manualOf(t.manualId)?.pages || 1); render();
    }));
    root.querySelectorAll('[data-cnext]').forEach((b) => b.addEventListener('click', () => {
      const t = reader.tabs.find((x) => x.manualId === b.dataset.cnext); if (!t) return;
      t.page = clamp(t.page + 1, 1, manualOf(t.manualId)?.pages || 1); render();
    }));
  }

  function open(manualId, page, opts = {}) {
    const m = manualOf(manualId);
    if (!m) { U.toast('手册不存在或已被删除', 'warn'); return; }
    reader.open = true;
    const existing = reader.tabs.find((t) => t.manualId === manualId);
    if (existing) {
      reader.active = reader.tabs.indexOf(existing);
      existing.page = clamp(page || existing.page, 1, m.pages);
    } else {
      reader.tabs.push({ manualId, page: clamp(page || 1, 1, m.pages) });
      reader.active = reader.tabs.length - 1;
    }
    if (opts.cite) {
      reader.hl = { manualId, page: page || 1, label: opts.cite };
    } else if (opts.keepHl === false) {
      reader.hl = null;
    }
    render();
    if (!reader.bindKeys) {
      reader.bindKeys = (e) => {
        if (e.key === 'Escape') { close(); }
      };
      document.addEventListener('keydown', reader.bindKeys);
    }
  }

  function close() {
    reader.open = false;
    reader.tabs = [];
    reader.hl = null;
    reader.split = false;
    document.getElementById('readerRoot').innerHTML = '';
    if (reader.bindKeys) { document.removeEventListener('keydown', reader.bindKeys); reader.bindKeys = null; }
  }

  function openLocal(file, page) {
    if (!file) return;
    if (!reader.localManuals[file.id]) {
      reader.localManuals[file.id] = {
        id: file.id, name: file.name, brand: file.brand, pages: file.pages || 80, local: true
      };
    }
    reader.hl = null;
    open(file.id, page || 1);
  }

  function openSource(src) {
    if (!src) return;
    if (src.type === 'note') {
      const note = U.Store.note(src.noteId);
      if (!note) { U.toast('对应笔记已不存在', 'warn'); return; }
      close();
      location.hash = '#/note/' + src.noteId;
      U.toast('已打开笔记来源', 'info');
      return;
    }
    if (src.type === 'manual') {
      if (U.Store.manual(src.manualId)) {
        open(src.manualId, src.page || 1, { cite: `${src.section || ''} · ${src.meta || ''}` });
      } else {
        U.toast('该手册已从知识库删除，引用失效', 'warn');
      }
    }
  }

  function removeManual(manualId) {
    reader.tabs = reader.tabs.filter((t) => t.manualId !== manualId);
    reader.active = clamp(reader.active, 0, Math.max(0, reader.tabs.length - 1));
    if (reader.open) {
      if (!reader.tabs.length) close(); else render();
    }
  }

  window.Reader = { open, close, openSource, openLocal, removeManual };
})();
