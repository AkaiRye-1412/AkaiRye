(function () {
  window.Views = window.Views || {};

  /* ---------------- 笔记列表 ---------------- */
  Views.notes = function (el) {
    const state = { q: '', filter: 'all' };
    el.innerHTML = `<div class="page page-notes">
      <section class="card notes-card">
        <header class="card-head notes-head">
          <div class="card-title"><h2>售后笔记</h2><span class="count-pill" id="noteCount"></span></div>
          <div class="notes-tools">
            <div class="search-field">
              ${icon('search', 15)}
              <input id="noteSearch" placeholder="搜索项目、客户或标签…" autocomplete="off" />
            </div>
            <div class="seg sm">
              <button class="seg-btn active" data-nfilter="all">全部</button>
              <button class="seg-btn" data-nfilter="active">进行中</button>
              <button class="seg-btn" data-nfilter="archived">已归档</button>
            </div>
            <button class="btn ghost sm" id="btnCleanup">${icon('scissors', 14)} 存量整理</button>
            <button class="btn primary sm" id="btnNewNote2">${icon('plus', 14)} 新建笔记</button>
          </div>
        </header>
        <div class="notes-table-head">
          <span>项目 / 客户</span><span>状态</span><span>开始日期</span><span>最近更新</span><span>标签</span><span></span>
        </div>
        <div class="notes-list" id="notesList"></div>
        <footer class="card-foot"><span>${icon('database', 13)} 数据直接读写 Obsidian vault · 不建立副本</span>
          <span class="foot-dot"></span><span>点击任意行打开笔记</span></footer>
      </section>
    </div>`;

    function renderList() {
      const notes = U.Store.state.notes.filter((n) => {
        if (state.filter !== 'all' && n.status !== state.filter) return false;
        const q = state.q.trim().toLowerCase();
        if (!q) return true;
        return [n.title, n.customer, n.tags.join(' '), n.summary || ''].join(' ').toLowerCase().includes(q);
      }).sort((a, b) => b.updated.localeCompare(a.updated));
      const box = el.querySelector('#notesList');
      el.querySelector('#noteCount').textContent = notes.length;
      if (!notes.length) {
        box.innerHTML = `<div class="empty-block"><div class="empty-ic">${icon('search', 26)}</div>
          <p>没有匹配的笔记</p><span class="empty-sub">试试更换关键词或筛选条件</span></div>`;
        return;
      }
      const groups = [];
      notes.forEach((n) => {
        let g = groups.find((x) => x.customer === n.customer);
        if (!g) { g = { customer: n.customer, notes: [] }; groups.push(g); }
        g.notes.push(n);
      });
      box.innerHTML = groups.map((g) => `
        <div class="notes-group-label">${icon('folder', 13)} ${U.esc(g.customer)} <span>${g.notes.length}</span></div>
        ${g.notes.map((n) => {
          const days = U.daysSince(n.updated);
          return `<div class="note-row" data-note="${n.id}">
            <div class="nr-main">
              <span class="nr-title">${U.esc(n.title)}</span>
              <span class="nr-summary">${U.esc(n.summary || '')}</span>
            </div>
            <div class="nr-status">${n.status === 'active'
              ? `<span class="badge ${days > U.Store.state.config.followUpDays ? 'warn' : 'ok-soft'}">${days > U.Store.state.config.followUpDays ? '待跟进' : '进行中'}</span>`
              : '<span class="badge muted">已归档</span>'}</div>
            <div class="nr-date">${U.fmtDate(n.start)}</div>
            <div class="nr-date"><strong>${U.fmtDate(n.updated)}</strong><em>${U.relDays(n.updated)}</em></div>
            <div class="nr-tags">${n.tags.slice(0, 2).map((t) => `<span class="tag">${U.esc(t)}</span>`).join('')}</div>
            <div class="nr-go">${icon('chevronRight', 15)}</div>
          </div>`;
        }).join('')}`).join('');
      box.querySelectorAll('[data-note]').forEach((row) => row.addEventListener('click', () => { location.hash = '#/note/' + row.dataset.note; }));
    }

    let t;
    el.querySelector('#noteSearch').addEventListener('input', (e) => {
      state.q = e.target.value;
      clearTimeout(t); t = setTimeout(renderList, 120);
    });
    el.querySelectorAll('[data-nfilter]').forEach((b) => b.addEventListener('click', () => {
      state.filter = b.dataset.nfilter;
      el.querySelectorAll('[data-nfilter]').forEach((x) => x.classList.toggle('active', x === b));
      renderList();
    }));
    el.querySelector('#btnCleanup').addEventListener('click', () => { location.hash = '#/cleanup'; });
    el.querySelector('#btnNewNote2').addEventListener('click', () => App.newNoteModal());
    renderList();
  };

  /* ---------------- 笔记编辑器 ---------------- */
  Views.note = function (el, id) {
    const S = U.Store.state;
    const note = U.Store.note(id);
    if (!note) {
      el.innerHTML = `<div class="page"><div class="empty-block"><p>笔记不存在或已被删除</p>
        <button class="btn ghost" onclick="location.hash='#/notes'">返回笔记列表</button></div></div>`;
      return;
    }
    App.setNoteContext(note);
    const threshold = S.config.followUpDays;
    const view = { mode: 'split', saveState: 'saved', dirty: false };
    const initial = { body: note.body, customer: note.customer, title: note.title, status: note.status, start: note.start };

    el.innerHTML = `<div class="page page-note">
      <div class="note-topbar">
        <div class="note-crumb">
          <button class="icon-btn" data-back title="返回笔记列表">${icon('arrowLeft', 18)}</button>
          <span class="crumb-note">客户售后问题</span>${icon('chevronRight', 12)}
          <span class="crumb-note">${U.esc(note.customer)}</span>${icon('chevronRight', 12)}
          <span class="crumb-current">${U.esc(note.title)}</span>
        </div>
        <div class="note-actions">
          <span class="save-status" id="saveStatus"><span class="dot"></span>已保存</span>
          <span class="index-chip" id="indexChip">${icon('checkCircle', 13)} 已索引</span>
          <button class="btn ghost sm" id="btnNoteMore">${icon('more', 15)}</button>
          <button class="btn sm ${note.status === 'active' ? 'ghost' : 'primary'}" id="btnArchive">
            ${icon('archive', 14)} ${note.status === 'active' ? '归档' : '取消归档'}
          </button>
        </div>
      </div>

      <section class="note-fields card">
        <div class="nf-head"><span class="nf-title">${icon('notes', 15)} 头部字段</span>
          <span class="nf-hint">frontmatter 中固定字段，用于看板与检索</span></div>
        <div class="nf-grid">
          <label class="nf-field wide"><span>客户</span><input class="input" data-field="customer" value="${U.esc(note.customer)}" /></label>
          <label class="nf-field wide"><span>项目名</span><input class="input" data-field="title" value="${U.esc(note.title)}" /></label>
          <label class="nf-field"><span>状态</span><select class="input" data-field="status">
            <option value="active" ${note.status === 'active' ? 'selected' : ''}>进行中</option>
            <option value="archived" ${note.status === 'archived' ? 'selected' : ''}>已归档</option>
          </select></label>
          <label class="nf-field"><span>开始日期</span><input class="input" type="date" data-field="start" value="${note.start}" /></label>
          <label class="nf-field"><span>最近更新</span><span class="nf-readonly" id="nfUpdated">${note.updated}</span></label>
        </div>
      </section>

      <section class="editor-card card">
        <div class="editor-toolbar">
          <div class="seg sm" id="viewMode">
            <button class="seg-btn" data-mode="edit">${icon('edit', 13)} 编辑</button>
            <button class="seg-btn active" data-mode="split">${icon('columns', 13)} 分栏</button>
            <button class="seg-btn" data-mode="preview">${icon('eye', 13)} 预览</button>
          </div>
          <div class="editor-tools">
            <button class="btn ghost sm" id="btnInsertSection">${icon('plus', 13)} 插入今日小节</button>
            <button class="btn ghost sm" id="btnOpenObsidian">${icon('external', 13)} 在 Obsidian 中打开</button>
          </div>
          <div class="editor-meta"><span id="wordCount"></span></div>
        </div>
        <div class="editor-area mode-split" id="editorArea">
          <div class="editor-edit">
            <div class="editor-pane-label">Markdown · 直接编辑同一份 .md</div>
            <textarea id="noteEditor" spellcheck="false">${U.esc(note.body)}</textarea>
          </div>
          <div class="editor-preview-wrap">
            <div class="editor-pane-label">预览</div>
            <div class="editor-preview md-body" id="preview">${U.md(note.body)}</div>
          </div>
        </div>
        <footer class="editor-foot">
          <span class="editor-path" id="editorPath">${icon('file', 12)} ${U.esc(note.path)}</span>
          <button class="link-btn" id="btnCopyPath">复制路径</button>
          <span class="editor-foot-right">编辑后自动保存 · 8 秒后增量索引</span>
        </footer>
      </section>
    </div>`;

    /* ---- 编辑器状态与自动保存 ---- */
    const editor = el.querySelector('#noteEditor');
    const preview = el.querySelector('#preview');
    const area = el.querySelector('#editorArea');
    const saveEl = el.querySelector('#saveStatus');
    const chip = el.querySelector('#indexChip');
    let saveTimer = null;
    let indexTimer = null;
    let saving = false;

    const setSaveState = (text, cls) => {
      saveEl.className = 'save-status ' + (cls || '');
      saveEl.innerHTML = `<span class="dot"></span>${U.esc(text)}`;
    };
    const setChip = (cls, ic, text) => {
      chip.className = 'index-chip ' + cls;
      chip.innerHTML = `${icon(ic, 13)} ${U.esc(text)}`;
    };
    const countWords = () => {
      const chars = editor.value.replace(/\s/g, '').length;
      const lines = editor.value.split('\n').length;
      el.querySelector('#wordCount').textContent = `${chars} 字 · ${lines} 行`;
    };
    const scheduleIndex = () => {
      clearInterval(indexTimer);
      let n = S.config.indexDelay;
      setChip('pending', 'clock', `等待索引 · ${n}s`);
      indexTimer = setInterval(() => {
        if (!chip.isConnected) { clearInterval(indexTimer); return; }
        n -= 1;
        if (n <= 0) {
          clearInterval(indexTimer);
          setChip('done', 'checkCircle', '已索引');
          App.refreshNavBadges && App.refreshNavBadges();
        } else {
          setChip('pending', 'clock', `等待索引 · ${n}s`);
        }
      }, 1000);
    };
    const persist = (announce) => {
      if (!view.dirty) return;
      setSaveState('保存中…', 'saving');
      saving = true;
      setTimeout(() => {
        note.body = editor.value;
        note.updated = U.todayStr();
        note.customer = el.querySelector('[data-field="customer"]').value.trim() || note.customer;
        note.title = el.querySelector('[data-field="title"]').value.trim() || note.title;
        note.status = el.querySelector('[data-field="status"]').value;
        note.start = el.querySelector('[data-field="start"]').value || note.start;
        S.noteDrafts[id] = null;
        U.Store.save();
        view.dirty = false;
        saving = false;
        const now = new Date();
        setSaveState('已保存 ' + now.toTimeString().slice(0, 5), 'saved');
        el.querySelector('#nfUpdated').textContent = note.updated;
        scheduleIndex();
        if (announce) U.toast('笔记已写入 vault', 'success');
      }, 320);
    };
    const kickSave = () => {
      view.dirty = true;
      setSaveState('未保存', 'dirty');
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => persist(false), 1200);
    };
    const syncFromEditor = () => {
      preview.innerHTML = U.md(editor.value);
      countWords();
    };

    editor.addEventListener('input', () => { syncFromEditor(); kickSave(); });
    countWords();

    el.querySelectorAll('[data-field]').forEach((f) => {
      f.addEventListener('change', () => {
        kickSave();
        if (f.dataset.field === 'status') { note.status = f.value; refreshArchiveBtn(); }
        if (f.dataset.field === 'title') el.querySelector('.crumb-current').textContent = f.value;
        if (f.dataset.field === 'customer') el.querySelectorAll('.crumb-note')[1].textContent = f.value;
      });
      if (f.tagName === 'INPUT') f.addEventListener('input', () => kickSave());
    });

    const refreshArchiveBtn = () => {
      const b = el.querySelector('#btnArchive');
      b.className = 'btn sm ' + (note.status === 'active' ? 'ghost' : 'primary');
      b.innerHTML = `${icon('archive', 14)} ${note.status === 'active' ? '归档' : '取消归档'}`;
    };

    el.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => {
      view.mode = b.dataset.mode;
      area.className = 'editor-area mode-' + view.mode;
      el.querySelectorAll('[data-mode]').forEach((x) => x.classList.toggle('active', x === b));
      if (view.mode !== 'preview') editor.focus();
    }));

    el.querySelector('#btnInsertSection').addEventListener('click', () => {
      const tpl = `## ${U.todayStr()} 新问题标题\n**现象**：\n**排查**：\n**处理**：\n**结果**：\n**待跟进**：`;
      editor.value = tpl + '\n\n' + editor.value.replace(/^\n+/, '');
      syncFromEditor();
      kickSave();
      if (view.mode === 'preview') el.querySelector('[data-mode="split"]').click();
      editor.focus();
      editor.setSelectionRange(3, 3 + 10);
      U.toast('已插入今日小节模板', 'success');
    });

    el.querySelector('#btnOpenObsidian').addEventListener('click', () => {
      U.toast('已在 Obsidian 中打开该笔记（原型演示）', 'info');
    });
    el.querySelector('#btnCopyPath').addEventListener('click', () => U.copy(note.path));
    el.querySelector('#editorPath').addEventListener('click', () => U.copy(note.path));

    el.querySelector('#btnArchive').addEventListener('click', async () => {
      if (note.status === 'active') {
        const ok = await U.confirm({
          title: '归档这个项目？',
          message: '归档后项目将从「进行中」列表移入「已归档」，不自动改变任何历史记录。可随时取消归档。',
          confirmText: '归档项目'
        });
        if (!ok) return;
        note.status = 'archived';
        U.toast('项目已归档', 'success');
      } else {
        note.status = 'active';
        U.toast('项目已恢复为进行中', 'success');
      }
      el.querySelector('[data-field="status"]').value = note.status;
      refreshArchiveBtn();
      kickSave();
      persist(false);
    });

    el.querySelector('#btnNoteMore').addEventListener('click', (e) => {
      U.menu(e.currentTarget, [
        { icon: 'alert', label: '模拟外部修改冲突', meta: '演示', onClick: () => showConflict() },
        { icon: 'external', label: '在 Obsidian 中打开', onClick: () => U.toast('已在 Obsidian 中打开该笔记（原型演示）', 'info') },
        { icon: 'copy', label: '复制文件路径', onClick: () => U.copy(note.path) },
        { divider: true },
        { icon: 'refresh', label: '重新扫描头部字段', onClick: () => U.toast('已重新解析 frontmatter 字段', 'success') }
      ], { align: 'right' });
    });

    el.querySelector('[data-back]').addEventListener('click', () => {
      clearTimeout(saveTimer);
      persist(false);
      location.hash = '#/notes';
    });

    el.__saveNote = () => {
      if (!view.dirty) { U.toast('没有需要保存的修改', 'info'); return; }
      clearTimeout(saveTimer);
      persist(true);
    };

    function syncEditorBody(body) {
      editor.value = body;
      preview.innerHTML = U.md(body);
      countWords();
      view.dirty = false;
      setSaveState('已保存 ' + new Date().toTimeString().slice(0, 5), 'saved');
    }

    function showConflict() {
      const external = note.body + `\n\n## ${U.todayStr()} Obsidian 外部补充\n**回访**：客户反馈高速段已连续运行两天，张力稳定；补一张波形截图。\n**结论**：可进入观察期，一周后再确认。`;
      U.modal({
        title: '检测到文件冲突',
        subtitle: '该笔记在 Obsidian 中被外部修改，工作台中有尚未保存的内容。请选择处理方式。',
        width: 860,
        body: `<div class="conflict-tip">${icon('alert', 16)} 禁止静默覆盖：文件指纹不匹配，需要你明确选择。</div>
          <div class="conflict-grid">
            <div class="conflict-pane"><h4>工作台中的未保存内容</h4><pre>${U.esc(editor.value)}</pre></div>
            <div class="conflict-pane external"><h4>Obsidian 中的最新内容</h4><pre>${U.esc(external)}</pre></div>
          </div>`,
        actions: [
          { label: '重新加载外部版本', variant: 'ghost', onClick: () => {
            note.body = external; note.updated = U.todayStr();
            U.Store.save();
            syncEditorBody(external);
            el.querySelector('#nfUpdated').textContent = note.updated;
            U.toast('已重新加载 Obsidian 中的最新版本', 'success');
            scheduleIndex();
          } },
          { label: '用我的覆盖', variant: 'primary', onClick: () => {
            note.body = editor.value; note.updated = U.todayStr();
            U.Store.save();
            view.dirty = false;
            setSaveState('已保存 ' + new Date().toTimeString().slice(0, 5), 'saved');
            el.querySelector('#nfUpdated').textContent = note.updated;
            U.toast('已用工作台内容覆盖文件', 'success');
            scheduleIndex();
          } },
          { label: '另存副本', variant: 'ghost', onClick: () => {
            const copy = Object.assign({}, note, {
              id: U.uid('n'), title: note.title + '（副本）', updated: U.todayStr(),
              path: note.path.replace(/\.md$/, '') + '-副本.md'
            });
            U.Store.state.notes.unshift(copy);
            U.Store.save();
            U.toast('已另存为副本，原文件保持外部版本', 'success', {
              action: '打开副本', onAction: () => { location.hash = '#/note/' + copy.id; }
            });
          } }
        ]
      });
    }
  };
})();
