(function () {
  window.Views = window.Views || {};

  const DEFAULT_SEL = { folders: ['客户售后问题'], files: [], excludedFiles: [], excludedFolders: [] };

  function getSel() {
    const cfg = U.Store.state.config;
    if (!cfg.indexSelection) cfg.indexSelection = U.clone(DEFAULT_SEL);
    const s = cfg.indexSelection;
    ['folders', 'files', 'excludedFiles', 'excludedFolders'].forEach((k) => {
      if (!Array.isArray(s[k])) s[k] = [];
    });
    return s;
  }

  function isUnder(path, parent) {
    return path === parent || path.indexOf(parent + '/') === 0;
  }

  function dirOf(path) {
    const i = path.lastIndexOf('/');
    return i < 0 ? '' : path.slice(0, i);
  }

  /* 售后笔记 + vault 中其余原有笔记，全部来自同一份 .md，不建立副本 */
  function vaultFiles() {
    const S = U.Store.state;
    const list = S.notes.map((n) => ({
      path: n.path,
      name: n.path.split('/').pop(),
      updated: n.updated,
      body: n.body,
      note: n,
      folder: dirOf(n.path)
    }));
    (DATA.vaultExtras || []).forEach((e) => {
      if (list.some((x) => x.path === e.path)) return;
      list.push({
        path: e.path,
        name: e.name || e.path.split('/').pop(),
        updated: e.updated || '',
        body: e.body || '',
        note: null,
        folder: dirOf(e.path)
      });
    });
    return list;
  }

  /* 最具体的目录规则生效：具体目录的选择/排除优先于父目录继承 */
  function folderRule(path) {
    const s = getSel();
    const d = dirOf(path);
    let rule = null;
    let len = -1;
    s.folders.forEach((f) => {
      if (isUnder(d, f) && f.length > len) { rule = 'in'; len = f.length; }
    });
    s.excludedFolders.forEach((f) => {
      if (isUnder(d, f) && f.length > len) { rule = 'out'; len = f.length; }
    });
    return rule;
  }

  function isSelected(path) {
    const s = getSel();
    if (s.excludedFiles.indexOf(path) >= 0) return false;
    if (s.files.indexOf(path) >= 0) return true;
    return folderRule(path) === 'in';
  }

  function folderStats(path, list) {
    const kids = list.filter((f) => isUnder(f.path, path));
    const n = kids.filter((f) => isSelected(f.path)).length;
    return { total: kids.length, selected: n, state: n === 0 ? 'none' : n === kids.length ? 'all' : 'some' };
  }

  function toggleFolder(path) {
    const s = getSel();
    const list = vaultFiles();
    const st = folderStats(path, list).state;
    const n = list.filter((f) => isUnder(f.path, path)).length;
    s.folders = s.folders.filter((f) => f !== path && !isUnder(f, path));
    s.excludedFolders = s.excludedFolders.filter((f) => f !== path && !isUnder(f, path));
    s.files = s.files.filter((p) => !isUnder(p, path));
    s.excludedFiles = s.excludedFiles.filter((p) => !isUnder(p, path));
    if (st === 'all') {
      s.excludedFolders.push(path);
      return { n, selected: false };
    }
    s.folders.push(path);
    return { n, selected: true };
  }

  function toggleFile(path) {
    const s = getSel();
    if (isSelected(path)) {
      s.files = s.files.filter((p) => p !== path);
      if (isSelected(path) && s.excludedFiles.indexOf(path) < 0) s.excludedFiles.push(path);
      return { n: 1, selected: false };
    }
    s.excludedFiles = s.excludedFiles.filter((p) => p !== path);
    if (s.files.indexOf(path) < 0) s.files.push(path);
    return { n: 1, selected: true };
  }

  function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  function highlight(text, q) {
    const safe = U.esc(text);
    try {
      return safe.replace(new RegExp(escRe(U.esc(q)), 'ig'), (m) => `<mark>${m}</mark>`);
    } catch (e) { return safe; }
  }

  /* ---------------- 所有笔记 ---------------- */
  Views.allNotes = function (el) {
    const state = { q: '', filter: 'all', collapsed: new Set(), currentPath: null };
    const files = vaultFiles;

    el.innerHTML = `<div class="page page-allnotes">
      <section class="card allnotes-shell">
        <aside class="an-tree">
          <div class="an-tree-head">
            <div class="an-tree-title"><h2>所有笔记</h2><span class="count-pill" id="anTotal">0</span></div>
            <div class="an-tree-sub">${icon('database', 12)} 直接读取 Obsidian vault · 勾选即知识库范围</div>
            <div class="search-field an-search">${icon('search', 14)}
              <input id="anSearch" placeholder="搜索标题或正文…" autocomplete="off" /></div>
            <div class="seg sm an-filter" id="anFilter">
              <button class="seg-btn active" data-filter="all">全部</button>
              <button class="seg-btn" data-filter="selected">已勾选</button>
              <button class="seg-btn" data-filter="unselected">未勾选</button>
            </div>
          </div>
          <div class="an-tree-scroll" id="anTree"></div>
          <div class="an-tree-foot" id="anFoot"></div>
        </aside>
        <main class="an-reader" id="anReader"></main>
      </section>
    </div>`;

    const treeEl = el.querySelector('#anTree');
    const readerEl = el.querySelector('#anReader');
    let searchTimer;

    function refreshFoot() {
      const total = files().length;
      const on = files().filter((f) => isSelected(f.path)).length;
      el.querySelector('#anTotal').textContent = total;
      el.querySelector('#anFoot').innerHTML =
        `<span class="an-foot-dot"></span>已选 <strong>${on}</strong> / ${total} 篇 · 保存后 ${U.Store.state.config.indexDelay} 秒增量索引`;
    }

    function overviewHtml() {
      const total = files().length;
      const on = files().filter((f) => isSelected(f.path)).length;
      const s = getSel();
      const srcFolders = s.folders.filter((f) => files().some((x) => isUnder(x.path, f)));
      const srcFiles = s.files.filter((p) => files().some((f) => f.path === p));
      const chips = srcFolders.map((f) => {
        const n = files().filter((x) => isUnder(x.path, f)).length;
        return `<span class="an-src-chip">${icon('folder', 12)} ${U.esc(f)} <em>${n} 篇</em></span>`;
      }).concat(srcFiles.map((p) => `<span class="an-src-chip">${icon('file', 12)} ${U.esc(p.split('/').pop())}</span>`));
      const dirs = new Set(files().map((f) => f.folder)).size;
      return `<div class="an-overview">
        <div class="an-ov-ic">${icon('book', 26)}</div>
        <h2>浏览 Obsidian 中的每一篇笔记</h2>
        <p>左侧按 vault 原目录呈现全部 Markdown 笔记。勾选文件夹或单篇笔记，就是把它们纳入知识库；未勾选的笔记仍可在这里阅读。</p>
        <div class="an-stats">
          <div class="an-stat"><strong>${total}</strong><span>笔记总数</span></div>
          <div class="an-stat"><strong class="ok">${on}</strong><span>已纳入知识库</span></div>
          <div class="an-stat"><strong>${dirs}</strong><span>所在目录</span></div>
          <div class="an-stat"><strong>${U.Store.state.config.indexDelay}s</strong><span>增量索引延迟</span></div>
        </div>
        <div class="an-src">
          <h3>当前知识库来源</h3>
          <div class="an-src-chips">${chips.join('') || '<span class="muted">尚未勾选任何目录</span>'}</div>
        </div>
        <div class="an-overview-note">${icon('info', 12)} 默认仅勾选「客户售后问题」。其余目录按需勾选，勾选前不会上传任何内容。</div>
      </div>`;
    }

  /* 正文首行若为「# 与文件名同名」的 H1，则去掉，避免与阅读器大标题重复 */
  function readerBody(body, name) {
    if (!body) return U.md('（空笔记）');
    const stripped = String(name).replace(/\.md$/i, '').trim();
    const lines = body.replace(/\r\n/g, '\n').split('\n');
    const m = (lines[0] || '').trim().match(/^#\s+(.+)$/);
    if (m && m[1].trim() === stripped) {
      lines.shift();
      while (lines.length && !lines[0].trim()) lines.shift();
    }
    return U.md(lines.join('\n'));
  }

  function readerHtml(f) {
    const on = isSelected(f.path);
    const chars = (f.body || '').replace(/\s/g, '').length;
    const n = f.note;
    const extra = n
      ? `<span>客户 ${U.esc(n.customer)}</span><span class="sep-dot"></span><span>${n.status === 'active' ? '进行中' : '已归档'}</span><span class="sep-dot"></span>`
      : '';
    return `<div class="an-reader-inner">
      <header class="an-reader-head">
          <div class="an-reader-crumb">${icon('folder', 12)} ${U.esc(f.folder || 'vault 根目录')} ${icon('chevronRight', 11)} <span>${U.esc(f.name)}</span></div>
          <h1 class="an-reader-title">${U.esc(f.name.replace(/\.md$/i, ''))}</h1>
          <div class="an-reader-meta">
            <span class="badge ${on ? 'ok-soft' : 'muted'}" id="anIndexBadge">${icon(on ? 'checkCircle' : 'eyeOff', 12)} ${on ? '已纳入知识库' : '未纳入知识库'}</span>
            ${extra}
            <span>${U.fullDate(f.updated)} 修改</span>
            <span class="sep-dot"></span>
            <span>${chars} 字</span>
            <button class="btn ghost sm an-open-ext" id="anOpenExt">${icon('external', 13)} 在 Obsidian 中打开</button>
          </div>
        </header>
        <div class="an-content md-body">${readerBody(f.body, f.name)}</div>
        <footer class="an-reader-foot">
          <span class="an-path">${icon('file', 12)} ${U.esc(U.Store.state.config.paths.vault + '\\' + f.path)}</span>
          <span class="sep-dot"></span>
          <span>只读预览 · 编辑请在 Obsidian 或「售后笔记」页完成</span>
        </footer>
      </div>`;
    }

    function bindReader() {
      const ext = readerEl.querySelector('#anOpenExt');
      if (ext) ext.addEventListener('click', () => U.toast('原型演示：将调用系统默认程序打开该笔记', 'info'));
    }

    function renderReader() {
      const f = state.currentPath ? files().find((x) => x.path === state.currentPath) : null;
      if (state.currentPath && !f) state.currentPath = null;
      readerEl.innerHTML = f ? readerHtml(f) : overviewHtml();
      bindReader();
    }

    function buildTree(list) {
      const root = { type: 'folder', path: '', name: '', children: [] };
      list.forEach((f) => {
        const parts = f.path.split('/');
        let node = root;
        let acc = '';
        for (let i = 0; i < parts.length - 1; i++) {
          acc = acc ? acc + '/' + parts[i] : parts[i];
          let child = node.children.find((c) => c.type === 'folder' && c.path === acc);
          if (!child) {
            child = { type: 'folder', path: acc, name: parts[i], children: [] };
            node.children.push(child);
          }
          node = child;
        }
        node.children.push({ type: 'file', path: f.path, name: f.name, file: f });
      });
      const sortNode = (n) => {
        n.children.sort((a, b) => (a.type === b.type
          ? a.name.localeCompare(b.name, 'zh-CN')
          : a.type === 'folder' ? -1 : 1));
        n.children.forEach((c) => { if (c.type === 'folder') sortNode(c); });
      };
      sortNode(root);
      return root;
    }

    function pruneNode(node) {
      if (node.type === 'file') {
        const on = isSelected(node.path);
        if (state.filter === 'selected' && !on) return null;
        if (state.filter === 'unselected' && on) return null;
        return Object.assign({}, node);
      }
      const kids = node.children.map(pruneNode).filter(Boolean);
      if (!kids.length) return null;
      return { type: 'folder', path: node.path, name: node.name, children: kids };
    }

    function checkHtml(path, type, st) {
      const attr = type === 'folder' ? `data-check-folder="${U.esc(path)}"` : `data-check-file="${U.esc(path)}"`;
      const title = st === 'all' ? '移出知识库' : st === 'some' ? '全部纳入知识库' : '纳入知识库';
      return `<button class="an-check ${st}" ${attr} title="${title}" aria-pressed="${st === 'all'}">${st === 'all' ? icon('check', 11) : ''}</button>`;
    }

    function treeHtml(node, depth) {
      return node.children.map((c) => {
        if (c.type === 'folder') {
          const st = folderStats(c.path, files());
          const open = !state.collapsed.has(c.path) || !!state.q || state.filter !== 'all';
          return `<div class="an-row folder" data-folder="${U.esc(c.path)}" style="padding-left:${8 + depth * 13}px">
            <button class="an-caret" data-caret="${U.esc(c.path)}" tabindex="-1" aria-label="展开或折叠">${icon(open ? 'chevronDown' : 'chevronRight', 13)}</button>
            ${checkHtml(c.path, 'folder', st.state)}
            <span class="an-fic">${icon('folder', 14)}</span>
            <span class="an-name">${U.esc(c.name)}</span>
            <span class="an-count ${st.selected ? 'on' : ''}">${st.selected}/${st.total}</span>
          </div>` + (open ? treeHtml(c, depth + 1) : '');
        }
        const on = isSelected(c.path);
        return `<div class="an-row file ${state.currentPath === c.path ? 'active' : ''}" data-open="${U.esc(c.path)}" style="padding-left:${8 + depth * 13 + 20}px">
          <span class="an-gap"></span>
          ${checkHtml(c.path, 'file', on ? 'all' : 'none')}
          <span class="an-fic">${icon('file', 13)}</span>
          <span class="an-name">${U.esc(c.name)}</span>
          <span class="an-date">${U.fmtDate(c.file.updated)}</span>
        </div>`;
      }).join('');
    }

    function searchHtml(q) {
      const ql = q.toLowerCase();
      const hits = files()
        .filter((f) => f.name.toLowerCase().indexOf(ql) >= 0 || (f.body || '').toLowerCase().indexOf(ql) >= 0)
        .sort((a, b) => (b.updated || '').localeCompare(a.updated || ''));
      if (!hits.length) {
        return `<div class="empty-block"><div class="empty-ic">${icon('search', 24)}</div>
          <p>没有匹配的笔记</p><span class="empty-sub">试试更短的关键词</span></div>`;
      }
      const rows = hits.map((f) => {
        const on = isSelected(f.path);
        const src = (f.body || '').replace(/\n+/g, ' ');
        const i = src.toLowerCase().indexOf(ql);
        let snippet = src.slice(0, 120);
        if (i >= 0) snippet = (i > 24 ? '…' : '') + src.slice(Math.max(0, i - 24), i + ql.length + 76) + '…';
        return `<div class="an-result" data-open="${U.esc(f.path)}">
          ${checkHtml(f.path, 'file', on ? 'all' : 'none')}
          <div class="an-result-main">
            <div class="an-result-title"><strong>${U.esc(f.name.replace(/\.md$/i, ''))}</strong>
              <span class="an-result-path">${U.esc(f.folder)}</span></div>
            <p class="an-result-snip">${highlight(snippet, q)}</p>
          </div>
        </div>`;
      }).join('');
      return `<div class="an-results-head">${hits.length} 篇匹配 · 标题或正文</div><div class="an-results">${rows}</div>`;
    }

    function renderTree() {
      if (state.q.trim()) {
        treeEl.innerHTML = searchHtml(state.q.trim());
        return;
      }
      const pruned = pruneNode(buildTree(files()));
      treeEl.innerHTML = pruned
        ? treeHtml(pruned, 0)
        : `<div class="empty-block"><div class="empty-ic">${icon('folder', 24)}</div><p>没有符合条件的笔记</p></div>`;
    }

    function afterToggle(result, label) {
      U.Store.save();
      refreshFoot();
      renderTree();
      const keep = readerEl.scrollTop;
      renderReader();
      readerEl.scrollTop = keep;
      U.toast(result.selected
        ? `已纳入知识库：${label}（${result.n} 篇）· ${U.Store.state.config.indexDelay} 秒后增量索引`
        : `已移出知识库：${label}（${result.n} 篇），对应索引将清理`,
      result.selected ? 'success' : 'info');
    }

    function openFile(path) {
      state.currentPath = path;
      renderReader();
      readerEl.scrollTop = 0;
      treeEl.querySelectorAll('.an-row.file').forEach((r) => r.classList.toggle('active', r.dataset.open === path));
    }

    treeEl.addEventListener('click', (e) => {
      const chk = e.target.closest('.an-check');
      if (chk) {
        e.preventDefault();
        e.stopPropagation();
        if (chk.dataset.checkFolder != null) {
          const p = chk.dataset.checkFolder;
          afterToggle(toggleFolder(p), p);
        } else if (chk.dataset.checkFile != null) {
          const p = chk.dataset.checkFile;
          afterToggle(toggleFile(p), p.split('/').pop());
        }
        return;
      }
      const caret = e.target.closest('.an-caret');
      if (caret) {
        e.stopPropagation();
        const p = caret.dataset.caret;
        if (state.collapsed.has(p)) state.collapsed.delete(p); else state.collapsed.add(p);
        renderTree();
        return;
      }
      const folderRow = e.target.closest('.an-row.folder');
      if (folderRow) {
        const p = folderRow.dataset.folder;
        if (state.collapsed.has(p)) state.collapsed.delete(p); else state.collapsed.add(p);
        renderTree();
        return;
      }
      const openRow = e.target.closest('[data-open]');
      if (openRow) openFile(openRow.dataset.open);
    });

    el.querySelector('#anSearch').addEventListener('input', (e) => {
      state.q = e.target.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderTree, 120);
    });

    el.querySelectorAll('#anFilter [data-filter]').forEach((b) => b.addEventListener('click', () => {
      state.filter = b.dataset.filter;
      el.querySelectorAll('#anFilter [data-filter]').forEach((x) => x.classList.toggle('active', x === b));
      renderTree();
    }));

    refreshFoot();
    renderTree();
    renderReader();
  };
})();
