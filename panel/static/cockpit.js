/* The Overview cockpit (2026-10-27): the page's own data, drawn with the telemetry engine (telemetry.js).
   The page's loaders hand their replies here (HSCockpit.ingest); nothing here fetches, so the cockpit adds no requests.
   What each figure is, and where it comes from (measured, estimated or not available), is written next to it and in
   each token-flow stage's detail; the table is in ../README.md, "Where the Overview's numbers come from". */
(function () {
  'use strict';
  const T = window.HSTelemetry;
  const $ = id => document.getElementById(id);
  if (!T || !$('cockpit')) return;
  const { fmtNum, fmtMs, fmtPct, esc } = T;

  const S = { tokens: null, daily: null, dailyState: 'loading', activity: null, progress: null, progressState: 'loading',
              clients: null, lastTs: null, rtkCommands: null, sel: null,
              range: T.store.get('cp-tok-range') || 'all',
              pulse: { view: T.store.get('cp-pulse-view') || 'saved', table: false,
                       range: (() => { try { return JSON.parse(T.store.get('cp-pulse-range') || '{}'); } catch (e) { return {}; } })() } };
  const now = () => Date.now() / 1000;
  const day = t => new Date(t).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  const dayAxis = t => new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  const hm = t => new Date(t).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  const hourAxis = span => t => span > 24 ? dayAxis(t) : hm(t);
  const hourTip = t => `${day(t)}, ${hm(t)} to ${hm(t + 3600e3)}`;

  // ---- the windows ----
  const win = T.Windows($('ckGrid'), {
    key: 'cp-cockpit-layout-v1', dock: $('ckDock'), live: $('ckLive'),
    defaults: { order: ['resume', 'savings', 'github', 'pulse', 'systems', 'map'],
                size: { resume: 'm', savings: 'l', github: 'l', pulse: 'l', systems: 'm', map: 'xl' } },
  });
  $('ckReset').onclick = () => win.reset();
  const motionBtn = $('ckMotion');
  function syncMotion() {
    const on = T.Motion.pref() === 'on';
    motionBtn.setAttribute('aria-pressed', String(on));
    motionBtn.title = on ? 'Turn decorative movement off (data still updates)' : 'Turn decorative movement on';
    const why = T.Motion.why();
    $('ckWhy').textContent = on && why ? `Movement is off: ${why}.` : '';
  }
  motionBtn.onclick = () => { T.Motion.set(T.Motion.pref() !== 'on'); syncMotion(); };
  T.Motion.on(syncMotion);
  syncMotion();

  // ---- the instruments ----
  const rxV = new T.Reactor($('rxVerified'), { label: 'Verified saved' });
  const rxE = new T.Reactor($('rxEstimated'), { label: 'Estimated saved' });
  const rxC = new T.Reactor($('rxCalls'), { label: 'Tool calls' });
  const pulse = new T.Pulse($('ckPulse'));
  const ghTrace = new T.Pulse($('ckGhTrace'), { compact: true });
  // The token pipeline: two lanes of tokens on their way to Claude. RTK trims the shell's output; the front door and
  // local work keep tool definitions, files and writing out of Claude's context. Permissions, tool calls and memory
  // turn no tokens away, so they carry only their counts.
  const NAMES = { shell: 'Claude Code shell', apps: 'Sessions and apps', rtk: 'RTK trimming', door: 'Front door', perm: 'Permissions',
                  tools: 'Tool calls', mem: 'Memory', local: 'Local model', claude: 'Claude', sink: 'Kept out of Claude' };
  const map = new T.FlowMap($('ckMapStage'), {
    label: 'Where HomeShed keeps tokens out of Claude',
    lanes: [{ id: 'shell', source: 'shell', stages: ['rtk'] }, { id: 'apps', source: 'apps', stages: ['door', 'perm', 'tools', 'mem', 'local'] }],
    order: ['rtk', 'door', 'perm', 'tools', 'mem', 'local'], banded: ['rtk', 'door', 'local'],
    names: NAMES,
    tones: { shell: 'measured', rtk: 'measured', apps: 'live', door: 'estimated', perm: 'live', tools: 'live', mem: 'live', local: 'estimated' },
    bandNames: { rtk: 'Shell output RTK trimmed', door: "Tool definitions not loaded and replies trimmed", generation: 'Writing done locally',
                 reading: 'Reading done locally', retrieval: 'Search instead of reading' },
    quiet: () => $('cockpit').classList.contains('classic'),
    onSelect: id => { S.sel = id; renderDetail(); },
  });

  // ---- Savings: verified (RTK, measured) and estimated (sizes / 4), never added together ----
  function renderSavings() {
    const L = T.ledger(S.tokens), r = L.measured.rtk;
    const rows = S.daily;
    // verified
    if (!S.tokens) rxV.set({ status: 'loading', label: 'Verified saved' });
    else if (!r) rxV.set({ status: 'unavailable', kind: 'measured', label: 'Verified saved', value: 'No data',
      period: L.measured.rtkWhy === 'not-set-up' ? "RTK isn't set up" : "RTK isn't answering",
      aria: 'Verified tokens saved: no data. RTK, which measures them, ' + (L.measured.rtkWhy === 'not-set-up' ? "isn't set up." : "isn't answering.") });
    else {
      const span = { week: 7, month: 30 }[S.range] || 0;
      const d30 = rows ? T.dailySeries(rows, 30) : null;
      const sum = span && rows ? T.dailySeries(rows, span).reduce((a, p) => a + (p.v || 0), 0) : null;
      const val = span && sum != null ? sum : r.tokens;
      rxV.set({ status: 'ok', kind: 'measured', tone: 'measured', label: 'Verified saved', value: fmtNum(val), raw: r.tokens, unit: 'tokens',
        period: span && sum != null ? `last ${span} days, by RTK` : 'all time, measured by RTK',
        arc: r.pct != null ? { frac: r.pct, title: `RTK made command output ${fmtPct(r.pct)} smaller on average` } : null,
        spokes: d30 ? { values: d30.map(p => p.v), titles: d30.map(p => `${day(p.t)}: ${p.v == null ? 'no data' : fmtNum(p.v) + ' tokens'}`) } : null,
        aria: `Verified tokens saved, measured by RTK: ${fmtNum(val)} ${span ? `in the last ${span} days` : 'all time'}. Output ${fmtPct(r.pct)} smaller on average.` });
    }
    const seg = $('rxRange');
    seg.hidden = !(r && rows && rows.length);
    seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.r === S.range)));
    // estimated
    const e = L.estimated;
    if (!S.tokens) rxE.set({ status: 'loading', label: 'Estimated saved' });
    else if (!e) rxE.set({ status: 'unavailable', kind: 'estimated', label: 'Estimated saved', value: 'No data', period: "HomeShed's counters didn't load" });
    else {
      const h = L.hours ? T.hourlySeries(L.hours, 24, now(), b => ((b.saved_chars || 0) + (b.fd_chars || 0)) / L.cpt) : null;
      const parts = [['generation', 'Writing done locally', 1], ['reading', 'Reading done locally', 0.78], ['retrieval', 'Search instead of reading', 0.58],
                     ['frontDoor', 'Front door', 0.4]].map(([k, t, a]) => ({ v: e[k] || 0, tone: 'estimated', dashed: true, alpha: a, title: `${t}: about ${fmtNum(e[k] || 0)} tokens` }));
      rxE.set({ status: 'ok', kind: 'estimated', tone: 'estimated', label: 'Estimated saved', value: '≈ ' + fmtNum(L.estimatedTotal), raw: L.estimatedTotal,
        unit: 'tokens', period: 'all time', segments: parts,
        spokes: h ? { values: h.map(p => p.v), titles: h.map(p => `${hourTip(p.t)}: ${p.v == null ? 'no data' : 'about ' + fmtNum(p.v) + ' tokens'}`) } : null,
        aria: `Estimated tokens saved: about ${fmtNum(L.estimatedTotal)}, all time, from sizes divided by ${L.cpt}.` });
    }
    // the ledger: every source on its own line, with what kind of figure it is
    const fd = L.measured.frontDoorChars, row = (label, val, cls, prov, provCls, tip) =>
      `<div class="ck-lrow" title="${esc(tip || '')}"><span>${esc(label)}</span><b class="${cls}">${esc(val)}</b><span class="prov ${provCls}">${esc(prov)}</span></div>`;
    const out = [];
    if (!S.tokens) out.push('<div class="ck-lrow"><span>Reading the counters…</span><b class="u"></b></div>');
    else {
      out.push(r ? row('Command output trimmed (RTK)', fmtNum(r.tokens), 'm', `measured, ${fmtNum(r.commands)} commands`, 'm', "RTK's own count of the shell output it cut before Claude read it")
                 : row('Command output trimmed (RTK)', 'no data', 'u', L.measured.rtkWhy === 'not-set-up' ? "RTK isn't set up" : 'not answering', 'u'));
      out.push(fd ? row('Tool lists not loaded, replies trimmed', '≈ ' + fmtNum(e.frontDoor), 'e', `${fmtNum(fd.hidden + fd.trimmed)} characters measured, ÷ ${L.cpt}`, 'e',
                        "The front door: definitions of tools an app didn't load, and characters cut from tool replies")
                  : row('Tool lists not loaded (front door)', 'no data', 'u', 'needs the tool server update', 'u'));
      if (e) {
        out.push(row('Writing done locally', '≈ ' + fmtNum(e.generation), 'e', 'estimate: what the local model wrote', 'e'));
        if (e.reading) out.push(row('Reading done locally', '≈ ' + fmtNum(e.reading), 'e', 'estimate: file minus its summary', 'e'));
        if (e.retrieval) out.push(row('Search instead of reading', '≈ ' + fmtNum(e.retrieval), 'e', 'estimate: source files minus chunks', 'e'));
      }
      out.push(row('Cached input', 'no data', 'u', 'provider-reported; HomeShed is not sent it', 'u', "Claude's prompt-cache counts stay with the provider"));
      // as money: measured and estimated each with its own tokens and price, side by side, never added together
      const mp = T.moneyParts(L);
      if (mp) {
        const part = (x, cls, name) => x
          ? `<span class="ck-money-part ${cls}" title="${esc(x.tip)}" tabindex="0">${name}: <b>${esc(x.tokens)}</b> tokens <b>≈ ${esc(x.money)}</b></span>`
          : `<span class="ck-money-part u">${name}: no data</span>`;
        out.push(`<div class="ck-money" title="Each figure is priced on its own (hover it for how); they are never added together"><span class="ck-money-h">As money, all time, at ${esc(mp.label.replace(/^Claude\s+/, ''))} API prices</span>`
          + `<span class="ck-money-parts">${part(mp.measured, 'm', 'Measured')}${part(mp.estimated, 'e', 'Estimated')}</span></div>`);
      }
    }
    $('ckLedger').innerHTML = out.join('');
  }
  document.querySelectorAll('#rxRange button').forEach(b => b.addEventListener('click', () => {
    S.range = b.dataset.r; T.store.set('cp-tok-range', S.range); renderSavings();
  }));

  // ---- GitHub: repo.stats from the release reports ----
  function renderGithub() {
    const box = $('ckGhFigs');
    const list = S.progress;
    if (S.progressState === 'loading') return;
    const repos = (list || []).filter(p => p.stats && !p.stats.private && p.repo);
    $('ckGhRepo').textContent = repos.length === 1 ? repos[0].repo : repos.length ? `${repos.length} repos` : '';
    if (!repos.length) {
      box.innerHTML = `<p class="ck-note">${list ? 'No public repo has reported yet. An agent reports with repo.progress_update.' : "The release reports didn't load."}</p>`;
      ghTrace.set({ series: [], message: 'No traffic to draw yet.' });
      return;
    }
    const sum = f => repos.reduce((a, p) => a + (Number(f(p.stats)) || 0), 0);
    const stars = sum(s => s.stars), week = sum(s => s.stars_week);
    const dl = sum(s => s.release_downloads) + sum(s => (s.pypi || {}).month);
    const traffic = repos.some(p => p.stats.traffic);
    const latest = repos.map(p => (p.stats.releases || [])[0]).find(Boolean);
    const fig = (v, k, cls, tip) => `<div class="ck-gh-fig ${cls || ''}" title="${esc(tip || k)}"><b>${v}</b><small>${esc(k)}</small></div>`;
    box.innerHTML = fig(fmtNum(stars) + (week ? `<em>+${fmtNum(week)} this week</em>` : ''), 'stars', 'big')
      + fig(fmtNum(dl), 'downloads', '', repos.some(p => p.stats.pypi) ? 'Release downloads, plus PyPI downloads this month' : 'Release downloads')
      + fig(traffic ? fmtNum(sum(s => (s.traffic || {}).views_14d)) : '–', traffic ? 'views, 14 days' : 'views: needs a GitHub token')
      + fig(traffic ? fmtNum(sum(s => (s.traffic || {}).clones_14d)) : '–', 'clones, 14 days')
      + fig(latest ? esc(latest.tag) : '–', latest ? `latest of ${sum(s => (s.releases || []).length)} releases` : 'no release yet');
    if (!traffic) { ghTrace.set({ series: [], message: "Views and clones need a GitHub token with traffic access (GitHub's own figures)." }); return; }
    const rows = f => repos.flatMap(p => ((p.stats.traffic || {}).daily || []).map(d => ({ date: d.day || d.date, v: Number(d[f]) || 0 })));
    const views = T.dailySeries(rows('views'), 14, 'v'), clones = T.dailySeries(rows('clones'), 14, 'v');
    ghTrace.set({ label: 'GitHub views and clones a day, last 14 days (from GitHub)', fmtX: day, fmtAxisX: dayAxis,
      series: [{ name: 'Views', tone: 'live', points: views }, { name: 'Clones', tone: 'measured', points: clones }] });
  }

  // ---- Pulse: one trace at a time; each says what it is ----
  const VIEWS = {
    saved: { name: 'Saved a day', ranges: [[7, '7 d'], [30, '30 d'], [90, '90 d']], def: 30 },
    calls: { name: 'Calls an hour', ranges: [[24, '24 h'], [168, '7 d'], [336, '14 d']], def: 24 },
    est: { name: 'Estimated an hour', ranges: [[24, '24 h'], [168, '7 d'], [336, '14 d']], def: 24 },
    latency: { name: 'Response time', ranges: [], def: 0 },
  };
  function renderPulseBar() {
    const v = VIEWS[S.pulse.view] ? S.pulse.view : 'saved';
    $('ckPulseTabs').innerHTML = Object.entries(VIEWS).map(([k, x]) => `<button type="button" data-v="${k}" aria-pressed="${k === v}">${esc(x.name)}</button>`).join('');
    const rg = S.pulse.range[v] || VIEWS[v].def;
    $('ckPulseRange').hidden = !VIEWS[v].ranges.length;
    $('ckPulseRange').innerHTML = VIEWS[v].ranges.map(([n, t]) => `<button type="button" data-n="${n}" aria-pressed="${n === rg}">${t}</button>`).join('');
    $('ckPulseTable').setAttribute('aria-pressed', String(S.pulse.table));
  }
  function renderPulse() {
    const v = VIEWS[S.pulse.view] ? S.pulse.view : 'saved', rg = S.pulse.range[v] || VIEWS[v].def;
    const L = T.ledger(S.tokens), note = $('ckPulseNote');
    if (v === 'saved') {
      note.innerHTML = '<span class="prov m">measured by RTK</span>';
      if (!S.daily) { pulse.set({ series: [], message: S.dailyState === 'loading' ? 'Reading RTK’s daily figures…' : "RTK's daily figures aren't available (it isn't set up, or isn't answering)." }); return; }
      pulse.set({ label: `Tokens RTK kept out of Claude each day, last ${rg} days`, fmtX: day, fmtAxisX: dayAxis,
        fmtTip: x => fmtNum(x) + ' tokens', series: [{ name: 'Tokens saved', tone: 'measured', points: T.dailySeries(S.daily, rg) }] });
    } else if (v === 'calls' || v === 'est') {
      note.innerHTML = v === 'calls' ? '<span class="prov l">counted by HomeShed</span>' : `<span class="prov e">estimate: sizes ÷ ${L.cpt}</span>`;
      if (!L.hours) { pulse.set({ series: [], message: S.tokens ? "Hourly counts start once the tool server's update is deployed." : 'Reading the counters…' }); return; }
      const series = v === 'calls'
        ? [{ name: 'Calls', tone: 'live', points: T.hourlySeries(L.hours, rg, now(), 'calls') },
           { name: 'Errors and refusals', tone: 'bad', points: T.hourlySeries(L.hours, rg, now(), b => (b.errors || 0) + (b.refused || 0)) }]
        : [{ name: 'Estimated tokens saved', tone: 'estimated', dashed: true, points: T.hourlySeries(L.hours, rg, now(), b => ((b.saved_chars || 0) + (b.fd_chars || 0)) / L.cpt) }];
      pulse.set({ label: v === 'calls' ? `Tool calls an hour, last ${rg} hours` : `Estimated tokens saved an hour, last ${rg} hours`, legendAlways: v === 'calls',
        fmtX: hourTip, fmtAxisX: hourAxis(rg), series });
    } else {
      const pts = T.latencyPoints(S.activity);
      const st = T.activityStats(S.activity, now());
      note.innerHTML = `<span class="prov l">measured, the last ${st.ran} calls</span>`;
      if (!pts.length) { pulse.set({ series: [], kind: 'dots', message: S.activity ? 'No calls since HomeShed last restarted.' : 'Reading recent calls…' }); return; }
      pulse.set({ label: `Response time of the last ${pts.length} tool calls. Median ${fmtMs(st.p50)}`, kind: 'dots', fmtY: fmtMs,
        fmtX: t => `${day(t)} ${new Date(t).toLocaleTimeString('en-GB')}`, fmtAxisX: hm, legendAlways: true,
        fmtTip: (x, p) => `${fmtMs(x)}${p && p.bad ? ', failed' : ''}`,
        series: [{ name: `Each call (median ${fmtMs(st.p50)}, slowest 5% over ${fmtMs(st.p95)}); failed calls in red`, tone: 'live', points: pts }] });
    }
  }
  $('ckPulseTabs').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S.pulse.view = b.dataset.v; T.store.set('cp-pulse-view', S.pulse.view); renderPulseBar(); renderPulse();
  });
  $('ckPulseRange').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S.pulse.range[S.pulse.view] = Number(b.dataset.n); T.store.set('cp-pulse-range', JSON.stringify(S.pulse.range)); renderPulseBar(); renderPulse();
  });
  $('ckPulseTable').addEventListener('click', () => { S.pulse.table = !S.pulse.table; pulse.showTable(S.pulse.table); renderPulseBar(); });

  // ---- Systems: lifetime calls, how many worked, how fast it's going now ----
  function renderSystems() {
    const L = T.ledger(S.tokens), c = L.calls, st = T.activityStats(S.activity, now());
    if (!S.tokens) rxC.set({ status: 'loading', label: 'Tool calls' });
    else if (!c) rxC.set({ status: 'unavailable', kind: 'live', label: 'Tool calls', value: 'No data', period: "HomeShed's counters didn't load" });
    else {
      const h = L.hours ? T.hourlySeries(L.hours, 24, now(), 'calls') : null;
      rxC.set({ status: 'ok', kind: 'live', tone: 'live', label: 'Tool calls', value: fmtNum(c.total), raw: c.total, unit: 'calls',
        period: c.since ? 'since ' + new Date(c.since * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : 'all time',
        arc: c.total ? { frac: 1 - c.errors / c.total, title: `${fmtPct(1 - c.errors / c.total)} of calls worked` } : null,
        spokes: h ? { values: h.map(p => p.v), titles: h.map(p => `${hourTip(p.t)}: ${p.v == null ? 'no data' : p.v + ' calls'}`) } : null,
        rate: st.perMin5,
        aria: `Tool calls, all time: ${fmtNum(c.total)}, ${fmtPct(c.total ? 1 - c.errors / c.total : null)} worked. ${st.perMin5 ? `${st.perMin5.toFixed(1)} a minute lately` : 'None in the last five minutes'}.` });
    }
    $('ckRate').textContent = S.activity ? (st.perMin5 ? st.perMin5.toFixed(st.perMin5 < 10 ? 1 : 0) : '0') : '—';
    $('ckP50').textContent = S.activity ? fmtMs(st.p50) : '—';
    $('ckErr').textContent = c ? fmtNum(c.errors) : '—';
    const refused = S.clients ? S.clients.reduce((a, r) => a + (r.refused || 0), 0) : null;
    $('ckRefused').textContent = refused != null ? fmtNum(refused) : S.activity ? fmtNum(st.refused) : '—';
    $('ckRefusedL').textContent = refused != null ? 'refused, all time' : 'refused, recent calls';
  }

  // ---- the token flow: the bands (real tokens), each stage's figure and badges, the sink ----
  function flowData() {
    const L = T.ledger(S.tokens), c = L.calls, st = T.activityStats(S.activity, now()), fd = L.measured.frontDoorChars, r = L.measured.rtk;
    const e = L.estimated, live = S.clients ? S.clients.filter(x => x.sessions).length : null;
    const refused = S.clients ? S.clients.reduce((a, x) => a + (x.refused || 0), 0) : S.activity ? st.refused : null;
    const streams = !S.tokens ? {} : {
      rtk: [{ id: 'rtk', chan: 'm', v: r ? r.tokens : null }],
      door: [{ id: 'door', chan: 'e', v: fd && e ? e.frontDoor : null }],
      local: e ? [{ id: 'generation', chan: 'e', v: e.generation }, { id: 'reading', chan: 'e', v: e.reading }, { id: 'retrieval', chan: 'e', v: e.retrieval }]
        : [{ id: 'local', chan: 'e', v: null }],
    };
    const none = { status: 'none' };
    const loc = e ? e.generation + e.reading + e.retrieval : null;
    const text = {
      shell: r ? { badges: [`${fmtNum(r.commands)} commands`] } : { ...none, badges: [S.tokens ? (L.measured.rtkWhy === 'not-set-up' ? "RTK isn't set up" : "RTK isn't answering") : ''] },
      apps: c ? { badges: [`${fmtNum(c.total)} calls`].concat(live != null ? [`${live} live`] : []), aria: c ? `${fmtNum(c.total)} calls all time${live != null ? `, ${live} connected now` : ''}` : '' } : none,
      rtk: r ? { figure: fmtNum(r.tokens), kind: 'm', badges: [r.pct != null ? `${fmtPct(r.pct)} smaller` : 'measured'],
                 aria: `${fmtNum(r.tokens)} tokens kept out, measured by RTK, ${fmtPct(r.pct)} smaller on average` }
        : { ...none, figure: S.tokens ? 'no data' : '', kind: 'u' },
      door: fd && e ? { figure: '≈ ' + fmtNum(e.frontDoor), kind: 'e', badges: [`${fmtNum(fd.listings)} short lists`],
                        aria: `about ${fmtNum(e.frontDoor)} tokens kept out, an estimate from ${fmtNum(fd.hidden + fd.trimmed)} characters measured` }
        : { ...none, figure: S.tokens ? 'no data' : '', kind: 'u', badges: S.tokens ? ['needs an update'] : [] },
      perm: refused != null ? { badges: [`${fmtNum(refused)} refused`] } : none,
      tools: S.activity ? { badges: [`${fmtMs(st.p50)} median`].concat(c ? [`${fmtNum(c.errors)} errors`] : []) } : none,
      mem: c ? { badges: [`${fmtNum(c.memoryReads)} recalls`, `${fmtNum(c.memoryWrites)} saves`] } : none,
      local: e ? { figure: '≈ ' + fmtNum(loc), kind: 'e', badges: c ? [`${fmtNum(c.local)} local calls`] : [],
                   aria: `about ${fmtNum(loc)} tokens kept out: writing ${fmtNum(e.generation)}, reading ${fmtNum(e.reading)}, search ${fmtNum(e.retrieval)}, estimates` }
        : { ...none, figure: S.tokens ? 'no data' : '', kind: 'u' },
      claude: { badges: ['Cached input: no data'] },
    };
    const mp = T.moneyParts(L);
    const short = mp ? mp.label.replace(/^Claude\s+/, '') : '';
    const sink = {
      sub: 'Two separate counts, never added',
      moneyNote: mp ? `at ${short} prices` : '',
      m: r ? { value: fmtNum(r.tokens), kind: 'measured by RTK', money: mp && mp.measured ? '≈ ' + mp.measured.money : '',
               tip: mp && mp.measured ? mp.measured.tip : 'Measured by RTK: its own count of the shell output it cut.' }
        : { kind: S.tokens ? (L.measured.rtkWhy === 'not-set-up' ? "measured: RTK isn't set up" : "measured: RTK isn't answering") : 'measured' },
      e: e ? { value: '≈ ' + fmtNum(L.estimatedTotal), kind: 'estimated', money: mp && mp.estimated ? '≈ ' + mp.estimated.money : '',
               tip: mp && mp.estimated ? mp.estimated.tip : `An estimate: the sizes HomeShed measured, ÷ ${L.cpt}.` }
        : { kind: 'estimated' },
    };
    // the legend's one always-useful line: which stage keeps the most out
    const big = [['RTK trimming', r && r.tokens, ''], ['Front door', fd && e ? e.frontDoor : null, '≈ '], ['Local model', loc, '≈ ']]
      .filter(x => x[1] > 0).sort((a, b) => b[1] - a[1])[0];
    return { streams, text, sink, loading: !S.tokens, note: big ? `Most kept out: ${big[0]}, ${big[2]}${fmtNum(big[1])} tokens` : '' };
  }
  const HOW = {
    apps: 'Every tool call HomeShed serves, from your own sessions (the owner token) and each app with its own client token. Counted by the tool server (usage.json), all time.',
    door: "Apps on the front door see a short tool list and look tools up by name, so the full definitions never enter their context. HomeShed measures the characters it didn't send (and those cut from replies with fields or to_file); tokens are those characters ÷ 4, an estimate.",
    perm: "Calls refused before they ran: a tool the app wasn't granted, a switched-off tool, or a rate limit. Counted per app by the tool server. Refusing a call keeps no tokens out by itself, so this stage has no band.",
    tools: 'How long each call took, from the tool server’s list of its last 200 calls (it starts again when HomeShed restarts). Errors are calls that ran and failed, all time. No band: running a tool keeps no tokens out by itself.',
    mem: 'memory.recall, recall_facts and recall_relevant calls (reads) and capture or remember_fact calls (saves), all time. Counts only: what was recalled is never shown here, and no tokens figure is claimed for it.',
    local: 'Work the local model did so Claude did not: the text it wrote, files it read and summarised, searches instead of opening files. Sizes ÷ 4, one documented rule per tool: an estimate. Its band is three streams side by side.',
    shell: "Commands Claude Code ran through RTK on your PC (RTK's own count).",
    rtk: "RTK trims shell output before Claude reads it, and counts the tokens it removed. Measured by RTK itself; HomeShed doesn't estimate this one.",
    claude: "What still reaches Claude is counted by the provider (Anthropic) and isn't sent to HomeShed, so the lanes into Claude are hatched: no data. The same goes for Claude's prompt-cache counts.",
    sink: "Every band ends here. The measured count (RTK) and the estimate (the front door and local work) come from different stages, so neither counts the other's tokens, but they are kept as two figures and never added into one. Band widths share one scale, so the two can be compared by eye. Money, when chosen in Settings, prices each at the chosen model's API rates: tokens kept out of Claude's context at the input price, the local model's writing at the output price.",
  };
  const KINDS = { m: 'measured', e: 'estimate', l: 'counted', u: 'no data' };
  function renderDetail() {
    const box = $('ckMapDetail'), id = S.sel;
    box.hidden = !id;
    if (!id) return;
    const L = T.ledger(S.tokens), c = L.calls, st = T.activityStats(S.activity, now()), fd = L.measured.frontDoorChars, r = L.measured.rtk, e = L.estimated;
    const rows = [];
    const add = (label, v, k) => rows.push(`<div class="ck-lrow"><span>${esc(label)}</span><b class="${k === 'l' ? 'm' : k}">${esc(v == null ? 'no data' : v)}</b><span class="prov ${v == null ? 'u' : k}">${KINDS[v == null ? 'u' : k]}</span></div>`);
    const ev = filter => (S.activity || []).filter(filter).slice(0, 6);
    let events = [];
    if (id === 'apps') { add('Calls, all time', c && fmtNum(c.total), 'l'); add('Connected now', S.clients ? String(S.clients.filter(x => x.sessions).length) : null, 'l'); events = ev(() => true); }
    if (id === 'door') { add('Definitions not loaded', fd && fmtNum(fd.hidden) + ' chars', 'm'); add('Reply characters trimmed', fd && fmtNum(fd.trimmed) + ' chars', 'm'); add('As tokens', fd && '≈ ' + fmtNum(e.frontDoor), 'e'); add('Short lists served', fd && fmtNum(fd.listings), 'm'); }
    if (id === 'perm') { add('Refused, all time', S.clients ? fmtNum(S.clients.reduce((a, x) => a + (x.refused || 0), 0)) : null, 'l'); add('Refused, recent calls', S.activity ? fmtNum(st.refused) : null, 'l'); events = ev(x => x.refused); }
    if (id === 'tools') { add('Median time, recent', S.activity ? fmtMs(st.p50) : null, 'm'); add('Slowest 5% over', S.activity ? fmtMs(st.p95) : null, 'm'); add('Errors, all time', c && fmtNum(c.errors), 'l'); add('Recent calls a minute', S.activity ? st.perMin5.toFixed(1) : null, 'm'); events = ev(x => !x.refused); }
    if (id === 'mem') { add('Recalls', c && fmtNum(c.memoryReads), 'l'); add('Saves', c && fmtNum(c.memoryWrites), 'l'); events = ev(x => /^memory\./.test(x.id || '')); }
    if (id === 'local') { add('Writing done locally', e && '≈ ' + fmtNum(e.generation), 'e'); add('Reading done locally', e && '≈ ' + fmtNum(e.reading), 'e'); add('Search instead of reading', e && '≈ ' + fmtNum(e.retrieval), 'e'); add('Local model workload (not a saving)', S.tokens ? fmtNum(L.workload) + ' tokens' : null, 'm'); add('Local calls', c && fmtNum(c.local), 'l'); events = ev(x => /^(local_ai|reasoning)\.|^knowledge\.search$/.test(x.id || '')); }
    if (id === 'shell') { add('Commands through RTK', r && fmtNum(r.commands), 'm'); }
    if (id === 'rtk') { add('Tokens removed', r && fmtNum(r.tokens), 'm'); add('Average saving', r && fmtPct(r.pct), 'm'); }
    if (id === 'claude') { add('Tokens it was sent', null, 'u'); add('Cached input', null, 'u'); }
    if (id === 'sink') {
      add('Kept out, measured', r && fmtNum(r.tokens), 'm'); add('Kept out, estimated', e && '≈ ' + fmtNum(L.estimatedTotal), 'e');
      const mp = T.moneyParts(L);
      if (mp) { add(`Measured, at ${mp.label} prices`, mp.measured && '≈ ' + mp.measured.money, 'm'); add(`Estimated, at ${mp.label} prices`, mp.estimated && '≈ ' + mp.estimated.money, 'e'); }
    }
    const evHtml = events.length ? `<p class="ck-ev-h">Latest calls</p><ul>${events.map(x => `<li class="${x.refused || x.ok === false ? 'bad' : ''}"><b>${esc(x.id)}</b><span>${x.refused ? 'refused' : x.ok === false ? 'failed' : fmtMs(x.duration_ms)} · ${esc(hm(x.timestamp * 1000))}</span></li>`).join('')}</ul>` : '';
    box.innerHTML = `<div class="ck-detail-head"><h3>${esc(NAMES[id] || id)}</h3><button type="button" class="tw-btn ck-detail-x" aria-label="Close the detail" title="Close (Esc)">`
      + '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button></div>'
      + `${rows.join('')}<p>${esc(HOW[id])}</p>${evHtml}`;
  }
  $('ckMapDetail').addEventListener('click', ev => { if (ev.target.closest('.ck-detail-x')) map.select(S.sel); });
  $('ckMapStage').closest('.ck-map').addEventListener('keydown', ev => { if (ev.key === 'Escape' && S.sel) { const id = S.sel; map.select(id); const n = document.querySelector(`#ckMapStage [data-node="${id}"]`); if (n) n.focus(); } });
  function renderMap() { map.update(flowData()); renderDetail(); }

  // ---- flows: one particle per real event ----
  function route(e) {
    if (e.refused) return [['apps', 'perm'], 'bad'];
    const id = e.id || '';
    if (/^(local_ai|reasoning)\.|^knowledge\.search$/.test(id)) return [['apps', 'local', 'sink'], e.ok === false ? 'bad' : 'estimated'];
    return [/^memory\./.test(id) ? ['apps', 'mem', 'claude'] : ['apps', 'claude'], e.ok === false ? 'bad' : 'live'];
  }

  function renderAll() { renderSavings(); renderGithub(); renderPulseBar(); renderPulse(); renderSystems(); renderMap(); }

  // ---- the summary screen keeps its own layout: the status, the core and the numbers go back there ----
  const marks = {};
  function placeholder(node, key) { if (!marks[key] && node) { marks[key] = document.createComment(key); node.parentNode.insertBefore(marks[key], node); } }
  function place(summary) {
    const hero = $('statusHero'), core = document.querySelector('.core-wrap'), strip = document.querySelector('.stat-strip');
    placeholder(hero, 'hero'); placeholder(strip, 'strip');
    if (summary) {
      if (hero && marks.hero) marks.hero.parentNode.insertBefore(hero, marks.hero);
      if (core && hero) hero.appendChild(core);
      if (strip && marks.strip) marks.strip.parentNode.insertBefore(strip, marks.strip);
    } else {
      if (hero) $('ckStatus').appendChild(hero);
      if (core) $('ckCore').appendChild(core);
      if (strip) $('ckRead').prepend(strip);
    }
    window.dispatchEvent(new Event('resize'));  // the live core measures itself again
  }
  place(document.body.classList.contains('summary'));

  window.HSCockpit = {
    summary: place,
    ingest(kind, d) {
      try {
        if (kind === 'tokens') {
          const c = d && d.rtk && d.rtk.available ? Number(d.rtk.total_commands) || 0 : null;
          if (c != null && S.rtkCommands != null && c > S.rtkCommands) for (let i = 0; i < Math.min(3, c - S.rtkCommands); i++) setTimeout(() => map.flow(['shell', 'rtk', 'sink'], 'measured'), i * 260);
          if (c != null) S.rtkCommands = c;
          S.tokens = d; renderSavings(); renderSystems(); renderPulse(); renderMap();
        } else if (kind === 'savings') {
          S.daily = d && d.daily && d.daily.available !== false && Array.isArray(d.daily.daily) ? d.daily.daily : null;
          S.dailyState = 'done'; renderSavings(); if (S.pulse.view === 'saved') renderPulse();
        } else if (kind === 'activity') {
          const list = Array.isArray(d) ? d : null;
          if (list && S.lastTs != null) {
            list.filter(e => e.timestamp > S.lastTs).reverse().slice(-12).forEach((e, i) => {
              const [r, tone] = route(e); setTimeout(() => map.flow(r, tone), i * 180);
            });
          }
          if (list && list.length) S.lastTs = Math.max(S.lastTs || 0, ...list.map(e => e.timestamp));
          else if (list && S.lastTs == null) S.lastTs = 0;
          S.activity = list; renderSystems(); renderMap(); if (S.pulse.view === 'latency') renderPulse();
        } else if (kind === 'progress') {
          S.progress = Array.isArray(d) ? d : null; S.progressState = 'done'; renderGithub();
        } else if (kind === 'clients') {
          S.clients = Array.isArray(d) ? d : null; renderSystems(); renderMap();
        } else if (kind === 'viewer') {
          ['resume', 'github'].forEach(id => { const w = document.querySelector(`[data-win="${id}"]`); if (w) w.hidden = true; });
          win.refresh();
        }
      } catch (err) { console.warn('cockpit:', kind, err); }  // one figure failing never takes the page with it
    },
  };
  // ---- Cockpit on or off (the owner, 2026-10-27: a visible switch, not a theme): off is a calm fixed layout of the same
  // figures as plain numbers and thin lines; nothing in it moves, and the windows stay put. Remembered per browser.
  const modeBtn = $('ckMode');
  function setMode(on, save) {
    $('cockpit').classList.toggle('classic', !on);
    if (modeBtn) {
      modeBtn.setAttribute('aria-pressed', String(on));
      modeBtn.title = on ? 'Cockpit on: dials, traces and the pipeline map. Click for plain numbers' : 'Cockpit off: plain numbers. Click for the dials, traces and pipeline map';
    }
    if (save) T.store.set('cp-cockpit', on ? null : 'off');
    renderAll();
    window.dispatchEvent(new Event('resize'));  // the traces and the core measure themselves again
  }
  if (modeBtn) modeBtn.onclick = () => setMode($('cockpit').classList.contains('classic'), true);
  setMode(T.store.get('cp-cockpit') !== 'off', false);
  T.HexField($('hexBg'));
  // The page's own endless animations (live dots, pings, the release card's shine) wait while their part is out of sight.
  document.querySelectorAll('.ck-bridge, #ckGrid > .tw, #opsView > :not(#cockpit)').forEach(n => { if (!n._tmVisible) T.watchVisible(n); });
})();
