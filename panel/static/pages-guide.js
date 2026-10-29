/* The Guide page (static/pages-guide.js): the guides, drawn from their Markdown. Loaded the first time the page
   opens (Pages in panel.js). */
// --- Guide page: a central guide library (a Strapi site), or the guides that came with this install ---
// Markdown is rendered after escaping every character, and only http(s) links are made, so a guide can't
// inject anything into the page.
function mdInline(t) {
  return t.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function renderMarkdown(md) {
  const out = [];
  let para = [], depth = -1;
  const flush = () => { if (para.length) { out.push(`<p>${mdInline(para.join(' '))}</p>`); para = []; } };
  const close = to => { while (depth > to) { out.push('</ul>'); depth--; } };
  for (const raw of escapeHtml(md).split('\n')) {
    const h = raw.match(/^(#{1,3})\s+(.*)$/), li = raw.match(/^(\s*)- (.*)$/);
    if (h) { flush(); close(-1); out.push(`<h${h[1].length}>${mdInline(h[2])}</h${h[1].length}>`); }
    else if (li) {
      flush();
      const d = Math.floor(li[1].length / 2);
      while (depth < d) { out.push('<ul>'); depth++; }
      close(d);
      out.push(`<li>${mdInline(li[2])}</li>`);
    } else if (!raw.trim()) { flush(); close(-1); }
    else if (depth >= 0 && /^\s+/.test(raw)) { out[out.length - 1] = out[out.length - 1].replace(/<\/li>$/, ' ' + mdInline(raw.trim()) + '</li>'); }
    else { close(-1); para.push(raw.trim()); }
  }
  flush(); close(-1);
  return out.join('\n');
}
async function loadGuide(slug) {
  const body = document.getElementById('guideBody'), list = document.getElementById('guideList'), note = document.getElementById('guideNote');
  const layout = document.getElementById('guideLayout'), toc = document.getElementById('guideToc');
  layout.classList.add('no-toc');  // until this guide's headings are known (and on any error below)
  let d;
  try { d = await apiCall('GET', '/api/guides'); }
  catch (e) { body.innerHTML = `<div class="empty">${loadErr('guides', e)}</div>`; return; }
  const guides = d.guides || [];
  if (!guides.length) { body.innerHTML = '<div class="empty">No guides yet.</div>'; return; }
  const current = slug || (guides.find(g => g.slug === 'control-panel') || guides[0]).slug;
  list.innerHTML = guides.length > 1 ? guides.map(g => `<button class="ghost${g.slug === current ? ' active' : ''}" onclick="loadGuide('${escAttr(g.slug)}')">${escapeHtml(g.title)}</button>`).join('') : '';
  let g;
  try { g = await apiCall('GET', `/api/guides/${encodeURIComponent(current)}`); }
  catch (e) { body.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; return; }
  note.textContent = (d.note ? d.note + ' ' : '') + (g.source === 'library' ? 'From the central guide library.' : 'The copy that came with this install.');
  body.innerHTML = renderMarkdown(g.body || '');
  // Contents: buttons, not #links, because the page opens the tab named in the URL hash on load.
  const heads = [...body.querySelectorAll('h2')];
  heads.forEach((h, i) => { h.id = 'guide-s' + i; });
  toc.innerHTML = '<div class="toc-title">On this page</div>' + heads.map(h =>
    `<button type="button" onclick="guideJump('${h.id}')">${escapeHtml(h.textContent)}</button>`).join('');
  layout.classList.toggle('no-toc', heads.length < 3);
}
function guideJump(id) {
  const smooth = !matchMedia('(prefers-reduced-motion: reduce)').matches && !document.body.classList.contains('no-anim');
  document.getElementById(id)?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
}

// ---- what switchTab does for this page (panel.js's Pages) ----
Pages.on('guide', { enter: () => loadGuide() });
