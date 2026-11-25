(function () {
  const Charts = {};

  function roundedTopBar(x, y, w, h, r) {
    const rr = Math.min(r, w / 2, h);
    return `<path class="bar-path" d="M${x} ${y + h} L${x} ${y + rr} Q${x} ${y} ${x + rr} ${y} L${x + w - rr} ${y} Q${x + w} ${y} ${x + w} ${y + rr} L${x + w} ${y + h} Z"/>`;
  }

  /* 柱状趋势图（单系列，无图例；hover 提示 + 选择性直标） */
  Charts.trend = function (container, opt) {
    if (!container) return;
    const values = opt.values || [];
    const labels = opt.labels || [];
    if (!values.length) { container.innerHTML = '<div class="chart-empty">暂无数据</div>'; return; }
    const W = 640, H = 216, m = { l: 30, r: 12, t: 22, b: 30 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const rawMax = Math.max(...values, 1);
    const step = Math.max(1, Math.ceil(rawMax / 4));
    const max = Math.ceil(rawMax / step) * step;
    const ticks = [0, step, step * 2, step * 3].filter((t) => t <= max);
    if (ticks[ticks.length - 1] !== max) ticks.push(max);
    const band = iw / values.length;
    const bw = Math.min(24, band * 0.56);

    const grid = ticks.map((t) => {
      const y = m.t + ih - (t / max) * ih;
      return `<line class="chart-grid" x1="${m.l}" y1="${y}" x2="${W - m.r}" y2="${y}"/>` +
        `<text class="chart-axis" x="${m.l - 8}" y="${y + 3.5}" text-anchor="end">${t}</text>`;
    }).join('');

    const bars = values.map((v, i) => {
      const h = Math.max(2, (v / max) * ih);
      const x = m.l + band * i + (band - bw) / 2;
      const y = m.t + ih - h;
      const active = i === values.length - 1;
      const label = active ? `<text class="chart-label" x="${x + bw / 2}" y="${y - 7}" text-anchor="middle">${v}</text>` : '';
      return `<g class="chart-bar-g" data-i="${i}">
        <rect class="chart-hit" x="${m.l + band * i}" y="${m.t}" width="${band}" height="${ih}" fill="transparent"/>
        ${roundedTopBar(x, y, bw, h, 4).replace('bar-path', 'bar-path chart-bar' + (active ? ' chart-bar-last' : ''))}
        ${label}
        <text class="chart-x" x="${m.l + band * i + band / 2}" y="${H - m.b + 17}" text-anchor="middle">${labels[i] || ''}</text>
      </g>`;
    }).join('');

    container.classList.add('chart');
    container.innerHTML = `<svg class="trend-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="新增售后记录趋势">
      ${grid}${bars}
      <text class="chart-axis-title" x="${m.l - 8}" y="${m.t - 8}" text-anchor="end">${opt.unit || '条'}</text>
    </svg><div class="chart-tip" aria-hidden="true"></div>`;

    const tip = container.querySelector('.chart-tip');
    const svg = container.querySelector('.trend-svg');
    container.querySelectorAll('.chart-bar-g').forEach((g) => {
      const i = Number(g.dataset.i);
      g.addEventListener('mouseenter', () => {
        tip.innerHTML = `<span class="tip-label">${opt.tipLabel || ''}${opt.tipLabel ? ' · ' : ''}${labels[i]}</span><span class="tip-value">${values[i]} ${opt.unit || '条'}</span>`;
        const r = g.getBoundingClientRect();
        const cr = container.getBoundingClientRect();
        let left = r.left - cr.left + r.width / 2;
        left = Math.max(44, Math.min(left, cr.width - 44));
        tip.style.left = left + 'px';
        tip.style.top = (r.top - cr.top + 2) + 'px';
        tip.classList.add('show');
        g.querySelector('.chart-hit').nextElementSibling?.classList.add('hover');
      });
      g.addEventListener('mouseleave', () => {
        tip.classList.remove('show');
        svg.querySelectorAll('.chart-bar').forEach((b) => b.classList.remove('hover'));
      });
    });
  };

  /* 客户分布条形图（单系列记录数 + 未结数文本，直标数值） */
  Charts.clients = function (container, rows, opt) {
    if (!container) return;
    const max = Math.max(...rows.map((r) => r.records), 1);
    container.innerHTML = `<div class="hbar-list">${rows.map((r, i) => `
      <div class="hbar-row" title="${U.esc(r.name)}：记录 ${r.records} 条，未结 ${r.open} 条">
        <span class="hbar-name">${U.esc(r.name)}</span>
        <div class="hbar-track">
          <div class="hbar-fill ${i === 0 ? 'first' : ''}" style="width:${(r.records / max) * 100}%">
            <span class="hbar-value">${r.records}</span>
          </div>
        </div>
        <span class="hbar-open">未结 ${r.open}</span>
      </div>`).join('')}</div>`;
  };

  Charts.spark = function (values, cls) {
    const max = Math.max(...values, 1);
    const w = 72, h = 20;
    const pts = values.map((v, i) => `${(i / (values.length - 1)) * (w - 2) + 1},${h - 2 - (v / max) * (h - 4)}`).join(' ');
    return `<svg class="spark ${cls || ''}" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">
      <polyline fill="none" stroke="var(--chart-series-1)" stroke-width="2" stroke-linecap="round" points="${pts}"/>
      <circle cx="${w - 1}" cy="${h - 2 - (values[values.length - 1] / max) * (h - 4)}" r="3" fill="var(--chart-series-1)"/>
    </svg>`;
  };

  window.Charts = Charts;
})();
