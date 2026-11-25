(function () {
  window.Views = window.Views || {};
  const FOLDER_ORDER = ['高创', 'ACS', 'Agito', '其他', '未分类'];
  const MANUAL_STATUS = {
    ready: { group: 'indexed', label: '已入库', cls: 'ready', ic: 'checkCircle' },
    queue: { group: 'processing', label: '排队中', cls: 'queued', ic: 'clock' },
    parsing: { group: 'processing', label: '解析中', cls: 'parsing', ic: 'refresh' },
    rebuilding: { group: 'processing', label: '重建中', cls: 'parsing', ic: 'refresh' },
    failed: { group: 'issue', label: '解析失败', cls: 'failed', ic: 'alertCircle' },
    update: { group: 'issue', label: '有更新', cls: 'update', ic: 'alert' },
    invalid: { group: 'issue', label: '路径失效', cls: 'invalid', ic: 'link' }
  };

  function relativeFolder(path, root) {
    let rel = path || '';
    if (root && rel.toLowerCase().startsWith(root.toLowerCase())) rel = rel.slice(root.length);
    rel = rel.replace(/^[\\/]+/, '');
    const i = rel.search(/[\\/]/);
    if (i > 0) return rel.slice(0, i);
    return '未分类';
  }

  function statusMeta(f) {
    if (!f.manualId) return { group: 'unindexed', label: '未入库', cls: 'unindexed', ic: 'file' };
    return MANUAL_STATUS[f.status] || MANUAL_STATUS.ready;
  }

  function statusChip(f) {
    const m = statusMeta(f);
    let label = m.label;
    if ((f.status === 'parsing' || f.status === 'rebuilding') && f.progress != null) label += ' ' + f.progress + '%';
    return `<span class="mstatus ${m.cls}">${icon(m.ic, 12)}${U.esc(label)}</span>`;
  }

  function brandBadge(b) {
    const cls = b === '高创' ? 'hk' : b === 'ACS' ? 'acs' : b === 'Agito' ? 'agito' : 'unknown';
    return `<span class="brand-badge ${cls}">${U.esc(b || '未知')}</span>`;
  }

  Views.pdf = function (el) {
    const S = U.Store.state;
    const state = { q: '', brand: 'all', status: 'all', collapsed: {}, scanning: false, scanned: '09:32', preview: null };

    const rootPath = () => (S.config.paths.pdf || '').replace(/[\\/]+$/, '');
    function allFiles() {
      const root = rootPath();
      const manuals = S.manuals.filter((m) => m.status !== 'invalid').map((m) => ({
        id: 'kb-' + m.id, manualId: m.id, name: m.name, path: m.path,
        folder: relativeFolder(m.path, root), brand: m.brand, pages: m.pages, size: m.size,
        modified: m.updated, status: m.status, progress: m.progress, manual: m
      }));
      const extras = DATA.localPdfFiles
        .filter((f) => !S.manuals.some((m) => m.name === f.name))
        .map((f) => Object.assign({}, f, { manualId: null, status: 'unindexed', progress: null }));
      return manuals.concat(extras);
    }
    function statusIn(f, value) {
      if (value === 'all') return true;
      const g = statusMeta(f).group;
      if (value === 'indexed') return g === 'indexed';
      if (value === 'unindexed') return g === 'unindexed';
      if (value === 'processing') return g === 'processing';
      if (value === 'issue') return g === 'issue';
      if (value === 'update') return f.status === 'update';
      return true;
    }
    function visibleFiles() {
      const q = state.q.trim().toLowerCase();
      return allFiles().filter((f) => {
        if (state.brand !== 'all' && f.brand !== state.brand) return false;
        if (!statusIn(f, state.status)) return false;
        if (!q) return true;
        return [f.name, f.folder, f.path, f.brand].join(' ').toLowerCase().includes(q);
      }).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
    }
    function counts(list) {
      const items = list || allFiles();
      return {
        total: items.length,
        indexed: items.filter((f) => statusMeta(f).group === 'indexed').length,
        unindexed: items.filter((f) => statusMeta(f).group === 'unindexed').length,
        processing: items.filter((f) => statusMeta(f).group === 'processing').length,
        issue: items.filter((f) => statusMeta(f).group === 'issue').length
      };
    }
    function brandCounts() {
      const files = allFiles();
      const brands = [...new Set(S.config.brands.concat('未知'))];
      return brands.map((b) => ({ brand: b, n: files.filter((f) => (f.brand || '未知') === b).length }));
    }
    function orphanManuals() { return S.manuals.filter((m) => m.status === 'invalid'); }
    function orphanHtml() {
      const orphans = orphanManuals();
      if (!orphans.length) return '';
      return `<div class="pdf-orphan">${icon('link', 14)}
        <div><strong>知识库中有 ${orphans.length} 个失效引用</strong>
          <span>${orphans.map((m) => U.esc(m.name)).join('、')} · 原文件已移动或改名，不在当前扫描结果中</span></div>
        <button class="link-btn" data-orphan-kb>去知识库重新定位 ${icon('chevronRight', 11)}</button>
      </div>`;
    }
    function statsHtml() {
      const c = counts();
      return `<div class="pdf-stat"><strong>${c.total}</strong><span>全部 PDF</span><em>本地文件夹</em></div>
        <div class="pdf-stat"><strong class="ok">${c.indexed}</strong><span>已入库</span><em>可被检索</em></div>
        <div class="pdf-stat"><strong>${c.unindexed}</strong><span>未入库</span><em>仅本地文件</em></div>
        <div class="pdf-stat"><strong class="${c.processing ? 'amber' : ''}">${c.processing}</strong><span>处理中</span><em>后台队列</em></div>
        <div class="pdf-stat"><strong class="${c.issue ? 'danger' : ''}">${c.issue}</strong><span>需处理</span><em>更新 / 失效 / 失败</em></div>`;
    }
    function brandChipsHtml() {
      const total = allFiles().length;
      return [`<button class="brand-chip ${state.brand === 'all' ? 'active' : ''} all" data-pbrand="all">全部<em>${total}</em></button>`]
        .concat(brandCounts().map((b) => `<button class="brand-chip ${state.brand === b.brand ? 'active' : ''}" data-pbrand="${U.esc(b.brand)}">${U.esc(b.brand)}<em>${b.n}</em></button>`))
        .join('');
    }
    function rowHtml(f) {
      const actions = f.status === 'invalid'
        ? `<button class="btn ghost sm" data-topdfkb>去处理</button>`
        : `<button class="btn ghost sm" data-popend>打开</button>`;
      return `<div class="pdf-row" data-pdfid="${f.id}">
        <div class="pdf-file-cell">
          <span class="pdf-file-ic">PDF</span>
          <div class="pdf-file-main">
            <strong class="pdf-file-name" title="${U.esc(f.name)}">${U.esc(f.name)}</strong>
            <span class="pdf-file-sub">${brandBadge(f.brand)}<span class="pdf-file-path" title="${U.esc(f.path)}">${U.esc(f.path)}</span></span>
          </div>
        </div>
        <div class="pdf-status-cell">${statusChip(f)}
          <em>${f.status === 'invalid' ? '原文件已移动或改名' : f.status === 'failed' ? (f.manual && f.manual.error ? U.esc(f.manual.error) : '解析失败，可重试') : f.status === 'update' ? '原文件内容已变化' : ''}</em>
        </div>
        <div class="pdf-meta-cell"><strong>${f.pages} 页</strong><span>${U.esc(f.size || '—')}</span></div>
        <div class="pdf-updated"><strong>${U.fmtDate(f.modified)}</strong><span>${U.relDays(f.modified)}</span></div>
        <div class="pdf-actions">${actions}
          ${!f.manualId ? `<button class="btn primary sm pdf-add-btn" data-pdfadd>加入知识库</button>` : ''}
          <button class="icon-btn" data-pdfmenu title="更多操作">${icon('more', 16)}</button>
        </div>
      </div>`;
    }
    function listHtml() {
      const list = visibleFiles();
      if (!list.length) {
        return `<div class="empty-block"><div class="empty-ic">${icon('folder', 26)}</div>
          <p>当前筛选下没有 PDF</p><span class="empty-sub">试试清空搜索或切换品牌 / 状态</span></div>`;
      }
      const groups = {};
      list.forEach((f) => { (groups[f.folder] = groups[f.folder] || []).push(f); });
      const order = Object.keys(groups).sort((a, b) => {
        const ia = FOLDER_ORDER.indexOf(a), ib = FOLDER_ORDER.indexOf(b);
        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
      });
      return order.map((folder) => {
        const items = groups[folder];
        const open = state.collapsed[folder] !== true;
        const issues = items.filter((f) => statusMeta(f).group === 'issue').length;
        return `<div class="pdf-folder">
          <button class="pdf-folder-head" data-pfolder="${U.esc(folder)}">
            ${icon(open ? 'chevronDown' : 'chevronRight', 14)}
            ${icon('folder', 14)}
            <strong>${U.esc(folder)}</strong>
            <span class="pdf-folder-count">${items.length} 份</span>
            ${issues ? `<span class="pdf-folder-issues">${issues} 项需处理</span>` : ''}
          </button>
          ${open ? `<div class="pdf-folder-body">${items.map(rowHtml).join('')}</div>` : ''}
        </div>`;
      }).join('');
    }

    function pageHtml() {
      const total = allFiles().length;
      return `<div class="page page-pdf">
        <section class="card pdf-hero">
          <div class="pdf-hero-top">
            <div class="pdf-hero-title">
              <span class="pdf-hero-ic">${icon('file', 19)}</span>
              <div><h2>PDF手册</h2><p>浏览本地 PDF 根目录中的所有文件 · 只读引用，不复制文件</p></div>
            </div>
            <div class="pdf-hero-actions">
              <button class="btn ghost sm" id="pdfOpenFolder">${icon('folder', 13)} 打开文件夹</button>
              <button class="btn ghost sm" id="pdfRescan">${icon('refresh', 13)} 重新扫描</button>
            </div>
          </div>
          <div class="pdf-path-row">
            <span class="pdf-root-label">扫描根目录</span>
            <span class="pdf-path-pill">${icon('folder', 12)}<code>${U.esc(rootPath())}</code>
              <button class="link-btn" id="pdfSettings">修改</button></span>
            <span class="pdf-scan-note" id="pdfScanNote">上次扫描 ${state.scanned} · ${total} 份 PDF · 仅读取文件列表</span>
          </div>
          <div class="pdf-stats" id="pdfStats">${statsHtml()}</div>
          <div id="pdfOrphanWrap">${orphanHtml()}</div>
        </section>

        <div class="pdf-workspace" id="pdfWorkspace">
        <section class="card pdf-main">
          <header class="pdf-toolbar">
            <div class="card-title"><h2>文件夹中的 PDF</h2><span class="card-sub" id="pdfCount">${total} 份</span></div>
            <div class="pdf-toolbar-right">
              <div class="search-field">${icon('search', 15)}
                <input id="pdfSearch" placeholder="搜索文件名或路径…" autocomplete="off" />
              </div>
              <div class="brand-chips" id="pdfBrands">${brandChipsHtml()}</div>
              <select class="input select sm" id="pdfStatus" aria-label="按入库状态筛选">
                <option value="all">全部状态</option>
                <option value="indexed">已入库</option>
                <option value="unindexed">未入库</option>
                <option value="processing">处理中</option>
                <option value="update">有更新</option>
                <option value="issue">需处理</option>
              </select>
            </div>
          </header>
          <div class="pdf-list-head">
            <span>文件</span><span>入库状态</span><span>页数 / 大小</span><span>修改时间</span><span>操作</span>
          </div>
          <div class="pdf-list" id="pdfList">${listHtml()}</div>
          <footer class="card-foot">
            <span>${icon('shield', 12)} 只读取文件列表并引用原路径，不复制 PDF</span>
            <span class="foot-dot"></span>
            <span>未入库文件不参与知识助手检索</span>
            <span class="foot-dot"></span>
            <button class="link-btn" id="pdfGoKb">前往知识库管理 ${icon('chevronRight', 11)}</button>
          </footer>
        </section>
        </div>
      </div>`;
    }

    function refreshDynamic() {
      if (!el.isConnected || !el.querySelector('#pdfStats')) return;
      const list = visibleFiles();
      el.querySelector('#pdfStats').innerHTML = statsHtml();
      el.querySelector('#pdfCount').textContent = `${list.length} 份` + (state.q || state.brand !== 'all' || state.status !== 'all' ? ` / 共 ${allFiles().length} 份` : '');
      el.querySelector('#pdfBrands').innerHTML = brandChipsHtml();
      el.querySelector('#pdfList').innerHTML = listHtml();
      el.querySelector('#pdfScanNote').textContent = `上次扫描 ${state.scanned} · ${allFiles().length} 份 PDF · 仅读取文件列表`;
      const ow = el.querySelector('#pdfOrphanWrap');
      if (ow) ow.innerHTML = orphanHtml();
      markPreviewRows();
      refreshPreviewChrome();
    }
    Views.pdfRefresh = refreshDynamic;

    function openFile(f) {
      if (f.status === 'invalid') {
        U.toast('原文件路径已失效，请先在知识库中重新定位', 'warn');
        return;
      }
      openPreview(f);
    }

    function addToKb(f) {
      if (S.manuals.some((m) => m.name === f.name)) {
        U.toast('该文件已在知识库中', 'info');
        return;
      }
      const manual = {
        id: U.uid('m'), name: f.name, brand: f.brand || '未知', pages: f.pages,
        status: 'queue', chunks: 0, size: f.size, path: f.path,
        added: U.todayStr(), updated: U.todayStr(), progress: 0
      };
      S.manuals.unshift(manual);
      U.Store.save();
      if (state.preview && state.preview.name === f.name) {
        state.preview.manualId = manual.id;
        state.preview.status = 'queue';
        state.preview.progress = 0;
      }
      refreshDynamic();
      if (state.preview && state.preview.manualId === manual.id) renderPreview(false);
      App.startKbEngine && App.startKbEngine();
      App.refreshNavBadges && App.refreshNavBadges();
      U.toast('已加入入库队列：' + f.name, 'success');
    }

    function openMenu(f, anchor) {
      const items = [
        { icon: 'panel', label: '在右侧预览', onClick: () => openFile(f) },
        { icon: 'expand', label: '全屏查看', onClick: () => fullscreenFile(f) }
      ];
      if (!f.manualId) {
        items.push({ icon: 'plus', label: '加入知识库', onClick: () => addToKb(f) });
      }
      items.push({ icon: 'copy', label: '复制文件路径', onClick: () => U.copy(f.path) });
      items.push({ icon: 'folder', label: '在资源管理器中打开', onClick: () => U.toast('已在资源管理器中打开所在文件夹（原型演示）', 'info') });
      if (f.manualId) {
        items.push({ divider: true });
        items.push({ icon: 'book', label: '在知识库中查看', onClick: () => { location.hash = '#/kb'; } });
      }
      if (f.status === 'invalid') {
        items.push({ icon: 'link', label: '去知识库重新定位', onClick: () => { location.hash = '#/kb'; U.toast('请在知识库中重新定位该手册', 'info'); } });
      }
      U.menu(anchor, items, { align: 'right' });
    }

    /* ---------- 右侧内嵌预览 ---------- */
    const clampPage = (n, max) => Math.max(1, Math.min(n, max || 1));
    function livePreviewFile() {
      const pv = state.preview;
      if (!pv) return null;
      if (pv.manualId) {
        const m = U.Store.manual(pv.manualId);
        if (m) return Object.assign({}, pv, {
          name: m.name, brand: m.brand, pages: m.pages, size: m.size, path: m.path,
          status: m.status, progress: m.progress
        });
      }
      return pv;
    }
    function previewPageData(file, no) {
      const map = file.manualId ? DATA.manualPages[file.manualId] : null;
      if (map && map[no]) return map[no];
      if (map) {
        const keys = Object.keys(map).map(Number);
        let best = keys[0];
        keys.forEach((k) => { if (Math.abs(k - no) < Math.abs(best - no)) best = k; });
        if (Math.abs(best - no) <= 8) {
          const p = Object.assign({}, map[best]);
          p.no = no;
          return p;
        }
      }
      return {
        no, chapter: `第 ${Math.max(1, Math.floor(no / 20))} 章 · 技术说明`,
        heading: `${file.name.replace(/\.pdf$/i, '')} · 第 ${no} 页`,
        body: [
          `本页为演示用占位内容，摘录自《${file.name}》。实际内容由 MinerU 解析后按章节切块保留。`,
          '参数设置应在低速工况下完成；修改后需执行保存并重启控制使参数生效。若出现异常报警，应先导出诊断日志再复位。',
          '相关章节的完整内容可在知识库中查看切块，或在检索结果中通过来源角标直接定位到本页。'
        ]
      };
    }
    function previewPageHtml(file) {
      const p = previewPageData(file, state.preview.page);
      const paras = (p.body || []).map((t) => `<p class="pdf-p">${U.esc(t)}</p>`).join('');
      const table = p.table ? `<table class="pdf-table"><thead><tr>${p.table.head.map((h) => `<th>${U.esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${p.table.rows.map((r) => `<tr>${r.map((c) => `<td>${U.esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '';
      return `<div class="pdf-page pdf-preview-page">
        <div class="pdf-page-head"><span>${U.esc(file.name)}</span><span>${U.esc(p.chapter.split('·')[0].trim())}</span></div>
        <div class="pdf-content">
          <div class="pdf-chapter">${U.esc(p.chapter)}</div>
          <h3 class="pdf-heading">${U.esc(p.heading)}</h3>
          ${paras}${table}
          <div class="pdf-note">${icon('info', 12)} 页面内容为原型演示，来源引用原 PDF 文件，不复制文件。</div>
        </div>
        <div class="pdf-page-foot"><span>${U.esc(file.name)}</span><span>— ${p.no} —</span></div>
      </div>`;
    }
    function previewHtml(file) {
      const pages = file.pages || 1;
      const page = clampPage(state.preview.page, pages);
      const hint = file.manualId ? statusMeta(file).label + ' · 全屏可并排' : '未入库 · 仅本地预览';
      return `<header class="pdf-preview-head">
        <span class="pdf-file-ic">PDF</span>
        <div class="pdf-preview-ident">
          <strong title="${U.esc(file.name)}">${U.esc(file.name)}</strong>
          <em>${U.esc(file.brand || '未知')} · ${pages} 页 · ${U.esc(file.size || '—')}</em>
        </div>
        <div class="pdf-preview-top-actions">
          ${!file.manualId ? `<button class="btn primary sm" data-padd>${icon('plus', 12)} 加入知识库</button>` : ''}
          <button class="btn ghost sm" data-pexpand title="在独立阅读器中全屏查看">${icon('expand', 13)} 全屏</button>
          <button class="icon-btn" data-pclose title="关闭预览">${icon('x', 16)}</button>
        </div>
      </header>
      <div class="pdf-preview-toolbar">
        <div class="reader-page-nav">
          <button class="icon-btn" data-pprev title="上一页" ${page <= 1 ? 'disabled' : ''}>${icon('left', 15)}</button>
          <input class="reader-page-input" id="pdfPreviewPage" type="number" value="${page}" min="1" max="${pages}" />
          <span class="page-total">/ ${pages}</span>
          <button class="icon-btn" data-pnext title="下一页" ${page >= pages ? 'disabled' : ''}>${icon('chevronRight', 15)}</button>
        </div>
        <span id="pdfPreviewStatus">${statusChip(file)}</span>
        <span class="pdf-preview-hint" id="pdfPreviewHint">${U.esc(hint)}</span>
      </div>
      ${!file.manualId ? `<div class="pdf-preview-local">${icon('info', 12)} 该 PDF 尚未入库，不参与知识助手检索；需要时可在右上角加入知识库。</div>` : ''}
      <div class="pdf-preview-body" id="pdfPreviewBody">${previewPageHtml(file)}</div>
      <footer class="pdf-preview-foot">
        <span>${icon('file', 11)} 引用原文件 · 只读</span>
        <span>预览模式 · 需要更宽阅读区可切换全屏</span>
      </footer>`;
    }
    function bindPreview(aside) {
      aside.querySelector('[data-pclose]').addEventListener('click', closePreview);
      aside.querySelector('[data-pexpand]').addEventListener('click', () => {
        fullscreenFile(livePreviewFile(), state.preview.page);
      });
      const addBtn = aside.querySelector('[data-padd]');
      if (addBtn) addBtn.addEventListener('click', () => addToKb(livePreviewFile()));
      aside.querySelector('[data-pprev]').addEventListener('click', () => setPreviewPage(state.preview.page - 1));
      aside.querySelector('[data-pnext]').addEventListener('click', () => setPreviewPage(state.preview.page + 1));
      aside.querySelector('#pdfPreviewPage').addEventListener('change', (e) => {
        setPreviewPage(parseInt(e.target.value, 10) || 1);
      });
    }
    function renderPreview(resetScroll) {
      const file = livePreviewFile();
      if (!file) return;
      const ws = el.querySelector('#pdfWorkspace');
      if (!ws) return;
      ws.classList.add('with-preview');
      let aside = ws.querySelector('#pdfPreview');
      if (!aside) {
        aside = document.createElement('aside');
        aside.id = 'pdfPreview';
        aside.className = 'card pdf-preview';
        ws.appendChild(aside);
      }
      aside.innerHTML = previewHtml(file);
      bindPreview(aside);
      markPreviewRows();
      if (resetScroll) requestAnimationFrame(() => {
        const body = aside.querySelector('#pdfPreviewBody');
        if (body) body.scrollTop = 0;
      });
    }
    function refreshPreviewChrome() {
      if (!state.preview) return;
      const aside = el.querySelector('#pdfPreview');
      if (!aside) return;
      const live = livePreviewFile();
      if (!live) return;
      const status = aside.querySelector('#pdfPreviewStatus');
      if (status) status.innerHTML = statusChip(live);
      const hint = aside.querySelector('#pdfPreviewHint');
      if (hint) hint.textContent = live.manualId ? statusMeta(live).label + ' · 全屏可并排' : '未入库 · 仅本地预览';
      const meta = aside.querySelector('.pdf-preview-ident em');
      if (meta) meta.textContent = `${live.brand || '未知'} · ${live.pages || 0} 页 · ${live.size || '—'}`;
      if (live.manualId) {
        aside.querySelector('[data-padd]')?.remove();
        aside.querySelector('.pdf-preview-local')?.remove();
      }
      markPreviewRows();
    }
    function markPreviewRows() {
      const name = state.preview && state.preview.name;
      el.querySelectorAll('.pdf-row').forEach((row) => {
        const title = row.querySelector('.pdf-file-name')?.getAttribute('title');
        row.classList.toggle('previewing', !!name && title === name);
      });
    }
    function openPreview(f) {
      if (f.status === 'invalid') {
        U.toast('原文件路径已失效，请先在知识库中重新定位', 'warn');
        return;
      }
      if (state.preview && state.preview.name === f.name) {
        renderPreview(false);
        return;
      }
      state.preview = Object.assign({}, f, { page: 1 });
      renderPreview(true);
      setTimeout(() => {
        const target = window.innerWidth <= 900 ? el.querySelector('#pdfPreview') : el.querySelector('#pdfWorkspace');
        target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 80);
    }
    function closePreview() {
      const ws = el.querySelector('#pdfWorkspace');
      if (ws) {
        ws.classList.remove('with-preview');
        ws.querySelector('#pdfPreview')?.remove();
      }
      state.preview = null;
      markPreviewRows();
    }
    function setPreviewPage(n) {
      const file = livePreviewFile();
      if (!file) return;
      const pages = file.pages || 1;
      state.preview.page = clampPage(n, pages);
      renderPreview(true);
    }
    function fullscreenFile(file, page) {
      if (!file) return;
      const at = page || ((state.preview && state.preview.name === file.name) ? state.preview.page : 1);
      if (file.manualId && U.Store.manual(file.manualId)) Reader.open(file.manualId, at);
      else Reader.openLocal(file, at);
    }

    function bind() {
      el.querySelector('#pdfSearch').addEventListener('input', (e) => { state.q = e.target.value; refreshDynamic(); });
      el.querySelector('#pdfStatus').addEventListener('change', (e) => { state.status = e.target.value; refreshDynamic(); });
      el.querySelector('#pdfBrands').addEventListener('click', (e) => {
        const b = e.target.closest('[data-pbrand]');
        if (!b) return;
        state.brand = b.dataset.pbrand;
        refreshDynamic();
      });
      el.querySelector('#pdfList').addEventListener('click', (e) => {
        const folder = e.target.closest('[data-pfolder]');
        if (folder) {
          const name = folder.dataset.pfolder;
          state.collapsed[name] = state.collapsed[name] !== true;
          refreshDynamic();
          return;
        }
        const menu = e.target.closest('[data-pdfmenu]');
        if (menu) {
          const f = allFiles().find((x) => x.id === menu.closest('.pdf-row').dataset.pdfid);
          if (f) openMenu(f, menu);
          return;
        }
        const add = e.target.closest('[data-pdfadd]');
        if (add) {
          const f = allFiles().find((x) => x.id === add.closest('.pdf-row').dataset.pdfid);
          if (f) addToKb(f);
          return;
        }
        if (e.target.closest('[data-topdfkb]')) {
          location.hash = '#/kb';
          U.toast('请在知识库中重新定位原文件', 'info');
          return;
        }
        const openBtn = e.target.closest('[data-popend]');
        const row = e.target.closest('.pdf-row');
        if (openBtn || row) {
          const f = allFiles().find((x) => x.id === (row && row.dataset.pdfid));
          if (f) openFile(f);
        }
      });
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-orphan-kb]')) {
          location.hash = '#/kb';
          U.toast('请在知识库中重新定位原文件', 'info');
        }
      });
      el.querySelector('#pdfOpenFolder').addEventListener('click', () => U.toast('已在资源管理器中打开 ' + rootPath() + '（原型演示）', 'info'));
      el.querySelector('#pdfSettings').addEventListener('click', () => { location.hash = '#/settings'; });
      el.querySelector('#pdfGoKb').addEventListener('click', () => { location.hash = '#/kb'; });
      el.querySelector('#pdfRescan').addEventListener('click', () => {
        if (state.scanning) return;
        state.scanning = true;
        const btn = el.querySelector('#pdfRescan');
        btn.disabled = true;
        btn.innerHTML = `${icon('refresh', 13, 'spin')} 扫描中…`;
        setTimeout(() => {
          if (!el.isConnected) return;
          state.scanning = false;
          state.scanned = new Date().toTimeString().slice(0, 5);
          refreshDynamic();
          btn.disabled = false;
          btn.innerHTML = `${icon('refresh', 13)} 重新扫描`;
          U.toast(`扫描完成 · 共 ${allFiles().length} 份 PDF`, 'success');
        }, 900);
      });
    }

    el.innerHTML = pageHtml();
    bind();
  };
})();
