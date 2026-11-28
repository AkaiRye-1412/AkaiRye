(function () {
  window.Views = window.Views || {};

  /* ================= 终端 ================= */
  const BUILTIN = [
    { cmd: '/help', desc: '查看工作台内置命令' },
    { cmd: '/clear', desc: '清空当前终端输出' },
    { cmd: '/note', desc: '打开新建售后笔记' },
    { cmd: '/rag <问题>', desc: '唤起知识助手并提问' },
    { cmd: '/kb', desc: '跳转到知识库' }
  ];
  const basename = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;
  const nowTime = () => new Date().toTimeString().slice(0, 5);

  Views.terminal = function (el) {
    const S = U.Store.state;
    if (!Array.isArray(S.terminal)) S.terminal = [];
    if (!S.terminal.length) {
      S.terminal = [{ id: U.uid('t'), cwd: 'C:\\Users\\Administrator', title: 'Users',
        lines: [{ t: 'sys', text: '工作台终端 · 后台 PTY 会话（原型模拟）' }], running: false, history: [] }];
    }
    let activeId = S.ui.termActive;
    if (!S.terminal.some((t) => t.id === activeId)) activeId = S.terminal[0].id;
    S.ui.termActive = activeId;

    function tab() {
      let t = S.terminal.find((x) => x.id === activeId);
      if (!t) { t = S.terminal[0]; activeId = t.id; }
      return t;
    }

    function push(t, item) {
      t.lines.push(item);
      if (t.lines.length > 400) t.lines = t.lines.slice(-400);
    }

    function addTab(cwd, intro) {
      const t = {
        id: U.uid('t'), cwd: cwd || 'C:\\Users\\Administrator', title: basename(cwd || 'Users'),
        lines: intro ? [{ t: 'sys', text: intro }] : [{ t: 'sys', text: `新终端 · ${cwd || 'C:\\Users\\Administrator'}（本机 PTY）` }],
        running: false, history: []
      };
      S.terminal.push(t);
      activeId = t.id; S.ui.termActive = t.id;
      U.Store.save(); render();
    }

    function run(raw) {
      const t = tab();
      const cmd = raw.trim();
      if (!cmd) return;
      t.history = t.history || [];
      t.history.push(cmd);
      t.histPos = t.history.length;
      push(t, { t: 'cmd', text: `${t.cwd}> ${cmd}` });
      const lower = cmd.toLowerCase();

      if (cmd.startsWith('/')) {
        const name = cmd.split(/\s+/)[0];
        if (name === '/help') {
          push(t, { t: 'sys', text: '工作台内置命令（以 / 开头）：' });
          BUILTIN.forEach((b) => push(t, { t: 'help', text: `  ${b.cmd.padEnd(16, ' ')} ${b.desc}` }));
          push(t, { t: 'sys', text: '其余输入交给本机 shell（原型中为模拟输出）' });
        } else if (name === '/clear') {
          t.lines = [];
        } else if (name === '/note') {
          push(t, { t: 'sys', text: '已唤起「新建售后笔记」弹窗' });
          U.Store.save(); App.newNoteModal();
        } else if (name === '/rag') {
          const q = cmd.slice(4).trim();
          push(t, { t: 'sys', text: q ? `已向知识助手提问：${q}` : '已唤起知识助手面板' });
          U.Store.save(); App.globalAsk(q);
        } else if (name === '/kb') {
          push(t, { t: 'sys', text: '正在跳转知识库…' });
          U.Store.save(); location.hash = '#/kb';
        } else {
          push(t, { t: 'err', text: `未知内置命令：${name}。输入 /help 查看可用命令。` });
        }
        U.Store.save(); render(); return;
      }

      if (lower === 'cls' || lower === 'clear') {
        t.lines = [];
      } else if (lower === 'pwd') {
        push(t, { t: 'out', text: t.cwd });
      } else if (/^cd\b/.test(lower)) {
        const arg = cmd.slice(3).trim().replace(/^["']|["']$/g, '');
        if (!arg) {
          push(t, { t: 'out', text: t.cwd });
        } else if (arg === '..') {
          const parts = t.cwd.split('\\');
          parts.pop();
          t.cwd = parts.join('\\') || 'C:\\';
          t.title = basename(t.cwd);
          push(t, { t: 'out', text: t.cwd });
        } else {
          t.cwd = /^[A-Za-z]:/.test(arg) || arg.startsWith('\\\\') ? arg : t.cwd + '\\' + arg;
          t.title = basename(t.cwd);
          push(t, { t: 'out', text: t.cwd });
        }
      } else if (lower === 'ls' || lower === 'dir') {
        const items = {
          'C:\\Users\\Administrator': ['Desktop', 'Documents', 'Downloads', 'obsidian-backup', 'scripts'],
        }[t.cwd] || ['src', 'public', 'package.json', 'README.md', '.gitignore'];
        push(t, { t: 'out', text: '    Directory: ' + t.cwd });
        push(t, { t: 'dim', text: '' });
        items.forEach((i) => push(t, { t: 'out', text: i }));
      } else if (lower === 'git status') {
        push(t, { t: 'out', text: 'On branch ' + (t.cwd.includes('after-sales') ? 'feat/manual-queue' : 'main') });
        push(t, { t: 'sys', text: 'Your branch is up to date with \'origin/main\'.' });
        push(t, { t: 'dim', text: '' });
        push(t, { t: 'out', text: 'Changes not staged for commit:' });
        push(t, { t: 'warn', text: '  modified:   src/workbench/notes.ts' });
        push(t, { t: 'warn', text: '  modified:   src/rag/retriever.ts' });
        push(t, { t: 'dim', text: '  (use "git restore <file>..." to discard changes)' });
      } else if (lower.startsWith('git log')) {
        push(t, { t: 'out', text: 'a1f3c92 补充复卷机张力调试记录与来源引用' });
        push(t, { t: 'out', text: '7d20e11 feat(queue): 入库任务失败重试' });
        push(t, { t: 'out', text: 'b8e4a07 chore: 同步 2026Q4 样例' });
      } else if (lower.startsWith('npm run dev') || lower.startsWith('npm start')) {
        push(t, { t: 'out', text: 'VITE v5.4.8  ready in 412 ms' });
        push(t, { t: 'out', text: '➜  Local:   http://localhost:5173/' });
        push(t, { t: 'sys', text: '进程运行中 · 关闭标签前会二次确认' });
        t.running = true;
      } else if (lower === 'help') {
        push(t, { t: 'sys', text: '输入 /help 查看工作台内置命令；其他命令在本机 shell 中执行。' });
      } else {
        push(t, { t: 'dim', text: `[原型模拟] ${cmd} 未连接真实 shell，无实际输出。` });
      }
      U.Store.save();
      render();
    }

    function render() {
      const t = tab();
      el.innerHTML = `<div class="page page-terminal">
        <section class="terminal-shell">
          <header class="term-tabs">
            <div class="term-tabs-left">
              ${S.terminal.map((x) => `<button class="term-tab ${x.id === activeId ? 'active' : ''}" data-tab="${x.id}">
                ${x.running ? '<span class="run-dot"></span>' : icon('terminal', 13)}
                <span class="term-tab-name">${U.esc(x.title)}</span>
                <span class="term-tab-close" data-close="${x.id}" title="关闭标签">${icon('x', 12)}</span>
              </button>`).join('')}
              <button class="term-new" id="termNew" title="新建终端标签">${icon('plus', 15)}</button>
            </div>
            <div class="term-head-right">
              <span class="term-persist">${icon('checkCircle', 13)} 后台会话保持 · 刷新可恢复</span>
            </div>
          </header>
          <div class="term-wrap">
            <div class="term-bar"><span class="term-dot red"></span><span class="term-dot amber"></span><span class="term-dot green"></span>
              <span class="term-bar-title">${U.esc(t.cwd)}</span>
              ${t.running ? '<span class="term-running">● 进程运行中</span>' : ''}
            </div>
            <div class="term-body" id="termBody">
              ${t.lines.map((l) => {
                if (l.t === 'cmd') return `<div class="term-line cmd"><span class="term-prompt-inline">PS</span> ${U.esc(l.text)}</div>`;
                return `<div class="term-line ${l.t}">${U.esc(l.text) || '&nbsp;'}</div>`;
              }).join('')}
              <div class="term-input-line">
                <span class="term-ps">PS ${U.esc(t.cwd)}&gt;</span>
                <input id="termInput" class="term-input" autocomplete="off" spellcheck="false" placeholder="输入命令，/ 开头为内置命令" />
              </div>
              <div class="term-suggest" id="termSuggest"></div>
            </div>
            <div class="term-foot">
              <span>${icon('lightbulb', 12)} 输入 <code>/help</code> 查看内置命令</span>
              <span>每个标签独立工作目录</span>
              <span>原型为模拟输出，不执行真实命令</span>
            </div>
          </div>
        </section>
      </div>`;

      const body = el.querySelector('#termBody');
      body.scrollTop = body.scrollHeight;
      const input = el.querySelector('#termInput');
      const suggest = el.querySelector('#termSuggest');

      el.querySelectorAll('[data-tab]').forEach((b) => {
        b.addEventListener('click', (e) => {
          if (e.target.closest('[data-close]')) return;
          activeId = b.dataset.tab; S.ui.termActive = activeId;
          U.Store.save(); render();
        });
      });
      el.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', async (e) => {
        e.stopPropagation();
        const tabId = b.dataset.close;
        const x = S.terminal.find((z) => z.id === tabId);
        if (!x) return;
        // 直接关闭，不再二次确认
        S.terminal = S.terminal.filter((z) => z.id !== tabId);
        if (activeId === tabId) activeId = S.terminal[0]?.id || null;
        if (!S.terminal.length) {
          S.terminal = [{ id: U.uid('t'), cwd: 'C:\\Users\\Administrator', title: 'Users',
            lines: [{ t: 'sys', text: '新终端 · 本机 PTY（原型模拟）' }], running: false, history: [] }];
          activeId = S.terminal[0].id;
        }
        S.ui.termActive = activeId;
        U.Store.save(); render();
        U.toast('终端标签已关闭', 'success');
      }));
      el.querySelector('#termNew').addEventListener('click', (e) => {
        U.menu(e.currentTarget, DATA.terminalPresets.map((p) => ({
          icon: 'folder', label: basename(p), meta: p, onClick: () => addTab(p)
        })), { align: 'left' });
      });

      const updateSuggest = () => {
        const v = input.value;
        if (v.startsWith('/')) {
          const match = BUILTIN.filter((b) => b.cmd.split(' ')[0].startsWith(v.split(' ')[0]));
          suggest.innerHTML = match.map((m) => `<button class="sug-item" data-sug="${m.cmd.split(' ')[0]}"><code>${m.cmd}</code><span>${m.desc}</span></button>`).join('');
          suggest.classList.toggle('show', !!match.length);
          suggest.querySelectorAll('[data-sug]').forEach((b) => b.addEventListener('mousedown', (ev) => {
            ev.preventDefault(); input.value = b.dataset.sug + ' '; input.focus(); suggest.classList.remove('show');
          }));
        } else suggest.classList.remove('show');
      };
      input.addEventListener('input', updateSuggest);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          const v = input.value;
          input.value = '';
          suggest.classList.remove('show');
          run(v);
        } else if (e.key === 'ArrowUp' && t.history?.length) {
          e.preventDefault();
          t.histPos = Math.max(0, (t.histPos == null ? t.history.length : t.histPos) - 1);
          input.value = t.history[t.histPos] || '';
        } else if (e.key === 'ArrowDown' && t.history?.length) {
          e.preventDefault();
          t.histPos = Math.min(t.history.length, (t.histPos == null ? 0 : t.histPos) + 1);
          input.value = t.history[t.histPos] || '';
        } else if (e.key === 'l' && e.ctrlKey) {
          e.preventDefault(); t.lines = []; U.Store.save(); render();
        }
      });
      setTimeout(() => input.focus(), 30);
    }

    render();
  };

  /* ================= 代码仓库 ================= */
  Views.repos = function (el) {
    const S = U.Store.state;
    const state = { showHidden: false, refreshing: false };

    function render() {
      const visible = S.repos.filter((r) => state.showHidden || !r.hidden);
      const pinned = visible.filter((r) => r.pinned);
      const others = visible.filter((r) => !r.pinned);
      el.innerHTML = `<div class="page page-repos">
        <section class="card repos-card">
          <header class="card-head repos-head">
            <div class="card-title"><h2>代码仓库</h2><span class="card-sub">只读状态 · Git 操作请走终端</span></div>
            <div class="repos-tools">
              <div class="root-chips">${S.config.paths.codeRoots.map((r) => `<span class="root-chip" title="${U.esc(r)}">${icon('folder', 12)} ${U.esc(r)}</span>`).join('')}
                <button class="link-btn" data-settings>管理根目录</button></div>
              <button class="btn ghost sm ${state.showHidden ? 'on' : ''}" id="btnHidden">${icon(state.showHidden ? 'eyeOff' : 'eye', 14)} ${state.showHidden ? '隐藏无关仓库' : '显示已隐藏'}</button>
              <button class="btn ghost sm" id="btnRefresh">${icon('refresh', 14, state.refreshing ? 'spin' : '')} 刷新</button>
            </div>
          </header>
          <div class="repo-grid">
            ${[...pinned, ...others].length ? [...pinned, ...others].map(repoCard).join('') : '<div class="empty-block"><p>没有匹配的仓库</p></div>'}
          </div>
          <footer class="card-foot">
            <span>${icon('clock', 13)} 状态读取于 ${S.repos[0]?.scanned || '09:32'} · 每 30 分钟自动刷新</span>
            <span class="foot-dot"></span>
            <span>${icon('shield', 13)} 只读：不提供 commit / push / pull / 切分支</span>
            <span class="foot-dot"></span>
            <span>${pinned.length} 置顶 · ${others.length} 其他${S.repos.some((r) => r.hidden) ? ' · ' + S.repos.filter((r) => r.hidden).length + ' 已隐藏' : ''}</span>
          </footer>
        </section>
      </div>`;

      el.querySelectorAll('[data-terminal]').forEach((b) => b.addEventListener('click', () => {
        const id = b.dataset.terminal;
        const r = S.repos.find((x) => x.id === id);
        App.openTerminal(r.path);
      }));
      el.querySelectorAll('[data-repo-menu]').forEach((b) => b.addEventListener('click', (e) => {
        e.stopPropagation();
        const r = S.repos.find((x) => x.id === b.dataset.repoMenu);
        U.menu(b, [
          { icon: 'pin', label: r.pinned ? '取消置顶' : '置顶仓库', onClick: () => { r.pinned = !r.pinned; U.Store.save(); render(); U.toast(r.pinned ? '已置顶到首页' : '已取消置顶', 'success'); } },
          { icon: r.hidden ? 'eye' : 'eyeOff', label: r.hidden ? '取消隐藏' : '隐藏仓库', onClick: () => { r.hidden = !r.hidden; U.Store.save(); render(); U.toast(r.hidden ? '已在首页与列表中隐藏' : '已恢复显示', 'success'); } },
          { icon: 'copy', label: '复制仓库路径', onClick: () => U.copy(r.path) },
          { icon: 'terminal', label: '在此目录打开终端', onClick: () => App.openTerminal(r.path) },
          { divider: true },
          { icon: 'refresh', label: '刷新此仓库', onClick: () => U.toast('已重新读取 Git 状态', 'success') }
        ], { align: 'right' });
      }));
      el.querySelector('#btnHidden').addEventListener('click', () => { state.showHidden = !state.showHidden; render(); });
      el.querySelector('#btnRefresh').addEventListener('click', () => {
        if (state.refreshing) return;
        state.refreshing = true; render();
        setTimeout(() => {
          const tm = nowTime();
          S.repos.forEach((r) => { r.scanned = tm; });
          state.refreshing = false; render();
          U.toast(`已刷新 ${S.repos.length} 个仓库的 Git 状态`, 'success');
        }, 900);
      });
      el.querySelector('[data-settings]').addEventListener('click', () => { location.hash = '#/settings'; });
    }

    function repoCard(r) {
      const ahead = r.ahead ? `<span class="git-ahead">↑ ${r.ahead}</span>` : '';
      const behind = r.behind ? `<span class="git-behind">↓ ${r.behind}</span>` : '';
      const sync = !r.ahead && !r.behind ? '<span class="git-sync">与远端同步</span>' : '';
      return `<article class="repo-card ${r.hidden ? 'is-hidden' : ''}">
        <header class="repo-card-head">
          <div class="repo-card-name">${icon('git', 16)}<strong>${U.esc(r.name)}</strong>${r.pinned ? `<span class="pin-mark" title="已置顶">${icon('pin', 12)}</span>` : ''}</div>
          <button class="icon-btn" data-repo-menu="${r.id}" title="更多操作">${icon('more', 16)}</button>
        </header>
        <div class="repo-branch-line">
          <span class="branch-chip">${icon('git', 11)} ${U.esc(r.branch)}</span>
          ${r.dirty ? `<span class="dirty-chip">${r.dirty} 处未提交</span>` : '<span class="clean-chip">工作区干净</span>'}
          ${ahead}${behind}${sync}
        </div>
        <div class="repo-card-commit">
          <span class="commit-hash">${U.esc(r.commit.hash)}</span>
          <span class="commit-msg">${U.esc(r.commit.msg)}</span>
          <span class="commit-time">${U.esc(r.commit.time)}</span>
        </div>
        <footer class="repo-card-foot">
          <span class="repo-path" title="${U.esc(r.path)}">${icon('folder', 12)} ${U.esc(r.path)}</span>
          <button class="btn ghost sm" data-terminal="${r.id}">${icon('terminal', 13)} 打开终端</button>
        </footer>
      </article>`;
    }

    render();
  };
})();
