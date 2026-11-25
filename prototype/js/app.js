(function () {
  const App = {
    noteContextId: null,
    currentRoute: 'home',
    _kbTimer: null
  };

  const routeInfo = () => {
    const raw = (location.hash || '#/home').replace(/^#\/?/, '');
    const [head, id] = raw.split('/');
    return { kind: head || 'home', id };
  };

  const PAGE_NAMES = {
    home: '首页', notes: '售后笔记', note: '售后笔记', kb: '知识库', terminal: '终端',
    repos: '代码仓库', settings: '设置', cleanup: '存量整理', pdf: 'PDF手册', allnotes: '所有笔记'
  };

  function buildNav() {
    const nav = document.getElementById('nav');
    nav.innerHTML = DATA.nav.map((n) => `<a class="nav-item" href="${n.route}" data-nav="${n.id}">
      <span class="nav-ic">${icon(n.icon, 17)}</span>
      <span class="nav-label">${n.label}</span>
      ${n.badge === 'kb' ? '<span class="nav-badge" data-badge="kb"></span>' : ''}
    </a>`).join('') + `<div class="nav-sep"></div>
      <button class="nav-item nav-util" id="navCleanup"><span class="nav-ic">${icon('scissors', 16)}</span><span class="nav-label">存量整理</span></button>
      <button class="nav-item nav-util nav-pdf" id="navPdf"><span class="nav-ic">${icon('file', 16)}</span><span class="nav-label">PDF手册</span></button>
      <button class="nav-item nav-util nav-allnotes" id="navAllNotes"><span class="nav-ic">${icon('layers', 16)}</span><span class="nav-label">所有笔记</span></button>`;
    document.getElementById('navCleanup').addEventListener('click', () => { location.hash = '#/cleanup'; });
    document.getElementById('navPdf').addEventListener('click', () => { location.hash = '#/pdf'; });
    document.getElementById('navAllNotes').addEventListener('click', () => { location.hash = '#/allnotes'; });
    refreshNavBadges();
  }

  function refreshNavBadges() {
    const processing = U.Store.state.manuals.filter((m) => ['parsing', 'queue', 'rebuilding'].includes(m.status)).length;
    document.querySelectorAll('[data-badge="kb"]').forEach((b) => {
      b.textContent = processing || '';
      b.classList.toggle('show', processing > 0);
    });
  }

  function setActiveNav(active) {
    document.querySelectorAll('.nav-item[data-nav]').forEach((n) => n.classList.toggle('active', n.dataset.nav === active));
    document.getElementById('navCleanup').classList.toggle('active', active === 'notes' && App.currentRoute === 'cleanup');
    document.getElementById('navPdf').classList.toggle('active', active === 'pdf');
    document.getElementById('navAllNotes').classList.toggle('active', active === 'allnotes');
  }

  function renderRoute() {
    const r = routeInfo();
    const kind = ['home', 'notes', 'kb', 'terminal', 'repos', 'settings', 'cleanup', 'note', 'pdf', 'allnotes'].includes(r.kind) ? r.kind : 'home';
    App.currentRoute = kind;
    const view = document.getElementById('view');
    const activeNav = kind === 'note' || kind === 'cleanup' ? 'notes' : kind;
    setActiveNav(activeNav);
    document.getElementById('pageName').textContent = PAGE_NAMES[kind] || '首页';
    document.getElementById('crumb').textContent = '售后工作台';

    if (kind !== 'note') setNoteContext(null);

    if (kind === 'home') Views.home(view);
    else if (kind === 'notes') Views.notes(view);
    else if (kind === 'note') Views.note(view, r.id);
    else if (kind === 'kb') Views.kb(view);
    else if (kind === 'pdf') Views.pdf(view);
    else if (kind === 'terminal') Views.terminal(view);
    else if (kind === 'repos') Views.repos(view);
    else if (kind === 'settings') Views.settings(view);
    else if (kind === 'cleanup') Views.cleanup(view);
    else if (kind === 'allnotes') Views.allNotes(view);
    else Views.home(view);

    document.getElementById('main').scrollTop = 0;
    refreshNavBadges();
  }

  function setNoteContext(note) {
    const id = note ? note.id : null;
    if (id === App.noteContextId) return;
    App.noteContextId = id;
    Rag.setContext(note);
  }
  App.setNoteContext = setNoteContext;

  function globalAsk(q) {
    q = (q || '').trim();
    const input = document.getElementById('globalAsk');
    if (!q) {
      Rag.open(true);
      U.toast('已展开知识助手，请在右侧输入问题', 'info');
      return;
    }
    if (Rag.state.pending) { U.toast('正在生成回答，请稍候', 'info'); return; }
    Rag.ask(q);
    if (input) input.value = '';
  }
  App.globalAsk = globalAsk;

  function newNoteModal() {
    const S = U.Store.state;
    const customers = [...new Set(S.notes.map((n) => n.customer))];
    const tpl = `## ${U.todayStr()} 新问题标题\n**现象**：\n**排查**：\n**处理**：\n**结果**：\n**待跟进**：`;
    U.modal({
      title: '新建售后笔记',
      subtitle: '直接写入 Obsidian vault 的「客户售后问题」文件夹，不建立副本。',
      width: 620,
      body: `<div class="nn-grid">
        <label class="field"><span class="field-label">客户 <em class="field-badge">frontmatter name</em></span>
          <input class="input" id="nnCustomer" list="nnCustomers" placeholder="输入或选择客户" autocomplete="off" />
          <datalist id="nnCustomers">${customers.map((c) => `<option value="${U.esc(c)}"></option>`).join('')}</datalist></label>
        <label class="field"><span class="field-label">项目名</span>
          <input class="input" id="nnTitle" placeholder="例如：2#复卷机张力异常" autocomplete="off" /></label>
        <label class="field"><span class="field-label">状态</span>
          <select class="input" id="nnStatus"><option value="active">进行中</option><option value="archived">已归档</option></select></label>
        <label class="field"><span class="field-label">开始日期</span>
          <input class="input" id="nnStart" type="date" value="${U.todayStr()}" /></label>
      </div>
      <div class="nn-template">
        <div class="nn-template-head">${icon('notes', 13)} 自动带出固定头部与日期小节模板</div>
        <div class="nn-fields-preview">
          <span>客户：<strong id="nnpCustomer">—</strong></span>
          <span>状态：<strong id="nnpStatus">进行中</strong></span>
          <span>开始：<strong>${U.todayStr()}</strong></span>
        </div>
        <pre class="nn-body-preview">${U.esc(tpl)}</pre>
      </div>
      <div class="nn-path">${icon('folder', 12)} 保存到：<code id="nnPath">客户售后问题/…</code></div>`,
      actions: [
        { label: '取消', variant: 'ghost' },
        { label: '创建并打开', variant: 'primary', onClick: (close, wrap) => {
          const customer = wrap.querySelector('#nnCustomer').value.trim();
          const title = wrap.querySelector('#nnTitle').value.trim();
          if (!customer || !title) { U.toast('客户与项目名不能为空', 'warn'); return false; }
          const start = wrap.querySelector('#nnStart').value || U.todayStr();
          const status = wrap.querySelector('#nnStatus').value;
          const safe = (title + '').replace(/[\\/:*?"<>|]/g, '-');
          const note = {
            id: U.uid('n'), customer, title, status, start, updated: U.todayStr(),
            path: `客户售后问题/${customer}-${safe}.md`,
            tags: [], summary: '新建售后记录 · 待补充处理过程',
            body: tpl
          };
          U.Store.state.notes.unshift(note);
          U.Store.save();
          U.toast('笔记已创建，将出现在 Obsidian 对应文件夹', 'success');
          location.hash = '#/note/' + note.id;
        } }
      ],
      onMount: (wrap) => {
        const upd = () => {
          const c = wrap.querySelector('#nnCustomer').value.trim() || '客户';
          const t = wrap.querySelector('#nnTitle').value.trim() || '项目名';
          const safe = t.replace(/[\\/:*?"<>|]/g, '-');
          wrap.querySelector('#nnpCustomer').textContent = c;
          wrap.querySelector('#nnPath').textContent = `客户售后问题/${c}-${safe}.md`;
          wrap.querySelector('#nnpStatus').textContent = wrap.querySelector('#nnStatus').value === 'active' ? '进行中' : '已归档';
        };
        ['#nnCustomer', '#nnTitle', '#nnStatus'].forEach((sel) => {
          wrap.querySelector(sel).addEventListener('input', upd);
          wrap.querySelector(sel).addEventListener('change', upd);
        });
      }
    });
  }
  App.newNoteModal = newNoteModal;

  function openTerminal(path, fallback) {
    const S = U.Store.state;
    let cwd = fallback || path || 'C:\\Users\\Administrator';
    if (!fallback && path) {
      if (/\.(md|pdf|txt)$/i.test(path)) cwd = (S.config.paths.vault || 'D:\\Obsidian\\售后库') + '\\' + (S.config.paths.notes || '客户售后问题');
      else cwd = path.replace(/[\\/]$/, '');
    }
    const t = {
      id: U.uid('t'), cwd, title: cwd.split(/[\\/]/).filter(Boolean).pop() || 'Terminal',
      lines: [{ t: 'sys', text: `已在 ${cwd} 打开终端 · 后台 PTY 会话` }], running: false, history: []
    };
    S.terminal.push(t);
    S.ui.termActive = t.id;
    U.Store.save();
    if (location.hash === '#/terminal') renderRoute();
    else location.hash = '#/terminal';
    U.toast('已在该目录打开终端', 'success');
  }
  App.openTerminal = openTerminal;

  function startKbEngine() {
    if (App._kbTimer) return;
    App._kbTimer = setInterval(() => {
      const S = U.Store.state;
      let changed = false;
      const active = S.manuals.filter((m) => m.status === 'parsing' || m.status === 'rebuilding');
      if (!active.length) {
        const q = S.manuals.find((m) => m.status === 'queue');
        if (q) { q.status = 'parsing'; q.progress = 2; changed = true; }
      }
      active.forEach((m) => {
        if (m.status !== 'parsing' && m.status !== 'rebuilding') return;
        m.progress = Math.min(100, (m.progress || 0) + 3 + Math.floor(Math.random() * 8));
        m.updated = U.todayStr();
        if (m.progress >= 100) {
          const wasRebuild = m.status === 'rebuilding';
          m.status = 'ready';
          m.progress = 100;
          m.chunks = Math.max(40, Math.round((m.pages || 50) * 2.4));
          changed = true;
          if (App.currentRoute === 'kb') U.toast(`${wasRebuild ? '重建完成' : '已入库'}：${m.name.replace(/\.pdf$/i, '')}`, 'success');
        } else changed = true;
      });
      if (changed) {
        U.Store.save();
        if (Views.kbRefresh) Views.kbRefresh();
        if (Views.pdfRefresh) Views.pdfRefresh();
        refreshNavBadges();
      }
    }, 1600);
  }
  App.startKbEngine = startKbEngine;
  App.refreshNavBadges = refreshNavBadges;
  App.renderRoute = renderRoute;

  function showPrivacy() {
    U.modal({
      title: '数据外传说明',
      subtitle: '已确认接受的数据边界 · 可在设置中更换模型服务',
      width: 620,
      body: `<div class="privacy-modal">
        <div class="pm-row"><div class="pm-ic">${icon('upload', 16)}</div><div><strong>索引阶段</strong>
          <p>白名单笔记与 PDF 解析后的<strong>切块内容</strong>会发送至云端 embedding 厂商，并写入向量库。</p></div></div>
        <div class="pm-row"><div class="pm-ic">${icon('message', 16)}</div><div><strong>问答阶段</strong>
          <p>检索命中的<strong>片段内容</strong>会作为上下文发送至所选 LLM 厂商，用于生成带引用的回答。</p></div></div>
        <div class="pm-row"><div class="pm-ic">${icon('shield', 16)}</div><div><strong>不外传的部分</strong>
          <p>API Key、终端输出、Git 状态、会话元数据仅保存在本机；后台只监听回环地址，不对局域网开放。</p></div></div>
        <div class="pm-row"><div class="pm-ic">${icon('settings', 16)}</div><div><strong>可替换</strong>
          <p>模型接入层可配置，后续可切换为本地自托管模型以降低外传。PDF 解析使用本地 MinerU。</p></div></div>
      </div>
      <div class="pm-foot">${icon('info', 12)} 原型仅展示界面交互，不会发起任何真实网络请求。</div>`,
      actions: [{ label: '我知道了', variant: 'primary' }]
    });
  }
  App.showPrivacy = showPrivacy;

  function showShortcuts() {
    const rows = [
      ['全局提问 / 定位输入框', 'Ctrl', 'K'],
      ['新建售后笔记', 'Ctrl', 'N'],
      ['打开终端', 'Ctrl', '`'],
      ['知识助手发送', 'Ctrl', 'Enter'],
      ['保存当前笔记', 'Ctrl', 'S'],
      ['关闭弹窗 / 阅读器', 'Esc', '']
    ];
    U.modal({
      title: '快捷键',
      subtitle: '默认快捷键 · 部分组合键可能被浏览器占用，实现时需做兼容性验证',
      width: 460,
      body: `<div class="shortcut-modal">${rows.map(([n, a, b]) => `<div class="shortcut-row"><span>${n}</span>
        <span class="keys">${a ? `<kbd>${a}</kbd>` : ''}${b ? `<kbd>${b}</kbd>` : ''}</span></div>`).join('')}</div>`,
      actions: [{ label: '关闭', variant: 'ghost' }]
    });
  }
  App.showShortcuts = showShortcuts;

  document.addEventListener('DOMContentLoaded', () => {
    U.Store.load();
    document.getElementById('askIcon').innerHTML = icon('search', 16);
    document.getElementById('icTerm').innerHTML = icon('terminal', 15);
    document.getElementById('icPlus').innerHTML = icon('plus', 15);
    document.getElementById('railIcon').innerHTML = icon('sparkle', 18);
    document.getElementById('vaultIcon').innerHTML = icon('book', 13);

    buildNav();
    Rag.init();
    startKbEngine();

    document.getElementById('btnNewNote').addEventListener('click', newNoteModal);
    document.getElementById('btnTerminalQuick').addEventListener('click', () => { location.hash = '#/terminal'; });
    document.getElementById('btnPrivacy').addEventListener('click', showPrivacy);
    document.getElementById('btnShortcuts').addEventListener('click', showShortcuts);

    const askBox = document.getElementById('askBox');
    const askInput = document.getElementById('globalAsk');
    askBox.addEventListener('click', () => askInput.focus());
    askInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); globalAsk(askInput.value); }
    });

    document.addEventListener('keydown', (e) => {
      const ctrl = e.ctrlKey || e.metaKey;
      if (ctrl && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        askInput.focus(); askInput.select();
      } else if (ctrl && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        newNoteModal();
      } else if (ctrl && e.key === '`') {
        e.preventDefault();
        location.hash = '#/terminal';
      } else if (ctrl && e.key.toLowerCase() === 's') {
        const viewEl = document.getElementById('view');
        if (viewEl && viewEl.__saveNote) {
          e.preventDefault();
          viewEl.__saveNote();
        }
      } else if (e.key === 'Escape') {
        U.closeMenu();
        if (Rag.state.scopeOpen) { Rag.state.scopeOpen = false; Rag.render(); }
      }
    });

    if (!location.hash) history.replaceState(null, '', '#/home');
    window.addEventListener('hashchange', renderRoute);
    renderRoute();
    document.getElementById('main').addEventListener('click', () => {});
  });

  window.App = App;
})();
