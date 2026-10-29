/* Work and admin pages (the whole-panel revamp, 2026-10-27; the owner: "get everything to match and flow ... real top
   quality throughout"): Projects, Observations, and the shared parts the To-do, Rules and Settings pages use here.
   The same grammar as the Overview cockpit (telemetry.js): a deck at the top of each page with one instrument drawn
   from real data (a Reactor ring or a Pulse trace) beside its readouts, then one compact table with a toolbar, a side
   drawer for detail and confirmations for anything that changes something. Cockpit off (the Overview's switch) hides
   the instruments and leaves the plain numbers. Nothing here invents a figure: a gap is a gap, and a page with no
   history draws no trace. The pure helpers at the top are what dashboard/js_tests/pages_work_admin_test.js checks.
   Uses the page's own helpers (escapeHtml, escAttr, agoText, apiCall, loadErr, UI) at call time, never at load. */
(function (root) {
  'use strict';
  const DAY = 86400000;

  // ---------------------------------------------------------------------------------------------------------------
  // Pure helpers
  // ---------------------------------------------------------------------------------------------------------------
  function dayStart(ms) { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); }
  // Counts per local day for the last `days` days, oldest first: [{t, v}]. Times outside the window are left out.
  function dayCounts(times, days, nowMs) {
    const end = dayStart(nowMs), out = [];
    for (let i = days - 1; i >= 0; i--) out.push({ t: end - i * DAY, v: 0 });
    const first = out[0].t;
    (times || []).forEach(ms => {
      if (ms == null || !isFinite(ms)) return;
      const k = Math.round((dayStart(ms) - first) / DAY);
      if (k >= 0 && k < days) out[k].v++;
    });
    return out;
  }
  // "2026-10-27" as a local midnight (the observation log keeps dates, not times)
  function dateMs(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ''));
    return m ? new Date(+m[1], +m[2] - 1, +m[3]).getTime() : null;
  }
  // One page of a list: {page (clamped), pages, from, to}
  function paginate(total, page, per) {
    const pages = Math.max(1, Math.ceil(total / per)), p = Math.max(1, Math.min(pages, page || 1));
    return { page: p, pages, from: (p - 1) * per, to: Math.min(total, p * per) };
  }
  // Rules broken more than once, declined ones not counted (the escalation signal: a rule broken twice gets a guard)
  function ruleRepeats(items) {
    const n = {};
    (items || []).filter(o => o.rule && o.status !== 'declined').forEach(o => { n[o.rule] = (n[o.rule] || 0) + 1; });
    return n;
  }
  const OBS_SINCE = { any: 0, week: 7, month: 30, quarter: 90 };
  function obsMatches(o, f, repeats, nowMs) {
    if (f.status === 'repeat') { if (!(o.rule && (repeats[o.rule] || 0) > 1)) return false; }
    else if (f.status && f.status !== 'all' && o.status !== f.status) return false;
    if (f.rule && f.rule !== 'any' && (f.rule === '__none' ? !!o.rule : o.rule !== f.rule)) return false;
    const days = OBS_SINCE[f.since] || 0, at = dateMs(o.date);
    if (days && (at == null || at < dayStart(nowMs) - (days - 1) * DAY)) return false;
    const q = String(f.q || '').trim().toLowerCase();
    if (q && !['#' + o.id, o.title, o.rule, o.area, o.class, o.source].some(v => v && String(v).toLowerCase().includes(q))) return false;
    return true;
  }
  // The Rules page's recent events: a guard that stopped something, or one that only spoke up
  function ruleKind(decision) { return /^blocked/.test(String(decision || '')) ? 'stop' : 'note'; }
  // The vault's audit actions, in groups the change log can filter by
  function vaultKind(action) {
    const a = String(action || '');
    return /^(added|replaced|updated)$/.test(a) ? 'change' : a === 'deleted' ? 'delete' : /reveal/.test(a) ? 'reveal' : 'other';
  }
  // Disk shares for the Projects ring: the biggest n, the rest as one "others" slice. Sizes in bytes.
  function shares(items, n) {
    const s = (items || []).filter(x => x.v > 0).sort((a, b) => b.v - a.v);
    const top = s.slice(0, n), rest = s.slice(n).reduce((a, x) => a + x.v, 0);
    return rest > 0 ? [...top, { name: `${s.length - n} others`, v: rest, other: true }] : top;
  }
  const pure = { dayStart, dayCounts, dateMs, paginate, ruleRepeats, obsMatches, ruleKind, vaultKind, shares, OBS_SINCE };
  if (typeof module === 'object' && module.exports) { module.exports = pure; return; }

  // ---------------------------------------------------------------------------------------------------------------
  // Shared page parts
  // ---------------------------------------------------------------------------------------------------------------
  const $ = id => document.getElementById(id);
  const T = () => root.HSTelemetry;
  const WA = root.WA = { pure };
  // Cockpit off on the Overview is off here too: the instruments step aside, the plain numbers stay.
  function syncClassic() {
    const ck = $('cockpit');
    document.documentElement.classList.toggle('wa-classic', !!(ck && ck.classList.contains('classic')));
  }
  if ($('cockpit') && 'MutationObserver' in root) new MutationObserver(syncClassic).observe($('cockpit'), { attributes: true, attributeFilter: ['class'] });
  syncClassic();
  WA.live = (id, ok, text, at) => {
    const el = $(id + 'Live'), up = $(id + 'Updated');
    if (el) { el.className = 'ui-live ' + (ok ? 'ok' : 'bad'); el.lastElementChild.textContent = text; }
    if (up) up.textContent = at ? 'Updated ' + new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  };
  // A Pulse trace in host (made once, then fed). series: [{name, tone, dashed, points:[{t, v}]}]
  WA.trace = (host, data) => {
    if (!host || !T()) return;
    if (!host._pulse) host._pulse = new (T().Pulse)(host, { compact: true });
    host._pulse.set(Object.assign({ fmtX: t => new Date(t).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }),
      fmtAxisX: t => new Date(t).toLocaleDateString([], { day: 'numeric', month: 'short' }), fmtY: v => T().fmtNum(v) }, data));
  };
  // A Reactor dial in host (made once, then set)
  WA.dial = (host, st) => {
    if (!host || !T()) return;
    if (!host._dial) host._dial = new (T().Reactor)(host, { label: st.label });
    host._dial.set(st);
  };
  // A question with a text box, built like UI.confirm: run(text) does the work; the dialog stays open and busy until the
  // server answers, and an error shows inside it.
  WA.ask = ({ title, body, label, placeholder = '', action, danger = false, run }) => new Promise(resolve => {
    let d = $('waAsk');
    if (!d) {
      d = document.createElement('dialog'); d.id = 'waAsk'; d.className = 'ui-dialog';
      d.setAttribute('aria-labelledby', 'waAskTitle');
      document.body.appendChild(d);
    }
    d.innerHTML = `<form class="ui-dialog-in"><div class="ui-dialog-body"><h2 id="waAskTitle">${escapeHtml(title)}</h2>
        ${body ? `<div class="ui-dialog-text">${body}</div>` : ''}
        <div class="ui-field"><label for="waAskText">${escapeHtml(label)}</label>
          <textarea id="waAskText" rows="3" maxlength="500" placeholder="${escAttr(placeholder)}"></textarea></div>
        <div class="ui-err" role="alert"></div></div>
      <div class="ui-dialog-foot"><button type="button" class="ghost" data-x>Cancel</button>
        <button type="submit" class="${danger ? 'ui-danger' : 'ui-primary'}" data-ok>${escapeHtml(action)}</button></div></form>`;
    const ok = d.querySelector('[data-ok]'), err = d.querySelector('.ui-err'), text = d.querySelector('textarea');
    let result = false;
    d.querySelector('[data-x]').onclick = () => { if (!ok.disabled) d.close(); };
    d.oncancel = e => { if (ok.disabled) e.preventDefault(); };
    d.onclose = () => resolve(result);
    d.querySelector('form').onsubmit = e => {
      e.preventDefault(); err.textContent = '';
      UI.busy(ok, async () => {
        try { result = (await run(text.value.trim())) || true; d.close(); } catch (x) { err.textContent = x.message || String(x); }
      });
    };
    d.showModal();
    text.focus();
  });
  // A side drawer (the shared .ui-drawer), made once per id; returns {d, head, body}
  WA.drawer = (id, label) => {
    let d = $(id);
    if (!d) {
      d = document.createElement('dialog'); d.id = id; d.className = 'ui-drawer wa-drawer'; d.setAttribute('aria-labelledby', id + 'Title');
      d.innerHTML = `<div class="ui-drawer-head"><div class="ui-drawer-top"><div class="ui-drawer-id"><h2 id="${id}Title" tabindex="-1"></h2>
          <div class="ui-drawer-sub" id="${id}Sub"></div></div>
          <button type="button" class="ui-x" data-close aria-label="Close ${escAttr(label)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div></div>
        <div class="ui-drawer-body" id="${id}Body" tabindex="-1"></div>`;
      d.querySelector('[data-close]').onclick = () => d.close();
      d.addEventListener('click', e => { if (e.target === d) d.close(); });  // a click on the backdrop
      document.body.appendChild(d);
    }
    return { d, title: $(id + 'Title'), sub: $(id + 'Sub'), body: $(id + 'Body') };
  };
  // Leaving a page closes its drawer (the To-do drawer is closed by its own page)
  const DRAWERS = { projects: 'prjDrawer', observations: 'obsDrawer', settings: 'credDrawer' };
  WA.leave = tab => Object.entries(DRAWERS).forEach(([t, id]) => { const d = $(id); if (t !== tab && d && d.open) d.close(); });
  WA.kebab =(label, key) => `<button type="button" class="ui-kebab" aria-haspopup="menu" aria-expanded="false" aria-label="${escAttr(label)}" data-key="${escAttr(key)}"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="3" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="8" cy="13" r="1.5"/></svg></button>`;
  WA.pager = (el, pg, total, go) => {
    el.innerHTML = pg.pages > 1 ? `<span class="ui-count">${pg.from + 1}–${pg.to} of ${total}</span>
      <span class="ui-spacer"></span><button type="button" class="ghost" data-p="-1" ${pg.page <= 1 ? 'disabled' : ''}>Previous</button>
      <span class="ui-sub">Page ${pg.page} of ${pg.pages}</span>
      <button type="button" class="ghost" data-p="1" ${pg.page >= pg.pages ? 'disabled' : ''}>Next</button>` : '';
    el.querySelectorAll('[data-p]').forEach(b => { b.onclick = () => go(pg.page + Number(b.dataset.p)); });
  };

  // ---------------------------------------------------------------------------------------------------------------
  // Observations: rule breaks and corrections (mcp-server observe.*, through /api/observations). One row each, newest
  // first, in pages of 25; status, rule and date filters; a drawer for the detail; a dialog for the log form.
  // ---------------------------------------------------------------------------------------------------------------
  const STATUS = { open: ['Open', 'warn'], actioned: ['Fixed', 'good'], parked: ['Parked', ''], declined: ['Declined', ''], superseded: ['Superseded', ''] };
  const PER = 25;
  let _obs = [], _obsAt = 0, _obsErr = null, _obsBusy = false;
  const _os = { q: '', status: 'all', rule: 'any', since: 'any', sort: 'new', page: 1, sel: null };  // the saved state joins in wireObs
  const saveOs = () => UI.save('cp-obs-page', _os);
  async function loadObservations() {
    if (_obsBusy) return;
    _obsBusy = true;
    try {
      const d = await apiCall('GET', '/api/observations?status=all');
      _obs = d.observations || []; _obsAt = Date.now(); _obsErr = null;
    } catch (e) { _obsErr = e; }
    finally { _obsBusy = false; }
    renderObservations();
  }
  function obsBadge(open) {
    const badge = $('obsBadge');
    if (badge) { badge.textContent = open; badge.style.display = open ? 'inline-grid' : 'none'; }
    if (typeof mirrorBadges === 'function') mirrorBadges();
    if (typeof _sys !== 'undefined') _sys.obsOpen = open;
  }
  function renderObservations() {
    const list = $('obsRows');
    if (!list) return;
    if (_obsErr && !_obsAt) {
      WA.live('obs', false, "Couldn't load");
      list.innerHTML = `<tr class="ui-empty-row"><td colspan="8"><div class="ui-empty">${loadErr('observations', _obsErr)}</div></td></tr>`;
      return;
    }
    WA.live('obs', !_obsErr, _obsErr ? 'Refresh failed, showing the last list' : 'Live', _obsAt);
    const counts = {};
    _obs.forEach(o => { counts[o.status] = (counts[o.status] || 0) + 1; });
    obsBadge(counts.open || 0);
    const rep = ruleRepeats(_obs), repeated = Object.keys(rep).filter(r => rep[r] > 1);
    // readouts: each one is a status filter
    const m = [['open', counts.open || 0, 'Open'], ['actioned', counts.actioned || 0, 'Fixed'], ['parked', counts.parked || 0, 'Parked'],
      ['declined', counts.declined || 0, 'Declined'], ['repeat', repeated.length, repeated.length === 1 ? 'Rule broken twice' : 'Rules broken twice'],
      ['week', _obs.filter(o => obsMatches(o, { since: 'week' }, rep, Date.now())).length, 'Logged in 7 days']];
    $('obsMetrics').innerHTML = m.map(([k, n, l]) => `<button type="button" class="ui-metric${(k === 'open' || k === 'repeat') && n ? ' warn' : k === 'week' && n ? ' live' : ''}" data-key="obs-m-${k}"
        aria-pressed="${k === 'week' ? _os.since === 'week' : _os.status === k}" onclick="Obs.metric('${k}')" title="Show ${escAttr(l.toLowerCase())}"><span class="ui-metric-num">${n}</span><span class="ui-metric-label">${l}</span></button>`).join('');
    // the trace: corrections logged per day, the last 60 days (the log keeps a date per entry, so this is measured)
    const days = dayCounts(_obs.map(o => dateMs(o.date)), 60, Date.now()), in60 = days.reduce((a, x) => a + x.v, 0);
    const fixed = dayCounts(_obs.filter(o => o.status === 'actioned').map(o => dateMs(o.date)), 60, Date.now());
    $('obsTraceMeta').textContent = `${in60} in 60 days`;
    WA.trace($('obsTrace'), { label: 'Corrections logged per day, last 60 days, and how many of those are now fixed', legendAlways: true,
      series: [{ name: 'Logged', tone: 'live', points: days }, { name: 'Since fixed', tone: 'measured', points: fixed }],
      message: _obs.length ? '' : 'Nothing logged yet' });
    // the rule filter lists the rules actually logged
    const rules = [...new Set(_obs.map(o => o.rule).filter(Boolean))].sort();
    const rsel = $('obsRule');
    rsel.innerHTML = `<option value="any">Any rule</option><option value="__none">No rule</option>` + rules.map(r =>
      `<option value="${escAttr(r)}">${escapeHtml(r)}${rep[r] > 1 ? ` (${rep[r]}×)` : ''}</option>`).join('');
    rsel.value = _os.rule === 'any' || _os.rule === '__none' || rules.includes(_os.rule) ? _os.rule : 'any';
    $('obsRuleList').innerHTML = rules.concat(typeof OBS_RULE_HINTS !== 'undefined' ? OBS_RULE_HINTS : []).filter((r, i, a) => a.indexOf(r) === i).map(r => `<option value="${escAttr(r)}">`).join('');
    $('obsStatus').value = _os.status; $('obsSince').value = _os.since; $('obsSort').value = _os.sort;
    if ($('obsSearch').value !== _os.q) $('obsSearch').value = _os.q;
    const shown = _obs.filter(o => obsMatches(o, _os, rep, Date.now()));
    const key = o => _os.sort === 'stops' ? -(o.stops || 0) : 0;
    shown.sort((a, b) => key(a) - key(b) || (_os.sort === 'old' ? 1 : -1) * ((dateMs(a.date) || 0) - (dateMs(b.date) || 0) || Number(a.id) - Number(b.id)));
    const filtered = _os.q || _os.status !== 'all' || _os.rule !== 'any' || _os.since !== 'any';
    $('obsClear').hidden = !filtered;
    $('obsCount').textContent = filtered ? `${shown.length} of ${_obs.length}` : `${_obs.length} ${_obs.length === 1 ? 'entry' : 'entries'}`;
    const pg = paginate(shown.length, _os.page, PER);
    _os.page = pg.page; saveOs();
    UI.rerender(list, shown.length ? shown.slice(pg.from, pg.to).map(o => obsRow(o, rep)).join('')
      : `<tr class="ui-empty-row"><td colspan="8"><div class="ui-empty">${_obs.length
        ? '<b>Nothing matches.</b><span>Try another word, or clear the filters.</span><button type="button" class="ghost" onclick="Obs.clear()">Clear filters</button>'
        : "<b>No corrections logged yet.</b><span>When Claude breaks a rule or you correct it, log it here. A rule broken twice gets a guard.</span><button type=\"button\" class=\"ui-primary\" onclick=\"Obs.openLog()\">Log a correction</button>"}</div></td></tr>`);
    WA.pager($('obsMore'), pg, shown.length, p => { _os.page = p; renderObservations(); $('obsWork').scrollIntoView({ block: 'start' }); });
    if (_os.sel != null && $('obsDrawer') && $('obsDrawer').open) drawObsDrawer();
  }
  function obsRow(o, rep) {
    const [label, tone] = STATUS[o.status] || [o.status, ''], n = o.rule ? rep[o.rule] || 0 : 0;
    const sec = [o.area, o.class].filter(Boolean).join(' · ');
    return `<tr data-key="obs-${escAttr(o.id)}" data-tone="${o.status === 'open' ? 'warn' : ''}" tabindex="0" class="${String(_os.sel) === String(o.id) ? 'is-sel' : ''}"
        onclick="if (!event.target.closest('button')) Obs.open('${escAttr(o.id)}')" onkeydown="if (event.key === 'Enter' && event.target === this) Obs.open('${escAttr(o.id)}')">
      <td class="num ui-c-id">#${escapeHtml(o.id)}</td>
      <td class="ui-name-cell"><span class="wa-title">${escapeHtml(o.title)}</span>${sec ? `<span class="ui-sec">${escapeHtml(sec)}</span>` : ''}</td>
      <td data-label="Rule">${o.rule ? `<code class="wa-code">${escapeHtml(o.rule)}</code>${n > 1 ? ` <span class="ui-chip bad" title="Broken ${n} times: the escalation to a guard is due">${n}×</span>` : ''}` : '<span class="ui-dim">—</span>'}</td>
      <td data-label="Status"><span class="ui-state ${tone}">${escapeHtml(label)}</span></td>
      <td data-label="Logged" class="ui-nowrap">${escapeHtml(o.date || '')}</td>
      <td data-label="From" class="ui-opt2">${escapeHtml(o.source || '')}</td>
      <td data-label="Stops" class="num ui-opt" title="Guard stops of this kind of slip, from the daily self-review">${o.stops ? Number(o.stops) : '<span class="ui-dim">0</span>'}</td>
      <td class="ui-c-act">${WA.kebab(`Actions for #${o.id}`, 'obs-k-' + o.id).replace('<button', `<button onclick="Obs.menu(this, '${escAttr(o.id)}')"`)}</td></tr>`;
  }
  function obsActions(o) {
    const items = o.status === 'open'
      ? [{ label: 'Mark fixed', run: () => setStatus(o, 'actioned') }, { label: 'Park', run: () => setStatus(o, 'parked') },
        { label: 'Decline', run: () => setStatus(o, 'declined') }]
      : [{ label: 'Reopen', run: () => setStatus(o, 'open') }];
    return [...items, '-', { label: 'Log it again', run: () => openLog(o) }, { label: 'Open the details', run: () => Obs.open(o.id) }];
  }
  async function setStatus(o, status) {
    const verb = { actioned: 'Mark fixed', parked: 'Park', declined: 'Decline', open: 'Reopen' }[status];
    const post = async note => {
      await apiCall('POST', `/api/observations/${encodeURIComponent(o.id)}`, { status, resolution: note || '' });
      UI.toast(`#${o.id}: ${(STATUS[status] || [status])[0].toLowerCase()}`);
      await loadObservations();
    };
    if (status === 'open') {
      await UI.confirm({ title: `Reopen #${o.id}?`, body: `<p><b>${escapeHtml(o.title)}</b> goes back on the open list.</p>`, action: 'Reopen', danger: false, run: () => post('') });
      return;
    }
    await WA.ask({ title: `${verb}: #${o.id}`, body: `<p><b>${escapeHtml(o.title)}</b></p>`, label: 'A note for the record (optional)',
      placeholder: status === 'actioned' ? 'What fixed it' : 'Why', action: verb, run: post });
  }
  function drawObsDrawer() {
    const o = _obs.find(x => String(x.id) === String(_os.sel)), w = WA.drawer('obsDrawer', 'the observation');
    if (!o) { w.d.close(); return; }
    const rep = ruleRepeats(_obs), n = o.rule ? rep[o.rule] || 0 : 0, [label, tone] = STATUS[o.status] || [o.status, ''];
    w.title.textContent = `#${o.id} ${o.title}`;
    w.sub.innerHTML = `<span class="ui-state ${tone}">${escapeHtml(label)}</span><span>Logged ${escapeHtml(o.date || '')}</span>${o.source ? `<span>by ${escapeHtml(o.source)}</span>` : ''}`;
    const others = o.rule ? _obs.filter(x => x.rule === o.rule && x.id !== o.id).slice(-6).reverse() : [];
    w.body.innerHTML = `<div class="ui-block"><dl class="ui-dl">
        <dt>Rule</dt><dd>${o.rule ? `<code class="wa-code">${escapeHtml(o.rule)}</code>` : 'None tagged'}</dd>
        ${o.class ? `<dt>Kind of slip</dt><dd><code class="wa-code">${escapeHtml(o.class)}</code></dd>` : ''}
        ${o.area ? `<dt>Area</dt><dd>${escapeHtml(o.area)}</dd>` : ''}
        <dt>Guard stops</dt><dd>${Number(o.stops || 0)}${o.last_seen ? `, last seen ${escapeHtml(o.last_seen)}` : ''}</dd></dl></div>
      ${n > 1 ? `<div class="ui-note bad">This rule has been broken ${n} times. The same rule broken twice gets a mechanical guard.</div>` : ''}
      <div class="ui-block"><h3>Actions</h3><div class="ui-btnrow">${obsActions(o).filter(x => x !== '-' && !/details/.test(x.label)).map((x, i) =>
        `<button type="button" class="${i ? 'ghost' : 'ui-primary'}" data-i="${i}">${escapeHtml(x.label)}</button>`).join('')}</div>
        <p class="hint">Log it again when it happened again, or the fix didn't hold: a new entry tagged with the same rule.</p></div>
      ${others.length ? `<div class="ui-block"><h3>The same rule, other entries</h3>${others.map(x => `<button type="button" class="wa-link-row" data-open="${escAttr(x.id)}">
        <span>#${escapeHtml(x.id)} ${escapeHtml(x.title)}</span><span class="ui-state ${(STATUS[x.status] || [])[1] || ''}">${escapeHtml((STATUS[x.status] || [x.status])[0])}</span></button>`).join('')}</div>` : ''}`;
    const acts = obsActions(o).filter(x => x !== '-' && !/details/.test(x.label));
    w.body.querySelectorAll('[data-i]').forEach(b => { b.onclick = () => acts[+b.dataset.i].run(); });
    w.body.querySelectorAll('[data-open]').forEach(b => { b.onclick = () => Obs.open(b.dataset.open); });
  }
  function openLog(again) {
    const d = $('obsLog');
    $('obsLogErr').textContent = '';
    if (again) {
      $('obsTitle').value = again.title + ' (again)'; $('obsRuleIn').value = again.rule || ''; $('obsIssue').value = '';
      $('obsIssue').placeholder = `What happened this time? (correcting #${again.id})`;
    } else $('obsIssue').placeholder = 'What happened: what you saw, and what you expected';
    if ($('obsDrawer') && $('obsDrawer').open) $('obsDrawer').close();
    d.showModal();
    (again ? $('obsIssue') : $('obsTitle')).focus();
  }
  async function logObservation(ev) {
    if (ev) ev.preventDefault();
    const val = id => $(id).value.trim(), err = $('obsLogErr');
    const body = { title: val('obsTitle'), issue: val('obsIssue'), rule: val('obsRuleIn'), fix: val('obsFix') };
    $('obsTitle').setAttribute('aria-invalid', String(!body.title)); $('obsIssue').setAttribute('aria-invalid', String(!body.issue));
    if (!body.title || !body.issue) { err.textContent = 'A title and what happened are both needed.'; return; }
    await UI.busy($('obsLogBtn'), async () => {
      try {
        const d = await apiCall('POST', '/api/observations', body);
        ['obsTitle', 'obsIssue', 'obsRuleIn', 'obsFix'].forEach(id => { $(id).value = ''; $(id).removeAttribute('aria-invalid'); });
        $('obsLog').close();
        if (d.escalate) {
          $('obsBanner').hidden = false;
          $('obsBanner').textContent = `Logged #${d.id}. This rule has now been broken ${d.repeat_count + 1} times. ${d.hint || ''}`;
        }
        UI.toast(`Logged #${d.id}`);
        _os.sel = null;
        await loadObservations();
        const row = document.querySelector(`#obsRows tr[data-key="obs-${CSS.escape(String(d.id))}"]`);
        if (row) row.classList.add('is-new');
      } catch (e) { err.textContent = e.message; }  // what was typed stays in the boxes
    });
  }
  const Obs = root.Obs = {
    metric(k) { if (k === 'week') _os.since = _os.since === 'week' ? 'any' : 'week'; else _os.status = _os.status === k ? 'all' : k; _os.page = 1; renderObservations(); UI.refocus('obs-m-' + k); },
    clear() { Object.assign(_os, { q: '', status: 'all', rule: 'any', since: 'any', page: 1 }); renderObservations(); },
    open(id) {
      _os.sel = String(id); saveOs();
      const w = WA.drawer('obsDrawer', 'the observation');
      drawObsDrawer();
      if (!w.d.open) { w.d.showModal(); w.title.focus(); }
      w.d.onclose = () => { const was = _os.sel; _os.sel = null; saveOs(); renderObservations(); UI.refocus('obs-' + was); };
      document.querySelectorAll('#obsRows tr').forEach(r => r.classList.toggle('is-sel', r.dataset.key === 'obs-' + id));
    },
    menu(btn, id) { const o = _obs.find(x => String(x.id) === String(id)); if (o) UI.menu(btn, obsActions(o)); },
    openLog: () => openLog(null),
  };
  function wireObs() {
    Object.assign(_os, UI.keep('cp-obs-page', _os), { sel: null });
    const set = (k, v) => { _os[k] = v; _os.page = 1; renderObservations(); };
    $('obsSearch').addEventListener('input', e => set('q', e.target.value));
    ['Status', 'Rule', 'Since', 'Sort'].forEach(n => $('obs' + n).addEventListener('change', e => set(n.toLowerCase(), e.target.value)));
    $('obsLogForm').addEventListener('submit', logObservation);
    UI.sticky($('obsWork'));
  }
  root.loadObservations = loadObservations;
  root.renderObservations = renderObservations;
  root.logObservation = logObservation;

  // ---------------------------------------------------------------------------------------------------------------
  // Projects: every folder agents work in (the helper on the owner's PC, /api/projects). A ring of where the disk goes,
  // a toolbar with cards or a compact table, and a drawer per project for its git state, key files, sessions and pins.
  // Refreshes each minute while open; paths copy on click.
  // ---------------------------------------------------------------------------------------------------------------
  root.Projects = (() => {
    let _items = [], _timer = null, _loaded = 0, _err = null, _busy = false, _soon = 0;
    const st = { q: '', sort: 'recent', view: 'cards', sel: null };  // the saved state joins in wire()
    const save = () => UI.save('cp-projects-page', st);
    const GH = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38v-1.33c-2.23.48-2.7-1.07-2.7-1.07-.36-.92-.89-1.17-.89-1.17-.73-.5.06-.49.06-.49.8.06 1.23.83 1.23.83.72 1.22 1.88.87 2.34.66.07-.52.28-.87.51-1.07-1.78-.2-3.65-.89-3.65-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.2c0 .21.15.46.55.38A8 8 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>';
    const bytes = n => n == null ? '…' : n < 1024 ? n + ' B' : ['KB', 'MB', 'GB', 'TB'].reduce((a, u, i) =>
      a || (n < 1024 ** (i + 2) || u === 'TB' ? (n / 1024 ** (i + 1)).toFixed(n < 10 * 1024 ** (i + 1) ? 1 : 0) + ' ' + u : ''), '');
    const ago = ts => ts ? agoText(new Date(ts * 1000).toISOString()) : 'never';
    const sizeOf = p => (p.size && p.size.bytes) || 0;
    const keyOf = p => p.folder.toLowerCase();
    const find = k => _items.find(p => keyOf(p) === k);
    const msg = (t, bad) => { const m = $('prjMsg'); m.textContent = t; m.classList.toggle('bad', !!bad); };

    async function load() {
      if (_busy) return;
      _busy = true;
      try {
        const d = await apiCall('GET', '/api/projects');
        _items = d.projects || []; _loaded = Date.now(); _err = null;
        msg(d.measuring ? `Measuring ${d.measuring} folder${d.measuring === 1 ? '' : 's'}… sizes fill in shortly.` : '');
        clearTimeout(_soon);
        if (d.measuring && _timer) _soon = setTimeout(load, 8000);
      } catch (e) { _err = e; }
      finally { _busy = false; }
      render();
    }
    function deck() {
      const total = _items.reduce((a, p) => a + sizeOf(p), 0), live = _items.filter(p => p.open).length;
      const busy = _items.filter(p => p.busy).length;
      const repos = new Set(_items.map(p => p.git && (p.git.github || p.git.root || p.folder)).filter(Boolean)).size;
      const changed = _items.reduce((a, p) => a + ((p.git && p.git.changed) || 0), 0), toPush = _items.reduce((a, p) => a + ((p.git && p.git.ahead) || 0), 0);
      const agents = new Set(_items.flatMap(p => p.agents || [])).size;
      $('prjMetrics').innerHTML = [[_items.length, 'Project folders'], [live, busy ? `Open now, ${busy} working` : 'Open now', live ? 'live' : ''],
        [repos, 'Git repos'], [changed, 'Changed files', changed ? 'warn' : ''], [toPush, 'Commits to push'], [agents, 'Agents seen']]
        .map(([n, l, c]) => `<div class="ui-metric ${c || ''}"><span class="ui-metric-num">${n}</span><span class="ui-metric-label">${l}</span></div>`).join('');
      // the ring: each folder's share of the disk the folders use (a folder inside another counts in both, as measured)
      const sh = shares(_items.map(p => ({ name: p.name, v: sizeOf(p), key: keyOf(p) })), 6);
      const tot = sh.reduce((a, x) => a + x.v, 0);
      WA.dial($('prjDial'), sh.length ? {
        status: 'ok', label: 'On disk', value: bytes(total).split(' ')[0], unit: bytes(total).split(' ')[1] || '', period: `${sh.length > 6 ? 'top 6 and the rest' : _items.length + ' folders'}`,
        segments: sh.map((x, i) => ({ v: x.v, tone: x.other ? 'estimated' : 'measured', alpha: x.other ? 0.5 : 1 - i * 0.12, title: `${x.name}: ${bytes(x.v)}` })),
        aria: `${bytes(total)} on disk across ${_items.length} project folders`,
      } : { status: 'unavailable', label: 'On disk', period: _loaded ? 'Not measured yet' : 'Reading…' });
      $('prjLegend').innerHTML = sh.length ? sh.map((x, i) => `<li${x.key ? ` data-key="${escAttr(x.key)}"` : ''}><i style="opacity:${x.other ? 0.5 : (1 - i * 0.12).toFixed(2)}" class="${x.other ? 'est' : ''}"></i>
          <span>${escapeHtml(x.name)}</span><b>${bytes(x.v)}</b><em>${Math.round(x.v / tot * 100)}%</em></li>`).join('') : '';
    }
    function stateOf(p) {
      return !p.exists ? ['bad', 'Folder gone'] : p.busy ? ['run', 'Working now'] : p.open ? ['good', 'Open, idle'] : ['', ago(p.last_active)];
    }
    function gitChips(g) {
      if (!g) return '<span class="ui-dim">Not a repo</span>';
      return `<span class="ui-chip plain">⎇ ${escapeHtml(g.branch || '?')}</span>${g.changed ? ` <span class="ui-chip warn">${g.changed} changed</span>` : g.changed === 0 ? ' <span class="ui-chip good">clean</span>' : ''}${g.ahead ? ` <span class="ui-chip plain">↑${g.ahead}</span>` : ''}${g.behind ? ` <span class="ui-chip warn">↓${g.behind}</span>` : ''}`;
    }
    function meter(p, max) {
      const pct = max > 1 ? Math.max(2, Math.round(Math.log(1 + sizeOf(p)) / Math.log(1 + max) * 100)) : 0;  // log: a whole drive dwarfs the rest
      return `<span class="wa-meter" title="Log scale"><i style="width:${pct}%"></i></span>`;
    }
    function row(p, max) {
      const [tone, label] = stateOf(p), k = keyOf(p);
      return `<tr data-key="prj-${escAttr(k)}" data-tone="${tone === 'bad' ? 'bad' : ''}" tabindex="0" class="${st.sel === k ? 'is-sel' : ''}"
          onclick="if (!event.target.closest('button, a')) Projects.open(this.dataset.key.slice(4))" onkeydown="if (event.key === 'Enter' && event.target === this) Projects.open(this.dataset.key.slice(4))">
        <td class="ui-name-cell"><span class="wa-title">${escapeHtml(p.name)}</span><span class="ui-sec mono">${escapeHtml(p.folder)}</span></td>
        <td data-label="State"><span class="ui-state ${tone === 'run' ? 'good wa-run' : tone}">${escapeHtml(label)}</span></td>
        <td data-label="Owner" class="ui-opt2">${escapeHtml(p.owner || '')}${(p.agents || []).length > 1 ? `<span class="ui-sec">+${p.agents.length - 1} more</span>` : ''}</td>
        <td data-label="Size" class="num">${p.size ? bytes(p.size.bytes) + (p.size.partial ? '+' : '') : p.exists ? 'Measuring…' : '—'}${meter(p, max)}</td>
        <td data-label="Files" class="num ui-opt">${p.size ? p.size.files.toLocaleString() : '…'}</td>
        <td data-label="Git">${gitChips(p.git)}</td>
        <td data-label="Pins" class="num ui-opt3">${(p.pins || []).length || '<span class="ui-dim">0</span>'}</td>
        <td class="ui-c-act">${WA.kebab(`Actions for ${p.name}`, 'prj-k-' + k).replace('<button', `<button onclick="Projects.menu(this, '${escAttr(k)}')"`)}</td></tr>`;
    }
    function card(p, max) {
      const [tone, label] = stateOf(p), k = keyOf(p), g = p.git, pins = p.pins || [];
      return `<article class="wa-card ${p.open ? 'live' : ''} ${p.exists ? '' : 'missing'} ${st.sel === k ? 'is-sel' : ''}" data-key="prj-${escAttr(k)}" tabindex="0"
          onclick="if (!event.target.closest('button, a')) Projects.open(this.dataset.key.slice(4))" onkeydown="if (event.key === 'Enter' && event.target === this) Projects.open(this.dataset.key.slice(4))">
        <header class="wa-card-head"><div class="wa-card-id"><h3>${escapeHtml(p.name)}</h3>
          <button type="button" class="prj-path" data-copy="${escAttr(p.folder)}" title="Copy the path">${escapeHtml(p.folder)}</button></div>
          <span class="ui-state ${tone === 'run' ? 'good wa-run' : tone}">${escapeHtml(label)}</span></header>
        <dl class="wa-card-nums"><div><dt>Size</dt><dd>${p.size ? bytes(p.size.bytes) : p.exists ? 'Measuring…' : '—'}</dd>${meter(p, max)}</div>
          <div><dt>Files</dt><dd>${p.size ? p.size.files.toLocaleString() : '…'}</dd></div><div><dt>Active</dt><dd>${escapeHtml(ago(p.last_active))}</dd></div></dl>
        <div class="wa-card-git">${gitChips(g)}${g && g.github ? `<a href="${escAttr(g.github)}" target="_blank" rel="noopener">${GH}${escapeHtml(g.github.replace('https://github.com/', ''))}</a>` : ''}</div>
        ${g && g.last_commit ? `<div class="wa-card-commit" title="${escAttr(g.last_commit.subject)}"><code>${escapeHtml(g.last_commit.hash)}</code> ${escapeHtml(g.last_commit.subject)}</div>` : ''}
        <footer class="wa-card-foot"><span class="ui-dim">Owner</span> <b>${escapeHtml(p.owner || '')}</b>
          <button type="button" class="ghost wa-pins-btn" onclick="Projects.open('${escAttr(k)}', 'pins')">${pins.length ? `${pins.length} pinned` : 'Pin files'}</button></footer>
      </article>`;
    }
    function render() {
      if (!$('prjRows')) return;
      if (_err && !_loaded) {
        WA.live('prj', false, "Couldn't load");
        $('prjGrid').innerHTML = `<div class="ui-empty">${loadErr('projects', _err)}</div>`; $('prjRows').innerHTML = '';
        deck(); return;
      }
      WA.live('prj', !_err, _err ? 'Refresh failed, showing the last list' : 'Live', _loaded);
      deck();
      const q = st.q.trim().toLowerCase();
      if ($('prjSearch').value !== st.q) $('prjSearch').value = st.q;
      $('prjSort').value = st.sort;
      document.querySelectorAll('#prjViews button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === st.view)));
      const list = _items.filter(p => !q || [p.name, p.folder, p.owner, ...(p.agents || []), p.git && p.git.github, p.git && p.git.branch]
        .some(v => v && String(v).toLowerCase().includes(q)));
      list.sort(st.sort === 'size' ? (a, b) => sizeOf(b) - sizeOf(a) : st.sort === 'name' ? (a, b) => a.name.localeCompare(b.name)
        : (a, b) => (b.open - a.open) || (b.last_active - a.last_active));
      $('prjCount').textContent = q ? `${list.length} of ${_items.length}` : `${_items.length} ${_items.length === 1 ? 'folder' : 'folders'}`;
      const max = Math.max(0, ..._items.map(sizeOf));
      const empty = `<div class="ui-empty">${_items.length ? '<b>No project matches that.</b><button type="button" class="ghost" onclick="Projects.search(\'\')">Clear the search</button>'
        : '<b>No projects yet.</b><span>A folder shows here once a Claude Code session has worked in it.</span>'}</div>`;
      $('prjTable').hidden = st.view !== 'table'; $('prjGrid').hidden = st.view === 'table';
      if (st.view === 'table') UI.rerender($('prjRows'), list.length ? list.map(p => row(p, max)).join('') : `<tr class="ui-empty-row"><td colspan="8">${empty}</td></tr>`);
      else UI.rerender($('prjGrid'), list.length ? list.map(p => card(p, max)).join('') : empty);
      if (st.sel && $('prjDrawer') && $('prjDrawer').open) drawDrawer();
    }
    function drawDrawer(focusPins) {
      const p = find(st.sel), w = WA.drawer('prjDrawer', 'the project');
      if (!p) { w.d.close(); return; }
      const [tone, label] = stateOf(p), g = p.git, pins = p.pins || [];
      w.title.textContent = p.name;
      w.sub.innerHTML = `<span class="ui-state ${tone === 'run' ? 'good' : tone}">${escapeHtml(label)}</span><button type="button" class="prj-path" data-copy="${escAttr(p.folder)}" title="Copy the path">${escapeHtml(p.folder)}</button>`;
      const typed = { path: ($('prjPinPath') || {}).value || '', note: ($('prjPinNote') || {}).value || '' };  // a refresh keeps what's being typed
      w.body.innerHTML = `<div class="ui-block"><div class="ui-nums"><div><b>${p.size ? bytes(p.size.bytes) + (p.size.partial ? '+' : '') : '…'}</b><span>size</span></div>
          <div><b>${p.size ? p.size.files.toLocaleString() : '…'}</b><span>files</span></div><div><b>${escapeHtml(ago(p.last_active))}</b><span>last active</span></div></div></div>
        <div class="ui-block"><h3>Who works here</h3><dl class="ui-dl"><dt>Owner</dt><dd>${escapeHtml(p.owner || '')}</dd>
          ${(p.agents || []).filter(a => a !== p.owner).length ? `<dt>Also</dt><dd>${p.agents.filter(a => a !== p.owner).map(escapeHtml).join(', ')}</dd>` : ''}</dl>
          ${(p.sessions || []).length ? `<ul class="wa-list">${p.sessions.map(s => `<li><span title="${escAttr(s.title || '')}">${escapeHtml(s.name)}${s.kind === 'job' ? ' <span class="ui-chip plain">job</span>' : ''}${s.open ? ' <span class="ui-chip good">open</span>' : ''}</span><span class="ui-sub">${escapeHtml(ago(s.last_active))}</span></li>`).join('')}</ul>
          ${p.sessions_total > p.sessions.length ? `<p class="hint">${p.sessions.length} of ${p.sessions_total} sessions shown.</p>` : ''}` : ''}</div>
        <div class="ui-block"><h3>Git</h3>${g ? `<div class="ui-btnrow">${gitChips(g)}${g.github ? `<a class="wa-gh" href="${escAttr(g.github)}" target="_blank" rel="noopener">${GH}${escapeHtml(g.github.replace('https://github.com/', ''))}</a>`
            : g.remote ? `<span class="ui-chip plain" title="${escAttr(g.remote)}">remote: ${escapeHtml(g.remote)}</span>` : '<span class="ui-chip plain">no remote</span>'}</div>
          ${g.last_commit ? `<p class="wa-commit"><code>${escapeHtml(g.last_commit.hash)}</code> ${escapeHtml(g.last_commit.subject)} <span class="ui-sub">${escapeHtml(ago(g.last_commit.at))}</span></p>` : ''}
          ${g.root && g.root.toLowerCase() !== p.folder.toLowerCase() ? `<p class="hint">Part of the repo at <code>${escapeHtml(g.root)}</code></p>` : ''}` : '<p class="hint">Not a git repo.</p>'}</div>
        ${(p.key_files || []).length ? `<div class="ui-block"><h3>Key files</h3><div class="ui-btnrow">${p.key_files.map(k =>
          `<button type="button" class="ui-chip plain wa-file" data-copy="${escAttr(p.folder + '\\' + k.path.replace(/\//g, '\\'))}" title="Copy the full path">${k.dir ? '▸ ' : ''}${escapeHtml(k.path)}</button>`).join('')}</div></div>` : ''}
        <div class="ui-block" id="prjPins"><h3>Pinned for agents <span class="ui-sub">${pins.length}</span></h3>
          <p class="hint">Files or folders an agent working here should read. Click a path to copy it.</p>
          ${pins.length ? `<ul class="wa-list wa-pins">${pins.map((pin, j) => `<li><button type="button" class="wa-file" data-copy="${escAttr(p.folder + '\\' + pin.path)}" title="Copy the full path">${pin.dir ? '▸ ' : ''}<code>${escapeHtml(pin.path)}</code></button>
            <em>${escapeHtml(pin.note || '')}</em><button type="button" class="ghost danger" data-unpin="${j}" aria-label="Unpin ${escAttr(pin.path)}">Unpin</button></li>`).join('')}</ul>`
            : '<p class="ui-dim">Nothing pinned yet.</p>'}
          ${p.exists ? `<form class="wa-pin-add" id="prjPinForm"><div class="ui-field"><label for="prjPinPath">Path in this folder</label><input id="prjPinPath" placeholder="e.g. docs/plan.md" autocomplete="off" spellcheck="false" value="${escAttr(typed.path)}"></div>
            <div class="ui-field"><label for="prjPinNote">Why an agent should look (optional)</label><input id="prjPinNote" autocomplete="off" value="${escAttr(typed.note)}"></div>
            <div class="ui-err" id="prjPinErr" role="alert"></div><button type="submit" class="ui-primary" id="prjPinBtn">Pin</button></form>` : ''}</div>`;
      w.body.querySelectorAll('[data-unpin]').forEach(b => { b.onclick = () => unpin(p, +b.dataset.unpin); });
      const f = $('prjPinForm');
      if (f) f.onsubmit = e => { e.preventDefault(); pin(p); };
      if (focusPins) { $('prjPins').scrollIntoView({ block: 'start' }); ($('prjPinPath') || w.title).focus(); }
    }
    async function pin(p) {
      const path = $('prjPinPath').value.trim(), err = $('prjPinErr');
      if (!path) { err.textContent = 'Type a path inside the folder to pin.'; $('prjPinPath').focus(); return; }
      await UI.busy($('prjPinBtn'), async () => {
        try {
          p.pins = (await apiCall('POST', '/api/projects/pins', { folder: p.folder, path, note: $('prjPinNote').value })).pins;
          $('prjPinPath').value = ''; $('prjPinNote').value = '';
          UI.toast(`Pinned ${path}`); render(); drawDrawer(); $('prjPinPath') && $('prjPinPath').focus();
        } catch (e) { err.textContent = "Couldn't pin: " + e.message; }
      });
    }
    async function unpin(p, j) {
      const path = p.pins[j].path;
      await UI.confirm({ title: `Unpin ${path}?`, body: `<p>Agents working in <b>${escapeHtml(p.name)}</b> stop being pointed at it. The file itself isn't touched.</p>`,
        action: 'Unpin', run: async () => {
          p.pins = (await apiCall('POST', '/api/projects/pins/remove', { folder: p.folder, path })).pins;
          UI.toast(`Unpinned ${path}`); render(); drawDrawer();
        } });
    }
    document.addEventListener('click', e => {
      const el = e.target.closest('#projectsView [data-copy], #prjDrawer [data-copy]');
      if (!el) return;
      e.stopPropagation();
      navigator.clipboard?.writeText(el.dataset.copy).then(() => UI.toast('Copied: ' + el.dataset.copy), () => msg(el.dataset.copy));
    }, true);
    function open(k, part) {
      st.sel = k; save();
      const w = WA.drawer('prjDrawer', 'the project');
      drawDrawer(part === 'pins');
      if (!w.d.open) { w.d.showModal(); if (part !== 'pins') w.title.focus(); }
      w.d.onclose = () => { const was = st.sel; st.sel = null; save(); render(); UI.refocus('prj-' + was); };
      document.querySelectorAll('#projectsView [data-key^="prj-"]').forEach(r => r.classList.toggle('is-sel', r.dataset.key === 'prj-' + k));
    }
    function menu(btn, k) {
      const p = find(k);
      if (!p) return;
      UI.menu(btn, [{ label: 'Open the details', run: () => open(k) }, { label: 'Pins', run: () => open(k, 'pins') },
        { label: 'Copy the path', run: () => navigator.clipboard?.writeText(p.folder).then(() => UI.toast('Copied: ' + p.folder)) },
        ...(p.git && p.git.github ? [{ label: 'Open on GitHub', run: () => window.open(p.git.github, '_blank', 'noopener') }] : [])]);
    }
    function wire() {
      Object.assign(st, UI.keep('cp-projects-page', st), { sel: null });
      $('prjSearch').addEventListener('input', e => { st.q = e.target.value; save(); render(); });
      $('prjSort').addEventListener('change', e => { st.sort = e.target.value; save(); render(); });
      document.querySelectorAll('#prjViews button').forEach(b => { b.onclick = () => { st.view = b.dataset.view; save(); render(); }; });
      UI.sticky($('prjWork'));
    }
    function start() { if (_timer) return; load(); _timer = setInterval(() => { if (!document.hidden) load(); }, 60000); }
    function stop() { clearInterval(_timer); _timer = null; clearTimeout(_soon); }
    return { load, render, start, stop, open, menu, wire, search(q) { st.q = q; save(); render(); }, sort(s) { st.sort = s; save(); render(); } };
  })();

  // ---------------------------------------------------------------------------------------------------------------
  // To-do: the deck. Tasks finished and failed per day over the last 14 days, from each closed task's last update
  // (when it finished), and readouts that open the matching tab. The example list draws no history: it has none.
  // ---------------------------------------------------------------------------------------------------------------
  /* global _todo, TODO_TABS, setTodoTab: the To-do page's own (pages-todo.js), which calls this once it's in */
  WA.todoDeck = () => {
    if (typeof _todo === 'undefined' || !$('todoMetrics')) return;
    const rows = _todo.rows || [], ex = _todo.example, now = Date.now(), since = dayStart(now) - 13 * DAY;
    const closedAt = st => rows.filter(t => t.status === st && t.updated).map(t => t.updated * 1000);
    const done = dayCounts(closedAt('done'), 14, now), failed = dayCounts(closedAt('failed'), 14, now);
    const sum = a => a.reduce((x, p) => x + p.v, 0);
    const n = f => rows.filter(f).length, TABS = typeof TODO_TABS !== 'undefined' ? TODO_TABS : [];
    const fit = key => (TABS.find(x => x[0] === key) || [0, 0, () => false])[2];
    const m = [['you', n(fit('you')), 'Need you', 'warn'], ['running', n(fit('running')), 'Running', 'live'], ['open', n(fit('open')), 'Open', ''],
      ['finished', ex ? n(fit('finished')) : rows.filter(t => t.status === 'done' && t.updated * 1000 >= since).length, ex ? 'Finished' : 'Done in 14 days', 'good'],
      ['failed', ex ? n(fit('failed')) : rows.filter(t => t.status === 'failed' && t.updated * 1000 >= since).length, ex ? 'Failed' : 'Failed in 14 days', 'warn']];
    $('todoMetrics').innerHTML = m.map(([k, v, l, tone]) => `<button type="button" class="ui-metric${v && tone ? ' ' + tone : ''}" data-key="todo-m-${k}"
        aria-pressed="${_todo.tab === k}" onclick="setTodoTab('${k}'); UI.refocus('todo-m-${k}')"><span class="ui-metric-num">${v}</span><span class="ui-metric-label">${l}</span></button>`).join('');
    $('todoProv').className = ex ? 'prov u' : 'prov';
    $('todoProv').textContent = ex ? 'example list: no history' : 'measured, per day';
    $('todoTraceMeta').textContent = ex ? '' : `${sum(done)} done, ${sum(failed)} failed in 14 days`;
    WA.trace($('todoTrace'), ex ? { series: [], label: 'No history for the example list', message: "The example list has no history. Your own tasks' results show here." }
      : { label: 'Tasks finished and failed per day, last 14 days', legendAlways: true,
        series: [{ name: 'Done', tone: 'measured', points: done }, { name: 'Failed', tone: 'bad', dashed: true, points: failed }],
        message: sum(done) + sum(failed) ? '' : 'Nothing finished in the last 14 days' });
  };

  // ---------------------------------------------------------------------------------------------------------------
  // Keys and passwords: Show value, in a dialog. The same route and checks as before (/api/vault/<name>/reveal: the
  // owner's password again, five wrong ones pause showing for ten minutes). The value only ever goes into one element's
  // textContent, for 30 seconds, and is cleared when the time is up or the dialog closes; never a URL, the console or
  // storage. The password box is emptied after every try.
  // ---------------------------------------------------------------------------------------------------------------
  WA.reveal = c => {
    let d = $('waReveal');
    if (!d) {
      d = document.createElement('dialog'); d.id = 'waReveal'; d.className = 'ui-dialog';
      d.setAttribute('aria-labelledby', 'waRevealTitle');
      document.body.appendChild(d);
    }
    let timer = 0, tick = 0;
    const label = c.label || c.name;
    d.innerHTML = `<form class="ui-dialog-in" autocomplete="off"><div class="ui-dialog-body"><h2 id="waRevealTitle">Show ${escapeHtml(label)}?</h2>
        <span class="ui-tag">Shown for 30 seconds</span>
        <div class="ui-dialog-text"><p>Type your Control Panel password. The value of <code>${escapeHtml(c.name)}</code> shows here for 30 seconds, then it's cleared.
          Showing it is recorded in the change log.</p></div>
        <div class="ui-field" data-ask><label for="waRevealPw">Your Control Panel password</label>
          <input type="password" id="waRevealPw" autocomplete="current-password" spellcheck="false"></div>
        <div class="wa-reveal-box" data-shown hidden><code aria-label="The value"></code>
          <button type="button" class="ghost" data-copy-value>Copy</button><span class="ui-sub" aria-live="off"></span></div>
        <div class="ui-err" role="alert"></div></div>
      <div class="ui-dialog-foot"><button type="button" class="ghost" data-x>Cancel</button>
        <button type="submit" class="ui-primary" data-ok>Show the value</button></div></form>`;
    const ok = d.querySelector('[data-ok]'), err = d.querySelector('.ui-err'), pw = d.querySelector('input'), box = d.querySelector('[data-shown]');
    const code = box.querySelector('code'), left = box.querySelector('.ui-sub');
    const clear = () => { clearTimeout(timer); clearInterval(tick); code.textContent = ''; box.hidden = true; };
    d.querySelector('[data-x]').onclick = () => { if (!ok.disabled) d.close(); };
    d.oncancel = e => { if (ok.disabled) e.preventDefault(); };
    d.onclose = () => { clear(); pw.value = ''; d.innerHTML = ''; };
    box.querySelector('[data-copy-value]').onclick = e => navigator.clipboard.writeText(code.textContent).then(() => { e.target.textContent = 'Copied'; }, () => {});
    d.querySelector('form').onsubmit = e => {
      e.preventDefault(); err.textContent = '';
      if (!box.hidden) { d.close(); return; }
      UI.busy(ok, async () => {
        let r;
        try { r = await apiCall('POST', `/api/vault/${encodeURIComponent(c.name)}/reveal`, { password: pw.value }); }
        catch (x) { pw.value = ''; err.textContent = x.message; pw.focus(); return; }
        pw.value = '';
        d.querySelector('[data-ask]').hidden = true;
        code.textContent = r.value;
        r = null;
        box.hidden = false;
        let n = 30;
        left.textContent = `Clears in ${n} s`;
        tick = setInterval(() => { n--; left.textContent = `Clears in ${n} s`; }, 1000);
        timer = setTimeout(() => d.close(), 30000);
        ok.textContent = 'Hide it now';
        d.querySelector('[data-x]').hidden = true;
      });
    };
    d.showModal();
    pw.focus();
  };

  // ---------------------------------------------------------------------------------------------------------------
  // Rules: four dials for how each request followed the protocol (each a measured share of the requests in the
  // window), and the recent guard activity and daily self-review as bounded feeds with a kind filter.
  // ---------------------------------------------------------------------------------------------------------------
  let _ru = null;
  WA.rulesDeck = d => {
    _ru = d;
    const q = d.requests || {}, n = q.total || 0, days = d.window_days || 7;
    WA.live('ru', true, 'Live', Date.now());
    $('ruTotal').textContent = n.toLocaleString();
    $('ruCode').textContent = (q.code_requests || 0).toLocaleString();
    $('ruPacksN').textContent = (d.packs || []).reduce((a, p) => a + p.rules.reduce((b, r) => b + r.guards.length, 0), 0);
    $('ruDialMeta').textContent = n ? `${n} requests, last ${days} days` : `last ${days} days`;
    [['Memory', 'memory_pct', 'Memory first', n], ['Routed', 'routed_pct', 'Routed', n], ['Delegated', 'delegated_pct', 'Local model', n],
      ['Checked', 'checked_pct', 'Tested', q.code_requests || 0]].forEach(([id, k, label, of]) => {
      const v = q[k];
      WA.dial($('ruDial' + id), v == null ? { status: 'unavailable', label, period: 'No requests yet' } : {
        status: 'ok', label, value: String(v), unit: '%', period: `of ${of.toLocaleString()}`, raw: v,
        tone: 'measured', arc: { frac: v / 100, title: `${v}% of ${of} requests` },
        aria: `${label}: ${v}% of ${of} requests in the last ${days} days` });
    });
  };
  WA.rulesFeeds = d => {
    d = d || _ru;
    if (!d) return;
    const titleOf = {};
    (d.packs || []).forEach(p => p.rules.forEach(r => r.guards.forEach(g => { titleOf[g.id] = g.title; })));
    const kind = $('ruEventsKind').value, ev = (d.recent || []).filter(e => kind === 'all' || ruleKind(e.decision) === kind);
    const stops = (d.recent || []).filter(e => ruleKind(e.decision) === 'stop').length;
    $('ruEventsCount').textContent = (d.recent || []).length ? `${stops} of the last ${(d.recent || []).length} stopped something` : '';
    UI.feed($('ruEvents'), ev.map((e, i) => {
      const stop = ruleKind(e.decision) === 'stop';
      const what = titleOf[e.guard] ? `${stop ? 'Stopped' : 'Noted'}: ${titleOf[e.guard]}` : ((typeof RULE_DECISION !== 'undefined' ? RULE_DECISION : {})[e.decision] || e.decision);
      return `<div class="wa-ev ui-feed-row" tabindex="-1"><span class="wa-ev-dot ${stop ? 'stop' : ''}" aria-hidden="true"></span>
        <span class="wa-ev-when" title="${escAttr(e.t || '')}">${escapeHtml(agoText(e.t))}</span><span class="wa-ev-what">${escapeHtml(what)}</span>
        <span class="wa-ev-side">${stop ? 'stopped' : 'spoke up'}</span>${e.detail ? `<span class="wa-ev-detail" title="${escAttr(e.detail)}">${escapeHtml(e.detail)}</span>` : ''}</div>`;
    }), { key: 'ru-ev-' + kind, limit: 8, empty: kind === 'all' ? 'Nothing has needed stopping yet.' : 'Nothing of that kind recently.' });
    const sr = d.self_review;
    $('ruReviewSec').hidden = !sr;
    if (!sr) return;
    const look = new Set((sr.look_at || []).map(x => x.class)), rk = $('ruReviewKind').value;
    $('ruReviewDate').textContent = `${sr.date}: ${sr.stops} ${sr.stops === 1 ? 'stop' : 'stops'}`;
    UI.feed($('ruReview'), (sr.top || []).filter(t => rk === 'all' || look.has(t.class)).map(t => `<div class="wa-ev ui-feed-row" tabindex="-1">
        <span class="wa-ev-dot ${look.has(t.class) ? 'stop' : ''}" aria-hidden="true"></span><span class="wa-ev-when">${Number(t.stops)}×</span>
        <span class="wa-ev-what">${escapeHtml(titleOf[t.class] || t.class)}</span>
        <span class="wa-ev-side">${t.sessions} ${t.sessions === 1 ? 'session' : 'sessions'}, ${Math.round((t.retry_rate || 0) * 100)}% retried${look.has(t.class) ? ' · look at this' : ''}</span></div>`),
      { key: 'ru-sr-' + rk, limit: 6, empty: rk === 'all' ? 'No stops that day.' : 'Nothing to look at from that day.' });
  };

  function wire() {
    if ($('obsWork')) wireObs();
    if ($('prjWork')) root.Projects.wire();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire); else wire();
  // what switchTab does for these two pages (panel.js's Pages: this file loads the first time one of them opens, or
  // the To-do, Rules or Settings page, which share its parts)
  if (root.Pages) {
    root.Pages.on('observations', { enter: () => loadObservations() });
    root.Pages.on('projects', { enter: () => root.Projects.start(), leave: () => root.Projects.stop() });
  }
})(typeof window !== 'undefined' ? window : globalThis);
