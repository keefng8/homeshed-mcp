/* The Overview below the cockpit's top half (overview-lower, 2026-10-27). The owner: "the bottom half now has lines and
   simple graphs. The top half looks the part now. Get everything to match and flow." So every window under the token
   pipeline is drawn in the cockpit's own language: fine bezel markings, provenance in the stroke (solid measured,
   dashed estimate, hatched no data), phosphor traces made of a wide faint pass and a thin bright one (no shadowBlur),
   and a lit head on the newest real value.

   The page's loaders hand their replies here (OVL.ingest) or call the small drawing helpers while they build a row
   (OVL.gauge-free: the bars are restyled in CSS). Nothing here fetches, polls or animates on a timer: an instrument is
   redrawn when its data arrives, and the only movement is a one-off ping on a real new event (paused by the Motion
   switch, reduced motion and a hidden tab, like the rest of the cockpit).
   Cockpit off (the header's switch, cockpit.js) takes #opsView back to the calm classic cards: this file follows the
   switch by watching #cockpit's class, so it needs no hook in cockpit.js.
   Pure helpers (arcs, scales, time windows, the call tape's layout) are exported for node:
   dashboard/js_tests/ovlower_test.js. */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.OVL = api;
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  'use strict';
  const hasDOM = typeof document !== 'undefined';
  const TAU = Math.PI * 2;
  const f2 = n => (Math.round(n * 100) / 100).toString();
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  function fmtNum(n) {
    if (n == null || !isFinite(n)) return '—';
    const a = Math.abs(n), s = n < 0 ? '−' : '';
    if (a >= 1e9) return s + (a / 1e9).toFixed(a >= 1e10 ? 1 : 2) + 'B';
    if (a >= 1e6) return s + (a / 1e6).toFixed(a >= 1e7 ? 1 : 2) + 'M';
    if (a >= 1e4) return s + (a / 1e3).toFixed(a >= 1e5 ? 0 : 1) + 'k';
    return s + Math.round(a).toLocaleString('en-GB');
  }
  const fmtMs = ms => ms == null || !isFinite(ms) ? '—' : ms >= 1000 ? (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + ' s' : Math.round(ms) + ' ms';

  // ---------------------------------------------------------------------------------------------------------------
  // Pure helpers
  // ---------------------------------------------------------------------------------------------------------------
  // A dial's sweep: 270 degrees, opening at the bottom (like the cockpit's gauges), from `a0` clockwise.
  const SWEEP = 1.5 * Math.PI, A0 = 0.75 * Math.PI;
  const clamp01 = v => Math.max(0, Math.min(1, Number(v) || 0));
  function polar(cx, cy, r, a) { return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; }
  function arcD(cx, cy, r, a0, a1) {
    if (a1 - a0 >= TAU - 1e-4) {  // a whole ring: two halves (one arc can't close on itself)
      const [x0, y0] = polar(cx, cy, r, a0), [x1, y1] = polar(cx, cy, r, a0 + Math.PI);
      return `M${f2(x0)} ${f2(y0)}A${r} ${r} 0 1 1 ${f2(x1)} ${f2(y1)}A${r} ${r} 0 1 1 ${f2(x0)} ${f2(y0)}`;
    }
    const [x0, y0] = polar(cx, cy, r, a0), [x1, y1] = polar(cx, cy, r, a1);
    return `M${f2(x0)} ${f2(y0)}A${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${f2(x1)} ${f2(y1)}`;
  }
  // Segments of a whole as [start, end] angles, a fixed gap between them; a zero part takes no room.
  function segments(values, a0, sweep, gap) {
    const tot = values.reduce((a, v) => a + Math.max(0, Number(v) || 0), 0);
    if (!tot) return values.map(() => null);
    const live = values.filter(v => v > 0).length, g = live > 1 ? gap : 0;
    let a = a0;
    return values.map(v => {
      if (!(v > 0)) return null;
      const span = v / tot * sweep, s = [a + g / 2, a + Math.max(g / 2 + 0.004, span - g / 2)];
      a += span;
      return s;
    });
  }
  // n equal slots (gates, checks) with `lit` of them on, as [start, end, on] angles.
  function slots(n, lit, a0, sweep, gap) {
    n = Math.max(0, Math.min(60, Math.round(n) || 0));
    const out = [], w = n ? sweep / n : 0, g = n > 1 ? Math.min(gap, w * 0.4) : 0;
    for (let i = 0; i < n; i++) out.push([a0 + i * w + g / 2, a0 + (i + 1) * w - g / 2, i < lit]);
    return out;
  }
  // The call tape's window: at least `min` seconds, else the age of the oldest entry rounded up to a whole 5 minutes
  // (at most `max`), so the scale stays still between polls instead of creeping.
  function tapeWindow(times, nowSec, min, max) {
    min = min || 900; max = max || 6 * 3600;
    const old = times.length ? nowSec - Math.min(...times) : 0;
    return Math.max(min, Math.min(max, Math.ceil(old / 300) * 300));
  }
  // Each call's mark: x in [0, 1] across the window, y height in [0.12, 1] from its duration on a log scale
  // (1 ms .. 10 s), and its kind. Older than the window: left out.
  function tapeMarks(entries, nowSec, span, riskOf) {
    const lo = Math.log10(1), hi = Math.log10(10000);
    return (entries || []).filter(e => e && isFinite(e.timestamp) && nowSec - e.timestamp <= span && nowSec - e.timestamp >= -5)
      .map(e => {
        const ms = Number(e.duration_ms);
        const h = e.h != null ? clamp01(e.h) : isFinite(ms) && ms > 0 ? 0.12 + 0.88 * Math.max(0, Math.min(1, (Math.log10(Math.max(1, ms)) - lo) / (hi - lo))) : 0.12;
        const kind = e.refused ? 'refused' : e.ok === false ? 'failed' : riskOf && riskOf(e.id) === 'write' ? 'write' : 'read';
        return { x: Math.max(0, Math.min(1, 1 - (nowSec - e.timestamp) / span)), h: e.refused ? 0.12 : h, kind, e };
      });
  }
  // Labels for a tape of `span` seconds: one every 1, 5, 10, 15, 30 or 60 minutes, about `want` of them.
  function tapeTicks(span, want) {
    want = want || 6;
    const step = [60, 300, 600, 900, 1800, 3600, 7200].find(s => span / s <= want) || 7200;
    const out = [];
    for (let s = 0; s <= span + 1; s += step) out.push({ x: 1 - s / span, ago: s });
    return { step, ticks: out };
  }
  const agoLabel = s => s === 0 ? 'now' : s < 3600 ? `−${Math.round(s / 60)} m` : `−${+(s / 3600).toFixed(1)} h`;
  // A trace's points in [0, 1] x [0, 1] (y up): a null is a gap; the scale starts at zero.
  function tracePoints(values, max) {
    const n = values.length, m = max || Math.max(1e-9, ...values.filter(v => v != null));
    return values.map((v, i) => v == null ? null : [n > 1 ? i / (n - 1) : 0.5, Math.max(0, Math.min(1, v / m))]);
  }
  const pure = { arcD, segments, slots, tapeWindow, tapeMarks, tapeTicks, tracePoints, agoLabel, clamp01, SWEEP, A0, fmtNum, fmtMs };
  if (!hasDOM) return pure;

  // ---------------------------------------------------------------------------------------------------------------
  // Drawing (SVG strings: the loaders build rows as strings, so an instrument is a string too)
  // ---------------------------------------------------------------------------------------------------------------
  // A dial's bezel: a hairline ring, fine ticks over the sweep (a longer one every quarter), registration marks.
  function bezel(cx, r, n, opts) {
    opts = opts || {};
    const a0 = opts.full ? -Math.PI / 2 : A0, sw = opts.full ? TAU : SWEEP;
    let d = '';
    for (let i = 0; i <= n; i++) {
      if (opts.full && i === n) break;
      const a = a0 + i / n * sw, major = i % (n / 4) === 0, [x0, y0] = polar(cx, cx, r - (major ? 4.5 : 2.4), a), [x1, y1] = polar(cx, cx, r, a);
      d += `M${f2(x0)} ${f2(y0)}L${f2(x1)} ${f2(y1)}`;
    }
    return `<circle cx="${cx}" cy="${cx}" r="${r + 2.2}" class="o-hair"/><path d="${d}" class="o-ticks"/>`;
  }
  // A lit arc: a wide faint pass, a mid pass and the thin bright core, then a head at its end.
  function litArc(cx, r, a0, a1, cls, w, head) {
    const d = arcD(cx, cx, r, a0, a1), [hx, hy] = polar(cx, cx, r, a1);
    return `<g class="o-lit ${cls || ''}"><path d="${d}" class="o-glow" style="stroke-width:${w + 6}"/><path d="${d}" class="o-glow2" style="stroke-width:${w + 2.5}"/>`
      + `<path d="${d}" class="o-core" style="stroke-width:${w}"/>${head ? `<circle cx="${f2(hx)}" cy="${f2(hy)}" r="${(w * 0.85 + 0.8).toFixed(2)}" class="o-head"/>` : ''}</g>`;
  }
  // A small dial: fraction on a 270-degree sweep, with its figure in the middle. tone: measured | estimated | live | bad
  function dial(frac, opts) {
    opts = opts || {};
    const S = 64, c = S / 2, r = 23, fr = frac == null ? null : clamp01(frac);
    const marks = (opts.marks || []).map(m => {
      const a = A0 + clamp01(m.at) * SWEEP, [x0, y0] = polar(c, c, r - 5, a), [x1, y1] = polar(c, c, r + 5, a);
      return `<path d="M${f2(x0)} ${f2(y0)}L${f2(x1)} ${f2(y1)}" class="o-mark ${m.cls || ''}"><title>${esc(m.title || '')}</title></path>`;
    }).join('');
    const body = fr == null
      ? `<path d="${arcD(c, c, r, A0, A0 + SWEEP)}" class="o-hatch"/>`
      : litArc(c, r, A0, A0 + Math.max(0.01, fr * SWEEP), opts.dashed ? 'est' : '', 2.6, fr > 0);
    return `<svg class="o-dial t-${opts.tone || 'live'}" viewBox="0 0 ${S} ${S}" role="img" aria-label="${esc(opts.aria || '')}">${opts.aria ? `<title>${esc(opts.aria)}</title>` : ''}`
      + bezel(c, 30, 20) + `<path d="${arcD(c, c, r, A0, A0 + SWEEP)}" class="o-track"/>${body}${marks}`
      + `<text x="${c}" y="${c + 4}" class="o-val">${esc(opts.value == null ? '' : opts.value)}</text>`
      + (opts.sub ? `<text x="${c}" y="${c + 25}" class="o-sub">${esc(opts.sub)}</text>` : '') + '</svg>';
  }
  // A composition ring: parts of a whole round a full circle, one hue, each part a step lighter, 2-degree gaps.
  function compRing(parts, opts) {
    opts = opts || {};
    const S = 88, c = S / 2, r = 33, segs = segments(parts.map(p => p.v), -Math.PI / 2, TAU, 0.06);
    const arcs = parts.map((p, i) => segs[i] ? `<path d="${arcD(c, c, r, segs[i][0], segs[i][1])}" class="o-seg${p.est ? ' est' : ''}" style="opacity:${(1 - i * 0.11).toFixed(2)}"><title>${esc(p.title || p.label)}</title></path>` : '').join('');
    return `<svg class="o-comp t-${opts.tone || 'live'}" viewBox="0 0 ${S} ${S}" role="img" aria-label="${esc(opts.aria || '')}">`
      + bezel(c, 42, 48, { full: true }) + `<circle cx="${c}" cy="${c}" r="${r}" class="o-track"/>${arcs}`
      + `<circle cx="${c}" cy="${c}" r="24" class="o-in"/><text x="${c}" y="${c + 3}" class="o-val">${esc(opts.value)}</text>`
      + `<text x="${c}" y="${c + 14}" class="o-sub">${esc(opts.sub || '')}</text></svg>`;
  }
  // A two-part micro ring (reads and writes): no figures inside, its title says them.
  function split(a, b, title) {
    if (a == null || b == null) return '<svg class="o-split" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" class="o-hatch"/></svg>';
    const segs = segments([a, b], -Math.PI / 2, TAU, 0.35);
    return `<svg class="o-split" viewBox="0 0 20 20" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title><circle cx="10" cy="10" r="7" class="o-track"/>`
      + (segs[0] ? `<path d="${arcD(10, 10, 7, segs[0][0], segs[0][1])}" class="o-part r"/>` : '')
      + (segs[1] ? `<path d="${arcD(10, 10, 7, segs[1][0], segs[1][1])}" class="o-part w"/>` : '') + '</svg>';
  }
  // An oscilloscope trace on a precision grid. series: [{name, tone, dashed, values, titles}]; columns: hover titles.
  // The SVG stretches to its box (non-scaling strokes); round-capped zero-length strokes stay round dots.
  function trace(series, opts) {
    opts = opts || {};
    const W = 400, H = 100, all = series.flatMap(s => s.values).filter(v => v != null);
    const max = opts.max || Math.max(1, ...all), n = Math.max(...series.map(s => s.values.length), 2);
    let grid = '';
    for (let i = 0; i <= 20; i++) grid += `M${f2(W * i / 20)} 0V${H}`;
    for (let i = 0; i <= 4; i++) grid += `M0 ${f2(H * i / 4)}H${W}`;
    const lines = series.map(s => {
      const pts = tracePoints(s.values, max), segs = [];
      let cur = [];
      pts.forEach(p => { if (!p) { if (cur.length) segs.push(cur); cur = []; } else cur.push(`${f2(p[0] * W)} ${f2(H - 4 - p[1] * (H - 10))}`); });
      if (cur.length) segs.push(cur);
      const d = segs.map(sg => sg.length > 1 ? 'M' + sg.join('L') : `M${sg[0]}l0 0`).join('');
      if (opts.dots) {
        const dd = pts.map(p => p ? `M${f2(p[0] * W)} ${f2(H - 4 - p[1] * (H - 10))}l0 0` : '').join('');
        return `<g class="o-tr dots t-${s.tone || 'live'}"><path d="${dd}" class="o-glow"/><path d="${dd}" class="o-core"/></g>`;
      }
      const last = [...pts].reverse().find(Boolean);
      const area = segs.length && s.fill !== false ? segs.filter(sg => sg.length > 1).map(sg => `M${sg[0].split(' ')[0]} ${H}L${sg.join('L')}L${sg[sg.length - 1].split(' ')[0]} ${H}Z`).join('') : '';
      return `<g class="o-tr t-${s.tone || 'live'}${s.dashed ? ' est' : ''}">${area ? `<path d="${area}" class="o-area"/>` : ''}<path d="${d}" class="o-glow"/><path d="${d}" class="o-glow2"/><path d="${d}" class="o-core"/>`
        + (last ? `<path d="M${f2(last[0] * W)} ${f2(H - 4 - last[1] * (H - 10))}l0 0" class="o-headdot"/>` : '') + '</g>';
    }).join('');
    const ref = opts.ref != null ? `<path d="M0 ${f2(H - 4 - Math.min(1, opts.ref / max) * (H - 10))}H${W}" class="o-ref"><title>${esc(opts.refTitle || '')}</title></path>` : '';
    const cols = (opts.columns || []).map((t, i, a) => `<rect x="${f2(W * (a.length > 1 ? i / (a.length - 1) : 0.5) - W / a.length / 2)}" y="0" width="${f2(W / a.length)}" height="${H}" class="o-col"><title>${esc(t)}</title></rect>`).join('');
    const yl = opts.fmtY ? `<span class="o-y top">${esc(opts.fmtY(max))}</span><span class="o-y mid">${esc(opts.fmtY(max / 2))}</span>` : '';
    const xl = opts.xLabels ? `<span class="o-x l">${esc(opts.xLabels[0])}</span><span class="o-x r">${esc(opts.xLabels[1])}</span>` : '';
    return `<div class="o-trace${opts.cls ? ' ' + opts.cls : ''}"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(opts.aria || '')}">`
      + `<path d="${grid}" class="o-grid"/>${ref}${lines}${cols}</svg>${yl}${xl}</div>`;
  }
  // The call tape: every recent call as a stroke at its time, as tall as it took (log scale), its kind in its colour.
  function tape(entries, opts) {
    opts = opts || {};
    const now = opts.now || Date.now() / 1000;
    const span = opts.span || tapeWindow((entries || []).map(e => e.timestamp), now, opts.min, opts.max);
    const marks = tapeMarks(entries, now, span, opts.riskOf);
    const W = 600, H = 48, base = H - 6, { ticks } = tapeTicks(span, opts.want || 6);
    let grid = '', minor = '';
    ticks.forEach(t => { grid += `M${f2(t.x * W)} 0V${base}`; });
    const mstep = ticks.length > 1 ? (ticks[0].x - ticks[1].x) / 5 : 0;
    if (mstep > 0) for (let x = 1; x >= 0; x -= mstep) minor += `M${f2(x * W)} ${base}v3`;
    const by = { read: '', write: '', failed: '', refused: '' }, cols = [];
    marks.sort((a, b) => a.x - b.x).forEach(m => {
      const x = f2(m.x * W);
      by[m.kind] += m.kind === 'refused' ? `M${x} ${base - 2}l0 0` : `M${x} ${base}V${f2(base - m.h * (base - 4))}`;
      cols.push(`<rect x="${f2(m.x * W - 3)}" y="0" width="6" height="${H}" class="o-col"><title>${esc((opts.title || defTitle)(m.e, m.kind))}</title></rect>`);
    });
    const fresh = (opts.fresh || []).map(e => tapeMarks([e], now, span, opts.riskOf)[0]).filter(Boolean)
      .map(m => `<span class="o-ping k-${m.kind}" style="left:${(m.x * 100).toFixed(2)}%"></span>`).join('');
    const labels = ticks.map(t => `<span class="o-x" style="left:${(t.x * 100).toFixed(2)}%">${esc(agoLabel(t.ago))}</span>`).join('');
    return `<div class="o-tape"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(opts.aria || '')}">`
      + `<path d="${grid}" class="o-grid"/><path d="M0 ${base + 0.5}H${W}" class="o-base"/><path d="${minor}" class="o-ticks"/>`
      + Object.entries(by).map(([k, d]) => d ? `<g class="k-${k}"><path d="${d}" class="o-glow"/><path d="${d}" class="o-core"/></g>` : '').join('')
      + cols.join('') + `</svg>${fresh}<div class="o-xs">${labels}</div></div>`;
  }
  const defTitle = (e, kind) => `${e.id || 'call'}: ${kind === 'refused' ? 'refused' : kind === 'failed' ? 'failed after ' + fmtMs(e.duration_ms) : fmtMs(e.duration_ms)}, `
    + new Date(e.timestamp * 1000).toLocaleTimeString('en-GB');
  const KEY = (cls, t) => `<span class="o-key ${cls}"><i></i>${esc(t)}</span>`;

  // ---------------------------------------------------------------------------------------------------------------
  // The page
  // ---------------------------------------------------------------------------------------------------------------
  const $ = id => document.getElementById(id);
  const ops = $('opsView');
  if (!ops) return pure;
  const motion = () => !document.documentElement.classList.contains('tm-still') && !document.hidden;
  const on = () => ops.classList.contains('ovl-ck');
  const shown = {};  // what each instrument last drew, so an unchanged reply redraws nothing
  function put(node, html, key) {
    if (!node) return;
    if (shown[node.id || key] === html) return;
    shown[node.id || key] = html;
    node.innerHTML = html;
  }
  function make(id, cls, where, how) {
    let n = $(id);
    if (n || !where) return n;
    n = document.createElement('div');
    n.id = id; n.className = 'ovl-i ' + cls;
    if (how === 'before') where.parentNode.insertBefore(n, where);
    else if (how === 'after') where.parentNode.insertBefore(n, where.nextSibling);
    else if (how === 'first') where.insertBefore(n, where.firstChild);
    else where.appendChild(n);
    return n;
  }
  // the instruments' boxes exist from the first paint, at their final size, in a reading state
  const ctxCard = $('ctxCard'), ctxDial = make('ovlCtxDial', 'ovl-ctxdial', ctxCard && ctxCard.querySelector('.live-head'), 'after');
  const ctxRuler = make('ovlCtxRuler', 'ovl-ruler', $('ctxFeed'), 'before');
  const act = document.querySelector('.activity-card');
  const tapeBox = make('ovlTape', 'ovl-tapebox', act && act.querySelector('.ticker'), 'before');
  const sysTape = make('ovlSysTape', 'ovl-systape', $('ckRead'), 'end');
  const capCol = document.querySelector('[data-card="tools"]'), capComp = make('ovlCaps', 'ovl-compbox', capCol && capCol.querySelector('.scroll-body'), 'before');
  const memCol = document.querySelector('[data-card="memory"]'), memComp = make('ovlMem', 'ovl-compbox', memCol && memCol.querySelector('.scroll-body'), 'before');
  ['rwRead', 'rwWrite'].forEach(id => { const p = $(id); if (p) make(id + 'Dial', 'ovl-rwdial', p, 'first'); });
  const reading = '<div class="ovl-wait">Reading…</div>';
  [ctxDial, tapeBox, sysTape, capComp, memComp].forEach(n => { if (n && !n.innerHTML) n.innerHTML = reading; });

  // ---- Claude context: one ring per session (outermost = the top row), the alarm a radial line across them ----
  function ctxReactor(d) {
    const alarm = ((d.settings || {}).context_alarm_pct || 90) / 100, auto = (d.settings || {}).context_auto_compact
      ? Math.min((d.settings || {}).context_auto_compact_pct || 90, alarm * 100 - 2) / 100 : null;
    const rows = (d.sessions || []).slice(0, 5), S = 132, c = S / 2;
    const top = (d.sessions || []).reduce((m, s) => Math.max(m, s.used_pct || 0), 0);
    let rings = '';
    rows.forEach((s, i) => {
      const r = 50 - i * 8.2, pct = clamp01((s.used_pct || 0) / 100), cls = pct >= alarm ? 'bad' : pct >= alarm - 0.15 ? 'warn' : '';
      rings += `<g><title>${esc(`${s.project || 'Session'}: ${Math.round(pct * 100)}% full`)}</title><path d="${arcD(c, c, r, A0, A0 + SWEEP)}" class="o-track thin"/>`
        + (pct > 0 ? litArc(c, r, A0, A0 + Math.max(0.02, pct * SWEEP), cls, 3, true) : '') + '</g>';
    });
    const ray = (f, cls, t) => { const a = A0 + f * SWEEP, [x0, y0] = polar(c, c, 13, a), [x1, y1] = polar(c, c, 58, a);
      return `<path d="M${f2(x0)} ${f2(y0)}L${f2(x1)} ${f2(y1)}" class="o-mark ${cls}"><title>${esc(t)}</title></path>`; };
    let scale = '';
    [0, 0.25, 0.5, 0.75, 1].forEach(f => { const [x, y] = polar(c, c, 63.5, A0 + f * SWEEP); scale += `<text x="${f2(x)}" y="${f2(y + 2)}" class="o-scale">${f * 100}</text>`; });
    return `<svg class="o-ctx" viewBox="-6 -6 ${S + 12} ${S + 12}" role="img" aria-label="${esc(`Context use: ${rows.map(s => `${s.project} ${Math.round(s.used_pct || 0)}%`).join(', ')}. Alarm at ${Math.round(alarm * 100)}%.`)}">`
      + bezel(c, 60, 40) + scale + rings + ray(alarm, 'bad', `Alarm at ${Math.round(alarm * 100)}%`)
      + (auto != null ? ray(auto, 'auto', `Auto-compact at ${Math.round(auto * 100)}%`) : '')
      + `<text x="${c}" y="${c + 5}" class="o-val big">${rows.length ? Math.round(top) + '%' : '—'}</text><text x="${c}" y="${c + 17}" class="o-sub">fullest</text></svg>`
      + `<div class="o-legend">${KEY('m', 'used')}${KEY('bad', 'alarm')}${auto != null ? KEY('auto', 'auto-compact') : ''}</div>`;
  }
  // a row's place in the reactor: a tiny concentric mark with its own ring lit (identity without colour)
  function ringMark(i) {
    if (i > 4) return '';
    let g = '';
    for (let k = 0; k < 5; k++) g += `<circle cx="7" cy="7" r="${6.2 - k * 1.25}" class="${k === i ? 'on' : ''}"/>`;
    return `<svg class="o-rmark ovl-i" viewBox="0 0 14 14" aria-hidden="true">${g}</svg>`;
  }

  // ---- Release progress: the gates as lit slots, the readiness checks inside ----
  function gates(p, ready) {
    const total = Number(p.total) || 0, done = Number(p.done) || 0;
    const S = 104, c = S / 2;
    const out = slots(total, done, A0, SWEEP, 0.07).map(([a, b, lit]) => lit ? litArc(c, 41, a, b, '', 4, false) : `<path d="${arcD(c, c, 41, a, b)}" class="o-slot"/>`).join('');
    const rn = ready ? +ready[2] : 0, rd = ready ? +ready[1] : 0;
    const inner = rn ? slots(rn, rd, A0, SWEEP, 0.06).map(([a, b, lit]) => `<path d="${arcD(c, c, 32, a, b)}" class="${lit ? 'o-slot on' : 'o-slot'}"/>`).join('') : '';
    const pct = Math.max(0, Math.min(100, Math.round(Number(p.percent) || 0)));
    return `<div class="ovl-i ovl-gates"><svg class="o-gates" viewBox="0 0 ${S} ${S}" role="img" aria-label="${esc(`Release ${pct}%: ${done} of ${total} gates passed${rn ? `, readiness ${rd} of ${rn} checks` : ''}`)}">`
      + bezel(c, 50, 40) + out + inner + `<text x="${c}" y="${c + 5}" class="o-val big">${pct}%</text><text x="${c}" y="${c + 17}" class="o-sub">release</text></svg>`
      + `<div class="o-cap"><b>${done}</b> of ${total} gates${rn ? `<br><b>${rd}</b> of ${rn} checks` : ''}</div></div>`;
  }
  // ---- Released: the live version's ring with a bezel ----
  function relBezel() {
    const S = 120, c = 60;
    return `<svg class="ovl-i o-relbez" viewBox="0 0 ${S} ${S}" aria-hidden="true">${bezel(c, 58, 60, { full: true })}`
      + `<circle cx="${c}" cy="${c}" r="47" class="o-track"/>${litArc(c, 47, -Math.PI / 2, 1.5 * Math.PI - 0.001, '', 3.4, false)}</svg>`;
  }
  // ---- GitHub traffic in a release row: two traces, views solid, clones dashed, hover each day ----
  function traffic(days) {
    if (!days || days.length < 2) return '';
    const fmt = x => new Date(x.day || x.date || '').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
    return `<div class="ovl-i ovl-traffic"><div class="o-legend">${KEY('l', 'views')}${KEY('m dash', 'clones')}<span class="prov m">from GitHub</span></div>`
      + trace([{ name: 'Views', tone: 'live', values: days.map(x => Number(x.views) || 0) },
               { name: 'Clones', tone: 'measured', dashed: true, fill: false, values: days.map(x => Number(x.clones) || 0) }],
        { fmtY: fmtNum, xLabels: [fmt(days[0]), fmt(days[days.length - 1])], aria: `Views and clones a day, last ${days.length} days`,
          columns: days.map(x => `${fmt(x)}: ${x.views} views, ${x.clones} clones`) }) + '</div>';
  }

  // ---- the activity: the call tape (Live activity, Systems) and the read/write dials ----
  let lastActTs = null, risk = id => null, lastEntries = null;
  function drawTapes(entries, fresh) {
    const now = Date.now() / 1000;
    const keyRow = `<div class="o-legend">${KEY('l', 'read')}${KEY('e', 'write')}${KEY('bad', 'failed')}${KEY('ref', 'refused')}<span class="o-note">height: how long it took, 1 ms to 10 s (log)</span></div>`;
    if (!entries.length) { put(tapeBox, '<div class="ovl-wait">No calls since HomeShed last restarted.</div>'); put(sysTape, ''); return; }
    put(tapeBox, tape(entries, { now, riskOf: risk, fresh: motion() ? fresh : [], aria: `The last ${entries.length} tool calls over time, each as tall as it took` }) + keyRow);
    put(sysTape, tape(entries, { now, riskOf: risk, fresh: motion() ? fresh : [], want: 4, aria: 'Recent tool calls over time' }));
  }
  function rwDials(read, write) {
    const tot = (read || 0) + (write || 0);
    [['rwReadDial', read, 'live', 'reads'], ['rwWriteDial', write, 'estimated', 'writes']].forEach(([id, v, tone, what]) => {
      put($(id), dial(tot ? v / tot : null, { tone, value: tot ? Math.round(v / tot * 100) + '%' : '—', sub: 'of calls',
        aria: tot ? `${what}: ${Math.round(v / tot * 100)}% of all calls` : `${what}: no calls counted yet` }));
    });
  }

  // ---- Tools and memory: what they're made of ----
  function caps(list) {
    if (!capComp || !Array.isArray(list)) return;
    const by = {};
    list.forEach(c => { by[c.category || 'other'] = (by[c.category || 'other'] || 0) + 1; });
    let rows = Object.entries(by).sort((a, b) => b[1] - a[1]);
    if (rows.length > 7) rows = [...rows.slice(0, 6), ['other', rows.slice(6).reduce((a, r) => a + r[1], 0)]];
    const writes = list.filter(c => c.risk === 'write').length;
    put(capComp, compRing(rows.map(([k, v]) => ({ v, label: k, title: `${k}: ${v} tools` })), { value: list.length, sub: 'tools', aria: `${list.length} tools in ${Object.keys(by).length} groups` })
      + `<div class="o-list">${rows.map(([k, v], i) => `<div style="--o:${(1 - i * 0.11).toFixed(2)}"><i></i><span>${esc(k)}</span><b>${v}</b></div>`).join('')}</div>`
      + `<div class="o-side">${dial(list.length ? writes / list.length : null, { tone: 'estimated', value: writes, sub: 'can write', aria: `${writes} of ${list.length} tools can change something` })}</div>`);
  }
  function memory(d) {
    if (!memComp) return;
    if (!d || !Array.isArray(d.categories)) { put(memComp, '<div class="ovl-wait">No memory to draw</div>'); return; }
    const rows = d.categories.filter(c => c.count).sort((a, b) => b.count - a.count), tot = rows.reduce((a, c) => a + c.count, 0);
    if (!tot) { put(memComp, '<div class="ovl-wait">Nothing saved yet</div>'); return; }
    put(memComp, compRing(rows.map(c => ({ v: c.count, label: c.label, title: `${c.label}: ${c.count}${c.count_capped ? '+' : ''} facts` })), { value: fmtNum(tot), sub: 'facts', aria: `${tot} facts in ${rows.length} groups` })
      + `<div class="o-list">${rows.slice(0, 7).map((c, i) => `<div style="--o:${(1 - i * 0.11).toFixed(2)}"><i></i><span>${esc(c.label)}</span><b>${c.count}${c.count_capped ? '+' : ''}</b></div>`).join('')}</div>`);
  }
  // ---- the summary screen's two strips: the savings parts with their provenance, and the local work's spokes ----
  function tokParts(parts) {
    const bar = $('tokBar');
    if (!bar) return;
    const tot = parts.reduce((a, p) => a + p.v, 0);
    if (!tot) { bar.innerHTML = ''; return; }
    let a = 0;
    bar.innerHTML = `<div class="ovl-i o-spec">${parts.filter(p => p.v > 0).map(p => {
      const w = p.v / tot * 100, s = `<span class="${p.est ? 'est' : 'm'}" style="left:${a.toFixed(2)}%;width:${w.toFixed(2)}%;--k:${p.color}" title="${esc(`${p.label}: ${fmtNum(p.v)} tokens, ${p.est ? 'estimate' : 'measured'}`)}"></span>`;
      a += w; return s;
    }).join('')}<i class="o-rule"></i></div>`
      + `<span class="ovl-plain o-plainbar">${parts.filter(p => p.v > 0).map(p => `<span style="width:${(p.v / tot * 100).toFixed(2)}%;background:${p.color}"></span>`).join('')}</span>`;
  }
  function pipe(capsMap, ids) {
    const vals = Object.entries(ids).map(([id, el]) => [el, ((capsMap || {})[id] || {}).calls || 0]);
    const max = Math.max(1, ...vals.map(v => v[1]));
    vals.forEach(([el, n]) => { const box = $(el); if (box && box.parentNode) box.parentNode.style.setProperty('--o-f', Math.sqrt(n / max).toFixed(3)); });
  }

  // ---- what the loaders hand over ----
  const api = Object.assign({}, pure, {
    dial, compRing, split, trace, tape, ringMark, gates, relBezel, traffic, tokParts,
    ingest(kind, d) {
      try {
        if (kind === 'context') {
          if (!d || !Array.isArray(d.sessions)) { put(ctxDial, '<div class="ovl-wait">The context monitor isn\'t answering</div>'); put(ctxRuler, ''); return; }
          put(ctxDial, ctxReactor(d));
          const act = !!d.can_compact && !d.viewer;
          put(ctxRuler, d.sessions.length ? `<div class="ctx-row${act ? ' with-act' : ''}"><span class="o-rl">session</span><span class="o-scale-row">${[0, 25, 50, 75, 100].map(v => `<i style="left:${v}%">${v}</i>`).join('')}</span><span class="o-rl r">used</span><span class="o-rl r">updated</span>${act ? '<span></span>' : ''}</div>` : '');
        } else if (kind === 'activity') {
          const list = Array.isArray(d) ? d : [];
          const fresh = lastActTs == null ? [] : list.filter(e => e.timestamp > lastActTs).slice(0, 12);
          if (list.length) lastActTs = Math.max(lastActTs || 0, ...list.map(e => e.timestamp));
          else if (lastActTs == null) lastActTs = 0;
          lastEntries = list;
          drawTapes(list, fresh);
        } else if (kind === 'risk') {
          risk = d; if (lastEntries) { delete shown.ovlTape; delete shown.ovlSysTape; drawTapes(lastEntries, []); }
        } else if (kind === 'rw') rwDials(d.read, d.write);
        else if (kind === 'caps') caps(d);
        else if (kind === 'memory') memory(d);
        else if (kind === 'tokparts') tokParts(d);
        else if (kind === 'pipe') pipe(d.caps, d.ids);
      } catch (err) { console.warn('overview-lower:', kind, err); }  // one instrument failing never takes a loader with it
    },
  });

  // ---- Cockpit on or off: follow the header's switch (cockpit.js toggles #cockpit.classic) ----
  const ck = $('cockpit');
  function sync() {
    ops.classList.toggle('ovl-ck', !(ck && ck.classList.contains('classic')));
    if (typeof root.redrawSparks === 'function') root.redrawSparks();
  }
  if (ck) new MutationObserver(sync).observe(ck, { attributes: true, attributeFilter: ['class'] });
  sync();
  // endless decoration in these cards waits while they're out of sight (telemetry.css .tm-off)
  if (root.HSTelemetry && root.HSTelemetry.watchVisible) ops.querySelectorAll(':scope > .card, :scope > .cols > .col').forEach(n => { if (!n._tmVisible) root.HSTelemetry.watchVisible(n); });
  return api;
});
