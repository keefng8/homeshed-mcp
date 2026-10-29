/* HomeShed start-up ("Awakening the HomeShed Core", the owner's brief of 2026-10-27).
   A cinematic core plays over the page while the Control Panel really starts, and every word on it comes from that
   start: the page's own first requests (/api/me, /api/health, /api/capabilities, /api/memory, /api/local-ai,
   /api/ui-settings, /api/activity) are watched as they come back, nothing is asked twice and nothing is invented.
   The decoration runs on its own clock and never holds the page: when the first data is in, the panel opens at once.

   The page uses it like this (panel-start.js, and panel.js's loadAll):
     HomeShedBoot.mount({ enter: finishBoot, retry: () => loadAll() })   once its own script has started
     HomeShedBoot.loaded()                                                at the end of every loadAll()
   After the hand-off only a compact connection indicator stays (ConnectionRecovery): no replay, no animation frame.

   Parts (one file, one set of tokens): HomeShedCore (the live-core mark), OrbitalRings, EnergyParticles,
   SystemTelemetry (the six readouts), BootStatus (state and stage line), ConnectionRecovery. */
(function () {
  'use strict';
  const root = document.getElementById('hsBoot');

  // ---- shared tokens: radii in the core's 1000-unit square, ring speeds in seconds per turn, timings in ms ----
  const T = {
    r: { hud: 488, ticks: 457, a: 405, b: 360, flow: 300, out: 250, in: 171, core: 75, node: 14 },
    spin: { a: 96, b: 64, nodes: 48, flow: 7.5, pulse: 15 },
    openAfter: 2500,   // "Open the panel" shows when start-up isn't done by then
    timeout: 20000,    // then it says start-up is taking too long, and which part it's waiting for
    degradedHold: 2600, // a degraded start shows what's off for this long, then opens (unless the person is choosing)
    exit: 620,
  };
  const C = 500;
  const rad = d => d * Math.PI / 180;
  const f1 = n => n.toFixed(1);
  const pt = (r, deg) => [C + r * Math.cos(rad(deg)), C + r * Math.sin(rad(deg))];
  const arc = (r, a0, a1) => {
    const [x0, y0] = pt(r, a0), [x1, y1] = pt(r, a1);
    return `M${f1(x0)} ${f1(y0)}A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${f1(x1)} ${f1(y1)}`;
  };
  const svg = (cls, body) => `<svg class="${cls}" viewBox="0 0 1000 1000" aria-hidden="true" focusable="false">${body}</svg>`;
  const layer = (cls, body, spin) => `<div class="hb-l ${cls}"><div class="hb-in"><div class="hb-spin${spin ? ' ' + spin : ''}">${svg('', body)}</div></div></div>`;
  const ring = (r, cls, extra) => `<circle cx="${C}" cy="${C}" r="${r}" class="${cls}"${extra || ''}/>`;

  // ---- HomeShedCore: the live-core mark (brand/live-core: a lit core, two rings, four nodes, an activity arc) ----
  function HomeShedCore() {
    // the activity arc, 96° to 176° on the outer ring: its head bright, its tail fading (the logo's own comet)
    let comet = '';
    for (let i = 0, n = 20; i < n; i++) {
      const t = i / n, a0 = 96 + t * 80, a1 = a0 + 80 / n + .6;
      comet += `<path d="${arc(T.r.out, a0, a1)}" class="hb-comet" style="stroke-opacity:${f1(Math.pow(1 - t, 1.4) * 10) / 10}"/>`;
    }
    const [hx, hy] = pt(T.r.out, 96);
    const mark = ring(T.r.out, 'hb-s hb-mark-out', ' pathLength="1"') + ring(T.r.in, 'hb-s hb-mark-in', ' pathLength="1"')
      + `<g class="hb-comets"><path d="${arc(T.r.out, 96, 140)}" class="hb-comet-glow"/>${comet}`
      + `<circle cx="${f1(hx)}" cy="${f1(hy)}" r="34" fill="url(#hbSprite)"/><circle cx="${f1(hx)}" cy="${f1(hy)}" r="6.5" class="hb-hot"/></g>`;
    // the nodes on the inner ring (as the logo: -82°, 8°, 98°, 188°): green, the orange spark, an ice highlight, green
    const kinds = ['n-neon', 'n-orange', 'n-ice', 'n-text'];
    const nodes = [-82, 8, 98, 188].map((a, i) => {
      const [x, y] = pt(T.r.in, a);
      return `<g class="hb-node ${kinds[i]}" style="--i:${i}"><circle cx="${f1(x)}" cy="${f1(y)}" r="40" class="hb-node-glow"/>`
        + `<circle cx="${f1(x)}" cy="${f1(y)}" r="${T.r.node}" class="hb-node-dot"/></g>`;
    }).join('');
    const heart = `<circle cx="${C}" cy="${C}" r="185" fill="url(#hbHalo)"/>`
      + `<circle cx="${C}" cy="${C}" r="${T.r.core}" fill="url(#hbSphere)" class="hb-sphere"/>`
      + `<ellipse cx="${C - 22}" cy="${C - 36}" rx="27" ry="17" class="hb-shine"/>`
      // the machined bezel round the core: a dark titanium band, a lit inner edge, fine index marks
      + ring(112, 'hb-bezel') + ring(104, 'hb-s hb-bezel-edge') + ring(121, 'hb-s hb-bezel-out')
      + ring(112, 'hb-bezel-ticks', ` style="stroke-dasharray:1.4 ${f1(2 * Math.PI * 112 / 36 - 1.4)}"`);
    return layer('hb-heart', heart, 'hb-breath') + layer('hb-mark', mark) + layer('hb-nodes', nodes, 'hb-turn-nodes');
  }

  // ---- OrbitalRings: calibration ring and ticks (still), two engineered rings turning at different speeds ----
  function OrbitalRings() {
    const brackets = [45, 135, 225, 315].map(a => {
      const p = (r, d) => pt(r, a + d).map(f1).join(' ');
      return `<path d="M${p(462, -7)}L${p(478, -7)}A478 478 0 0 1 ${p(478, 7)}L${p(462, 7)}" class="hb-s hb-bracket"/>`;
    }).join('');
    const cross = [0, 90, 180, 270].map(a => {
      const [x0, y0] = pt(272, a), [x1, y1] = pt(326, a);
      return `<line x1="${f1(x0)}" y1="${f1(y0)}" x2="${f1(x1)}" y2="${f1(y1)}" class="hb-s hb-cross"/>`;
    }).join('');
    const [ox, oy] = pt(497, 338);  // one orange index mark: the small asymmetry
    const hud = ring(T.r.hud, 'hb-s hb-faint', ' pathLength="1"') + ring(430, 'hb-s hb-dots') + brackets + cross
      + `<circle cx="${f1(ox)}" cy="${f1(oy)}" r="5" class="hb-index"/>`;
    const circ = 2 * Math.PI * T.r.ticks, circM = 2 * Math.PI * 455;
    const ticks = ring(T.r.ticks, 'hb-tick', ` style="stroke-dasharray:1.6 ${f1(circ / 120 - 1.6)}"`)
      + ring(455, 'hb-tick hb-tick-major', ` style="stroke-dasharray:3 ${f1(circM / 12 - 3)};stroke-dashoffset:1.5"`);
    // ring A: segmented, three orbital nodes; drawn twice (wide and faint, then thin) for the glow
    const seg = [.30, .012, .08, .012, .004, .012, .22, .03, .10, .012, .17, .048].join(' ');
    const aNodes = [22, 148, 262].map(a => {
      const [x, y] = pt(T.r.a, a);
      return `<circle cx="${f1(x)}" cy="${f1(y)}" r="22" fill="url(#hbSprite)"/><circle cx="${f1(x)}" cy="${f1(y)}" r="4.6" class="hb-orb"/>`;
    }).join('');
    const ringA = ring(T.r.a, 'hb-s hb-a-glow', ` pathLength="1" style="stroke-dasharray:${seg}"`)
      + ring(T.r.a, 'hb-s hb-a', ` pathLength="1" style="stroke-dasharray:${seg}"`) + aNodes;
    const ringB = ring(T.r.b, 'hb-s hb-b') + ring(348, 'hb-tick hb-ribbon')
      + `<path d="${arc(T.r.b, 200, 214)}" class="hb-spark-seg-glow"/><path d="${arc(T.r.b, 200, 214)}" class="hb-spark-seg"/>`
      + [20, 110].map(a => { const [x, y] = pt(T.r.b, a); return `<rect x="${f1(x - 4)}" y="${f1(y - 4)}" width="8" height="8" class="hb-sq"/>`; }).join('');
    // the one-off orange runs of the assembly: they travel once along the paths, then they're gone
    const runs = ['hb-glow-pass', ''].map(g => ring(T.r.a, `hb-run hb-run-a ${g}`, ' pathLength="1"')
      + ring(T.r.in, `hb-run hb-run-b ${g}`, ' pathLength="1"')).join('');
    return layer('hb-hud hb-outer', hud) + layer('hb-ticks hb-outer', ticks) + layer('hb-ring-b hb-outer', ringB, 'hb-turn-b')
      + layer('hb-ring-a hb-outer', ringA, 'hb-turn-a') + layer('hb-runs', runs);
  }

  // ---- EnergyParticles: information moving round two orbits, and a pulse round ring A ----
  function EnergyParticles() {
    let inner = '';
    for (let i = 0; i < 8; i++) {
      const [x, y] = pt(T.r.flow, -i * 3.4);
      inner += `<circle cx="${f1(x)}" cy="${f1(y)}" r="${f1(4.2 - i * .4)}" class="hb-p" style="opacity:${f1(1 - i * .12)}"/>`;
    }
    for (let i = 0; i < 3; i++) {
      const [x, y] = pt(T.r.flow, 180 - i * 4);
      inner += `<circle cx="${f1(x)}" cy="${f1(y)}" r="${f1(2.6 - i * .5)}" class="hb-p" style="opacity:${f1(.55 - i * .15)}"/>`;
    }
    const [hx, hy] = pt(T.r.a, 0);
    const pulse = [[40, .12, 5], [20, .35, 3], [8, .9, 2.4]].map(([len, op, w]) =>
      `<path d="${arc(T.r.a, -len, 0)}" class="hb-pulse" style="stroke-opacity:${op};stroke-width:${w}"/>`).join('')
      + `<circle cx="${f1(hx)}" cy="${f1(hy)}" r="26" fill="url(#hbSprite)"/><circle cx="${f1(hx)}" cy="${f1(hy)}" r="3.6" class="hb-hot"/>`;
    return layer('hb-flow hb-outer', inner, 'hb-turn-flow') + layer('hb-flow hb-outer', pulse, 'hb-turn-pulse');
  }

  // ---- the backdrop: depth, a barely visible grid and a distant network (still: drawn once, never redrawn) ----
  function backdrop() {
    let s = 7;
    const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
    const pts = [];
    while (pts.length < 34) {
      const x = rnd() * 1600, y = rnd() * 900;
      if (((x - 800) / 520) ** 2 + ((y - 450) / 360) ** 2 > 1) pts.push([x, y]);
    }
    let lines = '', dots = '';
    const clear = (x, y) => ((x - 800) / 560) ** 2 + ((y - 450) / 400) ** 2 > 1;  // nothing crosses the core
    pts.forEach((p, i) => {
      pts.map((q, j) => [Math.hypot(p[0] - q[0], p[1] - q[1]), j]).filter(d => d[1] > i && d[0] < 330).sort((a, b) => a[0] - b[0]).slice(0, 2)
        .filter(([, j]) => clear((p[0] + pts[j][0]) / 2, (p[1] + pts[j][1]) / 2))
        .forEach(([, j]) => { lines += `M${p[0] | 0} ${p[1] | 0}L${pts[j][0] | 0} ${pts[j][1] | 0}`; });
      dots += `<circle cx="${p[0] | 0}" cy="${p[1] | 0}" r="${i % 7 === 0 ? 2.6 : 1.6}" class="${i % 7 === 0 ? 'hb-net-hot' : 'hb-net-dot'}"/>`;
    });
    return `<div class="hb-bg" aria-hidden="true"><div class="hb-depth"></div><div class="hb-grid"></div>`
      + `<svg class="hb-net" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">`
      + `<path d="${lines}" class="hb-net-line"/>${dots}</svg><div class="hb-vignette"></div></div>`;
  }

  // ---- SystemTelemetry: six readouts, each bound to one real request ----
  const SYSTEMS = [
    ['me', 'Sign-in', 'left'], ['mcp', 'Tool server', 'left'], ['caps', 'Tools', 'left'],
    ['settings', 'Settings', 'right'], ['memory', 'Memory', 'right'], ['models', 'AI models', 'right'],
  ];
  function SystemTelemetry(side) {
    return `<ul class="hb-ro-col hb-ro-${side}" aria-label="Start-up checks">` + SYSTEMS.filter(s => s[2] === side).map(([k, label], i) =>
      `<li class="hb-ro" data-k="${k}" data-s="wait" style="--i:${i + (side === 'right' ? 3 : 0)}"><span class="hb-ro-txt">`
      + `<span class="hb-ro-l">${label}</span><span class="hb-ro-v">Checking</span></span><i class="hb-lead" aria-hidden="true"></i></li>`).join('') + '</ul>';
  }

  // ---- BootStatus: the state, the stage line (read out by screen readers) and the way out ----
  function BootStatus() {
    return `<div class="hb-lock"><div class="hb-word">HOMESHED</div><div class="hb-sub">MCP INFRASTRUCTURE</div></div>`
      + `<div class="hb-text"><div class="hb-phase">INITIALISING INTELLIGENCE</div>`
      + `<p class="hb-stage" role="status" aria-live="polite" aria-atomic="true"><span class="hb-state"><i></i><span class="hb-state-w">Starting</span></span>`
      + `<span class="hb-stage-t">Starting the Control Panel…</span></p>`
      + `<div class="hb-meta"><span class="hb-wave" aria-hidden="true"><svg viewBox="0 0 240 20" preserveAspectRatio="none">`
      + `<path d="${wave()}"/></svg></span><span class="hb-elapsed" aria-hidden="true"></span></div>`
      + `<div class="hb-actions"><button type="button" class="hb-btn hb-retry" hidden>Retry</button>`
      + `<button type="button" class="hb-btn hb-open">Open the panel</button></div></div>`;
  }
  function wave() {  // two identical periods side by side, so sliding by half loops without a seam
    let d = 'M0 10';
    for (let x = 0; x <= 240; x += 2) {
      const u = (x % 120) / 120 * Math.PI * 2;
      d += `L${x} ${f1(10 - Math.sin(u * 3) * 3.2 * Math.sin(u / 2) ** 2 - Math.sin(u * 7) * .8)}`;
    }
    return d;
  }

  const defs = `<svg class="hb-defs" width="0" height="0" aria-hidden="true" focusable="false"><defs>`
    + `<radialGradient id="hbSprite"><stop offset="0" class="hb-sp0"/><stop offset=".35" class="hb-sp1"/><stop offset="1" class="hb-sp2"/></radialGradient>`
    + `<radialGradient id="hbHalo"><stop offset=".3" class="hb-ha0"/><stop offset=".62" class="hb-ha1"/><stop offset="1" class="hb-ha2"/></radialGradient>`
    + `<radialGradient id="hbSphere" cx=".42" cy=".38" r=".7"><stop offset="0" class="hb-sph0"/><stop offset=".45" class="hb-sph1"/>`
    + `<stop offset="1" class="hb-sph2"/></radialGradient></defs></svg>`;

  // ---- the watched start-up: what each request said, straight from the page's own fetches ----
  const WATCH = { '/api/health': 'health', '/api/me': 'me', '/api/capabilities': 'caps', '/api/memory': 'memory',
    '/api/local-ai': 'models', '/api/ui-settings': 'settings', '/api/activity': 'activity' };
  const got = {};      // key -> { ok, data, status, net }
  let pending = {}, booting = !!root, loadedOnce = false, entered = false, timedOut = false, retrying = false, holding = false;
  let opts = null, t0 = performance.now(), timers = [], onKey = null, onVis = null, ro = null, tick = null, chosen = false;
  const nativeFetch = window.fetch;
  function keyOf(input) {
    try { return WATCH[new URL(typeof input === 'string' ? input : input && input.url, location.href).pathname] || null; }
    catch (e) { return null; }
  }
  function watchedFetch(input) {
    const p = nativeFetch.apply(this, arguments);
    const k = keyOf(input);
    if (k && (booting || k === 'health')) {
      pending[k] = (pending[k] || 0) + 1;
      p.then(r => r.status === 401 ? { ok: false, status: 401 }
        : r.ok ? r.clone().json().then(d => ({ ok: true, data: d }), () => ({ ok: false, status: r.status }))
          : { ok: false, status: r.status }, () => ({ ok: false, net: true }))
        .then(res => { pending[k]--; got[k] = res; (booting ? render : Recovery.health)(k, res); });
    }
    return p;
  }
  if (nativeFetch) window.fetch = watchedFetch;

  // health, in the page's own terms (pages-overview.js, loadHealth): ok / not set up (grey, never red) / set up and down
  const mcpOf = h => h.mcp_server === 'ok' ? 'ok' : !h.mcp_server || h.mcp_server === 'unknown' ? 'off' : 'bad';
  function readout(k) {
    const r = got[k], h = got.health;
    if (k === 'mcp') {
      if (!h) return pending.health ? ['wait', 'Reaching it'] : ['wait', 'Waiting'];
      if (!h.ok) return h.status === 401 ? ['bad', 'Signed out'] : ['bad', h.net ? 'Panel offline' : `Panel error ${h.status}`];
      const s = mcpOf(h.data);
      return s === 'ok' ? ['ok', 'Answering'] : s === 'off' ? ['off', 'Not checked'] : ['bad', 'Not answering'];
    }
    if (!r) return ['wait', 'Checking'];
    if (k === 'me') return r.ok ? ['ok', r.data.role === 'viewer' ? 'View only' : 'Owner'] : ['off', 'Unknown'];
    if (k === 'settings') return r.ok ? ['ok', r.data.animations === false ? 'Loaded, still' : 'Loaded'] : ['warn', 'Defaults'];
    if (k === 'caps') return r.ok && Array.isArray(r.data) ? ['ok', `${r.data.length} ready`] : ['warn', "Didn't load"];
    if (k === 'memory') {
      const mc = h && h.ok ? h.data.memory_core : '';
      if (mc === 'not-set-up' || (r.ok && r.data.not_set_up)) return ['off', 'Not set up'];
      if (mc === 'built-in' && mcpOf(h.data) !== 'ok') return ['warn', 'Needs tool server'];  // it lives in the tool server
      if (mc && mc !== 'ok' && mc !== 'built-in' && mc !== 'unknown') return ['warn', 'Not answering'];
      if (!r.ok || !Array.isArray(r.data.categories)) return ['warn', "Didn't load"];
      const n = r.data.categories.reduce((s, c) => s + (c.count || 0), 0);
      return ['ok', `${n.toLocaleString()} facts`];
    }
    if (k === 'models') {
      if (!r.ok) return ['warn', "Didn't load"];
      const tiles = (Array.isArray(r.data.servers) ? r.data.servers : []).filter(s => !s.hidden);
      if (!tiles.length) return ['off', 'None set up'];
      const on = tiles.filter(s => s.online).length;
      return [on === tiles.length ? 'ok' : on ? 'warn' : 'bad', `${on} of ${tiles.length} online`];
    }
    return ['wait', 'Checking'];
  }

  // the overall state, and the one line that says what's really happening
  const STAGES = [['health', 'Establishing connections…'], ['me', 'Verifying permissions…'], ['caps', 'Loading capabilities…'],
    ['memory', 'Opening shared memory…'], ['models', 'Checking the AI models…'], ['settings', 'Loading your settings…'],
    ['activity', 'Loading recent activity…']];
  function assess() {
    const h = got.health;
    const probs = [];
    if (h && !h.ok && h.status === 401) return { state: 'failed', line: 'Your sign-in has ended. Opening the sign-in page…' };
    if (h && !h.ok) return { state: 'failed', line: h.net ? "The Control Panel server didn't answer. Check that it's running, then retry."
      : `The Control Panel server answered with error ${h.status}. Retry in a moment.` };
    if (h && mcpOf(h.data) === 'bad') return { state: 'failed',
      line: `The tool server isn't answering (${h.data.mcp_server}). Tools, memory and models need it. The panel keeps checking.` };
    if (timedOut && !loadedOnce) {
      const waiting = STAGES.filter(([k]) => pending[k] && !got[k]).map(([k]) => SYSTEMS.find(s => s[0] === (k === 'health' ? 'mcp' : k)))
        .filter(Boolean).map(s => s[1].toLowerCase());
      return { state: 'failed', line: `Start-up is taking longer than it should${waiting.length ? `. Still waiting for: ${waiting.join(', ')}` : ''}.` };
    }
    if (!h) return { state: Object.keys(got).length || Object.keys(pending).length ? 'connecting' : 'starting',
      line: Object.keys(pending).length ? 'Establishing connections…' : 'Starting the Control Panel…' };
    if (!loadedOnce) {
      const st = STAGES.find(([k]) => k !== 'health' && pending[k] && !got[k]);
      return { state: 'initialising', line: st ? st[1] : 'Loading recent activity…' };
    }
    if (mcpOf(h.data) === 'off') probs.push("the tool server isn't set up, so tools and memory are off");
    const m = readout('memory'), md = readout('models'), cp = readout('caps');
    if (m[0] === 'warn') probs.push(m[1] === 'Not answering' ? "shared memory isn't answering" : "shared memory didn't load");
    if (cp[0] === 'warn') probs.push("the tool list didn't load");
    if (md[0] === 'warn' || md[0] === 'bad') {
      const tiles = md[1] === "Didn't load" ? [] : got.models.data.servers.filter(s => !s.hidden), off = tiles.filter(s => !s.online).length;
      probs.push(!tiles.length ? "the AI model list didn't load" : `${off} of ${tiles.length} AI models ${off === 1 ? 'is' : 'are'} offline`);
    }
    if (probs.length) {
      const txt = probs.join('. ');
      return { state: 'degraded', line: `${txt[0].toUpperCase()}${txt.slice(1)}. Everything else is running.` };
    }
    return { state: 'ready', line: 'Ready for operation.' };
  }

  const PHASE = { starting: 'INITIALISING INTELLIGENCE', connecting: 'INITIALISING INTELLIGENCE', initialising: 'INITIALISING INTELLIGENCE',
    ready: 'READY FOR OPERATION', degraded: 'RUNNING WITH LIMITS', failed: 'NOT READY' };
  const WORD = { starting: 'Starting', connecting: 'Connecting', initialising: 'Initialising', ready: 'Ready', degraded: 'Degraded', failed: 'Failed' };
  const $ = sel => root.querySelector(sel);
  function setText(sel, text) { const el = $(sel); if (el && el.textContent !== text) el.textContent = text; }

  function render() {
    if (!booting) return;
    SYSTEMS.forEach(([k]) => {
      const el = root.querySelector(`.hb-ro[data-k="${k}"]`), [s, v] = readout(k);
      if (el.dataset.s !== s) el.dataset.s = s;
      const val = el.querySelector('.hb-ro-v');
      if (val.textContent !== v) val.textContent = v;
    });
    if (got.settings && got.settings.ok && got.settings.data.animations === false) root.classList.add('hb-still');
    const a = assess();
    root.dataset.state = a.state;
    setText('.hb-phase', PHASE[a.state]);
    setText('.hb-state-w', WORD[a.state]);
    setText('.hb-stage-t', retrying && a.state === 'failed' ? 'Checking again…' : a.line);
    $('.hb-retry').hidden = !(a.state === 'failed' || a.state === 'degraded') || (got.health && got.health.status === 401);
    const late = performance.now() - t0 > T.openAfter;
    root.classList.toggle('hb-can-open', late || a.state === 'failed' || a.state === 'degraded');
    if (!opts || entered) return;
    if (a.state === 'ready') enter();
    else if (a.state === 'degraded') {
      if (!holding) { holding = true; timers.push(setTimeout(() => { if (!chosen && root.dataset.state === 'degraded') enter(); }, T.degradedHold)); }
    } else holding = false;
  }

  // ---- the hand-off: the page opens at once; the core contracts and fades over it, then everything is removed ----
  function enter() {
    if (entered) return;
    entered = true;
    booting = false;
    const shell = document.getElementById('shell');
    if (shell) shell.removeAttribute('inert');
    try { opts.enter(); } catch (e) { setTimeout(() => { throw e; }); }
    const still = root.classList.contains('hb-still');
    root.classList.add('hb-exit');
    root.setAttribute('aria-hidden', 'true');
    cleanup();
    setTimeout(() => { root.remove(); Recovery.start(); }, still ? 0 : T.exit);
  }
  function cleanup() {
    timers.forEach(clearTimeout); timers = [];
    clearInterval(tick); tick = null;
    if (onKey) document.removeEventListener('keydown', onKey, true);
    if (onVis) document.removeEventListener('visibilitychange', onVis);
    if (ro) ro.disconnect();
    onKey = onVis = ro = null;
  }

  function retry() {
    retrying = true; timedOut = false; t0 = performance.now(); holding = false;
    delete got.health; render();
    timers.push(setTimeout(() => { retrying = false; render(); }, 1200));
    try { opts && opts.retry(); } catch (e) { /* the page's own poll goes on */ }
  }

  // ---- ConnectionRecovery: after the hand-off, a compact indicator when the connection drops (never a replay) ----
  const Recovery = (function () {
    let el = null, last = 'ok', hideT = 0, started = false;
    function show(kind, text) {
      if (!el) {
        el = document.createElement('div');
        el.className = 'hb-conn';
        el.setAttribute('role', 'status');
        el.innerHTML = '<span class="hb-conn-core" aria-hidden="true"><i></i></span><span class="hb-conn-t"></span>'
          + '<button type="button" class="hb-conn-retry">Retry</button>';
        el.querySelector('button').addEventListener('click', () => { try { opts && opts.retry(); } catch (e) { /* the poll goes on */ } });
        document.body.appendChild(el);
      }
      clearTimeout(hideT);
      el.dataset.kind = kind;
      el.querySelector('.hb-conn-t').textContent = text;
      el.querySelector('button').hidden = kind === 'back';
      el.hidden = false;
      if (kind === 'back') hideT = setTimeout(() => { el.hidden = true; }, 2600);
    }
    function state(res) {
      if (!res.ok) return res.status === 401 ? 'ok' : 'lost';  // 401: the page itself goes to the sign-in page
      return mcpOf(res.data) === 'bad' ? 'tool' : 'ok';
    }
    function apply(s) {
      if (s === last) return;
      if (s === 'lost') show('lost', 'Reconnecting to the Control Panel…');
      else if (s === 'tool') show('tool', "The tool server isn't answering. Checking again…");
      else if (el) show('back', 'Reconnected');
      last = s;
    }
    return {
      start() { started = true; if (got.health) apply(state(got.health)); },
      health(k, res) { if (started && k === 'health') apply(state(res)); },
    };
  })();

  // ---- build it: the first frame is already the dark field and the green point ----
  if (root) {
    root.innerHTML = defs + backdrop() + `<div class="hb-frame">${BootStatus()}`
      + SystemTelemetry('left')
      + `<div class="hb-core"><div class="hb-l hb-init"><i class="hb-hline"></i><i class="hb-point"></i></div>`
      + `<div class="hb-l hb-glowl"><div class="hb-in hb-sprite"></div></div>${OrbitalRings()}${EnergyParticles()}${HomeShedCore()}</div>`
      + SystemTelemetry('right') + `</div>`;
    root.setAttribute('aria-label', 'HomeShed is starting');
    Object.entries(T.spin).forEach(([k, v]) => root.style.setProperty('--hb-t-' + k, v));
    const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (calm || (document.body && document.body.classList.contains('no-anim'))) root.classList.add('hb-still');
    // hairlines stay one CSS pixel at any size: the strokes are in core units, so tell them how big a pixel is
    const core = $('.hb-core');
    const size = () => root.style.setProperty('--hb-px', f1(1000 / Math.max(160, core.getBoundingClientRect().width)));
    size();
    if (window.ResizeObserver) { ro = new ResizeObserver(size); ro.observe(core); }
    onVis = () => root.classList.toggle('hb-paused', document.hidden);
    document.addEventListener('visibilitychange', onVis);
    onVis();
    $('.hb-open').addEventListener('click', () => { chosen = true; if (opts) enter(); });
    $('.hb-retry').addEventListener('click', () => { chosen = true; retry(); });
    root.addEventListener('pointerdown', e => { if (e.target.closest('.hb-actions')) chosen = true; });
    onKey = e => { if (e.key === 'Escape' && opts) { e.preventDefault(); enter(); } };
    document.addEventListener('keydown', onKey, true);
    tick = setInterval(() => {  // the honest clock: seconds since start-up began, shown once it's slow
      const s = Math.round((performance.now() - t0) / 1000);
      setText('.hb-elapsed', s >= 3 && root.dataset.state !== 'ready' ? `${s} s` : '');
      if (!timedOut && !loadedOnce && performance.now() - t0 > T.timeout) timedOut = true;
      render();
    }, 1000);
    render();
  }

  window.HomeShedBoot = {
    tokens: T,
    mount(o) {
      opts = o;
      if (!root) { setTimeout(o.enter, 0); return; }
      const shell = document.getElementById('shell');
      if (shell) shell.setAttribute('inert', '');
      render();
    },
    loaded() { loadedOnce = true; if (booting) render(); },
  };
})();
