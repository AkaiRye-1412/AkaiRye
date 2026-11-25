(function () {
  window.Views = window.Views || {};

  Views.cleanup = function (el) {
    const S = U.Store.state;
    let step = 'scan'; // scan | preview | running | done
    let scanPct = 0;
    let execPct = 0;
    let rows = U.clone(S.cleanup || DATA.cleanupRows);
    const customers = [...new Set(S.notes.map((n) => n.customer))];

    function counts() {
      const active = rows.filter((r) => !r.skip);
      const missing = rows.filter((r) => !r.customer || !r.project).length;
      return { total: rows.length, active: active.length, missing };
    }

    function stepsHtml() {
      const idx = { scan: 0, preview: 1, running: 2, done: 2 }[step];
      return `<div class="wizard-steps">
        ${[['扫描存量', '识别缺失头部字段'], ['预览确认', '可修改 / 跳过'], ['写入与回滚', '自动备份']].map(([t, s], i) => `
          <div class="wstep ${i < idx ? 'done' : ''} ${i === idx ? 'active' : ''}">
            <span class="wstep-no">${i < idx ? icon('check', 13) : i + 1}</span>
            <div><strong>${t}</strong><span>${s}</span></div>
          </div>`).join('<span class="wstep-line"></span>')}
      </div>`;
    }

    function bodyHtml() {
      if (step === 'scan') {
        return `<div class="scan-card">
          <div class="scan-ic">${icon('scissors', 30)}</div>
          <h3>扫描存量售后笔记</h3>
          <p>识别「客户售后问题」中缺失头部字段的笔记。客户直接读取现有 frontmatter 的 <code>name</code> 字段；项目名从文件名或标题推断；无法识别的项留空等待确认。</p>
          <ul class="scan-points">
            <li>${icon('checkCircle', 14)} 只写入头部字段，<strong>不触碰正文</strong></li>
            <li>${icon('checkCircle', 14)} 执行前自动备份，可一键回滚</li>
            <li>${icon('checkCircle', 14)} 存量默认标为「已归档」，避免误标进行中</li>
          </ul>
          <button class="btn primary" id="btnScan">${icon('search', 15)} 开始扫描</button>
          <span class="scan-hint">预计扫描 48 篇笔记 · 当前约 12 篇缺少头部字段</span>
        </div>`;
      }
      if (step === 'running') {
        const active = rows.filter((r) => !r.skip);
        const total = active.length;
        const doneN = Math.min(total, Math.round((execPct / 100) * total));
        return `<div class="exec-card">
          <div class="exec-head"><span class="spin-wrap">${icon('refresh', 22, 'spin')}</span>
            <div><h3>正在写入头部字段</h3><p>仅修改 frontmatter · 正文保持不变 · 已自动备份</p></div></div>
          <div class="exec-progress"><span style="width:${execPct}%"></span></div>
          <div class="exec-meta"><span>${doneN} / ${total} 个文件</span><span>${execPct}%</span></div>
          <div class="exec-log">${active.slice(0, Math.max(1, doneN)).map((r, i) => `
            <div class="exec-line ${i === doneN - 1 ? 'current' : ''}">${icon('check', 12)} ${U.esc(r.file)}</div>`).join('')}</div>
        </div>`;
      }
      if (step === 'done') {
        const c = counts();
        return `<div class="done-card">
          <div class="done-ic">${icon('checkCircle', 34)}</div>
          <h3>存量整理完成</h3>
          <p>已为 <strong>${c.active}</strong> 篇笔记写入头部字段，跳过 ${c.total - c.active} 篇。所有正文内容均未改变。</p>
          <div class="backup-note">${icon('shield', 14)} 执行前备份：<code>D:\\AkaiRye\\backup\\cleanup-2026-11-25-1012.zip</code></div>
          <div class="done-actions">
            <button class="btn ghost" id="btnRollback">${icon('history', 14)} 一键回滚</button>
            <button class="btn primary" id="btnFinish">${icon('check', 14)} 完成，返回笔记</button>
          </div>
        </div>`;
      }
      // preview
      const c = counts();
      return `<div class="preview-wrap">
        <div class="preview-head">
          <div><h3>预览确认 · ${c.total} 篇笔记</h3>
            <span>${c.missing ? `<em class="warn-text">${c.missing} 项无法完整识别，请补全或跳过</em>` : '<em class="ok-text">全部可识别</em>'} · 修改仅写入头部</span></div>
          <div class="preview-tools">
            <button class="btn ghost sm" id="btnSkipMissing">跳过未识别项</button>
            <button class="btn ghost sm" id="btnSetArchived">全部设为已归档</button>
          </div>
        </div>
        <div class="cleanup-table">
          <div class="cleanup-head">
            <span>文件</span><span>客户</span><span>项目名</span><span>状态</span><span title="识别说明">识别</span><span>跳过</span>
          </div>
          ${rows.map((r, i) => `<div class="cleanup-row ${r.skip ? 'skipped' : ''}" data-row="${i}">
            <div class="cleanup-file">${icon('file', 14)}<span title="${U.esc(r.file)}">${U.esc(r.file)}</span></div>
            <div><input class="input sm ${!r.customer ? 'missing' : ''}" data-col="customer" data-i="${i}" value="${U.esc(r.customer)}" placeholder="请填写" list="customerList" ${r.skip ? 'disabled' : ''} /></div>
            <div><input class="input sm ${!r.project ? 'missing' : ''}" data-col="project" data-i="${i}" value="${U.esc(r.project)}" placeholder="请填写" ${r.skip ? 'disabled' : ''} /></div>
            <div><select class="input select sm" data-col="status" data-i="${i}" ${r.skip ? 'disabled' : ''}>
              <option value="archived" ${r.status === 'archived' ? 'selected' : ''}>已归档</option>
              <option value="active" ${r.status === 'active' ? 'selected' : ''}>进行中</option>
            </select></div>
            <div class="cleanup-infer">${(!r.customer || !r.project) ? icon('alert', 12) : icon('check', 12)}<span>${U.esc(r.inferred || '')}</span></div>
            <div class="cleanup-skip"><label class="switch small"><input type="checkbox" data-skip="${i}" ${r.skip ? 'checked' : ''} /><span></span></label></div>
          </div>`).join('')}
        </div>
        <datalist id="customerList">${customers.map((c) => `<option value="${U.esc(c)}"></option>`).join('')}</datalist>
        <div class="preview-foot">
          <div><span class="preview-count">将写入 <strong>${c.active}</strong> 篇</span><span>跳过 ${c.total - c.active} 篇</span>
            <span class="backup-inline">${icon('shield', 13)} 执行前自动备份</span></div>
          <div><button class="btn ghost" id="btnBackScan">重新扫描</button>
            <button class="btn primary" id="btnExecute" ${c.active ? '' : 'disabled'}>${icon('edit', 14)} 写入 ${c.active} 篇头部字段</button></div>
        </div>
      </div>`;
    }

    function render() {
      el.innerHTML = `<div class="page page-cleanup">
        <header class="cleanup-top">
          <div class="cleanup-title">
            <button class="icon-btn" data-back title="返回笔记">${icon('arrowLeft', 18)}</button>
            <div><h2>存量笔记整理</h2><p>补全缺失的头部字段 · 只写 frontmatter，不改正文</p></div>
          </div>
          ${stepsHtml()}
        </header>
        <section class="card cleanup-card">${bodyHtml()}</section>
        <footer class="cleanup-foot">${icon('info', 13)} 原型演示：不会修改真实 vault 文件；执行仅影响浏览器内模拟数据。</footer>
      </div>`;
      bind();
    }

    function scan() {
      step = 'scan';
      scanPct = 0;
      render();
      const btn = el.querySelector('#btnScan');
      btn.disabled = true;
      btn.innerHTML = `${icon('refresh', 15, 'spin')} 扫描中 0%`;
      const timer = setInterval(() => {
        if (!btn.isConnected) { clearInterval(timer); return; }
        scanPct += Math.floor(Math.random() * 9) + 6;
        if (scanPct >= 100) {
          clearInterval(timer);
          step = 'preview';
          rows = U.clone(DATA.cleanupRows);
          render();
          U.toast(`扫描完成，发现 ${rows.length} 篇待整理笔记`, 'success');
        } else {
          btn.innerHTML = `${icon('refresh', 15, 'spin')} 扫描中 ${Math.min(99, scanPct)}%`;
        }
      }, 260);
    }

    function execute() {
      step = 'running';
      execPct = 0;
      render();
      const timer = setInterval(() => {
        if (!el.isConnected) { clearInterval(timer); return; }
        execPct += Math.floor(Math.random() * 12) + 8;
        if (execPct >= 100) {
          clearInterval(timer);
          execPct = 100;
          rows.forEach((r) => { if (!r.skip) r.done = true; });
          S.cleanup = rows;
          U.Store.save();
          step = 'done';
          render();
          U.toast('存量笔记头部字段已写入，备份可回滚', 'success');
        } else render();
      }, 320);
    }

    function bind() {
      el.querySelector('[data-back]')?.addEventListener('click', () => { location.hash = '#/notes'; });
      el.querySelector('#btnScan')?.addEventListener('click', scan);
      el.querySelector('#btnBackScan')?.addEventListener('click', () => { step = 'scan'; render(); });
      el.querySelector('#btnSetArchived')?.addEventListener('click', () => {
        rows.forEach((r) => { r.status = 'archived'; });
        render();
        U.toast('已将所有建议状态设为「已归档」', 'info');
      });
      el.querySelector('#btnSkipMissing')?.addEventListener('click', () => {
        rows.forEach((r) => { if (!r.customer || !r.project) r.skip = true; });
        render();
        U.toast('已跳过未识别的笔记', 'info');
      });
      el.querySelectorAll('[data-col]').forEach((inp) => {
        inp.addEventListener('change', () => {
          const r = rows[Number(inp.dataset.i)];
          r[inp.dataset.col] = inp.value.trim();
          U.Store.save();
          render();
        });
      });
      el.querySelectorAll('[data-skip]').forEach((cb) => cb.addEventListener('change', () => {
        rows[Number(cb.dataset.skip)].skip = cb.checked;
        render();
      }));
      el.querySelector('#btnExecute')?.addEventListener('click', async () => {
        const c = counts();
        const unresolved = rows.filter((r) => !r.skip && (!r.customer || !r.project)).length;
        if (unresolved) {
          U.toast(`还有 ${unresolved} 篇缺少客户或项目名，请补全或跳过`, 'warn');
          return;
        }
        const ok = await U.confirm({
          title: '确认写入头部字段？',
          message: `将为 ${c.active} 篇笔记写入 / 更新头部字段，跳过 ${c.total - c.active} 篇。执行前会自动备份，可一键回滚。`,
          confirmText: '开始写入',
          detail: `<span>${icon('shield', 13)} 仅修改 frontmatter：客户、项目名、状态、开始日期、最近更新</span>`
        });
        if (ok) execute();
      });
      el.querySelector('#btnFinish')?.addEventListener('click', () => { location.hash = '#/notes'; });
      el.querySelector('#btnRollback')?.addEventListener('click', async () => {
        const ok = await U.confirm({
          title: '回滚到整理前？',
          message: '将从自动备份中恢复这些笔记的头部字段。正文不受影响。回滚后可以重新整理。',
          confirmText: '回滚', danger: true
        });
        if (!ok) return;
        rows = U.clone(DATA.cleanupRows);
        S.cleanup = rows;
        U.Store.save();
        step = 'preview';
        render();
        U.toast('已回滚到整理前的状态', 'success');
      });
    }

    render();
  };
})();
