(function () {
  window.Views = window.Views || {};
  const STATUS_LABEL = { ready: '已入库', parsing: '解析中', rebuilding: '重建中', queue: '排队', failed: '失败', update: '有更新', invalid: '路径失效' };

  function brandBadge(b) {
    const cls = b === '高创' ? 'hk' : b === 'ACS' ? 'acs' : b === 'Agito' ? 'agito' : 'unknown';
    return `<span class="brand-badge ${cls}">${U.esc(b || '未知')}</span>`;
  }
  function detectBrand(name) {
    if (/高创|CDV/i.test(name)) return '高创';
    if (/ACS|SPii/i.test(name)) return 'ACS';
    if (/Agito|AG-?20/i.test(name)) return 'Agito';
    return '未知';
  }
  function queuePos(m) {
    const active = U.Store.state.manuals.filter((x) => x.status === 'queue');
    return active.findIndex((x) => x.id === m.id) + 1;
  }
  function statusChip(m) {
    if (m.status === 'ready') return `<span class="mstatus ready">${icon('checkCircle', 13)}已入库</span>`;
    if (m.status === 'queue') return `<span class="mstatus queued">${icon('clock', 13)}排队中 · 第 ${queuePos(m)} 位</span>`;
    if (m.status === 'parsing') return `<span class="mstatus parsing">${icon('refresh', 13)}解析中 ${m.progress}%</span>`;
    if (m.status === 'rebuilding') return `<span class="mstatus parsing">${icon('refresh', 13)}重建中 ${m.progress}% <em>旧索引可用</em></span>`;
    if (m.status === 'failed') return `<span class="mstatus failed">${icon('alertCircle', 13)}解析失败</span>`;
    if (m.status === 'update') return `<button class="mstatus update" data-rebuild="${m.id}">${icon('alert', 13)}有更新 · 待重建</button>`;
    if (m.status === 'invalid') return `<span class="mstatus invalid">${icon('link', 13)}路径失效</span>`;
    return `<span class="mstatus">${U.esc(STATUS_LABEL[m.status] || m.status)}</span>`;
  }

  Views.kb = function (el) {
    const S = U.Store.state;
    const state = { q: '', brand: 'all', status: 'all', collapsedGroups: new Set() };
    const idx = { running: false, pct: 0, timer: null };

    function isUnder(path, parent) { return path === parent || path.indexOf(parent + '/') === 0; }
    const noteList = () => (window.RagEngine ? RagEngine.vaultSelected() : []);
    function noteStatus(path) { return ((S.ui || {}).noteStatus || {})[path] || 'ready'; }
    function setNoteStatus(path, st) {
      if (!S.ui) S.ui = {};
      if (!S.ui.noteStatus) S.ui.noteStatus = {};
      S.ui.noteStatus[path] = st;
      U.Store.save();
    }

    function noteSrcChips() {
      const s = S.config.indexSelection || { folders: [], files: [] };
      const list = noteList();
      const chips = (s.folders || []).map((f) => {
        const n = list.filter((it) => isUnder(it.path, f)).length;
        return `<span class="kb-src-chip">${icon('folder', 12)} ${U.esc(f)} <em>${n} 篇</em></span>`;
      }).concat((s.files || []).map((p) => `<span class="kb-src-chip">${icon('file', 12)} ${U.esc(p.split('/').pop())}</span>`));
      return chips.join('') || `<span class="muted">尚未勾选 · 去「所有笔记」勾选文件夹</span>`;
    }

    function focusNoteInRag(item) {
      const hasNote = !!item.note;
      const ctx = hasNote ? item.note : { customer: item.section, title: item.title, id: item.path };
      if (window.Rag && Rag.setContext) {
        Rag.setContext(ctx);
        if (Rag.state) Rag.state.mode = hasNote ? 'auto' : 'notes';
        Rag.ask(item.title + ' 的处理要点？');
      } else {
        showNoteChunks(item);
      }
    }

    function noteRowHtml(item) {
      const st = noteStatus(item.path);
      const badge = st === 'pending'
        ? `<span class="nstatus pending">${icon('refresh', 12)}待索引</span>`
        : st === 'failed'
          ? `<span class="nstatus failed">${icon('alertCircle', 12)}失败</span>`
          : `<span class="nstatus ready">${icon('checkCircle', 12)}已索引</span>`;
      return `<div class="kb-note-row" data-path="${U.esc(item.path)}">
        <span class="kb-note-ic">${icon('file', 13)}</span>
        <div class="kb-note-main">
          <span class="kb-note-name" title="${U.esc(item.title)}">${U.esc(item.title)}</span>
          <span class="kb-note-sub"><span class="kb-note-path" title="${U.esc(item.path)}">${U.esc(item.path)}</span><span class="meta-dot"></span>${U.esc(item.updated)} 修改</span>
        </div>
        ${badge}
        <button class="icon-btn kb-note-more" data-note-act="${U.esc(item.path)}" title="更多操作">${icon('more', 16)}</button>
      </div>`;
    }

    function noteGroupsHtml() {
      const list = noteList();
      if (!list.length) {
        return `<div class="empty-block"><div class="empty-ic">${icon('folder', 28)}</div>
          <p>尚未勾选任何笔记</p><span class="empty-sub">在「所有笔记」勾选文件夹，即可纳入知识库</span></div>`;
      }
      const groups = {};
      list.forEach((it) => { const g = it.section; (groups[g] = groups[g] || []).push(it); });
      return Object.keys(groups).sort((a, b) => a.localeCompare(b, 'zh-CN')).map((g) => {
        const items = groups[g];
        const collapsed = state.collapsedGroups.has(g);
        return `<div class="kb-note-group" data-group="${U.esc(g)}">
          <div class="kb-note-group-head" data-toggle-group="${U.esc(g)}">
            <span class="kb-note-caret">${icon(collapsed ? 'chevronRight' : 'chevronDown', 13)}</span>
            <span class="kb-note-fic">${icon('folder', 14)}</span>
            <span class="kb-note-gname">${U.esc(g)}</span>
            <span class="kb-note-gcount">${items.length} 篇</span>
          </div>
          ${collapsed ? '' : `<div class="kb-note-group-body">${items.map(noteRowHtml).join('')}</div>`}
        </div>`;
      }).join('');
    }

    function findNoteBody(item) {
      if (item.note) return item.note.body;
      const e = (DATA.vaultExtras || []).find((x) => x.path === item.path);
      return e ? e.body : '';
    }
    function splitNoteChunks(body) {
      if (!body) return [];
      const lines = body.replace(/\r\n/g, '\n').split('\n');
      const chunks = []; let cur = null;
      lines.forEach((l) => {
        const m = l.match(/^##\s+(.+)$/);
        if (m) { if (cur) chunks.push(cur); cur = { head: m[1], lines: [] }; }
        else if (cur) cur.lines.push(l);
        else if (l.trim() && !/^#\s/.test(l)) cur = { head: '正文', lines: [l] };
      });
      if (cur) chunks.push(cur);
      return chunks.map((c) => ({
        head: c.head,
        text: (c.lines.join('\n').trim().slice(0, 160)) || '（空）',
        chars: c.lines.join('').replace(/\s/g, '').length
      }));
    }

    function showNoteChunks(item) {
      const chunks = splitNoteChunks(findNoteBody(item));
      U.modal({
        title: '片段预览 · ' + item.title,
        subtitle: `${chunks.length} 个切块 · 按小节保留层级 · 来源 ${item.path}`,
        width: 720,
        body: `<div class="chunk-list">${chunks.map((c, i) => `<div class="chunk-item">
            <div class="chunk-head"><span class="chunk-no">#${String(i + 1).padStart(3, '0')}</span>
              <strong>${U.esc(c.head)}</strong><em>${c.chars} 字</em></div>
            <p>${U.esc(c.text)}</p>
          </div>`).join('')}</div>`,
        actions: [{ label: '关闭', variant: 'ghost' }]
      });
    }

    function rebuildNote(item) {
      setNoteStatus(item.path, 'pending');
      refreshNoteSection();
      U.toast(`正在重建笔记索引：${item.title}`, 'info');
      setTimeout(() => {
        setNoteStatus(item.path, 'ready');
        refreshNoteSection();
        U.toast(`笔记索引已重建：${item.title}`, 'success');
      }, 1600);
    }

    function removeNote(item) {
      U.confirm({
        title: '从知识库移出这篇笔记？',
        message: `将「${item.title}」移出知识库范围，对应索引与向量会清理。笔记文件不会被删除，仍可在「所有笔记」阅读，也可随时重新勾选纳入。`,
        confirmText: '移出并清理', danger: true
      }).then((ok) => {
        if (!ok) return;
        RagEngine.removeNoteFromIndex(item.path);
        U.Store.save();
        refreshNoteSection();
        refreshDynamic();
        U.toast(`已移出知识库：${item.title}`, 'info');
      });
    }

    function openNoteActions(path, anchor) {
      const item = noteList().find((it) => it.path === path);
      if (!item) return;
      const st = noteStatus(path);
      U.menu(anchor, [
        { icon: 'sparkle', label: '在知识助手检索', onClick: () => focusNoteInRag(item) },
        { icon: 'refresh', label: st === 'pending' ? '索引中…' : '立即重建', disabled: st === 'pending', onClick: () => rebuildNote(item) },
        { divider: true },
        { icon: 'external', label: '在「所有笔记」中打开', onClick: () => { location.hash = '#/allnotes'; } },
        { icon: 'trash', label: '移出知识库', danger: true, onClick: () => removeNote(item) }
      ], { align: 'right' });
    }

    function refreshNoteSection() {
      if (!el.isConnected || !el.querySelector('#kbNotesGroups')) return;
      const ov = el.querySelector('#ovNotes'); if (ov) ov.textContent = noteList().length;
      const nsc = el.querySelector('#noteSrcChips'); if (nsc) nsc.innerHTML = noteSrcChips();
      el.querySelector('#kbNotesGroups').innerHTML = noteGroupsHtml();
    }

    function filtered() {
      return S.manuals.filter((m) => {
        if (state.brand !== 'all' && m.brand !== state.brand) return false;
        if (state.status === 'ready' && m.status !== 'ready') return false;
        if (state.status === 'processing' && !['parsing', 'queue', 'rebuilding'].includes(m.status)) return false;
        if (state.status === 'issue' && !['failed', 'invalid', 'update'].includes(m.status)) return false;
        if (state.status === 'update' && m.status !== 'update') return false;
        const q = state.q.trim().toLowerCase();
        if (!q) return true;
        return (m.name + ' ' + m.path + ' ' + m.brand).toLowerCase().includes(q);
      }).sort((a, b) => {
        const rank = (m) => ({ failed: 0, update: 1, invalid: 2, parsing: 3, rebuilding: 3, queue: 4, ready: 5 }[m.status] ?? 6);
        return rank(a) - rank(b);
      });
    }
    const counts = () => ({
      ready: S.manuals.filter((m) => m.status === 'ready').length,
      processing: S.manuals.filter((m) => ['parsing', 'queue', 'rebuilding'].includes(m.status)).length,
      issue: S.manuals.filter((m) => ['failed', 'invalid'].includes(m.status)).length,
      update: S.manuals.filter((m) => m.status === 'update').length
    });

    function rowHtml(m) {
      const processing = m.status === 'parsing' || m.status === 'rebuilding';
      return `<div class="kb-row" data-id="${m.id}">
        <div class="kb-file">
          <span class="pdf-ic">PDF</span>
          <div class="kb-file-main">
            <span class="kb-name" title="${U.esc(m.name)}">${U.esc(m.name)}</span>
            <span class="kb-sub">${brandBadge(m.brand)} <span>${m.pages} 页</span><span class="meta-dot"></span>
              <span class="kb-path" title="${U.esc(m.path)}">${U.esc(m.path)}</span></span>
          </div>
        </div>
        <div class="kb-status-cell">${statusChip(m)}
          ${processing ? U.meter(m.progress) : ''}
          ${m.status === 'failed' ? `<em class="kb-error">${U.esc(m.error || '解析失败')}</em>` : ''}
          ${m.status === 'update' ? '<em class="kb-error warn-text">原文件内容已变化</em>' : ''}
        </div>
        <div class="kb-chunk">${m.chunks ? `<strong>${U.num(m.chunks)}</strong><span>切块</span>` : '<strong>—</strong><span>切块</span>'}</div>
        <div class="kb-updated"><strong>${U.fmtDate(m.updated)}</strong><span>${U.relDays(m.updated)}</span></div>
        <button class="icon-btn kb-more" data-mact="${m.id}" title="更多操作">${icon('more', 16)}</button>
      </div>`;
    }

    function queueHtml() {
      const jobs = S.manuals.filter((m) => ['parsing', 'rebuilding', 'queue'].includes(m.status));
      if (!jobs.length) return '';
      return `<div class="kb-queue" id="kbQueue">
        <div class="kb-queue-head">${icon('layers', 15)} 入库队列 <em>${jobs.length} 个任务 · 后台执行，不影响使用</em></div>
        ${jobs.slice(0, 3).map((m) => `<div class="kb-job">
          <span class="kb-job-name">${U.esc(m.name)}</span>
          <span class="kb-job-status">${m.status === 'queue' ? '排队 · 第 ' + queuePos(m) + ' 位' : (m.status === 'rebuilding' ? '重建中' : '解析中') + ' ' + m.progress + '%'}</span>
          ${m.status === 'queue' ? '<span class="kb-job-wait">等待 MinerU</span>' : U.meter(m.progress)}
        </div>`).join('')}
      </div>`;
    }

    function listHtml() {
      const list = filtered();
      if (!list.length) {
        return `<div class="empty-block"><div class="empty-ic">${icon('book', 28)}</div><p>没有匹配的手册</p>
          <span class="empty-sub">调整筛选条件，或拖入新的 PDF 入库</span></div>`;
      }
      return list.map(rowHtml).join('');
    }

    function pageHtml() {
      const c = counts();
      return `<div class="page page-kb">
        <section class="kb-overview">
          <div class="kb-overview-bar">
            <div class="kb-ov-item">
              <span class="kb-ov-ic">${icon('notes', 15)}</span>
              <strong id="ovNotes">${noteList().length}</strong><span>篇笔记已纳入</span>
            </div>
            <span class="kb-ov-sep"></span>
            <div class="kb-ov-item">
              <span class="kb-ov-ic">${icon('book', 15)}</span>
              <strong id="ovManuals">${c.ready}</strong><span>份手册已入库</span>
              <em class="kb-ov-warn" id="ovWarn" style="display:${(c.issue + c.update) ? '' : 'none'}">${icon('alertCircle', 11)} ${c.issue + c.update} 待处理</em>
            </div>
            <span class="kb-ov-sep"></span>
            <div class="kb-ov-item">
              <span class="live-dot">监听中</span><span class="kb-ov-meta">保存后 ${S.config.indexDelay} 秒增量 · 仅重建变化文件</span>
            </div>
            <div class="kb-ov-actions">
              <button class="link-btn" id="btnReindexNotes">${icon('refresh', 12)} 重建笔记索引</button>
            </div>
          </div>
          <div id="noteMeterWrap"></div>
        </section>

        <section class="card kb-notes-section">
          <header class="kb-toolbar kb-notes-head">
            <div class="kb-toolbar-head">
              <div class="card-title"><h2>笔记资源</h2><span class="card-sub">来自「所有笔记」勾选范围 · 自动增量索引 · 未勾选笔记不参与检索</span></div>
              <a class="link-btn" href="#/allnotes">${icon('external', 12)} 在「所有笔记」管理勾选</a>
            </div>
            <div class="kb-note-src-row" id="noteSrcChips">${noteSrcChips()}</div>
          </header>
          <div id="kbNotesGroups">${noteGroupsHtml()}</div>
          <footer class="card-foot">
            <span>${icon('shield', 13)} 笔记切块内容会发送至云端 embedding 服务（已确认授权外传）</span>
            <span class="foot-dot"></span><span>${icon('refresh', 12)} 保存后 ${S.config.indexDelay} 秒自动增量，仅重建变化文件</span>
          </footer>
        </section>

        <section class="card kb-main">
          <header class="kb-toolbar">
            <div class="kb-toolbar-head">
              <div class="card-title"><h2>手册知识库</h2><span class="card-sub">关键词 + 向量混合检索的资料来源</span></div>
              <button class="btn primary sm" id="btnImport" title="选择 PDF 加入入库队列（仅支持 PDF）">${icon('upload', 14)} 导入 PDF</button>
              <input type="file" id="kbFileInput" multiple accept=".pdf,application/pdf" hidden />
            </div>
            <div class="kb-toolbar-right">
              <div class="search-field">
                ${icon('search', 15)}<input id="kbSearch" placeholder="搜索手册名称或路径…" autocomplete="off" />
              </div>
              <div class="brand-chips" id="brandChips">
                ${['all', '高创', 'ACS', 'Agito', '未知'].map((b) => {
                  const n = b === 'all' ? S.manuals.length : S.manuals.filter((m) => m.brand === b).length;
                  return `<button class="brand-chip ${b === state.brand ? 'active' : ''} ${b === 'all' ? 'all' : ''}" data-brand="${b}">${b === 'all' ? '全部' : b}<em>${n}</em></button>`;
                }).join('')}
              </div>
              <select class="input select sm" id="kbStatusFilter" aria-label="按状态筛选">
                <option value="all">全部状态</option>
                <option value="ready">已入库</option>
                <option value="processing">处理中</option>
                <option value="update">有更新</option>
                <option value="issue">异常</option>
              </select>
            </div>
          </header>

          <div id="kbQueueWrap">${queueHtml()}</div>

          <div class="kb-list-head">
            <span>手册</span><span>状态</span><span>切块</span><span>最近更新</span><span></span>
          </div>
          <div class="kb-list" id="kbList">${listHtml()}</div>
          <footer class="card-foot">
            <span>${icon('shield', 13)} 切块内容会发送至云端 embedding 服务（已确认授权外传）</span>
            <span class="foot-dot"></span><span>${icon('link', 12)} 原文件移动或改名后标记失效，可重新定位</span>
          </footer>
        </section>
      </div>`;
    }

    function refreshDynamic() {
      if (!el.isConnected || !el.querySelector('#ovManuals')) return;
      const c = counts();
      const ovM = el.querySelector('#ovManuals'); if (ovM) ovM.textContent = c.ready;
      const ovW = el.querySelector('#ovWarn');
      if (ovW) {
        const w = c.issue + c.update;
        if (w) { ovW.style.display = ''; ovW.innerHTML = `${icon('alertCircle', 11)} ${w} 待处理`; }
        else ovW.style.display = 'none';
      }
      el.querySelector('#kbQueueWrap').innerHTML = queueHtml();
      el.querySelector('#kbList').innerHTML = listHtml();
      const ovN = el.querySelector('#ovNotes'); if (ovN) ovN.textContent = noteList().length;
      const nsc = el.querySelector('#noteSrcChips'); if (nsc) nsc.innerHTML = noteSrcChips();
    }
    Views.kbRefresh = refreshDynamic;

    function updateIndexCard() {
      const wrap = el.querySelector('#noteMeterWrap');
      const btn = el.querySelector('#btnReindexNotes');
      if (!wrap) return;
      if (idx.running) {
        wrap.innerHTML = `<div class="kb-reindex"><span>正在重建 ${idx.pct}%</span>${U.meter(idx.pct)}</div>`;
        btn.disabled = true;
        btn.textContent = '重建中…';
      } else {
        wrap.innerHTML = '';
        btn.disabled = false;
        btn.textContent = '重建索引';
      }
    }

    function importFiles(files) {
      const pdfs = Array.from(files).filter((f) => /pdf$/i.test(f.name));
      if (!pdfs.length) { U.toast('仅支持 PDF 文件', 'warn'); return; }
      let added = 0;
      pdfs.forEach((f) => {
        if (S.manuals.some((m) => m.name === f.name)) return;
        const pages = Math.max(20, Math.round((f.size || 100000) / 1600));
        S.manuals.unshift({
          id: U.uid('m'), name: f.name, brand: detectBrand(f.name), pages,
          status: 'queue', chunks: 0, size: ((f.size || 1) / 1048576).toFixed(1) + ' MB',
          path: 'D:\\手册\\导入\\' + f.name, added: U.todayStr(), updated: U.todayStr(), progress: 0
        });
        added++;
      });
      if (!added) { U.toast('相同文件已在知识库中', 'warn'); return; }
      U.Store.save();
      U.toast(`已加入入库队列：${added} 份 PDF`, 'success');
      refreshDynamic();
      App.startKbEngine && App.startKbEngine();
    }

    function bind() {
    el.querySelector('#kbSearch').addEventListener('input', (e) => { state.q = e.target.value; refreshDynamic(); });
    el.querySelector('#kbStatusFilter').addEventListener('change', (e) => { state.status = e.target.value; refreshDynamic(); });
    el.querySelector('#brandChips').addEventListener('click', (e) => {
      const b = e.target.closest('[data-brand]');
      if (!b) return;
      state.brand = b.dataset.brand;
      el.querySelectorAll('[data-brand]').forEach((x) => x.classList.toggle('active', x === b));
      refreshDynamic();
    });
    const fileInput = el.querySelector('#kbFileInput');
    fileInput.addEventListener('change', () => { importFiles(fileInput.files); fileInput.value = ''; });
    el.querySelector('#btnImport').addEventListener('click', () => fileInput.click());
    el.querySelector('#btnReindexNotes').addEventListener('click', () => {
      if (idx.running) return;
      const list = noteList();
      if (!list.length) { U.toast('当前没有已勾选的笔记，请先在「所有笔记」勾选', 'warn'); return; }
      idx.running = true; idx.pct = 0; updateIndexCard();
      list.forEach((it) => setNoteStatus(it.path, 'pending'));
      refreshNoteSection();
      idx.timer = setInterval(() => {
        idx.pct += Math.floor(Math.random() * 7) + 5;
        if (idx.pct >= 100) {
          idx.pct = 100; clearInterval(idx.timer); idx.running = false;
          list.forEach((it) => setNoteStatus(it.path, 'ready'));
          updateIndexCard(); refreshNoteSection();
          U.toast(`${list.length} 篇笔记索引已重建`, 'success');
        } else updateIndexCard();
      }, 320);
    });
    const notesGroups = el.querySelector('#kbNotesGroups');
    if (notesGroups) {
      notesGroups.addEventListener('click', (e) => {
        const tog = e.target.closest('[data-toggle-group]');
        if (tog) {
          const g = tog.dataset.toggleGroup;
          if (state.collapsedGroups.has(g)) state.collapsedGroups.delete(g); else state.collapsedGroups.add(g);
          notesGroups.innerHTML = noteGroupsHtml();
          return;
        }
        const more = e.target.closest('[data-note-act]');
        if (more) openNoteActions(more.dataset.noteAct, more);
      });
    }
    el.querySelector('#kbList').addEventListener('click', (e) => {
      const rebuild = e.target.closest('[data-rebuild]');
      if (rebuild) { promptRebuild(rebuild.dataset.rebuild); return; }
      const more = e.target.closest('[data-mact]');
      if (more) openActions(more.dataset.mact, more);
    });
    }

    /* ---------- 行操作与弹窗 ---------- */
    function openActions(id, anchor) {
      const m = U.Store.manual(id);
      if (!m) return;
      const items = [];
      if (m.status === 'ready' || m.status === 'update' || m.status === 'rebuilding') {
        items.push({ icon: 'layers', label: '查看切块', onClick: () => showChunks(m) });
      }
      if (m.status === 'failed') {
        items.push({ icon: 'refresh', label: '重试解析', onClick: () => retryManual(m) });
      }
      if (m.status === 'invalid') {
        items.push({ icon: 'link', label: '重新定位原文件', onClick: () => relocate(m) });
      }
      if (m.status === 'update') {
        items.push({ icon: 'refresh', label: '立即重建索引', onClick: () => promptRebuild(m.id) });
      }
      if (m.status === 'ready') {
        items.push({ icon: 'refresh', label: '重建索引', onClick: () => promptRebuild(m.id) });
      }
      items.push({ divider: true });
      items.push({ icon: 'copy', label: '复制原文件路径', onClick: () => U.copy(m.path) });
      items.push({ icon: 'trash', label: '删除并清理向量', danger: true, onClick: () => removeManual(m) });
      U.menu(anchor, items, { align: 'right' });
    }

    function retryManual(m) {
      m.status = 'queue'; m.progress = 0; m.error = null; m.chunks = 0;
      U.Store.save(); refreshDynamic();
      U.toast('已重新加入入库队列', 'success');
      App.startKbEngine && App.startKbEngine();
    }

    function promptRebuild(id) {
      const m = U.Store.manual(id);
      if (!m) return;
      U.modal({
        title: '检测到原文件内容变化',
        subtitle: '原 PDF 内容已被修改。重建期间将保留旧索引，检索不中断。',
        width: 520,
        body: `<div class="choice-note">${icon('info', 15)} 重建需要重新经过 MinerU 解析与云端向量化，会产生 embedding 调用费用。</div>
          <div class="choice-list">
            <div><strong>立即重建</strong><span>后台执行，旧索引在新索引完成前继续可用</span></div>
            <div><strong>稍后</strong><span>保持「有更新」状态，不影响当前检索</span></div>
            <div><strong>忽略</strong><span>继续使用旧索引，直到下次重建</span></div>
          </div>`,
        actions: [
          { label: '忽略', variant: 'ghost', onClick: () => { m.status = 'ready'; U.Store.save(); refreshDynamic(); U.toast('已忽略更新，继续使用旧索引', 'info'); } },
          { label: '稍后', variant: 'ghost', onClick: () => { U.toast('已保留「有更新」标记，稍后可在列表中重建', 'info'); } },
          { label: '立即重建', variant: 'primary', onClick: () => {
            m.status = 'rebuilding'; m.progress = 0;
            U.Store.save(); refreshDynamic();
            U.toast('已开始重建，旧索引保持可用', 'success');
            App.startKbEngine && App.startKbEngine();
          } }
        ]
      });
    }

    function showChunks(m) {
      const chapterSamples = [
        ['§7.3 张力控制参数', 'P-04.12 张力锥度系数：设定收卷过程中的张力递减比例，范围 0–100%，默认 50%。'],
        ['§7.4 卷径补偿', '卷径补偿支持外部模拟量输入与内部计算两种模式；内部计算需设置初卷径与材料厚度。'],
        ['§5.2 通讯配置', 'PDO 刷新周期建议与主站任务周期一致；干扰环境下可将 Sync 周期调整至 1 ms。'],
        ['§4.4 状态字与故障码', 'Bus Error 对应状态字 bit 12，常见原因为从站掉线、PDO 周期不匹配、屏蔽失效。'],
        ['§6.2 调试软件', '通过调试工具可导出参数表与故障日志，建议在复位前完成导出。']
      ];
      U.modal({
        title: '切块预览 · ' + m.name,
        subtitle: `${U.num(m.chunks)} 个切块 · 按章节保留标题层级 · 引用 ${m.pages} 页原 PDF`,
        width: 720,
        body: `<div class="chunk-list">${chapterSamples.map(([sec, text], i) => `
          <div class="chunk-item">
            <div class="chunk-head"><span class="chunk-no">#${String(i + 1).padStart(3, '0')}</span>
              <strong>${U.esc(sec)}</strong><em>第 ${120 + i * 6} 页</em>
              ${brandBadge(m.brand)}</div>
            <p>${U.esc(text)}</p>
            <div class="chunk-foot"><span>来源：${U.esc(m.name)}</span><button class="link-btn" data-open-reader>在内置阅读器打开 ${icon('external', 11)}</button></div>
          </div>`).join('')}</div>`,
        onMount: (wrap, close) => {
          wrap.querySelector('[data-open-reader]').addEventListener('click', () => {
            close();
            Reader.open(m.id, 128);
          });
        }
      });
    }

    function relocate(m) {
      U.modal({
        title: '重新定位原文件',
        subtitle: '工作台只记录引用路径，不会复制或删除 PDF 文件。',
        width: 560,
        body: `<label class="field"><span class="field-label">新的文件路径</span>
            <input class="input mono" id="relocatePath" value="D:\\手册\\${U.esc(m.brand)}\\${U.esc(m.name)}" /></label>
          <p class="modal-hint">上次记录的路径：<code>${U.esc(m.path)}</code></p>`,
        actions: [
          { label: '取消', variant: 'ghost' },
          { label: '确认并重新入库', variant: 'primary', onClick: (close, wrap) => {
            const p = wrap.querySelector('#relocatePath').value.trim();
            if (!p) { U.toast('路径不能为空', 'warn'); return false; }
            m.path = p; m.status = 'queue'; m.progress = 0; m.chunks = 0; m.error = null;
            U.Store.save(); refreshDynamic();
            U.toast('已更新引用路径并重新加入入库队列', 'success');
            App.startKbEngine && App.startKbEngine();
          } }
        ]
      });
    }

    function removeManual(m) {
      U.confirm({
        title: '删除这份手册？',
        message: `将从知识库中删除「${m.name}」及其 ${U.num(m.chunks || 0)} 个切块与向量。原 PDF 文件不会被删除。删除后相关内容不再出现在检索结果中。`,
        confirmText: '删除并清理',
        danger: true
      }).then((ok) => {
        if (!ok) return;
        S.manuals = S.manuals.filter((x) => x.id !== m.id);
        U.Store.save(); refreshDynamic();
        U.toast('已删除并清理对应索引与向量', 'success');
        App.refreshNavBadges && App.refreshNavBadges();
        Rag.onManualRemoved && Rag.onManualRemoved(m.id);
      });
    }

    function render() {
      try {
        el.innerHTML = pageHtml();
        bind();
        updateIndexCard();
      } catch (err) {
        console.error('[views-kb] render error:', err);
        el.innerHTML = `<div class="page" style="padding:48px 32px"><div class="empty-block"><div class="empty-ic">${icon('alertCircle', 28)}</div><p>知识库页面渲染出错</p><span class="empty-sub">${U.esc(err && err.message ? err.message : String(err))}</span><span class="empty-sub">若持续报错，请按 Ctrl+Shift+R 硬刷新页面</span></div></div>`;
      }
    }
    render();
    App.refreshNavBadges && App.refreshNavBadges();
  };
})();
