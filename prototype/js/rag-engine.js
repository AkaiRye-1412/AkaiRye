(function () {
  /* NOTE_IDS：售后笔记的预置来源 id（保留用于主题回答引用）。
     手册来源 MANUAL_IDS 不变。
     其余售后笔记（无预置来源）与 vaultExtras 笔记的来源在运行时动态生成，
     并按「所有笔记」勾选范围纳入检索。 */
  const NOTE_IDS = ['s_n1', 's_n2', 's_n4', 's_n5', 's_n9'];
  const MANUAL_IDS = ['s_m1', 's_m1b', 's_m2', 's_m3', 's_m4', 's_m4b'];
  const RELEVANT = {
    tension: ['s_n1', 's_m1', 's_m1b', 's_m2'],
    fault: ['s_n5', 's_m4', 's_m4b'],
    comm: ['s_n2', 's_m2', 's_m3', 's_m4'],
    generic: []
  };

  function detectTopic(q) {
    const s = (q || '').toLowerCase();
    if (/张力|锥度|卷径|收卷/.test(s)) return 'tension';
    if (/e-?07|故障码|报警|过热|状态字|bus error/.test(s)) return 'fault';
    if (/通讯|通信|掉线|ethercat|ether ?net|pdo|ether ?cat|连不上/.test(s)) return 'comm';
    return 'generic';
  }

  /* ---------- 勾选范围判定（与 views-allnotes 一致的最具体目录规则） ---------- */
  function dirOf(path) {
    const i = path.lastIndexOf('/');
    return i < 0 ? '' : path.slice(0, i);
  }
  function isUnder(path, parent) {
    return path === parent || path.indexOf(parent + '/') === 0;
  }
  function folderRule(path, s) {
    const d = dirOf(path);
    let rule = null, len = -1;
    (s.folders || []).forEach((f) => { if (isUnder(d, f) && f.length > len) { rule = 'in'; len = f.length; } });
    (s.excludedFolders || []).forEach((f) => { if (isUnder(d, f) && f.length > len) { rule = 'out'; len = f.length; } });
    return rule;
  }
  function isNoteSelected(path) {
    if (!path) return false;
    const cfg = U.Store.state.config || {};
    const s = cfg.indexSelection || { folders: ['客户售后问题'], files: [], excludedFiles: [], excludedFolders: [] };
    if ((s.excludedFiles || []).indexOf(path) >= 0) return false;
    if ((s.files || []).indexOf(path) >= 0) return true;
    return folderRule(path, s) === 'in';
  }

  function extractSnippet(body) {
    if (!body) return '';
    const lines = body.replace(/\r\n/g, '\n').split('\n');
    if (/^#\s/.test(lines[0] || '')) lines.shift();
    const txt = lines.map((l) => l.replace(/^#{1,6}\s/, '').replace(/^[-\d>]\s?/, '').trim())
      .filter(Boolean).join(' ');
    return txt.length > 80 ? txt.slice(0, 80) + '…' : txt;
  }
  function dirLabel(path) {
    const d = dirOf(path);
    return d || 'vault 根目录';
  }
  const src = (id, pool) => pool[id];

  /* ---------- 动态来源池 ----------
     1) 预置来源（DATA.sources：5 篇售后笔记 + 手册）
     2) 无预置来源的售后笔记（DATA.notes 中其余 6 篇）→ 动态生成来源
     3) vaultExtras 笔记 → 动态生成来源
     这样 vault 中每一篇笔记都有来源记录，勾选即可纳入检索。 */
  function buildPool() {
    const pool = {};
    Object.assign(pool, DATA.sources);

    // 已有预置来源的售后笔记 noteId 集合
    const presetNoteIds = new Set();
    Object.keys(pool).forEach((id) => { if (pool[id].noteId) presetNoteIds.add(pool[id].noteId); });

    (DATA.notes || []).forEach((n) => {
      if (presetNoteIds.has(n.id)) return;
      const id = 's_nx_' + n.id;
      if (pool[id]) return;
      pool[id] = {
        id, type: 'note', noteId: n.id, path: n.path, dyn: true,
        title: (n.customer && n.title) ? (n.customer + ' · ' + n.title) : n.path.split('/').pop().replace(/\.md$/i, ''),
        meta: '售后记录 · ' + (n.updated || ''),
        section: dirLabel(n.path),
        snippet: extractSnippet(n.body)
      };
    });

    (DATA.vaultExtras || []).forEach((e) => {
      const id = 's_v_' + e.path.replace(/[^a-zA-Z0-9]/g, '_');
      if (pool[id]) return;
      pool[id] = {
        id, type: 'note', vault: true, path: e.path,
        title: (e.name || e.path.split('/').pop()).replace(/\.md$/i, ''),
        meta: '笔记 · ' + (e.updated || ''),
        section: dirLabel(e.path),
        snippet: extractSnippet(e.body)
      };
    });
    return pool;
  }

  function selectedNoteIds(pool) {
    return Object.keys(pool).filter((id) => {
      const s = pool[id];
      if (!s || s.type !== 'note') return false;
      let path = s.path;
      if (!path && s.noteId) { const n = U.Store.note(s.noteId); path = n && n.path; }
      return path && isNoteSelected(path);
    });
  }

  /* 把查询切成 2-gram（适配中文无空格分词），任一命中标题或正文即算相关 */
  function keywords(q) {
    const s = (q || '').toLowerCase().replace(/\s+/g, '');
    const grams = [];
    for (let i = 0; i <= s.length - 2; i++) grams.push(s.slice(i, i + 2));
    return grams;
  }

  /* 所有笔记来源（含动态售后 + vaultExtras）按 2-gram 命中标题/正文，排除已引用的 id，最多 N 条 */
  function noteKeywordHits(q, allow, pool, excludeIds) {
    const kws = keywords(q);
    if (!kws.length) return [];
    const ex = excludeIds || [];
    const re = new RegExp(kws.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'));
    return Object.keys(pool).filter((id) => {
      const s = pool[id];
      if (!s || s.type !== 'note' || !allow.has(id) || ex.indexOf(id) >= 0) return false;
      const t = (s.title || '').toLowerCase() + ' ' + (s.snippet || '').toLowerCase();
      return re.test(t);
    }).slice(0, 5);
  }

  function allowed(res, ctx, pool) {
    const noteIds = selectedNoteIds(pool);
    if (res.kind === 'all') return new Set([...noteIds, ...MANUAL_IDS]);
    if (res.kind === 'manuals') return new Set(MANUAL_IDS);
    if (res.kind === 'brands') {
      return new Set(MANUAL_IDS.filter((id) => {
        const b = (pool[id] || {}).brand || '';
        return b === res.brands[0] || res.brands.includes(b);
      }));
    }
    if (res.kind === 'project' && ctx) {
      return new Set(noteIds.filter((id) => {
        const s = pool[id]; if (!s) return false;
        const n = s.noteId ? U.Store.note(s.noteId) : null;
        return n && n.customer === ctx.customer;
      }));
    }
    return new Set(noteIds);
  }

  const notFound = (label, q) => ({
    noResult: true,
    text: `未在「${label}」范围内找到能回答这个问题的依据。

为避免编造，我不会给出推断性结论。可以尝试：
- 将检索范围切换到「全部知识库」或「全部手册」；
- 在问题中加入设备型号、故障码或参数名；
- 确认相关手册已经完成入库、相关笔记已在「所有笔记」中勾选（未勾选笔记不参与检索）。

> 当前问题：${q}`,
    sources: [], fragments: [], topic: 'none'
  });

  /* ---------- 笔记范围回答：售后笔记主题预置 + 全部勾选笔记关键词兜底 ---------- */
  function noteAnswer(topic, allow, res, ctx, pool, q) {
    const label = res.label;
    const parts = [];
    const S = [];
    const cite = (id) => { S.push(id); return `{{${S.length - 1}}}`; };
    const topicNote = { tension: 's_n1', fault: 's_n5', comm: 's_n2' }[topic];
    const hasTopicNote = topicNote && allow.has(topicNote);

    if (topic === 'tension') {
      if (hasTopicNote) {
        parts.push(`在「${label}」内找到 1 条直接相关的处理记录。\n\n` +
          `**${src(topicNote, pool).title}**（${src(topicNote, pool).meta}）：${src(topicNote, pool).snippet}${cite(topicNote)}\n\n` +
          `按当时的排查顺序，建议你先复测**张力传感器零点**与**卷径计算**，再检查编码器联轴器是否打滑。若仍波动，可记录 600 / 800 / 1200 m/min 三档波形做对比。\n\n` +
          `> 本范围只检索笔记来源；张力锥度 P-04.12 等参数定义需切换到「全部手册」或「全部知识库」。`);
      }
    } else if (topic === 'fault') {
      if (hasTopicNote) {
        parts.push(`在「${label}」内找到相关的故障处理记录。\n\n` +
          `**${src(topicNote, pool).title}**（${src(topicNote, pool).meta}）：${src(topicNote, pool).snippet}${cite(topicNote)}\n\n` +
          `记录中没有直接出现 Agito 的报警定义；建议先确认报警对象是驱动器还是运动控制器，再切换到手册范围检索对应故障码。`);
      }
    } else if (topic === 'comm') {
      if (hasTopicNote) {
        parts.push(`在「${label}」内找到 1 条通讯掉线记录。\n\n` +
          `**${src(topicNote, pool).title}**（${src(topicNote, pool).meta}）：${src(topicNote, pool).snippet}${cite(topicNote)}\n\n` +
          `建议先做物理层检查（屏蔽层、走线、交换机端口），再核对 PDO / Sync 周期。厂家手册中的状态字与周期定义需要切换到「全部手册」。`);
      }
    } else {
      // generic：当前项目笔记优先 + 关键词命中
      const ids = [];
      if (ctx && pool['s_' + ctx.id] && allow.has('s_' + ctx.id)) ids.push('s_' + ctx.id);
      noteKeywordHits(q, allow, pool, ids).forEach((id) => { if (ids.length < 5) ids.push(id); });
      if (ids.length) {
        const bullets = ids.map((id) => `- **${src(id, pool).title}**：${src(id, pool).snippet}${cite(id)}`).join('\n');
        parts.push(`在「${label}」内找到 ${ids.length} 条相关记录：\n\n${bullets}\n\n` +
          `如需具体参数或故障码定义，可把范围切换到「全部手册」，或在问题里补充设备型号。`);
      }
    }

    // 通用兜底：在全部勾选笔记中按关键词补充命中（排除已引用）
    const extra = noteKeywordHits(q, allow, pool, S);
    if (extra.length) {
      const ebullets = extra.map((id) => `- **${src(id, pool).title}**（${src(id, pool).section}）：${src(id, pool).snippet}${cite(id)}`).join('\n');
      if (!parts.length) parts.push(`在「${label}」的笔记中找到以下相关内容：`);
      parts.push(`\n\n另在勾选笔记中找到 ${extra.length} 条相关：\n${ebullets}`);
    }

    if (!parts.length) return notFound(label, q);
    return { text: parts.join('\n\n'), sources: S, fragments: [], topic };
  }

  /* ---------- 手册范围回答（不变，只引用 MANUAL_IDS） ---------- */
  function manualAnswer(topic, allow, res) {
    const S = [];
    const parts = [];
    const add = (id, line) => {
      if (!allow.has(id)) return false;
      S.push(id);
      parts.push(`- ${line} {{${S.length - 1}}}`);
      return true;
    };
    const label = res.label;

    if (topic === 'tension') {
      parts.push(`在「${label}」中找到以下与张力波动相关的章节：`);
      add('s_m1', `**张力锥度**：P-04.12 设定收卷过程中的张力递减比例（0–100%，出厂 50%）；手册建议先在低速段完成基础整定，再验证高速段。`);
      add('s_m1b', `**卷径补偿**：内部计算模式需正确设置初卷径与材料厚度；编码器分辨率错误会导致卷径同比例偏差。`);
      add('s_m2', `**同步与通讯**：若为 EtherCAT 轴，PDO / Sync 周期应与主站任务周期一致；干扰环境可调整为 1 ms。`);
      if (parts.length === 1) return notFound(label, '张力波动');
      parts.push(`> 手册给的是参数与排查依据；具体现场处理记录请切换到「全部知识库」或在笔记范围内提问。`);
    } else if (topic === 'fault') {
      parts.push(`在「${label}」中找到与 E-07 / Bus Error 相关的章节：`);
      add('s_m4b', `**E-07 过热保护**：依次检查电源模块温度、风扇转速、散热风道；面板温度持续超过 75℃ 应停机。`);
      add('s_m4', `**Bus Error**：对应状态字 bit 12，常见原因是从站掉线、PDO 周期不匹配、线缆屏蔽失效；建议先导出诊断日志再复位。`);
      if (parts.length === 1) return notFound(label, '故障码');
      parts.push(`> 不同厂家的 E-07 含义可能不同，请先确认对象品牌后再按对应章节处理。`);
    } else if (topic === 'comm') {
      parts.push(`在「${label}」中找到以下通讯配置依据：`);
      add('s_m2', `**PDO 刷新周期**：建议与主站任务周期一致；干扰环境下可将 Sync 周期调整为 1 ms，并检查屏蔽层接地。`);
      add('s_m3', `**通讯参数配置**：SPiiPlus 支持 EtherCAT 与 CANopen，PDO 映射偏移需在初始化阶段一次性配置，修改后必须重启通讯栈。`);
      add('s_m4', `**Bus Error 排查**：状态字 bit 12 置位时，优先检查物理层、PDO 周期和线缆屏蔽。`);
      if (parts.length === 1) return notFound(label, '通讯问题');
    } else {
      parts.push(`在「${label}」中找到以下常用依据：`);
      add('s_m1', `**张力参数组**：P-04.10 卷径补偿模式、P-04.12 张力锥度系数、P-04.13 张力滤波时间。`);
      add('s_m3', `**编程与通讯**：PDO 映射初始化后不可热改，修改需重启通讯栈。`);
      add('s_m4b', `**过热故障**：E-07 触发后先检查风扇转速与散热风道，再复位。`);
      if (parts.length === 1) return notFound(label, '相关内容');
      parts.push(`> 在问题中补充型号、参数号或故障码，可获得更精确的手册章节。`);
    }
    return { text: parts.join('\n'), sources: S, fragments: [], topic };
  }

  /* ---------- 全库回答：复用预置文本并按范围裁剪来源 + 勾选笔记关键词兜底 ---------- */
  function allAnswer(topic, allow, res, pool, q) {
    const base = DATA.answers[topic] || DATA.answers.generic;
    const orig = base.sources;
    const kept = orig.filter((id) => allow.has(id));
    const idx = new Map(orig.map((id, i) => [i, kept.indexOf(id)]));
    let text = base.text.replace(/\{\{(\d+)\}\}/g, (m, d) => {
      const k = idx.get(Number(d));
      return k != null && k >= 0 ? `{{${k}}}` : '';
    });

    const S = kept.slice();
    const extra = noteKeywordHits(q, allow, pool, S);
    if (extra.length) {
      const ebullets = extra.map((id) => {
        const n = S.length; S.push(id);
        return `- **${src(id, pool).title}**（${src(id, pool).section}）：${src(id, pool).snippet}{{${n}}}`;
      }).join('\n');
      text += `\n\n另在勾选笔记中找到 ${extra.length} 条相关：\n${ebullets}`;
    }
    text += `\n\n> 范围：${res.label} · 命中的来源均已标注。`;
    if (!kept.length && !extra.length) return notFound(res.label, q);
    return { text, sources: S, fragments: [], topic };
  }

  function build(q, res, ctx) {
    const pool = buildPool();
    const topic = detectTopic(q);
    const allow = allowed(res, ctx, pool);
    let out;
    if (res.kind === 'all') out = allAnswer(topic, allow, res, pool, q);
    else if (res.kind === 'manuals' || res.kind === 'brands') out = manualAnswer(topic, allow, res);
    else out = noteAnswer(topic, allow, res, ctx, pool, q);

    if (!out.noResult) {
      const relevant = RELEVANT[topic] || [];
      const hit = relevant.some((id) => allow.has(id)) || topic === 'generic' || out.sources.length > 0;
      if (!hit) out = notFound(res.label, q);
    }
    // 片段：优先用预置片段，按范围过滤；不足时用来源原文补齐
    if (!out.noResult) {
      let frags = (DATA.fragments[topic] || []).filter((f) => allow.has(f.sid));
      if (!frags.length) {
        frags = out.sources.slice(0, 4).map((id) => ({ sid: id, text: (pool[id] || {}).snippet || '' }));
      } else {
        const missing = out.sources.filter((id) => !frags.some((f) => f.sid === id)).slice(0, 2);
        missing.forEach((id) => frags.push({ sid: id, text: (pool[id] || {}).snippet || '' }));
      }
      out.fragments = frags;
    }
    return out;
  }

  function compareData() { return U.clone(DATA.compare); }

  /* ---------- 供知识库页面使用的笔记资源 API ---------- */
  function vaultSelected() {
    const pool = buildPool();
    const out = [];
    Object.keys(pool).forEach((id) => {
      const s = pool[id];
      if (!s || s.type !== 'note') return;
      let path = s.path;
      if (!path && s.noteId) { const n = U.Store.note(s.noteId); path = n && n.path; }
      if (!path || !isNoteSelected(path)) return;
      out.push({
        sourceId: id, path,
        name: path.split('/').pop(),
        title: s.title,
        updated: (s.meta || '').replace(/^笔记 · /, '').replace(/^售后记录 · /, ''),
        snippet: s.snippet,
        section: s.section,
        note: s.noteId ? U.Store.note(s.noteId) : null
      });
    });
    return out.sort((a, b) => (b.updated || '').localeCompare(a.updated || ''));
  }

  function removeNoteFromIndex(path) {
    const cfg = U.Store.state.config || {};
    const s = cfg.indexSelection || (cfg.indexSelection = { folders: ['客户售后问题'], files: [], excludedFiles: [], excludedFolders: [] });
    s.files = (s.files || []).filter((p) => p !== path);
    if ((s.excludedFiles || []).indexOf(path) < 0) s.excludedFiles.push(path);
    U.Store.save();
  }

  window.RagEngine = {
    build, detectTopic, allowed, compareData,
    NOTE_IDS, MANUAL_IDS,
    buildPool, isNoteSelected, selectedNoteIds, vaultSelected, removeNoteFromIndex, extractSnippet, noteKeywordHits
  };
})();
