/* Typecho 归档台 — 前端应用
 * 同一份代码同时服务于本地服务端模式（fetch API）与静态报告模式（内嵌数据）。 */
(() => {
  'use strict';

  const MODE = window.__MODE__ || 'server';
  const SERVER = MODE === 'server';

  let A = null;                 // 归档数据
  let RECORDS = null;           // 原始记录索引（服务端按需加载）
  const S = {
    nav: 'overview', view: 'overview', q: '', ftype: '',
    fstatus: '', sel: null, selScroll: true, rec: null, exported: null,
  };

  const $ = (s, r = document) => r.querySelector(s);
  const el = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const nf = n => Number(n || 0).toLocaleString('en-US');
  const trunc = (s, n) => {
    s = String(s ?? '');
    return s.length > n ? s.slice(0, n) + '…' : s;
  };
  const bytes = n => {
    n = Number(n || 0);
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1073741824) return (n / 1048576).toFixed(2) + ' MB';
    return (n / 1073741824).toFixed(2) + ' GB';
  };

  /* ----------------------------------------------------------- 资源地址 */
  function assetUrl(p) {
    if (!p) return '';
    if (!SERVER) return p;
    return '/api/upload?p=' + encodeURIComponent(p);
  }

  /* ----------------------------------------------------------- Markdown */
  const BLOCK_TAGS = /^(div|p|section|article|figure|figcaption|iframe|video|audio|table|thead|tbody|tr|td|th|ul|ol|li|blockquote|pre|h[1-6]|img|hr|br|span|a|center|font|strong|em|b|i|u|s|del|ins|sup|sub|code|dl|dt|dd|details|summary|picture|source)\b/i;

  function sanitize(html) {
    return html
      .replace(/<script[\s\S]*?<\/script>/gi, '')
      .replace(/<style[\s\S]*?<\/style>/gi, '')
      .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
      .replace(/javascript:/gi, '');
  }

  function inlineMd(s) {
    let t = esc(s);
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g,
      (m, alt, src, title) => `<img src="${assetUrl(src)}" alt="${alt}"${title ? ` title="${title}"` : ''}>`);
    t = t.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g,
      (m, txt, href, title) => `<a href="${href}"${title ? ` title="${title}"` : ''} target="_blank" rel="noreferrer">${txt}</a>`);
    t = t.replace(/(^|[\s(])(https?:\/\/[^\s<>()]+)/g,
      (m, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`);
    t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    t = t.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    return t;
  }

  function mdInlineMixed(s) {
    const tags = [];
    const prot = String(s).replace(/<[^>]+>/g, m => { tags.push(m); return `\u0000${tags.length - 1}\u0000`; });
    const out = inlineMd(prot).replace(/\u0000(\d+)\u0000/g, (m, i) => tags[+i] || '');
    return sanitize(out);
  }

  function md(src) {
    if (!src) return '';
    let text = String(src).replace(/^\s*<!--\s*markdown\s*-->\s*/i, '');
    text = text.replace(/\r\n?/g, '\n');

    // Typecho「Spoiler」插件：把 [note …] … [/note] 收成提示块
    const notes = [];
    text = text.replace(/\[note([^\]]*)\]([\s\S]*?)\[\/note\]/gi, (m, attrs, body) => {
      const type = /danger/i.test(attrs) ? 'danger' : /info/i.test(attrs) ? 'info' : 'warning';
      notes.push({ type, body: body.trim() });
      return `\n\n\u0001NOTE${notes.length - 1}\u0001\n\n`;
    });

    const lines = text.split('\n');
    const out = [];
    let i = 0;

    while (i < lines.length) {
      let line = lines[i];

      if (/^\s*$/.test(line)) { i++; continue; }

      // 占位的 note 提示块
      const noteHit = line.match(/^\u0001NOTE(\d+)\u0001$/);
      if (noteHit) {
        const note = notes[Number(noteHit[1])];
        i++;
        if (note) {
          const label = note.type === 'danger' ? '重要提示'
            : note.type === 'info' ? '说明' : '内容提示';
          out.push(`<div class="note ${note.type}"><div class="note-h">${label}</div>`
            + `<div class="note-b">${mdInlineMixed(note.body)}</div></div>`);
        }
        continue;
      }

      // 作者用作分隔的裸 !!!
      if (/^\s*!!!\s*$/.test(line)) { i++; continue; }

      // 遮罩遮罩：<!--SPOILER … -->
      if (/^\s*<!--\s*SPOILER/i.test(line)) {
        const buf = [];
        let cur = line.replace(/^\s*<!--\s*SPOILER/i, '');
        if (cur.trim()) buf.push(cur.replace(/-->\s*$/, ''));
        while (i + 1 < lines.length && !/-->/.test(lines[i])) {
          i++;
          buf.push(lines[i].replace(/-->\s*$/, ''));
        }
        i++;
        const body = buf.join('\n').trim();
        out.push(`<details class="spoiler"><summary>内容提示 · 点击展开</summary>`
          + `<div class="sp-body">${esc(body).replace(/\n/g, '<br>')}</div></details>`);
        continue;
      }

      // 代码块
      const fence = line.match(/^\s*(```|~~~)\s*([\w+-]*)\s*$/);
      if (fence) {
        const mark = fence[1];
        i++;
        const buf = [];
        while (i < lines.length && !new RegExp('^\\s*' + mark).test(lines[i])) buf.push(lines[i++]);
        i++;
        out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
        continue;
      }
      // 分割线
      if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) { out.push('<hr>'); i++; continue; }
      // 标题（缩进多少都认：真实文章里经常整段缩进）
      const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
      if (h) { const n = h[1].length; out.push(`<h${n}>${inlineMd(h[2])}</h${n}>`); i++; continue; }
      // 引用
      if (/^\s*>\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
        out.push(`<blockquote>${md(buf.join('\n'))}</blockquote>`);
        continue;
      }
      // 表格
      if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const cells = r => r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
        const head = cells(line);
        i += 2;
        const body = [];
        while (i < lines.length && /\|/.test(lines[i]) && !/^\s*$/.test(lines[i])) body.push(cells(lines[i++]));
        out.push('<table><thead><tr>' + head.map(c => `<th>${inlineMd(c)}</th>`).join('') +
          '</tr></thead><tbody>' + body.map(r => '<tr>' + r.map(c => `<td>${inlineMd(c)}</td>`).join('') + '</tr>').join('') +
          '</tbody></table>');
        continue;
      }
      // 无序列表
      if (/^\s*[-*+]\s+/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*[-*+]\s+/, ''));
        out.push('<ul>' + buf.map(t => `<li>${inlineMd(t)}</li>`).join('') + '</ul>');
        continue;
      }
      // 有序列表
      if (/^\s*\d+[.)]\s+/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ''));
        out.push('<ol>' + buf.map(t => `<li>${inlineMd(t)}</li>`).join('') + '</ol>');
        continue;
      }
      // 原始 HTML 块
      if (/^\s*<[a-z!/]/i.test(line) && BLOCK_TAGS.test(line.trim().replace(/^</, ''))) {
        const buf = [];
        while (i < lines.length && !/^\s*$/.test(lines[i])) buf.push(lines[i++]);
        out.push(sanitize(buf.join('\n')));
        continue;
      }
      // 段落
      const buf = [];
      while (i < lines.length && !/^\s*$/.test(lines[i]) &&
        !/^\s*(#{1,6}\s|>|```|~~~|[-*+]\s|\d+[.)]\s)/.test(lines[i])) {
        buf.push(lines[i++]);
      }
      // 兜底：上面的分支都没接住、但停止条件又命中时，必须把当前行吃掉。
      // 否则 i 不前进，整个渲染会卡死在这里（这是真发生过的死循环）。
      if (!buf.length) buf.push(lines[i++]);
      out.push(`<p>${inlineMd(buf.join('\n')).replace(/\n/g, '<br>')}</p>`);
    }
    return sanitize(out.join('\n'));
  }

  function renderBody(post) {
    if (post.type === 'attachment') {
      const meta = parseJson(post.text);
      return meta ? attachmentCard(post, meta) : `<p class="dim">该附件没有附加元数据。</p>`;
    }
    if (post.isMarkdown) return md(post.text);
    return sanitize(String(post.text || ''));
  }

  function parseJson(t) {
    try { return t && t.trim().startsWith('{') ? JSON.parse(t) : null; } catch { return null; }
  }

  function attachmentCard(post, meta) {
    const isImg = meta.isImage || /^image\//.test(meta.mime || '');
    const url = assetUrl(meta.path || meta.url || '');
    return `<dl class="kv">
      <dt>文件名</dt><dd>${esc(meta.name || post.title)}</dd>
      <dt>路径</dt><dd class="mono">${esc(meta.path || '—')}</dd>
      <dt>大小</dt><dd>${bytes(meta.size)}</dd>
      <dt>类型</dt><dd>${esc(meta.mime || meta.type || '—')}</dd>
      <dt>所属内容</dt><dd>${post.parent ? `<a href="#" data-goto="${post.parent}">#${post.parent}</a>` : '—'}</dd>
    </dl>${isImg && url ? `<div style="margin-top:16px"><img src="${url}" alt="" style="max-width:100%;border-radius:8px"></div>` : ''}`;
  }

  /* ----------------------------------------------------------- 导航 */
  const NAV = () => [
    { group: '浏览', items: [
      { id: 'overview', label: '概览', ico: '◉' },
      { id: 'posts', label: '文章', ico: '▤', n: A.stats.counts.posts },
      { id: 'pages', label: '独立页面', ico: '▭', n: A.stats.counts.pages },
      { id: 'attachments', label: '附件', ico: '▣', n: A.stats.counts.attachments },
    ] },
    { group: '互动', items: [
      { id: 'comments', label: '评论', ico: '❝', n: A.stats.counts.comments },
      { id: 'users', label: '用户', ico: '☺', n: A.stats.counts.users },
    ] },
    { group: '组织', items: [
      { id: 'categories', label: '分类', ico: '❖', n: A.stats.counts.categories },
      { id: 'tags', label: '标签', ico: '❯', n: A.stats.counts.tags },
    ] },
    { group: '文件', items: [
      { id: 'fileinfo', label: '数据来源', ico: '⌗' },
      ...(A.schema && Object.keys(A.schema).length
        ? [{ id: 'schema', label: '字段结构', ico: '⊞' }] : []),
      ...(A.file.kind === 'dat' && SERVER
        ? [{ id: 'records', label: '原始记录', ico: '⋮' }] : []),
    ] },
    { group: '源码', items: [
      ...(A.site ? [{ id: 'source', label: '站点信息', ico: '⚙' }] : []),
      { id: 'integrity', label: '体检', ico: '✓',
        n: A.integrity.filter(x => x.level === 'warn' || x.level === 'error').length || '' },
      ...(A.diff ? [{ id: 'diff', label: '两边对账', ico: '⇄' }] : []),
    ] },
    ...(SERVER ? [{ group: '输出', items: [{ id: 'export', label: '导出', ico: '↧' }] }] : []),
  ];

  function navIdToView(id) {
    if (id === 'posts') return { view: 'posts', ftype: 'post' };
    if (id === 'pages') return { view: 'posts', ftype: 'page' };
    if (id === 'attachments') return { view: 'posts', ftype: 'attachment' };
    if (id === 'categories') return { view: 'metas', ftype: 'category' };
    if (id === 'tags') return { view: 'metas', ftype: 'tag' };
    return { view: id, ftype: '' };
  }

  function renderNav() {
    el('nav').innerHTML = NAV().map(g => `
      <div class="nav-group">${g.group}</div>
      ${g.items.map(it => `<div class="nav-item${S.nav === it.id ? ' on' : ''}" data-nav="${it.id}">
        <span class="ico">${it.ico}</span><span>${it.label}</span>
        ${it.n ? `<span class="n">${nf(it.n)}</span>` : ''}
      </div>`).join('')}`).join('');
  }

  /* ----------------------------------------------------------- 头部 */
  const TITLES = {
    overview: ['概览', '备份与站点的整体情况'],
    posts: ['文章', '按时间倒序排列'],
    comments: ['评论', '全部读者的留言'],
    metas: ['分类与标签', ''],
    users: ['用户', '站点的作者与订阅者'],
    source: ['站点信息', '来自源码包的配置、主题与插件'],
    integrity: ['体检', '完整性校验与数据质量检查'],
    diff: ['两边对账', '备份文件与源码包数据库的差异'],
    fileinfo: ['数据来源', '这份数据是从哪读来的'],
    schema: ['字段结构', '每张表在备份文件中的字段布局'],
    records: ['原始记录', '逐条查看备份中的二进制记录'],
    export: ['导出', '把备份转换成其它格式'],
  };

  function setHeader() {
    let [t, s] = TITLES[S.view] || ['', ''];
    if (S.view === 'posts') {
      const map = { post: '文章', page: '独立页面', attachment: '附件' };
      t = map[S.ftype] || '全部内容';
      s = `共 ${nf(filteredPosts().length)} 条`;
    } else if (S.view === 'metas') {
      t = S.ftype === 'tag' ? '标签' : '分类';
      s = `共 ${nf((S.ftype === 'tag' ? A.metas.tags : A.metas.categories).length)} 个`;
    }
    el('viewTitle').textContent = t;
    el('viewSub').textContent = s;
  }

  /* ----------------------------------------------------------- 过滤 */
  function matchQ(hay, q) { return hay.toLowerCase().includes(q); }

  function filteredPosts() {
    const q = S.q.trim().toLowerCase();
    return A.posts.filter(p => {
      if (S.ftype && p.type !== S.ftype) return false;
      if (S.fstatus && p.status !== S.fstatus) return false;
      if (!q) return true;
      const hay = [
        p.title, p.slug, p.author, p.excerpt, p.text || '',
        p.categories.map(c => c.name).join(' '),
        p.tags.map(t => t.name).join(' '),
        Object.values(p.fields || {}).join(' '),
      ].join('\n');
      return matchQ(hay, q);
    });
  }

  /* ----------------------------------------------------------- 通用片段 */
  function badges(p) {
    const cls = p.status === 'publish' ? 'pub' : p.status === 'private' ? 'priv' : 'hid';
    return `<span class="badge ${cls}">${esc(p.statusLabel)}</span>`;
  }

  function rowPost(p) {
    return `<div class="row${S.sel === p.cid ? ' on' : ''}" data-cid="${p.cid}">
      <div class="row-t">${esc(p.title)}</div>
      <div class="row-m">
        <span>${esc(p.createdText.slice(0, 10))}</span>
        ${p.status !== 'publish' ? badges(p) : ''}
        <span>${p.type === 'attachment' ? bytes(p.fileSize) : nf(p.chars) + ' 字'}</span>
        ${p.commentsNum ? `<span>${p.commentsNum} 评</span>` : ''}
        ${p.categories.length ? `<span class="tag">${esc(p.categories[0].name)}</span>` : ''}
      </div>
      ${p.excerpt ? `<div class="row-e">${esc(p.excerpt)}</div>` : ''}
    </div>`;
  }

  function statCard(k, v, u = '') {
    return `<div class="card"><div class="k">${k}</div><div class="v">${v}<span class="u">${u}</span></div></div>`;
  }

  function split(listHtml, detailHtml, headHtml = '') {
    return `<div class="split">
      <div class="listpane">
        <div class="list-head">${headHtml}</div>
        <div class="list-scroll">${listHtml}</div>
      </div>
      <div class="detail">${detailHtml}</div>
    </div>`;
  }

  /* ----------------------------------------------------------- 概览 */
  function viewOverview() {
    const s = A.stats, c = s.counts, f = A.file, site = A.site;
    const years = Object.entries(s.years);
    const maxY = Math.max(1, ...years.map(y => y[1]));
    const siteTitle = (site && site.options && site.options.title)
      || (f.origin || f.path).split(/[\\/]/).pop() || '归档数据';
    const bad = A.integrity.filter(i => i.level === 'error').length;
    const warn = A.integrity.filter(i => i.level === 'warn').length;

    return `
    <div class="page">
      <div class="sect">
        <h3>${esc(siteTitle)}</h3>
        <p class="hint">${esc(s.span.first)} — ${esc(s.span.last)} · 跨度 ${nf(s.span.days)} 天 ·
          平均每篇 ${nf(s.words.avgPerPost)} 字${site ? ` · ${esc(site.version || '')}` : ''}</p>
        <div class="cards">
          ${statCard('文章', nf(c.posts))}
          ${statCard('独立页面', nf(c.pages))}
          ${statCard('附件', nf(c.attachments))}
          ${statCard('评论', nf(c.comments))}
          ${statCard('分类 / 标签', nf(c.categories) + ' / ' + nf(c.tags))}
          ${statCard('用户', nf(c.users))}
        </div>
      </div>

      <div class="sect two">
        <div>
          <h3>按年份分布</h3>
          <p class="hint">文章与独立页面的创建时间</p>
          <div class="bars">
            ${years.map(([y, n]) => `<div class="b" title="${y}: ${n}">
              <em>${n}</em><i style="height:${Math.max(3, (n / maxY) * 78)}%"></i><span>${y}</span></div>`).join('')}
          </div>
        </div>
        <div>
          <h3>常用标签</h3>
          <p class="hint">按关联数量排序</p>
          <div class="tags-cloud">
            ${s.topTags.slice(0, 16).map(t =>
              `<span class="t" data-tag="${esc(t.name)}">${esc(t.name)}<b>${t.count}</b></span>`).join('')}
          </div>
        </div>
      </div>

      <div class="sect two">
        <div>
          <h3>最近更新</h3>
          <p class="hint">最新的 6 篇内容</p>
          <table class="grid"><tbody>
            ${A.posts.filter(p => p.type !== 'attachment').slice(0, 6).map(p => `
              <tr data-cid="${p.cid}" style="cursor:pointer">
                <td class="ell">${esc(p.title)}</td>
                <td class="dim" style="white-space:nowrap">${esc(p.createdText.slice(0, 10))}</td>
              </tr>`).join('')}
          </tbody></table>
        </div>
        <div>
          <h3>分类构成</h3>
          <p class="hint">按文章数量排序</p>
          <table class="grid"><tbody>
            ${s.topCategories.slice(0, 6).map(x => `
              <tr><td>${esc(x.name)}</td>
                  <td class="num">${x.count}</td></tr>`).join('')}
          </tbody></table>
        </div>
      </div>

      <div class="sect">
        <h3>数据来源与体检</h3>
        <p class="hint">${esc(f.origin || f.path)}</p>
        <dl class="kv">
          <dt>来源类型</dt><dd>${f.kind === 'dat' ? '备份文件 .dat'
            : (A.site ? '源码包内的 SQLite 数据库' : 'SQLite 数据库')}</dd>
          <dt>文件大小</dt><dd>${bytes(f.size)}</dd>
          ${f.kind === 'dat' ? `<dt>格式版本</dt><dd class="mono">${esc(f.version)}</dd>` : ''}
          <dt>记录条数</dt><dd>${nf(f.records)} 条（${Object.entries(f.tables).map(([k, v]) => `${k} ${v}`).join(' · ')}）</dd>
          ${f.kind === 'dat' ? `<dt>校验状态</dt><dd>${f.md5_failed
            ? `<span style="color:var(--err)">${f.md5_failed} 条记录损坏</span>`
            : `<span style="color:var(--ok)">全部 ${nf(f.md5_ok)} 条记录校验通过</span>`}</dd>
          <dt>文件尾</dt><dd>${f.footer_ok ? '完整' : '缺失（可能被截断）'}</dd>` : ''}
          <dt>体检结论</dt><dd>${bad ? `<span style="color:var(--err)">${bad} 项错误</span>` : '无错误'}
            ${warn ? ` · <span style="color:var(--warn)">${warn} 项提醒</span>` : ''}</dd>
        </dl>
      </div>
    </div>`;
  }

  /* ----------------------------------------------------------- 内容列表 */
  function viewPosts() {
    const rows = filteredPosts();
    const statuses = [['', '全部状态'], ['publish', '已发布'], ['hidden', '草稿'], ['private', '私密']];
    const types = [['', '全部'], ['post', '文章'], ['page', '页面'], ['attachment', '附件']];
    const head = `
      ${types.map(([v, l]) => `<span class="chip${S.ftype === v ? ' on' : ''}" data-ftype="${v}">${l}</span>`).join('')}
      <span class="count-note">${nf(rows.length)} 条</span>
      <div style="flex:1 1 100%;display:flex;gap:6px;margin-top:2px">
        ${statuses.map(([v, l]) => `<span class="chip${S.fstatus === v ? ' on' : ''}" data-fstatus="${v}">${l}</span>`).join('')}
      </div>`;
    const list = rows.length
      ? rows.slice(0, 400).map(rowPost).join('') +
        (rows.length > 400 ? `<div class="empty">仅显示前 400 条，请用搜索缩小范围</div>` : '')
      : `<div class="empty">没有匹配的内容</div>`;
    return split(list, S.sel != null ? detailPost(S.sel) : emptyDetail('从左侧选择一篇内容'), head);
  }

  function emptyDetail(msg) {
    return `<div class="empty">${msg}</div>`;
  }

  /* ----------------------------------------------------------- 单篇导出
   * 在浏览界面里把当前文章存成独立文件。两种模式共用同一份实现：
   * 服务端模式与静态报告都只是页面，生成与下载全在浏览器里完成。
   */

  const EXPORT_CSS = `
* { box-sizing: border-box; }
body { color-scheme: light; margin: 0; background: #f6f4ef; color: #23201b;
  font: 16px/1.85 "Segoe UI", "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased; }
.wrap { max-width: 780px; margin: 0 auto; padding: 52px 24px 72px; }
h1 { font-size: 30px; line-height: 1.36; margin: 0 0 10px; letter-spacing: -.3px; }
.meta { margin: 0 0 10px; font-size: 13px; color: #5a5349; }
.chips { margin: 0 0 30px; display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 12px; padding: 1px 9px; border-radius: 999px;
  background: #efece4; border: 1px solid #e2ddd1; color: #5a5349; }
.prose { font-size: 15.5px; }
.prose h1, .prose h2, .prose h3, .prose h4 { margin: 1.6em 0 .6em; line-height: 1.4; }
.prose h2 { font-size: 1.28em; } .prose h3 { font-size: 1.1em; }
.prose p { margin: 0 0 1.05em; }
.prose a { color: #b45309; text-decoration: none; border-bottom: 1px solid rgba(180,83,9,.35); }
.prose img { max-width: 100%; border-radius: 8px; display: block; margin: 1em 0; }
.prose blockquote { margin: 1em 0; padding: .4em 1em; border-left: 3px solid #ddd7c9;
  color: #5a5349; background: #efece4; border-radius: 0 6px 6px 0; }
.prose code { font: .88em ui-monospace, Consolas, monospace; background: #efece4;
  padding: .12em .38em; border-radius: 4px; border: 1px solid #ece8de; }
.prose pre { background: #efece4; border: 1px solid #e2ddd1; border-radius: 8px;
  padding: 12px 14px; overflow-x: auto; margin: 1em 0; }
.prose pre code { background: none; border: 0; padding: 0; font-size: 13px; line-height: 1.7; }
.prose table { border-collapse: collapse; margin: 1em 0; font-size: 14px; }
.prose th, .prose td { border: 1px solid #e2ddd1; padding: 5px 10px; }
.prose th { background: #efece4; }
.prose hr { border: 0; border-top: 1px solid #e2ddd1; margin: 2em 0; }
.prose ul, .prose ol { padding-left: 1.5em; margin: 0 0 1em; }
.note { border: 1px solid #e2ddd1; border-left-width: 3px; border-radius: 8px;
  padding: 9px 14px; margin: 1.2em 0; background: #efece4; }
.note.warning { border-left-color: #a8641a; }
.note.danger { border-left-color: #a33a34; }
.note.info { border-left-color: #45688f; }
.note-h { font-size: 12px; font-weight: 600; margin-bottom: 3px; color: #5a5349; }
.note-b { font-size: 14px; color: #5a5349; }
.spoiler { border: 1px dashed #ddd7c9; border-radius: 8px; padding: 10px 14px; background: #efece4; }
.sp-body { margin-top: 8px; font-size: 13.5px; color: #5a5349; }
.comments { margin-top: 56px; border-top: 1px solid #e2ddd1; padding-top: 24px; }
.comments h2 { font-size: 16px; margin: 0 0 14px; }
.cmt { border-top: 1px solid #ece8de; padding: 9px 0; }
.cmt .h { display: flex; gap: 8px; align-items: baseline; font-size: 13px; color: #8b8377; }
.cmt .h b { color: #23201b; }
.cmt .x { margin-top: 3px; white-space: pre-wrap; color: #5a5349; font-size: 14px; }
.cmt .kids { margin-left: 16px; padding-left: 12px; border-left: 1px solid #ece8de; }\n.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
footer { margin-top: 56px; padding-top: 14px; border-top: 1px solid #e2ddd1;
  font-size: 12px; color: #8b8377; }
body.dark { color-scheme: dark; background: #14130f; color: #ece7dc; }
body.dark .meta, body.dark .cmt .x, body.dark .note-b, body.dark .sp-body { color: #b6ae9f; }
body.dark .chip, body.dark .prose code, body.dark .prose pre,
body.dark .prose th, body.dark .note, body.dark .spoiler { background: #1e1c18; border-color: #2e2b25; }
body.dark .prose blockquote { background: #1b1a16; border-left-color: #2e2b25; }
body.dark .prose th, body.dark .prose td, body.dark .cmt,
body.dark .comments, body.dark footer { border-color: #2e2b25; }
body.dark .cmt .h, body.dark footer { color: #847c6e; }
body.dark .cmt .h b, body.dark .prose th { color: #ece7dc; }`;

  function yamlScalar(value) {
    if (value === null || value === undefined || value === '') return 'null';
    if (typeof value === 'number') return String(value);
    const text = String(value);
    if (/^(null|true|false|yes|no|on|off|~)$/i.test(text) || /^[\d.+-]+$/.test(text)
      || /[:#\[\]{}&*!|>'"%@`\n]/.test(text)) {
      return JSON.stringify(text);
    }
    return text;
  }

  function yamlField(key, value) {
    if (Array.isArray(value)) {
      if (!value.length) return `${key}: []`;
      return `${key}: ` + value.map(item => `\n  - ${yamlScalar(item)}`).join('');
    }
    return `${key}: ${yamlScalar(value)}`;
  }

  function articleBaseName(post) {
    const slug = String(post.slug || post.cid)
      .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 80);
    const date = (post.createdText || '').slice(0, 10);
    return date ? `${date}-${slug}` : slug;
  }

  function buildMarkdown(post) {
    const lines = [
      yamlField('title', post.title),
      yamlField('cid', post.cid),
      yamlField('slug', post.slug),
      yamlField('type', post.type),
      yamlField('status', post.status),
      yamlField('date', post.createdText),
      yamlField('modified', post.modifiedText),
      yamlField('author', post.author),
      yamlField('categories', post.categories.map(c => c.name)),
      yamlField('tags', post.tags.map(t => t.name)),
      yamlField('template', post.template),
      yamlField('comments', post.commentsNum),
      yamlField('word_count', post.chars),
    ];
    const body = String(post.text || '').replace(/^\s*<!--\s*markdown\s*-->\s*/i, '');
    return '---\n' + lines.join('\n') + '\n---\n\n' + body + '\n';
  }

  async function inlineImages(root) {
    const images = [...root.querySelectorAll('img')];
    await Promise.all(images.map(async img => {
      const src = img.getAttribute('src') || '';
      if (!src || src.startsWith('data:')) return;
      try {
        const res = await fetch(src);
        if (!res.ok) return;
        const blob = await res.blob();
        if (!blob.size || blob.size > 4 * 1024 * 1024) return;
        const dataUrl = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        });
        img.setAttribute('src', dataUrl);
      } catch {
        /* 取不到就保留原地址 */
      }
    }));
  }

  function sourceLabel() {
    const file = A.file || {};
    return String(file.origin || file.path || '').split(/[\\/]/).pop() || '';
  }

  async function buildStandaloneHtml(post) {
    const holder = document.createElement('div');
    holder.innerHTML = renderBody(post);
    await inlineImages(holder);

    const comments = A.comments.filter(c => c.cid === post.cid);
    const commentHtml = comments.length
      ? `<section class="comments"><h2>评论 ${comments.length}</h2>${buildCommentTree(comments, false)}</section>`
      : '';

    const meta = [
      post.createdText,
      post.author ? `作者 ${post.author}` : '',
      post.type === 'attachment' ? '' : `${nf(post.chars)} 字`,
      post.typeLabel,
    ].filter(Boolean).map(esc).join(' · ');

    const chips = [...post.categories.map(c => c.name), ...post.tags.map(t => t.name)];
    const chipsHtml = chips.length
      ? `<p class="chips">${chips.map(name => `<span class="chip">${esc(name)}</span>`).join('')}</p>`
      : '';

    const stamp = new Date().toLocaleString('sv-SE').slice(0, 16);
    // 导出的样子跟着当前界面走，而不是跟着系统偏好
    const dark = document.documentElement.dataset.theme === 'dark';

    return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(post.title)}</title>
<style>${EXPORT_CSS}</style>
</head>
<body${dark ? ' class="dark"' : ''}>
<article class="wrap">
  <header>
    <h1>${esc(post.title)}</h1>
    <p class="meta">${meta}</p>
    ${chipsHtml}
  </header>
  <div class="prose">${holder.innerHTML}</div>
  ${commentHtml}
  <footer>由 Typecho 归档台导出 · ${esc(sourceLabel())} · cid ${post.cid} · ${esc(stamp)}</footer>
</article>
</body>
</html>`;
  }

  function saveBlob(name, text, mime) {
    const blob = new Blob([text], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(url); link.remove(); }, 2000);
  }

  let toastTimer = null;
  function toast(message) {
    let node = document.getElementById('toast');
    if (!node) {
      node = document.createElement('div');
      node.id = 'toast';
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 2600);
  }

  async function downloadArticle(cid, format) {
    const post = A.posts.find(x => x.cid === cid);
    if (!post) { toast('找不到这篇文章'); return; }
    const base = articleBaseName(post);
    try {
      if (format === 'md') {
        saveBlob(`${base}.md`, buildMarkdown(post), 'text/markdown');
        toast(`已保存 ${base}.md`);
        return;
      }
      toast('正在打包图片…');
      const html = await buildStandaloneHtml(post);
      saveBlob(`${base}.html`, html, 'text/html');
      const count = A.comments.filter(c => c.cid === cid).length;
      toast(`已保存 ${base}.html${count ? `（含 ${count} 条评论）` : ''}`);
    } catch (err) {
      toast(`导出失败：${err.message}`);
    }
  }

  function detailPost(cid) {
    const p = A.posts.find(x => x.cid === cid);
    if (!p) return emptyDetail('内容不存在');
    const cmts = A.comments.filter(c => c.cid === cid);
    const atts = A.posts.filter(x => x.type === 'attachment' && x.parent === cid);
    const fields = Object.entries(p.fields || {});
    const tree = buildCommentTree(cmts);

    return `<div class="article">
      <h2 class="a-title">${esc(p.title)}</h2>
      <div class="a-meta">
        <span>${esc(p.createdText)}</span>
        ${p.modifiedText && p.modifiedText !== p.createdText ? `<span>修订于 ${esc(p.modifiedText)}</span>` : ''}
        ${p.author ? `<span>作者 <b>${esc(p.author)}</b></span>` : ''}
        <span>${p.type === 'attachment' ? bytes(p.fileSize) : nf(p.chars) + ' 字'}</span>
        <span>${esc(p.typeLabel)}</span>
        ${badges(p)}
        ${p.hasPassword ? '<span class="badge priv">加密</span>' : ''}
        ${p.template ? `<span class="mono dim">${esc(p.template)}</span>` : ''}
        ${p.type === 'attachment' ? '' : `<div class="a-actions">
          <span class="dim">下载</span>
          <button class="chip" data-dl="html" data-dlc="${p.cid}" title="保存为可独立打开的网页">HTML</button>
          <button class="chip" data-dl="md" data-dlc="${p.cid}" title="保存为带 front matter 的 Markdown">Markdown</button>
        </div>`}
      </div>
      ${(p.categories.length || p.tags.length) ? `<div class="a-chips">
        ${p.categories.map(c => `<span class="a-chip cat">${esc(c.name)}</span>`).join('')}
        ${p.tags.map(t => `<span class="a-chip">${esc(t.name)}</span>`).join('')}
      </div>` : ''}
      <div class="prose">${renderBody(p)}</div>

      ${fields.length ? `<div class="sect" style="padding:22px 0 0">
        <h3>自定义字段</h3>
        <table class="grid fields-t"><tbody>
          ${fields.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v || '—')}</td></tr>`).join('')}
        </tbody></table>
      </div>` : ''}

      ${atts.length ? `<div class="sect" style="padding:22px 0 0">
        <h3>附件 ${atts.length}</h3>
        <div class="thumbs">
          ${atts.map(a => {
            const meta = parseJson(a.text) || {};
            const img = meta.isImage || /^image\//.test(meta.mime || '');
            return `<div class="thumb" data-cid="${a.cid}" style="cursor:pointer">
              ${img ? `<img src="${assetUrl(meta.path || '')}" alt="">` : '<div style="height:84px;display:grid;place-items:center;font-size:22px">▣</div>'}
              <span title="${esc(meta.name || a.title)}">${esc(meta.name || a.title)}</span>
            </div>`;
          }).join('')}
        </div>
      </div>` : ''}

      ${cmts.length ? `<div class="sect" style="padding:26px 0 0">
        <h3>评论 ${cmts.length}</h3>
        ${tree}
      </div>` : ''}

      <div class="sect" style="padding:26px 0 0">
        <span class="pill dim mono">cid ${cid} · slug ${esc(p.slug || '—')} · 评论 ${p.commentsNum}</span>
      </div>
    </div>`;
  }

  function buildCommentTree(list, showIp = true) {
    const byParent = new Map();
    list.forEach(c => {
      const k = c.parent || 0;
      if (!byParent.has(k)) byParent.set(k, []);
      byParent.get(k).push(c);
    });
    const walk = (pid, depth) => (byParent.get(pid) || []).map(c => `
      <div class="cmt">
        <div class="h"><b>${esc(c.author)}</b>
          <span>${esc(c.createdText)}</span>
          ${c.status !== 'approved' ? `<span class="badge hid">${esc(c.status)}</span>` : ''}
          ${showIp && c.ip ? `<span class="mono" style="font-size:10px">${esc(c.ip)}</span>` : ''}
        </div>
        <div class="x">${esc(c.text)}</div>
        ${depth < 3 ? `<div class="kids">${walk(c.coid, depth + 1)}</div>` : ''}
      </div>`).join('');
    return walk(0, 0) || '<div class="empty">无</div>';
  }

  /* ----------------------------------------------------------- 评论 */
  function filteredComments() {
    const q = S.q.trim().toLowerCase();
    return A.comments.filter(c => !q ||
      [c.author, c.text, c.postTitle, c.mail, c.ip].join('\n').toLowerCase().includes(q));
  }

  function viewComments() {
    const rows = filteredComments();
    const head = `<span class="count-note" style="margin-left:0">${nf(rows.length)} 条</span>`;
    const list = rows.slice(0, 400).map(c => `
      <div class="row${S.sel === c.coid ? ' on' : ''}" data-coid="${c.coid}">
        <div class="row-t">${esc(c.author)} <span class="dim" style="font-weight:400">评论了</span> ${esc(c.postTitle || '(未知文章)')}</div>
        <div class="row-m"><span>${esc(c.createdText)}</span>${c.ip ? `<span class="mono">${esc(c.ip)}</span>` : ''}</div>
        <div class="row-e">${esc(c.text)}</div>
      </div>`).join('') || `<div class="empty">没有匹配的评论</div>`;
    return split(list, S.sel != null ? detailComment(S.sel) : emptyDetail('从左侧选择一条评论'), head);
  }

  function detailComment(coid) {
    const c = A.comments.find(x => x.coid === coid);
    if (!c) return emptyDetail('评论不存在');
    const post = A.posts.find(p => p.cid === c.cid);
    const siblings = A.comments.filter(x => x.cid === c.cid).sort((a, b) => a.created - b.created);
    const idx = siblings.findIndex(x => x.coid === coid);
    const prev = siblings[idx - 1], next = siblings[idx + 1];
    return `<div class="article">
      <h2 class="a-title" style="font-size:19px">${esc(c.author)}</h2>
      <div class="a-meta">
        <span>${esc(c.createdText)}</span>
        ${c.mail ? `<span>${esc(c.mail)}</span>` : ''}
        ${c.url ? `<span>${esc(c.url)}</span>` : ''}
        ${c.ip ? `<span class="mono">${esc(c.ip)}</span>` : ''}
        <span>${esc(c.status)} · ${esc(c.type)}</span>
      </div>
      <div class="prose" style="font-size:14.5px">${esc(c.text).replace(/\n/g, '<br>')}</div>
      <div class="sect" style="padding:22px 0 0">
        <h3>所属内容</h3>
        <p><a href="#" data-goto="${c.cid}">${esc(c.postTitle || '(未知)')}</a></p>
      </div>
      <div class="sect" style="padding:16px 0 0">
        <h3>浏览器</h3>
        <p class="dim mono" style="font-size:11.5px">${esc(c.agent || '—')}</p>
      </div>
      <div class="sect" style="padding:16px 0 0;display:flex;gap:14px">
        ${prev ? `<a href="#" data-coid="${prev.coid}">← 上一条</a>` : ''}
        ${next ? `<a href="#" data-coid="${next.coid}">下一条 →</a>` : ''}
      </div>
    </div>`;
  }

  /* ----------------------------------------------------------- 分类/标签 */
  function viewMetas() {
    const isTag = S.ftype === 'tag';
    const rows = isTag ? A.metas.tags : A.metas.categories;
    const q = S.q.trim().toLowerCase();
    const shown = rows.filter(r => !q || (r.name + r.slug + r.description).toLowerCase().includes(q));
    const head = `<span class="count-note" style="margin-left:0">${nf(shown.length)} 个</span>`;
    const list = shown.map(m => `
      <div class="row${S.sel === m.mid ? ' on' : ''}" data-mid="${m.mid}">
        <div class="row-t">${esc(m.name)}</div>
        <div class="row-m"><span class="mono">${esc(m.slug)}</span><span>${m.actual} 篇</span>
          ${m.count !== m.actual ? `<span class="dim">记录值 ${m.count}</span>` : ''}</div>
        ${m.description ? `<div class="row-e">${esc(m.description)}</div>` : ''}
      </div>`).join('') || `<div class="empty">没有匹配项</div>`;
    return split(list, S.sel != null ? detailMeta(S.sel) : emptyDetail('从左侧选择一个分类或标签'), head);
  }

  function detailMeta(mid) {
    const all = [...A.metas.categories, ...A.metas.tags];
    const m = all.find(x => x.mid === mid);
    if (!m) return emptyDetail('不存在');
    const posts = A.posts.filter(p => (p.categories.concat(p.tags)).some(x => x.mid === mid));
    return `<div class="article">
      <h2 class="a-title">${esc(m.name)}</h2>
      <div class="a-meta">
        <span>${m.type === 'tag' ? '标签' : '分类'}</span>
        <span class="mono">${esc(m.slug)}</span>
        <span>${posts.length} 篇内容</span>
        ${m.count !== posts.length ? `<span class="dim">缓存计数 ${m.count}</span>` : ''}
      </div>
      ${m.description ? `<p class="dim">${esc(m.description)}</p>` : ''}
      <table class="grid"><tbody>
        ${posts.map(p => `<tr data-goto="${p.cid}" style="cursor:pointer">
          <td class="ell" style="max-width:520px">${esc(p.title)}</td>
          <td class="dim" style="white-space:nowrap">${esc(p.createdText.slice(0, 10))}</td>
          <td class="num dim">${p.type === 'attachment' ? bytes(p.fileSize) : nf(p.chars) + ' 字'}</td>
        </tr>`).join('') || '<tr><td class="dim">暂无内容</td></tr>'}
      </tbody></table>
    </div>`;
  }

  /* ----------------------------------------------------------- 用户 */
  function viewUsers() {
    const q = S.q.trim().toLowerCase();
    const rows = A.users.filter(u => !q ||
      [u.name, u.screenName, u.mail, u.groupLabel].join(' ').toLowerCase().includes(q));
    const head = `<span class="count-note" style="margin-left:0">${nf(rows.length)} 人</span>`;
    const list = rows.map(u => `
      <div class="row${S.sel === u.uid ? ' on' : ''}" data-uid="${u.uid}">
        <div class="row-t">${esc(u.screenName || u.name)}</div>
        <div class="row-m"><span class="badge">${esc(u.groupLabel)}</span>
          ${u.posts ? `<span>${u.posts} 篇</span>` : ''}${u.comments ? `<span>${u.comments} 评</span>` : ''}</div>
      </div>`).join('') || `<div class="empty">没有匹配用户</div>`;
    return split(list, S.sel != null ? detailUser(S.sel) : emptyDetail('从左侧选择一位用户'), head);
  }

  function detailUser(uid) {
    const u = A.users.find(x => x.uid === uid);
    if (!u) return emptyDetail('用户不存在');
    const posts = A.posts.filter(p => p.authorId === uid);
    return `<div class="article">
      <h2 class="a-title">${esc(u.screenName || u.name)}</h2>
      <div class="a-meta"><span>${esc(u.groupLabel)}</span>
        <span class="mono">${esc(u.name)}</span>
        ${u.mail ? `<span>${esc(u.mail)}</span>` : ''}
        <span>注册于 ${esc(u.createdText.slice(0, 10))}</span></div>
      <dl class="kv" style="margin-bottom:20px">
        <dt>用户 ID</dt><dd>${u.uid}</dd>
        <dt>个人网址</dt><dd>${esc(u.url || '—')}</dd>
        <dt>最后登录</dt><dd>${esc(u.loggedText || '—')}</dd>
        <dt>发文 / 评论</dt><dd>${posts.length} / ${u.comments}</dd>
        <dt>凭据</dt><dd>${u.hasAuthCode ? '有 authCode' : '无 authCode'}（密码为不可逆哈希，未导出）</dd>
      </dl>
      <h3 style="font-size:13px;margin:0 0 8px">发布的内容</h3>
      <table class="grid"><tbody>
        ${posts.map(p => `<tr data-goto="${p.cid}" style="cursor:pointer">
          <td class="ell" style="max-width:520px">${esc(p.title)}</td>
          <td class="dim" style="white-space:nowrap">${esc(p.createdText.slice(0, 10))}</td>
          <td class="num dim">${esc(p.typeLabel)}</td></tr>`).join('') ||
          '<tr><td class="dim">无</td></tr>'}
      </tbody></table>
    </div>`;
  }

  /* ----------------------------------------------------------- 备份文件 */
  function viewFileInfo() {
    const f = A.file;
    const tables = f.tables;
    const isDat = f.kind === 'dat';
    const kindLabel = isDat ? '备份文件 .dat' : 'SQLite 数据库';
    const cards = isDat
      ? `${statCard('大小', bytes(f.size).split(' ')[0], bytes(f.size).split(' ')[1])}
         ${statCard('记录', nf(f.records))}
         ${statCard('校验通过', nf(f.md5_ok))}
         ${statCard('格式版本', esc(f.version))}`
      : `${statCard('大小', bytes(f.size).split(' ')[0], bytes(f.size).split(' ')[1])}
         ${statCard('记录', nf(f.records))}
         ${statCard('数据表', String(Object.keys(tables).length))}
         ${statCard('来源', '数据库')}`;
    return `<div class="page">
      <div class="sect">
        <h3>${kindLabel}</h3>
        <p class="hint">${esc(f.origin || f.path)}</p>
        <div class="cards">${cards}</div>
        ${isDat ? '' : `<p class="hint" style="margin-top:12px">
          数据直接读自 <span class="mono">${esc((f.origin || f.path).split(/[\\/]/).pop())}</span>，
          字段按备份白名单对齐，因此浏览、体检、导出与读取 .dat 备份得到的结果结构完全一致。</p>`}
      </div>
      <div class="sect">
        <h3>数据构成</h3>
        <p class="hint">${isDat
          ? '按记录类型统计；类型编号由 <span class="mono">Widget\\Backup</span> 定义'
          : '按数据表统计'}</p>
        <table class="grid">
          <thead><tr><th>${isDat ? '类型编号' : '序号'}</th><th>表</th>
            <th class="num">记录数</th><th class="num">字段数</th></tr></thead>
          <tbody>
          ${Object.entries(tables).map(([name, n], i) => {
            const ids = { contents: 1, comments: 2, metas: 3, relationships: 4, users: 5, fields: 6 };
            const avg = A.schema[name] ? A.schema[name].fields.length : '';
            return `<tr><td class="mono">${isDat ? (ids[name] ?? '?') : i + 1}</td><td>${name}</td>
              <td class="num">${nf(n)}</td><td class="num dim">${avg}</td></tr>`;
          }).join('')}
          </tbody>
        </table>
      </div>
      ${isDat ? `<div class="sect">
        <h3>格式说明</h3>
        <p class="hint">文件 = 21 字节魔数 + 若干记录 + 21 字节魔数；记录之间无分隔符</p>
        <table class="grid">
          <thead><tr><th>区段</th><th>长度</th><th>说明</th></tr></thead>
          <tbody>
            <tr><td>魔数</td><td class="mono">21</td><td><span class="mono">%TYPECHO_BACKUP_0001%</span>，文件头与文件尾各一份</td></tr>
            <tr><td>type</td><td class="mono">uint16</td><td>记录类型，1–6 对应六张表</td></tr>
            <tr><td>headerLen</td><td class="mono">uint16</td><td>JSON 字段表的字节数</td></tr>
            <tr><td>bodyLen</td><td class="mono">uint32</td><td>正文区总字节数，等于字段长度之和</td></tr>
            <tr><td>header</td><td class="mono">变长</td><td>JSON 对象，<span class="mono">{"字段名": 字节长度 | null}</span></td></tr>
            <tr><td>body</td><td class="mono">bodyLen</td><td>字段原始字节顺序拼接，null 不占字节</td></tr>
            <tr><td>md5</td><td class="mono">32</td><td>前三个区段的 MD5 十六进制文本</td></tr>
          </tbody>
        </table>
      </div>` : ''}
      ${f.warnings && f.warnings.length ? `<div class="sect"><h3>解析告警</h3>
        <ul class="dim" style="font-size:12px">${f.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
    </div>`;
  }

  /* ----------------------------------------------------------- 字段结构 */
  function viewSchema() {
    const names = Object.keys(A.schema);
    return `<div class="page">
      ${names.map(t => `
        <div class="sect">
          <h3>${t} <span class="dim" style="font-weight:400;font-size:11.5px">${nf(A.schema[t].rows)} 条记录 · ${A.schema[t].fields.length} 个字段</span></h3>
          <p class="hint">字段顺序即 body 中的拼接顺序</p>
          <table class="grid">
            <thead><tr><th>#</th><th>字段</th><th class="num">总字节</th><th class="num">NULL 数</th></tr></thead>
            <tbody>${A.schema[t].fields.map((f, i) => `<tr>
              <td class="dim mono">${i}</td><td class="mono">${esc(f.name)}</td>
              <td class="num">${nf(f.bytes)}</td><td class="num ${f.nulls ? '' : 'dim'}">${nf(f.nulls)}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`).join('')}
    </div>`;
  }

  /* ----------------------------------------------------------- 源码 */
  function viewSource() {
    const s = A.site;
    if (!s) return emptyDetail('未关联源码包');
    const d = s.db || {};
    return `<div class="page">
      <div class="sect">
        <h3>站点</h3>
        <p class="hint">${esc(s.origin || s.root)}${s.temporary ? ' · 已临时解包' : ''}</p>
        <div class="cards">
          ${statCard('版本', esc(s.version || '—'))}
          ${statCard('主题', esc(s.options.theme || '—'))}
          ${statCard('插件', nf(s.plugins.filter(p => p.activated).length), '/ ' + nf(s.plugins.length))}
          ${statCard('文件', nf(s.files.files))}
        </div>
      </div>

      <div class="sect two">
        <div>
          <h3>基本信息</h3>
          <p class="hint">来自 SQLite 的 options 表</p>
          <dl class="kv">
            <dt>站点标题</dt><dd>${esc(s.options.title || '—')}</dd>
            <dt>站点描述</dt><dd>${esc(s.options.description || '—')}</dd>
            <dt>站点地址</dt><dd class="mono">${esc(s.options.siteUrl || '—')}</dd>
            <dt>后台目录</dt><dd class="mono">${esc(s.admin_dir)}</dd>
            <dt>时区</dt><dd>UTC+${(Number(s.options.timezone || 28800) / 3600)}</dd>
            <dt>Markdown</dt><dd>${s.options.markdown === '1' ? '开启' : '关闭'}</dd>
            <dt>XML-RPC</dt><dd>${s.options.allowXmlRpc === '1' ? '开启' : '关闭'}</dd>
            <dt>开放注册</dt><dd>${s.options.allowRegister === '1' ? '是' : '否'}</dd>
            <dt>数据库</dt><dd class="mono">${esc(d.adapter || '—')} · ${bytes(d.size)}</dd>
          </dl>
        </div>
        <div>
          <h3>文件构成</h3>
          <p class="hint">PHP ${nf(s.files.php_files)} 个，共 ${nf(s.files.php_lines)} 行</p>
          <table class="grid"><tbody>
            ${Object.entries(s.files.by_ext).map(([k, v]) =>
              `<tr><td class="mono">${esc(k)}</td><td class="num">${nf(v)}</td></tr>`).join('')}
          </tbody></table>
        </div>
      </div>

      <div class="sect">
        <h3>主题</h3>
        <p class="hint">${esc((s.config && s.config.theme_dir) || '/usr/themes')} 下的目录</p>
        <table class="grid">
          <thead><tr><th>目录</th><th>名称</th><th>状态</th><th class="num">文件</th><th class="num">大小</th></tr></thead>
          <tbody>${s.themes.map(t => `<tr>
            <td class="mono">${esc(t.name)}</td>
            <td title="${esc(t.title)}">${esc(trunc(t.title, 74))}${t.author ? `<span class="dim"> · ${esc(trunc(t.author, 20))}</span>` : ''}</td>
            <td>${t.active ? '<span class="pill"><i class="dot"></i>启用中</span>' : '<span class="dim">未启用</span>'}</td>
            <td class="num">${t.files}</td><td class="num dim">${bytes(t.size)}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>

      <div class="sect">
        <h3>插件</h3>
        <p class="hint">${esc((s.config && s.config.plugin_dir) || '/usr/plugins')} 下的目录</p>
        <table class="grid">
          <thead><tr><th>目录</th><th>名称</th><th>状态</th><th class="num">文件</th></tr></thead>
          <tbody>${s.plugins.map(p => `<tr>
            <td class="mono">${esc(p.name)}</td><td>${esc(p.title)}</td>
            <td>${p.activated ? '<span class="pill"><i class="dot"></i>已启用</span>' : '<span class="dim">未启用</span>'}</td>
            <td class="num">${p.files}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>

      <div class="sect two">
        <div>
          <h3>上传附件</h3>
          <p class="hint">${nf(s.uploads.count)} 个文件，共 ${bytes(s.uploads.size)}</p>
          <table class="grid"><tbody>
            ${Object.entries(s.uploads.by_year).map(([y, n]) =>
              `<tr><td class="mono">${esc(y)}</td><td class="num">${nf(n)}</td></tr>`).join('')}
          </tbody></table>
        </div>
        <div>
          <h3>路由</h3>
          <p class="hint">自定义固定链接结构（${s.routing.length} 条）</p>
          <table class="grid"><tbody>
            ${s.routing.slice(0, 14).map(r => `<tr>
              <td class="mono dim" style="width:96px">${esc(r.name)}</td>
              <td class="mono ell" style="max-width:260px">${esc(r.url)}</td></tr>`).join('')}
          </tbody></table>
        </div>
      </div>

      ${s.warnings.length ? `<div class="sect"><h3>解析提示</h3>
        <ul class="dim" style="font-size:12px">${s.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
    </div>`;
  }

  /* ----------------------------------------------------------- 体检 */
  function viewIntegrity() {
    const order = { error: 0, warn: 1, info: 2, ok: 3 };
    const list = [...A.integrity].sort((a, b) => order[a.level] - order[b.level]);
    const counts = { error: 0, warn: 0, info: 0, ok: 0 };
    A.integrity.forEach(i => counts[i.level] = (counts[i.level] || 0) + 1);
    return `<div class="page">
      <div class="sect">
        <h3>结论</h3>
        <p class="hint">逐条校验备份文件的完整性与数据的一致性</p>
        <div class="cards">
          ${statCard('错误', nf(counts.error))}
          ${statCard('提醒', nf(counts.warn))}
          ${statCard('提示', nf(counts.info))}
          ${statCard('通过', nf(counts.ok))}
        </div>
      </div>
      <div class="sect">
        ${list.map(i => `<div class="issue ${i.level}">
          <div class="t"><span class="lv ${i.level}">${i.level.toUpperCase()}</span>${esc(i.title)}</div>
          <div class="d">${esc(i.detail)}</div>
          ${i.items.length ? `<ul>${i.items.slice(0, 12).map(x => `<li>${esc(x.text)}</li>`).join('')}
            ${i.items.length > 12 ? `<li class="dim">… 另有 ${i.items.length - 12} 项</li>` : ''}</ul>` : ''}
        </div>`).join('')}
      </div>
    </div>`;
  }

  /* ----------------------------------------------------------- 对账 */
  function viewDiff() {
    if (!A.diff) return emptyDetail('未关联源码包，无法对账');
    const labels = {
      contents: '内容', comments: '评论', metas: '分类标签', users: '用户',
      relationships: '关联', fields: '自定义字段',
    };
    return `<div class="page">
      <div class="sect">
        <h3>备份 vs 源码包数据库</h3>
        <p class="hint">${esc((A.site && A.site.origin) || '')} › ${esc(A.diff.dbPath.split(/[\\/]/).pop())} · 以主键比对，找出只有一边存在的数据</p>
        <table class="grid">
          <thead><tr><th>表</th><th class="num">备份</th><th class="num">数据库</th>
            <th class="num">仅备份有</th><th class="num">仅数据库有</th></tr></thead>
          <tbody>${Object.entries(A.diff.tables).map(([name, t]) => `<tr>
            <td>${labels[name] || name} <span class="mono dim" style="margin-left:6px">${name}</span></td>
            <td class="num">${nf(t.backup)}</td>
            <td class="num">${nf(t.db)}</td>
            <td class="num" style="${t.onlyBackupCount ? 'color:var(--accent);font-weight:600' : ''}">${nf(t.onlyBackupCount)}</td>
            <td class="num" style="${t.onlyDbCount ? 'color:var(--accent);font-weight:600' : ''}">${nf(t.onlyDbCount)}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>
      ${Object.entries(A.diff.tables).filter(([n, t]) =>
        (t.onlyBackupDetail && t.onlyBackupDetail.length) || (t.onlyDbDetail && t.onlyDbDetail.length)).map(([name, t]) => `
        <div class="sect">
          <h3>${labels[name] || name}</h3>
          ${t.onlyBackupDetail && t.onlyBackupDetail.length ? `
            <p class="hint">只存在于备份中（源码包生成之后新增或修改）</p>
            <table class="grid"><tbody>${t.onlyBackupDetail.map(x =>
              `<tr><td class="mono dim" style="width:90px">${esc(String(x.id))}</td><td>${esc(x.text)}</td></tr>`).join('')}</tbody></table>` : ''}
          ${t.onlyDbDetail && t.onlyDbDetail.length ? `
            <p class="hint">只存在于数据库（备份中缺失）</p>
            <table class="grid"><tbody>${t.onlyDbDetail.map(x =>
              `<tr><td class="mono dim" style="width:90px">${esc(String(x.id))}</td><td>${esc(x.text)}</td></tr>`).join('')}</tbody></table>` : ''}
        </div>`).join('')}
    </div>`;
  }

  /* ----------------------------------------------------------- 原始记录 */
  async function viewRecords() {
    if (RECORDS === null) {
      const body = el('body');
      body.innerHTML = '<div class="loading">正在读取记录索引…</div>';
      RECORDS = await (await fetch('/api/records')).json();
    }
    const q = S.q.trim().toLowerCase();
    const rows = RECORDS.filter(r => !q ||
      String(r.seq).includes(q) || (r.table || '').toLowerCase().includes(q) ||
      (r.preview || '').toLowerCase().includes(q));
    const head = `<span class="count-note" style="margin-left:0">${nf(rows.length)} 条</span>`;
    const list = rows.slice(0, 500).map(r => `
      <div class="row${S.rec === r.seq ? ' on' : ''}" data-seq="${r.seq}">
        <div class="row-t"><span class="mono dim">#${r.seq}</span> ${esc(r.table || 'type ' + r.typeId)}
          <span class="dim" style="font-weight:400;font-size:11px">${r.bodyLen} B</span></div>
        <div class="row-m"><span class="mono">@${nf(r.offset)}</span>
          <span>${r.fields} 字段</span>
          <span style="color:${r.md5Ok === false ? 'var(--err)' : 'var(--ok)'}">${r.md5Ok === false ? 'md5 失败' : 'md5 通过'}</span></div>
        ${r.preview ? `<div class="row-e">${esc(r.preview)}</div>` : ''}
      </div>`).join('');
    return split(list, S.rec != null ? await detailRecord(S.rec) : emptyDetail('从左侧选择一条记录'), head);
  }

  async function detailRecord(seq) {
    const r = await (await fetch('/api/record/' + seq)).json();
    if (!r || r.error) return emptyDetail('记录不存在');
    return `<div class="article">
      <h2 class="a-title" style="font-size:19px">记录 #${r.seq}
        <span class="dim" style="font-size:13px;font-weight:400">${esc(r.table || 'type ' + r.typeId)}</span></h2>
      <div class="a-meta">
        <span>类型 ${r.typeId}</span><span>偏移 ${nf(r.offset)}</span>
        <span>正文 ${r.bodyLen} B</span><span>编码后 ${bytes(r.size)}</span>
        <span style="color:${r.md5Ok === false ? 'var(--err)' : 'var(--ok)'}">${r.md5Ok === false ? 'md5 校验失败' : 'md5 校验通过'}</span>
      </div>
      <h3 style="font-size:13px;margin:18px 0 8px">字段</h3>
      <table class="grid">
        <thead><tr><th>#</th><th>字段</th><th class="num">字节</th><th>值</th></tr></thead>
        <tbody>${r.fields.map((f, i) => `<tr>
          <td class="dim mono">${i}</td>
          <td class="mono">${esc(f.name)}</td>
          <td class="num">${f.null ? 'NULL' : nf(f.len)}</td>
          <td class="ell" style="max-width:440px">${f.null ? '<span class="dim">NULL</span>' : esc(f.preview)}</td>
        </tr>`).join('')}</tbody>
      </table>
      <h3 style="font-size:13px;margin:22px 0 8px">二进制</h3>
      <div class="hex">${esc(r.hex)}</div>
      <p class="hint" style="margin-top:8px">前 1024 字节；md5 区段位于记录末尾 32 字节。</p>
    </div>`;
  }

  /* ----------------------------------------------------------- 导出 */
  function viewExport() {
    const items = [
      ['markdown', 'Markdown 归档', '每篇内容一个 .md，带 YAML front matter；评论另存为 JSON'],
      ['sqlite', '重建 SQLite', '生成可直接作为 Typecho 数据库的 typecho-rebuilt.db'],
      ['json', '完整 JSON', '包含全部内容、评论、分类、用户与体检结果的数据包'],
      ['csv', 'CSV 表格', '内容、评论、分类、标签、用户等逐表导出，Excel 可直接打开'],
      ['html', '离线 HTML 报告', '单文件，双击即用，可搜索筛选阅读，无需联网'],
      ['bundle', '全部打包', '以上所有格式打成一个 zip'],
    ];
    return `<div class="page">
      <div class="sect">
        <h3>导出</h3>
        <p class="hint">生成的文件会另存，不会修改原始备份</p>
        <table class="grid">
          <thead><tr><th>格式</th><th>内容</th><th></th></tr></thead>
          <tbody>${items.map(([kind, name, desc]) => `<tr>
            <td style="width:150px;font-weight:550">${name}</td>
            <td class="dim">${desc}</td>
            <td style="width:90px;text-align:right">
              <button class="chip" data-export="${kind}">生成</button></td>
          </tr>`).join('')}</tbody>
        </table>
      </div>
      <div class="sect" id="exportOut">
        <p class="hint">选择一种格式开始生成。</p>
      </div>
    </div>`;
  }

  async function doExport(kind, btn) {
    const out = el('exportOut');
    if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
    try {
      const res = await fetch('/api/export?kind=' + kind, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || '导出失败');
      out.innerHTML = `<h3>已生成</h3>
        <p class="hint">${esc(data.path || '')}</p>
        <dl class="kv">
          <dt>文件</dt><dd>${esc(data.file || '')}</dd>
          <dt>大小</dt><dd>${bytes(data.size)}</dd>
          ${data.detail ? `<dt>内容</dt><dd>${esc(data.detail)}</dd>` : ''}
        </dl>
        <p style="margin-top:14px"><a href="${data.download}" class="chip">下载 ${esc(data.file || '')}</a></p>`;
    } catch (e) {
      out.innerHTML = `<h3>导出失败</h3><p class="hint" style="color:var(--err)">${esc(e.message)}</p>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '生成'; }
    }
  }

  /* ----------------------------------------------------------- 深链接 */
  function syncHash() {
    if (!SERVER) return;
    const parts = [S.nav];
    if (S.sel != null) parts.push('s' + S.sel);
    if (S.rec != null) parts.push('r' + S.rec);
    try { history.replaceState(null, '', '#' + parts.map(encodeURIComponent).join('/')); } catch { }
  }

  function applyHash() {
    const raw = (location.hash || '').replace(/^#\/?/, '');
    if (!raw) return false;
    const parts = raw.split('/').filter(Boolean).map(decodeURIComponent);
    const nav = parts[0];
    const known = NAV().some(g => g.items.some(it => it.id === nav));
    if (!known) return false;
    S.nav = nav;
    const t = navIdToView(nav);
    S.view = t.view; S.ftype = t.ftype;
    for (const p of parts.slice(1)) {
      if (p.startsWith('s')) S.sel = Number(p.slice(1));
      else if (p.startsWith('r')) S.rec = Number(p.slice(1));
    }
    // 原始记录视图用 rec，不占 sel
    if (S.view === 'records' && S.rec == null && S.sel != null) {
      S.rec = S.sel; S.sel = null;
    }
    return true;
  }

  /* ----------------------------------------------------------- 主渲染 */
  function render() {
    setHeader();
    syncHash();
    const body = el('body');
    switch (S.view) {
      case 'overview': body.innerHTML = viewOverview(); break;
      case 'posts': body.innerHTML = viewPosts(); break;
      case 'comments': body.innerHTML = viewComments(); break;
      case 'metas': body.innerHTML = viewMetas(); break;
      case 'users': body.innerHTML = viewUsers(); break;
      case 'fileinfo': body.innerHTML = viewFileInfo(); break;
      case 'schema': body.innerHTML = viewSchema(); break;
      case 'source': body.innerHTML = viewSource(); break;
      case 'integrity': body.innerHTML = viewIntegrity(); break;
      case 'diff': body.innerHTML = viewDiff(); break;
      case 'export': body.innerHTML = viewExport(); break;
      case 'records':
        body.innerHTML = '<div class="loading">载入中…</div>';
        viewRecords().then(html => { body.innerHTML = html; }).catch(e => {
          body.innerHTML = `<div class="empty">读取记录失败：${esc(e.message)}</div>`;
        });
        break;
      default: body.innerHTML = emptyDetail('');
    }
  }

  function rerender() {
    const ls = $('.list-scroll'), ds = $('.detail');
    const lt = ls ? ls.scrollTop : 0, dt = ds ? ds.scrollTop : 0;
    render();
    const ls2 = $('.list-scroll');
    if (ls2) ls2.scrollTop = lt;
    const ds2 = $('.detail');
    if (ds2) ds2.scrollTop = S.selScroll ? 0 : dt;
    S.selScroll = true;
  }

  /* ----------------------------------------------------------- 事件 */
  function bind() {
    document.addEventListener('click', e => {
      const nav = e.target.closest('[data-nav]');
      if (nav) {
        const id = nav.dataset.nav;
        S.nav = id;
        const t = navIdToView(id);
        S.view = t.view; S.ftype = t.ftype;
        S.sel = null; S.rec = null;
        renderNav(); rerender();
        return;
      }
      const ftype = e.target.closest('[data-ftype]');
      if (ftype) { S.ftype = ftype.dataset.ftype; S.sel = null; rerender(); return; }
      const fstatus = e.target.closest('[data-fstatus]');
      if (fstatus) { S.fstatus = fstatus.dataset.fstatus; S.sel = null; rerender(); return; }

      const dl = e.target.closest('[data-dl]');
      if (dl) {
        e.preventDefault();
        downloadArticle(Number(dl.dataset.dlc), dl.dataset.dl);
        return;
      }

      const cid = e.target.closest('[data-cid]');
      if (cid) { S.sel = Number(cid.dataset.cid); S.selScroll = true; rerender(); return; }
      const coid = e.target.closest('[data-coid]');
      if (coid) { S.sel = Number(coid.dataset.coid); S.selScroll = true; rerender(); return; }
      const mid = e.target.closest('[data-mid]');
      if (mid) { S.sel = Number(mid.dataset.mid); S.selScroll = true; rerender(); return; }
      const uid = e.target.closest('[data-uid]');
      if (uid) { S.sel = Number(uid.dataset.uid); S.selScroll = true; rerender(); return; }
      const seq = e.target.closest('[data-seq]');
      if (seq) { S.rec = Number(seq.dataset.seq); rerender(); return; }

      const goto = e.target.closest('[data-goto]');
      if (goto) {
        e.preventDefault();
        S.nav = 'posts'; S.view = 'posts'; S.ftype = '';
        S.sel = Number(goto.dataset.goto);
        renderNav(); rerender();
        return;
      }
      const tag = e.target.closest('[data-tag]');
      if (tag) {
        S.q = tag.dataset.tag; el('q').value = S.q;
        S.nav = 'tags'; S.view = 'metas'; S.ftype = 'tag'; S.sel = null;
        renderNav(); rerender();
        return;
      }
      const exp = e.target.closest('[data-export]');
      if (exp) { doExport(exp.dataset.export, exp); return; }
    });

    let timer = null;
    el('q').addEventListener('input', e => {
      clearTimeout(timer);
      const v = e.target.value;
      timer = setTimeout(() => { S.q = v; rerender(); }, 140);
    });

    document.addEventListener('keydown', e => {
      if (e.key === '/' && document.activeElement !== el('q')) {
        e.preventDefault(); el('q').focus();
      } else if (e.key === 'Escape' && document.activeElement === el('q')) {
        el('q').value = ''; S.q = ''; el('q').blur(); rerender();
      }
    });

    el('themeBtn').addEventListener('click', () => {
      const cur = document.documentElement.dataset.theme;
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('tarch-theme', next); } catch { }
    });

    // 图片加载失败时隐藏，避免离线报告里出现破图
    document.addEventListener('error', e => {
      if (e.target && e.target.tagName === 'IMG') e.target.style.display = 'none';
    }, true);
  }

  /* ----------------------------------------------------------- 启动 */
  function setBrand() {
    const site = A.site;
    const title = (site && site.options && site.options.title) || '';
    el('brandSub').textContent = title || ((A.file.origin || A.file.path).split(/[\\/]/).pop() || '');
    el('sideMeta').textContent = bytes(A.file.size);
    document.title = title ? `${title} · 归档台` : 'Typecho 归档台';
  }

  async function boot() {
    try {
      A = window.__ARCHIVE__ || await (await fetch('/api/archive')).json();
    } catch (e) {
      el('body').innerHTML = `<div class="empty">无法载入数据：${esc(e.message)}</div>`;
      return;
    }
    if (A.error) { el('body').innerHTML = `<div class="empty">${esc(A.error)}</div>`; return; }
    try {
      const t = localStorage.getItem('tarch-theme');
      if (t) document.documentElement.dataset.theme = t;
    } catch { }
    setBrand();
    applyHash();
    renderNav();
    bind();
    render();
    window.addEventListener('hashchange', () => {
      if (applyHash()) { renderNav(); rerender(); }
    });
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
