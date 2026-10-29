/* The Overview (static/pages-overview.js): the live core, the status hero and its numbers, the shared live data the
   poll keeps current (health, tools, memory, local AI, recent calls, tokens), past sessions, Claude context, clients,
   GitHub releases and the summary screen. Loaded with the page (it is the landing page); the cockpit (cockpit.js) and
   the lower half (pages-ovlower.js) draw what these loaders hand them. */
// ---- Live core: the Overview's one animated element, driven only by real data.
//   centre colour  = overall health (green / amber / red)
//   orbiting dots  = the AI models (solid = online, hollow red = offline, halo = busy right now)
//   packets        = real tool calls from the activity feed flying into the centre
//                    (accent = read, amber = write); each one lands with a ripple
// Bursts of calls speed the orbit up; it settles back to a slow idle breath. Runs only while
// Overview is showing (and the browser tab is visible); with reduced motion it draws still frames.
const _calm = window.HSTelemetry && window.HSTelemetry.Calm;  // telemetry.js: idle, endless decoration holds still
const Core = (() => {
  const cv = document.getElementById('coreCanvas');
  const ctx = cv.getContext('2d');
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let W = 0, H = 0, c = {}, level = 'ok', models = [], packets = [], ripples = [];
  let energy = 0, angle = -Math.PI / 2, sweep = 0, running = false, last = 0, raf = 0;
  const ease = t => 1 - Math.pow(1 - t, 3);

  function recolor() {
    c = { ok: cssVar('--ok'), warn: cssVar('--warn'), bad: cssVar('--bad'), accent: cssVar('--accent'),
          sep: cssVar('--separator'), surface: cssVar('--surface'), mavis: document.documentElement.dataset.theme === 'mavis' };
    if (held()) draw(0);
  }
  function fit() {
    const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (!r.width) return false;
    if (r.width !== W || r.height !== H) {
      W = r.width; H = r.height; cv.width = W * dpr; cv.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    return true;
  }
  function circle(x, y, r) { ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); }

  function draw(dt) {
    if (!fit() || !c.accent) return;
    const cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 8;
    const now = performance.now() / 1000;
    const col = c[level] || c.ok;
    ctx.clearRect(0, 0, W, H);

    ctx.lineWidth = 1; ctx.strokeStyle = c.sep;
    circle(cx, cy, R * 0.62); ctx.stroke();
    circle(cx, cy, R * 0.95); ctx.stroke();
    // a slow sweep on the outer ring; it speeds up when calls are flowing
    ctx.lineCap = 'round'; ctx.lineWidth = 2;
    const sg = ctx.createConicGradient ? ctx.createConicGradient(sweep, cx, cy) : null;
    if (sg) { sg.addColorStop(0, rgba(c.accent, 0)); sg.addColorStop(0.16, rgba(c.accent, 0.8)); sg.addColorStop(0.1601, rgba(c.accent, 0)); ctx.strokeStyle = sg; }
    else ctx.strokeStyle = rgba(c.accent, 0.6);
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.95, sweep, sweep + Math.PI * 2 * 0.16); ctx.stroke();

    ripples = ripples.filter(r => r.t < 1);
    ripples.forEach(r => {
      r.t = Math.min(1, r.t + dt / 1.2);
      ctx.strokeStyle = rgba(r.col, 0.55 * (1 - r.t)); ctx.lineWidth = 1.5;
      circle(cx, cy, R * (0.25 + 0.72 * ease(r.t))); ctx.stroke();
    });

    const breath = still ? 0.5 : Math.sin(now * (1.2 + energy * 0.9)) * 0.5 + 0.5;
    const cr = R * (0.2 + 0.025 * breath + Math.min(energy, 3) * 0.012);
    const halo = ctx.createRadialGradient(cx, cy, cr * 0.5, cx, cy, cr * 2.6);
    halo.addColorStop(0, rgba(col, (c.mavis ? 0.42 : 0.28) + 0.12 * breath)); halo.addColorStop(1, rgba(col, 0));
    ctx.fillStyle = halo; circle(cx, cy, cr * 2.6); ctx.fill();
    const body = ctx.createRadialGradient(cx - cr * 0.35, cy - cr * 0.35, cr * 0.1, cx, cy, cr);
    body.addColorStop(0, rgba(col, 1)); body.addColorStop(1, rgba(col, 0.78));
    ctx.fillStyle = body; circle(cx, cy, cr); ctx.fill();

    const n = Math.max(models.length, 1);
    models.forEach((m, i) => {
      const a = angle + i * (Math.PI * 2 / n);
      const x = cx + Math.cos(a) * R * 0.62, y = cy + Math.sin(a) * R * 0.62;
      if (m.busy && m.online) { ctx.strokeStyle = rgba(c.accent, 0.35 + 0.35 * breath); ctx.lineWidth = 1.5; circle(x, y, 8 + 3 * breath); ctx.stroke(); }
      circle(x, y, 5);
      if (m.online) { ctx.fillStyle = c.accent; ctx.fill(); }
      else { ctx.fillStyle = c.surface; ctx.fill(); ctx.strokeStyle = c.bad; ctx.lineWidth = 1.5; ctx.stroke(); }
    });

    packets = packets.filter(p => !p.done);
    packets.forEach(p => {
      p.t = Math.min(1, p.t + dt / 0.85);
      const at = k => { const e = ease(k), rad = R * 1.02 + (cr - R * 1.02) * e, a = p.a + e * 0.7; return [cx + Math.cos(a) * rad, cy + Math.sin(a) * rad]; };
      const [x, y] = at(p.t), [tx, ty] = at(Math.max(0, p.t - 0.14));
      const tail = ctx.createLinearGradient(tx, ty, x, y);
      tail.addColorStop(0, rgba(p.col, 0)); tail.addColorStop(1, rgba(p.col, 0.8));
      ctx.strokeStyle = tail; ctx.lineWidth = 2.2; ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(x, y); ctx.stroke();
      ctx.fillStyle = p.col; circle(x, y, 3.2); ctx.fill();
      if (p.t >= 1) { p.done = true; ripples.push({ t: 0, col: p.col }); energy = Math.min(energy + 0.7, 4); }
    });

    angle += dt * (0.16 + energy * 0.4);
    sweep += dt * (0.7 + energy * 1.1);
    energy = Math.max(0, energy - dt * 0.3);
  }
  // 2026-10-27 (the optimisation check): idle, it draws about 4 times a second from a timer instead of asking for every
  // frame; it stops while the tab is hidden or the core is scrolled out of sight, and picks up again when it's back.
  let tmr = 0, offscreen = false;
  function next(idle) {
    if (!running || document.hidden || offscreen) return;
    if (idle && _calm && !_calm.awake()) return;  // 2026-10-28: idle and calm, the breath holds (no frames at all)
    if (idle) tmr = setTimeout(() => { tmr = 0; raf = requestAnimationFrame(frame); }, 250);
    else raf = requestAnimationFrame(frame);
  }
  function frame(t) {
    raf = 0;
    if (!running) return;
    const idle = !packets.length && !ripples.length && energy < 0.05;
    const dt = last ? Math.min((t - last) / 1000, 0.05) : 0;
    last = t;
    draw(dt);
    next(idle);
  }
  const resume = () => { if (running && !raf && !tmr) { last = 0; next(false); } };
  document.addEventListener('visibilitychange', resume);
  if ('IntersectionObserver' in window) new IntersectionObserver(es => { offscreen = !es[0].isIntersecting; resume(); }).observe(cv);
  if (_calm) _calm.on(resume);
  const held = () => !running || (!raf && !tmr);  // no frame coming: a change draws itself
  return {
    start() { if (still || window._noAnim) { draw(0); return; } if (running) return; running = true; last = 0; next(false); },
    stop() { running = false; cancelAnimationFrame(raf); clearTimeout(tmr); raf = tmr = 0; },
    recolor,
    setLevel(l) { level = l; if (held()) draw(0); },
    setModels(ms) { models = ms; if (held()) draw(0); },
    packet(kind) {
      if (still || !running || packets.length > 30) return;
      packets.push({ t: 0, a: Math.random() * Math.PI * 2, col: kind === 'write' ? c.warn : c.accent });
      if (_calm) _calm.wake();
      resume();
    },
  };
})();
Core.recolor();
Core.start();
window.addEventListener('resize', () => { if (!Core._r) Core._r = requestAnimationFrame(() => { Core._r = 0; Core.recolor(); }); });

// ---- status line: one plain-English sentence built from the same live data as everything else ----
const _sys = { reachable: true, mcp: null, mem: null, ai: null, caps: null, facts: null, saved: null, obsOpen: 0, features: null };
function setHTMLIfChanged(id, html) {
  const el = document.getElementById(id);
  if (el && el._h !== html) { el.innerHTML = html; el._h = html; }
}
function listNames(a) { return a.length < 2 ? a[0] : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1]; }
// Weekly value line (branding-and-pro-review.md, moment 3): measured RTK savings over the last 7 days, priced at a
// Claude API input rate. Labelled an estimate: subscription users don't pay per token.
const GBP_PER_M_TOKENS = 2.40; // ≈ $3 per million input tokens (Sonnet API)
async function loadWeekValue() {
  let d;
  try { d = await (await fetch('/api/savings')).json(); } catch (e) { window.HSCockpit && HSCockpit.ingest('savings', null); return; }
  window.HSCockpit && HSCockpit.ingest('savings', d);
  const since = new Date(); since.setDate(since.getDate() - 6);
  const from = since.toLocaleDateString('en-CA');
  _sys.week = ((d.daily && d.daily.daily) || []).filter(x => x.date >= from).reduce((a, x) => a + (x.saved_tokens || 0), 0);
  renderHero();
}
function renderHero() {
  const probs = [];
  const feats = _sys.features || [];
  // "off" is a part that isn't set up: shown grey with how to add it, never counted as a problem
  const broken = feats.filter(f => f.status === 'down' || f.status === 'degraded');
  if (!_sys.reachable) probs.push(['bad', "Can't reach the dashboard server. Retrying every 4 seconds"]);
  else if (broken.length) {
    // Feature health speaks in consequences ("decisions use the GPU fallback"), not component names.
    const tools = broken.find(f => f.label === 'Tools');
    (tools ? [tools] : broken).forEach(f => probs.push([f.status === 'down' ? 'bad' : 'warn', f.detail.replace(/\.$/, '')]));
  } else {
    if (_sys.mcp === false) probs.push(['bad', "HomeShed isn't answering"]);
    if (_sys.mem === false) probs.push(['bad', "Shared memory is set up but isn't answering"]);
    if (_sys.ai && _sys.ai.offline.length) {
      const off = _sys.ai.offline;
      probs.push([_sys.ai.online ? 'warn' : 'bad', `${listNames(off)} ${off.length > 1 ? 'are' : 'is'} offline`]);
    }
  }
  const level = probs.some(p => p[0] === 'bad') ? 'bad' : probs.length ? 'warn' : 'ok';
  let line;
  if (!probs.length && _sys.mcp !== true) {
    line = 'Checking how everything is…';  // nothing known yet: don't claim "running" (R&D's sweep of 2a)
  } else if (!probs.length) {
    const ai = _sys.ai ? `<b>${_sys.ai.online} of ${_sys.ai.total}</b> AI models are online`
      : _sys.aiNone ? 'No AI model is set up yet' : 'AI models are being checked';
    line = `Everything is running. ${ai}${_sys.caps != null ? `, and <b>${_sys.caps}</b> tools are ready` : ''}.`;
  } else {
    line = probs.map(p => escapeHtml(p[1])).join('. ') + '.' + (level === 'warn' ? ' Everything else is running.' : '');
  }
  const sub = [];
  const gbp = _sys.week / 1e6 * GBP_PER_M_TOKENS;  // pennies undersell it: the £ shows from £1 up
  if (_sys.week) sub.push(`This week: ${fmtTok(_sys.week)} tokens kept out of Claude${gbp >= 1 ? `, worth about £${gbp.toFixed(2)} (estimate)` : ''}.`);
  else if (_sys.saved) sub.push(`About ${fmtTok(_sys.saved)} tokens kept out of Claude so far.`);
  if (_sys.facts != null) sub.push(`${_sys.facts.toLocaleString()} ${_sys.facts === 1 ? 'fact' : 'facts'} in shared memory.`);
  const hero = document.getElementById('statusHero');
  hero.classList.toggle('warn', level === 'warn');
  hero.classList.toggle('bad', level === 'bad');
  setHTMLIfChanged('heroLine', line);
  setHTMLIfChanged('heroSub', sub.join(' '));
  setHTMLIfChanged('heroFeatures', feats.map(f =>
    `<span class="feat ${f.status}" title="${escAttr(f.detail)}"><i></i>${escapeHtml(f.label)}</span>`).join(''));
  const actions = [];
  if (_sys.ai && _sys.ai.offline.length && _sys.reachable) actions.push(`<button class="ghost" onclick="switchTab('models')">Open Models</button>`);
  if (_sys.obsOpen) actions.push(`<button class="ghost" onclick="switchTab('observations')">Review ${_sys.obsOpen} open observation${_sys.obsOpen === 1 ? '' : 's'}</button>`);
  setHTMLIfChanged('heroActions', actions.join(''));
  Core.setLevel(level);
  document.title = level === 'ok' ? 'HomeShed' : `(${probs.length}) HomeShed`;
  const bs = document.getElementById('bootStatus');
  if (bs) bs.textContent = level === 'ok' ? 'Everything is running' : 'Some things need a look';
  const badge = document.getElementById('liveBadge');
  badge.classList.toggle('lost', !_sys.reachable);
  document.getElementById('liveText').textContent = _sys.reachable ? 'Live' : 'Reconnecting…';
}

// ---- animated count-up for stat numbers ----
function animateNumber(el, from, to, suffix) {
  if (from === to || isNaN(from) || isNaN(to)) { el.textContent = to + (suffix || ''); return; }
  const dur = 500, start = performance.now();
  function step(now) {
    const p = Math.min(1, (now - start) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    const val = Math.round(from + (to - from) * eased);
    el.textContent = val + (suffix || '');
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
function setStatNumber(id, value) {
  const el = document.getElementById(id);
  const prev = parseInt(el.dataset.raw || '0', 10);
  if (typeof value === 'number') {
    animateNumber(el, isNaN(prev) ? 0 : prev, value);
    el.dataset.raw = value;
  } else {
    el.textContent = value;
    el.dataset.raw = '0';
  }
}

// On the second while the tab shows; nothing at all while it's hidden (it picks up on the way back). 2026-10-28.
let _clockT = 0;
function tickClock() {
  clearTimeout(_clockT); _clockT = 0;
  if (document.hidden) return;
  const now = new Date(), put = (id, t) => { const el = document.getElementById(id); if (el.textContent !== t) el.textContent = t; };
  put('clock', now.toLocaleTimeString('en-GB', { hour12: false }));
  put('sumClock', now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }));
  _clockT = setTimeout(tickClock, 1005 - now.getMilliseconds());
}
document.addEventListener('visibilitychange', tickClock);
// The folded nav rail opens while the keyboard is in it: .kb-in on the sidebar (was :has(:focus-visible), which the
// browser re-checked whenever anything in the sidebar changed, the clock included). 2026-10-28.
document.addEventListener('focusin', e => { const sb = e.target.closest && e.target.closest('.sidebar'); if (sb) sb.classList.toggle('kb-in', e.target.matches(':focus-visible')); });
document.addEventListener('focusout', e => { const sb = e.target.closest && e.target.closest('.sidebar'); if (sb && !(e.relatedTarget && sb.contains(e.relatedTarget))) sb.classList.remove('kb-in'); });

// Summary screen (the owner, 2026-10-01: "a summary dash ... i can then open on a monitor that's spare"): the Overview
// itself in body.summary, which the CSS packs into one screen. Leaving it, or opening any other page, goes back.
function setSummary(on) {
  if (on && _activeTab !== 'ops') switchTab('ops');
  document.body.classList.toggle('summary', on);
  window.HSCockpit && HSCockpit.summary(on);  // the status, the live core and the numbers move between the two layouts
  try { history.replaceState(null, '', on ? '#summary' : location.pathname); } catch (e) { /* cosmetic */ }
  if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {});
}
function toggleSummaryFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
}
tickClock();

function relTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const diffMs = Date.now() - d.getTime();
  const m = Math.floor(diffMs / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

function drawSpark(canvas, data) {
  if (!canvas || !data || data.length < 2) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const min = Math.min(...data), max = Math.max(...data);
  const range = (max - min) || 1;
  const pad = 3;
  const pts = data.map((v, i) => [
    (i / (data.length - 1)) * w,
    h - pad - ((v - min) / range) * (h - pad * 2)
  ]);
  ctx.beginPath();
  pts.forEach(([x, y], i) => i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y));
  const accent = cssVar('--accent'), glow = document.documentElement.dataset.theme === 'mavis';
  ctx.strokeStyle = accent;
  ctx.lineWidth = 1.5;
  ctx.shadowColor = rgba(accent, 0.7);
  ctx.shadowBlur = glow ? 5 : 0;
  ctx.stroke();
  ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
  ctx.shadowBlur = 0;
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, rgba(accent, 0.16));
  grad.addColorStop(1, rgba(accent, 0));
  ctx.fillStyle = grad;
  ctx.fill();
}

// Last sparkline data, so a theme switch can redraw the canvases instantly in the new accent.
let _sparkData = [];
function redrawSparks() { _sparkData.forEach((d, i) => drawNeon(document.getElementById(`spark-${i}`), d.data, d.on)); }
// The AI models strip's line: a phosphor trace when the model is online (a wide faint pass, a mid pass, the thin bright
// core, on a fine grid with a lit newest point: the cockpit's language, no shadowBlur), a flat grey line when it's offline
// or has no history yet (so a cell is never blank). Cockpit off: one thin line, no grid.
function drawNeon(canvas, data, on) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d'), dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const ck = !!document.querySelector('#opsView.ovl-ck'), glow = ck ? parseFloat(cssVar('--tm-glow')) || 0 : 0;
  const pts = Array.isArray(data) && data.length > 1 ? data : [0, 0];
  const min = Math.min(...pts), range = (Math.max(...pts) - min) || 1, pad = 4;
  const xy = pts.map((v, i) => [(i / (pts.length - 1)) * (w - pad * 2) + pad, h - pad - ((v - min) / range) * (h - pad * 2)]);
  const color = on ? (ck ? cssVar('--tm-measured') : cssVar('--neon')) : cssVar('--text-3');
  if (ck) {  // the precision grid: a fine vertical every eighth and the baseline
    ctx.lineWidth = 1; ctx.strokeStyle = cssVar('--tm-grid-2'); ctx.beginPath();
    for (let i = 0; i <= 8; i++) { const x = Math.round(pad + (w - pad * 2) * i / 8) + 0.5; ctx.moveTo(x, 1); ctx.lineTo(x, h - 1); }
    ctx.stroke();
    ctx.strokeStyle = cssVar('--tm-grid'); ctx.beginPath(); ctx.moveTo(pad, h - 0.5); ctx.lineTo(w - pad, h - 0.5); ctx.stroke();
  }
  const line = (lw, a) => { ctx.beginPath(); xy.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.lineWidth = lw; ctx.strokeStyle = rgba(color, a); ctx.stroke(); };
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  if (on && ck) {
    ctx.beginPath(); xy.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.lineTo(xy[xy.length - 1][0], h); ctx.lineTo(xy[0][0], h); ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, rgba(color, 0.2)); g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g; ctx.fill();
    if (glow) { line(6, 0.06 * glow); line(3.2, 0.14 * glow); }
  }
  line(ck ? 1.5 : 1.6, 1);
  if (on) {
    const [lx, ly] = xy[xy.length - 1];
    if (glow) {
      const rg = ctx.createRadialGradient(lx, ly, 0, lx, ly, 9);
      rg.addColorStop(0, rgba(color, 0.5 * glow)); rg.addColorStop(1, rgba(color, 0));
      ctx.fillStyle = rg; ctx.fillRect(lx - 9, ly - 9, 18, 18);
    }
    ctx.beginPath(); ctx.arc(lx, ly, 2.4, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
  }
}

// Which memory categories are expanded -- tracked separately from the DOM because loadMemory()
// rebuilds #memory's innerHTML from scratch every poll (4s). Without this, that rebuild wipes
// the .open class the user just clicked on, and an expanded category silently closes itself
// a few seconds later. Real bug, found live.
const _openMemCategories = new Set();
// Populated when a category is expanded -- /api/memory's own "latest" is capped to 4 per
// category (a multi-category overview, not meant to carry every fact for every category on
// every 4s poll). Real gap found live 2026-09-24 ("I only see 4 of 8") -- expanding a category
// now fetches its real full list via /api/memory/<id> instead of showing the same capped 4.
const _memoryFullCache = {};

async function toggleMem(id) {
  if (_viewer) return;  // a view-only login sees counts, never what the memories say
  const el = document.getElementById('mem-' + id);
  el.classList.toggle('open');
  if (!el.classList.contains('open')) {
    _openMemCategories.delete(id);
    return;
  }
  _openMemCategories.add(id);
  const body = el.querySelector('.mem-row-body');
  body.innerHTML = '<div class="loading">Loading full list…</div>';
  try {
    const r = await fetch(`/api/memory/${encodeURIComponent(id)}` + memQuery());
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    _memoryFullCache[id] = d.facts;
    body.innerHTML = d.facts.length
      ? d.facts.map(f => `<div class="mem-fact">${escapeHtml(f.content || '')}<div class="mem-ts">${relTime(f.timestamp)}</div></div>`).join('')
      : '<div class="empty">No facts yet</div>';
  } catch (e) {
    body.innerHTML = `<div class="empty">${loadErr('full list', e)}</div>`;
  }
}

// Previous-poll snapshots, used purely to detect "this changed since last time" for the flash
// effect. Not persisted -- resets on page reload, which is fine, first poll just can't flash.
const _prev = { memCounts: {}, aiTokens: {}, capRisk: {}, lastActivityTs: 0 };

// Counts/sub-labels are recomputed from the FULL activity list every poll, not incremented --
// an incrementing counter starting at 0 resets to 0 on every page reload, which is the same
// class of bug the memory-accordion fix addressed: client state must be derived from server
// truth, not accumulated locally, or a refresh silently throws it away.
// Lifetime calls per tool from mcp-server's persisted usage counters (loaded with the token strip).
// the owner 2026-09-28 ("I don't see read / writes on mobile"): counting only the in-memory buffer
// showed 0 after every tool-server restart, so the big numbers are now lifetime totals.
let _lifetimeCaps = null;
function computeRwCounts(entries) {
  // Which tools write comes from the tool list (loadCapabilities). Until it lands every call would
  // count as a read (seen live 2026-09-28: 211 reads, 0 writes), so the numbers wait for it.
  const riskKnown = Object.keys(_prev.capRisk).length > 0;
  let read = 0, write = 0, lastRead = null, lastWrite = null;
  entries.forEach(e => { // newest-first, so the first match per risk is the most recent
    if (_prev.capRisk[e.id] === 'write') {
      write++;
      if (!lastWrite) lastWrite = e;
    } else {
      read++;
      if (!lastRead) lastRead = e;
    }
  });
  let lifeRead = null, lifeWrite = null;
  if (_lifetimeCaps) {
    lifeRead = 0; lifeWrite = 0;
    for (const [id, c] of Object.entries(_lifetimeCaps)) {
      if (_prev.capRisk[id] === 'write') lifeWrite += c.calls || 0; else lifeRead += c.calls || 0;
    }
  }
  const count = n => riskKnown ? n.toLocaleString() : '…';
  const sub = last => !riskKnown ? 'loading the tool list…' : last ? `latest: ${last.id}` : 'none since HomeShed restarted';
  document.getElementById('rwReadCount').textContent = count(lifeRead ?? read);
  document.getElementById('rwWriteCount').textContent = count(lifeWrite ?? write);
  document.getElementById('rwReadSub').textContent = sub(lastRead);
  document.getElementById('rwWriteSub').textContent = sub(lastWrite);
  if (riskKnown && window.OVL) OVL.ingest('rw', { read: lifeRead ?? read, write: lifeWrite ?? write });

  // Context for why counts might look low -- the buffer resets to 0 on every mcp-server
  // redeploy (in-memory only, not a durable log), which is easy to mistake for a bug if the
  // panel gives no clue. Real user confusion this fixed, 2026-09-23.
  const bufEl = document.getElementById('tickerBufferInfo');
  bufEl.textContent = entries.length >= 200
    ? '200+ buffered'
    : `${entries.length} since HomeShed last restarted`;
}

// Real call counts for the reasoning.* pipeline, computed the same idempotent way as
// computeRwCounts -- recounted from the full activity buffer on every poll, not incremented.
// Added 2026-09-24 per the owner's request for "some visuals to any of the pipelines" -- genuine
// data from the same live feed the ticker already uses, not a simulation.
// 2026-09-26: fed by LIFETIME counters (mcp-server usage.py via /api/tokens), not the in-memory
// activity buffer -- that buffer resets on every mcp-server redeploy, which is why the owner "never
// saw figures". computeReasoningCounts stays as a no-op because the activity poll still calls it.
const _REASONING_STAT_IDS = {
  'reasoning.solve': 'statSolve',
  'reasoning.decompose_task': 'statDecompose',
  'reasoning.route': 'statRoute',
  'reasoning.delegate': 'statDelegate',
  'reasoning.pipeline': 'statPipeline',
  'local_ai.ask': 'statAsk',
};

function computeReasoningCounts(entries) { /* superseded by applyPipelineCounts (lifetime) */ }

function applyPipelineCounts(est) {
  const caps = (est && est.capabilities) || {};
  for (const [id, elId] of Object.entries(_REASONING_STAT_IDS)) {
    const el = document.getElementById(elId);
    const n = caps[id] ? caps[id].calls : 0;
    if (el.textContent !== String(n)) { el.textContent = n; flashEl(el, 'flash'); }
  }
  window.OVL && OVL.ingest('pipe', { caps, ids: _REASONING_STAT_IDS });
  if (est && est.since) document.getElementById('pipeSince').textContent = 'since ' + new Date(est.since * 1000).toLocaleDateString();
}

function pulseRw(panelId) {
  const panel = document.getElementById(panelId);
  panel.classList.remove('pulsing'); void panel.offsetWidth;
  panel.classList.add('pulsing');
  setTimeout(() => panel.classList.remove('pulsing'), 1800);
}

function flashEl(el, cls) {
  if (!el) return;
  el.classList.remove(cls); void el.offsetWidth; // restart animation if already running
  el.classList.add(cls);
}

// A tab opened before a redeploy (found 2026-10-01: an old tab showed "Busy" through a whole compact): the Summary
// screen reloads itself at once, since nobody types there; any other page offers a reload, so nothing typed is lost.
let _panelVersion = null;
function panelUpdated() {
  if (document.body.classList.contains('summary')) { location.reload(); return; }
  const pill = document.getElementById('updatePill');
  if (pill) pill.hidden = false;
}
async function loadHealth() {
  try {
    const r = await fetch('/api/health');
    if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent('/' + location.hash); return; }
    const d = await r.json();
    _sys.reachable = true;
    if (d.panel) { if (!_panelVersion) _panelVersion = d.panel; else if (d.panel !== _panelVersion) panelUpdated(); }
    // true: running. false: set up but not answering (red). null: not set up, or nothing said about it (grey, never
    // red: release gate B, a first run mustn't look broken).
    _sys.mcp = d.mcp_server === 'ok' ? true : d.mcp_server && d.mcp_server !== 'unknown' ? false : null;
    _sys.memOff = d.memory_core === 'not-set-up';
    const builtIn = d.memory_core === 'built-in';  // HomeShed's own memory store: no separate service
    _sys.mem = d.memory_core === 'ok' || (builtIn && _sys.mcp) ? true
      : d.memory_core && d.memory_core !== 'unknown' && !_sys.memOff && !builtIn ? false : null;
    setHealthTile('statMcp', _sys.mcp, 'HomeShed', '');
    setHealthTile('statMem', _sys.mem, 'Shared memory', _sys.memOff ? "The shared memory service isn't set up." : '');
    if (builtIn) Object.assign(document.getElementById('statMem'), { textContent: 'Built in',
      title: 'HomeShed keeps memory itself: no separate service needed.' });
  } catch (e) {
    _sys.reachable = false;
    // #10: say it, don't leave a bare dash (control-panel-review-2026-09-30.md)
    ['statMcp', 'statMem'].forEach(id => { const el = document.getElementById(id); el.textContent = 'No reply'; el.className = 'stat-num err'; el.title = 'The Control Panel server did not answer. Press Refresh in a minute.'; });
  }
}

function setHealthTile(id, state, what, offWhy) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = state === true ? 'Running' : state === false ? 'Down' : offWhy ? 'Not set up' : '—';
  el.className = 'stat-num ' + (state === false ? 'err' : state === null ? 'muted' : '');
  el.title = state === false ? `${what} is set up but isn't answering.` : offWhy;
}

async function loadCapabilities() {
  const el = document.getElementById('capabilities');
  try {
    const r = await fetch('/api/capabilities');
    const caps = await r.json();
    if (!Array.isArray(caps)) throw new Error(caps.error || 'unexpected response');
    setStatNumber('statCaps', caps.length);
    _sys.caps = caps.length;
    document.getElementById('capCount').textContent = caps.length;
    el.innerHTML = caps.map(c => `
      <div class="cap-row" id="cap-${c.id}" title="${(c.description || '').replace(/"/g, '&quot;')}">
        <span class="badge">${c.category}</span>
        <span class="cap-id">${c.id}</span>
        <span class="cap-desc-inline">${c.description || ''}</span>
        <span class="badge ${c.risk === 'write' ? 'write' : 'read'}">${c.risk}</span>
      </div>
    `).join('');
    caps.forEach(c => { _prev.capRisk[c.id] = c.risk; });
    if (window.OVL) { OVL.ingest('caps', caps); OVL.ingest('risk', id => _prev.capRisk[id]); }
  } catch (e) {
    el.innerHTML = `<div class="empty">${loadErr('capabilities', e)}</div>`;
    Object.assign(document.getElementById('statCaps'), { textContent: '—', title: "The tool list didn't load: " + e.message });
  }
}

// Every project's memory (the owner, 2026-09-28: "I don't see other projects memory either in the dash? Just this
// one"). Each project has its own agent in the memory store; the owner picks which one this panel shows.
let _memAgent = '', _memProjectsAt = 0;
try { _memAgent = localStorage.getItem('cp-mem-agent') || ''; } catch (e) { /* not remembered */ }
const memQuery = () => _memAgent ? '?agent=' + encodeURIComponent(_memAgent) : '';
async function loadMemoryProjects() {
  const sel = document.getElementById('memProject');
  if (!sel || _viewer) return;
  _memProjectsAt = Date.now();
  let d;
  try { d = await apiCall('GET', '/api/memory/projects'); } catch (e) { return; }
  const rows = d.projects || [];
  if (rows.length < 2) { sel.hidden = true; return; }
  sel.innerHTML = rows.map(p => {
    const value = p.mine ? '' : p.id, label = p.mine ? 'Claude sessions, shared' : niceName(p.name);
    return `<option value="${escAttr(value)}" ${value === _memAgent ? 'selected' : ''}>${escapeHtml(label)} (${p.facts ? p.facts + ' facts' : 'nothing yet'})</option>`;
  }).join('');
  sel.hidden = false;
}
function niceName(n) {  // "research-improvements" reads as "Research improvements"
  const t = String(n || '').replace(/[-_]+/g, ' ').trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}
function setMemProject(id) {
  _memAgent = id;
  try { localStorage.setItem('cp-mem-agent', id); } catch (e) { /* not remembered */ }
  _openMemCategories.clear();
  Object.keys(_memoryFullCache).forEach(k => delete _memoryFullCache[k]);
  _prev.memCounts = {};  // a different project isn't "new facts": no flash
  loadMemory();
}

const MEM_NOT_SET_UP = "<div class=\"empty\">Shared memory isn't set up yet. It's a separate service (memory-core) that keeps facts your AI sessions can recall later.</div>";
async function loadMemory() {
  const el = document.getElementById('memory');
  if (!_viewer && Date.now() - _memProjectsAt > 120000) loadMemoryProjects();
  try {
    const r = await fetch('/api/memory' + memQuery());
    if (r.status === 404 && _memAgent) { setMemProject(''); return; }  // that project's memory is gone
    const d = await r.json();
    window.OVL && OVL.ingest('memory', d);
    if (d.not_set_up || !Array.isArray(d.categories)) {  // no memory service, or nothing said: grey, never "No reply"
      el.innerHTML = d.not_set_up ? MEM_NOT_SET_UP : '<div class="empty">No memory to show yet.</div>';
      Object.assign(document.getElementById('statFacts'), { textContent: d.not_set_up ? 'Not set up' : '—', className: 'stat-num muted',
        title: d.not_set_up ? "The shared memory service isn't set up." : '' });
      return;
    }
    const totalFacts = d.categories.reduce((s, c) => s + c.count, 0);
    const statFacts = document.getElementById('statFacts');
    const prevTotal = _prev.memCounts.__total;
    setStatNumber('statFacts', totalFacts);
    Object.assign(statFacts, { className: 'stat-num', title: '' });  // clear an earlier "No reply"
    _sys.facts = totalFacts;
    document.getElementById('memCount').textContent = totalFacts ? `${totalFacts} ${totalFacts === 1 ? 'fact' : 'facts'}` : '';

    const isFirstPoll = prevTotal === undefined;
    if (!isFirstPoll && totalFacts !== prevTotal) flashEl(statFacts.closest('.stat-box'), 'flash-write');

    const sel = document.getElementById('memProject');
    const who = sel && !sel.hidden && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].text.replace(/\s*\([^)]*\)$/, '') : 'This project';
    const empty = d.categories.filter(c => !c.count).map(c => c.label);
    el.innerHTML = !totalFacts ? `<div class="mem-empty"><b>Nothing saved yet</b>
        <span>${escapeHtml(who)} hasn't saved anything to memory. Facts show up here when its Claude session saves one.</span>
        ${_memAgent ? '<button type="button" class="ghost" onclick="setMemProject(\'\')">Show the shared memory</button>' : ''}</div>`
      : d.categories.filter(c => c.count).map(cat => {
      const prevCount = _prev.memCounts[cat.id];
      const changed = !isFirstPoll && prevCount !== undefined && cat.count !== prevCount;
      return `
      <div class="mem-row ${cat.legacy ? 'legacy' : ''} ${changed ? 'flash-write' : ''} ${_openMemCategories.has(cat.id) ? 'open' : ''}" id="mem-${cat.id}">
        <div class="mem-row-head" onclick="toggleMem('${cat.id}')">
          <span><span class="chevron">▸</span><span class="mem-label">${cat.label}</span></span>
          <span class="mem-count">${cat.count}${cat.count_capped ? '+' : ''}</span>
        </div>
        <div class="mem-row-body">
          ${(() => {
            // If this category is open and its full list has already been fetched (see
            // toggleMem), keep showing that instead of overwriting it with the capped 4-item
            // preview on every 4s poll -- otherwise an expanded category would visibly snap
            // back to 4 items a few seconds after opening.
            const full = _openMemCategories.has(cat.id) ? _memoryFullCache[cat.id] : null;
            const facts = full || cat.latest;
            return facts.length
              ? facts.map(f => `<div class="mem-fact">${escapeHtml((f.content || '').slice(0, full ? 2000 : 200))}<div class="mem-ts">${relTime(f.timestamp)}</div></div>`).join('')
              : '<div class="empty">No facts yet</div>';
          })()}
        </div>
      </div>`;
    }).join('') + (empty.length ? `<div class="mem-none">Nothing yet in ${escapeHtml(empty.join(', '))}.</div>` : '');

    d.categories.forEach(cat => { _prev.memCounts[cat.id] = cat.count; });
    _prev.memCounts.__total = totalFacts;
  } catch (e) {
    if (_sys.memOff || /isn't set up/.test(e.message)) {  // no memory service: grey, with what it is, never "No reply"
      el.innerHTML = MEM_NOT_SET_UP;
      Object.assign(document.getElementById('statFacts'), { textContent: 'Not set up', className: 'stat-num muted', title: "The shared memory service isn't set up." });
      return;
    }
    el.innerHTML = `<div class="empty">${loadErr('memory', e)}</div>`;
    Object.assign(document.getElementById('statFacts'), { textContent: 'No reply', className: 'stat-num err', title: e.message });
  }
}

async function loadLocalAi() {
  const el = document.getElementById('aiUsage');
  try {
    const r = await fetch('/api/local-ai');
    const d = await r.json();
    if (!Array.isArray(d.servers) || !d.servers.length) {  // no model set up: grey, with how, never "No reply"
      Object.assign(document.getElementById('statAiOnline'), { textContent: '—', className: 'stat-num muted', title: 'No model is set up yet.' });
      _sys.ai = null; _sys.aiNone = true; _sys.servers = [];
      el.innerHTML = '<div class="empty">No model is set up yet. Add your own model server (Ollama, LM Studio or llama.cpp), or a cloud key, in Settings.</div>';
      return;
    }
    _sys.aiNone = false;
    const tiles = d.servers.filter(s => !s.hidden);  // hidden: polled for status only, not a tile
    const onlineCount = tiles.filter(s => s.online).length;
    const statEl = document.getElementById('statAiOnline');
    statEl.textContent = `${onlineCount}/${tiles.length}`;
    statEl.className = 'stat-num ' + (onlineCount === tiles.length ? '' : onlineCount === 0 ? 'err' : 'warn');
    _sys.ai = { online: onlineCount, total: tiles.length, offline: tiles.filter(s => !s.online).map(s => s.name) };
    _sys.servers = d.servers;
    Core.setModels(tiles.map(s => ({ online: s.online, busy: (s.requests_processing || 0) > 0 })));

    el.innerHTML = tiles.map((s, i) => {
      const prevTok = _prev.aiTokens[s.name];
      const changed = prevTok !== undefined && s.total_tokens != null && s.total_tokens !== prevTok;
      const thirdMetric = s.type === 'gateway'
        ? { val: s.requests_total != null ? s.requests_total.toLocaleString() : '—', lbl: 'Requests' }
        : { val: s.requests_processing != null ? s.requests_processing : '0', lbl: 'In flight' };
      const decider = s.type === 'decider';  // a decision server counts decisions, not tokens
      const images = s.type === 'images';    // Nano Banana: images today against the owner's daily cap
      const llm = s.type === 'llm';          // Claude: the free monthly API credit left
      // with a monthly limit set (Settings > Language models): what's left of it; without one, this month's spend
      const m1 = llm ? (s.credit_left_usd != null ? { val: '$' + Number(s.credit_left_usd).toFixed(2), lbl: 'Credit left' }
                        : { val: s.cost_month_usd != null ? '$' + Number(s.cost_month_usd).toFixed(2) : '—', lbl: 'This month' })
               : images ? { val: s.requests_total != null ? s.requests_total.toLocaleString() : '—', lbl: 'Today' }
               : decider ? { val: s.requests_total != null ? s.requests_total.toLocaleString() : '—', lbl: 'Decisions' }
                      : { val: s.total_tokens != null ? s.total_tokens.toLocaleString() : '—', lbl: 'Tokens' };
      const m2 = llm ? { val: `${s.requests_total || 0} today` + (s.monthly_credit_usd ? ` · of $${s.monthly_credit_usd}/month` : ''), lbl: 'Use' }
               : images ? { val: s.cap ? `of ${s.cap}` + (s.cost_today_usd ? ` · ~$${s.cost_today_usd}` : '') : 'no cap set', lbl: 'Daily cap' }
               : decider ? { val: s.avg_ms != null ? s.avg_ms + ' ms' : '—', lbl: 'Average' }
                      : { val: s.delta_tokens_window != null ? '+' + s.delta_tokens_window.toLocaleString() : '—', lbl: 'Recent' };
      const busy = s.online && (s.requests_processing || 0) > 0;
      const tip = `${s.name}: ${s.label}. ${s.online ? 'Online' : 'Offline'}. ${m1.lbl} ${m1.val} · ${m2.lbl} ${m2.val} · ${thirdMetric.lbl} ${thirdMetric.val}`;
      return `
      <div class="ai-cell ${s.online ? 'online' : ''} ${busy ? 'busy' : ''} ${changed ? 'flash' : ''}" title="${escAttr(tip)}">
        <span class="dot" aria-hidden="true"></span>
        <div style="min-width:0"><div class="ai-cell-name">${escapeHtml(s.name)}</div>
          <div class="ai-cell-sub">${escapeHtml(s.online ? s.label : images ? 'Switched off (Settings > Image generation)' : llm ? 'Switched off (Settings > Language models)' : 'Offline')}</div></div>
        <canvas id="spark-${i}" aria-hidden="true"></canvas>
        <div class="ai-cell-num">${escapeHtml(String(m1.val))}<span>${escapeHtml(m1.lbl)}</span></div>
      </div>`;
    }).join('');
    _sparkData = tiles.map(s => ({ data: s.sparkline, on: !!s.online }));
    tiles.forEach((s, i) => { drawNeon(document.getElementById(`spark-${i}`), s.sparkline, !!s.online); _prev.aiTokens[s.name] = s.total_tokens; });
  } catch (e) {
    el.innerHTML = `<div class="empty">${loadErr('local AI usage', e)}</div>`;
    Object.assign(document.getElementById('statAiOnline'), { textContent: 'No reply', className: 'stat-num err', title: e.message });
  }
}

function timeAgoShort(ts) {
  const s = Math.max(0, Math.floor((Date.now() - ts * 1000) / 1000));
  if (s < 60) return s + 's ago';
  return Math.floor(s / 60) + 'm ago';
}

function renderTicker(entries) {
  const el = document.getElementById('tickerFeed');
  if (!entries.length) { el.innerHTML = '<span class="ticker-item">No calls since HomeShed last restarted.</span>'; return; }
  el.innerHTML = entries.slice(0, 8).map(e => {
    const risk = _prev.capRisk[e.id];
    const who = e.client ? `<span class="t-client" title="called with the ${escAttr(e.client)} client token">${escapeHtml(e.client)}</span>` : '';
    return `<span class="ticker-item">${who}<span class="${risk === 'write' ? 't-write' : 't-id'}">${e.id}</span> · ${e.duration_ms}ms · ${timeAgoShort(e.timestamp)}</span>`;
  }).join('<span class="ticker-item">·</span>');
}

async function loadActivity() {
  try {
    const r = await fetch('/api/activity');
    const entries = await r.json();
    if (!Array.isArray(entries)) {  // say why instead of leaving "Waiting…" on screen
      document.getElementById('tickerFeed').innerHTML = `<span class="ticker-item">${loadErr('recent calls', entries.error || 'unexpected reply')}</span>`;
      return;
    }
    _activityEntries = entries;
    window.HSCockpit && HSCockpit.ingest('activity', entries);
    window.OVL && OVL.ingest('activity', entries);  // the call tapes (pages-ovlower.js)
    renderTicker(entries); // always keep the ticker current, even on the first (bootstrap) poll
    computeRwCounts(entries); // counts/sub-labels always reflect full real history, every poll
    computeReasoningCounts(entries); // same idempotent recount, reasoning.* capabilities only

    if (_prev.lastActivityTs === 0) {
      // First poll: bootstrap the flash-tracking baseline silently. Without this, every entry
      // already in the 200-item history would flash at once on page load -- not real fresh
      // activity, just noise. Counts/labels above are already correct immediately either way.
      if (entries.length) _prev.lastActivityTs = Math.max(...entries.map(e => e.timestamp));
      return;
    }

    // Entries newest-first. Flash every one strictly newer than the last poll's newest --
    // real invocations that happened through the actual MCP interface, not a fake tick.
    const newOnes = entries.filter(e => e.timestamp > _prev.lastActivityTs).reverse();
    newOnes.forEach(e => {
      const row = document.getElementById('cap-' + e.id);
      const risk = _prev.capRisk[e.id];
      if (row) flashEl(row, risk === 'write' ? 'flash-write' : 'flash');
      pulseRw(risk === 'write' ? 'rwWrite' : 'rwRead');
    });
    newOnes.slice(-20).forEach((e, i) => setTimeout(() => Core.packet(_prev.capRisk[e.id]), i * 170));
    if (entries.length) _prev.lastActivityTs = Math.max(_prev.lastActivityTs, ...entries.map(e => e.timestamp));
  } catch (e) { /* activity feed is a nice-to-have, don't break the rest of the page over it */ }
}

// --- Token savings strip ----------------------------------------------------------
function fmtTok(n) {
  if (n == null || isNaN(n)) return '—';
  return n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(Math.round(n));
}

// Tokens saved as money (Settings > Control Panel > "Show tokens saved as money at"): what was kept out of Claude's
// context at the model's input price, what the local model wrote instead at its output price. Pounds first on a UK
// computer. null: tokens only.
function savedMoney(m, inTok, outTok) {
  if (!m) return null;
  const usd = (inTok * m.in + outTok * m.out) / 1e6;
  const fmt = (sym, v) => sym + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const both = [fmt('£', usd * m.gbp_per_usd), fmt('$', usd)];
  if (!(navigator.language || '').startsWith('en-GB')) both.reverse();
  return { text: '≈ ' + both.join(' · '), label: `at ${m.label} API prices`,
           tip: `What the same tokens would cost on the API: $${m.in} per million read, $${m.out} per million written. `
              + (m.approximate ? 'Pounds at an approximate rate (the daily rate could not be fetched).'
                               : `Pounds at the European Central Bank rate of ${m.rate_from.replace('ECB ', '')}.`) };
}
function showMoney(el, money) {
  el.hidden = !money;
  if (money) { el.textContent = `${money.text} ${money.label}`; el.title = money.tip; }
}

async function loadTokens() {
  try {
    const d = await (await fetch('/api/tokens')).json();
    window.HSCockpit && HSCockpit.ingest('tokens', d);
    const rtkOk = d.rtk && d.rtk.available;
    const rtk = rtkOk ? d.rtk.total_saved : 0;
    const est = d.estimate || { by_rule: {}, images_generated: 0 };
    applyPipelineCounts(d.estimate);
    if (d.estimate && d.estimate.capabilities) {
      _lifetimeCaps = d.estimate.capabilities;
      _lifetimeClients = d.estimate.clients || {};
      // The split needs each tool's risk. A page opened in a background tab skips loadAll until
      // it's shown, so fetch the tool list here if it hasn't landed yet.
      if (!Object.keys(_prev.capRisk).length) await loadCapabilities();
      loadActivity();
    }
    const gen = est.by_rule.generation || 0, read = est.by_rule.reading || 0, ret = est.by_rule.retrieval || 0;
    const local = (d.local_models || []).reduce((a, m) => a + (m.prompt_tokens || 0) + (m.output_tokens || 0), 0);
    const total = rtk + gen + read + ret;
    _sys.saved = total;
    renderHero();
    document.getElementById('tokTotal').textContent = '≈ ' + fmtTok(total);
    // Never a zero that looks measured (the prepper's rule): without RTK's own numbers, say why instead
    const rtkOff = !!(d.rtk && d.rtk.not_set_up);
    document.getElementById('tokSub').textContent = rtkOk
      ? `measured ${fmtTok(rtk)} + estimated ${fmtTok(gen + read + ret)} · ${d.rtk.total_commands} commands filtered`
      : `estimated ${fmtTok(gen + read + ret)}` + (rtkOff ? " · RTK isn't set up, so nothing is measured" : " · RTK's numbers aren't available right now");
    showMoney(document.getElementById('tokMoney'), savedMoney(d.money, rtk + read + ret, gen));
    document.getElementById('tokRtk').textContent = rtkOk ? fmtTok(rtk) : '—';
    document.getElementById('tokRtkSub').textContent = rtkOk ? `avg ${Math.round(d.rtk.avg_savings_pct)}% smaller`
      : rtkOff ? 'Not set up yet: RTK trims shell output before Claude reads it' : 'Not answering right now';
    document.getElementById('tokGen').textContent = fmtTok(gen);
    document.getElementById('tokLocal').textContent = fmtTok(local);
    const seg = [[rtk, 'var(--green)'], [gen, 'var(--cyan)'], [read, '#af52de'], [ret, '#ff9f0a']];
    if (window.OVL) OVL.ingest('tokparts', [[rtk, 'var(--green)', 'Output trimmed by RTK', false], [gen, 'var(--cyan)', 'Writing done locally', true],
      [read, '#af52de', 'Reading done locally', true], [ret, '#ff9f0a', 'Search instead of reading', true]].map(([v, color, label, est]) => ({ v, color, label, est })));
    else document.getElementById('tokBar').innerHTML = total
      ? seg.filter(([v]) => v > 0).map(([v, c]) => `<span style="width:${(v / total * 100).toFixed(2)}%;background:${c}"></span>`).join('')
      : '';
  } catch (e) {
    document.getElementById('tokSub').textContent = 'could not load: ' + e.message;
    window.HSCockpit && HSCockpit.ingest('tokens', {});
  }
}

// --- Clients live (Overview): who uses HomeShed, one row per caller ----------------------
// the owner 2026-09-28: "a little panel for multi-clients ... read / write, so we know the count, for that
// project". Reads/writes are all-time (usage counters: per client, and the owner token is the total
// minus every client); connections (a caller on one machine, last 15 min) and the live dot come from
// HomeShed's connection list.
// Release progress (repo.progress, 2026-09-30): what the agent preparing each project last reported. Owner only, so
// the card stays hidden until this runs; view-only logins never call it.
function progBar(label, pct, detail) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  return `<div class="prog-bar"><span>${label}</span><div class="ctx-bar"><i style="transform: scaleX(${p / 100})"></i></div>`
    + `<b>${p}%</b><span class="hint">${escapeHtml(detail)}</span></div>`;
}
const _progOpen = new Set(), _progGates = {};  // which projects show their gates, and each one's gates as fetched
async function loadProgress() {
  let d = null;
  try { d = await apiCall('GET', '/api/progress'); } catch (e) { /* shown below */ }
  const card = document.getElementById('progCard'), feed = document.getElementById('progFeed'),
    sub = document.getElementById('progSub'), orb = document.getElementById('progOrb');
  if (!card) return;
  const list = d && Array.isArray(d.projects) ? d.projects : null;
  card.hidden = !list;  // unreadable (tool server down, a view-only login, a demo without it): no card, no error
  window.HSCockpit && HSCockpit.ingest('progress', list);
  if (!list) return;
  if (!list.length) {
    orb.classList.add('idle'); sub.textContent = 'What your agents report';
    feed.innerHTML = '<div class="empty">No project has reported yet. An agent reports with repo.progress_update.</div>';
    return;
  }
  orb.classList.remove('idle');
  sub.textContent = list.length === 1 ? '1 project' : `${list.length} projects`;
  const outLink = (url, text) => /^https:\/\//.test(url || '')  // links the server has checked are https; open outside
    ? `<a href="${escAttr(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>` : '';
  feed.innerHTML = list.map(p => {
    const ready = /^(\d+) of (\d+)$/.exec(String((p.metrics || {}).readiness || ''));
    const waits = (p.waiting_on_owner || []).map(w => `<li><b>${escapeHtml(w.text)}</b>
        <span class="hint">(waiting ${agoText(w.since * 1000).replace(' ago', '')})</span>
        ${w.why ? `<div class="hint">Why: ${escapeHtml(w.why)}</div>` : ''}${w.how ? `<div class="hint">How: ${escapeHtml(w.how)}</div>` : ''}
        ${outLink(w.link, 'Open the page for this')}</li>`).join('');
    // the owner, 2026-10-01: "where the project is being made ... github repo name / link, and any useful info"
    const facts = [p.repo_url ? outLink(p.repo_url, 'GitHub: ' + p.repo) : '', p.visibility, p.branch && 'branch ' + p.branch,
      p.version, p.licence].filter(Boolean).map((x, i) => i === 0 && p.repo_url ? x : escapeHtml(x));
    const metrics = Object.entries(p.metrics || {}).filter(([k]) => k !== 'readiness')
      .map(([k, v]) => `<span>${escapeHtml(k.replace(/_/g, ' '))}: <b>${escapeHtml(v)}</b></span>`).join('');
    const id = escAttr(p.project);
    const rel = releaseState(p);
    if (rel && rel.mode === 'live') return shippedRow(p, rel);  // out: this card waits for the next version
    return `<div class="prog-row">
      <div class="prog-title"><b>${escapeHtml(p.title)}</b><span class="hint">${escapeHtml(p.agent || 'an agent')}, ${agoText(p.updated * 1000)}</span></div>
      ${window.OVL ? OVL.gates(p, ready) : ''}
      ${rel ? relMini(p, rel, outLink) : ''}
      ${facts.length ? `<div class="prog-facts">${facts.join(' · ')}</div>` : ''}
      ${rel ? '' : repoStats(p.stats) + trafficChart(p.stats)}
      ${p.folder ? `<div class="prog-facts">Made in <code>${escapeHtml(p.folder)}</code></div>` : ''}
      ${progBar('Release', p.percent, `${p.done} of ${p.total} gates`)}
      ${ready && +ready[2] ? progBar('Readiness', Math.round(100 * ready[1] / ready[2]), `${ready[1]} of ${ready[2]} checks`) : ''}
      ${p.next_step ? `<div class="prog-next"><b>Next:</b> ${escapeHtml(p.next_step)}</div>` : ''}
      ${waits ? `<div class="prog-wait">Waiting on you<ul>${waits}</ul></div>` : ''}
      ${metrics ? `<div class="prog-metrics">${metrics}</div>` : ''}
      ${p.note ? `<div class="hint">${escapeHtml(p.note)}</div>` : ''}
      <button type="button" class="ghost prog-gates-btn" id="progGatesBtn-${id}" aria-expanded="false" aria-controls="progGates-${id}"
        onclick="toggleProgGates('${id}', ${Number(p.updated) || 0})">Show the gates</button>
      <div class="prog-gates" id="progGates-${id}" hidden></div>
    </div>`;
  }).join('');
  const out = list.filter(p => releaseState(p));
  const relCard = document.getElementById('relCard');
  relCard.hidden = !out.length;
  document.getElementById('relSub').textContent = out.length === 1 ? `${out[0].stats.releases[0].tag} is out` : `${out.length} projects out`;
  document.getElementById('relFeed').innerHTML = out.map(p => releasedRow(p, p.stats.releases[0], outLink)).join('');
  list.forEach(p => _relSeen.add(p.project));
  list.filter(p => _progOpen.has(p.project)).forEach(p => showProgGates(p.project, p.updated));  // keep them open
}
// The owner, 2026-10-10: "the release progress panel is not 100% even though its released now ... handle the release
// differently". Released = the repo is public and GitHub has a release whose tag is the reported version. Then the
// row turns into the launch view; a newer version being prepped shows its progress with the live one beside it.
const _relSeen = new Set();  // rows already drawn once: their entrance animation plays only the first time
function releaseState(p) {
  const s = p.stats;
  if (!s || s.private || !(s.releases || []).length) return null;
  const norm = v => String(v || '').trim().replace(/^v/i, '');
  const shipped = p.version ? s.releases.find(r => norm(r.tag) === norm(p.version)) : s.releases[0];
  return shipped ? { mode: 'live', rel: shipped } : { mode: 'next', rel: s.releases[0] };
}
function relDate(iso) {
  const t = Date.parse(iso || '');
  return isNaN(t) ? null : { day: new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }), ago: agoText(t) };
}
function relMini(p, rel, outLink) {
  const d = relDate(rel.rel.published);
  const url = p.repo_url ? `${p.repo_url}/releases/tag/${encodeURIComponent(rel.rel.tag)}` : '';
  return `<div class="rel-mini"><i></i><b>${escapeHtml(rel.rel.tag)}</b> is live${d ? ` since ${escapeHtml(d.day)}` : ''}${url ? ' · ' + outLink(url, 'release notes') : ''}
    <span class="hint">· this card now follows ${escapeHtml(p.version || 'the next version')}</span></div>`;
}
// Release progress, once its version is out: nothing in progress until the next one starts (the owner's reset)
function shippedRow(p, rel) {
  const id = escAttr(p.project), tag = rel.rel.tag;
  const m = /^(v?)(\d+)\.(\d+)\.(\d+)/.exec(tag || '');
  const next = m ? `${m[1]}${m[2]}.${m[3]}.${+m[4] + 1}` : '';
  return `<div class="prog-row rel-done">
    <div class="prog-title"><b>${escapeHtml(p.title)}</b><span class="hint">${escapeHtml(p.agent || 'an agent')}, ${agoText(p.updated * 1000)}</span></div>
    <div class="rel-mini"><i></i><b>${escapeHtml(tag)}</b> is out<span class="hint">· shipped with ${p.done} of ${p.total} gates · it's in Released below</span></div>
    <div class="hint">Nothing in progress. Start the next release to track it here: it clears the old gates and waiting
      items, and the agent prepping it reports the new ones.</div>
    <form class="rel-next" onsubmit="event.preventDefault(); startNextRelease('${id}', this)">
      <input name="v" value="${escAttr(next)}" placeholder="Next version, e.g. v0.1.1" aria-label="Next version" maxlength="40">
      <button type="submit">Start the next release</button><span class="set-msg"></span></form>
  </div>`;
}
async function startNextRelease(project, form) {
  const msg = form.querySelector('.set-msg'), btn = form.querySelector('button');
  btn.disabled = true;
  try { await apiCall('POST', `/api/progress/${encodeURIComponent(project)}/next`, { version: form.v.value.trim() }); loadProgress(); }
  catch (e) { msg.className = 'set-msg bad'; msg.textContent = e.message; btn.disabled = false; }
}
function releasedRow(p, r, outLink) {
  const s = p.stats, d = relDate(r.published), id = escAttr(p.project);
  const enter = _relSeen.has(p.project) ? '' : ' enter';
  const relUrl = p.repo_url ? `${p.repo_url}/releases/tag/${encodeURIComponent(r.tag)}` : '';
  const chips = [
    p.repo_url && [p.repo_url, 'GitHub', p.repo],
    relUrl && [relUrl, 'Release', `${r.tag} · ${Number(r.downloads || 0).toLocaleString()} downloads`],
    s.pypi && [s.pypi.url, 'PyPI', `${s.pypi.package} ${s.pypi.version || ''}`],
  ].filter(Boolean).map(([url, k, v]) => /^https:\/\//.test(url)
    ? `<a class="rel-chip" href="${escAttr(url)}" target="_blank" rel="noopener noreferrer"><span>${k}</span>${escapeHtml(v)}<em aria-hidden="true">↗</em></a>` : '').join('');
  const older = (s.releases || []).slice(1);
  return `<div class="prog-row rel-row${enter}">
    <div class="rel-hero">
      <div class="rel-ring" aria-hidden="true">
        <svg viewBox="0 0 120 120"><defs><linearGradient id="relg-${id}" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="var(--ok)"/><stop offset="1" stop-color="var(--accent)"/></linearGradient></defs>
          <circle class="track" cx="60" cy="60" r="52"/><circle class="arc" cx="60" cy="60" r="52" pathLength="100" stroke="url(#relg-${id})"/></svg>
        ${window.OVL ? OVL.relBezel() : ''}
        <div class="rel-core"><b>${escapeHtml(r.tag)}</b><span>Live</span></div>
      </div>
      <div class="rel-info">
        <div class="rel-badge"><i></i>Released</div>
        <div class="rel-name">${escapeHtml(p.title)}</div>
        <div class="hint">${d ? `Published ${escapeHtml(d.day)} · ${escapeHtml(d.ago)}` : 'Published'}${p.licence ? ' · ' + escapeHtml(p.licence) : ''}</div>
        <div class="rel-chips">${chips}</div>
      </div>
    </div>
    ${repoStats(s)}${trafficChart(s)}
    ${older.length ? `<details class="rel-left"><summary>Earlier releases (${older.length})</summary><div class="rel-older">${older.map(o => {
      const od = relDate(o.published);
      return `<div><b>${escapeHtml(o.tag)}</b><span class="hint">${od ? escapeHtml(od.day) : ''}</span><span>${Number(o.downloads || 0).toLocaleString()} downloads</span></div>`;
    }).join('')}</div></details>` : ''}
  </div>`;
}
// Views and clones a day for the last 14 days: a small area chart, drawn from repo.stats' kept history
function trafficChart(s) {
  const days = (s && s.traffic && s.traffic.daily) || [];
  if (days.length < 2) return '';
  const W = 280, H = 56, max = Math.max(1, ...days.map(x => Math.max(x.views, x.clones)));
  const pts = k => days.map((x, i) => [(i / (days.length - 1)) * W, H - 4 - (x[k] / max) * (H - 10)]);
  const line = ps => ps.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('');
  const v = pts('views'), c = pts('clones');
  const quiet = days.every(x => !x.views && !x.clones);
  return `<div class="rel-chart"><div class="rel-chart-head"><span><i class="v"></i>Views</span><span><i class="c"></i>Clones</span>
      <span class="hint">last ${days.length} days${quiet ? ' · none yet (GitHub counts a few hours late)' : ''}</span></div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Views and clones a day, last ${days.length} days">
      <path class="area" d="${line(v)}L${W},${H}L0,${H}Z"/><path class="lv" d="${line(v)}"/><path class="lc" d="${line(c)}"/></svg></div>`
    + (window.OVL ? OVL.traffic(days) : '');  // the cockpit's trace of the same days (pages-ovlower.js)
}
// The owner, 2026-10-10: "stats on my dash from my github link" (repo.stats): GitHub, PyPI, and traffic with a token
function repoStats(s) {
  if (!s) return '';
  const n = v => v == null ? '–' : Number(v).toLocaleString();
  const t = s.traffic, py = s.pypi;
  const since = t && !isNaN(Date.parse(t.since)) ? new Date(t.since).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '';
  const tiles = [['Stars', n(s.stars), s.stars_week ? `+${s.stars_week} this week` : ''], ['Forks', n(s.forks), ''],
    ['Release downloads', n(s.release_downloads), (s.releases || [])[0] ? `${n(s.releases[0].downloads)} on ${s.releases[0].tag}` : ''],
    py ? ['PyPI downloads', py.week != null ? n(py.week) : '–', py.week != null ? `this week · ${n(py.month)} this month` : 'no figures yet'] : null,
    t ? ['Views', n(t.views_14d), `14 days · ${n(t.views_total)} since ${since}`] : null,
    t ? ['Clones', n(t.clones_14d), `14 days · ${n(t.clones_total)} since ${since}`] : null,
    ['Watching', n(s.watchers), `${n(s.open_issues)} open issues`]].filter(Boolean);
  const refs = t && (t.referrers || []).length ? `<div class="hint">Visitors came from: ${t.referrers.map(r => `${escapeHtml(r.from)} (${n(r.views)})`).join(', ')}</div>` : '';
  return `<div class="repo-stats">${tiles.map(([k, v, sub]) => `<div><span>${k}</span><b>${v}</b>${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</div>`).join('')}</div>
    ${refs}${s.traffic_note ? `<div class="hint">Views and clones: ${escapeHtml(s.traffic_note)}.</div>` : ''}`;
}
// "Show the gates": every gate with its state and evidence, fetched when opened, again only after a new report
function toggleProgGates(project, updated) {
  if (_progOpen.has(project)) {
    _progOpen.delete(project);
    const box = document.getElementById('progGates-' + project), btn = document.getElementById('progGatesBtn-' + project);
    if (box) box.hidden = true;
    if (btn) { btn.textContent = 'Show the gates'; btn.setAttribute('aria-expanded', 'false'); }
    return;
  }
  _progOpen.add(project);
  showProgGates(project, updated);
}
async function showProgGates(project, updated) {
  const box = document.getElementById('progGates-' + project), btn = document.getElementById('progGatesBtn-' + project);
  if (!box) return;
  box.hidden = false;
  if (btn) { btn.textContent = 'Hide the gates'; btn.setAttribute('aria-expanded', 'true'); }
  const cached = _progGates[project];
  if (cached && cached.updated === updated) { box.innerHTML = cached.html; return; }
  if (!cached) box.innerHTML = '<div class="loading">Loading…</div>';
  let d;
  try { d = await apiCall('GET', '/api/progress/' + encodeURIComponent(project)); }
  catch (e) { box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; return; }
  const label = { done: 'Done', doing: 'Doing', waiting: 'Waiting', blocked: 'Blocked', todo: 'To do' };
  const tone = { done: 'good', doing: 'info', waiting: 'warn', blocked: 'bad', todo: '' };
  const html = (d.steps || []).map(s => `<div class="prog-gate"><span class="pill ${tone[s.status] || ''}">${escapeHtml(label[s.status] || s.status)}</span>
      <div><div>${escapeHtml(s.title)}</div>${s.evidence ? `<div class="hint">${escapeHtml(s.evidence)}</div>` : ''}</div></div>`).join('')
    || '<div class="empty">No gates reported yet.</div>';
  _progGates[project] = { updated, html };
  if (_progOpen.has(project)) box.innerHTML = html;
}

let _lifetimeClients = null, _activityEntries = [], _clLast = {};
function splitRw(tools) {
  let r = 0, w = 0;
  for (const [id, n] of Object.entries(tools || {})) { if (_prev.capRisk[id] === 'write') w += n; else r += n; }
  return [r, w];
}
async function loadClientsLive() {
  let d;
  try { d = await (await fetch('/api/connections')).json(); } catch (e) { d = {}; }
  const feed = document.getElementById('clientsFeed'), sub = document.getElementById('clientsSub'), orb = document.getElementById('clientsOrb');
  if (!Array.isArray(d.connections)) { sub.textContent = "Can't reach HomeShed's connection list"; orb.classList.add('idle'); return; }
  const rows = {}, known = new Set(['owner', ...(d.clients || [])]);  // deleted clients keep history, not a row
  const row = name => rows[name] = rows[name] || { name, sessions: 0, hosts: [], last: 0, reads: null, writes: null, refused: 0 };
  d.connections.forEach(x => { if (!known.has(x.caller)) return; const r = row(x.caller); r.sessions++; r.hosts.push(x.host); r.last = Math.max(r.last, x.last); });
  _activityEntries.forEach(e => { if (!e.refused && known.has(e.client || 'owner')) { const r = row(e.client || 'owner'); r.last = Math.max(r.last, e.timestamp); } });
  if (_lifetimeClients && _lifetimeCaps && Object.keys(_prev.capRisk).length) {
    const ownerTools = {};
    for (const [id, c] of Object.entries(_lifetimeCaps)) ownerTools[id] = c.calls || 0;
    for (const [name, c] of Object.entries(_lifetimeClients)) {
      // every client's calls come off the owner's total, including deleted clients'
      for (const [id, n] of Object.entries(c.tools || {})) ownerTools[id] = Math.max(0, (ownerTools[id] || 0) - n);
      if (!known.has(name)) continue;
      const r = row(name);
      [r.reads, r.writes] = splitRw(c.tools);
      r.refused = c.refused || 0;
      r.last = Math.max(r.last, c.last || 0);
    }
    const o = row('owner');
    [o.reads, o.writes] = splitRw(ownerTools);
  }
  const list = Object.values(rows).sort((a, b) => (b.sessions - a.sessions) || (b.last - a.last));
  const live = list.filter(r => r.sessions);
  window.HSCockpit && HSCockpit.ingest('clients', list);
  const label = r => r.name === 'owner' ? 'You (owner token)' : r.name;
  document.getElementById('clConnected').textContent = live.length;
  document.getElementById('clSessions').textContent = d.connections.length;
  document.getElementById('clRefused').textContent = list.reduce((a, r) => a + r.refused, 0).toLocaleString();
  orb.classList.toggle('idle', !d.connections.some(x => Date.now() / 1000 - x.last < 90));
  sub.textContent = live.length ? `Connected: ${live.map(r => r.name === 'owner' ? 'you' : r.name).join(', ')}` : 'Nobody connected in the last 15 minutes';
  if (!list.length) { feed.innerHTML = '<div class="empty">No calls yet.</div>'; return; }
  const n = v => v == null ? '…' : v.toLocaleString();
  feed.innerHTML = list.map(r => {
    const fresh = _clLast[r.name] && r.last > _clLast[r.name];
    _clLast[r.name] = r.last;
    return `<div class="cl-row${fresh ? ' flash' : ''}">
      <span class="cl-name" title="${escAttr(label(r) + (r.hosts.length ? ', from ' + r.hosts.join(', ') : ''))}"><i class="${r.sessions ? 'live' : ''}"></i>${window.OVL ? OVL.split(r.reads, r.writes, r.reads == null ? 'Reads and writes not counted yet' : `${n(r.reads)} reads, ${n(r.writes)} writes`) : ''}<span>${escapeHtml(label(r))}</span></span>
      <span class="cl-num cl-sess"><b>${r.sessions}</b> ${r.sessions === 1 ? 'connection' : 'connections'}</span>
      <span class="cl-num"><b>${n(r.reads)}</b> reads</span>
      <span class="cl-num w"><b>${n(r.writes)}</b> writes</span>
      <span class="cl-last">${r.last ? agoText(new Date(r.last * 1000).toISOString()) : 'never'}</span>
    </div>`;
  }).join('');
}

// --- Claude context (Overview): each Claude Code session's context use -------------------------
// the owner 2026-09-28: "a memory compact monitor so you know when you will compact ... alarm at 90% ...
// live on the dash". Written by .claude/hooks/context-monitor.py (Claude Code's status line on the
// helper PC), served by its helper service. The bar's marker is the alarm from the Settings page.
// Plan usage (the plan limiter's figures): "Plan: 5-hour 62%, resets 03:31", amber from the warn
// level, red from the alarm level (Settings). Owner only: view-only logins don't get `plan`.
function renderPlanUsage(plan, settings) {
  const box = document.getElementById('ctxPlan');
  const wins = (plan && plan.windows) || {}, labels = (plan && plan.labels) || {};
  const warn = settings.plan_warn_pct || 75, alarm = settings.plan_alarm_pct || 90;
  const parts = ['five_hour', 'seven_day'].filter(k => wins[k] && wins[k].pct != null).map(k => {
    const w = wins[k], at = w.resets_at ? new Date(w.resets_at * 1000) : null;
    const soon = at && at - Date.now() < 86400000;
    const when = at ? at.toLocaleString('en-GB', soon ? { hour: '2-digit', minute: '2-digit' } : { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '';
    return `<span class="${w.pct >= alarm ? 'bad' : w.pct >= warn ? 'warn' : ''}">${escapeHtml(labels[k] || k)} <b>${Math.round(w.pct)}%</b>${when ? `, resets ${escapeHtml(when)}` : ''}</span>`;
  });
  box.hidden = !parts.length;
  box.innerHTML = parts.length ? 'Plan: ' + parts.join(' · ') : '';
}
async function loadContextLive() {
  let d;
  try { d = await (await fetch('/api/context')).json(); } catch (e) { d = {}; }
  const feed = document.getElementById('ctxFeed'), sub = document.getElementById('ctxSub'), orb = document.getElementById('ctxOrb'), card = document.getElementById('ctxCard');
  card.hidden = !!d.not_set_up;  // no context monitor on this install: no card (the prepper's clean-VM test)
  if (d.not_set_up) return;
  window.OVL && OVL.ingest('context', Object.assign({ viewer: _viewer }, d));  // the context reactor (pages-ovlower.js)
  if (!Array.isArray(d.sessions)) { sub.textContent = "Can't reach the context monitor on your PC"; orb.classList.add('idle'); return; }
  const alarm = (d.settings || {}).context_alarm_pct || 90, now = Date.now() / 1000;
  const live = d.sessions.filter(s => now - (s.updated || 0) < 900);
  const top = d.sessions.reduce((m, s) => Math.max(m, s.used_pct || 0), 0);
  const compactions = d.sessions.flatMap(s => s.compactions || []).filter(c => c.pct).sort((a, b) => b.at - a.at);
  const over = d.sessions.filter(s => (s.used_pct || 0) >= alarm);
  document.getElementById('ctxTop').textContent = d.sessions.length ? Math.round(top) + '%' : '—';
  document.getElementById('ctxAlarm').textContent = alarm + '%';
  document.getElementById('ctxCompact').textContent = compactions.length ? Math.round(compactions[0].pct) + '%' : 'not yet';
  orb.classList.toggle('idle', !live.length);
  card.classList.toggle('alarm', over.length > 0);
  renderPlanUsage(d.plan, d.settings || {});
  sub.textContent = over.length ? `${over.map(s => s.project).join(', ')} passed the ${alarm}% alarm: Claude was told to save memory`
    : live.length ? `${live.length} active ${live.length === 1 ? 'session' : 'sessions'}` : 'No Claude session active in the last 15 minutes';
  if (!d.sessions.length) { feed.innerHTML = '<div class="empty">No Claude sessions yet. They appear here once Claude Code shows its status line.</div>'; return; }
  const canCompact = !!d.can_compact && !_viewer;  // the helper can type "/compact" into a session's window
  // Auto-compact (the owner, 2026-10-01): the same steps by themselves, switched on here or in Settings. It starts at its
  // own level, always 2 points under the alarm (as the helper's auto_compact_pct), so the two never clash.
  const autoBox = document.getElementById('ctxAuto'), autoIn = document.getElementById('ctxAutoIn');
  const autoAt = Math.min((d.settings || {}).context_auto_compact_pct || 90, alarm - 2);
  autoBox.hidden = !canCompact;
  if (document.activeElement !== autoIn && !autoIn.disabled) autoIn.checked = !!(d.settings || {}).context_auto_compact;
  document.getElementById('ctxAutoTxt').textContent = `auto-compact at ${autoAt}%`;
  autoBox.title = `When a session passes ${autoAt}% and is between answers, it saves its memory, is compacted and carries `
    + `on, by itself, before the ${alarm}% alarm. At most once an hour per session.`;
  // Plan limits (the owner, 2026-10-02, on paid extra usage: "add a toggle for this weekly limit thing so i can turn it
  // off ... and a toggle next to auto compact. clearly labeled"). Shown once the PC's helper knows the setting.
  const st = d.settings || {}, limitsOn = st.plan_limits_on !== false;
  const limBox = document.getElementById('ctxLimits'), limIn = document.getElementById('ctxLimitsIn');
  limBox.hidden = _viewer || !('plan_limits_on' in st);
  if (document.activeElement !== limIn && !limIn.disabled) limIn.checked = limitsOn;
  document.getElementById('ctxLimitsTxt').textContent = limitsOn ? `plan limits: pause at ${st.plan_stop_pct || 98}%` : 'plan limits: off';
  limBox.title = 'On: near your plan limit Claude goes lean, wraps up, then pauses until the limit resets. Off: Claude keeps '
    + 'working past your limit (for paid extra usage). The same switch is on the Settings page, Claude plan usage.';
  feed.innerHTML = d.sessions.slice(0, 6).map((s, i) => {
    const pct = Math.max(0, Math.min(100, s.used_pct || 0));
    const armed = _ctxArm.sid === s.session && Date.now() < _ctxArm.until;
    // On every open session (the helper says which are open: an idle session's record can be old), "Busy" mid-turn.
    // While a compact runs, its step (one at a time: the helper refuses a second press until it has finished).
    const flow = s.compact_flow || null, step = flow && !flow.ended ? CTX_STEPS[flow.stage] : null;
    const busy = s.turn === 'busy' || s.turn === 'unknown';
    const off = busy || !!step || _ctxBusy.has(s.session);
    const act = !canCompact ? '' : `<span class="ctx-act">${s.alive && s.session
      ? `<button type="button" class="ghost${armed && !off ? ' armed' : ''}" ${off ? 'disabled' : ''}
          onclick="compactSession('${escAttr(s.session)}')" title="${step ? `Step ${step.n} of 3: ${step.what}`
            : busy ? "It's in the middle of something: Compact comes back when its answer has finished"
            : 'Save its memory, compact it, then tell it to carry on'}">${step ? step.label : _ctxBusy.has(s.session)
            ? 'Starting…' : busy ? 'Busy' : armed ? 'Sure?' : 'Compact'}</button>` : ''}</span>`;
    const cls = pct >= alarm ? 'bad' : pct >= alarm - 15 ? 'warn' : '';
    const last = (s.compactions || []).slice(-1)[0];
    const how = flow && flow.auto ? 'auto-compact' : 'compact';
    const detail = [step ? `${how}, step ${step.n} of 3: ${step.what}` : flow && flow.result ? `${how}: ${flow.result}` : '',
      s.model, s.window ? (s.window >= 1e6 ? `${+(s.window / 1e6).toFixed(1)}M window` : `${Math.round(s.window / 1000)}k window`) : '', last && last.pct ? `compacted at ${Math.round(last.pct)}%` : ''].filter(Boolean).join(' · ');
    return `<div class="ctx-row${canCompact ? ' with-act' : ''}" title="${escAttr(s.cwd || s.project || '')}">
      <span class="ctx-name"><b>${window.OVL ? OVL.ringMark(i) : ''}${escapeHtml(s.project || '?')}</b><span>${escapeHtml(detail)}</span></span>
      <span class="ctx-bar"><i class="${cls}" style="transform:scaleX(${(pct / 100).toFixed(3)})"></i><em style="left:${alarm}%"></em></span>
      <span class="ctx-pct">${Math.round(pct)}%</span>
      <span class="ctx-when">${agoText(new Date((s.updated || 0) * 1000).toISOString())}</span>
      ${act}
    </div>`;
  }).join('');
}
// Compact now (the owner, 2026-10-01: "how do i compact sessions memory"): press, then press Sure? within 6 s. The
// helper on the PC types into that session's own window, only between turns, in three steps: a save-your-memory prompt,
// "/compact" once that answer has finished, then a carry-on prompt once the compaction has. One at a time per session.
const CTX_STEPS = {
  saving: { n: 1, label: 'Saving…', what: 'saving its memory' },
  compacting: { n: 2, label: 'Compacting…', what: 'compacting' },
  continuing: { n: 3, label: 'Resuming…', what: 'being told to carry on' },
};
let _ctxArm = { sid: null, until: 0 };
const _ctxBusy = new Set();  // a press on its way to the helper: a double click doesn't send a second
async function setAutoCompact(el) {
  el.disabled = true;
  try {
    await apiCall('PUT', '/api/settings', { source: 'main-pc', key: 'context_auto_compact', value: el.checked });
    ctxNote(el.checked ? 'Auto-compact is on: a session past its level saves its memory, is compacted and carries on, '
      + "once it's between answers. The alarm only goes off for a session too busy for that." : 'Auto-compact is off.');
  } catch (e) { el.checked = !el.checked; ctxNote(e.message); }
  el.disabled = false;
}
async function setPlanLimits(el) {
  el.disabled = true;
  try {
    await apiCall('PUT', '/api/settings', { source: 'main-pc', key: 'plan_limits_on', value: el.checked });
    document.getElementById('ctxLimitsTxt').textContent = el.checked ? 'plan limits: on' : 'plan limits: off';
    ctxNote(el.checked ? 'Plan limits are on: near your limit Claude goes lean, wraps up, then pauses until it resets.'
      : 'Plan limits are off: Claude keeps working past your plan limit. Your usage still shows here and in the status line.');
  } catch (e) { el.checked = !el.checked; ctxNote(e.message); }
  el.disabled = false;
}
function ctxNote(text) {
  const el = document.getElementById('ctxNote');
  if (el) { el.textContent = text || ''; el.hidden = !text; }
}
async function compactSession(sid) {
  if (_ctxBusy.has(sid)) return;
  if (_ctxArm.sid !== sid || Date.now() > _ctxArm.until) {
    _ctxArm = { sid, until: Date.now() + 6000 };
    ctxNote('Press Sure? to compact it: it saves its memory first, is compacted, then carries on by itself. Check nothing is half-typed in that window: it would be sent too.');
    loadContextLive();
    return;
  }
  _ctxArm = { sid: null, until: 0 };
  _ctxBusy.add(sid);
  ctxNote('Starting…');
  loadContextLive();
  try {
    const d = await apiCall('POST', `/api/claude/sessions/${encodeURIComponent(sid)}/compact`);
    ctxNote(d.note || 'Asked it to save its memory first.');
  } catch (e) { ctxNote(e.message); }
  _ctxBusy.delete(sid);
  loadContextLive();
}

// Past sessions (the owner, 2026-09-29: "can this remember previous claude sessions? and launch them back in their
// terminal and locations from a click of a button?"). The GPU service's sessions.py lists them from Claude Code's
// own files and, when switched on in Settings, opens one in Windows Terminal in its own folder. Owner only.
// 2026-10-27 ("I always use the resume session buttons ... Can we remove chats?"): the first card on the Overview, with a
// search, and a remove on each row that only takes it off this list (the helper keeps the id; the file is never touched),
// with Undo, and a Hidden chats view to bring one back.
// As many rows as fit the chats window, measured (the window's size is fixed by its grid row, not by the list), so
// the list fills it with no empty block above Show all; 8 where nothing can be measured.
let _pastFit = 0;
function pastShown() {
  const list = document.getElementById('ctxPastList');
  const row = list && list.querySelector('.ov-row, .ov-skel');
  const foot = document.querySelector('.ov-resume-foot');
  // room for the Show all line when it isn't showing yet (it appears once there are more chats than rows)
  const h = list ? list.clientHeight - (foot && !foot.offsetHeight ? 30 : 0) : 0;
  if (h <= 0 || !row || !row.offsetHeight) return _pastFit || 8;
  return Math.max(3, Math.floor(h / row.offsetHeight));
}
if ('ResizeObserver' in window) new ResizeObserver(() => {
  const n = pastShown();
  if (n !== _pastFit) { _pastFit = n; if (_past.rows.length) renderPastSessions(); }
}).observe(document.getElementById('ctxPastList'));
const _past = { all: false, rows: [], on: false, can: true, q: '', hiddenView: false, hiddenRows: [], hiddenCount: 0, gone: new Set() };
async function loadPastSessions() {
  const box = document.getElementById('ovResume');
  let d, h = null;
  try {
    [d, h] = await Promise.all([apiCall('GET', '/api/claude/sessions?limit=40'),
      _past.hiddenView ? apiCall('GET', '/api/claude/sessions?hidden=1&limit=40').catch(() => null) : null]);
  } catch (e) {  // a view-only login: no window; no helper PC: the window says why (it keeps its place, no jump)
    if (_viewer) box.hidden = true;
    else { pastNote(e.message, true); document.getElementById('ctxPastList').innerHTML = ''; }
    return;
  }
  _past.rows = d.sessions || [];
  _past.on = !!d.resume_on;
  _past.can = d.can_resume !== false;
  _past.hiddenCount = d.hidden_count || 0;
  if (h) _past.hiddenRows = h.sessions || [];
  if (!_past.hiddenCount) _past.hiddenView = false;
  box.hidden = false;
  pastNote();
  renderPastSessions();
}
function pastNote(text, bad) {
  const el = document.getElementById('ctxPastNote');
  el.classList.toggle('bad', !!bad);
  if (text != null) { el.textContent = text; return; }
  el.innerHTML = _past.hiddenView ? 'Chats you removed from this list. Their files are still on your PC: bring one back to resume it here.'
    : !_past.can ? "Claude Code isn't installed on your PC for this user, so chats can't be reopened from here."
    : _past.on ? '' : 'To reopen a chat from here, switch on <a href="#" onclick="openSessionSetting(); return false;">Reopen past sessions</a> in Settings, under Claude.';
}
function openSessionSetting() { openSettingsAt('Claude context'); }  // a section, not a group
function pastFolder(f) {  // the last two folders: "Projects\dashboard", not the whole path
  const parts = String(f || '').replace(/[\\/]+$/, '').split(/[\\/]/).filter(Boolean);
  return parts.length > 2 ? '…\\' + parts.slice(-2).join('\\') : String(f || '');
}
function pastMark(text, q) {  // the search's match, highlighted (escaped first: nothing from the file is ever HTML)
  const t = escapeHtml(text);
  if (!q) return t;
  const at = String(text).toLowerCase().indexOf(q);
  return at < 0 ? t : escapeHtml(text.slice(0, at)) + '<mark>' + escapeHtml(text.slice(at, at + q.length)) + '</mark>' + escapeHtml(text.slice(at + q.length));
}
const PLAY_SVG = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 1.8v8.4c0 .5.6.8 1 .5l6.3-4.2a.6.6 0 0 0 0-1L4 1.3c-.4-.3-1 0-1 .5Z"/></svg>';
const X_SVG = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="m2.5 2.5 7 7m0-7-7 7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
function renderPastSessions() {
  const q = _past.q.trim().toLowerCase(), hv = _past.hiddenView;
  const src = (hv ? _past.hiddenRows : _past.rows).filter(s => !_past.gone.has(s.id));
  const hits = q ? src.filter(s => [s.name, s.title, s.folder].some(v => String(v || '').toLowerCase().includes(q))) : src;
  const shown = _pastFit = pastShown();
  const rows = q || _past.all || hv ? hits : hits.slice(0, shown);
  document.getElementById('ovResumeCount').textContent = hv ? `${src.length} hidden` : src.length ? `${src.length} recent` : '';
  document.getElementById('ovResumeTitle').textContent = hv ? 'Hidden chats' : 'Pick up where you left off';
  document.getElementById('ovSearch').hidden = !hv && src.length <= shown && !q;
  document.getElementById('ctxPastList').innerHTML = rows.map(s => {
    const when = agoText(new Date(s.last_active * 1000).toISOString());
    const sub = [s.title && s.title !== s.name ? s.title : '', pastFolder(s.folder)].filter(Boolean);
    const go = hv ? `<button type="button" class="ov-go ghosty" onclick="setPastHidden('${escAttr(s.id)}', false, this)">Bring back</button>`
      : s.open ? `<span class="ctx-open${s.status === 'busy' ? ' busy' : ''}" title="Open in a window now. ${s.status === 'busy' ? 'Working on something' : 'Waiting for you'}: switch to its window">${s.status === 'busy' ? 'Working' : 'Open'}</span>`
      : !_past.on || !_past.can ? '<span></span>'
      : `<button type="button" class="ov-go" data-id="${escAttr(s.id)}" onclick="resumeSession(this)"${s.folder_ok ? '' : ` disabled title="Its folder isn't there any more"`}
          aria-label="Resume ${escAttr(s.name)}">${PLAY_SVG}Resume</button>`;
    const x = hv ? '<span></span>' : `<button type="button" class="ov-x" onclick="setPastHidden('${escAttr(s.id)}', true, this)"
        title="Remove from this list (the chat itself is kept)" aria-label="Remove ${escAttr(s.name)} from this list">${X_SVG}</button>`;
    const letter = (String(s.folder || s.name).replace(/[\\/]+$/, '').split(/[\\/]/).pop() || s.name || '?').replace(/[^A-Za-z0-9]/g, '').charAt(0).toUpperCase() || '?';
    return `<div class="ov-row" role="listitem" data-id="${escAttr(s.id)}" title="${escAttr(s.folder)}">
      <span class="ov-av${s.open ? ' open' : ''}" aria-hidden="true">${escapeHtml(letter)}</span>
      <span class="ov-name"><b>${pastMark(s.name, q)}</b><span><em class="ov-when-m">${escapeHtml(when)} · </em>${sub.map(v => pastMark(v, q)).join(' · ')}</span></span>
      <span class="ov-when">${escapeHtml(when)}</span>${go}${x}</div>`;
  }).join('') || `<div class="ov-empty">${q ? `No chat matches “${escapeHtml(_past.q.trim())}”.`
    : hv ? 'No hidden chats.' : 'No past chats yet. Each Claude Code session on your PC shows up here, ready to resume.'}</div>`;
  const more = document.getElementById('ovMore'), hid = document.getElementById('ovHiddenBtn');
  more.hidden = hv || !!q || hits.length <= shown;
  more.textContent = _past.all ? 'Show fewer' : `Show all ${hits.length}`;
  hid.hidden = !hv && !_past.hiddenCount;
  hid.textContent = hv ? 'Back to recent chats' : `Hidden chats (${_past.hiddenCount})`;
}
function togglePastHidden() {
  _past.hiddenView = !_past.hiddenView;
  _past.q = ''; document.getElementById('ovSearch').value = '';
  if (!_past.hiddenView) { pastNote(); renderPastSessions(); return; }
  _past.hiddenRows = [];
  pastNote();
  renderPastSessions();
  loadPastSessions();
}
// Remove (hide) and bring back. The row goes at once; Undo in the toast brings it straight back.
let _toastTimer = 0;
function ovToast(text, undo) {
  const el = document.getElementById('ovToast');
  clearTimeout(_toastTimer);
  el.innerHTML = `<span>${escapeHtml(text)}</span>${undo ? '<button type="button">Undo</button>' : ''}`;
  if (undo) el.querySelector('button').onclick = () => { el.hidden = true; undo(); };
  el.hidden = false;
  _toastTimer = setTimeout(() => { el.hidden = true; }, undo ? 7000 : 3500);
}
async function setPastHidden(sid, hide, btn) {
  const row = btn && btn.closest('.ov-row'), all = [..._past.rows, ..._past.hiddenRows];
  const name = (all.find(s => s.id === sid) || {}).name || 'That chat';
  if (btn) btn.disabled = true;
  if (row) row.classList.add('leaving');
  _past.gone.add(sid);
  try {
    await apiCall('POST', `/api/claude/sessions/${encodeURIComponent(sid)}/${hide ? 'hide' : 'unhide'}`);
    _past.hiddenCount = Math.max(0, _past.hiddenCount + (hide ? 1 : -1));
    if (hide) ovToast(`Removed “${name}” from the list.`, () => setPastHidden(sid, false));
    else ovToast(`“${name}” is back in the list.`);
  } catch (e) {
    pastNote(e.message, true);
    if (btn) btn.disabled = false;
  }
  setTimeout(async () => { await loadPastSessions(); _past.gone.delete(sid); renderPastSessions(); }, row ? 260 : 0);
}
async function resumeSession(btn) {
  btn.disabled = true;
  btn.textContent = 'Opening…';
  try {
    const r = await apiCall('POST', `/api/claude/sessions/${encodeURIComponent(btn.dataset.id)}/resume`);
    if (r.queued) {  // several at once take turns, a few seconds apart, so the PC isn't hit by them all together
      btn.textContent = `Queued (${r.position})`;
      btn.title = `Opens in about ${r.opens_in_s}s`;
      pastNote(`Queued: number ${r.position}, it opens in about ${r.opens_in_s}s in ${r.terminal}, in ${r.folder}.`);
      setTimeout(() => { pastNote(); loadPastSessions(); }, Math.max(6000, (r.opens_in_s + 4) * 1000));
      return;
    }
    btn.textContent = 'Opened';
    btn.title = `Opened in ${r.terminal} on your PC`;
    pastNote(`Opened in ${r.terminal} on your PC, in ${r.folder}. Claude waits for you to type.`);
    setTimeout(() => { pastNote(); loadPastSessions(); }, 6000);
  } catch (e) {
    btn.innerHTML = PLAY_SVG + 'Resume';
    btn.disabled = false;
    pastNote(e.message, true);
  }
}

function openRepoStats() {
  const card = ['relCard', 'progCard'].map(id => document.getElementById(id)).find(c => c && !c.hidden);
  if (!card) return;
  card.classList.remove('collapsed');
  card.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash');
}
// A group heading shows only while one of its cards does.
function syncOverview() {
  document.querySelectorAll('#opsView > .ov-group').forEach(h => {
    let el = h.nextElementSibling, any = false;
    while (el && !el.classList.contains('ov-group')) { if (!el.hidden) any = true; el = el.nextElementSibling; }
    h.hidden = !any;
  });
}
