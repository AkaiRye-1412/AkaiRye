(function () {
  window.Views = window.Views || {};

  function noteGroups(notes) {
    const map = {};
    notes.forEach((n) => {
      (map[n.customer] = map[n.customer] || []).push(n);
    });
    return Object.keys(map).map((customer) => {
      const list = map[customer].sort((a, b) => b.updated.localeCompare(a.updated));
      const latest = list.reduce((m, n) => (n.updated > m ? n.updated : m), '');
      return { customer, notes: list, latest };
    }).sort((a, b) => b.latest.localeCompare(a.latest));
  }

  function trendFor(range) {
    if (range === '6m') {
      return { labels: DATA.trend.months.labels, values: DATA.trend.months.values, title: '近 6 个月 · 按月', total: 46 };
    }
    if (range === '30d') {
      const w = DATA.trend.weeks;
      return { labels: w.labels.slice(3), values: w.values.slice(3), title: '近 5 周 · 按周', total: 18 };
    }
    const w = DATA.trend.weeks;
    return { labels: w.labels.slice(4), values: w.values.slice(4), title: '本月 · 按周', total: 14 };
  }

  Views.home = function (el) {
    const state = { filter: 'active', range: 'month', open: {}, reposOpen: false };
    const S = U.Store.state;

    function render() {
      const notes = S.notes;
      const threshold = S.config.followUpDays;
      const active = notes.filter((n) => n.status === 'active');
      const archived = notes.filter((n) => n.status === 'archived');
      const stale = active.filter((n) => U.daysSince(n.updated) > threshold);
      const list = state.filter === 'active' ? active : archived;
      const groups = noteGroups(list);
      const days = elapsedAvg(archived);
      const tr = trendFor(state.range);
      const rangeLabel = { month: '本月', '30d': '近 30 天', '6m': '近 6 月' }[state.range];

      const clientRows = groupClients(notes);

      el.innerHTML = `<div class="page page-home">
        <section class="card projects-card">
          <header class="card-head">
            <div class="card-title">
              <h2>${state.filter === 'active' ? '进行中项目' : '已归档项目'}</h2>
              <span class="count-pill">${list.length}</span>
            </div>
            <div class="seg sm" role="tablist">
              <button class="seg-btn ${state.filter === 'active' ? 'active' : ''}" data-filter="active">进行中 <em>${active.length}</em></button>
              <button class="seg-btn ${state.filter === 'archived' ? 'active' : ''}" data-filter="archived">已归档 <em>${archived.length}</em></button>
            </div>
            <button class="btn ghost sm" data-go-notes>全部笔记 ${icon('chevronRight', 14)}</button>
          </header>
          <div class="project-groups">
            ${groups.length ? groups.map((g) => `
              <div class="pgroup">
                <button class="pgroup-head" data-group="${U.esc(g.customer)}">
                  ${icon(state.open[g.customer] === false ? 'chevronRight' : 'chevronDown', 14)}
                  <span class="customer-avatar" style="--av:${U.avatarColor(g.customer)}">${U.esc(g.customer.slice(0, 1))}</span>
                  <span class="pgroup-name">${U.esc(g.customer)}</span>
                  <span class="pgroup-count">${g.notes.length}</span>
                  <span class="pgroup-latest">最近更新 ${U.fmtDate(g.latest)}</span>
                </button>
                ${state.open[g.customer] === false ? '' : `<div class="pgroup-body">${g.notes.map((n) => projectRow(n, threshold)).join('')}</div>`}
              </div>`).join('') : `<div class="empty-block">
                <div class="empty-ic">${icon(state.filter === 'active' ? 'checkCircle' : 'archive', 28)}</div>
                <p>${state.filter === 'active' ? '没有进行中的项目' : '还没有归档项目'}</p>
                ${state.filter === 'active' ? '<button class="btn primary sm" data-new-note>新建售后笔记</button>' : ''}
              </div>`}
          </div>
          <footer class="card-foot">
            <span>${icon('notes', 13)} 由 ${notes.length} 篇售后笔记扫描生成 · 扫描于 09:32</span>
            <span class="foot-dot"></span>
            <span>待跟进阈值 ${threshold} 天</span>
            <button class="link-btn" data-go-cleanup>整理存量笔记 ${icon('chevronRight', 12)}</button>
          </footer>
        </section>

        <section class="card dash-card">
          <header class="card-head">
            <div class="card-title">
              <h2>数据看板</h2>
              <span class="card-sub">截至 11-25 · 全部由笔记头部字段计算</span>
            </div>
            <div class="seg sm">
              ${[['month', '本月'], ['30d', '近 30 天'], ['6m', '近 6 月']].map(([k, l]) =>
                `<button class="seg-btn ${state.range === k ? 'active' : ''}" data-range="${k}">${l}</button>`).join('')}
            </div>
          </header>
          <div class="stat-row">
            <div class="stat-tile">
              <span class="stat-label"><span class="status-dot blue"></span>进行中</span>
              <span class="stat-value">${active.length}</span>
              <span class="stat-sub">个项目待处理</span>
            </div>
            <div class="stat-tile warn">
              <span class="stat-label"><span class="status-dot amber"></span>待跟进</span>
              <span class="stat-value">${stale.length}</span>
              <span class="stat-sub">超过 ${threshold} 天未更新</span>
            </div>
            <div class="stat-tile">
              <span class="stat-label"><span class="status-dot green"></span>已归档</span>
              <span class="stat-value">${archived.length}</span>
              <span class="stat-sub">手动归档项目</span>
            </div>
            <div class="stat-tile">
              <span class="stat-label"><span class="status-dot violet"></span>合计记录</span>
              <span class="stat-value">${notes.length}</span>
              <span class="stat-sub">全部客户与项目</span>
            </div>
          </div>

          <div class="dash-grid">
            <div class="chart-card">
              <div class="chart-head">
                <div><h3>新增售后记录</h3><span>${tr.title} · 共 ${tr.total} 条</span></div>
              </div>
              <div class="trend-wrap" id="trendChart"></div>
            </div>
            <div class="chart-card">
              <div class="chart-head">
                <div><h3>客户分布</h3><span>按记录数 · 含未结项目</span></div>
              </div>
              <div id="clientChart"></div>
            </div>
          </div>

          <div class="aging-block">
            <div class="aging-head">
              <h3>${icon('clock', 15)} 时效提醒</h3>
              <span>平均处理 ${days} 天 · 阈值 ${threshold} 天</span>
            </div>
            ${stale.length ? `<div class="aging-list">${stale.sort((a, b) => U.daysSince(b.updated) - U.daysSince(a.updated)).map((n) => `
              <button class="aging-row" data-note="${n.id}">
                <span class="aging-days">${U.daysSince(n.updated)} 天</span>
                <span class="aging-main"><strong>${U.esc(n.customer)} · ${U.esc(n.title)}</strong>
                  <em>最近更新 ${U.fullDate(n.updated)} · 状态进行中</em></span>
                <span class="aging-go">${icon('chevronRight', 15)}</span>
              </button>`).join('')}</div>` : `<div class="aging-empty">${icon('checkCircle', 16)} 所有进行中项目都在 ${threshold} 天内有过更新</div>`}
          </div>
        </section>

        <section class="card repo-strip ${state.reposOpen ? 'open' : ''}">
          <button class="repo-strip-head" data-repo-toggle>
            <span class="repo-strip-title">${icon('git', 16)} 代码仓库
              <span class="count-pill">${S.repos.filter((r) => r.pinned && !r.hidden).length} 置顶</span>
            </span>
            <span class="repo-strip-meta">Git 指标独立于售后看板 · 状态读取于 09:32</span>
            <span class="repo-strip-caret">${icon(state.reposOpen ? 'chevronDown' : 'chevronRight', 15)}</span>
          </button>
          ${state.reposOpen ? `<div class="repo-strip-body">${S.repos.filter((r) => r.pinned && !r.hidden).map((r) => `
            <div class="mini-repo">
              <div class="mini-repo-top"><span class="repo-ic">${icon('git', 15)}</span><strong>${U.esc(r.name)}</strong>
                <span class="branch-chip">${U.esc(r.branch)}</span></div>
              <div class="mini-repo-meta">
                <span class="${r.dirty ? 'dirty' : 'clean'}">${r.dirty ? r.dirty + ' 处修改' : '无修改'}</span>
                <span>${r.ahead ? '↑' + r.ahead : ''}${r.ahead && r.behind ? ' ' : ''}${r.behind ? '↓' + r.behind : ''}${!r.ahead && !r.behind ? '与远端同步' : ''}</span>
              </div>
              <div class="mini-repo-commit">${U.esc(r.commit.msg)} · ${U.esc(r.commit.time)}</div>
            </div>`).join('')}
            <button class="btn ghost sm repo-more" data-go-repos>查看全部仓库 ${icon('chevronRight', 13)}</button>
          </div>` : ''}
        </section>
      </div>`;

      // 数据看板置于进行中项目上方
      const homePage = el.querySelector('.page-home');
      const projectsCard = homePage.querySelector('.projects-card');
      homePage.insertBefore(homePage.querySelector('.dash-card'), projectsCard);

      Charts.trend(el.querySelector('#trendChart'), {
        labels: tr.labels, values: tr.values, unit: '条', tipLabel: '新增记录'
      });
      Charts.clients(el.querySelector('#clientChart'), clientRows, {});

      el.querySelectorAll('[data-filter]').forEach((b) => b.addEventListener('click', () => { state.filter = b.dataset.filter; render(); }));
      el.querySelectorAll('[data-range]').forEach((b) => b.addEventListener('click', () => { state.range = b.dataset.range; render(); }));
      el.querySelectorAll('[data-group]').forEach((b) => b.addEventListener('click', () => {
        const c = b.dataset.group;
        state.open[c] = state.open[c] === false ? true : false;
        render();
      }));
      el.querySelectorAll('[data-note]').forEach((b) => b.addEventListener('click', () => { location.hash = '#/note/' + b.dataset.note; }));
      el.querySelectorAll('.project-row [data-open]').forEach((b) => b.addEventListener('click', (e) => {
        e.stopPropagation(); location.hash = '#/note/' + b.dataset.open;
      }));
      el.querySelectorAll('.project-row').forEach((row) => row.addEventListener('click', () => { location.hash = '#/note/' + row.dataset.note; }));
      el.querySelectorAll('[data-term]').forEach((b) => b.addEventListener('click', (e) => {
        e.stopPropagation(); App.openTerminal(b.dataset.term, U.Store.state.config.paths.vault + '\\' + U.Store.state.config.paths.notes);
      }));
      el.querySelector('[data-go-notes]')?.addEventListener('click', () => { location.hash = '#/notes'; });
      el.querySelector('[data-go-repos]')?.addEventListener('click', () => { location.hash = '#/repos'; });
      el.querySelector('[data-go-cleanup]')?.addEventListener('click', () => { location.hash = '#/cleanup'; });
      el.querySelector('[data-repo-toggle]')?.addEventListener('click', () => { state.reposOpen = !state.reposOpen; render(); });
      el.querySelectorAll('[data-new-note]').forEach((b) => b.addEventListener('click', () => App.newNoteModal()));
    }

    render();
  };

  function projectRow(n, threshold) {
    const days = U.daysSince(n.updated);
    const stale = n.status === 'active' && days > threshold;
    return `<div class="project-row" data-note="${n.id}">
      <span class="p-dot ${n.status === 'active' ? (stale ? 'stale' : 'active') : 'archived'}"></span>
      <div class="p-main">
        <div class="p-title-line">
          <span class="p-title">${U.esc(n.title)}</span>
          ${stale ? `<span class="badge warn">${icon('clock', 11)} ${days} 天未更新</span>` : ''}
          ${n.status === 'archived' ? '<span class="badge muted">已归档</span>' : ''}
          ${n.tags.includes('Agito') ? '<span class="badge brand-tag">Agito</span>' : ''}
          ${n.tags.includes('ACS') ? '<span class="badge brand-tag">ACS</span>' : ''}
          ${n.tags.includes('CDV3000') ? '<span class="badge brand-tag">高创</span>' : ''}
        </div>
        <div class="p-meta">
          <span>${U.esc(n.customer)}</span><span class="meta-dot"></span>
          <span>最近更新 ${U.fullDate(n.updated)}（${U.relDays(n.updated)}）</span>
          <span class="meta-dot"></span><span>开始 ${U.fmtDate(n.start)}</span>
        </div>
      </div>
      <div class="p-actions">
        <button class="row-act" data-term="${U.esc(n.path)}" title="在此目录打开终端">${icon('terminal', 15)}</button>
        <span class="p-open" data-open="${n.id}" title="打开笔记">${icon('chevronRight', 16)}</span>
      </div>
    </div>`;
  }

  function groupClients(notes) {
    const map = {};
    notes.forEach((n) => {
      if (!map[n.customer]) map[n.customer] = { name: n.customer, records: 0, open: 0 };
      map[n.customer].records++;
      if (n.status === 'active') map[n.customer].open++;
    });
    return Object.values(map).sort((a, b) => b.records - a.records).slice(0, 5);
  }

  function elapsedAvg(archived) {
    if (!archived.length) return '0';
    const days = archived.map((n) => Math.max(1, Math.round((U.parseDate(n.updated) - U.parseDate(n.start)) / 86400000)));
    return (days.reduce((a, b) => a + b, 0) / days.length).toFixed(1);
  }
})();
