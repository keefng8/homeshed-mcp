/* The Terminal page (static/pages-servers.js): SSH in the browser (terminal.py), xterm.js fetched on first open.
   The other server pages (Proxmox, Containers, Uptime, Tools, Run) are in pages-ops.js. Loaded the first time the page
   opens (Pages in panel.js). */
// ---- Projects (to-do #38): every folder agents work in, from the helper on the owner's PC. Refreshes each minute
// while open. Paths copy on click, so a file can be handed to an agent; pins are files or folders for agents to read.
// --- Proxmox tab (the owner, 2026-10-09: "Monitoring my proxmox clusters. i need actual disc space, ram usages, and
// whatever else i could use. i need full visuals on my vm's. with alarms."). Everything comes from the tool server's
// proxmox_monitor (/api/proxmox/monitor); the graphs are Proxmox's own (rrddata). Refreshes every 15 s while open.
// The Terminal tab (terminal.py): xterm.js, loaded the first time the tab opens. One WebSocket per open session; a session's id is kept in sessionStorage so a
// reload picks it up again (the server keeps it 5 minutes and redraws the last screen).
const Term = (() => {
  let lib = null, state = null, active = null, shown = false;
  const open = new Map();  // sid -> {term, fit, ws, el, host, name, gone}
  const KEEP = 'termSessions';
  function loadLib() {
    if (lib) return lib;
    lib = new Promise((res, rej) => {
      const css = document.createElement('link');
      css.rel = 'stylesheet'; css.href = '/vendor/xterm/xterm.css';
      document.head.appendChild(css);
      const add = (src, next) => { const s = document.createElement('script'); s.src = src; s.onload = next; s.onerror = rej; document.head.appendChild(s); };
      add('/vendor/xterm/xterm.js', () => add('/vendor/xterm/addon-fit.js', res));
    });
    return lib;
  }
  function kept() { try { return JSON.parse(sessionStorage.getItem(KEEP) || '[]'); } catch (e) { return []; } }
  function keep() {
    try { sessionStorage.setItem(KEEP, JSON.stringify([...open.entries()].filter(([, s]) => !s.gone).map(([sid, s]) => ({ sid, host: s.host })))); } catch (e) { /* private mode */ }
  }
  async function refresh() {
    try { state = await apiCall('GET', '/api/term/state'); } catch (e) { state = { error: e.message }; }
    render();
  }
  function render() {
    const note = document.getElementById('termNote'), lock = document.getElementById('termLock');
    const on = state && state.enabled && !state.locked && !state.error;
    lock.hidden = !(state && state.locked);
    note.innerHTML = !state ? '<div class="loading">Loading…</div>'
      : state.error ? `<div class="set-msg bad">${escapeHtml(state.error)}</div>`
      : !state.enabled ? `<p>The terminal is off. Switch <b>Terminal</b> on in <a href="#settings" onclick="switchTab('settings')">Settings</a> (Control Panel) to use it.</p>` : '';
    for (const id of ['termHosts', 'termSuggested']) document.getElementById(id).innerHTML = '';
    document.querySelectorAll('#terminalView details.term-more').forEach(d => { d.hidden = !on; });
    if (!on) return;
    document.getElementById('termKey').textContent = state.public_key || '';
    document.getElementById('termHosts').innerHTML = (state.hosts || []).map(h => `<div class="term-host" data-id="${escAttr(h.id)}">
      <b title="${escAttr(h.name)}">${escapeHtml(h.name)}</b>
      <span class="hint">${escapeHtml(h.user)}@${escapeHtml(h.host)}${h.port !== 22 ? ':' + h.port : ''}</span>
      <div class="term-host-act"><button type="button" onclick="Term.connect('${escAttr(h.id)}')">Open</button>
        <button type="button" class="ghost" onclick="Term.askClaude('${escAttr(h.id)}')" title="Start Claude Code on this machine">Claude</button>
        <button type="button" class="ghost" onclick="Term.askSetup('${escAttr(h.id)}')">Set up access</button>
        <button type="button" class="ghost" onclick="Term.forget('${escAttr(h.id)}', this)" title="Trust this machine's new identity (after a reinstall)">Forget its old key</button>
        <button type="button" class="ghost" onclick="Term.remove('${escAttr(h.id)}')" aria-label="Remove ${escAttr(h.name)}">Remove</button></div></div>`).join('')
      || '<p class="hint">No machines yet. Add one below, or pick a suggestion.</p>';
    const sug = state.suggested || [];
    document.getElementById('termSuggested').innerHTML = sug.length ? `<div class="hint">Found on your network</div>${sug.map((h, i) =>
      `<div class="term-host"><b>${escapeHtml(h.name)}</b><span class="hint">${escapeHtml(h.user)}@${escapeHtml(h.host)} · ${escapeHtml(h.found)}</span>
      <div class="term-host-act"><button type="button" class="ghost" onclick="Term.addSuggested(${i})">Add it</button></div></div>`).join('')}` : '';
  }
  async function addHost(body) {
    const msg = document.getElementById('termAddMsg');
    body = body || { name: v('termAddName'), host: v('termAddHost'), user: v('termAddUser'), port: v('termAddPort') || 22 };
    try { await apiCall('POST', '/api/term/hosts', body); msg.textContent = ''; ['termAddName', 'termAddHost', 'termAddUser', 'termAddPort'].forEach(id => { document.getElementById(id).value = ''; }); }
    catch (e) { msg.className = 'set-msg bad'; msg.textContent = e.message; return; }
    refresh();
  }
  function v(id) { return document.getElementById(id).value.trim(); }
  function askSetup(id) {
    const row = document.querySelector(`.term-host[data-id="${CSS.escape(id)}"]`);
    if (!row || row.querySelector('form')) return;
    const f = document.createElement('form');
    f.innerHTML = `<input type="password" placeholder="That machine's password" aria-label="That machine's password" autocomplete="off">
      <button type="submit">Add key</button>`;
    f.onsubmit = async e => {
      e.preventDefault();
      const pw = f.querySelector('input'), btn = f.querySelector('button');
      btn.disabled = true; btn.textContent = 'Adding…';
      try { await apiCall('POST', `/api/term/hosts/${encodeURIComponent(id)}/setup`, { password: pw.value }); f.outerHTML = '<span class="hint">Done: it signs in with the key now.</span>'; }
      catch (err) { btn.disabled = false; btn.textContent = 'Add key'; f.insertAdjacentHTML('afterend', `<span class="set-msg bad">${escapeHtml(err.message)}</span>`); }
      pw.value = '';  // used once, never kept
    };
    row.appendChild(f);
    f.querySelector('input').focus();
  }
  function askClaude(id) {
    const row = document.querySelector(`.term-host[data-id="${CSS.escape(id)}"]`);
    if (!row || row.querySelector('form')) return;
    const h = (state.hosts || []).find(x => x.id === id) || {};
    const f = document.createElement('form');
    f.innerHTML = `<input placeholder="Folder (optional), e.g. /home/me/project or C:\\Code\\project" aria-label="Folder to start Claude Code in" maxlength="200">
      <button type="submit">Start Claude</button>`;
    f.querySelector('input').value = h.claude_dir || '';
    f.onsubmit = e => { e.preventDefault(); const dir = f.querySelector('input').value.trim(); f.remove(); connect(id, null, { run: 'claude', dir }); refresh(); };
    row.appendChild(f);
    f.querySelector('input').focus();
  }
  async function connect(hostId, sid, extra) {
    await loadLib();
    const h0 = (state && state.hosts || []).find(x => x.id === hostId) || { name: hostId };
    const h = { ...h0, name: (extra && extra.run === 'claude' ? 'Claude · ' : '') + h0.name };
    const el = document.createElement('div');
    el.className = 'term-screen';
    document.getElementById('termScreens').appendChild(el);
    document.querySelector('#termScreens .term-empty')?.remove();
    const term = new Terminal({ cursorBlink: true, fontSize: 14, scrollback: 5000, allowProposedApi: false,
      fontFamily: '"JetBrains Mono", "Cascadia Mono", Consolas, monospace',
      theme: { background: '#0b0f17', foreground: '#e2e8f0', cursor: '#64d2ff', selectionBackground: '#334155' } });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(el);
    const tmpId = sid || 'new-' + Math.random().toString(36).slice(2);
    const s = { term, fit, el, host: hostId, name: h.name, gone: false, ws: null };
    open.set(tmpId, s);
    select(tmpId);
    fit.fit();
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const qs = new URLSearchParams({ host: hostId, cols: term.cols, rows: term.rows, ...(sid ? { sid } : extra || {}) });
    const ws = new WebSocket(`${proto}://${location.host}/api/term/ws?${qs}`);
    ws.binaryType = 'arraybuffer';
    s.ws = ws;
    let id = tmpId;
    ws.onmessage = ev => {
      if (typeof ev.data !== 'string') { term.write(new Uint8Array(ev.data)); return; }
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.t === 'ready' && msg.name) s.name = msg.name;
      if (msg.t === 'ready' && msg.sid !== id) { open.delete(id); id = msg.sid; open.set(id, s); if (active === tmpId) active = id; keep(); tabs(); }
      if (msg.t === 'error' || msg.t === 'exit') { term.write(`\r\n\x1b[33m${msg.why}\x1b[0m\r\n`); s.gone = true; keep(); tabs(); }
    };
    ws.onclose = ev => {
      if (s.gone) return;
      s.gone = true; keep(); tabs();
      term.write(ev.code === 4403 ? `\r\n\x1b[33m${ev.reason === 'unlock' ? 'Enter your password on the left first.' : ev.reason}\x1b[0m\r\n`
        : '\r\n\x1b[33mDisconnected. The session is kept 5 minutes: reopen the machine to pick it up.\x1b[0m\r\n');
      if (ev.reason === 'unlock') refresh();
    };
    const enc = new TextEncoder();
    const send = text => { if (ws.readyState === 1) ws.send(enc.encode(text)); };
    term.onData(send);
    term.onResize(({ cols, rows }) => { if (ws.readyState === 1) ws.send(JSON.stringify({ t: 'resize', c: cols, r: rows })); });
    new ResizeObserver(() => { if (el.classList.contains('on')) { try { fit.fit(); } catch (e) { /* hidden */ } } }).observe(el);
    // Copy and paste like Windows Terminal: Ctrl+C copies a selection (else it's the interrupt), Ctrl+V and a right-click
    // paste, Ctrl+Shift+C/V always copy/paste. Several lines ask first.
    const paste = async () => {
      let text = '';
      try { text = await navigator.clipboard.readText(); } catch (e) { return; }
      const lines = text.split(/\r?\n/).filter(Boolean).length;
      if (lines > 1 && !confirm(`Paste ${lines} lines into ${s.name}?`)) return;
      term.paste(text);
    };
    const copy = () => { const t = term.getSelection(); if (t) { navigator.clipboard.writeText(t).catch(() => {}); term.clearSelection(); } };
    term.attachCustomKeyEventHandler(e => {
      if (e.type !== 'keydown' || !e.ctrlKey) return true;
      const k = e.key.toLowerCase();
      if (k === 'c' && (e.shiftKey || term.hasSelection())) { copy(); return false; }
      if (k === 'v') { e.preventDefault(); paste(); return false; }
      return true;
    });
    el.addEventListener('contextmenu', e => { e.preventDefault(); if (term.hasSelection()) copy(); else paste(); });
    term.focus();
  }
  function select(id) {
    active = id;
    for (const [sid, s] of open) s.el.classList.toggle('on', sid === id);
    const s = open.get(id);
    if (s) { try { s.fit.fit(); } catch (e) { /* not drawn yet */ } s.term.focus(); }
    tabs();
  }
  function tabs() {
    document.getElementById('termTabs').innerHTML = [...open.entries()].map(([sid, s]) =>
      `<button type="button" role="tab" class="term-tab${sid === active ? ' on' : ''}${s.gone ? ' gone' : ''}" onclick="Term.select('${escAttr(sid)}')">
        <i></i>${escapeHtml(s.name)}<span class="x" onclick="event.stopPropagation(); Term.close('${escAttr(sid)}')" aria-label="Close">×</span></button>`).join('');
  }
  async function close(sid) {
    const s = open.get(sid);
    if (!s) return;
    if (!s.gone && /^[a-f0-9]{16}$/.test(sid)) { try { await apiCall('POST', `/api/term/sessions/${sid}/close`); } catch (e) { /* gone already */ } }
    try { s.ws && s.ws.close(); } catch (e) { /* closed */ }
    s.term.dispose(); s.el.remove(); open.delete(sid); keep();
    if (active === sid) { const next = [...open.keys()].pop(); if (next) select(next); else { active = null; tabs(); document.getElementById('termScreens').innerHTML = '<div class="empty term-empty">Pick a machine on the left to open a terminal.</div>'; } }
    else tabs();
  }
  return {
    async show() {
      shown = true;
      await refresh();
      if (state && state.enabled && !state.locked && !open.size) {  // after a reload: pick up the sessions still open
        const live = new Set((state.sessions || []).map(s => s.id));
        for (const k of kept()) if (live.has(k.sid)) await connect(k.host, k.sid);
      }
      const s = open.get(active); if (s) { try { s.fit.fit(); } catch (e) { /* hidden */ } }
    },
    hide() { shown = false; },
    connect: id => connect(id),
    select, close, addHost: () => addHost(),
    addSuggested(i) { const h = (state.suggested || [])[i]; if (h) addHost(h); },
    askSetup, askClaude,
    async remove(id) { if (!confirm('Remove this machine from the list? (Nothing changes on the machine itself.)')) return; await apiCall('DELETE', `/api/term/hosts/${encodeURIComponent(id)}`); refresh(); },
    async forget(id, btn) { await apiCall('POST', `/api/term/hosts/${encodeURIComponent(id)}/forget-key`); btn.textContent = 'Forgotten: it is trusted again on the next connection'; },
    async unlock() {
      const pw = document.getElementById('termLockPw');
      let err = '';
      try { await apiCall('POST', '/api/term/unlock', { password: pw.value }); } catch (e) { err = e.message; }
      pw.value = '';
      await refresh();
      if (err) document.getElementById('termNote').innerHTML = `<div class="set-msg bad">${escapeHtml(err)}</div>`;
    },
    copyKey(btn) { navigator.clipboard.writeText(document.getElementById('termKey').textContent).then(() => { btn.textContent = 'Copied'; }); },
  };
})();

// ---- what switchTab does for this page (panel.js's Pages) ----
Pages.on('terminal', { enter: () => Term.show(), leave: () => Term.hide() });
