/* The Rules page (static/pages-rules.js): installed packs, their guards and what they stopped. Loaded the first time
   the page opens, after pages-work-admin.js (Pages in panel.js). */
// --- Rules page: rule monitoring (the owner, 2026-09-28: "it should be monitored no? the rules?") --------
const RULE_DECISION = { 'blocked-write': 'Stopped a file change', 'blocked-no-check': 'Stopped: code not tested', 'blocked-stop': 'Stopped the turn',
  blocked: 'Blocked', alarm: 'Alarm', 'told-claude': 'Told Claude', 'reminded-write': 'Reminded (file change)',
  'reminded-no-check': 'Reminded (untested code)', 'reminded-stop': 'Reminded (end of turn)',
  'blocked-message': 'Stopped a long message', 'blocked-handoff': 'Stopped work going to another Claude unchecked',
  'reminded-handoff': 'Reminded (work sent to another Claude)' };
async function loadRules() {
  let d;
  try { d = await apiCall('GET', '/api/rules'); }
  catch (e) {
    if (window.WA) WA.live('ru', false, "Couldn't load");
    const why = `<div class="empty">${loadErr('rules', e)}</div>`;
    document.getElementById('ruList').innerHTML = why;
    if (e.notSetUp) {  // every part of the page says it, not just the list (the prepper's clean-VM re-sweep)
      document.getElementById('ruPacks').innerHTML = '<div class="empty">Rule packs run on the guard hooks, which aren\'t set up here.</div>';
      document.getElementById('ruEvents').innerHTML = '<div class="empty">Nothing to show: no guard hooks record activity here.</div>';
      document.getElementById('ruNote').textContent = '';
    }
    return;
  }
  const q = d.requests || {}, pct = v => v == null ? '—' : v + '%';
  document.getElementById('ruMemory').textContent = pct(q.memory_pct);
  document.getElementById('ruRouted').textContent = pct(q.routed_pct);
  document.getElementById('ruDelegated').textContent = pct(q.delegated_pct);
  document.getElementById('ruChecked').textContent = pct(q.checked_pct);
  const blocks = (d.rules || []).reduce((a, r) => a + (r.hits || 0), 0) + (d.other_guards || []).reduce((a, r) => a + (r.hits || 0), 0);
  document.getElementById('ruBlocks').textContent = blocks.toLocaleString();
  if (window.WA) WA.rulesDeck(d);
  document.getElementById('ruNote').textContent = q.total
    ? `Last ${d.window_days || 7} days: ${q.total} ${q.total === 1 ? 'request' : 'requests'}, ${q.code_requests} of them changed code. Change how strictly the rules are enforced on the Settings page.`
    : `Nothing recorded yet in the last ${d.window_days || 7} days. Each request to Claude is recorded when its turn ends.`;
  renderPacks(d.packs || [], d.drafts || [], d.window_days, d.previews || []);
  const rules = d.rules || [];
  document.getElementById('ruCount').textContent = `${rules.length} rules`;
  document.getElementById('ruList').innerHTML = rules.map(r => `<div class="rule-row">
      <div><div class="rule-id">${escapeHtml(r.id)}</div><span class="pill ${r.priority === 'CRITICAL' ? 'bad' : r.priority === 'HIGH' ? 'warn' : 'info'}">${escapeHtml((r.priority || 'rule').toLowerCase())}</span></div>
      <div class="rule-sum">${escapeHtml(r.summary)}<div class="hint">When: ${escapeHtml(r.trigger || 'always')}</div></div>
      <div class="rule-hits"><b>${r.hits || 0}</b>${r.hits ? `guard hits, last ${agoText(r.last)}` : (r.guards || []).length ? 'no hits' : 'no guard'}</div>
    </div>`).join('') + (d.other_guards || []).map(r => `<div class="rule-row"><div><div class="rule-id">${escapeHtml(r.id)}</div></div>
      <div class="rule-sum">Guarded outside the rules file (${escapeHtml(r.guards.join(', '))}).</div><div class="rule-hits"><b>${r.hits}</b>guard hits</div></div>`).join('');
  // Recent activity and the daily self-review: bounded feeds with a kind filter (pages-work-admin.js)
  if (window.WA) WA.rulesFeeds(d);
}

// Rule packs (the owner, 2026-09-28: "a new rules layout too, which compliments the new system", "i need the rules page
// much better in design ... i like the stats though"). Tiles for the installed packs, one pack open below them, a Get
// packs tile for Pro, and the drafts being built. (MODE_LABEL, the modes' words, is panel.js's: Settings shows it too.)
const MODE_HELP ={ block: "Claude can't do it, and is told why", warn: 'Claude can, and is told first why it may go wrong',
  remind: 'Claude can, and it shows on this page', off: 'Not checked' };
const GUARD_TOOLS = { Bash: 'shell commands', PowerShell: 'PowerShell', Write: 'new files', Edit: 'file edits', NotebookEdit: 'notebooks' };
let _packs = [], _packSel = null;
try { _packSel = localStorage.getItem('cp-pack'); } catch (e) { /* not remembered */ }
// Locked Pro packs (branding-and-pro-review.md, moments 2 and 4): packs on this PC that aren't installed run in audit
// only (guard-engine.py PREVIEW_DIRS), so each tile shows what it would have caught on your own work: real counts from
// the last N days, never an estimate. A tile never blocks anything, and Hide keeps it hidden in this browser.
let _hiddenPreviews = [];
try { _hiddenPreviews = JSON.parse(localStorage.getItem('cp-hide-previews') || '[]'); } catch (e) { /* none hidden */ }
function lockedTiles(previews, days) {
  return previews.filter(p => !_hiddenPreviews.includes(p.slug)).map(p => {
    const n = p.would_have_caught || 0, top = (p.top || []).map(t => t.title).join(', ');
    // A sampler holds a few of the pack's checks (the full pack is Pro), so say which: "Its 2 free sample checks (of 9)"
    const sample = p.full_checks ? `Its ${p.checks} free sample ${p.checks === 1 ? 'check' : 'checks'} (of ${p.full_checks})` : 'It';
    const act = _isPro ? `<button type="button" class="rp-unlock" onclick="selectPack('__get')">Install (in your Pro)</button>`
      : `<a href="${PRO_URL('rules-page')}" target="_blank" rel="noopener">Unlock with Pro</a>`;
    return `<div class="rp-tile rp-locked" data-pack="${escAttr(p.slug)}">
      <span class="rp-tile-top"><b>${escapeHtml(p.title)}</b><span class="pill info">Pro</span></span>
      <span class="rp-tile-sum">${escapeHtml(p.summary || '')}</span>
      <span class="rp-tile-stats"><span class="${n ? 'hot' : ''}">${sample}${n ? ` would have caught <b>${n}</b> in ${days} days` : ` caught nothing in ${days} days`}</span>
        ${top ? `<span title="${escAttr(top)}">Mostly: ${escapeHtml(top)}</span>` : ''}</span>
      <span class="rp-lock-act">${act}<button type="button" class="rp-hide" onclick="hidePreview('${escAttr(p.slug)}')">Hide</button></span>
    </div>`;
  }).join('');
}
function hidePreview(slug) {
  _hiddenPreviews.push(slug);
  try { localStorage.setItem('cp-hide-previews', JSON.stringify(_hiddenPreviews)); } catch (e) { /* this visit only */ }
  const t = document.querySelector(`#ruPacks .rp-locked[data-pack="${CSS.escape(slug)}"]`);
  if (t) t.remove();
}
function renderPacks(packs, drafts, days, previews = []) {
  _packs = packs;
  const checks = packs.reduce((a, p) => a + p.rules.reduce((b, r) => b + r.guards.length, 0), 0);
  document.getElementById('ruPackCount').textContent = packs.length ? `${packs.length} installed, ${checks} checks` : '';
  document.getElementById('ruPacks').innerHTML = packs.map(p => {
    const nR = p.rules.length, nG = p.rules.reduce((a, r) => a + r.guards.length, 0);
    return `<button type="button" class="rp-tile" data-pack="${escAttr(p.slug)}" onclick="selectPack(this.dataset.pack)">
      <span class="rp-tile-top"><b>${escapeHtml(p.title)}</b><span class="pill ${p.tier === 'free' ? 'good' : 'info'}">${p.tier === 'free' ? 'Free' : 'Pro'}</span></span>
      <span class="rp-tile-sum">${escapeHtml(p.summary || '')}</span>
      <span class="rp-tile-stats"><span><b>${nR}</b> ${nR === 1 ? 'rule' : 'rules'}</span><span><b>${nG}</b> ${nG === 1 ? 'check' : 'checks'}</span>
        <span class="${p.hits ? 'hot' : ''}"><b>${p.hits}</b> caught in ${days} days</span></span>
    </button>`;
  }).join('') + lockedTiles(previews, days) + `<button type="button" class="rp-tile rp-get" data-pack="__get" onclick="selectPack('__get')">
      <span class="rp-tile-top"><b>Get more packs</b></span>
      <span class="rp-tile-sum">Add packs from Pro, or update the ones you have.</span>
      <span class="rp-tile-stats"><span>Browse</span></span></button>`;
  if (!_packSel || (_packSel !== '__get' && !packs.some(p => p.slug === _packSel))) _packSel = packs.length ? packs[0].slug : '__get';
  selectPack(_packSel, false);
  document.getElementById('ruDraftsWrap').hidden = !drafts.length;
  document.getElementById('ruDraftCount').textContent = drafts.length ? `${drafts.length} drafts` : '';
  document.getElementById('ruDrafts').innerHTML = drafts.map(dr => {
    const ok = !(dr.problems || []).length;
    return `<div class="rp-tile rp-draft" title="${escAttr([dr.note, ...(dr.problems || [])].filter(Boolean).join('\n'))}">
      <span class="rp-tile-top"><b>${escapeHtml(dr.title)}</b><span class="pill warn">Draft</span></span>
      <span class="rp-tile-sum">${escapeHtml(dr.summary || '')}</span>
      <span class="rp-tile-stats"><span><b>${dr.rules}</b> rules</span><span><b>${dr.guards}</b> checks</span>
        <span class="${ok ? 'good' : 'warn'}">${ok ? 'Passes its checks' : `${dr.problems.length} ${dr.problems.length === 1 ? 'thing' : 'things'} to fix`}</span></span>
    </div>`;
  }).join('');
}
function selectPack(slug, remember = true) {
  _packSel = slug;
  if (remember) { try { localStorage.setItem('cp-pack', slug); } catch (e) { /* not remembered */ } }
  document.querySelectorAll('#ruPacks .rp-tile').forEach(t => t.classList.toggle('active', t.dataset.pack === slug));
  const box = document.getElementById('ruPackDetail');
  box.hidden = false;
  if (slug === '__get') { renderGetPacks(box); return; }
  const p = _packs.find(x => x.slug === slug);
  if (!p) { box.hidden = true; return; }
  box.innerHTML = `<div class="rp-detail-head"><h3>${escapeHtml(p.title)}</h3>
      <span class="pill ${p.tier === 'free' ? 'good' : 'info'}">${p.tier === 'free' ? 'Free' : 'Pro'}</span><span class="hint">Version ${escapeHtml(p.version || '')}</span></div>
    <p class="rp-detail-sum">${escapeHtml(p.summary || '')}</p>
    ${p.rules.map(r => `<div class="rp-rule">
      <div class="rp-rule-title">${escapeHtml(r.title)}${(r.origin || []).length ? `<span class="rule-id">${escapeHtml(r.origin.join(', '))}</span>` : ''}</div>
      <div class="rp-rule-do">${escapeHtml(r.do)}</div>
      ${r.guards.map(guardRow).join('')}
    </div>`).join('')}`;
}
function guardRow(g) {
  // A built-in check (2026-10-01's shared pack format) is run here by the same hook scripts as a 'hook' guard was.
  const builtIn = g.kind === 'hook' || g.kind === 'builtin';
  const watches = builtIn ? 'Built into the checks Claude runs on every request'
    : 'Watches ' + escapeHtml(g.tools.map(t => GUARD_TOOLS[t] || t).join(', '));
  const proof = g.examples && g.examples.block ? `, proved by ${g.examples.block + g.examples.allow} real examples` : '';
  const seen = g.hits ? ` Stepped in ${g.hits} ${g.hits === 1 ? 'time' : 'times'}, last ${agoText(g.last)}.` : '';
  // Replay proof (guard-replay --write-evidence): what this guard did on real past commands before it shipped.
  const ev = g.evidence && g.evidence.replayed_calls ? ` Tested on ${g.evidence.replayed_calls.toLocaleString()} real past commands (${escapeHtml(g.evidence.checked || '')}): would have stepped in ${g.evidence.fired} ${g.evidence.fired === 1 ? 'time' : 'times'}.` : '';
  const control = builtIn ? '<span class="rp-always">On while this pack is installed</span>'
    : `<div class="seg" role="radiogroup" aria-label="How strict: ${escAttr(g.title || g.id)}">${['block', 'warn', 'remind', 'off'].map(m =>
        `<button type="button" role="radio" aria-checked="${g.mode === m}" class="${g.mode === m ? 'on' : ''}"
          title="${escAttr(MODE_HELP[m] + (m === g.default ? ' (the default)' : ''))}" onclick="setGuardMode('${escAttr(g.id)}', '${m}', this)">${MODE_LABEL[m]}</button>`).join('')}</div>`;
  return `<div class="rp-guard"><div><div class="rp-guard-title">${escapeHtml(g.title || g.id)}</div>
      <div class="hint">${watches}${proof}.${seen}${ev}</div></div>
    <div class="rp-guard-ctl">${control}<span class="set-msg" id="gm-${escAttr(g.id)}"></span></div></div>`;
}
async function setGuardMode(id, mode, btn) {
  const msg = document.getElementById('gm-' + id);
  try {
    await apiCall('PUT', '/api/rules/guard-modes', { [id]: mode });
    btn.parentElement.querySelectorAll('button').forEach(b => { const on = b === btn; b.classList.toggle('on', on); b.setAttribute('aria-checked', String(on)); });
    _packs.forEach(p => p.rules.forEach(r => r.guards.forEach(g => { if (g.id === id) g.mode = mode; })));
    msg.textContent = 'Saved'; msg.className = 'set-msg ok';
  } catch (e) { msg.textContent = e.message; msg.className = 'set-msg bad'; }
}
async function renderGetPacks(box) {
  box.innerHTML = '<div class="loading">Checking your Pro membership…</div>';
  let d;
  try { d = await apiCall('GET', '/api/packs'); }
  catch (e) { box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; return; }
  if (_packSel !== '__get') return;  // another pack was picked meanwhile
  const pro = d.pro || {}, yours = (d.installed || []).filter(p => p.source === 'yours');
  const offer = !pro.configured
    ? `<div class="rp-pro-off"><p><b>Pro adds more packs</b> and keeps them up to date: Token Saver, Release Ready and more as they're written. Everything on this page keeps working without it.</p>
        <p class="hint">To connect: paste a Pro key in Settings, under Pro membership. Packs then install from here with one click.</p></div>`
    : pro.error ? `<div class="empty">${escapeHtml(pro.error)}</div>`
    : (pro.available || []).length ? pro.available.map(a => `<div class="rp-guard"><div><div class="rp-guard-title">${escapeHtml(a.title || a.slug)}</div>
        <div class="hint">${escapeHtml(a.description || '')}</div></div>
        <div class="rp-guard-ctl"><button class="${a.installed ? 'ghost' : ''}" onclick="packAction('install', '${escAttr(a.slug)}', this)">${a.installed ? 'Update' : 'Install'}</button>
          <span class="set-msg" id="pk-${escAttr(a.slug)}"></span></div></div>`).join('')
    : '<div class="empty">Pro has no packs for you yet.</div>';
  box.innerHTML = `<div class="rp-detail-head"><h3>Get more packs</h3></div>${offer}
    ${yours.length ? `<h4 class="rp-sub">Packs you installed</h4>${yours.map(p => `<div class="rp-guard"><div><div class="rp-guard-title">${escapeHtml(p.title)}</div>
        <div class="hint">Version ${escapeHtml(p.version || '')}</div></div>
        <div class="rp-guard-ctl"><button class="ghost" onclick="packAction('remove', '${escAttr(p.slug)}', this)">Remove</button>
          <span class="set-msg" id="pk-${escAttr(p.slug)}"></span></div></div>`).join('')}` : ''}`;
}
async function packAction(action, slug, btn) {
  if (action === 'remove' && !confirm(`Remove the ${slug} pack?\n\nClaude stops following it from its next action.`)) return;
  const msg = document.getElementById('pk-' + slug);
  btn.disabled = true;
  try {
    const r = await apiCall('POST', `/api/packs/${action}`, { slug });
    msg.textContent = action === 'install' ? `Installed ${r.installed.title} ${r.installed.version}. Claude follows it from its next action.` : 'Removed.';
    msg.className = 'set-msg ok';
    setTimeout(loadRules, 1500);
  } catch (e) { msg.textContent = e.message; msg.className = 'set-msg bad'; }
  finally { btn.disabled = false; }
}

// ---- what switchTab and the poll do for this page (panel.js's Pages) ----
Pages.on('rules', { enter: () => loadRules(), poll(slow) { if (slow) loadRules(); } });
