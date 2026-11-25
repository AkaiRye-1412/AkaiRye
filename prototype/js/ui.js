(function () {
  const U = {};

  U.esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  U.clone = (o) => JSON.parse(JSON.stringify(o));

  U.parseDate = (s) => new Date(s + (s.length <= 10 ? 'T00:00:00+08:00' : ''));

  U.daysSince = (s) => {
    const d = U.parseDate(s);
    return Math.max(0, Math.floor((DATA.today - d) / 86400000));
  };

  U.fmtDate = (s) => {
    if (!s) return '—';
    const d = U.parseDate(s);
    return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  U.fullDate = (s) => {
    if (!s) return '—';
    const d = U.parseDate(s);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  U.todayStr = () => '2026-11-25';
  U.relDays = (s) => {
    const n = U.daysSince(s);
    return n === 0 ? '今天' : n === 1 ? '昨天' : `${n} 天前`;
  };
  U.num = (n) => (n == null ? '—' : Number(n).toLocaleString('zh-CN'));
  U.debounce = (fn, ms) => {
    let t;
    return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); };
  };
  U.uid = (p) => (p || 'id') + '-' + Math.random().toString(36).slice(2, 7);

  U.inline = (s) => s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>');
  U.md = function (text) {
    if (!text) return '';
    const src = U.esc(text.replace(/\r\n/g, '\n'));

    const renderLines = (lines) => {
      let out = '';
      let para = [];
      let list = null;
      let items = [];
      let quote = [];
      const flushPara = () => {
        if (!para.length) return;
        out += `<p>${U.inline(para.join('<br>'))}</p>`;
        para = [];
      };
      const flushList = () => {
        if (!list) return;
        out += `<${list}>${items.map((it) => `<li>${U.inline(it)}</li>`).join('')}</${list}>`;
        list = null; items = [];
      };
      const flushQuote = () => {
        if (!quote.length) return;
        out += `<blockquote class="md-quote">${U.inline(quote.join('<br>'))}</blockquote>`;
        quote = [];
      };
      lines.forEach((line) => {
        if (/^-\s/.test(line)) {
          flushPara(); flushQuote();
          if (list !== 'ul') { flushList(); list = 'ul'; }
          items.push(line.replace(/^-\s/, ''));
          return;
        }
        if (/^\d+\.\s/.test(line)) {
          flushPara(); flushQuote();
          if (list !== 'ol') { flushList(); list = 'ol'; }
          items.push(line.replace(/^\d+\.\s/, ''));
          return;
        }
        if (/^>\s?/.test(line)) {
          flushPara(); flushList();
          quote.push(line.replace(/^>\s?/, ''));
          return;
        }
        if (/^##\s/.test(line)) {
          flushPara(); flushList(); flushQuote();
          out += `<h3 class="md-h">${U.inline(line.replace(/^##\s/, ''))}</h3>`;
          return;
        }
        if (/^#\s/.test(line)) {
          flushPara(); flushList(); flushQuote();
          out += `<h2 class="md-h">${U.inline(line.replace(/^#\s/, ''))}</h2>`;
          return;
        }
        flushList(); flushQuote();
        para.push(line);
      });
      flushPara(); flushList(); flushQuote();
      return out;
    };

    const blocks = src.split(/\n{2,}/);
    return blocks.map((block) => {
      const lines = block.split('\n');
      if (lines[0].startsWith('```')) {
        const code = lines.slice(1).join('\n').replace(/\n```$/, '');
        return `<pre class="md-code"><code>${code}</code></pre>`;
      }
      return renderLines(lines);
    }).join('');
  };

  /* ---------- store ---------- */
  const KEY = 'akai-workbench-proto-v1';
  const Store = {
    state: null,
    load() {
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { saved = null; }
      const base = {
        notes: U.clone(DATA.notes), manuals: U.clone(DATA.manuals), repos: U.clone(DATA.repos),
        sessions: U.clone(DATA.sessions), config: U.clone(DATA.config),
        cleanup: U.clone(DATA.cleanupRows),
        terminal: U.clone(DATA.terminalInitial),
        activeSessionId: 's1', noteDrafts: {},
        ui: { ragWidth: 400, ragCollapsed: null, ragTab: 'answer' }
      };
      if (saved && saved.v === 1) {
        this.state = Object.assign(base, saved.data, {
          notes: saved.data.notes || base.notes,
          manuals: saved.data.manuals || base.manuals,
          repos: saved.data.repos || base.repos,
          sessions: saved.data.sessions || base.sessions,
          config: saved.data.config || base.config,
          cleanup: saved.data.cleanup || base.cleanup,
          terminal: saved.data.terminal || base.terminal,
          ui: Object.assign(base.ui, saved.data.ui || {})
        });
      } else {
        this.state = base;
      }
      /* 旧版本 localStorage 迁移：「所有笔记」的知识库来源选择 */
      if (!this.state.config.indexSelection) {
        this.state.config.indexSelection = U.clone(DATA.config.indexSelection);
      }
      return this.state;
    },
    save() {
      clearTimeout(this._t);
      this._t = setTimeout(() => {
        try {
          localStorage.setItem(KEY, JSON.stringify({ v: 1, data: this.state }));
        } catch (e) { /* ignore quota */ }
      }, 250);
    },
    saveNow() {
      clearTimeout(this._t);
      try { localStorage.setItem(KEY, JSON.stringify({ v: 1, data: this.state })); } catch (e) {}
    },
    reset() {
      localStorage.removeItem(KEY);
      location.reload();
    },
    note(id) { return this.state.notes.find((n) => n.id === id); },
    manual(id) { return this.state.manuals.find((m) => m.id === id); },
    session(id) { return this.state.sessions.find((s) => s.id === id); }
  };
  U.Store = Store;

  /* ---------- toasts ---------- */
  const tr = () => document.getElementById('toastRoot');
  U.toast = function (msg, type = 'success', opts = {}) {
    const root = tr();
    if (!root) return;
    const iconName = type === 'success' ? 'checkCircle' : type === 'error' ? 'alertCircle' : type === 'warn' ? 'alert' : 'info';
    const node = document.createElement('div');
    node.className = `toast toast-${type}`;
    node.innerHTML = `<span class="toast-ic">${icon(iconName, 18)}</span><span class="toast-msg">${msg}</span>` +
      (opts.action ? `<button class="toast-act">${U.esc(opts.action)}</button>` : '');
    if (opts.action && opts.onAction) {
      node.querySelector('.toast-act').addEventListener('click', () => { opts.onAction(); node.remove(); });
    }
    root.appendChild(node);
    requestAnimationFrame(() => node.classList.add('in'));
    setTimeout(() => {
      node.classList.remove('in');
      setTimeout(() => node.remove(), 220);
    }, opts.duration || 3200);
  };

  /* ---------- modal ---------- */
  let currentModal = null;
  U.modal = function ({ title, subtitle, body, actions = [], width = 520, dismissible = true, onMount }) {
    const root = document.getElementById('modalRoot');
    if (currentModal) currentModal.remove();
    const wrap = document.createElement('div');
    wrap.className = 'modal-backdrop';
    const acts = actions.length ? `<div class="modal-foot">${actions.map((a, i) =>
      `<button class="btn ${a.variant || 'ghost'}" data-act="${i}">${U.esc(a.label)}</button>`).join('')}</div>` : '';
    wrap.innerHTML = `<div class="modal" role="dialog" aria-modal="true" style="max-width:${width}px">
      <div class="modal-head">
        <div><h2 class="modal-title">${U.esc(title || '')}</h2>
        ${subtitle ? `<p class="modal-sub">${subtitle}</p>` : ''}</div>
        ${dismissible ? `<button class="icon-btn modal-x" aria-label="关闭">${icon('x', 18)}</button>` : ''}
      </div>
      <div class="modal-body">${body || ''}</div>${acts}
    </div>`;
    root.appendChild(wrap);
    currentModal = wrap;
    const close = () => { if (!wrap.parentNode) return; wrap.classList.add('leaving'); setTimeout(() => wrap.remove(), 180); if (currentModal === wrap) currentModal = null; };
    wrap.querySelectorAll('[data-act]').forEach((b) => {
      b.addEventListener('click', () => {
        const a = actions[Number(b.dataset.act)];
        if (a.onClick) {
          const r = a.onClick(close, wrap);
          if (r === false) return;
        }
        if (a.keepOpen !== true) close();
      });
    });
    if (dismissible) {
      wrap.querySelector('.modal-x')?.addEventListener('click', close);
      wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
      const esc = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } };
      document.addEventListener('keydown', esc);
    }
    if (onMount) setTimeout(() => onMount(wrap, close), 0);
    setTimeout(() => { const f = wrap.querySelector('input,textarea,select'); if (f) f.focus(); }, 60);
    return close;
  };

  U.confirm = function ({ title, message, confirmText = '确认', cancelText = '取消', danger = false, detail }) {
    return new Promise((resolve) => {
      U.modal({
        title, width: 460,
        body: `<p class="confirm-msg">${message}</p>${detail ? `<div class="confirm-detail">${detail}</div>` : ''}`,
        actions: [
          { label: cancelText, variant: 'ghost', onClick: () => resolve(false) },
          { label: confirmText, variant: danger ? 'danger' : 'primary', onClick: () => resolve(true) }
        ]
      });
    });
  };

  U.prompt = function ({ title, label = '名称', value = '', okText = '确定' }) {
    return new Promise((resolve) => {
      U.modal({
        title, width: 420,
        body: `<label class="field"><span class="field-label">${U.esc(label)}</span>
          <input class="input" id="promptInput" value="${U.esc(value)}" /></label>`,
        actions: [
          { label: '取消', variant: 'ghost', onClick: () => resolve(null) },
          { label: okText, variant: 'primary', onClick: (close, wrap) => {
            const v = wrap.querySelector('#promptInput').value.trim();
            if (!v) { U.toast('名称不能为空', 'warn'); return false; }
            resolve(v);
          } }
        ],
        onMount: (wrap) => {
          const inp = wrap.querySelector('#promptInput');
          inp.select();
          inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') wrap.querySelector('[data-act="1"]').click(); });
        }
      });
    });
  };

  /* ---------- menus ---------- */
  function closeMenu() {
    const r = document.getElementById('menuRoot');
    if (r) r.innerHTML = '';
  }
  U.closeMenu = closeMenu;
  U.menu = function (anchor, items, opts = {}) {
    closeMenu();
    const root = document.getElementById('menuRoot');
    const rect = anchor.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.className = 'pop-menu';
    menu.innerHTML = items.map((it, i) => {
      if (it.divider) return `<div class="menu-divider"></div>`;
      return `<button class="menu-item ${it.danger ? 'danger' : ''} ${it.active ? 'active' : ''}" data-i="${i}" ${it.disabled ? 'disabled' : ''}>
        <span class="menu-ic">${it.icon ? icon(it.icon, 16) : ''}</span>
        <span class="menu-label">${U.esc(it.label)}${it.meta ? `<em>${U.esc(it.meta)}</em>` : ''}</span>
        ${it.active ? `<span class="menu-check">${icon('check', 14)}</span>` : ''}
      </button>`;
    }).join('');
    root.appendChild(menu);
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    let left = opts.align === 'right' ? rect.right - mw : rect.left;
    left = Math.max(8, Math.min(left, window.innerWidth - mw - 8));
    let top = rect.bottom + 6;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, rect.top - mh - 6);
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
    menu.querySelectorAll('.menu-item').forEach((b) => {
      b.addEventListener('click', () => {
        const it = items[Number(b.dataset.i)];
        closeMenu();
        if (it.onClick) it.onClick();
      });
    });
    setTimeout(() => {
      const outside = (e) => {
        if (!menu.contains(e.target) && !anchor.contains(e.target)) {
          closeMenu();
          document.removeEventListener('mousedown', outside);
        }
      };
      document.addEventListener('mousedown', outside);
    }, 0);
  };

  /* ---------- small helpers ---------- */
  U.copy = async function (text) {
    try {
      await navigator.clipboard.writeText(text);
      U.toast('已复制到剪贴板', 'success');
    } catch (e) {
      U.toast('复制失败，请手动复制', 'warn');
    }
  };

  U.focusEnd = (el) => {
    if (!el) return;
    el.focus();
    const len = el.value.length;
    try { el.setSelectionRange(len, len); } catch (e) {}
  };

  U.meter = (pct, cls) => `<div class="meter ${cls || ''}"><span style="width:${Math.max(0, Math.min(100, pct))}%"></span></div>`;

  U.avatarColor = (name) => {
    const palette = ['#2a78d6', '#1baf7a', '#c98500', '#6250d6', '#d03b3b', '#0b8a8a'];
    let h = 0;
    for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 997;
    return palette[h % palette.length];
  };

  window.U = U;
})();
