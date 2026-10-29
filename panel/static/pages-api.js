/* The API access page (static/pages-api.js): every app, agent and person with a token, their tools, limits and
   audit. Loaded the first time the page opens (Pages in panel.js). */
// --- API access page (2026-10-27 redesign; the owner's brief: show what matters at once, the rest only when needed;
// effortless at 20 clients, workable at 500). One row per client; a row opens the side drawer. Search, filters, sort,
// the open client and its tab survive the 20 s refresh and a reload. Every control posts to the same /api/clients
// routes as before (dashboard/main.py, mcp-server/clients.py); nothing here is invented or kept in the page.
let _apiClients = [], _apiTools = [], _apiConn = [], _apiAudit = [], _apiShared = [], _apiPro = false, _apiUnreadable = false;
let _apiLoaded = 0, _apiFail = null, _apiToolsOk = false, _apiNew = null;
const _apSt = UI.keep('cp-api-page', { q: '', status: 'all', seen: 'any', access: 'any', sort: 'name', sel: null, tab: 'overview',
  grantQ: '', grantOnly: false, shown: 25, audit: 'all' });
const _apOpenCats = new Set();
function clientStatus(c) {
  if (c.suspended) return ['bad', 'Suspended'];
  if (c.expired) return ['bad', 'Expired'];
  if (!c.last_used) return ['warn', 'Never used'];
  return ['good', 'Active'];
}
function connectSnippets(name, token) {
  const url = `http://${location.hostname}:8765/mcp`;
  const v = name.toUpperCase().replace(/[^A-Z0-9]/g, '_') + '_MCP_TOKEN';
  return `<div class="hint">Store it in a user environment variable named <code>${v}</code>, never in code or a committed file, and open a new terminal. Calls made with it are labelled <b>${escapeHtml(name)}</b> on the Overview and counted on this page.</div>
    <pre>Claude Code: .mcp.json in the project's root folder (the token is read when Claude starts)
{ "mcpServers": { "homeshed": { "type": "http", "url": "${url}",
  "headers": { "Authorization": "Bearer \${${v}}" } } } }</pre>
    <pre>Python (mcp SDK)
from mcp.client.streamable_http import streamablehttp_client
async with streamablehttp_client("${url}", headers={"Authorization": f"Bearer {os.environ['${v}']}"}) as (r, w, _): ...</pre>
    <pre>OpenCode (opencode.json)
"mcp": { "homeshed": { "type": "remote", "url": "${url}", "headers": { "Authorization": "Bearer {env:${v}}" } } }</pre>`;
}
function showToken(name, token, verb) {
  const box = document.getElementById('apToken');
  box.innerHTML = `<b>${escapeHtml(verb)} ${escapeHtml(name)}. Copy the token now: it won't be shown again.</b>
    <code id="apTokenText">${escapeHtml(token)}</code>
    <button class="ghost" onclick="navigator.clipboard.writeText(document.getElementById('apTokenText').textContent).then(() => this.textContent = 'Copied')">Copy token</button>
    <button class="ghost" onclick="document.getElementById('apToken').className = 'token-box'; document.getElementById('apToken').innerHTML = ''">I've saved it</button>
    ${connectSnippets(name, token)}`;
  box.className = 'token-box show';
  box.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
  box.focus({ preventScroll: true });
}
async function loadApi() {
  let d;
  try { d = await apiCall('GET', '/api/clients'); }
  catch (e) { _apiFail = e; apRender(); return; }
  const [t, se, pro] = await Promise.all([apiCall('GET', '/api/tools').catch(() => null),
    apiCall('GET', '/api/connections').catch(() => ({ connections: [] })), proStatus().catch(() => ({}))]);
  _apiClients = d.clients || []; _apiAudit = d.audit || []; _apiUnreadable = !!d.unreadable;
  if (t) { _apiTools = t.tools || []; _apiToolsOk = true; }
  _apiConn = se.connections || []; _apiPro = !!(pro.pro || pro.effective_pro);
  _apiLoaded = Date.now(); _apiFail = null;
  apRender();
  const again = _apSt._reopen; delete _apSt._reopen;  // the client that was open before a reload
  if (again && _activeTab === 'api' && _apiClients.some(c => c.name === again)) Api.open(again);
  loadSharedWaiting();
  loadFrontDoorSavings();
  loadSigninHelp();
}
// How the client connects: OAuth-signed-in apps are made by oauth_server.py with this note; everything else uses a token.
function clientIdentity(c) {
  const m = /^Signed in with OAuth: (.+)$/.exec(c.note || '');
  if (m) return { kind: 'oauth', label: 'OAuth: ' + m[1], purpose: '' };
  return { kind: 'token', label: c.managed_by ? `Token, managed by ${c.managed_by}` : 'Token', purpose: c.note || '' };
}
// The access it was given, read from its allow/deny patterns (clients.PRESETS: none [], read ["read:*"], all ["*"]).
function clientAccess(c) {
  const a = c.allow || [], d = c.deny || [];
  if (!a.length) return ['none', 'No tools'];
  if (!d.length && a.length === 1 && a[0] === '*') return ['all', 'Everything'];
  if (!d.length && a.length === 1 && a[0] === 'read:*') return ['read', 'Read-only'];
  return ['custom', 'Chosen tools'];
}
function clientGranted(c) { return _apiToolsOk ? _apiTools.filter(x => (x.clients || {})[c.name]).length : null; }
const apProblems = c => ((c.usage || {}).errors || 0) + ((c.usage || {}).refused || 0);
const AP_DAY = 86400;
const AP_SORT = {
  name: (a, b) => a.name.localeCompare(b.name),
  recent: (a, b) => (b.last_used || 0) - (a.last_used || 0) || a.name.localeCompare(b.name),
  calls: (a, b) => ((b.usage || {}).calls || 0) - ((a.usage || {}).calls || 0) || a.name.localeCompare(b.name),
  errors: (a, b) => apProblems(b) - apProblems(a) || a.name.localeCompare(b.name),
};
function apFiltered() { const s = _apSt; return !!(s.q.trim() || s.status !== 'all' || s.seen !== 'any' || s.access !== 'any'); }
function apMatches(c) {
  const s = _apSt, now = Date.now() / 1000, q = s.q.trim().toLowerCase(), used = c.last_used || 0;
  if (q && !c.name.includes(q) && !(c.note || '').toLowerCase().includes(q)) return false;
  if (s.status === 'active' && clientStatus(c)[1] !== 'Active') return false;
  if (s.status === 'suspended' && !c.suspended) return false;
  if (s.status === 'expired' && !c.expired) return false;
  if (s.status === 'never' && used) return false;
  if (s.status === 'blocked' && !(c.suspended || c.expired)) return false;
  if (s.seen === 'day' && !(used > now - AP_DAY)) return false;
  if (s.seen === 'week' && !(used > now - 7 * AP_DAY)) return false;
  if (s.seen === 'idle' && used > now - 30 * AP_DAY) return false;
  if (['none', 'read', 'all', 'custom'].includes(s.access) && clientAccess(c)[0] !== s.access) return false;
  if (s.access === 'front_door' && c.tool_view !== 'front_door') return false;
  if (s.access === 'full' && c.tool_view === 'front_door') return false;
  if (s.access === 'oauth' && clientIdentity(c).kind !== 'oauth') return false;
  return true;
}
const AP_DOTS = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="8" r="1.4"/><circle cx="8" cy="8" r="1.4"/><circle cx="13" cy="8" r="1.4"/></svg>';
const apMoney = v => '$' + Number(v || 0).toFixed(2);
function apRender() {
  apHeader();
  apMetrics();
  apToolbar();
  apRows();
  apAuditRender();
  if (_apSt.sel) apDrawer(true);
}
function apTick() { const el = document.getElementById('apUpdated'); if (el) el.textContent = _apiLoaded ? 'Updated ' + agoText(new Date(_apiLoaded).toISOString()) : ''; }
function apHeader() {
  const live = document.getElementById('apLive');
  live.className = 'ui-live ' + (_apiFail ? 'bad' : _apiLoaded ? 'ok' : '');
  document.getElementById('apLiveText').textContent = _apiFail ? "Can't reach HomeShed" : _apiLoaded ? 'Live' : 'Loading…';
  live.title = _apiFail ? String(_apiFail.message || '') : 'Refreshes every 20 seconds while this page is open';
  apTick();
  const msgs = [];
  if (_apiUnreadable) msgs.push('The clients file on HomeShed is unreadable, so every client token is being refused until it is fixed.');
  if (_apiFail && _apiLoaded) msgs.push(`${loadErr('the latest client list', _apiFail)} What you see was loaded ${escapeHtml(agoText(new Date(_apiLoaded).toISOString()))}.`);
  if (_apiLoaded && !_apiToolsOk) msgs.push("Couldn't load the tool list, so tool counts and tool choices aren't shown. Press Refresh to try again.");
  const b = document.getElementById('apBanner');
  b.hidden = !msgs.length; b.innerHTML = msgs.map(m => `<div>${m}</div>`).join('');
}
function apMetrics() {
  const week = Date.now() / 1000 - 7 * AP_DAY, sum = k => _apiClients.reduce((a, c) => a + ((c.usage || {})[k] || 0), 0);
  const set = (id, v) => { document.getElementById(id).textContent = _apiLoaded ? v.toLocaleString() : '—'; };
  const blocked = _apiClients.filter(c => c.suspended || c.expired).length;
  set('apClients', _apiClients.length);
  set('apActive', _apiClients.filter(c => c.last_used && c.last_used > week).length);
  set('apBlocked', blocked);
  set('apCalls', sum('calls'));
  set('apRefused', sum('refused'));
  document.getElementById('apBlocked').parentElement.classList.toggle('warn', blocked > 0);  // orange only when it needs a look
  document.getElementById('apRefused').parentElement.classList.toggle('warn', sum('refused') > 0);
  const s = _apSt, plain = !s.q.trim() && s.access === 'any';
  const on = { all: !apFiltered(), week: plain && s.seen === 'week' && s.status === 'all', blocked: plain && s.status === 'blocked' && s.seen === 'any' };
  document.querySelectorAll('#apiView .ui-metric[data-metric]').forEach(b => b.setAttribute('aria-pressed', String(!!on[b.dataset.metric])));
}
function apToolbar() {
  const s = _apSt;
  for (const [id, k] of [['apSearch', 'q'], ['apStatus', 'status'], ['apSeen', 'seen'], ['apAccess', 'access'], ['apSort', 'sort']]) {
    const el = document.getElementById(id);
    if (el !== document.activeElement && el.value !== s[k]) el.value = s[k];
  }
  document.getElementById('apClear').hidden = !apFiltered();
  document.getElementById('apAuditKind').value = s.audit;
}
function apRow(c, live) {
  const [tone, label] = clientStatus(c), u = c.usage || {}, id = clientIdentity(c), [, acc] = clientAccess(c), g = clientGranted(c);
  const sp = (c.spend_today || {}).total || 0, cap = c.daily_spend_usd, n = escAttr(c.name);
  const spTone = cap != null && sp >= cap ? 'ui-bad' : cap != null && cap > 0 && sp >= cap * 0.8 ? 'ui-warn' : '';
  const refused = u.refused || 0, errors = u.errors || 0;
  return `<tr data-key="row:${n}" data-name="${n}" data-tone="${tone === 'good' ? '' : tone}"${c.name === _apSt.sel ? ' class="is-sel"' : c.name === _apiNew ? ' class="is-new"' : ''}>
    <td class="ui-name-cell"><button type="button" class="ui-rowlink" data-key="open:${n}" aria-haspopup="dialog">${escapeHtml(c.name)}</button>
      <span class="ui-sec">${id.kind === 'oauth' ? `<span class="ui-chip">${escapeHtml(id.label)}</span> ` : ''}${escapeHtml(id.purpose) || (id.kind === 'oauth' ? '' : '<span class="ui-dim">No purpose given</span>')}</span></td>
    <td data-label="Status"><span class="ui-state ${tone}">${label}</span><span class="ui-sec">${live ? `<span class="ui-ok">Connected now${live > 1 ? ` (${live})` : ''}</span>`
      : c.expires && !c.expired ? 'Expires ' + UI.day(c.expires) : c.expired ? 'Since ' + UI.day(c.expires) : '&nbsp;'}</span></td>
    <td data-label="Permissions">${g == null ? '<span class="ui-dim">?</span>' : `<b>${g}</b> <span class="ui-dim">of ${_apiTools.length}</span>`}
      <span class="ui-sec">${acc}${c.tool_view === 'front_door' ? ', front door' : ''}</span></td>
    <td data-label="Last used">${c.last_used ? escapeHtml(UI.ago(c.last_used)) : '<span class="ui-dim">Never</span>'}
      <span class="ui-sec">${(u.calls || 0).toLocaleString()} calls${refused ? `, <span class="ui-bad">${refused.toLocaleString()} refused</span>` : ''}${errors ? `, ${errors.toLocaleString()} errors` : ''}</span></td>
    <td class="num ui-opt" data-label="Rate limit">${c.rate_per_min ? `${c.rate_per_min.toLocaleString()}<span class="ui-dim">/min</span>` : '<span class="ui-dim">None</span>'}</td>
    <td class="num ui-opt2" data-label="Spend today"><span class="${spTone}">${apMoney(sp)}</span><span class="ui-sec">${cap != null ? 'of ' + apMoney(cap) : 'no cap'}</span></td>
    <td class="ui-opt3" data-label="Memory">Own<span class="ui-sec">${c.memory_shared_read === false ? 'no shared reads' : 'reads shared'}</span></td>
    <td class="ui-c-act"><button type="button" class="ui-kebab" data-key="menu:${n}" aria-haspopup="menu" aria-expanded="false" aria-label="Actions for ${n}">${AP_DOTS}</button></td>
  </tr>`;
}
function apRows() {
  const body = document.getElementById('apRows'), more = document.getElementById('apMore'), count = document.getElementById('apCount');
  const empty = html => `<tr class="ui-empty-row"><td colspan="8"><div class="ui-empty">${html}</div></td></tr>`;
  if (!_apiLoaded) {
    body.innerHTML = _apiFail ? empty(`${loadErr('the clients', _apiFail)}<button type="button" class="ghost" onclick="loadApi()">Try again</button>`)
      : empty('<div class="loading">Loading…</div>');
    more.innerHTML = ''; count.textContent = ''; return;
  }
  const list = _apiClients.filter(apMatches).sort(AP_SORT[_apSt.sort] || AP_SORT.name), all = _apiClients.length;
  count.textContent = apFiltered() ? `${list.length} of ${all} client${all === 1 ? '' : 's'}` : `${all} client${all === 1 ? '' : 's'}`;
  if (!all) {
    body.innerHTML = empty(`<b>No clients yet</b><span>Add one when another project, app or person needs to use HomeShed. To connect a chat app such as Claude, see Connect a chat app below.</span>
      <button type="button" class="ui-primary" onclick="Api.openAdd()">Add client</button>`);
    more.innerHTML = ''; return;
  }
  if (!list.length) {
    body.innerHTML = empty(`<b>No client matches these filters</b><span>${_apSt.q.trim() ? `Nothing is called or described as “${escapeHtml(_apSt.q.trim())}” with these filters.` : 'Try a wider status or time.'}</span>
      <button type="button" class="ghost" onclick="Api.clear()">Clear filters</button>`);
    more.innerHTML = ''; return;
  }
  const live = {};
  _apiConn.forEach(x => { live[x.caller] = (live[x.caller] || 0) + 1; });
  const shown = list.slice(0, Math.max(25, _apSt.shown));
  UI.rerender(body, shown.map(c => apRow(c, live[c.name] || 0)).join(''));
  const rest = list.length - shown.length;
  more.innerHTML = rest > 0 ? `<button type="button" class="ghost" data-key="ap-more" onclick="Api.more()">Show ${Math.min(25, rest)} more</button>
    <span class="ui-sub">Showing ${shown.length} of ${list.length}</span>` : '';
  if (_apiNew) {
    const row = body.querySelector(`tr[data-name="${CSS.escape(_apiNew)}"]`);
    if (row && !document.getElementById('apToken').classList.contains('show')) row.scrollIntoView({ block: 'nearest' });
    setTimeout(() => { _apiNew = null; }, 3200);
  }
}
// Recent changes (clients.audit_trail: the last 40, newest first). The trail records the action, the client and a few
// details, never a token and not who did it: the owner's routes are the only ones that change clients.
const AP_AUDIT_KIND = a => /^(granted|revoked|access preset|tool view|tool set)/.test(a) ? 'access'
  : /^(created|deleted|suspended|resumed|token|limits|spend cap|memory agent|shared memory reads|control keys)/.test(a) ? 'life'
  : /oauth|waiting|approved|declined|withdrawn|parked|not used|rate limited|failed/.test(a) ? 'approvals' : 'other';
function apAuditDetail(a) {
  const out = [];
  if (a.tool) out.push(`<span style="font-family:var(--mono)">${escapeHtml(a.tool)}</span>`);
  if (a.preset) out.push('preset ' + escapeHtml(a.preset));
  if ('rate_per_min' in a) out.push(a.rate_per_min ? escapeHtml(a.rate_per_min) + '/min' : 'no rate limit');
  if ('expires_days' in a) out.push(a.expires_days ? `expires in ${escapeHtml(a.expires_days)} d` : 'never expires');
  if ('daily_spend_usd' in a) out.push(a.daily_spend_usd == null ? 'no spend cap' : 'cap ' + apMoney(a.daily_spend_usd));
  if ('count' in a) out.push(`${escapeHtml(a.count)} client${a.count === 1 ? '' : 's'}`);
  if (a.tool_view) out.push(a.tool_view === 'front_door' ? 'front door' : 'full list');
  if (a.images) out.push(a.images === 'content' ? 'images as pictures' : 'images inline');
  if ('patterns' in a) out.push(a.patterns ? escapeHtml([].concat(a.patterns).join(', ')) : 'whole grant');
  return out.join(', ');
}
function apAuditRow(a) {
  const known = a.client && _apiClients.some(c => c.name === a.client);
  const who = a.client ? (known ? `<button type="button" class="ui-link" onclick="Api.open('${escAttr(a.client)}')">${escapeHtml(a.client)}</button>`
    : `<span style="font-family:var(--mono)">${escapeHtml(a.client)}</span>`) : a.action === 'suspended all' ? 'every client' : '';
  const out = /failed|not used/.test(a.action) ? '<span class="ui-chip bad">Failed</span>' : /declined/.test(a.action) ? '<span class="ui-chip warn">Declined</span>'
    : /approved/.test(a.action) ? '<span class="ui-chip good">Approved</span>' : '';
  const det = apAuditDetail(a), when = new Date((a.t || 0) * 1000);
  return `<div class="ui-feed-row"><time class="ui-feed-time" datetime="${when.toISOString()}" title="${escAttr(when.toLocaleString())}">${escapeHtml(agoText(when.toISOString()))}</time>
    <div class="ui-feed-main">${escapeHtml(String(a.action || "").replace(/^./, ch => ch.toUpperCase()).replace(/^Oauth/, "OAuth"))}${who ? ': ' + who : ''}${det ? ` <span class="ui-dim">${det}</span>` : ''}</div>${out}</div>`;
}
function apAuditRender() {
  const el = document.getElementById('apAudit');
  if (!_apiLoaded) { if (_apiFail) el.innerHTML = `<div class="empty">${loadErr('the changes', _apiFail)}</div>`; return; }
  const rows = _apiAudit.filter(a => _apSt.audit === 'all' || AP_AUDIT_KIND(a.action || '') === _apSt.audit).map(apAuditRow);
  UI.feed(el, rows, { key: 'ap-audit', limit: 8, empty: _apSt.audit === 'all' ? 'No changes yet.' : 'No changes of this kind in the last 40.' });
}

// The drawer: one client, five tabs. It re-renders with each refresh unless the owner is typing in it.
const AP_TABS = [['overview', 'Overview'], ['access', 'Permissions'], ['limits', 'Limits and spending'], ['memory', 'Memory'], ['life', 'Lifecycle']];
function apDrawer(poll) {
  const d = document.getElementById('apDrawer'), c = _apiClients.find(x => x.name === _apSt.sel);
  if (poll && !d.open) return;
  if (!c) { if (d.open && _apiLoaded) { d.close(); UI.toast(`${_apSt.sel} isn't in the client list any more.`); } return; }
  const a = document.activeElement;
  if (poll && d.contains(a) && a.matches('input:not([type=checkbox]), select, textarea')) return;
  const [tone, label] = clientStatus(c), id = clientIdentity(c);
  document.getElementById('apDrawerTitle').textContent = c.name;
  const live = _apiConn.filter(x => x.caller === c.name).length;
  document.getElementById('apDrawerSub').innerHTML = `<span class="ui-state ${tone}">${label}</span><span class="ui-chip${id.kind === 'oauth' ? '' : ' plain'}">${escapeHtml(id.label)}</span>
    ${live ? `<span class="ui-chip good">Connected now${live > 1 ? ` from ${live} machines` : ''}</span>` : ''}${id.purpose ? `<span>${escapeHtml(id.purpose)}</span>` : ''}`;
  if (!AP_TABS.some(([k]) => k === _apSt.tab)) _apSt.tab = 'overview';
  UI.rerender(document.getElementById('apTabs'), AP_TABS.map(([k, l]) => `<button type="button" role="tab" class="ui-tab" id="apTab-${k}" data-key="tab:${k}"
    aria-selected="${k === _apSt.tab}" aria-controls="apDrawerBody" tabindex="${k === _apSt.tab ? 0 : -1}" onclick="Api.tab('${k}')">${l}</button>`).join(''));
  const body = document.getElementById('apDrawerBody');
  body.setAttribute('aria-labelledby', 'apTab-' + _apSt.tab);
  UI.rerender(body, ({ overview: apTabOverview, access: apTabAccess, limits: apTabLimits, memory: apTabMemory, life: apTabLife })[_apSt.tab](c), body);
}
function apTabOverview(c) {
  const u = c.usage || {}, id = clientIdentity(c), g = clientGranted(c), [, acc] = clientAccess(c), n = escAttr(c.name);
  const top = Object.entries(u.tools || {}).sort((a, b) => b[1] - a[1]).slice(0, 5);
  const changes = _apiAudit.filter(a => a.client === c.name).slice(0, 5);
  const live = _apiConn.filter(x => x.caller === c.name).length;
  const sp = c.spend_today || {};
  return `<section class="ui-block"><div class="ui-nums">
      <div><b>${(u.calls || 0).toLocaleString()}</b><span>calls</span></div>
      <div><b class="${u.errors ? 'ui-warn' : ''}">${(u.errors || 0).toLocaleString()}</b><span>errors</span></div>
      <div><b class="${u.refused ? 'ui-bad' : ''}">${(u.refused || 0).toLocaleString()}</b><span>refused</span></div>
      <div><b>${apMoney(sp.total)}</b><span>spent today</span></div></div></section>
    <section class="ui-block"><h3>Identity</h3><dl class="ui-dl">
      <dt>Purpose</dt><dd>${escapeHtml(id.purpose) || '<span class="ui-dim">None given</span>'}</dd>
      <dt>Connects with</dt><dd>${id.kind === 'oauth' ? `OAuth sign-in from <b>${escapeHtml(id.label.slice(7))}</b> (no pasted token)` : escapeHtml(id.label)}</dd>
      <dt>Connection</dt><dd>${live ? `Connected now${live > 1 ? ` from ${live} machines` : ''}` : 'Not connected in the last 15 minutes'}</dd>
      <dt>Created</dt><dd>${UI.day(c.created) || '<span class="ui-dim">Not recorded</span>'}</dd>
      <dt>Last used</dt><dd>${c.last_used ? `${escapeHtml(UI.ago(c.last_used))} <span class="ui-dim">(${escapeHtml(new Date(c.last_used * 1000).toLocaleString())})</span>` : 'Never'}</dd>
      <dt>Expires</dt><dd>${c.expires ? (c.expired ? '<span class="ui-bad">Expired</span> ' : '') + UI.day(c.expires) : 'Never'}</dd>
    </dl></section>
    <section class="ui-block"><h3>Access</h3>
      <p>${g == null ? 'Tool count unavailable' : `<b>${g}</b> of ${_apiTools.length} tools`}: ${acc}. Sees ${c.tool_view === 'front_door' ? 'the front door' : 'the full tool list'};
        images ${c.images === 'content' ? 'as pictures' : 'inline'}. ${c.rate_per_min ? `Up to ${c.rate_per_min.toLocaleString()} calls a minute.` : 'No rate limit.'}</p>
      <div class="ui-btnrow"><button type="button" class="ghost" data-key="ov:access" onclick="Api.tab('access')">Change permissions</button>
        <button type="button" class="ghost" data-key="ov:limits" onclick="Api.tab('limits')">Limits and spending</button></div></section>
    ${top.length ? `<section class="ui-block"><h3>Most used tools</h3>${top.map(([k, v]) => `<div class="grant-row" style="cursor:default"><span class="tid">${escapeHtml(k)}</span><span class="ui-dim" style="margin-left:auto">${v.toLocaleString()}</span></div>`).join('')}</section>` : ''}
    <section class="ui-block"><h3>Recent changes</h3>${changes.length ? changes.map(apAuditRow).join('') : `<div class="empty">None in the last 40 changes.</div>`}</section>`;
}
function apTabAccess(c) {
  const g = clientGranted(c), [kind, acc] = clientAccess(c), n = escAttr(c.name);
  const preset = (p, l) => `<button type="button" class="ghost${kind === p ? ' active' : ''}" data-key="preset:${p}" aria-pressed="${kind === p}" onclick="clientPreset('${n}', '${p}', this)">${l}</button>`;
  return `<section class="ui-block"><h3>Access</h3>
      <p>${g == null ? 'The tool list didn\'t load, so the count isn\'t shown.' : `<b>${g}</b> of ${_apiTools.length} tools granted`}. ${kind === 'custom' ? 'Chosen one by one.' : acc + '.'}</p>
      <div class="ui-btnrow">${preset('read', 'Read-only tools')}${preset('all', 'Everything')}${preset('none', 'No tools')}</div>
      <div class="hint" style="margin-top:6px">A preset replaces its current tool choices.</div></section>
    <section class="ui-block"><h3>How it sees the tools</h3>
      <div class="client-view">
        <div><div class="vlabel">Tool view</div>${viewSeg(c, 'tool_view', [['full', 'Full list'], ['front_door', 'Front door']])}
          <span class="hint">Front door: the app sees 5 tools that find and run all the others, instead of the whole list. Saves tokens, which helps small models most.</span></div>
        <div><div class="vlabel">Images</div>${viewSeg(c, 'images', [['inline', 'Inline'], ['content', 'As pictures']])}
          <span class="hint">Inline sends a picture as a long block of text. As pictures sends it as a real image, which costs models far less.</span></div>
      </div>
      ${c.tool_view === 'front_door' ? toolsetPicker(c, _apiPro) : ''}</section>
    <section class="ui-block"><h3>Choose tools</h3>
      ${_apiToolsOk ? `<div class="ui-inline" style="margin-bottom:8px"><input type="search" id="apGrantQ" data-key="grant-q" placeholder="Find a tool" aria-label="Find a tool"
          value="${escAttr(_apSt.grantQ)}" oninput="Api.grantFilter()" style="flex:1 1 200px;width:auto">
        <label class="ui-check"><input type="checkbox" id="apGrantOnly" data-key="grant-only" ${_apSt.grantOnly ? 'checked' : ''} onchange="Api.grantFilter()"> Granted only</label></div>
      <div id="apGrantList">${apGrantList(c)}</div>` : '<div class="empty">The tool list didn\'t load. Press Refresh to try again.</div>'}</section>`;
}
function apGrantList(c) {
  const q = _apSt.grantQ.trim().toLowerCase(), groups = {};
  _apiTools.filter(x => (!q || x.id.includes(q) || (x.category || '').toLowerCase().includes(q)) && (!_apSt.grantOnly || (x.clients || {})[c.name]))
    .forEach(x => (groups[x.category] = groups[x.category] || []).push(x));
  const n = escAttr(c.name), cats = Object.keys(groups).sort();
  if (!cats.length) return `<div class="empty">${q ? 'No tool matches that.' : 'No tools granted yet.'}</div>`;
  return cats.map(g => {
    const all = _apiTools.filter(x => x.category === g), on = all.filter(x => (x.clients || {})[c.name]).length;
    return `<details class="ui-group" data-cat="${escAttr(g)}"${q || _apOpenCats.has(g) ? ' open' : ''}><summary data-key="cat:${escAttr(g)}">${escapeHtml(g)}<span class="ui-dim">${on} of ${all.length}</span></summary>
      ${groups[g].map(x => `<label class="grant-row"><input type="checkbox" data-key="g:${escAttr(x.id)}" ${(x.clients || {})[c.name] ? 'checked' : ''} onchange="grantTool('${n}', '${escAttr(x.id)}', this)">
        <span class="tid">${escapeHtml(x.id)}</span>${x.enabled ? '' : '<span class="off-all">switched off for everyone</span>'}<span class="scope ${x.risk === 'write' ? 'write' : 'read'}">${escapeHtml(x.scope)}</span></label>`).join('')}</details>`;
  }).join('');
}
function apTabLimits(c) {
  const sp = c.spend_today || {}, cap = c.daily_spend_usd, total = sp.total || 0;
  const warn = cap != null && total >= cap ? `<div class="ui-note bad">At its cap: its paid calls are refused until tomorrow. Free and local tools keep working.</div>`
    : cap != null && cap > 0 && total >= cap * 0.8 ? `<div class="ui-note warn">Close to its cap: ${apMoney(total)} of ${apMoney(cap)} spent today.</div>` : '';
  return `<section class="ui-block"><h3>Rate limit</h3>
      <form class="ui-inline" onsubmit="Api.saveRate(event)" novalidate><label for="apRateEdit">Calls per minute</label>
        <input id="apRateEdit" data-key="rate" type="number" min="1" max="100000" inputmode="numeric" placeholder="No limit" value="${c.rate_per_min || ''}" ${c.expired ? 'disabled' : ''} aria-describedby="apRateEditErr">
        <button type="submit" class="ghost" data-key="rate-save" ${c.expired ? 'disabled' : ''}>Save</button></form>
      <div class="ui-err" id="apRateEditErr"></div>
      <div class="hint" style="margin-top:6px">Blank for no limit. Over the limit, its calls are refused until the minute is up.</div>
      ${c.expired ? '<div class="ui-note warn">This client has expired. Give it a new expiry under Lifecycle first: HomeShed saves the rate and the expiry together, and an expiry that has passed can\'t be kept.</div>' : ''}</section>
    <section class="ui-block"><h3>Daily spending cap</h3>
      <dl class="ui-dl" style="margin-bottom:10px"><dt>Spent today</dt><dd><b>${apMoney(total)}</b> <span class="ui-dim">(images ${apMoney(sp.image)}, AI models ${apMoney(sp.llm)})</span></dd>
        <dt>Cap</dt><dd>${cap != null ? apMoney(cap) + ' a day' : 'None of its own'}</dd></dl>
      ${warn}
      <form class="ui-inline" onsubmit="Api.saveSpend(event)" novalidate><label for="apSpendEdit">Dollars a day</label>
        <input id="apSpendEdit" data-key="spend" inputmode="decimal" placeholder="No cap" value="${cap != null ? Number(cap).toFixed(2) : ''}" aria-describedby="apSpendEditErr">
        <button type="submit" class="ghost" data-key="spend-save">Save</button></form>
      <div class="ui-err" id="apSpendEditErr"></div>
      <div class="hint" style="margin-top:6px">Paid images and AI model calls. At the cap its paid calls are refused for the rest of the day; free and local tools keep working. The providers' own daily caps still apply. HomeShed keeps each day's spend; it doesn't keep a running total.</div></section>`;
}
function apTabMemory(c) {
  const on = c.memory_shared_read !== false, n = escAttr(c.name), waiting = _apiShared.filter(i => i.client === c.name).length;
  return `<section class="ui-block"><h3>Its own memory</h3>
      <p>It saves and recalls in a memory of its own, kept apart from your other apps and projects.${c.memory_agent ? ` Memory name: <span style="font-family:var(--mono)">${escapeHtml(c.memory_agent)}</span>.` : ''}</p></section>
    <section class="ui-block"><h3>Reading the shared memory</h3>
      <p>${on ? 'It can read the shared memory: what you and your other apps saved for everyone.' : '<b>It can\'t read the shared memory.</b> It still has its own.'}</p>
      <button type="button" class="ghost${on ? ' danger' : ''}" data-key="shared" onclick="clientSharedRead('${n}', ${!on}, this)">${on ? 'Stop shared memory reads' : 'Allow shared memory reads'}</button></section>
    <section class="ui-block"><h3>Adding to the shared memory</h3>
      <p>Whatever it asks to add waits for your approval${waiting ? `: <b>${waiting}</b> from this app waiting now, at the top of this page.` : '. Nothing from this app is waiting.'}</p></section>`;
}
function apTabLife(c) {
  const n = escAttr(c.name), id = clientIdentity(c), left = c.expires ? Math.ceil((c.expires - Date.now() / 1000) / AP_DAY) : null;
  return `<section class="ui-block"><h3>Status</h3>
      <p>${c.suspended ? '<b>Suspended.</b> Every call it makes is refused until you resume it. Its tools, limits and token are kept.'
        : c.expired ? '<b>Expired.</b> Its calls are refused. Give it a new expiry below to let it back in.'
        : c.last_used ? '<b>Active.</b> Its token works.' : '<b>Never used.</b> Its token works but hasn\'t been used yet.'}</p>
      <button type="button" class="ghost${c.suspended ? '' : ' danger'}" data-key="suspend" onclick="clientSuspend('${n}', ${!c.suspended}, this)">${c.suspended ? 'Resume' : 'Suspend'}</button></section>
    <section class="ui-block"><h3>Expiry</h3>
      <p>${!c.expires ? 'Never expires.' : c.expired ? `Expired on ${UI.day(c.expires)}.` : `Expires on ${UI.day(c.expires)}, in ${left} day${left === 1 ? '' : 's'}.`}</p>
      <form class="ui-inline" onsubmit="Api.saveExpiry(event)" novalidate><label for="apExpEdit">Expire</label>
        <select id="apExpEdit" data-key="exp" onchange="document.getElementById('apExpDays').hidden = this.value !== 'custom'">
          <option value="">Never</option><option value="1">In 1 day</option><option value="7">In 7 days</option><option value="30" selected>In 30 days</option>
          <option value="90">In 90 days</option><option value="365">In a year</option><option value="custom">In a number of days…</option></select>
        <input id="apExpDays" data-key="exp-days" type="number" min="1" max="3650" placeholder="Days" aria-label="Days from now" hidden style="width:100px">
        <button type="submit" class="ghost" data-key="exp-save">Save expiry</button></form>
      <div class="ui-err" id="apExpErr"></div>
      <div class="hint" style="margin-top:6px">Counted in whole days from now. The rate limit stays as it is.</div></section>
    <section class="ui-block"><h3>Token</h3>
      <p>The current token was made ${UI.day(c.token_created || c.created) || 'at an unrecorded time'}. Tokens are stored hashed: HomeShed can't show one again, only make a new one.</p>
      ${id.kind === 'oauth' ? '<p class="hint">This app signs in with OAuth and doesn\'t use a pasted token. To end its access, suspend or delete it: its sign-in stops working at once.</p>' : ''}
      <button type="button" class="ghost" data-key="rotate" onclick="rotateClient('${n}')">Make a new token</button></section>
    <section class="ui-block danger-zone"><h3>Delete</h3>
      <p>Ends its access at once and removes its tools and limits. You can add a client with the same name later; it gets a new token.</p>
      <button type="button" class="ghost danger" data-key="delete" onclick="deleteClient('${n}')">Delete client</button></section>`;
}

// Page actions (the inline handlers call these).
const Api = {
  save() { UI.save('cp-api-page', _apSt); },
  set(k, v) { _apSt[k] = v; _apSt.shown = 25; Api.save(); apMetrics(); apToolbar(); apRows(); },
  clear() { Object.assign(_apSt, { q: '', status: 'all', seen: 'any', access: 'any', shown: 25 }); Api.save(); apRender(); document.getElementById('apSearch').focus(); },
  metric(m) {
    const was = document.querySelector(`#apiView .ui-metric[data-metric="${m}"]`).getAttribute('aria-pressed') === 'true';
    Object.assign(_apSt, { q: '', access: 'any', status: 'all', seen: 'any', shown: 25 });
    if (!was && m === 'week') _apSt.seen = 'week';
    if (!was && m === 'blocked') _apSt.status = 'blocked';
    Api.save(); apRender();
  },
  more() { _apSt.shown = Math.max(25, _apSt.shown) + 25; Api.save(); apRows(); UI.refocus('ap-more'); },
  auditKind(v) { _apSt.audit = v; Api.save(); apAuditRender(); },
  open(name, tab) {
    UI.closeMenu(false);
    _apSt.sel = name; if (tab) _apSt.tab = tab; Api.save();
    const d = document.getElementById('apDrawer');
    apDrawer(false);
    if (!d.open) d.showModal();
    document.getElementById('apDrawerBody').scrollTop = 0;
    (tab ? document.getElementById('apTab-' + tab) : document.getElementById('apDrawerTitle')).focus();
    document.querySelectorAll('#apRows tr.is-sel').forEach(r => r.classList.remove('is-sel'));
    document.querySelector(`#apRows tr[data-name="${CSS.escape(name)}"]`)?.classList.add('is-sel');
  },
  close() { document.getElementById('apDrawer').close(); },
  tab(k) {
    _apSt.tab = k; Api.save(); apDrawer(false);
    document.getElementById('apDrawerBody').scrollTop = 0;
    document.getElementById('apTab-' + k).focus();
  },
  grantFilter() {
    _apSt.grantQ = document.getElementById('apGrantQ').value; _apSt.grantOnly = document.getElementById('apGrantOnly').checked; Api.save();
    const c = _apiClients.find(x => x.name === _apSt.sel);
    if (c) document.getElementById('apGrantList').innerHTML = apGrantList(c);
  },
  menu(btn, name) {
    const c = _apiClients.find(x => x.name === name);
    if (!c) return;
    const shared = c.memory_shared_read !== false;
    UI.menu(btn, [
      { label: 'Open details', run: () => Api.open(name, 'overview') },
      { label: 'Change permissions', run: () => Api.open(name, 'access') },
      { label: 'Limits and spending', run: () => Api.open(name, 'limits') },
      '-',
      { label: c.suspended ? 'Resume' : 'Suspend', danger: !c.suspended, run: () => clientSuspend(name, !c.suspended) },
      { label: shared ? 'Stop shared memory reads' : 'Allow shared memory reads', run: () => clientSharedRead(name, !shared) },
      { label: 'Make a new token', run: () => rotateClient(name) },
      '-',
      { label: 'Delete client', danger: true, run: () => deleteClient(name) },
    ]);
  },
  openAdd() {
    const d = document.getElementById('apAdd');
    ['apNameErr', 'apRateErr', 'apMsg'].forEach(id => { document.getElementById(id).textContent = ''; });
    ['apName', 'apRate'].forEach(id => document.getElementById(id).removeAttribute('aria-invalid'));
    d.showModal(); document.getElementById('apName').focus();
  },
  sel() { return _apiClients.find(x => x.name === _apSt.sel); },
  async saveRate(e) {
    e.preventDefault();
    const c = Api.sel(), inp = document.getElementById('apRateEdit'), err = document.getElementById('apRateEditErr'), raw = inp.value.trim();
    if (!c) return;
    const rate = raw === '' ? null : Number(raw);
    if (rate !== null && !(Number.isInteger(rate) && rate >= 1 && rate <= 100000)) {
      err.textContent = 'Enter a whole number from 1 to 100000, or leave it blank for no limit.'; inp.setAttribute('aria-invalid', 'true'); inp.focus(); return;
    }
    err.textContent = ''; inp.removeAttribute('aria-invalid');
    // The server sets the rate and expiry together, in whole days from now: keep the expiry to the next whole day.
    const keep = c.expires ? Math.max(1, Math.ceil((c.expires - Date.now() / 1000) / AP_DAY)) : null;
    await UI.busy(e.submitter || e.target.querySelector('button'), async () => {
      try { await clientLimits(c.name, rate, keep); UI.toast(`Saved: ${c.name} ${rate ? `may make ${rate.toLocaleString()} calls a minute` : 'has no rate limit'}.`); }
      catch (x) { err.textContent = x.message; }
    });
  },
  async saveExpiry(e) {
    e.preventDefault();
    const c = Api.sel(), sel = document.getElementById('apExpEdit'), daysIn = document.getElementById('apExpDays'), err = document.getElementById('apExpErr');
    if (!c) return;
    let days = sel.value === 'custom' ? Number(daysIn.value) : sel.value === '' ? null : Number(sel.value);
    if (days !== null && !(Number.isInteger(days) && days >= 1 && days <= 3650)) {
      err.textContent = 'Enter a whole number of days from 1 to 3650.'; daysIn.setAttribute('aria-invalid', 'true'); daysIn.focus(); return;
    }
    err.textContent = ''; daysIn.removeAttribute('aria-invalid');
    await UI.busy(e.submitter || e.target.querySelector('button'), async () => {
      try { await clientLimits(c.name, c.rate_per_min || null, days); UI.toast(days ? `Saved: ${c.name} expires in ${days} day${days === 1 ? '' : 's'}.` : `Saved: ${c.name} never expires.`); }
      catch (x) { err.textContent = x.message; }
    });
  },
  async saveSpend(e) {
    e.preventDefault();
    const c = Api.sel(), inp = document.getElementById('apSpendEdit'), err = document.getElementById('apSpendEditErr'), v = inp.value.trim();
    if (!c) return;
    const usd = v === '' ? null : Number(v.replace(/[$£,\s]/g, ''));
    if (usd !== null && !(usd >= 0 && usd <= 10000)) {
      err.textContent = 'Enter dollars from 0 to 10000, for example 2.50, or leave it blank for no cap.'; inp.setAttribute('aria-invalid', 'true'); inp.focus(); return;
    }
    err.textContent = ''; inp.removeAttribute('aria-invalid');
    await UI.busy(e.submitter || e.target.querySelector('button'), async () => {
      try { await clientSpendCap(c.name, usd); UI.toast(usd === null ? `Saved: ${c.name} has no spending cap of its own.` : `Saved: ${c.name} may spend ${apMoney(usd)} a day.`); }
      catch (x) { err.textContent = x.message; }
    });
  },
};
// Wiring that only has to happen once: toolbar inputs, row clicks, the drawer's close and its tab keys.
(function apWire() {
  const on = (id, ev, fn) => document.getElementById(id).addEventListener(ev, fn);
  on('apSearch', 'input', e => Api.set('q', e.target.value));
  on('apStatus', 'change', e => Api.set('status', e.target.value));
  on('apSeen', 'change', e => Api.set('seen', e.target.value));
  on('apAccess', 'change', e => Api.set('access', e.target.value));
  on('apSort', 'change', e => Api.set('sort', e.target.value));
  UI.sticky(document.getElementById('apList'));
  on('apRows', 'click', e => {
    const row = e.target.closest('tr[data-name]');
    if (!row) return;
    const kebab = e.target.closest('.ui-kebab');
    if (kebab) { e.stopPropagation(); Api.menu(kebab, row.dataset.name); return; }
    if (e.target.closest('a, input, select')) return;
    Api.open(row.dataset.name);
  });
  const d = document.getElementById('apDrawer');
  d.addEventListener('close', () => {
    const name = _apSt.sel;
    _apSt.sel = null; Api.save();
    document.querySelectorAll('#apRows tr.is-sel').forEach(r => r.classList.remove('is-sel'));
    setTimeout(() => { if (!UI.refocus('open:' + name)) document.getElementById('apSearch').focus({ preventScroll: true }); }, 0);
  });
  d.addEventListener('click', e => { if (e.target === d) d.close(); });  // a click on the dimmed page closes it
  d.addEventListener('toggle', e => {
    const g = e.target.closest && e.target.closest('details.ui-group');
    if (g) g.open ? _apOpenCats.add(g.dataset.cat) : _apOpenCats.delete(g.dataset.cat);
  }, true);
  on('apTabs', 'keydown', e => {
    const i = AP_TABS.findIndex(([k]) => k === _apSt.tab), go = j => { e.preventDefault(); Api.tab(AP_TABS[(j + AP_TABS.length) % AP_TABS.length][0]); };
    if (e.key === 'ArrowRight') go(i + 1); else if (e.key === 'ArrowLeft') go(i - 1); else if (e.key === 'Home') go(0); else if (e.key === 'End') go(AP_TABS.length - 1);
  });
  document.getElementById('apAdd').addEventListener('click', e => { if (e.target.id === 'apAdd' && !document.getElementById('apCreate').disabled) e.target.close(); });
  document.getElementById('apAdd').addEventListener('cancel', e => { if (document.getElementById('apCreate').disabled) e.preventDefault(); });
  document.getElementById('apName').addEventListener('input', e => { e.target.removeAttribute('aria-invalid'); document.getElementById('apNameErr').textContent = ''; });
  // The open client comes back after a reload once the list has loaded.
  if (_apSt.sel) _apSt._reopen = _apSt.sel;
  _apSt.sel = null;
})();
// How to connect a chat app with OAuth sign-in (oauth_server.py). Open by default: it's where the owner looks. A tool
// server without sign-in answers 404, which reads the same as "not set up".
async function loadSigninHelp() {
  const body = document.getElementById('apSigninBody'), state = document.getElementById('apSigninState');
  if (!body) return;
  let d;
  try { d = await apiCall('GET', '/api/oauth/pending'); } catch (e) { d = { enabled: false, error: e.message }; }
  const waiting = (d.pending || []).length;
  state.textContent = d.enabled ? (waiting ? `${waiting} sign-in${waiting === 1 ? '' : 's'} waiting at the top of the page` : 'sign-in is on') : 'not set up yet';
  body.innerHTML = d.enabled ? `<ol class="hint" style="margin:8px 0 0 18px;padding:0;display:grid;gap:6px">
      <li>In the chat app, add a <b>custom connector</b> (in Claude: Settings, Connectors, Add custom connector). A connector is how a chat app reaches outside tools.</li>
      <li>For its address, give this server's public web address (the one starting <b>https://</b> that HomeShed was set up with) followed by <b>/mcp</b>, for example <span style="font-family:var(--mono)">https://tools.example.com/mcp</span>.</li>
      <li>When it asks how to sign in, choose <b>Sign in now</b> or <b>OAuth</b>. If it offers a choice of how the app is identified, either works: Claude's <b>published identity</b> (Claude names itself by a web address HomeShed checks) or <b>automatic registration</b> (the app registers itself here). Leave any "client ID" or "secret" boxes empty.</li>
      <li>A page opens showing a 6-digit code. Come back here: the sign-in waits at the top of the Control Panel for 10 minutes. Check the code matches, choose the app's name and access, and press <b>Approve</b>. The page then takes you back to the chat app.</li>
    </ol>
    <div class="hint" style="margin-top:8px">To end a chat app's access later, suspend or delete its app in the list below: its sign-in stops working at once.</div>
    <div id="apTrusted" class="ap-trusted"></div>`
    : `<div class="hint" style="margin-top:8px"><b>Not set up yet.</b> Sign-in needs this server's public https address: a web address starting <b>https://</b> that chat apps on the internet can reach (for example through a tunnel or a reverse proxy: a go-between that passes visitors from the internet safely on to HomeShed). Put that address in the tool server's settings as <span style="font-family:var(--mono)">MCP_PUBLIC_URL</span> and restart the tool server; this card then shows the steps.${d.error ? ` (Couldn't check just now: ${escapeHtml(d.error)}.)` : ''}</div>`;
  if (d.enabled) loadTrustedBrowsers();
}
// Browsers the owner ticked "Trust this app" for when approving (oauth_server.trusted): that app, from that browser, to
// the same return address, signs in again with no code for 90 days. Remove asks first; sign-ins already made are kept.
async function loadTrustedBrowsers() {
  const box = document.getElementById('apTrusted');
  if (!box) return;
  let d;
  try { d = await apiCall('GET', '/api/oauth/trusted'); } catch (e) { box.innerHTML = ''; return; }  // an older tool server
  const rows = d.trusted || [];
  box.innerHTML = `<h4 class="ap-trusted-h">Trusted browsers <span class="hint">${rows.length ? rows.length : 'none yet: tick "Trust this app" when you approve a sign-in'}</span></h4>`
    + (rows.length ? `<table class="ui-table compact"><thead><tr><th>App</th><th>Returns to</th><th>Trusted</th><th>Days left</th><th></th></tr></thead><tbody>${
      rows.map(r => `<tr><td><b>${escapeHtml(r.app_name || r.app)}</b> <span class="hint">as ${escapeHtml(r.app)}</span></td>
        <td>${escapeHtml(r.redirect_host || '')}</td><td>${agoText(new Date(r.at * 1000).toISOString())}</td>
        <td>${Number(r.expires_in_days) || 0}</td>
        <td><button type="button" class="ghost" data-tid="${escAttr(r.id)}" data-app="${escAttr(r.app_name || r.app)}" onclick="removeTrustedBrowser(this)">Remove</button></td></tr>`).join('')
    }</tbody></table>` : '');
}
async function removeTrustedBrowser(btn) {
  const ok = await UI.confirm({ title: `Stop trusting ${btn.dataset.app} on that browser?`, action: 'Remove',
    body: 'Its next sign-in from that browser waits for your OK and the 6-digit code again. Sign-ins it already has keep working.',
    run: () => apiCall('POST', `/api/oauth/trusted/${encodeURIComponent(btn.dataset.tid)}/remove`) });
  if (ok) UI.toast('Removed: it asks again next time.');
  loadTrustedBrowsers();
}
// What the front door saved each app (tools.savings, HomeShed Pro). Without Pro the route answers {pro: false} and the
// card says what Pro shows; it stays open either way (features show where the owner looks).
async function loadFrontDoorSavings() {
  const body = document.getElementById('apSavingsBody');
  if (!body) return;
  let d;
  try { d = await apiCall('GET', '/api/front-door/savings'); } catch (e) { body.innerHTML = `<div class="empty">${loadErr('the savings', e)}</div>`; return; }
  document.getElementById('apSavingsSince').textContent = d.since ? `since ${new Date(d.since * 1000).toLocaleDateString()}` : '';
  if (d.pro === false) { body.innerHTML = `<div class="empty">${escapeHtml(d.note || 'HomeShed Pro shows what the front door saved each app.')}</div>`; return; }
  const apps = d.apps || [];
  body.innerHTML = apps.length ? `<table class="fd-savings"><thead><tr><th>App</th><th>Tokens saved (about)</th><th>Tool lists shortened</th><th>Replies cut down</th></tr></thead>
    <tbody>${apps.map(a => `<tr><td>${escapeHtml(a.app)}</td><td><b>${(a.tokens_saved_about || 0).toLocaleString()}</b></td>
      <td>${(a.listings || 0).toLocaleString()}</td><td>${(a.calls_trimmed || 0).toLocaleString()}</td></tr>`).join('')}</tbody>
    <tfoot><tr><td><b>All apps</b></td><td><b>${(d.total_tokens_saved_about || 0).toLocaleString()}</b></td><td></td><td></td></tr></tfoot></table>`
    : '<div class="empty">Nothing saved yet. Set an app\'s Tool view to Front door and the savings show here once it uses it.</div>';
}
// Apps' writes to the shared memory wait here for the owner (the owner's decision, 2026-10-01). The text is the app's, so
// it's escaped; the card hides when nothing waits or the tool server can't say.
async function loadSharedWaiting() {
  const box = document.getElementById('apShared');
  if (!box) return;
  let d;
  try { d = await apiCall('GET', '/api/shared-memory/waiting'); } catch (e) { box.hidden = true; return; }
  const items = d.items || [];
  _apiShared = items;
  box.hidden = !items.length;
  const why = document.getElementById('apSharedWhy');
  // One line each: who, what, the start of the text (press it to read it all), then the decision.
  UI.rerender(document.getElementById('apSharedList'), items.map((i, k) => {
    const sid = String(i.session_id || ''), text = String(i.content || ''), id = escAttr(i.id);
    const what = sid === 'facts' || sid.startsWith('facts:') ? `a fact (${sid.split(':')[1] || 'general'})` : `a note in "${sid}"`;
    const open = _apSharedOpen.has(String(i.id));
    return `<div class="ui-strip-row${open ? ' open' : ''}">
      <span class="ui-live-dot" aria-hidden="true"></span>
      <button type="button" class="ui-strip-text" data-key="sh:${id}" aria-expanded="${open}" onclick="apSharedText('${id}')"
        title="${escAttr(agoText(new Date((i.at || 0) * 1000).toISOString()))}"><b>${escapeHtml(i.client || '?')}</b> wants to add ${escapeHtml(what)}:
        <span class="ui-strip-quote">${escapeHtml(text.slice(0, 1200))}${text.length > 1200 ? '…' : ''}</span></button>
      ${k === 0 ? `<button type="button" class="ui-strip-why-btn" data-key="sh-why" aria-expanded="${!why.hidden}" aria-controls="apSharedWhy"
        onclick="const w = document.getElementById('apSharedWhy'); w.hidden = !w.hidden; this.setAttribute('aria-expanded', String(!w.hidden))">Why?</button>` : '<span></span>'}
      <button type="button" class="ui-primary" data-key="sh-ok:${id}" onclick="decideShared('${id}', 'approve', this)">Approve</button>
      <button type="button" class="ghost" data-key="sh-no:${id}" onclick="decideShared('${id}', 'decline', this)">Decline</button>
    </div>`;
  }).join(''));
}
const _apSharedOpen = new Set();
function apSharedText(id) {
  _apSharedOpen.has(id) ? _apSharedOpen.delete(id) : _apSharedOpen.add(id);
  const b = document.querySelector(`#apSharedList [data-key="sh:${CSS.escape(id)}"]`);
  if (b) { b.closest('.ui-strip-row').classList.toggle('open', _apSharedOpen.has(id)); b.setAttribute('aria-expanded', String(_apSharedOpen.has(id))); }
}
async function decideShared(id, action, btn) {
  await UI.busy(btn, async () => {
    try { await apiCall('POST', `/api/shared-memory/waiting/${encodeURIComponent(id)}/${action}`); UI.toast(action === 'approve' ? 'Approved: added to the shared memory.' : 'Declined.'); }
    catch (e) { UI.toast(e.message, 'bad'); }
  });
  loadSharedWaiting();
}
// Add a client (the Add client dialog). Checked here first so a mistake is shown at its field and nothing typed is lost;
// the server checks again. The token comes back once and goes only to the show-once box (showToken).
async function createClient(ev) {
  if (ev) ev.preventDefault();
  const $ = id => document.getElementById(id), nameIn = $('apName'), rateIn = $('apRate');
  const name = nameIn.value.trim().toLowerCase(), rateRaw = rateIn.value.trim();
  nameIn.value = name;
  const nameErr = !/^[a-z][a-z0-9-]{1,31}$/.test(name) ? 'Use 2 to 32 characters: lowercase letters, digits and dashes, starting with a letter.'
    : name === 'owner' ? '“owner” is kept for your own token: pick another name.'
    : _apiClients.some(c => c.name === name) ? `There's already a client called ${name}.` : '';
  const rate = rateRaw === '' ? null : Number(rateRaw);
  const rateErr = rate !== null && !(Number.isInteger(rate) && rate >= 1 && rate <= 100000) ? 'Enter a whole number from 1 to 100000, or leave it blank for no limit.' : '';
  $('apNameErr').textContent = nameErr; $('apRateErr').textContent = rateErr; $('apMsg').textContent = '';
  nameIn.toggleAttribute('aria-invalid', !!nameErr); rateIn.toggleAttribute('aria-invalid', !!rateErr);
  if (nameErr) { nameIn.focus(); return; }
  if (rateErr) { $('apAdv').open = true; rateIn.focus(); return; }
  const body = { name, note: $('apNote').value.trim(), preset: $('apPreset').value, rate_per_min: rate,
    expires_days: parseInt($('apExpires').value, 10) || null };
  await UI.busy($('apCreate'), async () => {
    try {
      const d = await apiCall('POST', '/api/clients', body);
      $('apAdd').close();
      nameIn.value = ''; $('apNote').value = '';
      _apiNew = d.name;
      Object.assign(_apSt, { q: '', status: 'all', seen: 'any', access: 'any' }); Api.save();  // so the new client shows
      showToken(d.name, d.token, 'Created');
      await loadApi();
      UI.toast(`Created ${d.name}. Copy its token now: it's shown once.`);
    } catch (e) { $('apMsg').textContent = e.message; }
  });
}
async function clientAction(name, action) {
  await apiCall('POST', `/api/clients/${encodeURIComponent(name)}/${action}`);
  await loadApi();
}
function apOAuthNote(name) {
  const c = _apiClients.find(x => x.name === name);
  return c && clientIdentity(c).kind === 'oauth' ? ` Any chat app signed in as <b>${escapeHtml(name)}</b> is cut off too.` : '';
}
async function clientSuspend(name, on, btn) {
  if (!on) {
    return UI.busy(btn, async () => { try { await clientAction(name, 'resume'); UI.toast(`Resumed ${name}: its token works again.`); } catch (e) { UI.toast(e.message, 'bad'); } });
  }
  const ok = await UI.confirm({ title: `Suspend ${name}?`, action: 'Suspend',
    body: `Every call <b>${escapeHtml(name)}</b> makes is refused until you resume it.${apOAuthNote(name)} Its tools, limits and token are kept.`,
    run: () => clientAction(name, 'suspend') });
  if (ok) UI.toast(`Suspended ${name}.`);
}
async function clientSharedRead(name, on, btn) {
  if (on) {
    return UI.busy(btn, async () => { try { await clientAction(name, 'shared-read-on'); UI.toast(`${name} can read the shared memory again.`); } catch (e) { UI.toast(e.message, 'bad'); } });
  }
  const ok = await UI.confirm({ title: `Stop ${name} reading the shared memory?`, action: 'Stop shared reads',
    body: `<b>${escapeHtml(name)}</b> keeps its own memory but stops seeing what you and your other apps saved for everyone, until you allow it again.`,
    run: () => clientAction(name, 'shared-read-off') });
  if (ok) UI.toast(`${name} no longer reads the shared memory.`);
}
async function rotateClient(name) {
  const d = await UI.confirm({ title: `Make a new token for ${name}?`, action: 'Make a new token', permanent: true,
    body: `The current token for <b>${escapeHtml(name)}</b> stops working at once, so update wherever it's stored. The new token is shown once, at the top of this page.`,
    run: () => apiCall('POST', `/api/clients/${encodeURIComponent(name)}/rotate`) });
  if (!d) return;
  const dr = document.getElementById('apDrawer');
  if (dr.open) dr.close();
  showToken(name, d.token, 'New token for');
  loadApi();
}
async function deleteClient(name) {
  const ok = await UI.confirm({ title: `Delete ${name}?`, action: 'Delete client', permanent: true,
    body: `<b>${escapeHtml(name)}</b> loses access at once: its token stops working and its tools, limits and settings are removed.${apOAuthNote(name)} You can add a client with the same name later; it gets a new token.`,
    run: () => apiCall('DELETE', `/api/clients/${encodeURIComponent(name)}`) });
  if (!ok) return;
  const dr = document.getElementById('apDrawer');
  if (dr.open && _apSt.sel === name) dr.close();
  UI.toast(`Deleted ${name}.`);
  loadApi();
}
// How the app sees the tools (the tool server's front door): tool_view full | front_door, images inline | content.
// The tool server fills in the defaults, so an old app with neither set shows Full list and Inline.
function viewSeg(c, key, options) {
  return `<div class="seg" role="radiogroup" aria-label="${key === 'tool_view' ? 'Tool view' : 'Images'}: ${escAttr(c.name)}">${options.map(([v, label]) =>
    `<button type="button" role="radio" aria-checked="${c[key] === v}" class="${c[key] === v ? 'on' : ''}" data-key="seg:${key}:${v}"
      onclick="clientView('${escAttr(c.name)}', '${key}', '${v}', this)">${label}</button>`).join('')}</div>`;
}
async function clientView(name, key, value, btn) {
  await UI.busy(btn, async () => {
    try { await apiCall('POST', `/api/clients/${encodeURIComponent(name)}/view`, { [key]: value }); await loadApi(); }
    catch (e) { UI.toast(e.message, 'bad'); }
  });
}
// Tool set (HomeShed Pro): which tool groups the front door shows the app, saved as "<group>.*" patterns; null is its
// whole grant. It only narrows what the app sees; what may run is still the grant. The free HomeShed keeps the set, so
// it saves either way and starts working while Pro is on. Groups are the first part of the granted tools' ids.
function toolsetPicker(c, proOn) {
  const set = c.toolset || [];
  const groups = new Set(_apiTools.filter(x => (x.clients || {})[c.name]).map(x => x.id.split('.')[0]));
  set.filter(p => p.endsWith('.*')).forEach(p => groups.add(p.slice(0, -2)));
  groups.delete('tools');  // the front door's own tools are always shown
  const boxes = [...groups].sort().map(g => `<label><input type="checkbox" data-ts="${escAttr(c.name)}" data-key="ts:${escAttr(g)}" value="${escAttr(g)}.*"
    ${set.includes(g + '.*') ? 'checked' : ''} onchange="clientToolset('${escAttr(c.name)}')">${escapeHtml(g)}</label>`).join('');
  return `<div class="client-toolset"><div class="vlabel">Tool set (Pro): which tools the front door shows this app</div>
    <span class="hint">Tick the groups it should see; untick them all, or press Whole grant, to show every tool it's allowed.${proOn ? '' : ' Needs HomeShed Pro; saved now, used while Pro is on.'}</span>
    <div class="ts-groups"><div class="seg"><button type="button" data-key="ts-whole" class="${set.length ? '' : 'on'}" aria-pressed="${!set.length}" onclick="clientToolset('${escAttr(c.name)}', true)">Whole grant</button></div>
      ${boxes || '<span class="hint">No tools granted yet: choose some below first.</span>'}</div></div>`;
}
async function clientToolset(name, whole) {
  const boxes = [...document.querySelectorAll(`input[data-ts="${CSS.escape(name)}"]`)];
  const shown = new Set(boxes.map(b => b.value));
  const keep = ((_apiClients.find(c => c.name === name) || {}).toolset || []).filter(p => !shown.has(p));  // set elsewhere: kept
  const patterns = whole ? null : [...keep, ...boxes.filter(b => b.checked).map(b => b.value)];
  boxes.forEach(b => { b.disabled = true; });
  try { await apiCall('POST', `/api/clients/${encodeURIComponent(name)}/toolset`, { patterns: patterns && patterns.length ? patterns : null }); }
  catch (e) { UI.toast(e.message, 'bad'); }
  await loadApi();
}
async function clientPreset(name, preset) {
  const c = _apiClients.find(x => x.name === name), custom = c && clientAccess(c)[0] === 'custom';
  const words = { read: 'every read-only tool', all: 'every tool, including write tools that change things', none: 'no tools' };
  const ok = await UI.confirm({ title: `Give ${name} ${preset === 'none' ? 'no tools' : words[preset]}?`, danger: preset !== 'read', permanent: custom,
    action: { read: 'Use read-only tools', all: 'Give it everything', none: 'Remove every tool' }[preset],
    body: `<b>${escapeHtml(name)}</b> gets ${words[preset]}. This replaces its current tool choices${custom ? ': the tools you chose one by one aren\'t kept' : ''}.`,
    run: () => clientAction(name, 'preset-' + preset) });
  if (ok) UI.toast(`Saved: ${name} now has ${words[preset]}.`);
}
// Rate (calls a minute, null = none) and expiry (whole days from now, null = never) are one route on the tool server, so
// both always go together; the drawer keeps whichever one isn't being changed.
async function clientLimits(name, rate, expiresDays) {
  await apiCall('POST', `/api/clients/${encodeURIComponent(name)}/limits`, { rate_per_min: rate || null, expires_days: expiresDays || null });
  await loadApi();
}
// A daily dollar cap on the app's paid images and model calls (2026-10-10: one client per shop-app bot). At the cap its
// paid calls are refused (SPEND_CAP_REACHED); its free and local tools keep working. spend.today shows the numbers.
async function clientSpendCap(name, usd) {
  await apiCall('POST', `/api/clients/${encodeURIComponent(name)}/spend-cap`, { usd });
  await loadApi();
}
async function grantTool(name, tool, box) {
  box.disabled = true;
  try { await apiCall('POST', `/api/clients/${encodeURIComponent(name)}/tools/${encodeURIComponent(tool)}/${box.checked ? 'grant' : 'revoke'}`); }
  catch (e) { box.checked = !box.checked; UI.toast(e.message, 'bad'); }
  box.disabled = false;
  loadApi();
}
async function suspendAllClients() {
  const active = _apiClients.filter(c => !c.suspended).length;
  const d = await UI.confirm({ title: 'Suspend every client?', action: 'Suspend every client',
    body: `Use this if a token may have leaked. ${_apiLoaded ? `<b>${active}</b> client${active === 1 ? '' : 's'} that aren't suspended yet will be` : 'Every client is'} refused on every call, including chat apps that signed in. Their tools and limits are kept; resume them one by one.`,
    run: () => apiCall('POST', '/api/clients/suspend-all') });
  if (!d) return;
  UI.toast(`Suspended ${d.suspended} client${d.suspended === 1 ? '' : 's'}.`);
  loadApi();
}

// ---- what switchTab and the poll do for this page (panel.js's Pages) ----
Pages.on('api', {
  enter: () => loadApi(),
  leave() { const d = document.getElementById('apDrawer'); if (d.open) d.close(); },
  poll(slow) { if (slow) loadApi(); else apTick(); },  // live sessions and counts while the page is open
});
