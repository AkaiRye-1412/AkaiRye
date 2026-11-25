(function () {
  window.Views = window.Views || {};

  const PRESETS = {
    '火山方舟 Agent Plan': { baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', model: 'doubao-seed-1.6-pro' },
    'DeepSeek': { baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
    '阿里云百炼': { baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
    '自定义（OpenAI 兼容）': { baseUrl: '', model: '' }
  };

  Views.settings = function (el) {
    const S = U.Store.state;
    const cfg = S.config;
    let dirty = false;
    let testing = false;

    const markDirty = () => {
      dirty = true;
      const s = el.querySelector('#cfgSaveState');
      if (s) { s.innerHTML = `<span class="dot warn"></span>有未保存的修改`; s.className = 'save-status dirty'; }
    };

    el.innerHTML = `<div class="page page-settings">
      <header class="settings-top">
        <div><h2>设置</h2><p>单机配置 · 所有内容仅保存在本机</p></div>
        <div class="settings-top-actions">
          <span class="save-status" id="cfgSaveState"><span class="dot"></span>配置已保存</span>
          <button class="btn primary" id="btnSaveCfg">${icon('save', 15)} 保存设置</button>
        </div>
      </header>

      <div class="settings-grid">
        <section class="card cfg-card">
          <div class="cfg-head"><div class="cfg-ic">${icon('message', 17)}</div><div><h3>回答模型（LLM）</h3><span>用于 RAG 回答生成，可切换厂商，无需修改代码</span></div></div>
          <div class="cfg-body">
            <label class="field"><span class="field-label">厂商</span>
              <select class="input" id="llmProvider">${Object.keys(PRESETS).map((p) => `<option ${cfg.llm.provider === p ? 'selected' : ''}>${p}</option>`).join('')}</select>
            </label>
            <label class="field"><span class="field-label">接口地址 · Base URL</span>
              <input class="input mono" id="llmBase" value="${U.esc(cfg.llm.baseUrl)}" /></label>
            <label class="field"><span class="field-label">API Key <em class="field-badge">仅本机保存</em></span>
              <div class="input-with-btn">
                <input class="input mono" type="password" id="llmKey" value="${U.esc(cfg.llm.apiKey)}" />
                <button class="icon-btn inner" id="toggleKey" title="显示 / 隐藏">${icon('eye', 15)}</button>
              </div></label>
            <div class="cfg-row">
              <label class="field"><span class="field-label">模型名</span>
                <input class="input mono" id="llmModel" value="${U.esc(cfg.llm.model)}" /></label>
              <label class="field narrow"><span class="field-label">Temperature</span>
                <input class="input mono" id="llmTemp" type="number" step="0.1" min="0" max="2" value="${cfg.llm.temperature}" /></label>
            </div>
            <div class="cfg-actions">
              <button class="btn ghost sm" id="btnTestLlm">${icon('zap', 13)} 测试连接</button>
              <span class="cfg-hint">${icon('shield', 12)} Key 仅存本机配置，不写入日志</span>
            </div>
          </div>
        </section>

        <section class="card cfg-card">
          <div class="cfg-head"><div class="cfg-ic">${icon('sparkle', 17)}</div><div><h3>向量化模型（Embedding）</h3><span>笔记与 PDF 切块的索引来源</span></div></div>
          <div class="cfg-body">
            <div class="field"><span class="field-label">运行方式</span>
              <div class="seg sm" id="embMode">
                <button class="seg-btn ${cfg.embedding.mode === 'cloud' ? 'active' : ''}" data-mode="cloud">云端（当前）</button>
                <button class="seg-btn ${cfg.embedding.mode === 'local' ? 'active' : ''}" data-mode="local">本地自托管 <em>二期</em></button>
              </div>
            </div>
            <div class="cfg-row">
              <label class="field"><span class="field-label">服务商</span><input class="input" id="embProvider" value="${U.esc(cfg.embedding.provider)}" /></label>
              <label class="field narrow"><span class="field-label">维度</span><input class="input mono" id="embDim" type="number" value="${cfg.embedding.dim}" /></label>
            </div>
            <label class="field"><span class="field-label">模型名</span>
              <input class="input mono" id="embModel" value="${U.esc(cfg.embedding.model)}" /></label>
            <div class="cfg-note">${icon('alertCircle', 13)} 云端 embedding 会将笔记与手册切块发送至厂商，已在需求中确认接受此隐私边界。</div>
          </div>
        </section>

        <section class="card cfg-card span2">
          <div class="cfg-head"><div class="cfg-ic">${icon('folder', 17)}</div><div><h3>路径与索引</h3><span>文件读写、知识库来源、待跟进阈值</span></div></div>
          <div class="cfg-body cfg-grid-2">
            <div class="cfg-col">
              <label class="field"><span class="field-label">Obsidian Vault</span><input class="input mono" id="pathVault" value="${U.esc(cfg.paths.vault)}" /></label>
              <label class="field"><span class="field-label">售后笔记文件夹</span><input class="input mono" id="pathNotes" value="${U.esc(cfg.paths.notes)}" /></label>
              <label class="field"><span class="field-label">PDF 手册根目录</span><input class="input mono" id="pathPdf" value="${U.esc(cfg.paths.pdf)}" /></label>
              <div class="field"><span class="field-label">代码根目录 <em class="field-badge">1–3 个，递归扫描仓库</em></span>
                <div class="chips-row" id="codeRoots">${cfg.paths.codeRoots.map((r) => `<span class="chip removable" data-root="${U.esc(r)}">${icon('folder', 12)} ${U.esc(r)} <button data-remove-root="${U.esc(r)}">${icon('x', 11)}</button></span>`).join('')}
                  <button class="chip add" id="addRoot">${icon('plus', 11)} 添加根目录</button></div></div>
            </div>
            <div class="cfg-col">
              <div class="field"><span class="field-label">知识库来源 <em class="field-badge">「所有笔记」勾选</em></span>
                <div class="chips-row" id="whitelist">${((cfg.indexSelection && cfg.indexSelection.folders) || []).map((w) => `<span class="chip">${icon('folder', 12)} ${U.esc(w)}</span>`).join('')}</div>
                <button class="link-btn" id="manageIndexSources">在「所有笔记」中管理来源 ${icon('chevronRight', 11)}</button>
                <span class="field-help">勾选目录或单篇笔记即纳入索引；取消勾选会清理对应索引。默认仅「客户售后问题」。</span></div>
              <div class="cfg-row">
                <label class="field narrow"><span class="field-label">增量索引防抖（秒）</span><input class="input mono" id="idxDelay" type="number" min="1" max="60" value="${cfg.indexDelay}" /></label>
                <label class="field narrow"><span class="field-label">待跟进阈值（天）</span><input class="input mono" id="followDays" type="number" min="1" max="90" value="${cfg.followUpDays}" /></label>
              </div>
              <div class="index-mini">
                <div><strong>48</strong><span>笔记已索引</span></div>
                <div><strong>${U.num(S.manuals.filter((m) => m.status === 'ready').reduce((a, m) => a + m.chunks, 0))}</strong><span>手册切块</span></div>
                <button class="btn ghost sm" id="btnReindexAll">${icon('refresh', 13)} 重建全部索引</button>
              </div>
            </div>
          </div>
        </section>

        <section class="card cfg-card">
          <div class="cfg-head"><div class="cfg-ic">${icon('book', 17)}</div><div><h3>品牌枚举</h3><span>手册筛选与对比使用的固定品牌</span></div></div>
          <div class="cfg-body">
            <div class="chips-row" id="brandChips">${cfg.brands.map((b) => `<span class="chip removable">${U.esc(b)} <button data-remove-brand="${U.esc(b)}">${icon('x', 11)}</button></span>`).join('')}
              <button class="chip add" id="addBrand">${icon('plus', 11)} 新增品牌</button></div>
            <p class="field-help">不做自由文本输入，避免“高创 / ADVANCED / 高创科技”等命名分裂。</p>
          </div>
        </section>

        <section class="card cfg-card">
          <div class="cfg-head"><div class="cfg-ic">${icon('database', 17)}</div><div><h3>自动备份</h3><span>仅备份工作台数据，笔记由 vault 机制负责</span></div></div>
          <div class="cfg-body">
            <div class="cfg-toggle-row">
              <div><strong>每日自动备份</strong><span>启动时距上次备份超过 24 小时会补一次</span></div>
              <label class="switch"><input type="checkbox" id="backupEnabled" ${cfg.backup.enabled ? 'checked' : ''} /><span></span></label>
            </div>
            <label class="field"><span class="field-label">备份位置</span><input class="input mono" id="backupLocation" value="${U.esc(cfg.backup.location)}" /></label>
            <div class="cfg-row">
              <label class="field narrow"><span class="field-label">保留份数</span><input class="input mono" id="backupRetention" type="number" value="${cfg.backup.retention}" /></label>
              <div class="field"><span class="field-label">上次备份</span><span class="nf-readonly">${U.esc(cfg.backup.last)}</span></div>
            </div>
            <div class="backup-mini">
              <div class="backup-line"><span>配置 / 会话 / 索引与向量</span><em>约 86 MB</em></div>
              <div class="backup-list">${['11-25', '11-24', '11-23', '11-22', '11-21', '11-20', '11-19'].map((d, i) => `<span class="backup-pill ${i === 0 ? 'latest' : ''}">${d}</span>`).join('')}</div>
              <button class="btn ghost sm" id="btnBackupNow">${icon('download', 13)} 立即备份</button>
            </div>
          </div>
        </section>

        <section class="card cfg-card span2">
          <div class="cfg-head"><div class="cfg-ic">${icon('terminal', 17)}</div><div><h3>快捷键</h3><span>默认快捷键，可在后续版本自定义</span></div></div>
          <div class="cfg-body">
            <div class="shortcut-grid">
              ${[['全局提问 / 定位输入框', 'Ctrl', 'K'], ['新建售后笔记', 'Ctrl', 'N'], ['打开终端', 'Ctrl', '`'], ['知识助手发送', 'Ctrl', 'Enter'], ['保存当前笔记', 'Ctrl', 'S']].map(([name, k1, k2]) => `
                <div class="shortcut-row"><span>${name}</span><span class="keys"><kbd>${k1}</kbd><kbd>${k2}</kbd></span></div>`).join('')}
            </div>
            <p class="field-help">部分组合键可能被浏览器占用（如 Ctrl+N），实现时需做兼容性验证，并在冲突时提示。</p>
          </div>
        </section>

        <section class="card cfg-card privacy-card">
          <div class="cfg-head"><div class="cfg-ic">${icon('shield', 17)}</div><div><h3>隐私与数据边界</h3><span>已确认的数据外传范围</span></div></div>
          <div class="cfg-body">
            <ul class="privacy-list">
              <li>${icon('alertCircle', 13)} Obsidian 笔记与 PDF 的<strong>切块内容</strong>会发送至云端 embedding 厂商；</li>
              <li>${icon('alertCircle', 13)} 问答时，<strong>检索命中的片段</strong>会发送至 LLM 厂商；</li>
              <li>${icon('shield', 13)} 终端与 Git 仅在本机执行，后台只监听回环地址；</li>
              <li>${icon('shield', 13)} API Key 仅保存在本机配置文件，不出现在页面刷新日志中。</li>
            </ul>
            <button class="btn ghost sm" id="btnPrivacy2">查看完整数据外传说明</button>
          </div>
        </section>

        <section class="card cfg-card danger-zone">
          <div class="cfg-head"><div class="cfg-ic danger">${icon('alert', 17)}</div><div><h3>原型数据</h3><span>仅影响本浏览器的演示数据，不会触碰真实 vault</span></div></div>
          <div class="cfg-body">
            <div class="cfg-toggle-row"><div><strong>重置原型模拟数据</strong><span>恢复初始笔记、手册、会话与终端示例</span></div>
              <button class="btn danger sm" id="btnResetProto">重置数据</button></div>
          </div>
        </section>
      </div>
      <footer class="settings-foot">
        <span>${icon('info', 13)} 本页为交互原型，修改只保存在浏览器 localStorage，不会写入任何真实配置文件。</span>
      </footer>
    </div>`;

    /* interactions */
    const providerSel = el.querySelector('#llmProvider');
    providerSel.addEventListener('change', () => {
      const preset = PRESETS[providerSel.value];
      markDirty();
      if (preset) {
        el.querySelector('#llmBase').value = preset.baseUrl;
        el.querySelector('#llmModel').value = preset.model;
        markDirty();
        U.toast('已切换厂商并填入默认接口与模型', 'info');
      }
    });
    el.querySelector('#toggleKey').addEventListener('click', () => {
      const inp = el.querySelector('#llmKey');
      const isPass = inp.type === 'password';
      inp.type = isPass ? 'text' : 'password';
      el.querySelector('#toggleKey').innerHTML = icon(isPass ? 'eyeOff' : 'eye', 15);
    });
    el.querySelector('#btnTestLlm').addEventListener('click', (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.innerHTML = `${icon('refresh', 13, 'spin')} 测试中…`;
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = `${icon('zap', 13)} 测试连接`;
        U.toast('连接成功 · 延迟 186 ms · 模型可用', 'success');
      }, 1100);
    });
    el.querySelectorAll('#embMode [data-mode]').forEach((b) => b.addEventListener('click', () => {
      el.querySelectorAll('#embMode [data-mode]').forEach((x) => x.classList.toggle('active', x === b));
      markDirty();
      if (b.dataset.mode === 'local') U.toast('本地自托管 embedding 为二期规划，当前原型保持云端', 'info');
    }));

    el.querySelector('#addRoot').addEventListener('click', async () => {
      const v = await U.prompt({ title: '添加代码根目录', label: '本地路径', value: 'D:\\Code' });
      if (!v) return;
      cfg.paths.codeRoots.push(v);
      markDirty();
      renderChips();
    });
    el.querySelector('#manageIndexSources').addEventListener('click', () => { location.hash = '#/allnotes'; });
    el.querySelector('#addBrand').addEventListener('click', async () => {
      const v = await U.prompt({ title: '新增品牌', label: '品牌名称', value: '' });
      if (!v) return;
      if (!cfg.brands.includes(v)) cfg.brands.push(v);
      markDirty(); renderChips();
    });
    function renderChips() {
      el.querySelector('#codeRoots').innerHTML = cfg.paths.codeRoots.map((r) => `<span class="chip removable">${icon('folder', 12)} ${U.esc(r)} <button data-remove-root="${U.esc(r)}">${icon('x', 11)}</button></span>`).join('') + `<button class="chip add" id="addRoot">${icon('plus', 11)} 添加根目录</button>`;
      el.querySelector('#brandChips').innerHTML = cfg.brands.map((b) => `<span class="chip removable">${U.esc(b)} <button data-remove-brand="${U.esc(b)}">${icon('x', 11)}</button></span>`).join('') + `<button class="chip add" id="addBrand">${icon('plus', 11)} 新增品牌</button>`;
      bindChipRemoves();
    }
    function bindChipRemoves() {
      el.querySelectorAll('[data-remove-root]').forEach((b) => b.addEventListener('click', () => { cfg.paths.codeRoots = cfg.paths.codeRoots.filter((x) => x !== b.dataset.removeRoot); markDirty(); renderChips(); }));
      el.querySelectorAll('[data-remove-brand]').forEach((b) => b.addEventListener('click', async () => {
        const ok = await U.confirm({ title: '移除品牌？', message: `移除「${b.dataset.removeBrand}」后，已按该品牌打标的手册仍保留品牌标记，但筛选器中不再出现。`, confirmText: '移除' });
        if (!ok) return;
        cfg.brands = cfg.brands.filter((x) => x !== b.dataset.removeBrand); markDirty(); renderChips();
      }));
      el.querySelector('#addRoot')?.addEventListener('click', addRootHandler);
      el.querySelector('#addBrand')?.addEventListener('click', addBrandHandler);
    }
    function addRootHandler() { U.prompt({ title: '添加代码根目录', label: '本地路径', value: 'D:\\Code' }).then((v) => { if (v) { cfg.paths.codeRoots.push(v); markDirty(); renderChips(); } }); }
    function addBrandHandler() { U.prompt({ title: '新增品牌', label: '品牌名称', value: '' }).then((v) => { if (v && !cfg.brands.includes(v)) { cfg.brands.push(v); markDirty(); renderChips(); } }); }

    el.querySelectorAll('.input, select').forEach((i) => {
      if (['llmProvider'].includes(i.id)) return;
      i.addEventListener('input', markDirty);
      i.addEventListener('change', markDirty);
    });
    el.querySelector('#backupEnabled').addEventListener('change', markDirty);
    el.querySelector('#btnBackupNow').addEventListener('click', () => {
      cfg.backup.last = '2026-11-25 10:18';
      markDirty();
      U.toast('已创建本机备份（配置 / 会话 / 索引与向量）', 'success');
    });
    el.querySelector('#btnReindexAll').addEventListener('click', () => U.toast('已开始重建全部索引，可在知识库查看进度', 'success'));
    el.querySelector('#btnPrivacy2').addEventListener('click', () => App.showPrivacy());
    el.querySelector('#btnResetProto').addEventListener('click', async () => {
      const ok = await U.confirm({ title: '重置原型数据？', message: '将清除本浏览器中保存的演示数据并恢复初始状态。不会触碰任何真实文件。', confirmText: '重置并刷新', danger: true });
      if (ok) U.Store.reset();
    });

    el.querySelector('#btnSaveCfg').addEventListener('click', () => {
      cfg.llm.provider = el.querySelector('#llmProvider').value;
      cfg.llm.baseUrl = el.querySelector('#llmBase').value.trim();
      cfg.llm.apiKey = el.querySelector('#llmKey').value.trim();
      cfg.llm.model = el.querySelector('#llmModel').value.trim();
      cfg.llm.temperature = Number(el.querySelector('#llmTemp').value) || 0.3;
      cfg.embedding.mode = el.querySelector('#embMode .active').dataset.mode;
      cfg.embedding.provider = el.querySelector('#embProvider').value.trim();
      cfg.embedding.model = el.querySelector('#embModel').value.trim();
      cfg.embedding.dim = Number(el.querySelector('#embDim').value) || 2048;
      cfg.paths.vault = el.querySelector('#pathVault').value.trim();
      cfg.paths.notes = el.querySelector('#pathNotes').value.trim();
      cfg.paths.pdf = el.querySelector('#pathPdf').value.trim();
      cfg.indexDelay = Number(el.querySelector('#idxDelay').value) || 8;
      cfg.followUpDays = Number(el.querySelector('#followDays').value) || 7;
      cfg.backup.enabled = el.querySelector('#backupEnabled').checked;
      cfg.backup.location = el.querySelector('#backupLocation').value.trim();
      cfg.backup.retention = Number(el.querySelector('#backupRetention').value) || 7;
      U.Store.save();
      dirty = false;
      const s = el.querySelector('#cfgSaveState');
      s.className = 'save-status';
      s.innerHTML = `<span class="dot"></span>配置已保存`;
      U.toast('设置已保存到本机配置', 'success');
    });

    bindChipRemoves();
    renderChips();
  };
})();
