/* The "ops" pages of the Control Panel (2026-10-27 revamp): Savings, Models, Tools, Run, Containers, Uptime and
   Proxmox, drawn to the Overview cockpit's standard with the telemetry engine (telemetry.js) and the shared list kit
   in panel.js (UI: confirm, menu, toast, keep; the ui-* classes).
   What it keeps: every figure comes from the page's own API reply and is labelled measured, estimated or not
   available; nothing moves unless a real reading changed; no poll is faster than before, none runs while the tab is
   hidden, and none overlaps the last one. Each control posts to the same route it always did (named beside it).
   The pure helpers at the top are what dashboard/js_tests/pages_ops_test.js exercises. */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HSOps = api;
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  'use strict';
  const hasDOM = typeof document !== 'undefined';

  // ---------------------------------------------------------------------------------------------------------------
  // Pure helpers
  // ---------------------------------------------------------------------------------------------------------------
  const lc = s => String(s == null ? '' : s).toLowerCase();
  const frac = (a, b) => (a == null || !isFinite(a) || !b || !isFinite(b)) ? null : Math.max(0, Math.min(1, a / b));
  // a share's tone: bad from 90%, warn from 75%, nothing to say below (status colours only where something needs a look)
  const toneOf = (f, warn, bad) => f == null ? 'none' : f >= (bad || 0.9) ? 'bad' : f >= (warn || 0.75) ? 'warn' : 'ok';
  function cmp(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

  // Tools page: search the id and description, then area, on/off, risk; sort by name, calls, last use, speed or errors.
  function toolRows(tools, f) {
    f = f || {};
    const q = lc(f.q).trim();
    const rows = (tools || []).filter(t => t && (!q || lc(t.id).includes(q) || lc(t.description).includes(q))
      && (!f.cat || t.category === f.cat)
      && (!f.on || f.on === 'all' || (f.on === 'off' ? !t.enabled : !!t.enabled))
      && (!f.risk || f.risk === 'all' || t.risk === f.risk));
    const by = {
      name: (a, b) => cmp(a.id, b.id),
      calls: (a, b) => (b.calls || 0) - (a.calls || 0) || cmp(a.id, b.id),
      recent: (a, b) => (b.last_call || 0) - (a.last_call || 0) || cmp(a.id, b.id),
      slow: (a, b) => (b.avg_ms == null ? -1 : b.avg_ms) - (a.avg_ms == null ? -1 : a.avg_ms) || cmp(a.id, b.id),
      errors: (a, b) => (b.errors || 0) - (a.errors || 0) || cmp(a.id, b.id),
    };
    return rows.slice().sort(by[f.sort] || by.name);
  }
  function toolTotals(tools) {
    const list = tools || [], calls = list.reduce((a, t) => a + (t.calls || 0), 0), errors = list.reduce((a, t) => a + (t.errors || 0), 0);
    return { total: list.length, on: list.filter(t => t.enabled).length, off: list.filter(t => !t.enabled).length,
             calls, errors, success: calls ? (calls - errors) / calls : null, used: list.filter(t => t.calls).length };
  }

  // Containers: docker.container.list rows {id, name, image, status, ports}
  function portList(ports) {
    const out = [];
    Object.entries(ports || {}).forEach(([inner, binds]) => {
      if (!Array.isArray(binds) || !binds.length) return;
      const seen = new Set();
      binds.forEach(b => { const p = b && b.HostPort; if (p && !seen.has(p)) { seen.add(p); out.push(`${p}→${inner}`); } });
    });
    return out;
  }
  function containerRows(list, f) {
    f = f || {};
    const q = lc(f.q).trim();
    const rows = (list || []).filter(c => c && (!q || lc(c.name).includes(q) || lc(c.image).includes(q) || lc(c.id).includes(q))
      && (!f.state || f.state === 'all' || (f.state === 'running' ? c.status === 'running' : c.status !== 'running')));
    const by = { name: (a, b) => cmp(lc(a.name), lc(b.name)), image: (a, b) => cmp(lc(a.image), lc(b.image)) || cmp(lc(a.name), lc(b.name)),
                 state: (a, b) => ((a.status === 'running') - (b.status === 'running')) || cmp(lc(a.name), lc(b.name)) };
    return rows.slice().sort(by[f.sort] || by.name);
  }
  function containerCounts(list) {
    const l = list || [];
    return { total: l.length, running: l.filter(c => c.status === 'running').length, stopped: l.filter(c => c.status !== 'running').length,
             images: new Set(l.map(c => c.image)).size, published: l.filter(c => portList(c.ports).length).length };
  }

  // Uptime: uptime.status rows {id, name, type, active, status: up|down|unknown, message, last_check}. Down first.
  const MON_ORDER = { down: 0, unknown: 1, up: 2 };
  function monitorRows(list, f) {
    f = f || {};
    const q = lc(f.q).trim();
    return (list || []).filter(m => m && (!q || lc(m.name).includes(q) || lc(m.message).includes(q) || lc(m.type).includes(q))
      && (!f.state || f.state === 'all' || (f.state === 'paused' ? m.active === false : f.state === 'unknown' ? m.status === 'unknown' && m.active !== false : m.status === f.state))
      && (!f.type || m.type === f.type))
      .slice().sort((a, b) => ((MON_ORDER[a.status] ?? 1) - (MON_ORDER[b.status] ?? 1)) || cmp(lc(a.name), lc(b.name)));
  }
  // Kuma writes heartbeat times as UTC "YYYY-MM-DD HH:MM:SS[.fff]"; anything else stays unknown (null).
  function kumaTime(s) {
    if (!s || typeof s !== 'string') return null;
    const m = s.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
    if (!m) return null;
    const t = Date.parse(`${m[1]}T${m[2]}Z`);
    return isFinite(t) ? t / 1000 : null;
  }

  // Run page: the same tool list, searched by id and description, by area and risk.
  function runRows(tools, f) {
    f = f || {};
    const q = lc(f.q).trim();
    return (tools || []).filter(t => t && (!q || lc(t.id).includes(q) || lc(t.description).includes(q))
      && (!f.cat || t.category === f.cat) && (!f.risk || f.risk === 'all' || t.risk === f.risk))
      .slice().sort((a, b) => cmp(a.id, b.id));
  }

  // Savings: /api/savings {daily: {daily, summary, not_set_up?}, discover, estimate, money}. RTK's figures are
  // measured; the rest is estimated (characters / 4). The two are never added into one total.
  function savingsFigures(d) {
    d = d || {};
    const daily = d.daily || null, sum = daily && daily.summary, est = d.estimate || null;
    const rule = k => est && est.by_rule ? Number(est.by_rule[k]) || 0 : 0;
    const cpt = (est && est.chars_per_token) || 4, fd = est && est.front_door_total;
    const rtk = sum ? { tokens: Number(sum.total_saved) || 0, commands: Number(sum.total_commands) || 0,
                        pct: sum.avg_savings_pct == null ? null : Number(sum.avg_savings_pct) / 100 } : null;
    const e = est ? { generation: rule('generation'), reading: rule('reading'), retrieval: rule('retrieval'),
                      frontDoor: fd ? Math.floor(((Number(fd.hidden_chars) || 0) + (Number(fd.trimmed_chars) || 0)) / cpt) : null } : null;
    const disc = d.discover || null;
    let missed = null;
    if (disc && disc.available) {
      const rows = (disc.missed || []).slice().sort((a, b) => (b.estimated_savings_tokens || 0) - (a.estimated_savings_tokens || 0));
      missed = { rows, tokens: rows.reduce((a, m) => a + (m.estimated_savings_tokens || 0), 0), total: disc.total_commands || 0,
                 through: disc.already_rtk || 0, coverage: frac(disc.already_rtk || 0, disc.total_commands || 0),
                 sessions: disc.sessions_scanned, days: disc.since_days };
    }
    return {
      rtk, rtkWhy: rtk ? null : daily && daily.not_set_up ? 'not-set-up' : daily && daily.error ? 'error' : 'none',
      daily: (daily && Array.isArray(daily.daily)) ? daily.daily : [],
      est: e, estTotal: e ? e.generation + e.reading + e.retrieval + (e.frontDoor || 0) : null, cpt,
      hours: est && Array.isArray(est.hours) ? est.hours : null,
      missed, missedWhy: missed ? null : !disc ? 'hidden' : disc.not_set_up ? 'not-set-up' : disc.error ? 'error' : 'none',
      missedError: disc && disc.error ? String(disc.error) : '',
    };
  }
  // The 12-week calendar (Monday-first columns ending this week). level 0-4 by share of the busiest day; future days
  // are left out; a day with no RTK row is "no activity", not zero tokens measured.
  function heatCells(rows, today) {
    today = today ? new Date(today) : new Date();
    today.setHours(0, 0, 0, 0);
    const by = {};
    (rows || []).forEach(r => { if (r && typeof r.date === 'string') by[r.date] = r; });
    const max = Math.max(1, ...(rows || []).map(r => Number(r && r.saved_tokens) || 0));
    const start = new Date(today);
    start.setDate(start.getDate() - ((today.getDay() + 6) % 7) - 7 * 11);
    const out = [];
    const key = dt => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
    for (let dt = new Date(start); dt <= today; dt.setDate(dt.getDate() + 1)) {
      const k = key(dt), r = by[k], v = r ? Number(r.saved_tokens) || 0 : null, s = v ? v / max : 0;
      out.push({ key: k, v, row: r || null, level: !v ? 0 : s > 0.75 ? 4 : s > 0.5 ? 3 : s > 0.25 ? 2 : 1, today: dt.getTime() === today.getTime(),
                 dow: (dt.getDay() + 6) % 7, month: dt.getDate() <= 7 && dt.getDay() === 1 ? dt.toLocaleDateString('en-GB', { month: 'short' }) : '' });
    }
    return out;
  }

  // Proxmox: the cluster's own readings, added up over the nodes that answered (CPU weighted by each node's threads).
  function pveTotals(d) {
    const nodes = ((d && d.nodes) || []).filter(n => n && n.status === 'online');
    const w = n => Number(n.threads || n.cpus) || 1;
    const withCpu = nodes.filter(n => n.cpu_pct != null && isFinite(n.cpu_pct));
    const cpu = withCpu.length ? withCpu.reduce((a, n) => a + n.cpu_pct * w(n), 0) / withCpu.reduce((a, n) => a + w(n), 0) : null;
    const sum = k => nodes.reduce((a, n) => a + (Number(n[k]) || 0), 0);
    const shared = ((d && d.storage) || []).filter(s => s && s.shared && s.total);
    const guests = ((d && d.guests) || []).filter(g => g && !g.template);
    return { cpu: cpu == null ? null : cpu / 100, threads: nodes.reduce((a, n) => a + (Number(n.threads || n.cpus) || 0), 0),
             memUsed: sum('mem_used'), memTotal: sum('mem_total'), diskUsed: sum('root_used'), diskTotal: sum('root_total'),
             sharedUsed: shared.reduce((a, s) => a + (Number(s.used) || 0), 0), sharedTotal: shared.reduce((a, s) => a + (Number(s.total) || 0), 0),
             online: nodes.length, nodes: ((d && d.nodes) || []).length, guests: guests.length,
             running: guests.filter(g => g.status === 'running').length };
  }
  // Proxmox's history rows {time (s), key: value} into Pulse points {t (ms), v}; a missing reading stays a gap.
  function pvePoints(rows, key, f) {
    return (rows || []).filter(p => p && p.time).map(p => {
      const v = p[key];
      return { t: p.time * 1000, v: v == null || !isFinite(v) ? null : (f ? f(Number(v)) : Number(v)) };
    });
  }

  // The fine tick scale used in tables and the metrics strip: how many of n marks are lit for a share.
  function litTicks(f, n) { return f == null ? null : Math.round(Math.max(0, Math.min(1, f)) * (n || 20)); }

  const pure = { frac, toneOf, toolRows, toolTotals, portList, containerRows, containerCounts, monitorRows, kumaTime,
                 runRows, savingsFigures, heatCells, pveTotals, pvePoints, litTicks };
  if (!hasDOM) return pure;

  // ---------------------------------------------------------------------------------------------------------------
  // The kit: metrics strip, tick scales, the cockpit switch, one side drawer
  // ---------------------------------------------------------------------------------------------------------------
  const T = root.HSTelemetry;
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const num = n => n == null || !isFinite(n) ? '—' : T ? T.fmtNum(n) : String(n);
  const ms = v => v == null || !isFinite(v) ? '—' : T ? T.fmtMs(v) : Math.round(v) + ' ms';
  // a time on a graph's axis: short enough for its gutter (the hover gives the full figure)
  const msAxis = v => v == null || !isFinite(v) ? '' : v >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 's' : Math.round(v) + 'ms';
  const pctTxt = f => f == null ? '—' : (f * 100).toFixed(f >= 0.995 || f === 0 ? 0 : 1) + '%';
  const agoS = s => { if (!s) return 'never'; const d = Math.max(0, Date.now() / 1000 - s);
    return d < 60 ? 'just now' : d < 3600 ? Math.floor(d / 60) + 'm ago' : d < 86400 ? Math.floor(d / 3600) + 'h ago' : Math.floor(d / 86400) + 'd ago'; };
  const hm = () => new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const KEBAB = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="3" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="8" cy="13" r="1.5"/></svg>';
  const kebab = (key, label) => `<button type="button" class="ui-kebab" data-key="${esc(key)}" data-menu aria-haspopup="menu" aria-expanded="false" aria-label="${esc(label)}">${KEBAB}</button>`;
  // panel.js's shared list kit: a page-level const (not a window property), so read by its bare name; it is
  // defined by the time any page runs
  /* global UI, _lifetimeCaps, _activityEntries */
  const kit = () => { try { return UI; } catch (e) { return null; } };
  // pages-overview.js's page-level lets (not window properties): read by their bare names, guarded
  function pageVar(k) { try { return k === 'caps' ? _lifetimeCaps : _activityEntries; } catch (e) { return null; } }
  const isOpen = view => { const v = $(view); return !!v && v.classList.contains('active') && !document.hidden; };

  // A fine scale of 20 marks with a share of them lit: a reading, not a bar. f null: hatched (no reading).
  function ticks(f, tone, label) {
    if (f == null) return `<span class="sx-ticks none" aria-hidden="true"></span>`;
    return `<span class="sx-ticks" data-tone="${esc(tone || 'ok')}" style="--f:${(Math.max(0, Math.min(1, f))).toFixed(3)}"${label ? ` title="${esc(label)}"` : ' aria-hidden="true"'}></span>`;
  }
  // One metrics strip cell. filter: makes it a button that filters the list (aria-pressed shows which is on).
  function cell(c) {
    const tag = c.filter ? 'button' : 'div';
    const attrs = c.filter ? ` type="button" data-filter="${esc(c.filter)}" aria-pressed="${c.pressed ? 'true' : 'false'}"` : '';
    return `<${tag} class="ui-metric sx-metric${c.tone && c.tone !== 'ok' && c.tone !== 'none' ? ' ' + c.tone : ''}"${attrs}${c.title ? ` title="${esc(c.title)}"` : ''}>
      <span class="sx-metric-top"><span class="ui-metric-num">${c.num}</span>${c.prov ? `<span class="prov ${c.prov}">${c.prov === 'm' ? 'measured' : c.prov === 'e' ? 'estimate' : 'no data'}</span>` : ''}</span>
      <span class="ui-metric-label">${esc(c.label)}</span>${c.f !== undefined ? ticks(c.f, c.tone) : ''}</${tag}>`;
  }
  function strip(el, cells, onFilter) {
    if (!el) return;
    el.innerHTML = cells.map(cell).join('');
    if (onFilter) el.querySelectorAll('[data-filter]').forEach(b => { b.onclick = () => onFilter(b.dataset.filter); });
  }
  function live(id, state, text) {
    const el = $(id);
    if (!el) return;
    el.className = 'ui-live' + (state === 'ok' ? ' ok' : state === 'bad' ? ' bad' : '');
    const t = el.querySelector('[data-text]');
    if (t) t.textContent = text;
  }
  function state(cls, word) { return `<span class="ui-state ${cls}">${esc(word)}</span>`; }

  // Cockpit on or off for the pages with instruments (the Overview's switch and key, cp-cockpit): off shows the same
  // figures as plain numbers and nothing moves. One switch in each page bar, all kept in step.
  const Mode = (() => {
    const on = () => !T || T.store.get('cp-cockpit') !== 'off';
    function sync() {
      const v = on();
      document.querySelectorAll('.sx-page[data-cockpit]').forEach(p => p.classList.toggle('sx-classic', !v));
      document.querySelectorAll('[data-sx-mode]').forEach(b => {
        b.setAttribute('aria-pressed', String(v));
        b.title = v ? 'Cockpit on: dials and traces. Click for plain numbers' : 'Cockpit off: plain numbers. Click for the dials and traces';
      });
    }
    function set(v) {
      if (T) T.store.set('cp-cockpit', v ? null : 'off');
      const ck = $('cockpit'), btn = $('ckMode');  // the Overview's own switch shows the same state when it's next opened
      if (ck) ck.classList.toggle('classic', !v);
      if (btn) btn.setAttribute('aria-pressed', String(v));
      sync();
      root.dispatchEvent(new Event('resize'));  // traces measure themselves again
    }
    document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-sx-mode]'); if (b) set(!on()); });
    return { on, set, sync };
  })();

  // One side drawer for every ops page (a modal <dialog>: Esc closes it, focus stays inside, focus goes back after).
  const Drawer = (() => {
    let d = null, cur = null, back = null, watch = null;
    function build() {
      d = document.createElement('dialog');
      d.className = 'ui-drawer sx-drawer'; d.id = 'sxDrawer'; d.setAttribute('aria-labelledby', 'sxDrawerTitle');
      d.innerHTML = `<div class="ui-drawer-head"><div class="ui-drawer-top"><div class="ui-drawer-id"><h2 id="sxDrawerTitle" tabindex="-1"></h2>
          <div class="ui-drawer-sub" id="sxDrawerSub"></div></div>
          <button type="button" class="ui-x" data-x aria-label="Close the details"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
        <div class="ui-tabs" role="tablist" id="sxDrawerTabs" aria-label="Details"></div></div>
        <div class="ui-drawer-body" id="sxDrawerBody" role="tabpanel" tabindex="-1"></div>`;
      document.body.appendChild(d);
      d.querySelector('[data-x]').onclick = () => close();
      d.addEventListener('close', () => {
        if (watch) { watch.disconnect(); watch = null; }
        const c = cur; cur = null;
        if (c && c.onClose) c.onClose();
        if (back && back.isConnected) back.focus({ preventScroll: true });
      });
      d.addEventListener('click', e => { if (e.target === d) close(); });  // a click on the backdrop
    }
    function tabs() {
      const el = $('sxDrawerTabs');
      el.hidden = !(cur.tabs && cur.tabs.length > 1);
      el.innerHTML = (cur.tabs || []).map(t => `<button type="button" class="ui-tab" role="tab" id="sxTab-${esc(t.id)}" aria-selected="${t.id === cur.tab}" tabindex="${t.id === cur.tab ? 0 : -1}" data-tab="${esc(t.id)}">${esc(t.label)}</button>`).join('');
      el.querySelectorAll('[data-tab]').forEach(b => { b.onclick = () => select(b.dataset.tab, true); });
      el.onkeydown = e => {
        const ids = cur.tabs.map(t => t.id), i = ids.indexOf(cur.tab);
        const go = j => { e.preventDefault(); select(ids[(j + ids.length) % ids.length], true); el.querySelector(`[data-tab="${ids[(j + ids.length) % ids.length]}"]`).focus(); };
        if (e.key === 'ArrowRight') go(i + 1); else if (e.key === 'ArrowLeft') go(i - 1);
      };
    }
    function select(tab, user) {
      if (!cur) return;
      cur.tab = tab;
      if (cur.onTab) cur.onTab(tab);
      tabs();
      const body = $('sxDrawerBody');
      body.setAttribute('aria-labelledby', 'sxTab-' + tab);
      cur.render(tab, body);
      if (user) body.scrollTop = 0;
    }
    function open(o) {
      if (!d) build();
      const again = cur && cur.key === o.key;
      if (!again) back = document.activeElement;
      cur = Object.assign({}, o, { tab: o.tab && (o.tabs || []).some(t => t.id === o.tab) ? o.tab : (o.tabs && o.tabs[0] ? o.tabs[0].id : 'main') });
      head(o);
      select(cur.tab);
      if (!d.open) d.showModal();
      if (!again) $('sxDrawerTitle').focus({ preventScroll: true });
      // a page switch (keys 1-9) closes it, so it never sits over another page
      if (watch) watch.disconnect();
      const view = o.view && $(o.view);
      if (view && root.MutationObserver) {
        watch = new MutationObserver(() => { if (!view.classList.contains('active')) close(); });
        watch.observe(view, { attributes: true, attributeFilter: ['class'] });
      }
    }
    function head(o) { $('sxDrawerTitle').textContent = o.title; $('sxDrawerSub').innerHTML = o.sub || ''; }
    function close() { if (d && d.open) d.close(); }
    // the open one again, after a refresh: the same tab, its scroll and focus kept
    function refresh(key, o) {
      if (!cur || cur.key !== key || !d.open) return false;
      Object.assign(cur, o || {});
      head(cur);
      const body = $('sxDrawerBody'), top = body.scrollTop, a = document.activeElement, k = a && body.contains(a) ? a.dataset.key : null;
      if (cur.live !== false) cur.render(cur.tab, body, true);
      body.scrollTop = top;
      if (k) { const again = body.querySelector(`[data-key="${CSS.escape(k)}"]`); if (again) again.focus({ preventScroll: true }); }
      return true;
    }
    return { open, close, refresh, key: () => cur && d && d.open ? cur.key : null, tab: () => cur && cur.tab };
  })();

  // Row clicks: a row with data-key opens; its buttons, links, switches and menus keep their own job.
  function rowClicks(tbody, onOpen, onMenu) {
    tbody.addEventListener('click', e => {
      const m = e.target.closest('[data-menu]');
      if (m) { e.stopPropagation(); onMenu(m.closest('tr').dataset.key, m); return; }
      if (e.target.closest('input, label, a, select, textarea, button:not([data-open])')) return;
      const tr = e.target.closest('tr[data-key]');
      if (tr) onOpen(tr.dataset.key);
    });
  }
  // Pulses and Reactors are made once and kept (each listens for resizes and theme changes for as long as it lives).
  const _inst = new Map();
  function keep(key, make) { if (!_inst.has(key)) _inst.set(key, make()); return _inst.get(key); }
  function hostOf(key) { const i = _inst.get(key); return i ? i.host : null; }
  function el(tag, cls) { const n = document.createElement(tag); if (cls) n.className = cls; return n; }

  // ---------------------------------------------------------------------------------------------------------------
  // Tools: one row per tool, a switch on each, its stats in the drawer. GET /api/tools, POST /api/tools/{id}/enable|disable
  // ---------------------------------------------------------------------------------------------------------------
  const REPLACED_BY = { 'memory.capture': 'memory.remember_fact', 'memory.recall': 'memory.recall_relevant', 'workflow.run': 'reasoning.pipeline' };
  const Tools = (() => {
    let list = [], fail = null, at = 0, busy = false, loaded = false;
    const st = () => kit().keep('cp-tools-page', { q: '', cat: '', on: 'all', risk: 'all', sort: 'name', shown: 60, sel: null, tab: 'overview' });
    let S = null;
    const save = () => kit().save('cp-tools-page', S);
    async function load() {
      if (busy) return;
      busy = true;
      try {
        const d = await root.apiCall('GET', '/api/tools');
        list = d.tools || []; fail = null; at = Date.now(); loaded = true;
      } catch (e) { fail = e; }
      finally { busy = false; }
      render();
    }
    function bind() {
      S = S || st();
      const on = (id, ev, f) => { const n = $(id); if (n) n.addEventListener(ev, f); };
      $('tlSearch').value = S.q; $('tlOnSel').value = S.on; $('tlRisk').value = S.risk; $('tlSort').value = S.sort;
      on('tlSearch', 'input', e => { S.q = e.target.value; S.shown = 60; save(); renderRows(); });
      ['tlCat:cat', 'tlOnSel:on', 'tlRisk:risk', 'tlSort:sort'].forEach(p => { const [id, k] = p.split(':'); on(id, 'change', e => { S[k] = e.target.value; S.shown = 60; save(); renderRows(); }); });
      on('tlClear', 'click', () => { Object.assign(S, { q: '', cat: '', on: 'all', risk: 'all', sort: 'name' }); $('tlSearch').value = ''; save(); render(); });
      rowClicks($('tlRows'), key => openTool(key.slice(5)), (key, btn) => menu(key.slice(5), btn));
      $('tlRows').addEventListener('change', e => { const b = e.target.closest('input[data-switch]'); if (b) toggle(b.dataset.switch, b); });
      kit().sticky($('tlWork'));
    }
    function render() {
      if (!S) bind();
      const tt = toolTotals(list);
      live('tlLive', fail ? 'bad' : loaded ? 'ok' : '', fail ? "Can't reach HomeShed" : loaded ? 'Live' : 'Loading…');
      $('tlUpdated').textContent = at ? 'Updated ' + new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
      strip($('tlMetrics'), [
        { num: loaded ? `${tt.on}<small>/${tt.total}</small>` : '—', label: 'Switched on', f: loaded ? frac(tt.on, tt.total) : undefined, filter: 'on', pressed: S.on === 'on', title: 'Show the tools that are on' },
        { num: loaded ? String(tt.off) : '—', label: 'Switched off', tone: tt.off ? 'warn' : 'ok', filter: 'off', pressed: S.on === 'off', title: 'Show the tools that are off' },
        { num: loaded ? num(tt.calls) : '—', label: 'Calls so far', prov: loaded ? 'm' : '' },
        { num: loaded ? pctTxt(tt.success) : '—', label: 'Succeeded', f: loaded ? tt.success : undefined, tone: tt.success != null && tt.success < 0.9 ? 'warn' : 'ok' },
        { num: loaded ? String(tt.used) : '—', label: 'Tools ever used', f: loaded ? frac(tt.used, tt.total) : undefined },
      ], f => { S.on = S.on === f ? 'all' : f; $('tlOnSel').value = S.on; save(); render(); });
      const cats = [...new Set(list.map(t => t.category))].sort(), sel = $('tlCat');
      if (sel.options.length !== cats.length + 1) { sel.innerHTML = '<option value="">All areas</option>' + cats.map(c => `<option>${esc(c)}</option>`).join(''); }
      sel.value = cats.includes(S.cat) ? S.cat : '';
      const risks = [...new Set(list.map(t => t.risk))].sort(), rs = $('tlRisk');
      if (rs.options.length !== risks.length + 1) rs.innerHTML = '<option value="all">Any risk</option>' + risks.map(r => `<option value="${esc(r)}">${r === 'read' ? 'Read only' : r === 'write' ? 'Can change things' : esc(r)}</option>`).join('');
      rs.value = risks.includes(S.risk) ? S.risk : 'all';
      renderRows();
      if (Drawer.key() && Drawer.key().startsWith('tool:')) { const t = list.find(x => 'tool:' + x.id === Drawer.key()); if (t) Drawer.refresh('tool:' + t.id, drawerOpts(t)); }
      else if (S.sel && !Drawer.key() && isOpen('toolsView') && list.some(t => t.id === S.sel)) openTool(S.sel);  // reopened after a reload
    }
    function renderRows() {
      const body = $('tlRows');
      if (!loaded) {
        body.innerHTML = `<tr class="ui-empty-row"><td colspan="9"><div class="${fail ? 'ui-empty' : 'loading'}">${fail ? root.loadErr('tools', fail) : 'Loading…'}</div></td></tr>`;
        $('tlCount').textContent = ''; $('tlMore').innerHTML = ''; return;
      }
      const rows = toolRows(list, S), shown = rows.slice(0, S.shown);
      const filtered = S.q || S.cat || S.on !== 'all' || S.risk !== 'all';
      $('tlClear').hidden = !(filtered || S.sort !== 'name');
      $('tlCount').textContent = filtered ? `${rows.length} of ${list.length}` : `${list.length} tools`;
      const html = shown.map(t => {
        const sr = t.success_rate, key = 'tool:' + t.id;
        const cls = [t.enabled ? '' : 'sx-off', S.sel === t.id && Drawer.key() === key ? 'is-sel' : ''].filter(Boolean).join(' ');
        return `<tr data-key="${esc(key)}"${t.enabled ? '' : ' data-tone="warn"'}${cls ? ` class="${cls}"` : ''}>
          <td class="sx-c-sw"><label class="sw" title="${t.enabled ? 'Switch off' : 'Switch on'}"><input type="checkbox" data-switch="${esc(t.id)}" data-key="sw:${esc(t.id)}" ${t.enabled ? 'checked' : ''} aria-label="${esc(t.id)}: ${t.enabled ? 'on' : 'off'}"><span></span></label></td>
          <td class="ui-name-cell"><button type="button" class="ui-rowlink" data-open data-key="${esc(key)}">${esc(t.id)}</button>${REPLACED_BY[t.id] ? ` <span class="ui-chip plain" title="A newer tool does this job">older: ${esc(REPLACED_BY[t.id])}</span>` : ''}<span class="ui-sec" title="${esc(t.description || '')}">${esc(t.description || '')}</span></td>
          <td class="ui-opt" data-label="Area">${esc(t.category)}</td>
          <td data-label="Scope"><span class="ui-chip${t.risk === 'read' ? ' plain' : ' warn'}">${esc(t.scope)}</span></td>
          <td class="num" data-label="Calls">${(t.calls || 0).toLocaleString('en-GB')}</td>
          <td class="num ui-opt2 sx-c-meter" data-label="Succeeded">${sr == null ? '<span class="ui-dim">—</span>' : `${pctTxt(sr)}${ticks(sr, sr < 0.9 ? 'warn' : 'ok')}`}</td>
          <td class="num ui-opt" data-label="Speed">${t.avg_ms == null ? '<span class="ui-dim">—</span>' : ms(Math.round(t.avg_ms))}</td>
          <td class="num" data-label="Last used">${t.last_call ? agoS(t.last_call) : '<span class="ui-dim">never</span>'}</td>
          <td class="ui-c-act">${kebab('menu:' + t.id, 'Actions for ' + t.id)}</td></tr>`;
      }).join('') || `<tr class="ui-empty-row"><td colspan="9"><div class="ui-empty"><b>No tool matches.</b><span>Try another word, or press <b>Clear filters</b>.</span></div></td></tr>`;
      kit().rerender(body, html);
      const left = rows.length - shown.length;
      $('tlMore').innerHTML = left > 0 ? `<button type="button" class="ghost" data-key="tl-more">Show ${Math.min(60, left)} more (${left} left)</button>` : '';
      const more = $('tlMore').querySelector('button');
      if (more) more.onclick = () => { S.shown += 60; save(); renderRows(); kit().refocus('tl-more'); };
    }
    async function toggle(id, box) {
      const enable = box.checked, t = list.find(x => x.id === id);
      const go = () => root.apiCall('POST', `/api/tools/${encodeURIComponent(id)}/${enable ? 'enable' : 'disable'}`);
      if (!enable) {
        box.checked = true;  // stays on until the server confirms
        const ok = await kit().confirm({ title: `Switch off ${id}?`, action: 'Switch off', danger: true,
          body: 'Claude, the agent and this dashboard get a clear "switched off" message when they try to use it, until you switch it back on here.', run: go });
        if (!ok) { kit().refocus('sw:' + id); return; }
      } else {
        box.disabled = true;
        try { await go(); } catch (e) { box.checked = false; box.disabled = false; kit().toast(`Couldn't switch ${id} on: ${e.message}`, 'bad'); return; }
      }
      if (t) t.enabled = enable;
      kit().toast(`${id} switched ${enable ? 'on' : 'off'}.`);
      load();
    }
    function menu(id, btn) {
      const t = list.find(x => x.id === id);
      if (!t) return;
      kit().menu(btn, [
        { label: 'Details and stats', run: () => openTool(id) },
        { label: 'Run it by hand', run: () => { root.switchTab('run'); Run.select(id); } },
        { label: 'Copy its name', run: () => { navigator.clipboard.writeText(id).then(() => kit().toast(`Copied ${id}.`), () => {}); } },
        '-',
        t.enabled ? { label: 'Switch off…', danger: true, run: () => { const b = document.querySelector(`input[data-switch="${CSS.escape(id)}"]`); if (b) { b.checked = false; toggle(id, b); } } }
                  : { label: 'Switch on', run: () => { const b = document.querySelector(`input[data-switch="${CSS.escape(id)}"]`); if (b) { b.checked = true; toggle(id, b); } } },
      ]);
    }
    function drawerOpts(t) {
      return {
        key: 'tool:' + t.id, view: 'toolsView', title: t.id,
        sub: `${t.enabled ? state('good', 'On') : state('warn', 'Switched off')}<span class="ui-chip${t.risk === 'read' ? ' plain' : ' warn'}">${esc(t.scope)}</span><span>${esc(t.category)}</span>`,
        tabs: [{ id: 'overview', label: 'Overview' }, { id: 'usage', label: 'Usage' }, { id: 'calls', label: 'Recent calls' }],
        tab: S.tab, onTab: tab => { S.tab = tab; save(); },
        onClose: () => { S.sel = null; save(); renderRows(); },
        render: (tab, body, again) => drawTool(list.find(x => x.id === t.id) || t, tab, body, again),
      };
    }
    function openTool(id) {
      const t = list.find(x => x.id === id);
      if (!t) return;
      S.sel = id; save();
      Drawer.open(drawerOpts(t));
      renderRows();
    }
    function drawTool(t, tab, body, again) {
      if (tab === 'overview') {
        const who = Object.entries(t.clients || {}).filter(([, ok]) => ok).map(([n]) => n);
        const none = Object.keys(t.clients || {}).length && !who.length;
        body.innerHTML = `<div class="ui-block"><p>${esc(t.description || 'No description.')}</p>
            ${t.enabled ? '' : `<div class="ui-note warn">Switched off${t.off_reason ? `: ${esc(t.off_reason)}` : ''}. Every call to it is refused with a clear message until you switch it back on.</div>`}
            ${REPLACED_BY[t.id] ? `<div class="ui-note">A newer tool does this job: <b>${esc(REPLACED_BY[t.id])}</b>. No calls here doesn't mean it's broken.</div>` : ''}</div>
          <div class="ui-block"><h3>Access</h3><dl class="ui-dl"><dt>Area</dt><dd>${esc(t.category)}</dd><dt>Scope</dt><dd>${esc(t.scope)}</dd>
            <dt>Risk</dt><dd>${t.risk === 'read' ? 'Reads only' : 'Can change things (' + esc(t.risk) + ')'}</dd>
            <dt>Inputs</dt><dd>${Object.keys(t.inputs || {}).length ? Object.keys(t.inputs).map(esc).join(', ') : 'none'}</dd></dl></div>
          <div class="ui-block"><h3>Apps allowed to call it</h3>${who.length ? `<p>${who.map(n => `<span class="ui-chip plain">${esc(n)}</span>`).join(' ')}</p>`
            : `<p class="hint">${none ? 'No app has been granted it. You always can.' : 'No apps yet. You always can.'} Grants are set per app on the <b>API access</b> page.</p>`}</div>
          <div class="ui-block"><div class="ui-btnrow">
            <button type="button" class="ghost" data-key="d-run">Run it by hand</button>
            <button type="button" class="${t.enabled ? 'ghost danger' : 'ui-primary'}" data-key="d-sw">${t.enabled ? 'Switch off…' : 'Switch on'}</button></div></div>`;
        body.querySelector('[data-key="d-run"]').onclick = () => { Drawer.close(); root.switchTab('run'); Run.select(t.id); };
        body.querySelector('[data-key="d-sw"]').onclick = () => { const b = document.querySelector(`input[data-switch="${CSS.escape(t.id)}"]`); if (b) { b.checked = !t.enabled; toggle(t.id, b); } };
        return;
      }
      if (tab === 'usage') {
        const cap = (pageVar('caps') || {})[t.id] || null;
        let host = hostOf('tl-rx');
        if (!host) { host = el('div'); keep('tl-rx', () => new T.Reactor(host, { label: 'Calls' })); }
        const rx = _inst.get('tl-rx');
        body.innerHTML = `<div class="ui-block sx-drawer-inst"><div class="sx-rx-slot"></div>
            <div class="ui-nums"><div><b>${(t.calls || 0).toLocaleString('en-GB')}</b><span>calls, all time</span></div>
              <div><b>${(t.errors || 0).toLocaleString('en-GB')}</b><span>failed</span></div>
              <div><b>${ms(t.avg_ms == null ? null : Math.round(t.avg_ms))}</b><span>average, recent calls</span></div>
              <div><b>${t.last_call ? agoS(t.last_call) : 'never'}</b><span>last used</span></div></div></div>
          <div class="ui-block"><h3>What went through it</h3>${cap ? `<div class="ck-ledger">
              <div class="ck-lrow"><span>Text sent in</span><b class="m">${num(cap.in_chars)}</b><span class="prov m">measured, characters</span></div>
              <div class="ck-lrow"><span>Text sent back</span><b class="m">${num(cap.out_chars)}</b><span class="prov m">measured, characters</span></div>
              ${cap.saved_tokens || cap.saved_chars ? `<div class="ck-lrow"><span>Kept out of Claude</span><b class="e">≈ ${num(cap.saved_tokens != null ? cap.saved_tokens : Math.floor((cap.saved_chars || 0) / 4))}</b><span class="prov e">estimate, tokens${cap.rule ? ` (${esc(cap.rule)})` : ''}</span></div>` : ''}
            </div>` : '<p class="hint">The text sizes load with the Overview\'s savings figures; open the Overview once and they show here.</p>'}</div>`;
        body.querySelector('.sx-rx-slot').appendChild(host);
        rx.set(t.calls ? { status: 'ok', kind: 'measured', tone: t.success_rate != null && t.success_rate < 0.9 ? 'warn' : 'measured', label: 'Succeeded',
            value: pctTxt(t.success_rate), raw: t.calls, unit: `of ${num(t.calls)} calls`, period: 'all time, measured',
            arc: t.success_rate != null ? { frac: t.success_rate, title: `${pctTxt(t.success_rate)} of calls succeeded` } : null }
          : { status: 'unavailable', kind: 'measured', label: 'Succeeded', value: 'No calls', period: 'nothing to measure yet' });
        return;
      }
      // recent calls: the tool server's recent-calls list (the same one the Overview uses), this tool only
      const calls = (pageVar('activity') || []).filter(e => e && e.id === t.id).sort((a, b) => b.timestamp - a.timestamp);
      let host = hostOf('tl-pulse');
      if (!host) { host = el('div', 'sx-pulse-h'); keep('tl-pulse', () => new T.Pulse(host, { compact: true })); }
      body.innerHTML = `<div class="ui-block"><h3>Response time, recent calls</h3><div class="sx-pulse-slot"></div>
          <p class="hint">From the tool server's list of its last 200 calls (all tools). A red dot failed.</p></div>
        <div class="ui-block"><h3>Last calls</h3><div class="sx-feed">${calls.slice(0, 12).map(e => `<div class="ui-feed-row"><span class="ui-feed-time">${new Date(e.timestamp * 1000).toLocaleTimeString('en-GB')}</span>
          <span class="ui-feed-main">${e.refused ? state('warn', 'Refused') : e.ok === false ? state('bad', 'Failed') : state('good', 'Ran')}${e.client ? ` <span class="ui-dim">by ${esc(e.client)}</span>` : ''}</span>
          <span class="ui-dim">${e.refused ? '' : ms(e.duration_ms)}</span></div>`).join('') || '<p class="hint">No calls to this tool in the recent list.</p>'}</div></div>`;
      body.querySelector('.sx-pulse-slot').appendChild(host);
      const pts = calls.filter(e => !e.refused && isFinite(e.duration_ms)).map(e => ({ t: e.timestamp * 1000, v: Number(e.duration_ms), bad: e.ok === false })).sort((a, b) => a.t - b.t);
      _inst.get('tl-pulse').set({ kind: 'dots', series: [{ name: 'Response time', tone: 'live', points: pts }], fmtY: msAxis, fmtTip: v => ms(v),
        fmtX: tt => new Date(tt).toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        fmtAxisX: tt => new Date(tt).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
        message: pts.length ? '' : 'No recent calls to draw.', label: `Response times of ${t.id}'s recent calls` });
    }
    return { load, render, open: openTool };
  })();

  // ---------------------------------------------------------------------------------------------------------------
  // Run: find a tool in a compact table, fill its form, run it; saved runs behind a row menu.
  // GET /api/tools, GET|POST /api/run/saved, DELETE /api/run/saved/{name}, POST /api/run
  // ---------------------------------------------------------------------------------------------------------------
  const RUN_LONG = /^(text|prompt|state|script|body|note|instructions|question|content|message|description|code)$/;
  const Run = (() => {
    let tools = [], saved = [], sel = null, loaded = false, fail = null, bound = false, pending = null;
    const S = { q: '', cat: '', risk: 'all', shown: 80 };
    async function load() {
      try {
        const [t, s] = await Promise.all([root.apiCall('GET', '/api/tools'), root.apiCall('GET', '/api/run/saved')]);
        tools = t.tools || []; saved = s.runs || []; loaded = true; fail = null;
      } catch (e) { fail = e; }
      render();
      if (pending) { const p = pending; pending = null; select(p.id, p.args, p.name); }
    }
    function bind() {
      bound = true;
      $('runSearch').addEventListener('input', e => { S.q = e.target.value; S.shown = 80; renderList(); });
      $('runCat').addEventListener('change', e => { S.cat = e.target.value; renderList(); });
      $('runRisk').addEventListener('change', e => { S.risk = e.target.value; renderList(); });
      rowClicks($('runRows'), key => select(key.slice(5)), () => {});
      $('runRows').addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('tr[data-key]')) select(e.target.dataset.key.slice(5)); });
      rowClicks($('runSavedRows'), key => { const r = saved.find(x => 'saved:' + x.name === key); if (r) select(r.tool, r.args, r.name); }, (key, btn) => savedMenu(key.slice(6), btn));
    }
    function render() {
      if (!bound) bind();
      const cats = [...new Set(tools.map(t => t.category))].sort(), sel2 = $('runCat');
      if (sel2.options.length !== cats.length + 1) sel2.innerHTML = '<option value="">All areas</option>' + cats.map(c => `<option>${esc(c)}</option>`).join('');
      renderSaved(); renderList();
    }
    function renderSaved() {
      const body = $('runSavedRows');
      $('runSavedCount').textContent = saved.length ? `${saved.length} saved` : '';
      kit().rerender(body, saved.map(r => `<tr data-key="saved:${esc(r.name)}"${sel && r.name === $('runSaveName')?.value ? ' class="is-sel"' : ''}>
          <td class="ui-name-cell"><button type="button" class="ui-rowlink" data-open data-key="saved:${esc(r.name)}">${esc(r.name)}</button><span class="ui-sec">${esc(r.tool)}</span></td>
          <td class="ui-c-act">${kebab('smenu:' + r.name, 'Actions for ' + r.name)}</td></tr>`).join('')
        || `<tr class="ui-empty-row"><td colspan="2"><p class="hint sx-pad">None yet: open a tool, fill it in, and press <b>Save</b>.</p></td></tr>`);
    }
    function renderList() {
      const body = $('runRows');
      if (!loaded) { body.innerHTML = `<tr class="ui-empty-row"><td colspan="3"><div class="${fail ? 'ui-empty' : 'loading'}">${fail ? root.loadErr('the tool list', fail) : 'Loading…'}</div></td></tr>`; return; }
      const rows = runRows(tools, S), shown = rows.slice(0, S.shown);
      $('runCount').textContent = rows.length === tools.length ? `${tools.length} tools` : `${rows.length} of ${tools.length}`;
      kit().rerender(body, shown.map(t => `<tr data-key="tool:${esc(t.id)}" tabindex="0"${sel && sel.id === t.id ? ' class="is-sel" aria-current="true"' : ''}${t.enabled ? '' : ' data-tone="warn"'}>
          <td class="ui-name-cell"><span class="sx-mono">${esc(t.id)}</span><span class="ui-sec" title="${esc(t.description || '')}">${esc(t.description || '')}</span></td>
          <td data-label="Risk"><span class="ui-chip${!t.enabled ? ' plain' : t.risk === 'read' ? ' plain' : ' warn'}">${esc(t.enabled ? (t.risk === 'read' ? 'read' : t.risk) : 'off')}</span></td></tr>`).join('')
        || `<tr class="ui-empty-row"><td colspan="3"><div class="ui-empty"><b>No tool matches.</b></div></td></tr>`);
      const left = rows.length - shown.length;
      $('runMore').innerHTML = left > 0 ? `<button type="button" class="ghost" data-key="run-more">Show ${Math.min(80, left)} more (${left} left)</button>` : '';
      const more = $('runMore').querySelector('button');
      if (more) more.onclick = () => { S.shown += 80; renderList(); kit().refocus('run-more'); };
    }
    function savedMenu(name, btn) {
      const r = saved.find(x => x.name === name);
      if (!r) return;
      const t = tools.find(x => x.id === r.tool);
      kit().menu(btn, [
        { label: t && t.risk === 'read' ? 'Run it now' : 'Open to run', run: () => { select(r.tool, r.args, r.name); if (sel && sel.id === r.tool && sel.risk === 'read') now(); } },
        { label: 'Edit', run: () => select(r.tool, r.args, r.name) },
        '-',
        { label: 'Remove…', danger: true, run: () => kit().confirm({ title: `Remove "${r.name}"?`, permanent: true, action: 'Remove',
            body: `The saved run of <b>${esc(r.tool)}</b> goes. The tool itself stays.`,
            run: () => root.apiCall('DELETE', `/api/run/saved/${encodeURIComponent(r.name)}`) })
            .then(ok => { if (ok) { kit().toast(`Removed "${r.name}".`); load(); } }) },
      ]);
    }
    function field(name, spec, value) {
      const id = `runF_${name}`, type = spec.type || 'string';
      const req = spec.required ? ' <small class="sx-req">required</small>' : '';
      const dflt = spec.default !== undefined ? ` <small>default ${esc(JSON.stringify(spec.default))}</small>` : '';
      const v = value !== undefined ? value : '';
      let input;
      if (type === 'boolean') input = `<select id="${id}"><option value="">(default)</option><option value="true"${v === true ? ' selected' : ''}>true</option><option value="false"${v === false ? ' selected' : ''}>false</option></select>`;
      else if (type === 'integer' || type === 'number') input = `<input id="${id}" type="number"${type === 'integer' ? ' step="1"' : ' step="any"'} value="${esc(v === '' ? '' : String(v))}">`;
      else if (type === 'array' || type === 'object') input = `<textarea id="${id}" placeholder="${type === 'array' ? '["one", "two"]' : '{"key": "value"}'}">${esc(v === '' ? '' : JSON.stringify(v, null, 1))}</textarea>`;
      else input = RUN_LONG.test(name) ? `<textarea id="${id}">${esc(String(v))}</textarea>` : `<input id="${id}" type="text" value="${esc(String(v))}">`;
      return `<div class="run-field"><label for="${id}"><span class="sx-mono">${esc(name)}</span> <small>${esc(type)}</small>${req}${dflt}</label>${input}</div>`;
    }
    function select(id, args, savedName) {
      if (!loaded) { pending = { id, args, name: savedName }; return; }
      const t = tools.find(x => x.id === id);
      if (!t) return;
      sel = t; args = args || {};
      const inputs = t.inputs || {}, known = Object.keys(inputs);
      const extra = Object.fromEntries(Object.entries(args).filter(([k]) => !known.includes(k)));
      $('runMain').innerHTML = `<header class="tw-head"><h2 class="tw-title sx-mono" id="runTitle" tabindex="-1">${esc(t.id)}</h2>
          <span class="ui-chip${t.risk === 'read' ? ' plain' : ' warn'}">${esc(t.scope || t.risk)}</span>${t.enabled ? '' : state('warn', 'Switched off')}</header>
        <p class="hint sx-desc">${esc(t.description || '')}</p>
        ${t.enabled ? '' : `<div class="ui-note warn">This tool is switched off (${esc(t.off_reason || 'Tools page')}); a run will be refused.</div>`}
        <div class="run-fields">${known.length ? known.map(k => field(k, inputs[k], args[k])).join('') : '<p class="hint">This tool takes no inputs.</p>'}
          <details class="ui-adv"${Object.keys(extra).length ? ' open' : ''}><summary>Extra body fields</summary>
          <div class="run-field"><label for="runExtra">A JSON object, merged in: for anything the form doesn't show</label>
            <textarea id="runExtra" class="run-json" placeholder="{}">${Object.keys(extra).length ? esc(JSON.stringify(extra, null, 1)) : ''}</textarea></div></details></div>
        ${t.risk === 'read' ? '' : `<label class="ui-check sx-confirm"><input type="checkbox" id="runConfirm"> This tool can change things (${esc(t.risk)}). I mean to run it.</label>`}
        <div class="run-actions"><button type="button" class="ui-primary" id="runGo">Run</button>
          <span class="ui-spacer"></span>
          <input id="runSaveName" type="text" placeholder="Name to save as" value="${esc(savedName || '')}" aria-label="Name to save as">
          <button type="button" class="ghost" id="runSaveBtn">Save</button></div>
        <div class="ui-err sx-run-msg" id="runMsg" role="status"></div>
        <div id="runOut" aria-live="polite"></div>`;
      $('runGo').onclick = () => now();
      $('runSaveBtn').onclick = e => kit().busy(e.currentTarget, saveRun);
      renderList(); renderSaved();
      $('runTitle').focus({ preventScroll: true });
      if (matchMedia('(max-width: 860px)').matches) $('runMain').scrollIntoView({ block: 'start' });
    }
    function collect() {
      const t = sel, args = {};
      for (const [k, spec] of Object.entries(t.inputs || {})) {
        const n = $(`runF_${k}`), raw = n ? n.value.trim() : '';
        if (raw === '') { if (spec.required) throw new Error(`${k} is required.`); continue; }
        const type = spec.type || 'string';
        if (type === 'boolean') args[k] = raw === 'true';
        else if (type === 'integer' || type === 'number') { const v = Number(raw); if (Number.isNaN(v)) throw new Error(`${k} must be a number.`); args[k] = v; }
        else if (type === 'array' || type === 'object') {
          try { args[k] = JSON.parse(raw); } catch (e) { throw new Error(`${k} isn't valid JSON.`); }
          if (type === 'array' && !Array.isArray(args[k])) throw new Error(`${k} must be a list, like ["a", "b"].`);
        } else args[k] = n.value;
      }
      const extraRaw = ($('runExtra') || {}).value ? $('runExtra').value.trim() : '';
      if (extraRaw) {
        let extra; try { extra = JSON.parse(extraRaw); } catch (e) { throw new Error("Extra body fields aren't valid JSON."); }
        if (!extra || typeof extra !== 'object' || Array.isArray(extra)) throw new Error('Extra body fields must be a JSON object.');
        Object.assign(args, extra);
      }
      return args;
    }
    async function now() {
      const msg = $('runMsg'), out = $('runOut'), go = $('runGo');
      if (!sel || !go || go.disabled) return;
      let args;
      try { args = collect(); } catch (e) { msg.className = 'ui-err sx-run-msg'; msg.textContent = e.message; return; }
      const c = $('runConfirm');
      if (c && !c.checked) { msg.className = 'ui-err sx-run-msg'; msg.textContent = 'Tick the box first: this tool can change things.'; c.focus(); return; }
      msg.className = 'ui-sub sx-run-msg'; msg.textContent = 'Running…'; out.innerHTML = '';
      let d;
      await kit().busy(go, async () => {
        try {
          const r = await fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tool: sel.id, args }) });
          d = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
        } catch (e) { d = { ok: false, error: e.message }; }
      });
      msg.className = (d.ok ? 'ui-sub' : 'ui-err') + ' sx-run-msg';
      msg.textContent = d.ok ? `Done in ${ms(d.ms)}.` : `Failed${d.ms != null ? ` after ${ms(d.ms)}` : ''}.`;
      out.innerHTML = `<pre class="run-out${d.ok ? '' : ' bad'}">${esc(d.ok ? JSON.stringify(d.result, null, 2) : (d.error || 'Failed.'))}</pre>
        ${d.ok ? '<button type="button" class="ghost" data-copy>Copy answer</button>' : ''}`;
      const cp = out.querySelector('[data-copy]');
      if (cp) cp.onclick = () => navigator.clipboard.writeText(out.querySelector('pre').textContent).then(() => { cp.textContent = 'Copied'; }, () => {});
    }
    async function saveRun() {
      const msg = $('runMsg'), name = $('runSaveName').value.trim();
      if (!name) { msg.className = 'ui-err sx-run-msg'; msg.textContent = 'Type a name to save it as.'; $('runSaveName').focus(); return; }
      let args;
      try { args = collect(); } catch (e) { msg.className = 'ui-err sx-run-msg'; msg.textContent = e.message; return; }
      const replace = saved.some(r => r.name === name);
      try { await root.apiCall('POST', '/api/run/saved', { name, tool: sel.id, args, replace }); }
      catch (e) { msg.className = 'ui-err sx-run-msg'; msg.textContent = e.message; return; }
      msg.textContent = '';
      kit().toast(replace ? `Updated "${name}".` : `Saved as "${name}".`);
      load();
    }
    return { load, select, now };
  })();

  // ---------------------------------------------------------------------------------------------------------------
  // Containers: GET /api/docker/containers; POST /api/docker/containers/{start|stop|restart} {container, dry_run:false};
  // logs through POST /api/run {tool: docker.container.logs} (the tool server's own switch and log apply).
  // ---------------------------------------------------------------------------------------------------------------
  const Docker = (() => {
    let list = [], loaded = false, fail = null, at = 0, busy = false, S = null, bound = false;
    const save = () => kit().save('cp-docker-page', S);
    // the page's poll calls this every cycle; it asks at most every 10 s and never waits on the reply
    function load(force) {
      if (busy || (!force && Date.now() - at < 10000)) return;
      busy = true;
      root.apiCall('GET', '/api/docker/containers').then(d => { list = d.result || []; loaded = true; fail = null; at = Date.now(); },
        e => { fail = e; at = Date.now(); }).finally(() => { busy = false; render(); });
    }
    function bind() {
      bound = true;
      S = kit().keep('cp-docker-page', { q: '', state: 'all', sort: 'name', sel: null, tab: 'overview', tail: 200 });
      $('dockerSearch').value = S.q; $('dkState').value = S.state; $('dkSort').value = S.sort;
      $('dockerSearch').addEventListener('input', e => { S.q = e.target.value; save(); rows(); });
      $('dkState').addEventListener('change', e => { S.state = e.target.value; save(); render(); });
      $('dkSort').addEventListener('change', e => { S.sort = e.target.value; save(); rows(); });
      $('dkClear').onclick = () => { Object.assign(S, { q: '', state: 'all', sort: 'name' }); $('dockerSearch').value = ''; $('dkState').value = 'all'; $('dkSort').value = 'name'; save(); render(); };
      $('dkRefresh').onclick = () => load(true);
      rowClicks($('dockerRows'), key => open(key.slice(3)), (key, btn) => menu(key.slice(3), btn));
      kit().sticky($('dkWork'));
    }
    function render() {
      if (!bound) bind();
      const c = containerCounts(list);
      live('dkLive', fail ? 'bad' : loaded ? 'ok' : '', fail ? "Can't read Docker" : loaded ? 'Live' : 'Loading…');
      $('dkUpdated').textContent = at && loaded ? 'Updated ' + new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
      strip($('dkMetrics'), [
        { num: loaded ? `${c.running}<small>/${c.total}</small>` : '—', label: 'Running', f: loaded ? frac(c.running, c.total) : undefined, filter: 'running', pressed: S.state === 'running' },
        { num: loaded ? String(c.stopped) : '—', label: 'Stopped', tone: c.stopped ? 'warn' : 'ok', filter: 'stopped', pressed: S.state === 'stopped' },
        { num: loaded ? String(c.images) : '—', label: 'Images in use' },
        { num: loaded ? String(c.published) : '—', label: 'With ports open', title: 'Containers that publish a port on this machine' },
      ], f => { S.state = S.state === f ? 'all' : f; $('dkState').value = S.state; save(); render(); });
      rows();
      const k = Drawer.key();
      if (k && k.startsWith('dk:')) { const x = list.find(y => 'dk:' + y.name === k); if (x) Drawer.refresh(k, opts(x)); }
      else if (S.sel && !k && isOpen('dockerView') && list.some(y => y.name === S.sel)) open(S.sel);
    }
    function rows() {
      const body = $('dockerRows');
      if (!loaded) { body.innerHTML = `<tr class="ui-empty-row"><td colspan="5"><div class="${fail ? 'ui-empty' : 'loading'}">${fail ? root.loadErr('containers', fail) : 'Loading…'}</div></td></tr>`; return; }
      const r = containerRows(list, S);
      const filtered = S.q || S.state !== 'all';
      $('dkClear').hidden = !(filtered || S.sort !== 'name');
      $('dockerCount').textContent = filtered ? `${r.length} of ${list.length}` : `${list.length} containers`;
      kit().rerender(body, r.map(c => {
        const on = c.status === 'running', ports = portList(c.ports);
        return `<tr data-key="dk:${esc(c.name)}"${on ? '' : ' data-tone="bad"'}${S.sel === c.name && Drawer.key() ? ' class="is-sel"' : ''}>
          <td class="ui-name-cell"><button type="button" class="ui-rowlink" data-open data-key="dk:${esc(c.name)}">${esc(c.name)}</button><span class="ui-sec sx-mono">${esc(c.id || '')}</span></td>
          <td data-label="State">${on ? state('good', 'Running') : state('bad', c.status ? c.status[0].toUpperCase() + c.status.slice(1) : 'Unknown')}</td>
          <td data-label="Image"><span class="sx-mono sx-clip" title="${esc(c.image)}">${esc(c.image)}</span></td>
          <td class="ui-opt" data-label="Ports">${ports.length ? `<span class="sx-mono sx-clip" title="${esc(ports.join(', '))}">${esc(ports.slice(0, 3).join(', '))}${ports.length > 3 ? ` +${ports.length - 3}` : ''}</span>` : '<span class="ui-dim">none</span>'}</td>
          <td class="ui-c-act">${kebab('menu:' + c.name, 'Actions for ' + c.name)}</td></tr>`;
      }).join('') || (list.length ? `<tr class="ui-empty-row"><td colspan="5"><div class="ui-empty"><b>No container matches.</b><span>Try another word, or press <b>Clear filters</b>.</span></div></td></tr>`
        : `<tr class="ui-empty-row"><td colspan="5"><div class="ui-empty"><b>No containers found.</b><span>This page lists the Docker containers on the machine HomeShed runs on, and needs HomeShed to be able to reach Docker (its socket). The Docker section of HomeShed's configuration guide shows how.</span></div></td></tr>`));
    }
    function act(name, action) {
      const go = () => root.apiCall('POST', `/api/docker/containers/${action}`, { container: name, dry_run: false });
      const done = () => { kit().toast(`${name}: ${action === 'start' ? 'started' : action === 'stop' ? 'stopped' : 'restarted'}.`); load(true); };
      if (action === 'start') return kit().busy(null, async () => { try { await go(); done(); } catch (e) { kit().toast(`Couldn't start ${name}: ${e.message}`, 'bad'); } });
      return kit().confirm({ title: `${action === 'stop' ? 'Stop' : 'Restart'} ${name}?`, action: action === 'stop' ? 'Stop it' : 'Restart it', danger: action === 'stop',
        body: action === 'stop' ? `<b>${esc(name)}</b> stops, and whatever it serves is down until you start it again here.`
          : `<b>${esc(name)}</b> stops and starts again. Whatever it serves is down for a few seconds.`, run: go }).then(ok => { if (ok) done(); });
    }
    function menu(name, btn) {
      const c = list.find(x => x.name === name);
      if (!c) return;
      const on = c.status === 'running';
      kit().menu(btn, [{ label: 'Details', run: () => open(name) }, { label: 'Logs', run: () => open(name, 'logs') }, '-',
        ...(on ? [{ label: 'Restart…', run: () => act(name, 'restart') }, { label: 'Stop…', danger: true, run: () => act(name, 'stop') }]
               : [{ label: 'Start', run: () => act(name, 'start') }])]);
    }
    const logs = {};  // name -> {text, at, err, busy}
    function opts(c) {
      return { key: 'dk:' + c.name, view: 'dockerView', title: c.name,
        sub: `${c.status === 'running' ? state('good', 'Running') : state('bad', c.status || 'Unknown')}<span class="sx-mono">${esc(c.image)}</span>`,
        tabs: [{ id: 'overview', label: 'Overview' }, { id: 'logs', label: 'Logs' }], tab: S.tab,
        onTab: t => { S.tab = t; save(); if (t === 'logs' && !logs[c.name]) fetchLogs(c.name); },
        onClose: () => { S.sel = null; save(); rows(); },
        render: (tab, body, again) => draw(list.find(x => x.name === c.name) || c, tab, body, again) };
    }
    function open(name, tab) {
      const c = list.find(x => x.name === name);
      if (!c) return;
      S.sel = name; if (tab) S.tab = tab; save();
      Drawer.open(opts(c));
      if (S.tab === 'logs' && !logs[name]) fetchLogs(name);
      rows();
    }
    async function fetchLogs(name) {
      const L = logs[name] = Object.assign(logs[name] || {}, { busy: true });
      if (Drawer.key() === 'dk:' + name) Drawer.refresh('dk:' + name);
      try {
        const r = await root.apiCall('POST', '/api/run', { tool: 'docker.container.logs', args: { container: name, tail: S.tail || 200 } });
        if (!r.ok) throw new Error(r.error || 'No logs.');
        const lines = (r.result && r.result.lines) || [];
        Object.assign(L, { text: lines.map(l => typeof l === 'string' ? l : JSON.stringify(l)).join('\n'), n: lines.length, err: null, at: Date.now() });
      } catch (e) { Object.assign(L, { err: e.message, at: Date.now() }); }
      L.busy = false;
      if (Drawer.key() === 'dk:' + name) Drawer.refresh('dk:' + name);
    }
    function draw(c, tab, body) {
      if (tab === 'overview') {
        const ports = portList(c.ports), on = c.status === 'running';
        body.innerHTML = `<div class="ui-block"><dl class="ui-dl"><dt>State</dt><dd>${esc(c.status || 'unknown')}</dd><dt>ID</dt><dd class="sx-mono">${esc(c.id || '')}</dd>
            <dt>Image</dt><dd class="sx-mono">${esc(c.image)}</dd><dt>Ports</dt><dd class="sx-mono">${ports.length ? ports.map(esc).join('<br>') : 'none published'}</dd></dl></div>
          <div class="ui-block"><h3>Actions</h3><div class="ui-btnrow">${on ? `<button type="button" class="ghost" data-key="d-restart">Restart…</button><button type="button" class="ghost danger" data-key="d-stop">Stop…</button>`
            : '<button type="button" class="ui-primary" data-key="d-start">Start</button>'}<button type="button" class="ghost" data-key="d-logs">Logs</button></div>
            <p class="hint">Restart and Stop ask first: they affect a live service. HomeShed can't remove a container.</p></div>
          <div class="ui-block"><h3>Resource graphs</h3><p class="hint">Not available: HomeShed doesn't collect per-container CPU or memory. The machine's own graphs are on the <b>Proxmox</b> page.</p></div>`;
        const b = k => body.querySelector(`[data-key="${k}"]`);
        if (b('d-restart')) b('d-restart').onclick = () => act(c.name, 'restart');
        if (b('d-stop')) b('d-stop').onclick = () => act(c.name, 'stop');
        if (b('d-start')) b('d-start').onclick = () => act(c.name, 'start');
        b('d-logs').onclick = () => { open(c.name, 'logs'); };
        return;
      }
      const L = logs[c.name] || {};
      body.innerHTML = `<div class="ui-block"><div class="ui-inline"><label for="dkTail">Last</label>
          <select id="dkTail" data-key="d-tail">${[100, 200, 500, 1000].map(n => `<option value="${n}"${(S.tail || 200) === n ? ' selected' : ''}>${n} lines</option>`).join('')}</select>
          <button type="button" class="ghost" data-key="d-relog"${L.busy ? ' aria-busy="true" disabled' : ''}>${L.busy ? 'Reading…' : 'Read again'}</button>
          <button type="button" class="ghost" data-key="d-copy"${L.text ? '' : ' disabled'}>Copy</button>
          <span class="ui-sub">${L.at && !L.busy ? `${L.err ? 'Tried' : 'Read'} ${new Date(L.at).toLocaleTimeString('en-GB')}${L.n != null && !L.err ? `, ${L.n} lines` : ''}` : ''}</span></div>
          ${L.err ? `<div class="ui-note bad">${esc(L.err)}${/switched off|disabled/i.test(L.err) ? ' Switch <b>docker.container.logs</b> on on the <b>Tools</b> page to read logs here.' : ''}</div>` : ''}
          <pre class="sx-log" tabindex="0" aria-label="Log lines, newest last">${L.text ? esc(L.text) : L.busy ? 'Reading the logs…' : L.err ? '' : 'No lines yet.'}</pre></div>`;
      const pre = body.querySelector('.sx-log');
      if (pre && L.text) pre.scrollTop = pre.scrollHeight;
      body.querySelector('[data-key="d-tail"]').onchange = e => { S.tail = Number(e.target.value); save(); fetchLogs(c.name); };
      body.querySelector('[data-key="d-relog"]').onclick = () => fetchLogs(c.name);
      body.querySelector('[data-key="d-copy"]').onclick = e => navigator.clipboard.writeText(L.text || '').then(() => { e.target.textContent = 'Copied'; }, () => {});
    }
    return { load, render, act };
  })();

  // ---------------------------------------------------------------------------------------------------------------
  // Uptime: GET /api/monitors (Uptime Kuma's checks, read-only). Asked at most every 15 s while the page is open.
  // ---------------------------------------------------------------------------------------------------------------
  const Uptime = (() => {
    let data = null, loaded = false, fail = null, at = 0, busy = false, bound = false;
    const S = { q: '', state: 'all', type: '' };
    function load(force) {
      if (busy || (!force && Date.now() - at < 15000)) return;
      busy = true;
      fetch('/api/monitors').then(r => r.json()).then(d => {
        if (d.error) throw Object.assign(new Error(d.error), { notSetUp: !!d.not_set_up });
        data = d; loaded = true; fail = null;
      }).catch(e => { fail = e; }).finally(() => { at = Date.now(); busy = false; render(); });
    }
    function bind() {
      bound = true;
      $('upSearch').addEventListener('input', e => { S.q = e.target.value; rows(); });
      $('upState').addEventListener('change', e => { S.state = e.target.value; render(); });
      $('upType').addEventListener('change', e => { S.type = e.target.value; rows(); });
      $('upRefresh').onclick = () => load(true);
    }
    function render() {
      if (!bound) bind();
      const mons = (data && data.monitors) || [], s = (data && data.summary) || {};
      live('upLive', fail ? 'bad' : loaded ? (s.down ? 'bad' : 'ok') : '', fail ? "Can't read Uptime Kuma" : loaded ? (s.down ? `${s.down} down` : 'All up') : 'Loading…');
      $('upUpdated').textContent = loaded ? 'Updated ' + new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
      const paused = mons.filter(m => m.active === false).length, waiting = mons.filter(m => m.status === 'unknown' && m.active !== false).length;  // a paused check has no reading by design
      strip($('upMetrics'), [
        { num: loaded ? `${s.up || 0}<small>/${s.total || 0}</small>` : '—', label: 'Up', f: loaded ? frac(s.up || 0, s.total || 0) : undefined, filter: 'up', pressed: S.state === 'up' },
        { num: loaded ? String(s.down || 0) : '—', label: 'Down', tone: s.down ? 'bad' : 'ok', filter: 'down', pressed: S.state === 'down' },
        { num: loaded ? String(waiting) : '—', label: 'No reading yet', tone: waiting ? 'warn' : 'ok', filter: 'unknown', pressed: S.state === 'unknown' },
        { num: loaded ? String(paused) : '—', label: 'Paused in Kuma', filter: 'paused', pressed: S.state === 'paused' },
      ], f => { S.state = S.state === f ? 'all' : f; $('upState').value = S.state; render(); });
      const types = [...new Set(mons.map(m => m.type).filter(Boolean))].sort(), ts = $('upType');
      if (ts.options.length !== types.length + 1) ts.innerHTML = '<option value="">Any kind</option>' + types.map(t => `<option>${esc(t)}</option>`).join('');
      rows();
    }
    function rows() {
      const body = $('monitorsRows'), mons = (data && data.monitors) || [];
      if (!loaded) { body.innerHTML = `<tr class="ui-empty-row"><td colspan="4"><div class="${fail ? 'ui-empty' : 'loading'}">${fail ? root.loadErr('monitor status', fail) : 'Loading…'}</div></td></tr>`; return; }
      if (!mons.length) { body.innerHTML = `<tr class="ui-empty-row"><td colspan="4"><div class="ui-empty"><b>No uptime monitors yet.</b><span>This page shows the checks from Uptime Kuma (a free, self-hosted monitor). Add Kuma, give HomeShed read access to its data, and each check appears here, with anything that is down first.</span></div></td></tr>`; $('upCount').textContent = ''; return; }
      const r = monitorRows(mons, S);
      $('upCount').textContent = r.length === mons.length ? `${mons.length} checks` : `${r.length} of ${mons.length}`;
      kit().rerender(body, r.map(m => {
        const t = kumaTime(m.last_check), paused = m.active === false && m.status !== 'down';
        const cls = paused ? '' : m.status === 'up' ? 'good' : m.status === 'down' ? 'bad' : 'warn';
        return `<tr${m.status === 'down' ? ' data-tone="bad"' : m.status === 'unknown' && !paused ? ' data-tone="warn"' : ''}>
          <td class="ui-name-cell"><span class="sx-strong">${esc(m.name)}</span><span class="ui-sec">${esc(m.type || '')}</span></td>
          <td data-label="State">${state(cls, paused ? 'Paused' : m.status === 'up' ? 'Up' : m.status === 'down' ? 'Down' : 'No reading')}</td>
          <td class="num" data-label="Checked"${t ? ` title="${esc(new Date(t * 1000).toLocaleString('en-GB'))}"` : ''}>${t ? agoS(t) : '<span class="ui-dim">—</span>'}</td>
          <td class="ui-opt" data-label="Says"><span class="sx-clip" title="${esc(m.message || '')}">${esc(m.message || '')}</span></td></tr>`;
      }).join('') || `<tr class="ui-empty-row"><td colspan="4"><div class="ui-empty"><b>No check matches.</b></div></td></tr>`);
    }
    return { load, render };
  })();

  // ---------------------------------------------------------------------------------------------------------------
  // Savings: GET /api/savings. Two reactors (measured, estimated: never added together), the daily trace, the
  // 12-week calendar, the missed-savings scan.
  // ---------------------------------------------------------------------------------------------------------------
  const Savings = (() => {
    let F = null, raw = null, at = 0, busy = false, fail = null, bound = false, rxM = null, rxE = null, pulse = null;
    let range = (T && T.store.get('cp-sav-range')) || '30', missShown = 10;
    async function load() {
      if (busy) return;
      busy = true;
      try { const r = await fetch('/api/savings'); raw = await r.json(); if (!r.ok && raw && raw.error) throw new Error(raw.error); fail = null; }
      catch (e) { fail = e; raw = raw || {}; }
      finally { busy = false; }
      F = savingsFigures(raw); at = Date.now();
      render();
    }
    function bind() {
      bound = true;
      rxM = new T.Reactor($('svRxM'), { label: 'Measured' });
      rxE = new T.Reactor($('svRxE'), { label: 'Estimated' });
      pulse = new T.Pulse($('svPulse'));
      $('svRange').querySelectorAll('button').forEach(b => { b.onclick = () => { range = b.dataset.r; if (T) T.store.set('cp-sav-range', range); trace(); }; });
      $('svTable').onclick = () => { const on = $('svTable').getAttribute('aria-pressed') !== 'true'; $('svTable').setAttribute('aria-pressed', String(on)); pulse.showTable(on); };
      $('svRefresh').onclick = e => kit().busy(e.currentTarget, load);
      Mode.sync();
    }
    function render() {
      if (!bound) bind();
      if (!F) return;
      const r = F.rtk, e = F.est, gbp = raw && raw.money;
      live('svLive', fail ? 'bad' : 'ok', fail ? "Couldn't refresh" : 'Live');
      $('svUpdated').textContent = 'Updated ' + new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
      // the two headline reactors
      const d30 = T.dailySeries(F.daily, 30);
      if (!r) rxM.set({ status: 'unavailable', kind: 'measured', label: 'Trimmed by RTK', value: 'No data',
        period: F.rtkWhy === 'not-set-up' ? "RTK isn't set up" : "RTK's numbers aren't available", aria: 'Measured savings: no data, RTK has not reported.' });
      else rxM.set({ status: 'ok', kind: 'measured', tone: 'measured', label: 'Trimmed by RTK', value: num(r.tokens), raw: r.tokens, unit: 'tokens',
        period: 'all time, measured', arc: r.pct != null ? { frac: r.pct, title: `Command output ${pctTxt(r.pct)} smaller on average` } : null,
        spokes: { values: d30.map(p => p.v), titles: d30.map(p => `${new Date(p.t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}: ${p.v == null ? 'no data' : num(p.v) + ' tokens'}`) },
        aria: `Measured by RTK: ${num(r.tokens)} tokens kept out of Claude, all time; output ${pctTxt(r.pct)} smaller on average.` });
      if (!e) rxE.set({ status: 'unavailable', kind: 'estimated', label: 'Local work', value: 'No data', period: "HomeShed's counters didn't load" });
      else {
        const h = F.hours ? T.hourlySeries(F.hours, 24, Date.now() / 1000, b => ((b.saved_chars || 0) + (b.fd_chars || 0)) / F.cpt) : null;
        rxE.set({ status: 'ok', kind: 'estimated', tone: 'estimated', label: 'Local work', value: '≈ ' + num(F.estTotal), raw: F.estTotal, unit: 'tokens',
          period: 'all time, estimated',
          segments: [['generation', 'Writing done locally', 1], ['reading', 'Reading done locally', 0.78], ['retrieval', 'Search instead of reading', 0.58], ['frontDoor', 'Front door', 0.4]]
            .map(([k, t, a]) => ({ v: e[k] || 0, tone: 'estimated', dashed: true, alpha: a, title: `${t}: about ${num(e[k] || 0)} tokens` })),
          spokes: h ? { values: h.map(p => p.v), titles: h.map(p => `${new Date(p.t).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}: ${p.v == null ? 'no data' : 'about ' + num(p.v) + ' tokens'}`) } : null,
          aria: `Estimated, from text sizes: about ${num(F.estTotal)} tokens kept out of Claude by local work and the front door, all time.` });
      }
      // the ledger: each source on its own line, with what kind of figure it is
      const row = (label, val, cls, prov, pcls, tip) => `<div class="ck-lrow"${tip ? ` title="${esc(tip)}"` : ''}><span>${esc(label)}</span><b class="${cls}">${esc(val)}</b><span class="prov ${pcls}">${esc(prov)}</span></div>`;
      const L = [];
      L.push(r ? row('Command output trimmed (RTK)', num(r.tokens), 'm', `measured, ${num(r.commands)} commands`, 'm', "RTK's own count of the shell output it cut before Claude read it")
               : row('Command output trimmed (RTK)', 'no data', 'u', F.rtkWhy === 'not-set-up' ? "RTK isn't set up" : 'not answering', 'u'));
      if (e) {
        L.push(row('Writing done locally', '≈ ' + num(e.generation), 'e', 'estimate: what the local model wrote', 'e'));
        L.push(row('Reading done locally', '≈ ' + num(e.reading), 'e', 'estimate: file minus its summary', 'e'));
        L.push(row('Search instead of reading', '≈ ' + num(e.retrieval), 'e', 'estimate: source files minus chunks', 'e'));
        L.push(e.frontDoor != null ? row('Tool lists not loaded, replies trimmed', '≈ ' + num(e.frontDoor), 'e', `estimate: characters ÷ ${F.cpt}`, 'e', 'The front door: tool definitions an app did not load, and characters cut from tool replies')
                                   : row('Tool lists not loaded (front door)', 'no data', 'u', 'needs the tool server update', 'u'));
      } else L.push(row('Local work and the front door', 'no data', 'u', "HomeShed's counters didn't load", 'u'));
      const money = root.savedMoney && gbp && r ? root.savedMoney(gbp, r.tokens, 0) : null;
      const moneyE = root.savedMoney && gbp && e ? root.savedMoney(gbp, e.reading + e.retrieval + (e.frontDoor || 0), e.generation) : null;
      // Two rows, never one sum: a "+" between them read as a total (the owner, 27 Oct)
      if (money) L.push(`<div class="ck-lrow money" title="${esc(money.tip)}"><span>Measured, at ${esc(gbp.label || 'API')} API prices</span><b><span class="m">${esc(money.text.replace(/^≈ /, ''))}</span></b></div>`);
      if (moneyE) L.push(`<div class="ck-lrow money" title="${esc(moneyE.tip)}"><span>Estimated, at ${esc(gbp.label || 'API')} API prices</span><b><span class="e">${esc(moneyE.text)}</span></b></div>`);
      $('svLedger').innerHTML = L.join('');
      // the strip
      const m = F.missed;
      strip($('svMetrics'), [
        { num: r ? num(r.tokens) : '—', label: 'Trimmed by RTK', prov: r ? 'm' : 'u', title: "RTK's own count, all time" },
        { num: r && r.pct != null ? pctTxt(r.pct) : '—', label: 'Average reduction', f: r && r.pct != null ? r.pct : undefined, prov: r ? 'm' : '' },
        { num: r ? r.commands.toLocaleString('en-GB') : '—', label: 'Commands filtered', prov: r ? 'm' : '' },
        { num: e ? '≈ ' + num(F.estTotal) : '—', label: 'Local work and front door', prov: e ? 'e' : 'u' },
        { num: m ? '≈ ' + num(m.tokens) : '—', label: m && m.days ? `Missed, last ${m.days} days` : 'Missed by RTK', prov: m ? 'e' : '', tone: m && m.tokens ? 'warn' : 'ok', title: 'Commands that ran without RTK, and about what RTK would have cut' },
      ]);
      trace(); heat(); missed();
    }
    function trace() {
      const n = Number(range) || 30;
      $('svRange').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.r === String(n))));
      const pts = T.dailySeries(F.daily, n);
      const any = pts.some(p => p.v != null);
      const day = t => new Date(t).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
      pulse.set({ series: [{ name: 'Trimmed by RTK', tone: 'measured', points: pts }], fmtY: num, fmtX: day,
        fmtAxisX: t => new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }), fmtTip: v => num(v) + ' tokens',
        message: any ? '' : F.rtk ? 'No RTK activity in this span.' : F.rtkWhy === 'not-set-up' ? "RTK isn't set up yet, so nothing is measured. RTK trims shell output before Claude reads it." : "RTK's numbers aren't available right now.",
        label: `Tokens trimmed by RTK per day, last ${n} days, measured` });
      const tot = pts.reduce((a, p) => a + (p.v || 0), 0), act = pts.filter(p => p.v).length;
      $('svPulseNote').textContent = any ? `${num(tot)} tokens on ${act} active day${act === 1 ? '' : 's'}` : '';
    }
    function heat() {
      const cells = heatCells(F.daily);
      const tip = c => c.row ? `${c.key}: ${num(c.v)} tokens trimmed over ${c.row.commands || 0} commands (${Math.round(c.row.savings_pct || 0)}%)` : `${c.key}: no RTK activity`;
      const months = [];
      cells.forEach((c, i) => { if (c.month) months.push(`<span style="grid-column:${Math.floor(i / 7) + 1}">${esc(c.month)}</span>`); });
      $('svHeat').innerHTML = `<div class="sx-heat-months" aria-hidden="true">${months.join('')}</div>
        <div class="sx-heat-days" aria-hidden="true"><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span><span></span><span>Sun</span></div>
        <div class="sx-heat-grid" role="img" aria-label="${esc(`Twelve weeks of RTK trimming, one square a day. ${F.daily.length} active days.`)}">${cells.map(c =>
          `<i class="l${c.level}${c.today ? ' today' : ''}${c.v == null ? ' gap' : ''}" title="${esc(tip(c))}"></i>`).join('')}</div>`;
      const busiest = F.daily.slice().sort((a, b) => (b.saved_tokens || 0) - (a.saved_tokens || 0))[0];
      $('svDays').textContent = !F.rtk ? (F.rtkWhy === 'not-set-up' ? "RTK isn't set up yet" : 'No RTK numbers yet')
        : `${F.daily.length} active days${busiest ? `, busiest ${new Date(busiest.date + 'T12:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}` : ''}`;
    }
    function missed() {
      const m = F.missed, box = $('svMiss');
      if (!m) {
        $('svCoverage').innerHTML = '';
        box.innerHTML = `<div class="ui-empty">${F.missedWhy === 'hidden' ? '<span>The history scan is only shown to the owner.</span>'
          : F.missedWhy === 'not-set-up' ? "<span>RTK isn't set up yet, so there's no history to scan. RTK trims shell output before Claude reads it.</span>"
          : F.missedWhy === 'error' ? `<span>Couldn't scan history: ${esc(F.missedError)}.</span>` : '<span>No history scan yet.</span>'}</div>`;
        $('svMissMore').innerHTML = '';
        return;
      }
      $('svCoverage').innerHTML = `<div class="sx-cover"><span class="sx-cover-num">${pctTxt(m.coverage)}</span>${ticks(m.coverage, 'ok', 'Commands that went through RTK')}<span class="prov m">measured</span></div>
        <p class="hint">${m.through.toLocaleString('en-GB')} of ${m.total.toLocaleString('en-GB')} commands in ${esc(m.sessions)} Claude Code sessions over the last ${esc(m.days)} days went through RTK. The rows below could have: about ${num(m.tokens)} tokens (an estimate).</p>`;
      const rows = m.rows.slice(0, missShown);
      box.innerHTML = m.rows.length ? `<table class="ui-table sx-miss"><thead><tr><th scope="col">Command</th><th scope="col" class="ui-opt">Kind</th><th scope="col" class="num">Runs</th>
          <th scope="col" class="num">Could save <span class="prov e">estimate</span></th><th scope="col" class="ui-opt2">RTK way to run it</th></tr></thead><tbody>${rows.map(x => `<tr>
          <td class="ui-name-cell"><code class="sx-clip" title="${esc(x.command)}">${esc(x.command)}</code></td><td class="ui-opt" data-label="Kind">${x.category ? esc(x.category) : '<span class="ui-dim">–</span>'}</td>
          <td class="num" data-label="Runs">${x.count == null ? '–' : x.count.toLocaleString('en-GB')}</td>
          <td class="num" data-label="Could save">≈ ${num(x.estimated_savings_tokens)}${x.estimated_savings_pct != null ? ` <span class="ui-dim">(${Math.round(x.estimated_savings_pct)}%)</span>` : ''}</td>
          <td class="ui-opt2" data-label="RTK way">${x.rtk_equivalent ? `<code class="sx-clip" title="${esc(x.rtk_equivalent)}">${esc(x.rtk_equivalent)}</code>` : '<span class="ui-dim">–</span>'}</td></tr>`).join('')}</tbody></table>`
        : '<div class="ui-empty"><b>Nothing missed.</b><span>Every supported command went through RTK.</span></div>';
      const left = m.rows.length - rows.length;
      $('svMissMore').innerHTML = left > 0 ? `<button type="button" class="ghost" data-key="sv-more">Show ${Math.min(10, left)} more (${left} left)</button>` : '';
      const more = $('svMissMore').querySelector('button');
      if (more) more.onclick = () => { missShown += 10; missed(); kit().refocus('sv-more'); };
    }
    return { load };
  })();

  // ---------------------------------------------------------------------------------------------------------------
  // Models: the order the AIs are tried in, and why each answer came from where it did (GET /api/models,
  // /api/delegator/decisions; POST /api/delegator/{name}/reset). The GPU readings' reactors are drawn here too.
  // ---------------------------------------------------------------------------------------------------------------
  const ORDINAL = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth'];
  const Models = {
    delegator(list) {
      const el2 = $('mdlDelegator');
      el2.innerHTML = list.length ? `<table class="ui-table sx-order"><thead><tr><th scope="col" class="num">Order</th><th scope="col">Model</th><th scope="col">Where</th><th scope="col">State</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${list.map(b => {
        const aliases = Array.isArray(b.aliases) ? b.aliases : Object.keys(b.aliases || {});
        const st = b.benched ? state('warn', `Benched, retried in ${b.retry_in_s ?? '?'} s`) : b.configured ? state('good', 'Ready') : state('', 'Not set up');
        return `<tr${b.benched ? ' data-tone="warn"' : ''}><td class="num sx-mono" data-label="Order">${esc(b.tier)}</td>
          <td class="ui-name-cell"><span class="sx-strong"${aliases.length ? ` title="Also called ${esc(aliases.join(', '))}"` : ''}>${esc(b.name)}</span><span class="ui-sec">tried ${ORDINAL[b.tier - 1] || 'in turn ' + b.tier}</span></td>
          <td data-label="Where">${b.cloud ? 'In the cloud' : 'On your PC'}</td><td data-label="State">${st}</td>
          <td class="ui-c-act sx-c-wide">${b.benched ? `<button type="button" class="ghost" data-reset="${esc(b.name)}">Try again now</button>` : ''}</td></tr>`;
      }).join('')}</tbody></table>`
        : `<div class="ui-empty"><b>No model is set up yet.</b><span>Tools that ask for AI help try the models listed here, in order. Add your own model server (Ollama, LM Studio or llama.cpp), or a cloud key, in <b>Settings</b>.</span></div>`;
      el2.querySelectorAll('[data-reset]').forEach(b => { b.onclick = () => kit().busy(b, () => root.resetBench(b.dataset.reset)); });
    },
    decisions(d) {
      const box = $('mdlDecisions');
      let host = hostOf('mdl-pulse');
      if (!host) { host = el('div', 'sx-pulse-h'); keep('mdl-pulse', () => new T.Pulse(host, { compact: true })); $('mdlDecPulse').appendChild(host); }
      const pulse = _inst.get('mdl-pulse');
      if (!d || !d.decisions) {
        box.innerHTML = `<div class="ui-empty">${root.loadErr('decisions', (d && d.error) || 'unknown')}</div>`;
        pulse.set({ series: [], message: 'No decisions to draw.' }); return;
      }
      const list = d.decisions;
      const pts = list.filter(x => isFinite(x.ms)).map(x => ({ t: x.t * 1000, v: Number(x.ms), bad: !x.ok })).sort((a, b) => a.t - b.t);
      pulse.set({ kind: 'dots', series: [{ name: 'Time to answer', tone: 'live', points: pts }], fmtY: msAxis, fmtTip: ms,
        fmtX: t => new Date(t).toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        fmtAxisX: t => new Date(t).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
        message: pts.length ? '' : 'No questions delegated since HomeShed last restarted.', label: 'How long each delegated question took to answer' });
      const local = list.filter(x => x.ok && !x.cloud).length, cloud = list.filter(x => x.ok && x.cloud).length, none = list.filter(x => !x.ok).length;
      $('mdlDecNote').textContent = list.length ? `${local} answered on your PC, ${cloud} in the cloud${none ? `, ${none} not answered` : ''} (last ${list.length})` : '';
      if (!list.length) { box.innerHTML = '<div class="ui-empty"><span>No questions delegated since HomeShed last restarted.</span></div>'; Models._last = 0; return; }
      const fresh = Models._last == null ? 0 : list.filter(x => x.t > Models._last).length;
      const REASON = { busy: 'busy', 'cloud not allowed': 'cloud not allowed', 'not configured': 'not set up', 'benched after a recent failure': 'benched' };
      kit().feed(box, list.map((x, i) => `<div class="ui-feed-row sx-dec${i < fresh ? ' new' : ''}"><span class="ui-feed-time">${new Date(x.t * 1000).toLocaleTimeString('en-GB', { hour12: false })}</span>
        <span class="ui-feed-main">${x.ok ? `<span class="ui-chip${x.cloud ? '' : ' good'}">${x.cloud ? 'Cloud' : 'Local'}</span> <b class="sx-mono">${esc(x.chosen)}</b>` : state('bad', 'Nobody answered')}
          <span class="ui-dim">${(x.skipped || []).map(s => `${esc(s.backend)} ${esc(REASON[s.reason] || 'failed: ' + s.reason)}`).join(' · ') || (x.mode === 'explicit' ? 'asked for this model by name' : 'first choice answered')}</span></span>
        <span class="ui-dim sx-mono">${ms(x.ms)}</span></div>`), { key: 'mdl-dec', limit: 10, empty: 'Nothing yet.' });
      Models._last = list[0].t;
    },
    // a GPU reading as a reactor: frac fills the arc; tone from the safeguard thresholds (measured, warn, bad)
    gauge(slot, o) {
      if (!$('gauges')) return;  // the GPU gauges are on the owner's page only
      let host = hostOf('mdl-g-' + slot);
      if (!host) { host = el('div'); keep('mdl-g-' + slot, () => new T.Reactor(host, { label: o.label })); }
      const box = $('gauges');
      let cellEl = box.querySelector(`[data-g="${slot}"]`);
      if (!cellEl) { cellEl = el('div', 'sx-gauge'); cellEl.dataset.g = slot; cellEl.append(host, el('p', 'sx-gauge-note')); box.appendChild(cellEl); }
      _inst.get('mdl-g-' + slot).set(o.value == null ? { status: 'unavailable', kind: 'measured', label: o.label, value: 'No data', period: o.why || 'no reading' }
        : { status: 'ok', kind: 'measured', tone: o.tone === 'bad' ? 'bad' : o.tone === 'warn' ? 'warn' : 'measured', label: o.label, value: o.value, unit: o.unit, period: o.period,
            raw: o.raw, arc: o.frac != null ? { frac: o.frac, title: o.title || '' } : null });
      cellEl.querySelector('.sx-gauge-note').textContent = o.note || '';
    },
  };

  // ---------------------------------------------------------------------------------------------------------------
  // Proxmox: GET /api/proxmox/monitor every 15 s while open, /api/proxmox/history for the drawer's graphs.
  // Power changes go through POST /api/run {tool: proxmox.vm.power}: they are queued for the owner's OK, never done
  // at once (the tool's own rule).
  // ---------------------------------------------------------------------------------------------------------------
  const Pve = (() => {
    const REFRESH_MS = 15000, TFS = ['hour', 'day', 'week', 'month', 'year'];
    let timer = null, data = null, busy = false, fail = null, tf = 'hour', openTimer = null, bound = false;
    const want = { q: '', node: '', state: '', sort: 'vmid' };
    let rx = null;
    const size = n => n == null ? '–' : n >= 1e12 ? (n / 1e12).toFixed(2) + ' TB' : n >= 1e9 ? (n / 1e9).toFixed(n >= 1e11 ? 0 : 1) + ' GB' : (n / 1e6).toFixed(0) + ' MB';
    const pct = (u, t) => (u != null && t) ? Math.round(u * 1000 / t) / 10 : null;
    const rate = b => b == null ? '–' : b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB/s' : b >= 1e3 ? Math.round(b / 1e3) + ' KB/s' : Math.round(b) + ' B/s';
    const dur = s => !s ? '–' : s < 3600 ? Math.round(s / 60) + ' min' : s < 172800 ? Math.round(s / 3600) + ' h' : Math.round(s / 86400) + ' days';
    const ago = ts => ts ? dur(Date.now() / 1000 - ts) + ' ago' : 'never';
    // the graphs' axis labels, short enough for the axis gutter (the hover and the window's head give the full figure)
    const axisOf = f => f === size ? v => v >= 1e12 ? (v / 1e12).toFixed(1) + 'T' : v >= 1e9 ? Math.round(v / 1e9) + 'G' : Math.round(v / 1e6) + 'M'
      : f === rate ? v => v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? Math.round(v / 1e3) + 'K' : String(Math.round(v)) : f;
    const alarmsFor = prefixes => ((data && data.alarms && data.alarms.active) || []).filter(a => prefixes.some(p => a.id.startsWith(p)));
    const mainFs = g => (g.fs || []).find(f => f.mount === '/' || /^[A-Z]:\\?$/.test(f.mount)) || (g.fs || [])[0];
    // a reading in a table cell: the figure, then its fine scale
    const meterCell = (p, sub) => p == null ? `<span class="ui-dim">${esc(sub || '–')}</span>`
      : `<span class="sx-meter"><b class="${toneOf(p / 100)}">${Math.round(p)}%</b>${ticks(p / 100, toneOf(p / 100))}</span>${sub ? `<span class="ui-sec">${esc(sub)}</span>` : ''}`;

    function start() { if (timer) return; load(); timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS); }
    function stop() { clearInterval(timer); timer = null; }
    async function load() {
      if (busy) return;
      busy = true;
      try { data = await root.apiCall('GET', '/api/proxmox/monitor'); fail = null; }
      catch (e) { fail = e; }
      finally { busy = false; }
      render();
    }
    function bind() {
      bound = true;
      rx = { cpu: new T.Reactor($('pveRxCpu'), { label: 'CPU' }), ram: new T.Reactor($('pveRxRam'), { label: 'RAM' }), disk: new T.Reactor($('pveRxDisk'), { label: 'Storage' }) };
      $('pveSearch').addEventListener('input', e => filter('q', e.target.value));
      $('pveNodeSel').addEventListener('change', e => filter('node', e.target.value));
      $('pveStateSel').addEventListener('change', e => filter('state', e.target.value));
      $('pveSortSel').addEventListener('change', e => filter('sort', e.target.value));
      $('pveRefresh').onclick = e => kit().busy(e.currentTarget, load);
      $('pveAlarmBtn').onclick = () => alarmSettings();
      rowClicks($('pveNodeRows'), key => show('node', key.slice(5)), (key, btn) => kit().menu(btn, [{ label: 'Graphs and details', run: () => show('node', key.slice(5)) }]));
      rowClicks($('pveGuestRows'), key => { const [kind, id] = key.split(':'); show(kind, Number(id)); }, (key, btn) => guestMenu(key, btn));
      kit().sticky($('pveGuestWork'));
      Mode.sync();
    }
    function render() {
      if (!bound) bind();
      live('pveLive', fail ? 'bad' : data ? 'ok' : '', fail ? (data ? "Couldn't refresh" : "Can't read Proxmox") : data ? 'Live' : 'Reading the cluster…');
      if (!data) {
        $('pveNodeRows').innerHTML = `<tr class="ui-empty-row"><td colspan="8"><div class="${fail ? 'ui-empty' : 'loading'}">${fail ? root.loadErr('the cluster', fail) : 'Reading the cluster…'}</div></td></tr>`;
        return;
      }
      const d = data, cl = d.cluster || {}, guests = (d.guests || []).filter(g => !g.template), act = (d.alarms && d.alarms.active) || [];
      const tot = pveTotals(d);
      $('pveUpdated').textContent = `Read from ${d.host || 'Proxmox'} ${new Date(d.at * 1000).toLocaleTimeString('en-GB')}`;
      const old = guests.filter(g => g.last_backup && Date.now() / 1000 - g.last_backup > 2 * 86400).length;
      strip($('pveStrip'), [
        { num: esc(cl.name || 'Proxmox'), label: cl.quorate === false ? 'Quorum lost' : cl.quorate ? 'Cluster, quorate' : 'Single node', tone: cl.quorate === false ? 'bad' : 'ok' },
        { num: `${cl.nodes_online ?? tot.online}<small>/${cl.nodes ?? tot.nodes}</small>`, label: 'Nodes online', f: frac(cl.nodes_online ?? tot.online, cl.nodes ?? tot.nodes), tone: (cl.nodes_online ?? tot.online) < (cl.nodes ?? tot.nodes) ? 'bad' : 'ok' },
        { num: `${tot.running}<small>/${tot.guests}</small>`, label: 'VMs and containers running', f: frac(tot.running, tot.guests) },
        { num: String(act.length), label: act.length === 1 ? 'Alarm' : 'Alarms', tone: act.some(a => a.severity === 'critical') ? 'bad' : act.length ? 'warn' : 'ok' },
        { num: String(old), label: 'Backups over 2 days old', tone: old ? 'warn' : 'ok' },
      ]);
      // the cluster's reactors: measured, added up over the nodes that answered
      rx.cpu.set(tot.cpu == null ? { status: 'unavailable', kind: 'measured', label: 'CPU', value: 'No data', period: 'no node answered' }
        : { status: 'ok', kind: 'measured', tone: tot.cpu >= 0.9 ? 'bad' : tot.cpu >= 0.75 ? 'warn' : 'measured', label: 'CPU', value: Math.round(tot.cpu * 100) + '%', raw: tot.cpu,
            unit: `${tot.threads} threads`, period: 'now, all nodes', arc: { frac: tot.cpu, title: 'CPU busy across the nodes, weighted by threads' } });
      const ramF = frac(tot.memUsed, tot.memTotal);
      rx.ram.set(ramF == null ? { status: 'unavailable', kind: 'measured', label: 'RAM', value: 'No data', period: 'no node answered' }
        : { status: 'ok', kind: 'measured', tone: ramF >= 0.9 ? 'bad' : ramF >= 0.75 ? 'warn' : 'measured', label: 'RAM', value: Math.round(ramF * 100) + '%',
            unit: `${size(tot.memUsed)} of ${size(tot.memTotal)}`, period: 'in use now', arc: { frac: ramF } });
      const useShared = tot.sharedTotal > 0, dU = useShared ? tot.sharedUsed : tot.diskUsed, dT = useShared ? tot.sharedTotal : tot.diskTotal, dF = frac(dU, dT);
      rx.disk.set(dF == null ? { status: 'unavailable', kind: 'measured', label: 'Storage', value: 'No data', period: 'no reading' }
        : { status: 'ok', kind: 'measured', tone: dF >= 0.9 ? 'bad' : dF >= 0.75 ? 'warn' : 'measured', label: useShared ? 'Shared storage' : 'System disks', value: Math.round(dF * 100) + '%',
            unit: `${size(dT - dU)} free`, period: `of ${size(dT)}`, arc: { frac: dF } });
      renderAlarms(act, (d.alarms && d.alarms.recent) || [], d.alarms && d.alarms.enabled);
      renderNodes();
      const sel = $('pveNodeSel'), names = (d.nodes || []).map(n => n.node);
      if (sel.options.length !== names.length + 1) { sel.innerHTML = '<option value="">All nodes</option>' + names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join(''); sel.value = want.node; }
      renderGuests();
      renderTasks(d.tasks || []);
      $('pveFoot').textContent = 'Disk use inside a VM needs its QEMU guest agent; containers report theirs directly.';
      const k = Drawer.key();
      if (k && k.startsWith('pve:')) { const s = subject(); if (s) Drawer.refresh(k, Object.assign(drawerHead(s), { live: Drawer.tab() !== 'graphs' })); }
    }
    function renderAlarms(act, recent, enabled) {
      const row = (a, cleared) => `<div class="ui-feed-row"><span class="ui-feed-time">${cleared ? ago(a.cleared) : 'for ' + ago(a.since).replace(' ago', '')}</span>
        <span class="ui-feed-main">${state(cleared ? '' : a.severity === 'critical' ? 'bad' : 'warn', cleared ? 'Cleared' : a.severity === 'critical' ? 'Urgent' : 'Warning')} <b>${esc(a.title)}</b><span class="ui-sec">${esc(a.message || '')}</span></span><span></span></div>`;
      $('pveAlarmCount').textContent = act.length ? `${act.length} active` : 'none active';
      $('pveAlarms').innerHTML = (enabled === false ? '<div class="ui-note warn">Proxmox alarms are switched off: nothing is pushed to your phone. <b>Alarm settings</b> switches them back on.</div>' : '')
        + (act.length ? act.map(a => row(a, false)).join('') : `<p class="sx-allclear">${state('good', 'Nothing needs a look')}</p>`)
        + (recent.length ? `<details class="ui-group"><summary>Cleared in the last week <span class="ui-dim">${recent.length}</span></summary>${recent.map(a => row(a, true)).join('')}</details>` : '');
    }
    function nodeHot(n) { return alarmsFor([`node-offline:${n.node}`, `node-ram:${n.node}`, `node-cpu:${n.node}`, `node-disk:${n.node}`, `smart:${n.node}:`, `wear:${n.node}:`, `storage:${n.node}:`]); }
    function ownStorage(n) {
      return (data.storage || []).filter(st => !st.shared && st.node === n.node && st.total && st.status === 'available'
        && !(st.type === 'dir' && Math.abs(st.total - (n.root_total || 0)) <= 0.01 * (n.root_total || 1)));
    }
    function renderNodes() {
      const nodes = data.nodes || [];
      kit().rerender($('pveNodeRows'), nodes.map(n => {
        const on = n.status === 'online', hot = nodeHot(n), own = ownStorage(n);
        const smart = (n.disks || []).length ? (n.disks.every(k => /PASSED|OK/i.test(k.health || '')) ? state('good', `${n.disks.length} disk${n.disks.length === 1 ? '' : 's'} healthy`)
          : state('bad', 'Check the disks')) : '<span class="ui-dim">no SMART</span>';
        const wear = (n.disks || []).filter(k => k.wear_left != null).map(k => k.wear_left);
        return `<tr data-key="node:${esc(n.node)}"${!on || hot.some(a => a.severity === 'critical') ? ' data-tone="bad"' : hot.length ? ' data-tone="warn"' : ''}>
          <td class="ui-name-cell"><button type="button" class="ui-rowlink" data-open data-key="node:${esc(n.node)}">${esc(n.node)}</button><span class="ui-sec">${esc([n.ip, n.threads ? n.threads + ' threads' : n.cpus ? n.cpus + ' CPUs' : '', n.pve ? 'PVE ' + n.pve : ''].filter(Boolean).join(' · '))}</span></td>
          <td data-label="State">${on ? state('good', 'Online') : state('bad', 'Offline')}${n.error ? `<span class="ui-sec ui-bad">${esc(n.error)}</span>` : ''}</td>
          <td data-label="CPU">${meterCell(on ? n.cpu_pct : null, on ? `load ${(n.load || []).slice(0, 1).map(v => v == null ? '–' : v.toFixed(2)).join('')}` : '')}</td>
          <td data-label="RAM">${meterCell(on ? pct(n.mem_used, n.mem_total) : null, on ? `${size(n.mem_used)} of ${size(n.mem_total)}` : '')}</td>
          <td data-label="System disk">${meterCell(on ? pct(n.root_used, n.root_total) : null, on ? `${size(n.root_avail ?? ((n.root_total || 0) - (n.root_used || 0)))} free` : '')}</td>
          <td class="ui-opt" data-label="Own storage">${own.length ? own.slice(0, 2).map(s => `<span class="sx-meter sx-meter-s" title="${esc(`${s.storage} (${s.type}): ${size((s.total || 0) - (s.used || 0))} free of ${size(s.total)}`)}"><span class="sx-mono">${esc(s.storage)}</span>${ticks(frac(s.used, s.total), toneOf(frac(s.used, s.total)))}</span>`).join('') : '<span class="ui-dim">–</span>'}</td>
          <td class="ui-opt2" data-label="Disks">${smart}${wear.length ? `<span class="ui-sec${Math.min(...wear) <= 20 ? ' ui-bad' : Math.min(...wear) <= 40 ? ' ui-warn' : ''}">${Math.min(...wear)}% life left, worst</span>` : ''}</td>
          <td class="num ui-opt3" data-label="Up">${on ? dur(n.uptime_s) : '–'}</td></tr>`;
      }).join('') || '<tr class="ui-empty-row"><td colspan="8"><div class="ui-empty">No nodes.</div></td></tr>');
    }
    function guestDisk(g) {
      if (g.kind === 'lxc') return g.disk != null ? [pct(g.disk, g.maxdisk), `${size((g.maxdisk || 0) - g.disk)} free of ${size(g.maxdisk)}`] : [null, `${size(g.maxdisk)} (stopped)`];
      const f = mainFs(g);
      if (f) return [pct(f.used, f.total), `${f.mount} ${size((f.total || 0) - (f.used || 0))} free`];
      if (g.status !== 'running') return [null, `${size(g.maxdisk)} (stopped)`];
      return [null, g.fs_note === 'guest agent not answering' ? 'guest agent not answering' : 'needs the guest agent'];
    }
    function guestRam(g) {
      if (g.mem_basis === 'host') return [null, `node holds ${size(g.mem)}`];
      return [pct(g.mem, g.mem_total || g.maxmem), `${size(g.mem)} of ${size(g.mem_total || g.maxmem)}`];
    }
    function renderGuests() {
      const q = want.q.trim().toLowerCase();
      let gs = (data.guests || []).filter(g => (!want.node || g.node === want.node)
        && (!want.state || (want.state === 'running' ? g.status === 'running' : g.status !== 'running'))
        && (!q || String(g.vmid).includes(q) || String(g.name || '').toLowerCase().includes(q) || (g.tags || []).some(t => t.includes(q))));
      const by = { name: g => String(g.name || ''), cpu: g => -(g.cpu_pct || 0), ram: g => -(guestRam(g)[0] || 0), disk: g => -(guestDisk(g)[0] || 0), vmid: g => g.vmid };
      const key = by[want.sort] || by.vmid;
      gs = gs.slice().sort((a, b) => (a.template - b.template) || (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
      const all = (data.guests || []).length;
      $('pveGuestCount').textContent = gs.length === all ? `${all} guests` : `${gs.length} of ${all}`;
      kit().rerender($('pveGuestRows'), gs.map(g => {
        const on = g.status === 'running', [dp, dsub] = guestDisk(g), [rp, rsub] = guestRam(g);
        const hot = alarmsFor([`stopped:${g.vmid}`, `guest-ram:${g.vmid}`, `guest-disk:${g.vmid}:`, `backup:${g.vmid}`]);
        const oldB = g.last_backup && Date.now() / 1000 - g.last_backup > 2 * 86400, k = `${g.kind}:${g.vmid}`;
        return `<tr data-key="${esc(k)}"${hot.length ? ' data-tone="bad"' : oldB ? ' data-tone="warn"' : ''}${on ? '' : ' class="sx-off"'}>
          <td class="ui-name-cell"><button type="button" class="ui-rowlink" data-open data-key="${esc(k)}">${esc(g.name || 'unnamed')}</button><span class="ui-sec">${g.template ? 'template' : g.kind === 'lxc' ? 'CT' : 'VM'} ${esc(g.vmid)}${(g.tags || []).length ? ' · ' + g.tags.map(esc).join(', ') : ''}</span></td>
          <td data-label="State">${on ? state('good', 'Running') : hot.length ? state('bad', g.status || 'stopped') : state('', g.status ? g.status[0].toUpperCase() + g.status.slice(1) : 'Stopped')}<span class="ui-sec">${on ? 'up ' + dur(g.uptime_s) : ''}</span></td>
          <td class="ui-opt" data-label="Node">${esc(g.node)}</td>
          <td data-label="CPU">${on ? meterCell(g.cpu_pct, `${g.cpus || '?'} CPU`) : '<span class="ui-dim">–</span>'}</td>
          <td data-label="RAM">${on ? meterCell(rp, rsub) : `<span class="ui-dim">${size(g.maxmem)}</span>`}</td>
          <td data-label="Disk">${meterCell(dp, dsub)}</td>
          <td class="ui-opt2 num" data-label="Network">${on ? `<span class="sx-mono">↓ ${rate(g.net_in_bps)}</span><span class="ui-sec">↑ ${rate(g.net_out_bps)}</span>` : '<span class="ui-dim">–</span>'}</td>
          <td class="ui-opt3" data-label="Backup">${g.last_backup ? `<span class="${oldB ? 'ui-warn' : ''}">${ago(g.last_backup)}</span>` : '<span class="ui-dim">none</span>'}</td>
          <td class="ui-c-act">${g.template ? '' : kebab('menu:' + k, 'Actions for ' + (g.name || g.vmid))}</td></tr>`;
      }).join('') || `<tr class="ui-empty-row"><td colspan="9"><div class="ui-empty"><b>Nothing matches.</b></div></td></tr>`);
    }
    function guestMenu(key, btn) {
      const [kind, id] = key.split(':'), g = (data.guests || []).find(x => x.kind === kind && String(x.vmid) === id);
      if (!g) return;
      const on = g.status === 'running';
      kit().menu(btn, [{ label: 'Graphs and details', run: () => show(kind, g.vmid) }, '-',
        ...(on ? [{ label: 'Reboot…', run: () => power(g, 'reboot') }, { label: 'Shut down…', run: () => power(g, 'shutdown') }, { label: 'Power off…', danger: true, run: () => power(g, 'stop') }]
               : [{ label: 'Start…', run: () => power(g, 'start') }])]);
    }
    const POWER = { start: ['Start', 'starts'], reboot: ['Reboot', 'restarts: whatever it serves is down for a minute or two'],
                    shutdown: ['Shut down', 'is asked to shut down cleanly, and stays off until you start it'],
                    stop: ['Power off', 'is switched off at once, like pulling the plug: unsaved work inside it is lost'] };
    function power(g, action) {
      const name = g.name || `${g.kind === 'lxc' ? 'CT' : 'VM'} ${g.vmid}`;
      const [verb, what] = POWER[action];
      kit().confirm({ title: `${verb} ${name}?`, action: `Queue: ${verb.toLowerCase()}`, danger: action === 'stop' || action === 'shutdown',
        body: `<b>${esc(name)}</b> (${g.kind === 'lxc' ? 'container' : 'VM'} ${esc(g.vmid)} on ${esc(g.node)}) ${esc(what)}.<br><br>This queues the change: nothing happens on Proxmox until you approve it under <b>Waiting for you</b>.`,
        run: async () => {
          const r = await root.apiCall('POST', '/api/run', { tool: 'proxmox.vm.power', args: { vmid: g.vmid, action, node: g.node, kind: g.kind === 'lxc' ? 'lxc' : 'qemu', dry_run: false } });
          if (!r.ok) throw new Error(r.error || 'Not queued.');
          return r.result || true;
        } }).then(res => { if (res) kit().toast(`${verb} ${name}: queued for your OK.`); });
    }
    function renderTasks(tasks) {
      const card = $('pveTasks');
      card.hidden = !tasks.length;
      $('pveTaskRows').innerHTML = tasks.slice(0, 12).map(t => `<tr${t.status && t.status !== 'OK' ? ' data-tone="bad"' : ''}>
        <td class="ui-name-cell"><span class="sx-strong">${esc(t.type === 'vzdump' ? 'Backup' : t.type)}${t.id ? ' ' + esc(t.id) : ''}</span><span class="ui-sec">on ${esc(t.node || '')}</span></td>
        <td data-label="Started">${t.start ? esc(new Date(t.start * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })) : ''}</td>
        <td class="num ui-opt" data-label="Took">${t.end && t.start ? dur(t.end - t.start) : '–'}</td>
        <td data-label="Result">${!t.status ? state('', 'Running') : t.status === 'OK' ? state('good', 'OK') : state('bad', t.status)}</td></tr>`).join('');
    }
    function filter(k, v) { want[k] = v; if (data) renderGuests(); }

    // --- the drawer: Proxmox's own graphs (an hour to a year) as traces, and the details ---
    let open = null;
    const pulses = [];  // made once; the graphs tab reuses them
    let graphsEl = null;
    function subject() {
      if (!open || !data) return null;
      return open.kind === 'node' ? (data.nodes || []).find(n => n.node === open.id) : (data.guests || []).find(g => g.vmid === open.id);
    }
    function drawerHead(s) {
      const isNode = open.kind === 'node', on = isNode ? s.status === 'online' : s.status === 'running';
      return { title: isNode ? `Node ${s.node}` : `${s.name || 'unnamed'}`,
        sub: `${on ? state('good', isNode ? 'Online' : 'Running') : state(isNode ? 'bad' : '', s.status || 'stopped')}${isNode ? `<span>${esc(s.ip || '')}</span>` : `<span class="ui-chip plain">${s.kind === 'lxc' ? 'Container' : 'VM'} ${esc(s.vmid)}</span><span>on ${esc(s.node)}</span>`}` };
    }
    function show(kind, id) {
      open = { kind, id };
      const s = subject();
      if (!s) return;
      Drawer.open(Object.assign(drawerHead(s), { key: `pve:${kind}:${id}`, view: 'proxmoxView',
        tabs: [{ id: 'graphs', label: 'Graphs' }, { id: 'details', label: 'Details' }], tab: 'graphs',
        onClose: () => { open = null; clearInterval(openTimer); },
        render: (tab, body, again) => { if (tab === 'graphs') { if (!again) graphs(body); } else details(body); } }));
      clearInterval(openTimer);
      openTimer = setInterval(() => { if (!document.hidden && tf === 'hour' && open && Drawer.tab() === 'graphs') fill(); }, 60000);
    }
    function graphs(body) {
      if (!graphsEl) {
        graphsEl = el('div', 'sx-graphs');
        graphsEl.innerHTML = `<div class="sx-graph-bar"><div class="ck-seg" role="group" aria-label="Time span">${TFS.map(t => `<button type="button" data-tf="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}</div>
          <span class="prov m">measured by Proxmox</span><span class="ui-sub sx-graph-note"></span></div><div class="sx-graph-grid"></div>`;
        graphsEl.querySelectorAll('[data-tf]').forEach(b => { b.onclick = () => { tf = b.dataset.tf; fill(); }; });
        const grid = graphsEl.querySelector('.sx-graph-grid');
        for (let i = 0; i < 6; i++) {
          const w = el('section', 'sx-graph tw inst'); w.innerHTML = '<header class="tw-head"><h3 class="tw-title"></h3><span class="tw-meta"></span></header>';
          const h = el('div', 'sx-graph-plot'); w.appendChild(h); grid.appendChild(w);
          pulses.push({ w, p: new T.Pulse(h, { compact: true }) });
        }
      }
      body.innerHTML = '';
      body.appendChild(graphsEl);
      fill();
    }
    async function fill() {
      const s = subject();
      if (!s || !graphsEl) return;
      graphsEl.querySelectorAll('[data-tf]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.tf === tf)));
      const note = graphsEl.querySelector('.sx-graph-note');
      note.textContent = 'Reading…';
      const isNode = open.kind === 'node', q = new URLSearchParams({ kind: open.kind, node: s.node, timeframe: tf });
      if (!isNode) q.set('vmid', s.vmid);
      let h;
      try { h = await root.apiCall('GET', '/api/proxmox/history?' + q); }
      catch (e) { note.textContent = e.message; pulses.forEach(x => { x.p.set({ series: [], message: "Couldn't read the graphs." }); }); return; }
      if (!open) return;
      note.textContent = '';
      const rows = h.points || [], P = v => v * 100, pc = v => v.toFixed(v < 10 ? 1 : 0) + '%';
      const specs = isNode ? [
        ['CPU and IO wait', [['cpu', 'CPU', P, 'measured'], ['iowait', 'IO wait', P, 'live']], pc],
        ['RAM', [['memused', 'Used', null, 'measured'], ['memtotal', 'Size', null, 'live']], size],
        ['System disk', [['rootused', 'Used', null, 'measured'], ['roottotal', 'Size', null, 'live']], size],
        ['Network', [['netin', 'In', null, 'measured'], ['netout', 'Out', null, 'live']], rate],
        ['Load', [['loadavg', 'Load', null, 'measured']], v => v.toFixed(2)],
        ['Swap', [['swapused', 'Used', null, 'measured'], ['swaptotal', 'Size', null, 'live']], size],
      ] : [
        ['CPU', [['cpu', 'CPU', P, 'measured']], pc],
        ['RAM', [['mem', 'Used', null, 'measured'], ['maxmem', 'Size', null, 'live']], size],
        ['Network', [['netin', 'In', null, 'measured'], ['netout', 'Out', null, 'live']], rate],
        ['Disk reads and writes', [['diskread', 'Read', null, 'measured'], ['diskwrite', 'Write', null, 'live']], rate],
        ...(s.kind === 'lxc' ? [['Disk use', [['disk', 'Used', null, 'measured'], ['maxdisk', 'Size', null, 'live']], size]] : []),
      ];
      const fx = t => new Date(t).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
      const fa = t => tf === 'hour' || tf === 'day' ? new Date(t).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
      pulses.forEach((x, i) => {
        const sp = specs[i];
        x.w.hidden = !sp;
        if (!sp) return;
        const series = sp[1].map(([k, name, f, tone]) => ({ name, tone, points: pvePoints(rows, k, f) }));
        const lastOf = s2 => { const v = [...s2.points].reverse().find(p => p.v != null); return v ? `${s2.name} ${sp[2](v.v)}` : ''; };
        x.w.querySelector('.tw-title').textContent = sp[0];
        x.w.querySelector('.tw-meta').textContent = series.map(lastOf).filter(Boolean).join(' · ');
        const any = series.some(s2 => s2.points.some(p => p.v != null));
        x.p.set({ series, fmtY: axisOf(sp[2]), fmtTip: sp[2], fmtX: fx, fmtAxisX: fa, legendAlways: series.length > 1, message: any && rows.length > 1 ? '' : 'No data for this span yet.',
          label: `${sp[0]}, last ${tf}: ${series.map(lastOf).filter(Boolean).join(', ')}` });
      });
    }
    function details(body) {
      const s = subject();
      if (!s) { body.innerHTML = '<div class="ui-empty">Not found any more.</div>'; return; }
      if (open.kind === 'node') {
        const own = ownStorage(s);
        body.innerHTML = `<div class="ui-block"><dl class="ui-dl"><dt>State</dt><dd>${esc(s.status)}</dd><dt>Address</dt><dd class="sx-mono">${esc(s.ip || '–')}</dd>
            <dt>Processor</dt><dd>${esc([s.cpu_model, s.threads ? s.threads + ' threads' : s.cpus ? s.cpus + ' CPUs' : ''].filter(Boolean).join(', ') || '–')}</dd>
            <dt>Proxmox</dt><dd>${esc(s.pve || '–')}${s.kernel ? `, kernel ${esc(s.kernel)}` : ''}</dd><dt>Up</dt><dd>${dur(s.uptime_s)}</dd>
            ${s.iowait != null ? `<dt>IO wait</dt><dd>${(s.iowait * 100).toFixed(1)}%</dd>` : ''}</dl></div>
          <div class="ui-block"><h3>Readings now</h3><div class="sx-readings">
            <div><span>CPU</span>${meterCell(s.cpu_pct, `load ${(s.load || []).map(v => v == null ? '–' : v.toFixed(2)).join(' / ') || '–'}`)}</div>
            <div><span>RAM</span>${meterCell(pct(s.mem_used, s.mem_total), `${size(s.mem_used)} of ${size(s.mem_total)}`)}</div>
            <div><span>System disk</span>${meterCell(pct(s.root_used, s.root_total), `${size(s.root_avail ?? ((s.root_total || 0) - (s.root_used || 0)))} free of ${size(s.root_total)}`)}</div>
            <div><span>Swap</span>${meterCell(pct(s.swap_used, s.swap_total), `${size(s.swap_used)} of ${size(s.swap_total)}`)}</div>
            ${own.map(st => `<div><span class="sx-mono">${esc(st.storage)}</span>${meterCell(pct(st.used, st.total), `${st.type}: ${size((st.total || 0) - (st.used || 0))} free of ${size(st.total)}`)}</div>`).join('')}</div></div>
          ${(s.disks || []).length ? `<div class="ui-block"><h3>Physical disks</h3><table class="ui-table"><tbody>${s.disks.map(k => `<tr>
            <td class="ui-name-cell"><span class="sx-mono">${esc(k.dev || '')}</span><span class="ui-sec">${esc(k.model || '')} · ${esc(k.type || '')}, ${size(k.size)}</span></td>
            <td>${/PASSED|OK/i.test(k.health || '') ? state('good', k.health) : k.health ? state('bad', k.health) : '<span class="ui-dim">no SMART</span>'}${k.wear_left != null ? `<span class="ui-sec${k.wear_left <= 20 ? ' ui-bad' : k.wear_left <= 40 ? ' ui-warn' : ''}">${k.wear_left}% life left</span>` : ''}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
        return;
      }
      const on = s.status === 'running', [dp, dsub] = guestDisk(s), [rp, rsub] = guestRam(s);
      body.innerHTML = `<div class="ui-block"><dl class="ui-dl"><dt>State</dt><dd>${esc(s.status)}${on ? `, up ${dur(s.uptime_s)}` : ''}</dd><dt>Node</dt><dd>${esc(s.node)}</dd>
          <dt>Size</dt><dd>${esc(s.cpus || '?')} CPU, ${size(s.maxmem)} RAM, ${size(s.maxdisk)} disk</dd>
          <dt>Backup</dt><dd>${s.last_backup ? 'last ' + ago(s.last_backup) : 'none'}</dd>${(s.tags || []).length ? `<dt>Tags</dt><dd>${s.tags.map(esc).join(', ')}</dd>` : ''}</dl></div>
        ${on ? `<div class="ui-block"><h3>Readings now</h3><div class="sx-readings">
          <div><span>CPU</span>${meterCell(s.cpu_pct, `of ${s.cpus || '?'} CPU${s.cpus === 1 ? '' : 's'}`)}</div>
          <div><span>RAM</span>${meterCell(rp, rsub + (s.mem_basis === 'guest' ? ', incl. cache' : s.mem_basis === 'available' ? ', real use' : s.mem_basis === 'host' ? ': real use needs the balloon device' : ''))}</div>
          <div><span>Disk</span>${meterCell(dp, dsub)}</div>
          <div><span>Network</span><span class="sx-mono">↓ ${rate(s.net_in_bps)} · ↑ ${rate(s.net_out_bps)}</span></div>
          <div><span>Disk traffic</span><span class="sx-mono">read ${rate(s.disk_read_bps)} · write ${rate(s.disk_write_bps)}</span></div></div></div>` : ''}
        ${(s.fs || []).length ? `<div class="ui-block"><h3>File systems inside</h3><div class="sx-readings">${s.fs.map(f => `<div><span class="sx-mono">${esc(f.mount)}</span>${meterCell(pct(f.used, f.total), `${f.type || '?'}: ${size((f.total || 0) - (f.used || 0))} free of ${size(f.total)}`)}</div>`).join('')}</div></div>`
          : s.kind === 'qemu' ? `<div class="ui-block"><p class="hint">Disk use inside this VM needs the QEMU guest agent: in the VM, install it (Debian/Ubuntu: <code>apt install qemu-guest-agent</code>), then in Proxmox switch on Options &gt; QEMU Guest Agent and restart the VM.</p></div>` : ''}
        ${s.template ? '' : `<div class="ui-block"><h3>Power</h3><div class="ui-btnrow">${on ? '<button type="button" class="ghost" data-p="reboot">Reboot…</button><button type="button" class="ghost" data-p="shutdown">Shut down…</button><button type="button" class="ghost danger" data-p="stop">Power off…</button>'
          : '<button type="button" class="ghost" data-p="start">Start…</button>'}</div><p class="hint">Each one is queued for your OK under <b>Waiting for you</b>; nothing changes until you approve it.</p></div>`}`;
      body.querySelectorAll('[data-p]').forEach(b => { b.onclick = () => power(s, b.dataset.p); });
    }
    // Settings, opened on the alarms' section (panel.js's openSettingsAt loads the Settings page's file first)
    function alarmSettings() { root.openSettingsAt('Proxmox alarms'); }
    return { start, stop, filter, show, close: () => Drawer.close(), alarmSettings };
  })();

  // ---- the page's hooks: the old names (the markup's onclick and the other pages call some), and what switchTab and
  // the poll do for these pages (panel.js's Pages: this file loads the first time one of them opens) ----
  Object.assign(root, {
    loadTools: () => Tools.load(), loadRun: () => Run.load(), selectRunTool: (id, a, n) => Run.select(id, a, n), runNow: () => Run.now(),
    loadDockerContainers: () => Docker.load(), loadMonitors: () => Uptime.load(), loadSavings: () => Savings.load(), Pve,
  });
  if (hasDOM && root.Pages) {
    let uptimeLoaded = false, dockerLoaded = false;  // the first visit loads once; the poll keeps it current after that
    root.Pages.on('savings', { enter: () => Savings.load() });
    root.Pages.on('tools', { enter: () => Tools.load() });
    root.Pages.on('run', { enter: () => Run.load() });
    root.Pages.on('proxmox', { enter: () => Pve.start(), leave: () => Pve.stop() });
    root.Pages.on('monitors', { enter: () => { if (!uptimeLoaded) { uptimeLoaded = true; Uptime.load(); } }, poll: () => Uptime.load() });
    root.Pages.on('docker', { enter: () => { if (!dockerLoaded) { dockerLoaded = true; Docker.load(); } }, poll: () => Docker.load() });
  }
  // the Tools page refreshes every 10 s while it's open (as before)
  setInterval(() => { if (isOpen('toolsView')) Tools.load(); }, 10000);
  // pages with their own live state and Refresh: <html> carries a class while one is open (pages-ops.css hides the
  // shared header's pair). Watches only these views' class attribute, so the Overview's updates never trigger it.
  const OWN_BAR = ['toolsView', 'dockerView', 'monitorsView', 'savingsView', 'proxmoxView'], TIGHT = OWN_BAR.concat(['runView', 'modelsView']);
  function barClass() {
    const on = id => { const v = $(id); return !!v && v.classList.contains('active'); };
    document.documentElement.classList.toggle('sx-own-bar', OWN_BAR.some(on));
    document.documentElement.classList.toggle('sx-tight-head', TIGHT.some(on));
  }
  // the page fetches this file the first time one of its pages opens, long after DOMContentLoaded: wire up at once then
  const whenReady = fn => (document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn());
  if (hasDOM) whenReady(() => {
    Mode.sync();
    barClass();
    if (!root.MutationObserver) return;
    const mo = new MutationObserver(barClass);
    TIGHT.forEach(id => { const v = $(id); if (v) mo.observe(v, { attributes: true, attributeFilter: ['class'] }); });
  });

  return Object.assign({}, pure, { Mode, Drawer, Tools, Run, Docker, Uptime, Savings, Models, Pve, ticks, strip, live });
});
