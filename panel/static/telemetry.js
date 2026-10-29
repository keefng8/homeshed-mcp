/* HomeShed telemetry engine (2026-10-27). The owner: "I do not really like the bars for graphs. Remember I'm a nerd",
   and a mood board of reactor rings, oscilloscope traces and node maps. One visual system, three reusable parts:
     Reactor     a radial headline figure: fine bezel markings, a spectral ring of history, a progress arc with a lit
                 head, a composition ring, an orbit that turns only as fast as real events arrive, a lit core.
     Pulse       a time-series trace on a precision grid (canvas): hover/keyboard inspection, legend, table view.
     FlowMap     the token pipeline as a flow (SVG): each saving stage turns a band of tokens away from Claude, its
                 width the real count; a particle runs only for a real call.
   Plus Windows (the Overview's movable, resizable, collapsible windows, remembered per browser), Motion (one switch
   for decorative movement: the viewer's own, reduced motion, the panel's animations setting, a hidden tab) and
   HexField (the neon theme's background).
   Rules it keeps: no number is drawn that the caller didn't pass; no motion that implies activity that isn't happening;
   gaps stay gaps (null), never zeros. The pure helpers at the top are what the node tests exercise
   (dashboard/js_tests/telemetry_test.js); nothing here fetches anything. */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HSTelemetry = api;
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  'use strict';
  const hasDOM = typeof document !== 'undefined';
  const SVGNS = 'http://www.w3.org/2000/svg';

  // ---------------------------------------------------------------------------------------------------------------
  // Pure helpers
  // ---------------------------------------------------------------------------------------------------------------
  function fmtNum(n) {
    if (n == null || !isFinite(n)) return '—';
    const a = Math.abs(n), s = n < 0 ? '−' : '';
    if (a >= 1e9) return s + (a / 1e9).toFixed(a >= 1e10 ? 1 : 2) + 'B';
    if (a >= 1e6) return s + (a / 1e6).toFixed(a >= 1e7 ? 1 : 2) + 'M';
    if (a >= 1e4) return s + (a / 1e3).toFixed(a >= 1e5 ? 0 : 1) + 'k';
    return s + Math.round(a).toLocaleString('en-GB');
  }
  function fmtMs(ms) {
    if (ms == null || !isFinite(ms)) return '—';
    return ms >= 1000 ? (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + ' s' : Math.round(ms) + ' ms';
  }
  function fmtPct(f) { return f == null || !isFinite(f) ? '—' : Math.round(f * 100) + '%'; }

  // Axis ticks at 1, 2 or 5 x 10^k, covering [min, max] with about `count` steps.
  function niceTicks(min, max, count) {
    count = count || 4;
    if (!isFinite(min) || !isFinite(max)) return { min: 0, max: 1, step: 1, ticks: [0, 1] };
    if (max === min) max = min + (min === 0 ? 1 : Math.abs(min));
    const raw = (max - min) / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
    const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toPrecision(12));
    return { min: lo, max: hi, step, ticks };
  }

  function dayKey(d) {
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  // One point per local day for the last `days` days (oldest first). A day before the first recorded one is a gap
  // (null: the source wasn't counting yet); a later day with no row is a real zero (it counted, nothing happened).
  function dailySeries(rows, days, field, now) {
    now = now || new Date();
    field = field || 'saved_tokens';
    const by = {};
    let first = null;
    (rows || []).forEach(r => {
      if (!r || typeof r.date !== 'string') return;
      const v = Number(r[field]);
      if (!isFinite(v)) return;
      by[r.date] = (by[r.date] || 0) + v;
      if (first === null || r.date < first) first = r.date;
    });
    const out = [];
    for (let k = days - 1; k >= 0; k--) {
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - k);
      const key = dayKey(d);
      out.push({ t: d.getTime(), key, v: key in by ? by[key] : first !== null && key > first ? 0 : null });
    }
    return out;
  }

  // One point per clock hour for the last `n` hours, from usage.py's buckets [{t (epoch s, hour start), ...}].
  // `field` is a bucket key or a function of a bucket. Before the first bucket: a gap; after it: zero.
  function hourlySeries(hours, n, nowSec, field) {
    const get = typeof field === 'function' ? field : b => Number(b[field]) || 0;
    const by = {};
    let first = null;
    (hours || []).forEach(b => {
      if (!b || !isFinite(b.t)) return;
      by[b.t] = b;
      if (first === null || b.t < first) first = b.t;
    });
    const end = Math.floor(nowSec / 3600) * 3600, out = [];
    for (let t = end - (n - 1) * 3600; t <= end; t += 3600) {
      out.push({ t: t * 1000, v: by[t] ? get(by[t]) : first !== null && t > first ? 0 : null });
    }
    return out;
  }

  function percentile(sorted, p) {
    if (!sorted.length) return null;
    const i = (sorted.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
  }

  // The activity feed (the tool server's last 200 calls, newest first): what ran, how fast, what failed or was refused.
  function activityStats(entries, nowSec) {
    const list = Array.isArray(entries) ? entries.filter(e => e && isFinite(e.timestamp)) : [];
    const ran = list.filter(e => !e.refused);
    const ms = ran.map(e => Number(e.duration_ms)).filter(isFinite).sort((a, b) => a - b);
    const ts = list.map(e => e.timestamp);
    const recent = ran.filter(e => nowSec - e.timestamp <= 300).length;
    return {
      count: list.length, ran: ran.length,
      errors: ran.filter(e => e.ok === false).length,
      refused: list.length - ran.length,
      perMin5: recent / 5,
      p50: percentile(ms, 0.5), p95: percentile(ms, 0.95),
      first: ts.length ? Math.min(...ts) : null, last: ts.length ? Math.max(...ts) : null,
    };
  }
  function latencyPoints(entries) {
    return (Array.isArray(entries) ? entries : [])
      .filter(e => e && !e.refused && isFinite(e.timestamp) && isFinite(e.duration_ms))
      .map(e => ({ t: e.timestamp * 1000, v: Number(e.duration_ms), bad: e.ok === false, id: e.id }))
      .sort((a, b) => a.t - b.t);
  }

  // What HomeShed measures, kept apart from what it estimates (the owner's brief: no conflating, no double counting).
  //   tokens:  /api/tokens  {rtk, estimate (usage.py summary), local_models, money}
  // Each stage is a different place a token is kept out of Claude, so they never overlap: RTK trims shell output in
  // Claude Code; the front door skips tool definitions and trims tool replies; local work is text Claude didn't write
  // or read. Measured and estimated are never added into one headline.
  function ledger(tokens) {
    tokens = tokens || {};
    const rtk = tokens.rtk || null, est = tokens.estimate || null;
    const cpt = (est && est.chars_per_token) || 4;
    const rule = k => est && est.by_rule ? Number(est.by_rule[k]) || 0 : 0;
    const fd = est && est.front_door_total ? est.front_door_total : null;
    const caps = (est && est.capabilities) || {};
    const capSum = (pred, f) => Object.entries(caps).filter(([id]) => pred(id)).reduce((a, [, c]) => a + (Number(c[f]) || 0), 0);
    const out = {
      measured: {
        rtk: rtk && rtk.available ? { tokens: Number(rtk.total_saved) || 0, commands: Number(rtk.total_commands) || 0,
                                      pct: rtk.avg_savings_pct == null ? null : Number(rtk.avg_savings_pct) / 100 } : null,
        rtkWhy: !rtk ? 'unknown' : rtk.available ? null : rtk.not_set_up ? 'not-set-up' : 'unreachable',
        frontDoorChars: fd ? { hidden: Number(fd.hidden_chars) || 0, trimmed: Number(fd.trimmed_chars) || 0,
                               listings: Number(fd.listings) || 0, calls: Number(fd.calls) || 0 } : null,
      },
      estimated: est ? { generation: rule('generation'), reading: rule('reading'), retrieval: rule('retrieval'),
                         frontDoor: fd ? Math.floor(((Number(fd.hidden_chars) || 0) + (Number(fd.trimmed_chars) || 0)) / cpt) : null }
        : null,
      workload: (tokens.local_models || []).reduce((a, m) => a + (Number(m.prompt_tokens) || 0) + (Number(m.output_tokens) || 0), 0),
      calls: est ? { total: capSum(() => true, 'calls'), errors: capSum(() => true, 'errors'),
                     memoryReads: capSum(id => /^memory\.recall/.test(id), 'calls'),
                     memoryWrites: capSum(id => id === 'memory.capture' || id === 'memory.remember_fact', 'calls'),
                     local: capSum(id => /^(local_ai|reasoning)\.|^knowledge\.search$/.test(id), 'calls'),
                     since: est.since || null } : null,
      hours: est && Array.isArray(est.hours) ? est.hours : null,
      cpt,
    };
    const e = out.estimated;
    out.estimatedTotal = e ? e.generation + e.reading + e.retrieval + (e.frontDoor || 0) : null;
    out.measuredTotal = out.measured.rtk ? out.measured.rtk.tokens : null;
    // Money (only when the owner chose a model in Settings): kept out of Claude's context at its input price; the
    // local model's writing at its output price. Measured and estimated priced separately.
    const m = tokens.money;
    if (m && isFinite(m.in) && isFinite(m.out)) {
      const usd = (inTok, outTok) => (inTok * m.in + outTok * m.out) / 1e6;
      const estIn = e ? e.reading + e.retrieval + (e.frontDoor || 0) : null;
      out.money = { label: m.label, gbpPerUsd: Number(m.gbp_per_usd) || null, approximate: !!m.approximate,
                    rateFrom: m.rate_from || null, inPerM: Number(m.in), outPerM: Number(m.out),
                    measuredUsd: out.measuredTotal == null ? null : usd(out.measuredTotal, 0),
                    // the estimate's two parts: kept out of Claude's context (input price), written locally (output price)
                    estimatedIn: estIn, estimatedOut: e ? e.generation : null,
                    estimatedUsd: e ? usd(estIn, e.generation) : null };
    } else out.money = null;
    return out;
  }
  // The money line (the owner read "£2.96 + ≈ £13.24" as "1.7M tokens only worth £2"): measured and estimated each
  // carry their own tokens, price and how it was priced. There is no combined total to show.
  function moneyParts(L) {
    const m = L && L.money;
    if (!m) return null;
    const cur = usd => usd == null ? null : m.gbpPerUsd ? '£' + (usd * m.gbpPerUsd).toFixed(2) : '$' + usd.toFixed(2);
    const usdS = usd => '$' + usd.toFixed(2);
    const rate = m.gbpPerUsd ? ` In pounds at ${m.gbpPerUsd} per dollar (${m.approximate || !m.rateFrom ? 'an approximate rate' : m.rateFrom}).` : '';
    return {
      label: m.label,
      measured: L.measuredTotal == null ? null : {
        tokens: fmtNum(L.measuredTotal), money: cur(m.measuredUsd),
        tip: `Measured: ${fmtNum(L.measuredTotal)} tokens RTK cut from command output before Claude read them, priced as ${m.label} input at $${m.inPerM} per million tokens: ${usdS(m.measuredUsd)}.${rate}` },
      estimated: L.estimatedTotal == null ? null : {
        tokens: '≈' + fmtNum(L.estimatedTotal), money: cur(m.estimatedUsd),
        tip: `Estimated (sizes ÷ ${L.cpt}): about ${fmtNum(m.estimatedIn)} tokens kept out of Claude's context (front door, reading and search done locally) at the input price, $${m.inPerM} per million, plus about ${fmtNum(m.estimatedOut)} tokens the local model wrote instead, at the output price, $${m.outPerM} per million: ${usdS(m.estimatedUsd)}.${rate}` },
    };
  }

  // ---- the token-flow map's geometry (pure) ----
  // Tokens run along the flow toward Claude; each saving stage turns a stream away from Claude into one of two
  // channels (m: measured, e: estimated) that end at the "kept out" sink. Flow coordinates: u along the flow, v across
  // it. Wide: x = u, y = v. Tall (phones): x = v, y = u, so the flow runs down the screen.
  // One scale (px per token) for both channels, so they compare honestly; they are never stacked into one band.
  // Positions depend only on the size, never on the data, so nothing jumps when the figures land.
  //   spec: { w, h, tall, lanes: [{id, stages: [stageId]}], order: [stageId] (all lanes, along the flow),
  //           banded: [stageId] (the stages that can turn a band away), streams: {stageId: [{id, v: tokens or null, chan: 'm'|'e'}]} }
  const FLOW_GAP = 2, FLOW_RI = 18, FLOW_LABEL = 94;  // px between bands; a turn's inner radius; room for a stage's label
  function niceRound(x) {
    if (!(x > 0) || !isFinite(x)) return null;
    const mag = Math.pow(10, Math.floor(Math.log10(x)));
    return [1, 2, 5, 10].map(m => m * mag).reverse().find(s => s <= x) || mag;
  }
  function flowLayout(spec) {
    const tall = !!spec.tall, W = Math.max(280, Math.round(spec.w || 0));
    const lanes = spec.lanes || [], order = spec.order || [], streams = spec.streams || {};
    const clamp = (lo, x, hi) => Math.max(lo, Math.min(hi, x));
    const laneOf = {};
    lanes.forEach((l, i) => (l.stages || []).forEach(id => { laneOf[id] = i; }));
    const ok = s => s && s.v != null && isFinite(s.v) && s.v > 0;
    const tot = { m: 0, e: 0 }, seen = { m: false, e: false };
    order.forEach(id => (streams[id] || []).forEach(s => {
      if (s && s.v != null && isFinite(s.v)) seen[s.chan] = true;
      if (ok(s)) tot[s.chan] += Number(s.v);
    }));
    const CH = tall ? 4 : 5;  // half the conduit's width
    const banded = new Set(spec.banded || Object.keys(streams));  // the stages that can turn a band away: fixed, not data
    const reserve = id => banded.has(id) ? 1 : 0;
    let M, H, uEnd, vEc, vMc, heroW = 0;
    const v = [], stage = {}, srcU = [];
    if (!tall) {
      heroW = clamp(200, W * 0.22, 280);
      uEnd = W - 12 - heroW;
      const need = m => 58 + 92 * Math.max(1, lanes.length - 1) + 64 + Math.max(m / 2, 30) + Math.max(m + 30, 64) + Math.max(m / 2, 30) + 12;
      const avail = spec.h > 0 ? spec.h : need(40);
      M = 72;
      while (M > 22 && need(M) > avail) M -= 2;
      H = Math.max(Math.round(avail), Math.ceil(need(M)));
      const extra = H - need(M), top = extra * 0.3, laneGap = 92 + extra * 0.25 / Math.max(1, lanes.length - 1);
      lanes.forEach((l, i) => { v[i] = top + 58 + i * laneGap; srcU[i] = 160; });
      vEc = v[v.length - 1] + 64 + Math.max(M / 2, 30) + extra * 0.45;
      vMc = vEc + Math.max(M + 30, 64);
      // stages left to right: two stages on one lane leave room for their labels; any two leave room for their bands
      const uA = 160 + 24 + M / 2 + 8, uB = uEnd - M / 2 - 40;
      const gaps = order.map((id, i) => {
        if (!i) return 0;
        const band = (reserve(order[i - 1]) + reserve(id)) * M / 2 + 28;
        return laneOf[order[i - 1]] === laneOf[id] ? Math.max(FLOW_LABEL, band) : band;
      });
      const same = order.filter((id, i) => i && laneOf[order[i - 1]] === laneOf[id]).length;
      const free = uB - uA - gaps.reduce((a, b) => a + b, 0);
      if (free < 0) return flowLayout(Object.assign({}, spec, { tall: true }));  // too narrow for the stages side by side
      let u = uA;
      order.forEach((id, i) => {
        if (i) u += gaps[i] + (free > 0 ? (same ? (laneOf[order[i - 1]] === laneOf[id] ? free / same : 0) : free / (order.length - 1 || 1)) : 0);
        stage[id] = { u, lane: laneOf[id] };
      });
    } else {
      M = clamp(16, W * 0.11, 40);
      lanes.forEach((l, i) => { v[i] = 20 + 24 * i; });
      vMc = W - 10 - M / 2;
      vEc = vMc - M - 26;
      let u = 8;
      lanes.forEach((l, i) => { srcU[i] = u + 20; u += 44; });
      order.forEach(id => {
        if (reserve(id)) { stage[id] = { u: u + 50 + M / 2, lane: laneOf[id] }; u = stage[id].u + M / 2 + 12; }
        else { stage[id] = { u: u + 22, lane: laneOf[id] }; u += 48; }
      });
      uEnd = u + 20;
      H = Math.ceil(uEnd + 204 + 64);
    }
    const P = (a, b) => (tall ? [b, a] : [a, b]).map(n => +n.toFixed(1)).join(' ');
    const sweepOut = tall ? 1 : 0;
    const k = Math.max(tot.m, tot.e) > 0 ? M / Math.max(tot.m, tot.e) : 0;
    const bw = s => ok(s) ? Math.max(2, Number(s.v) * k) : 0;
    // the two channels: their full widths, then each stage's streams take slots from the outer edge inward, in flow
    // order, so no two bands cross
    const chans = {};
    ['m', 'e'].forEach(c => {
      const ws = [];
      order.forEach(id => (streams[id] || []).forEach(s => { if (s.chan === c && ok(s)) ws.push(bw(s)); }));
      const h = ws.length ? ws.reduce((a, b) => a + b, 0) + FLOW_GAP * (ws.length - 1) : 0;
      chans[c] = { c: c === 'm' ? vMc : vEc, h, total: seen[c] ? tot[c] : null, n: ws.length };
    });
    const cursor = { m: chans.m.c + chans.m.h / 2, e: chans.e.c + chans.e.h / 2 };
    const bands = [], stubs = [];
    order.forEach(id => {
      const st = stage[id], list = streams[id] || [];
      st.v = v[st.lane];
      const vStart = st.v + CH;
      ['m', 'e'].forEach(c => {
        const items = list.filter(s => s.chan === c && ok(s)).map(s => ({ s, w: bw(s) }));
        if (!items.length) return;
        const G = items.reduce((a, x) => a + x.w, 0) + FLOW_GAP * (items.length - 1);
        const ul0 = st.u - G / 2, vb0 = cursor[c], RoG = G + FLOW_RI;
        let off = 0;
        items.forEach(it => {
          const ul = ul0 + off, ur = ul + it.w, vb = vb0 - off, vt = vb - it.w;
          let Ro = RoG - off;
          if (vb - Ro < vStart) Ro = Math.max(it.w, vb - vStart);
          const Ri = Math.max(0, Ro - it.w), Rc = Ro - it.w / 2, uc = ul + it.w / 2, vc = vb - it.w / 2;
          const A = (r, sw, a, b) => `A${+r.toFixed(1)} ${+r.toFixed(1)} 0 0 ${sw} ${P(a, b)}`;
          bands.push({
            id: it.s.id, stage: id, chan: c, w: it.w, tokens: Number(it.s.v), Ro, Ri,
            ul, ur, vb, vt, vStart,
            d: `M${P(ul, vStart)}L${P(ul, vb - Ro)}${A(Ro, sweepOut, ul + Ro, vb)}L${P(uEnd, vb)}L${P(uEnd, vt)}`
              + `L${P(ur + Ri, vt)}${A(Ri, 1 - sweepOut, ur, vt - Ri)}L${P(ur, vStart)}Z`,
            outer: `M${P(ul, vStart)}L${P(ul, vb - Ro)}${A(Ro, sweepOut, ul + Ro, vb)}L${P(uEnd, vb)}`,
            inner: `M${P(ur, vStart)}L${P(ur, vt - Ri)}${A(Ri, sweepOut, ur + Ri, vt)}L${P(uEnd, vt)}`,
            core: `M${P(uc, vStart)}L${P(uc, vc - Rc)}${A(Rc, sweepOut, uc + Rc, vc)}L${P(uEnd, vc)}`,
          });
          off += it.w + FLOW_GAP;
        });
        cursor[c] = vb0 - G - FLOW_GAP;
      });
      // a stream HomeShed can't see: a short hatched stub, never a band of made-up width
      if (list.length && !list.some(ok) && list.some(s => s.v == null || !isFinite(s.v))) {
        stubs.push({ stage: id, d: `M${P(st.u - 5, vStart)}L${P(st.u - 5, vStart + 26)}L${P(st.u + 5, vStart + 26)}L${P(st.u + 5, vStart)}Z`,
                     at: tall ? [vStart + 30, st.u + 4] : [st.u + 9, vStart + 22] });
      }
      const [x, y] = tall ? [st.v, st.u] : [st.u, st.v];
      st.x = x; st.y = y;
    });
    const lanesOut = lanes.map((l, i) => {
      const stops = [{ id: l.source || l.id, u: srcU[i] }].concat((l.stages || []).map(id => ({ id, u: stage[id].u })), [{ id: 'claude', u: uEnd }]);
      return { id: l.id, v: v[i], u0: srcU[i], u1: uEnd, stops,
               rails: [`M${P(srcU[i], v[i] - CH)}L${P(uEnd, v[i] - CH)}`, `M${P(srcU[i], v[i] + CH)}L${P(uEnd, v[i] + CH)}`],
               body: `M${P(srcU[i], v[i] - CH)}L${P(uEnd, v[i] - CH)}L${P(uEnd, v[i] + CH)}L${P(srcU[i], v[i] + CH)}Z`,
               segs: stops.slice(1).map((s, j) => `M${P(stops[j].u, v[i])}L${P(s.u, v[i])}`) };
    });
    const lastV = v[v.length - 1];
    let claude, hero, rows, legend, sources;
    if (!tall) {
      claude = { x: uEnd, y: v[0] - 30, w: heroW, h: lastV + 24 - (v[0] - 30) };
      const hy = vEc - Math.max(M / 2, 30) - 30;
      hero = { x: uEnd, y: hy, w: heroW, h: vMc + Math.max(M / 2, 30) + 8 - hy };
      rows = { e: { x: uEnd + 18, y: vEc }, m: { x: uEnd + 18, y: vMc }, head: { x: uEnd + 18, y: hy + 20 } };
      const first = order.find(id => reserve(id));
      const edge = first ? stage[first].u - M / 2 - 16 : uEnd - 40;
      legend = { x: 12, y: lastV + 36, w: Math.max(120, edge - 12), h: H - (lastV + 36) - 8 };
      sources = lanes.map((l, i) => ({ id: l.source || l.id, x: 12, y: v[i] - 22, w: 148, h: 44 }));
    } else {
      claude = { x: 8, y: uEnd, w: 116, h: 196 };
      hero = { x: 132, y: uEnd, w: W - 8 - 132, h: 196 };
      rows = { e: { x: 146, y: uEnd + 76 }, m: { x: 146, y: uEnd + 148 }, head: { x: 146, y: uEnd + 22 } };
      legend = { x: 8, y: uEnd + 206, w: W - 16, h: 60 };
      sources = lanes.map((l, i) => ({ id: l.source || l.id, x: v[i], y: srcU[i], w: 0, h: 0 }));
    }
    const tokensPerScale = k ? niceRound((M / 2) / k) : null;
    return { tall, w: W, h: H, M, k, ch: CH, uEnd, lanes: lanesOut, stages: stage, bands, stubs, chans,
             claude, hero, rows, legend, sources, labelX: tall ? 62 : null, labelMax: tall ? vEc - M / 2 - 10 : null,
             scale: tokensPerScale ? { tokens: tokensPerScale, px: tokensPerScale * k } : null };
  }

  // ---- window layout (pure) ----
  const SIZES = ['s', 'm', 'l', 'xl'];
  function normaliseLayout(saved, defaults) {
    const known = defaults.order.slice();
    const s = saved && typeof saved === 'object' ? saved : {};
    const order = (Array.isArray(s.order) ? s.order : []).filter((id, i, a) => known.includes(id) && a.indexOf(id) === i);
    known.forEach(id => { if (!order.includes(id)) order.splice(Math.min(known.indexOf(id), order.length), 0, id); });
    const size = {};
    known.forEach(id => {
      const want = s.size && s.size[id];
      size[id] = SIZES.includes(want) ? want : defaults.size[id] || 'm';
    });
    const min = (Array.isArray(s.min) ? s.min : []).filter((id, i, a) => known.includes(id) && a.indexOf(id) === i);
    return { order, size, min };
  }
  function moveId(order, id, delta) {
    const i = order.indexOf(id), j = Math.max(0, Math.min(order.length - 1, i + delta));
    if (i < 0 || i === j) return order.slice();
    const out = order.slice();
    out.splice(i, 1);
    out.splice(j, 0, id);
    return out;
  }
  function moveBefore(order, id, target, after) {
    if (id === target || !order.includes(id) || !order.includes(target)) return order.slice();
    const out = order.filter(x => x !== id);
    out.splice(out.indexOf(target) + (after ? 1 : 0), 0, id);
    return out;
  }
  function nextSize(s) { return SIZES[(SIZES.indexOf(s) + 1) % SIZES.length]; }

  const pure = { fmtNum, fmtMs, fmtPct, niceTicks, dayKey, dailySeries, hourlySeries, percentile, activityStats,
                 latencyPoints, ledger, moneyParts, flowLayout, niceRound, normaliseLayout, moveId, moveBefore, nextSize, SIZES };
  if (!hasDOM) return pure;

  // ---------------------------------------------------------------------------------------------------------------
  // Shared browser plumbing
  // ---------------------------------------------------------------------------------------------------------------
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* per browser only */ } },
  };
  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function svg(tag, attrs, parent) {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }
  function tokens(node) {
    const cs = getComputedStyle(node || document.documentElement), v = k => cs.getPropertyValue(k).trim();
    return { measured: v('--tm-measured'), estimated: v('--tm-estimated'), live: v('--tm-live'), grid: v('--tm-grid'),
             grid2: v('--tm-grid-2'), ink: v('--tm-ink'), ink2: v('--tm-ink-2'), ink3: v('--tm-ink-3'), ok: v('--ok'),
             warn: v('--warn'), bad: v('--bad'), surface: v('--tm-surface'), glow: parseFloat(v('--tm-glow')) || 0,
             font: v('--tm-font') || 'system-ui', mono: v('--tm-mono') || 'monospace' };
  }
  function rgba(color, a) {  // #rgb, #rrggbb or rgb(): with alpha
    const c = String(color).trim();
    let r, g, b;
    if (c[0] === '#') {
      const h = c.length === 4 ? c.slice(1).split('').map(x => x + x).join('') : c.slice(1, 7);
      [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
    } else {
      const m = c.match(/[\d.]+/g) || [0, 0, 0];
      [r, g, b] = m.map(Number);
    }
    return `rgba(${r},${g},${b},${a})`;
  }
  // One animation loop for every instrument: each job runs per frame until it returns false; nothing runs while the
  // tab is hidden, and the loop stops when no job is left (no continuous redraws).
  const Frame = (() => {
    const jobs = new Set();
    let raf = 0, last = 0;
    function loop(t) {
      raf = 0;
      if (document.hidden) { last = 0; return; }
      const dt = last ? Math.min(0.1, (t - last) / 1000) : 0;
      last = t;
      jobs.forEach(j => { if (j(dt, t) === false) jobs.delete(j); });
      if (jobs.size) raf = requestAnimationFrame(loop); else last = 0;
    }
    const kick = () => { if (!raf && jobs.size && !document.hidden) raf = requestAnimationFrame(loop); };
    document.addEventListener('visibilitychange', kick);
    return { add(j) { jobs.add(j); kick(); }, remove(j) { jobs.delete(j); } };
  })();
  // Bloom without shadowBlur: one radial sprite per colour, drawn once, then stamped where something is lit.
  const sprites = new Map();
  function glowSprite(color) {
    if (sprites.has(color)) return sprites.get(color);
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const g = c.getContext('2d'), rg = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    rg.addColorStop(0, rgba(color, 0.9)); rg.addColorStop(0.25, rgba(color, 0.35)); rg.addColorStop(1, rgba(color, 0));
    g.fillStyle = rg; g.fillRect(0, 0, 64, 64);
    sprites.set(color, c);
    return c;
  }
  function stamp(ctx, color, x, y, r, a) {
    if (!color) return;
    const prev = ctx.globalAlpha;
    ctx.globalAlpha = a == null ? 1 : a;
    ctx.drawImage(glowSprite(color), x - r, y - r, r * 2, r * 2);
    ctx.globalAlpha = prev;
  }
  const themeListeners = new Set();
  const clearSprites = () => sprites.clear();
  new MutationObserver(() => { clearSprites(); themeListeners.forEach(f => f()); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  function onTheme(f) { themeListeners.add(f); }

  // Offscreen components pause (CSS reads .tm-off); a callback hears it too.
  const io = 'IntersectionObserver' in root ? new IntersectionObserver(list => list.forEach(e => {
    e.target.classList.toggle('tm-off', !e.isIntersecting);
    if (e.target._tmVisible) e.target._tmVisible(e.isIntersecting);
  })) : null;
  function watchVisible(node, f) { node._tmVisible = f; if (io) io.observe(node); }

  // ---- Motion: decorative movement on or off ----
  const Motion = (() => {
    const KEY = 'cp-motion', listeners = new Set();
    const reduce = root.matchMedia ? root.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false, addEventListener() {} };
    let pref = store.get(KEY) === 'off' ? 'off' : 'on';
    const why = () => pref === 'off' ? 'off here' : reduce.matches ? 'your system asks for reduced motion'
      : document.body && document.body.classList.contains('no-anim') ? "the Control Panel's animations are off in Settings" : null;
    const allowed = () => !why() && !document.hidden;
    function sync() {
      document.documentElement.classList.toggle('tm-still', !!why());
      listeners.forEach(f => f(allowed()));
    }
    document.addEventListener('visibilitychange', sync);
    if (reduce.addEventListener) reduce.addEventListener('change', sync);
    const watchBody = () => new MutationObserver(sync).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    if (document.body) watchBody(); else document.addEventListener('DOMContentLoaded', watchBody);
    setTimeout(sync, 0);
    return {
      allowed, why, pref: () => pref,
      set(on) { pref = on ? 'on' : 'off'; store.set(KEY, on ? null : 'off'); sync(); },
      on(f) { listeners.add(f); },
    };
  })();

  // ---- Calm: the panel's endless decoration (CSS loops such as live-dot pings and blinks, the Core's idle breath, the
  // neon sparks, the background video) runs only for a while after something real happens: the viewer moves, clicks
  // or types, or a real call arrives (wake()). Otherwise it holds still, each loop parked on its first frame (its
  // resting look), so an idle panel draws nothing at all (the idle-cost pass, 2026-10-28: a few loops kept the GPU
  // repainting every frame). One-shot animations (ripples, flows, entrances) are never touched.
  // Parking is CSS (telemetry.css .tm-calm .tm-held), not Animation.pause(): a scripted pause would take the loop away
  // from CSS for good, and the off-screen pause (.tm-off) would stop working on it.
  const Calm = (() => {
    const AWAKE_MS = 15000, listeners = new Set();
    let awake = false, tmr = 0, lastWake = 0;
    const endless = a => a.effect && a.effect.getTiming && a.effect.getTiming().iterations === Infinity;
    function park(a) {
      const el = a.effect.target, pe = a.effect.pseudoElement;
      if (!el || !el.classList) return;
      el.classList.add(pe === '::after' ? 'tm-held-a' : pe === '::before' ? 'tm-held-b' : 'tm-held');
      try { a.currentTime = 0; } catch (e) { /* detached */ }
    }
    function set(on) {
      if (on === awake) return;
      awake = on;
      if (!on && document.getAnimations) document.getAnimations().forEach(a => { if (endless(a)) park(a); });
      document.documentElement.classList.toggle('tm-calm', !on);
      listeners.forEach(f => f(on));
    }
    function wake() {
      const now = Date.now();
      if (document.hidden || now - lastWake < 500) return;
      lastWake = now;
      clearTimeout(tmr);
      tmr = setTimeout(() => { tmr = 0; set(false); }, AWAKE_MS);
      set(true);
    }
    // a loop that starts while calm (a redraw made a new live dot) is parked at once
    document.addEventListener('animationstart', e => {
      if (awake || !e.target.getAnimations) return;
      e.target.getAnimations({ subtree: true }).forEach(a => {
        if (a.animationName === e.animationName && (a.effect.pseudoElement || '') === (e.pseudoElement || '') && endless(a)) park(a);
      });
    }, true);
    ['pointermove', 'pointerdown', 'keydown', 'wheel'].forEach(t => root.addEventListener(t, wake, { passive: true, capture: true }));
    document.addEventListener('visibilitychange', () => { if (document.hidden) { clearTimeout(tmr); tmr = 0; set(false); } else wake(); });
    awake = true; set(false);  // start still; the first real event wakes it
    return { wake, awake: () => awake, on(f) { listeners.add(f); } };
  })();

  // ---------------------------------------------------------------------------------------------------------------
  // Reactor
  // ---------------------------------------------------------------------------------------------------------------
  const C = 100, TAU = Math.PI * 2;
  const polar = (r, a) => [C + r * Math.cos(a), C + r * Math.sin(a)];
  function arcPath(r, a0, a1) {
    const [x0, y0] = polar(r, a0), [x1, y1] = polar(r, a1);
    return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }
  let _uid = 0;
  class Reactor {
    constructor(host, opts) {
      opts = opts || {};
      this.host = host;
      host.classList.add('tm-reactor');
      const id = 'tmr' + (++_uid);
      const s = this.svg = svg('svg', { viewBox: '0 0 200 200', class: 'tm-reactor-svg', 'aria-hidden': 'true' });
      const defs = svg('defs', {}, s);
      const core = svg('radialGradient', { id: id + 'c' }, defs);
      svg('stop', { offset: '0%', 'stop-color': 'currentColor', 'stop-opacity': '.34', class: 'tm-core-in' }, core);
      svg('stop', { offset: '60%', 'stop-color': 'currentColor', 'stop-opacity': '.08' }, core);
      svg('stop', { offset: '100%', 'stop-color': 'currentColor', 'stop-opacity': '0' }, core);
      const f = svg('filter', { id: id + 'g', x: '-30%', y: '-30%', width: '160%', height: '160%' }, defs);
      svg('feGaussianBlur', { stdDeviation: '2.2', result: 'b' }, f);
      const mg = svg('feMerge', {}, f);
      svg('feMergeNode', { in: 'b' }, mg); svg('feMergeNode', { in: 'SourceGraphic' }, mg);
      // bezel: a hairline ring, 120 fine ticks, a longer one every 30 degrees, quadrant figures for the arc's scale
      const bez = svg('g', { class: 'tm-bezel' }, s);
      svg('circle', { cx: C, cy: C, r: 97, class: 'tm-hair' }, bez);
      svg('circle', { cx: C, cy: C, r: 88.5, class: 'tm-hair faint' }, bez);
      let d = '';
      for (let i = 0; i < 120; i++) {
        const a = i / 120 * TAU - Math.PI / 2, major = i % 10 === 0, r0 = major ? 89.5 : i % 5 === 0 ? 92 : 93.5;
        const [x0, y0] = polar(r0, a), [x1, y1] = polar(96, a);
        d += `M${x0.toFixed(2)} ${y0.toFixed(2)}L${x1.toFixed(2)} ${y1.toFixed(2)}`;
      }
      svg('path', { d, class: 'tm-ticks' }, bez);
      // registration marks at the four compass points, and a fine inner scale (60 marks) inside the orbit
      let reg = '', fine = '';
      for (let i = 0; i < 4; i++) { const a = i * Math.PI / 2, [x0, y0] = polar(99.5, a), [x1, y1] = polar(104, a); reg += `M${x0.toFixed(2)} ${y0.toFixed(2)}L${x1.toFixed(2)} ${y1.toFixed(2)}`; }
      for (let i = 0; i < 60; i++) { const a = i / 60 * TAU, [x0, y0] = polar(i % 5 ? 56.6 : 55.6, a), [x1, y1] = polar(57.8, a); fine += `M${x0.toFixed(2)} ${y0.toFixed(2)}L${x1.toFixed(2)} ${y1.toFixed(2)}`; }
      svg('path', { d: reg, class: 'tm-reg' }, bez);
      svg('path', { d: fine, class: 'tm-fine' }, bez);
      [['0', -90], ['25', 0], ['50', 90], ['75', 180]].forEach(([t, deg]) => {
        const [x, y] = polar(81.5, deg * Math.PI / 180);
        const n = svg('text', { x: x.toFixed(1), y: (y + 2.2).toFixed(1), class: 'tm-scale', 'text-anchor': 'middle' }, bez);
        n.textContent = t;
      });
      this.spokes = svg('g', { class: 'tm-spokes' }, s);
      // the progress arc: a track, the arc, its lit head (the brand mark's activity arc)
      svg('circle', { cx: C, cy: C, r: 70, class: 'tm-track' }, s);
      // no data: a hatched band where the arc would be (provenance in the stroke: solid, dashed, hatched)
      const hp = svg('pattern', { id: id + 'h', width: '4', height: '4', patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, defs);
      svg('rect', { width: '1.4', height: '4', class: 'tm-hatch-line' }, hp);
      svg('circle', { cx: C, cy: C, r: 66, class: 'tm-hatch', stroke: `url(#${id}h)` }, s);
      this.arc = svg('path', { class: 'tm-arc', filter: `url(#${id}g)` }, s);
      this.head = svg('circle', { r: 3.2, class: 'tm-head', filter: `url(#${id}g)` }, s);
      this.pointer = svg('path', { d: 'M0 -1.2L-2.6 -5.6H2.6Z', class: 'tm-pointer' }, s);  // on the bezel, at the arc's head
      this.segs = svg('g', { class: 'tm-segs' }, s);
      // the orbit: two rings and four nodes, like the logo; it turns only while real events arrive
      const orb = this.orbit = svg('g', { class: 'tm-orbit' }, s);
      svg('circle', { cx: C, cy: C, r: 52, class: 'tm-orbit-ring' }, orb);
      [0, 1, 2, 3].forEach(i => { const [x, y] = polar(52, i * TAU / 4 - Math.PI / 4); svg('circle', { cx: x, cy: y, r: 2.1, class: 'tm-node' }, orb); });
      svg('circle', { cx: C, cy: C, r: 46, fill: `url(#${id}c)`, class: 'tm-core' }, s);
      this.ripple = svg('circle', { cx: C, cy: C, r: 46, class: 'tm-ripple' }, s);
      const mid = this.mid = el('div', 'tm-reactor-mid');
      mid.innerHTML = '<span class="tm-r-label"></span><span class="tm-r-value"></span><span class="tm-r-unit"></span><span class="tm-r-period"></span>';
      host.append(s, mid);
      this.label = opts.label || '';
      this.last = null;
      host.setAttribute('role', 'img');
      this.set({ status: 'loading', label: this.label });
    }
    set(st) {
      st = st || {};
      const h = this.host, q = c => this.mid.querySelector(c);
      h.dataset.status = st.status || 'ok';
      h.dataset.kind = st.kind || '';
      h.style.setProperty('--tm-tone', `var(--tm-${st.tone || 'measured'})`);
      q('.tm-r-label').textContent = st.label || this.label;
      q('.tm-r-value').textContent = st.status === 'loading' ? '' : st.value != null ? st.value : '—';
      q('.tm-r-unit').textContent = st.status === 'loading' ? '' : st.unit || '';
      q('.tm-r-period').textContent = st.status === 'loading' ? 'Reading…' : st.period || '';
      // spectral ring: one spoke per period (day or hour), length by value; a gap is a dot
      this.spokes.textContent = '';
      const sp = st.spokes;
      if (sp && sp.values && sp.values.length) {
        const vals = sp.values, max = Math.max(1, ...vals.filter(v => v != null)), n = vals.length;
        vals.forEach((v, i) => {
          const a = -Math.PI / 2 + (i + 0.5) / n * TAU * 0.94 + TAU * 0.03;
          if (v == null) {
            const [x, y] = polar(77, a); svg('circle', { cx: x, cy: y, r: 0.6, class: 'tm-spoke gap' }, this.spokes); return;
          }
          const len = 1.5 + 9 * Math.sqrt(v / max), [x0, y0] = polar(75.5, a), [x1, y1] = polar(75.5 + len, a);
          const p = svg('path', { d: `M${x0.toFixed(2)} ${y0.toFixed(2)}L${x1.toFixed(2)} ${y1.toFixed(2)}`,
                                  class: 'tm-spoke' + (i === n - 1 ? ' now' : '') }, this.spokes);
          if (sp.titles && sp.titles[i]) svg('title', {}, p).textContent = sp.titles[i];
        });
      }
      // progress arc
      const fr = st.arc && st.arc.frac != null ? Math.max(0, Math.min(1, st.arc.frac)) : null;
      this.arc.style.display = this.head.style.display = this.pointer.style.display = fr == null ? 'none' : '';
      if (fr != null) {
        const a0 = -Math.PI / 2, a1 = a0 + Math.max(0.002, fr) * TAU * 0.9999;
        this.arc.setAttribute('d', arcPath(70, a0, a1));
        this.arc.classList.toggle('est', !!st.arc.dashed);
        const [x, y] = polar(70, a1);
        this.head.setAttribute('cx', x.toFixed(2)); this.head.setAttribute('cy', y.toFixed(2));
        const [px, py] = polar(88, a1);
        this.pointer.setAttribute('transform', `translate(${px.toFixed(2)} ${py.toFixed(2)}) rotate(${(a1 * 180 / Math.PI + 90).toFixed(1)})`);
        this.arc.innerHTML = '';
        if (st.arc.title) svg('title', {}, this.arc).textContent = st.arc.title;
      }
      // composition ring: shares of a whole, a 2-degree gap between
      this.segs.textContent = '';
      const segs = (st.segments || []).filter(x => x.v > 0), tot = segs.reduce((a, x) => a + x.v, 0);
      if (tot > 0) {
        let a = -Math.PI / 2;
        const gap = segs.length > 1 ? 0.035 : 0;
        segs.forEach(x => {
          const span = x.v / tot * TAU;
          if (span - gap > 0.01) {
            const p = svg('path', { d: arcPath(61, a + gap / 2, a + span - gap / 2), class: 'tm-seg' + (x.dashed ? ' est' : ''),
                                    style: `stroke:var(--tm-${x.tone || 'estimated'});opacity:${x.alpha || 1}` }, this.segs);
            if (x.title) svg('title', {}, p).textContent = x.title;
          }
          a += span;
        });
      }
      // orbit speed from a real rate (events a minute); none: it stands still
      const rate = Number(st.rate) || 0;
      this.orbit.classList.toggle('turning', rate > 0);
      if (rate > 0) this.orbit.style.animationDuration = Math.max(4, Math.min(90, 60 / Math.sqrt(rate))).toFixed(1) + 's';
      // a ripple once when the headline figure goes up (a real change, never on a timer)
      const num = Number(st.raw);
      if (isFinite(num) && this.last != null && num > this.last && Motion.allowed()) {
        this.ripple.classList.remove('go'); this.ripple.getBoundingClientRect(); this.ripple.classList.add('go');
      }
      if (isFinite(num)) this.last = num;
      h.setAttribute('aria-label', st.aria || [st.label || this.label, st.value, st.unit, st.period].filter(Boolean).join(' '));
    }
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Pulse
  // ---------------------------------------------------------------------------------------------------------------
  class Pulse {
    constructor(host, opts) {
      opts = opts || {};
      this.host = host;
      host.classList.add('tm-pulse');
      this.plot = el('div', 'tm-pulse-plot');
      this.cv = el('canvas', 'tm-pulse-cv');
      this.cv.tabIndex = 0;
      this.cv.setAttribute('role', 'img');
      this.tip = el('div', 'tm-pulse-tip'); this.tip.hidden = true;
      this.headDot = el('span', 'tm-pulse-head'); this.headDot.hidden = true;
      this.msg = el('div', 'tm-pulse-msg'); this.msg.hidden = true;
      this.plot.append(this.cv, this.headDot, this.tip, this.msg);
      this.legend = el('div', 'tm-pulse-legend');
      this.table = el('div', 'tm-pulse-table'); this.table.hidden = true;
      host.append(this.plot, this.legend, this.table);
      this.ctx = this.cv.getContext('2d');
      this.data = { series: [] };
      this.hover = null;
      this.compact = !!opts.compact;
      const ro = 'ResizeObserver' in root ? new ResizeObserver(() => this.draw()) : null;
      if (ro) ro.observe(this.plot); else root.addEventListener('resize', () => this.draw());
      onTheme(() => this.draw());
      watchVisible(this.plot, v => { if (v) this.draw(); });
      this.cv.addEventListener('pointermove', e => this.inspectAt(e.offsetX));
      this.cv.addEventListener('pointerleave', () => { this.hover = null; this.draw(); });
      this.cv.addEventListener('keydown', e => {
        const n = this.xs ? this.xs.length : 0;
        if (!n) return;
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault();
          const cur = this.hover == null ? n - 1 : this.hover + (e.key === 'ArrowLeft' ? -1 : 1);
          this.hover = Math.max(0, Math.min(n - 1, cur)); this.draw();
        } else if (e.key === 'Home' || e.key === 'End') { e.preventDefault(); this.hover = e.key === 'Home' ? 0 : n - 1; this.draw(); }
        else if (e.key === 'Escape') { this.hover = null; this.draw(); }
      });
      this.cv.addEventListener('blur', () => { this.hover = null; this.draw(); });
    }
    // {series:[{name, tone, dashed, points:[{t, v, bad?}]}], fmtY, fmtX, fmtTip, kind:'line'|'dots', message, label}
    set(d) {
      const prevLast = this._lastT;
      this.data = d || { series: [] };
      const pts = (this.data.series[0] || {}).points || [];
      const lastReal = [...pts].reverse().find(p => p.v != null);
      this._lastT = lastReal ? lastReal.t + ':' + lastReal.v : null;
      this._ping = prevLast !== undefined && prevLast !== null && this._lastT !== prevLast;
      this.hover = null;
      this.msg.hidden = !this.data.message;
      this.msg.textContent = this.data.message || '';
      this.legend.innerHTML = (this.data.series || []).length > 1 || this.data.legendAlways
        ? this.data.series.map(s => `<span class="tm-key${s.dashed ? ' est' : ''}" style="--k:var(--tm-${s.tone || 'measured'})"><i></i>${esc(s.name)}</span>`).join('')
        : '';
      this.cv.setAttribute('aria-label', this.data.label || 'Chart');
      this.renderTable();
      this.draw();
    }
    showTable(on) { this.table.hidden = !on; this.plot.hidden = !!on; if (!on) this.draw(); }
    renderTable() {
      const ss = this.data.series || [], fx = this.data.fmtX || (t => new Date(t).toLocaleString()), fy = this.data.fmtY || fmtNum;
      if (!ss.length || !ss[0].points.length) { this.table.innerHTML = '<p class="tm-empty">Nothing to list.</p>'; return; }
      const rows = ss[0].points.map((p, i) => `<tr><th scope="row">${esc(fx(p.t))}</th>${ss.map(s => {
        const q = s.points[i]; return `<td>${q && q.v != null ? esc(fy(q.v)) : '<span class="tm-gap">no data</span>'}</td>`;
      }).join('')}</tr>`).reverse().join('');
      this.table.innerHTML = `<table><caption class="tm-sr">${esc(this.data.label || '')}</caption><thead><tr><th scope="col">When</th>${ss.map(s => `<th scope="col">${esc(s.name)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`;
    }
    inspectAt(x) {
      if (!this.xs || !this.xs.length) return;
      let best = 0, bd = Infinity;
      this.xs.forEach((px, i) => { const dd = Math.abs(px - x); if (dd < bd) { bd = dd; best = i; } });
      if (best !== this.hover) { this.hover = best; this.draw(); }
    }
    draw() {
      const cv = this.cv, ctx = this.ctx, r = this.plot.getBoundingClientRect();
      if (!r.width || !r.height || this.plot.hidden) return;
      const dpr = root.devicePixelRatio || 1, W = r.width, H = r.height;
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const T = tokens(this.host), ss = this.data.series || [], fy = this.data.fmtY || fmtNum;
      const pad = { l: this.compact ? 34 : 46, r: 12, t: 12, b: this.compact ? 18 : 22 };
      const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
      const all = ss.flatMap(s => s.points.map(p => p.v)).filter(v => v != null);
      const pts0 = (ss[0] && ss[0].points) || [];
      // the precision grid: fine minor lines, majors at the ticks, corner marks
      const yt = niceTicks(0, all.length ? Math.max(...all) : 1, this.compact ? 3 : 4);
      const ty = v => pad.t + ph - (v - yt.min) / (yt.max - yt.min || 1) * ph;
      const t0 = pts0.length ? pts0[0].t : 0, t1 = pts0.length ? pts0[pts0.length - 1].t : 1;
      const tx = t => pad.l + (t1 === t0 ? pw / 2 : (t - t0) / (t1 - t0) * pw);
      ctx.lineWidth = 1;
      ctx.strokeStyle = T.grid2;
      ctx.beginPath();
      for (let i = 0; i <= 40; i++) { const x = Math.round(pad.l + pw * i / 40) + 0.5; ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); }
      for (let i = 0; i <= 12; i++) { const y = Math.round(pad.t + ph * i / 12) + 0.5; ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); }
      ctx.stroke();
      ctx.strokeStyle = T.grid;
      ctx.beginPath();
      yt.ticks.forEach(v => { const y = Math.round(ty(v)) + 0.5; ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); });
      ctx.stroke();
      // corner marks and axis ticks: fine technical markings
      ctx.strokeStyle = T.ink3; ctx.beginPath();
      [[pad.l, pad.t, 1, 1], [pad.l + pw, pad.t, -1, 1], [pad.l, pad.t + ph, 1, -1], [pad.l + pw, pad.t + ph, -1, -1]].forEach(([x, y, sx, sy]) => {
        ctx.moveTo(x + 0.5, y + 0.5 + sy * 7); ctx.lineTo(x + 0.5, y + 0.5); ctx.lineTo(x + 0.5 + sx * 7, y + 0.5);
      });
      for (let i = 0; i <= 40; i++) { const x = Math.round(pad.l + pw * i / 40) + 0.5; ctx.moveTo(x, pad.t + ph); ctx.lineTo(x, pad.t + ph + (i % 5 ? 2 : 4)); }
      ctx.stroke();
      ctx.font = `${this.compact ? 10 : 10.5}px ${T.mono}`;
      ctx.fillStyle = T.ink2; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      yt.ticks.forEach(v => ctx.fillText(fy(v), pad.l - 7, ty(v)));
      // x labels: about one per 90 px, from the data's own times
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      const fx = this.data.fmtAxisX || this.data.fmtX || (t => new Date(t).toLocaleDateString());
      if (pts0.length) {
        const every = Math.max(1, Math.ceil(pts0.length / Math.max(2, Math.floor(pw / 90))));
        for (let i = pts0.length - 1; i >= 0; i -= every) ctx.fillText(fx(pts0[i].t), Math.min(W - 24, Math.max(pad.l + 18, tx(pts0[i].t))), pad.t + ph + 6);
      }
      this.xs = pts0.map(p => tx(p.t));
      // the traces: a soft wide pass for the phosphor glow, then the thin bright line; a faint fill under the first
      const glow = T.glow;
      ss.forEach((s, si) => {
        const col = T[s.tone || 'measured'] || T.measured;
        if (this.data.kind === 'dots') {
          s.points.forEach(p => {
            if (p.v == null) return;
            ctx.beginPath(); ctx.arc(tx(p.t), ty(p.v), 2.2, 0, TAU);
            ctx.fillStyle = p.bad ? T.bad : rgba(col, 0.85); ctx.fill();
          });
          return;
        }
        const segs = [];
        let cur = [];
        s.points.forEach(p => { if (p.v == null) { if (cur.length) segs.push(cur); cur = []; } else cur.push([tx(p.t), ty(p.v)]); });
        if (cur.length) segs.push(cur);
        ctx.setLineDash(s.dashed ? [5, 4] : []);
        if (si === 0) segs.forEach(seg => {
          if (seg.length < 2) return;
          const g = ctx.createLinearGradient(0, pad.t, 0, pad.t + ph);
          g.addColorStop(0, rgba(col, glow ? 0.22 : 0.14)); g.addColorStop(1, rgba(col, 0));
          ctx.beginPath(); ctx.moveTo(seg[0][0], pad.t + ph); seg.forEach(([x, y]) => ctx.lineTo(x, y)); ctx.lineTo(seg[seg.length - 1][0], pad.t + ph);
          ctx.fillStyle = g; ctx.fill();
        });
        // phosphor: a wide faint pass, a mid pass, then the thin bright core (no shadowBlur: the research's rule)
        const line = (w, a) => segs.forEach(seg => {
          ctx.beginPath();
          seg.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
          if (seg.length === 1) { ctx.arc(seg[0][0], seg[0][1], 1.6, 0, TAU); }
          ctx.lineWidth = w; ctx.strokeStyle = rgba(col, a); ctx.stroke();
        });
        ctx.lineJoin = 'round'; ctx.lineCap = 'round';
        if (glow) { line(7, 0.06 * glow); line(3.5, 0.14 * glow); }
        line(1.6, s.dashed ? 0.8 : 1);
        ctx.setLineDash([]);
      });
      // the newest real point: a lit marker; it pings once when a new value arrives
      const lp = [...pts0].reverse().find(p => p.v != null);
      if (lp && this.data.kind !== 'dots') {
        const x = tx(lp.t), y = ty(lp.v), col = T[(ss[0] && ss[0].tone) || 'measured'];
        if (glow) stamp(ctx, col, x, y, 16, 0.55 * glow);
        ctx.beginPath(); ctx.arc(x, y, 2.8, 0, TAU); ctx.fillStyle = col; ctx.fill();
        this.headDot.hidden = false;
        this.headDot.style.cssText = `left:${x}px;top:${y}px;--k:var(--tm-${(ss[0] && ss[0].tone) || 'measured'})`;
        if (this._ping && Motion.allowed()) { this.headDot.classList.remove('ping'); void this.headDot.offsetWidth; this.headDot.classList.add('ping'); }
        this._ping = false;
      } else this.headDot.hidden = true;
      // inspection: a crosshair, the points, a readout
      if (this.hover != null && pts0[this.hover]) {
        const i = this.hover, x = this.xs[i];
        ctx.strokeStyle = T.ink2; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
        ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, pad.t); ctx.lineTo(Math.round(x) + 0.5, pad.t + ph); ctx.stroke(); ctx.setLineDash([]);
        const rows = ss.map(s => {
          const p = s.points[i];
          if (p && p.v != null) {
            ctx.beginPath(); ctx.arc(x, ty(p.v), 4, 0, TAU); ctx.fillStyle = T.surface; ctx.fill();
            ctx.lineWidth = 2; ctx.strokeStyle = T[s.tone || 'measured']; ctx.stroke();
          }
          return `<div><i style="--k:var(--tm-${s.tone || 'measured'})"></i>${esc(s.name)}<b>${p && p.v != null ? esc((this.data.fmtTip || fy)(p.v, p)) : 'no data'}</b></div>`;
        }).join('');
        const fxt = this.data.fmtX || fx;
        this.tip.innerHTML = `<div class="tm-tip-t">${esc(fxt(pts0[i].t))}</div>${rows}`;
        this.tip.hidden = false;
        const tw = this.tip.offsetWidth;
        this.tip.style.left = Math.max(4, Math.min(W - tw - 4, x + (x > W / 2 ? -tw - 12 : 12))) + 'px';
        this.tip.style.top = pad.t + 4 + 'px';
      } else this.tip.hidden = true;
    }
  }

  // ---------------------------------------------------------------------------------------------------------------
  // FlowMap: the token pipeline as a flow (SVG, drawn 1:1 in pixels so the text stays crisp at every width).
  // Conduits carry tokens toward Claude (hatched: what reaches Claude is the provider's count, not HomeShed's); each
  // saving stage turns a band away into the "kept out" sink, its width proportional to the tokens. Solid edges are
  // measured, dashed are estimates, a hatched stub is a stream HomeShed can't see. A conduit lights and a particle runs
  // only for a real call.
  // ---------------------------------------------------------------------------------------------------------------
  let flowSeq = 0;
  const hexPts = (x, y, r) => Array.from({ length: 6 }, (_, k) => { const a = Math.PI / 6 + k * Math.PI / 3; return `${(x + r * Math.cos(a)).toFixed(1)},${(y + r * Math.sin(a)).toFixed(1)}`; }).join(' ');
  class FlowMap {
    // opts: {label, lanes: [{id, source, stages}], order, banded: [stageId], names: {id}, tones: {id}, bandNames: {id},
    //        quiet: () => bool (no particles), onSelect}
    constructor(host, opts) {
      this.host = host;
      this.opts = opts;
      host.classList.add('tm-flow');
      this.uid = 'tmf' + (++flowSeq);
      this.wrap = el('div', 'tm-flow-stage');
      this.svg = svg('svg', { class: 'tm-flow-svg', role: 'group', 'aria-label': opts.label || 'Token flow' });
      this.legendEl = el('div', 'tm-flow-legend');
      this.wrap.append(this.svg, this.legendEl);
      host.appendChild(this.wrap);
      this.data = { streams: {}, text: {}, sink: null, note: '' };
      this.selected = null;
      this.particles = [];
      this.live = {};
      this.visible = true;
      this.sig = '';
      if ('ResizeObserver' in root) new ResizeObserver(() => this.schedule()).observe(this.wrap);
      watchVisible(this.wrap, v => { this.visible = v; });
      this.build();
    }
    schedule() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => { this._raf = 0; this.build(); });
    }
    update(data) {
      this.data = Object.assign({ streams: {}, text: {}, sink: null, note: '' }, data || {});
      if (!this.build()) this.paint();
    }
    // Draws the shapes again only when the size or a band's figure changed; the text is painted in place.
    build() {
      const w = Math.round(this.wrap.clientWidth);
      if (!w) return false;  // hidden: drawn once it shows (the resize observer hears it)
      const tall = w < 640, h = tall ? 0 : Math.round(this.wrap.clientHeight);
      const streams = this.data.streams || {};
      const sig = JSON.stringify([w, h, streams]);
      if (sig === this.sig) return false;
      this.sig = sig;
      const o = this.opts;
      const G = this.geo = flowLayout({ w, h: h >= 240 ? h : 0, tall, lanes: o.lanes, order: o.order, banded: o.banded, streams });
      this.host.dataset.layout = G.tall ? 'tall' : 'wide';  // the layout runs down the screen when the stages can't sit side by side
      const s = this.svg;
      s.textContent = '';
      s.setAttribute('viewBox', `0 0 ${G.w} ${G.h}`);
      s.style.height = G.h + 'px';
      const defs = svg('defs', {}, s);
      const pat = svg('pattern', { id: this.uid + 'h', width: 5, height: 5, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, defs);
      svg('line', { x1: 0.5, y1: 0, x2: 0.5, y2: 5, class: 'tm-hatch-ln' }, pat);
      const hatch = `url(#${this.uid}h)`;
      const P = (u, v) => G.tall ? [v, u] : [u, v];

      // the sink and Claude, behind everything that flows into them
      const boxes = svg('g', { class: 'tm-boxes' }, s);
      const C = G.claude, Hr = G.hero;
      const cg = svg('g', { class: 'tm-claude tm-stage', 'data-node': 'claude', tabindex: '0', role: 'button' }, boxes);
      svg('rect', { x: C.x, y: C.y, width: C.w, height: C.h, rx: 10, class: 'tm-claude-box' }, cg);
      svg('rect', { x: C.x, y: C.y, width: C.w, height: C.h, rx: 10, fill: hatch, class: 'tm-claude-hatch' }, cg);
      const ct = (y, cls, t) => { const n = svg('text', { x: C.x + 14, y: C.y + y, class: cls }, cg); n.textContent = t; return n; };
      ct(26, 'tm-cname', o.names.claude || 'Claude');
      (G.tall ? ['What reaches it', "is the provider's", 'count, not ours.'] : ['What still reaches it is', "the provider's own count,", 'not sent to HomeShed.'])
        .forEach((t, i) => ct(46 + i * 15, 'tm-cnote', t));
      this.claudeCache = ct(C.h - 14, 'tm-cnote dim', 'Cached input: no data');
      const hg = svg('g', { class: 'tm-sink tm-stage', 'data-node': 'sink', tabindex: '0', role: 'button' }, boxes);
      svg('rect', { x: Hr.x, y: Hr.y, width: Hr.w, height: Hr.h, rx: 12, class: 'tm-sink-box' }, hg);
      const R = G.rows;
      const hh = svg('text', { x: R.head.x, y: R.head.y, class: 'tm-hhead' }, hg);
      hh.textContent = o.names.sink || 'Kept out of Claude';
      this.sinkSub = svg('text', { x: R.head.x, y: R.head.y + 15, class: 'tm-hsub' }, hg);
      this.sinkRows = {};
      ['e', 'm'].forEach(c => {
        const r = R[c], g = svg('g', { class: 'tm-hrow ' + c }, hg);
        svg('line', { x1: r.x - 9, y1: r.y - 24, x2: r.x - 9, y2: r.y + 30, class: 'tm-gauge' }, g);
        this.sinkRows[c] = { v: svg('text', { x: r.x, y: r.y - 2, class: 'tm-hv' }, g), k: svg('text', { x: r.x, y: r.y + 14, class: 'tm-hk' }, g),
                             m: svg('text', { x: r.x, y: r.y + 29, class: 'tm-hm' }, g), g };
      });

      // the bands: under the conduits, so a band crossing a conduit passes beneath it
      const bg = svg('g', { class: 'tm-bands' }, s);
      this.bandEls = {};
      G.bands.forEach(b => {
        const g = svg('g', { class: `tm-band ${b.chan}`, 'data-stage': b.stage }, bg);
        svg('path', { d: b.d, class: 'tm-band-fill' }, g);
        svg('path', { d: b.outer, class: 'tm-band-edge' }, g);
        svg('path', { d: b.inner, class: 'tm-band-edge' }, g);
        svg('path', { d: b.core, class: 'tm-band-core' }, g);
        const t = svg('title', {}, g);
        t.textContent = `${(o.bandNames || {})[b.id] || b.id}: ${b.chan === 'e' ? 'about ' : ''}${fmtNum(b.tokens)} tokens kept out of Claude (${b.chan === 'e' ? 'an estimate' : 'measured'})`;
        (this.bandEls[b.stage] = this.bandEls[b.stage] || []).push(g);
      });
      G.stubs.forEach(st => {
        const g = svg('g', { class: 'tm-stub', 'data-stage': st.stage }, bg);
        svg('path', { d: st.d, fill: hatch, class: 'tm-stub-fill' }, g);
        svg('path', { d: st.d, class: 'tm-stub-edge' }, g);
        const t = svg('text', { x: st.at[0], y: st.at[1], class: 'tm-stub-t' }, g);
        t.textContent = 'no data';
      });

      // the conduits: two fine rails, hatched inside (their volume isn't measured), a fine scale along the top
      const lg = svg('g', { class: 'tm-conduits', 'aria-hidden': 'true' }, s);
      this.segEls = {};
      G.lanes.forEach(l => {
        const g = svg('g', { class: 'tm-conduit' }, lg);
        svg('path', { d: l.body, class: 'tm-cond-body' }, g);
        svg('path', { d: l.body, fill: hatch, class: 'tm-cond-hatch' }, g);
        l.rails.forEach(d => svg('path', { d, class: 'tm-rail' }, g));
        let ticks = '';
        for (let u = l.u0 + 12, i = 1; u < l.u1 - 4; u += 12, i++) {
          const n = i % 5 ? 2 : 4, [x0, y0] = P(u, l.v - G.ch - 1), [x1, y1] = P(u, l.v - G.ch - 1 - n);
          ticks += `M${x0} ${y0}L${x1} ${y1}`;
        }
        svg('path', { d: ticks, class: 'tm-cond-ticks' }, g);
        l.segs.forEach((d, j) => { this.segEls[l.id + ':' + j] = svg('path', { d, class: 'tm-cseg' }, g); });
        const [px, py] = P(l.u1, l.v);
        svg('path', { d: G.tall ? `M${px - 8} ${py - 3}L${px} ${py + 3}L${px + 8} ${py - 3}` : `M${px - 3} ${py - 8}L${px + 3} ${py}L${px - 3} ${py + 8}`, class: 'tm-port' }, g);
      });

      // the stages and the sources: a hex on the conduit, the name, the figure, small badges
      const ng = svg('g', { class: 'tm-stages' }, s);
      this.nodeEls = { claude: { g: cg }, sink: { g: hg } };
      const label = (id, g, x, y, anchor, lines) => {
        const t = lines.map((cls, i) => svg('text', { x, y: y + i * 15, 'text-anchor': anchor, class: cls }, g));
        t[0].textContent = o.names[id] || id;
        return t;
      };
      G.lanes.forEach((l, i) => {
        const src = G.sources[i], id = l.stops[0].id;
        const g = svg('g', { class: 'tm-stage tm-src', 'data-node': id, tabindex: '0', role: 'button', style: `--k:var(--tm-${o.tones[id] || 'live'})` }, ng);
        let t;
        if (!G.tall) {
          svg('rect', { x: src.x, y: src.y, width: src.w, height: src.h, rx: 8, class: 'tm-src-box' }, g);
          t = label(id, g, src.x + 12, src.y + 18, 'start', ['tm-sname', 'tm-sbadge']);
        } else {
          svg('polygon', { points: hexPts(src.x, src.y, 7), class: 'tm-snode src' }, g);
          svg('path', { d: `M${src.x + 9} ${src.y}H${G.labelX - 6}`, class: 'tm-leader' }, g);
          svg('rect', { x: 2, y: src.y - 20, width: G.labelMax, height: 42, rx: 8, class: 'tm-hit' }, g);
          t = label(id, g, G.labelX, src.y - 3, 'start', ['tm-sname', 'tm-sbadge']);
        }
        this.nodeEls[id] = { g, fig: null, badges: [t[1]], pills: [] };
      });
      const banded = new Set(o.banded || []);
      o.order.forEach(id => {
        const st = G.stages[id];
        const g = svg('g', { class: 'tm-stage', 'data-node': id, tabindex: '0', role: 'button', style: `--k:var(--tm-${o.tones[id] || 'live'})` }, ng);
        let t;
        if (!G.tall) {
          svg('rect', { x: st.x - 60, y: st.y - 56, width: 120, height: 64, rx: 8, class: 'tm-hit' }, g);
          t = label(id, g, st.x, st.y - 41, 'middle', ['tm-sname', 'tm-sfig', 'tm-sbadge']);
        } else {
          const top = banded.has(id) ? st.y - 50 - G.M / 2 : st.y - 22;
          svg('rect', { x: 2, y: top + 1, width: G.labelMax, height: banded.has(id) ? 46 : 46, rx: 8, class: 'tm-hit' }, g);
          if (!banded.has(id)) svg('path', { d: `M${st.x + 9} ${st.y}H${G.labelX - 6}`, class: 'tm-leader' }, g);
          t = label(id, g, G.labelX, top + 14, 'start', ['tm-sname', 'tm-sfig', 'tm-sbadge']);
        }
        svg('polygon', { points: hexPts(st.x, st.y, 8), class: 'tm-snode' }, g);
        this.nodeEls[id] = { g, lines: t };
      });
      Object.entries(this.nodeEls).forEach(([id, e]) => {
        e.g.addEventListener('click', () => this.select(id));
        e.g.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); this.select(id); } });
      });
      this.pg = svg('g', { class: 'tm-particles', 'aria-hidden': 'true' }, s);
      this.particles = [];
      const L = G.legend;
      Object.assign(this.legendEl.style, { left: L.x + 'px', top: L.y + 'px', width: L.w + 'px' });
      this.paint();
      this.mark();
      this.paintLive();
      return true;
    }
    // the words and figures, in place
    paint() {
      const G = this.geo;
      if (!G) return;
      const D = this.data, T = D.text || {}, o = this.opts;
      for (const id in this.nodeEls) {
        const e = this.nodeEls[id], x = T[id] || {};
        e.g.dataset.status = x.status || 'ok';
        e.g.setAttribute('aria-label', `${o.names[id] || id}: ${x.aria || [x.figure].concat(x.badges || []).filter(Boolean).join(', ') || 'no data'}. Show how it is worked out.`);
        if (e.lines) {
          const [, fig, badge] = e.lines, b = x.badges || [];
          if (x.figure) { fig.textContent = x.figure; fig.setAttribute('class', 'tm-sfig ' + (x.kind || '')); badge.textContent = b[0] || ''; }
          else { fig.textContent = b[0] || ''; fig.setAttribute('class', 'tm-sbadge'); badge.textContent = b[1] || ''; }
        } else if (e.badges) e.badges[0].textContent = (x.badges || []).join(', ');
      }
      if (this.claudeCache) this.claudeCache.textContent = G.tall ? 'Cache: no data' : (T.claude && T.claude.badges && T.claude.badges[0]) || 'Cached input: no data';
      const sk = D.sink || {};
      this.sinkSub.textContent = G.tall ? 'Never added together' : sk.sub || 'Two separate counts, never added';
      ['e', 'm'].forEach(c => {
        const r = this.sinkRows[c], x = sk[c] || {};
        r.v.textContent = '';
        r.v.setAttribute('class', 'tm-hv' + (x.value ? '' : ' none'));
        r.v.appendChild(document.createTextNode(x.value || (D.loading ? '…' : 'no data')));
        if (x.value) { const u = svg('tspan', { class: 'tm-hu', dx: 5 }, r.v); u.textContent = 'tokens'; }
        r.k.textContent = x.kind || (c === 'm' ? 'measured' : 'estimated');
        r.m.textContent = x.money ? x.money + (G.tall || !sk.moneyNote ? '' : ' ' + sk.moneyNote) : '';
        let tt = r.g.querySelector('title');
        if (!tt) tt = svg('title', {}, r.g);
        tt.textContent = x.tip || '';
      });
      const sc = G.scale;
      this.legendEl.innerHTML = '<div class="tm-lg-keys"><span class="prov m">measured</span><span class="prov e">estimate</span><span class="prov u">no data</span></div>'
        + (sc ? `<div class="tm-lg-scale"><i style="height:${Math.max(2, Math.round(sc.px))}px"></i><span>this wide = ${esc(fmtNum(sc.tokens))} tokens, one scale for both</span></div>` : '')
        + (D.note ? `<div class="tm-lg-note">${esc(D.note)}</div>` : '');
    }
    mark() {
      for (const id in this.nodeEls) {
        const g = this.nodeEls[id].g;
        g.classList.toggle('sel', id === this.selected);
        g.setAttribute('aria-pressed', String(id === this.selected));
      }
      this.svg.classList.toggle('has-sel', !!this.selected);
      for (const id in this.bandEls) this.bandEls[id].forEach(g => g.classList.toggle('sel', id === this.selected || this.selected === 'sink'));
    }
    select(id) {
      this.selected = this.selected === id ? null : id;
      this.mark();
      if (this.opts.onSelect) this.opts.onSelect(this.selected);
    }
    paintLive() {
      const now = performance.now(), on = k => this.live[k] && this.live[k].until > now;
      for (const k in this.segEls) {
        this.segEls[k].classList.toggle('live', on(k));
        if (on(k)) this.segEls[k].style.setProperty('--k', `var(--tm-${this.live[k].tone || 'live'})`);
      }
      for (const id in this.bandEls) this.bandEls[id].forEach(g => g.classList.toggle('live', on('band:' + id)));
    }
    // One real event along a route of ids (a source first; 'sink' last when the stage before it kept the tokens out):
    // the conduit lights for 30 s and, motion allowed, one particle runs it.
    flow(route, tone) {
      const G = this.geo;
      if (!G || !route || route.length < 2) return;
      Calm.wake();  // a real call: the panel's loops may move for a while
      const end = route[route.length - 1], divert = end === 'sink' ? route[route.length - 2] : null, stop = divert || end;
      const lane = G.lanes.find(l => l.stops[0].id === route[0]);
      if (!lane) return;
      const si = lane.stops.findIndex(x => x.id === stop);
      if (si < 1) return;
      const until = performance.now() + 30000;
      for (let j = 0; j < si; j++) this.live[lane.id + ':' + j] = { until, tone };
      const band = divert && G.bands.filter(b => b.stage === divert).sort((a, b) => b.w - a.w)[0];
      if (band) this.live['band:' + divert] = { until, tone };
      this.paintLive();
      clearTimeout(this._cool);
      this._cool = setTimeout(() => this.paintLive(), 30100);
      if (!Motion.allowed() || !this.visible || (this.opts.quiet && this.opts.quiet()) || this.particles.length > 24) return;
      const at = u => (G.tall ? [lane.v, u] : [u, lane.v]).join(' ');
      const d = `M${at(lane.stops[0].u)}L${at(lane.stops[si].u)}` + (band ? 'L' + band.core.slice(1) : '');
      const path = svg('path', { d, class: 'tm-ppath' }, this.pg);
      const len = path.getTotalLength();
      const dot = svg('circle', { r: 3, class: 'tm-particle', style: `--k:var(--tm-${tone || 'live'})` }, this.pg);
      this.particles.push({ dot, path, len, t: 0, speed: len / 1.6 });
      if (!this._job) { this._job = dt => this.tick(dt); Frame.add(this._job); }
    }
    tick(dt) {
      dt = Math.min(0.05, dt);
      if (!Motion.allowed() || !this.visible) { this.particles.forEach(p => { p.dot.remove(); p.path.remove(); }); this.particles = []; }
      this.particles = this.particles.filter(p => {
        p.t += dt * p.speed;
        if (p.t >= p.len || !p.dot.isConnected) { p.dot.remove(); p.path.remove(); return false; }
        const pt = p.path.getPointAtLength(p.t);
        p.dot.setAttribute('cx', pt.x.toFixed(1)); p.dot.setAttribute('cy', pt.y.toFixed(1));
        return true;
      });
      if (this.particles.length) return true;
      this._job = null;
      return false;
    }
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Windows: movable, resizable, collapsible; remembered per browser
  // ---------------------------------------------------------------------------------------------------------------
  const ICON = {
    grip: '<svg viewBox="0 0 12 12" aria-hidden="true"><g fill="currentColor"><circle cx="4" cy="3" r="1.1"/><circle cx="8" cy="3" r="1.1"/><circle cx="4" cy="6" r="1.1"/><circle cx="8" cy="6" r="1.1"/><circle cx="4" cy="9" r="1.1"/><circle cx="8" cy="9" r="1.1"/></g></svg>',
    size: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 4.5V2h2.5M10 7.5V10H7.5M2 2l3.2 3.2M10 10 6.8 6.8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    min: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 8.5h7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  };
  const SIZE_NAMES = { s: 'narrow', m: 'medium', l: 'wide', xl: 'full width' };
  function Windows(grid, opts) {
    const KEY = opts.key, dock = opts.dock, live = opts.live;
    const wins = () => [...grid.querySelectorAll(':scope > [data-win]')];
    const byId = id => grid.querySelector(`:scope > [data-win="${id}"]`);
    let L;
    try { L = normaliseLayout(JSON.parse(store.get(KEY) || 'null'), opts.defaults); } catch (e) { L = normaliseLayout(null, opts.defaults); }
    const save = () => store.set(KEY, JSON.stringify(L));
    const name = w => w.dataset.name || w.dataset.win;
    const say = t => { if (live) { live.textContent = ''; setTimeout(() => { live.textContent = t; }, 30); } };
    function apply() {
      L.order.forEach(id => { const w = byId(id); if (w) grid.appendChild(w); });
      wins().forEach(w => {
        const id = w.dataset.win, mini = L.min.includes(id);
        w.dataset.size = L.size[id];
        w.classList.toggle('tw-min', mini);
        const sb = w.querySelector('.tw-size');
        if (sb) { sb.title = `Size: ${SIZE_NAMES[L.size[id]]}. Click for ${SIZE_NAMES[nextSize(L.size[id])]}`; sb.setAttribute('aria-label', `Resize ${name(w)}: now ${SIZE_NAMES[L.size[id]]}`); }
      });
      dock.innerHTML = '';
      L.min.forEach(id => {
        const w = byId(id);
        if (!w || w.hidden) return;
        const b = el('button', 'tw-chip', `<i></i>${esc(name(w))}`);
        b.type = 'button';
        b.title = `Show ${name(w)} again`;
        b.setAttribute('aria-label', `Show the ${name(w)} window`);
        b.onclick = () => { L.min = L.min.filter(x => x !== id); save(); apply(); const h = w.querySelector('.tw-min-btn'); if (h) h.focus(); say(`${name(w)} shown`); };
        dock.appendChild(b);
      });
      dock.hidden = !dock.children.length;
      if (opts.onChange) opts.onChange(L);
    }
    wins().forEach(w => {
      const head = w.querySelector('.tw-head');
      if (!head) return;
      const id = w.dataset.win;
      const ctl = el('div', 'tw-ctl');
      const grip = el('button', 'tw-btn tw-grip', ICON.grip);
      grip.type = 'button';
      grip.title = 'Drag to move. Or focus and use the arrow keys';
      grip.setAttribute('aria-label', `Move ${name(w)}: arrow keys move it earlier or later`);
      const size = el('button', 'tw-btn tw-size', ICON.size); size.type = 'button';
      const min = el('button', 'tw-btn tw-min-btn', ICON.min); min.type = 'button';
      min.title = 'Minimise to the dock above';
      min.setAttribute('aria-label', `Minimise ${name(w)}`);
      ctl.append(grip, size, min);
      head.appendChild(ctl);
      size.onclick = () => { L.size[id] = nextSize(L.size[id]); save(); apply(); say(`${name(w)}: ${SIZE_NAMES[L.size[id]]}`); };
      min.onclick = () => {
        L.min = [...L.min.filter(x => x !== id), id]; save(); apply();
        const chip = dock.lastElementChild; if (chip) chip.focus();
        say(`${name(w)} minimised. Its button above shows it again`);
      };
      grip.addEventListener('keydown', e => {
        const k = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
        if (!k) return;
        e.preventDefault();
        L.order = moveId(L.order, id, k); save(); apply(); grip.focus();
        say(`${name(w)} moved to place ${L.order.indexOf(id) + 1} of ${L.order.length}`);
      });
      // drag by the grip (HTML drag and drop: mouse and pen; touch users get the arrow keys and the size button)
      grip.addEventListener('pointerdown', () => { w.draggable = true; });
      w.addEventListener('dragstart', e => {
        if (!w.draggable) { e.preventDefault(); return; }
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', id); } catch (x) { /* some browsers */ }
        grid.classList.add('tw-dragging'); w.classList.add('tw-drag');
      });
      w.addEventListener('dragend', () => {
        w.draggable = false; w.classList.remove('tw-drag'); grid.classList.remove('tw-dragging');
        wins().forEach(x => x.classList.remove('tw-before', 'tw-after'));
      });
    });
    document.addEventListener('pointerup', () => wins().forEach(w => { if (!w.classList.contains('tw-drag')) w.draggable = false; }));
    let over = null;
    grid.addEventListener('dragover', e => {
      const drag = grid.querySelector('.tw-drag');
      if (!drag) return;
      e.preventDefault();
      const t = e.target.closest && e.target.closest('[data-win]');
      wins().forEach(x => x.classList.remove('tw-before', 'tw-after'));
      if (!t || t === drag) { over = null; return; }
      const r = t.getBoundingClientRect(), after = e.clientX > r.left + r.width / 2;
      t.classList.add(after ? 'tw-after' : 'tw-before');
      over = { id: t.dataset.win, after };
    });
    grid.addEventListener('drop', e => {
      const drag = grid.querySelector('.tw-drag');
      if (!drag || !over) return;
      e.preventDefault();
      L.order = moveBefore(L.order, drag.dataset.win, over.id, over.after); save(); apply();
      say(`${name(drag)} moved`);
      over = null;
    });
    apply();
    return {
      reset() { store.set(KEY, null); L = normaliseLayout(null, opts.defaults); apply(); say('Layout reset'); },
      refresh: apply,
      layout: () => L,
    };
  }

  // ---------------------------------------------------------------------------------------------------------------
  // HexField: the neon theme's background, a dim hex-circuit lattice with neon traces (drawn here, no picture).
  // The lattice is drawn once per size; with motion allowed a few sparks run along the traces at about 30 fps,
  // only while the neon theme (data-theme="mavis") is on and the tab is showing.
  // ---------------------------------------------------------------------------------------------------------------
  function HexField(cv) {
    if (!cv) return null;
    const ctx = cv.getContext('2d');
    let base = null, traces = [], sparks = [], raf = 0, last = 0, W = 0, H = 0;
    const on = () => document.documentElement.dataset.theme === 'mavis';
    function rng(seed) { let s = seed >>> 0; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296; }
    function build() {
      if (!on()) return;
      const dpr = Math.min(2, root.devicePixelRatio || 1);
      W = root.innerWidth; H = root.innerHeight;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      base = document.createElement('canvas');
      base.width = cv.width; base.height = cv.height;
      const b = base.getContext('2d');
      b.setTransform(dpr, 0, 0, dpr, 0, 0);
      const T = tokens(), rand = rng(20261027);
      const R = 34, hx = R * 1.5, hy = R * Math.sqrt(3);
      const verts = new Map(), key = (x, y) => `${Math.round(x)},${Math.round(y)}`;
      const link = (a, c) => { (verts.get(a.k) || verts.set(a.k, { ...a, n: [] }).get(a.k)).n.push(c); };
      b.lineWidth = 1;
      for (let col = -1; col * hx < W + R * 2; col++) for (let row = -1; row * hy < H + R * 2; row++) {
        const cx = col * hx, cy = row * hy + (col % 2 ? hy / 2 : 0);
        const pts = Array.from({ length: 6 }, (_, k) => { const a = k * Math.PI / 3; return { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) }; });
        pts.forEach(p => { p.k = key(p.x, p.y); });
        // the cells: dark green-black, a few a shade lighter
        b.beginPath(); pts.forEach((p, i) => i ? b.lineTo(p.x, p.y) : b.moveTo(p.x, p.y)); b.closePath();
        const shade = rand();
        if (shade > 0.86) { b.fillStyle = rgba(T.measured, 0.035 + (shade - 0.86) * 0.25); b.fill(); }
        b.strokeStyle = rgba(T.measured, 0.07); b.stroke();
        pts.forEach((p, i) => { const q = pts[(i + 1) % 6]; link(p, q); link(q, p); });
      }
      // the traces: walks along the lattice's edges, green or orange, glowing; a bright node where each ends
      const list = [...verts.values()];
      traces = [];
      for (let k = 0; k < Math.max(6, Math.round(W * H / 90000)); k++) {
        let v = list[Math.floor(rand() * list.length)], prev = null;
        const path = [v];
        for (let s = 0; s < 6 + Math.floor(rand() * 10); s++) {
          const opts2 = v.n.filter(n => !prev || n.k !== prev.k);
          if (!opts2.length) break;
          const nx = opts2[Math.floor(rand() * opts2.length)];
          prev = v; v = verts.get(nx.k) || nx; path.push(v);
        }
        traces.push({ path, tone: rand() < 0.55 ? T.measured : T.estimated });
      }
      traces.forEach(t => {
        [[9, 0.035], [4, 0.09], [1.3, 0.6]].forEach(([w, a]) => {
          b.beginPath(); t.path.forEach((p, i) => i ? b.lineTo(p.x, p.y) : b.moveTo(p.x, p.y));
          b.lineWidth = w; b.strokeStyle = rgba(t.tone, a); b.lineJoin = 'round'; b.stroke();
        });
        [t.path[0], t.path[t.path.length - 1]].forEach(p => {
          stamp(b, t.tone, p.x, p.y, 14, 0.7);
          b.beginPath(); b.arc(p.x, p.y, 2, 0, TAU); b.fillStyle = t.tone; b.fill();
        });
      });
      // fine dust: tiny sparks scattered (still)
      for (let k = 0; k < W * H / 9000; k++) {
        b.beginPath(); b.arc(rand() * W, rand() * H, rand() * 1.1 + 0.2, 0, TAU);
        b.fillStyle = rgba(rand() < 0.5 ? T.estimated : T.measured, 0.15 + rand() * 0.35); b.fill();
      }
      // a vignette so the middle, where the cards sit, stays darkest
      const g = b.createRadialGradient(W * 0.55, H * 0.45, Math.min(W, H) * 0.2, W * 0.55, H * 0.45, Math.max(W, H) * 0.75);
      g.addColorStop(0, 'rgba(0,0,0,0.55)'); g.addColorStop(1, 'rgba(0,0,0,0.05)');
      b.fillStyle = g; b.fillRect(0, 0, W, H);
      sparks = traces.slice(0, 5).map((t, i) => ({ t, d: i * 0.37 % 1 }));
      paint(0);
    }
    function paint(dt) {
      if (!base) return;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cv.width, cv.height);
      ctx.drawImage(base, 0, 0);
      if (!dt && dt !== 0) return;
      const dpr = cv.width / (W || 1);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      sparks.forEach(s => {
        s.d = (s.d + dt * 0.06) % 1;
        const p = s.t.path, segs = p.length - 1, f = s.d * segs, i = Math.min(segs - 1, Math.floor(f)), u = f - i;
        if (segs < 1) return;
        const x = p[i].x + (p[i + 1].x - p[i].x) * u, y = p[i].y + (p[i + 1].y - p[i].y) * u;
        stamp(ctx, s.t.tone, x, y, 12, 0.8);
        ctx.beginPath(); ctx.arc(x, y, 1.5, 0, TAU); ctx.fillStyle = '#fff'; ctx.fill();
      });
    }
    let acc = 0;
    const job = dt => {  // about 30 frames a second is plenty for drifting sparks
      if (!on() || !Motion.allowed() || !Calm.awake()) { running = false; return false; }  // idle: the sparks hold
      acc += dt;
      if (acc >= 0.033) { paint(acc); acc = 0; }
      return true;
    };
    let running = false;
    function sync() {
      if (!on()) { Frame.remove(job); running = false; base = null; return; }
      if (!base) build();
      if (Motion.allowed() && Calm.awake() && !running) { running = true; Frame.add(job); }
      if (!Motion.allowed()) { Frame.remove(job); running = false; paint(); }
    }
    Calm.on(sync);
    let rt = 0;
    root.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { base = null; sync(); }, 200); });
    onTheme(() => { base = null; sync(); });
    Motion.on(sync);
    sync();
    return { sync };
  }

  return Object.assign({}, pure, { Motion, Calm, Frame, Reactor, Pulse, FlowMap, Windows, HexField, esc, store, tokens, watchVisible });
});

/* ---- business-live group (2026-10-27): small pure helpers for the Business and map pages, kept apart from the engine
   above (HSTelemetry.figures; js_tests/figures_test.js). Like the rest: nothing is invented, a missing figure is null. */
(function (root) {
  'use strict';
  const num = v => typeof v === 'number' && isFinite(v) ? v : null;
  // Today's money from the ledger (pence): what was sold, what it cost, what was kept, and the shares for a ring.
  function money(m) {
    m = m || {};
    const sales = num(m.revenue_today_pence), costs = num(m.costs_today_pence), profit = num(m.profit_today_pence);
    const sim = m.source === 'simulated';
    const margin = sales != null && sales > 0 && profit != null ? profit / sales : null;
    const segments = sales != null && sales > 0 && profit != null && profit >= 0 && costs != null
      ? [{ name: 'Costs', v: costs, tone: 'ink-3' }, { name: 'Kept', v: profit, tone: sim ? 'estimated' : 'measured' }].filter(s => s.v > 0) : [];
    return { sales, costs, profit, margin, segments, simulated: sim };
  }
  // A TrendScout score (1.0 = flat against the 28 days before) as a signed whole percent.
  function trend(score) {
    const s = num(score);
    if (s == null) return null;
    const pct = Math.round((s - 1) * 100);
    return { pct, dir: pct > 0 ? 1 : pct < 0 ? -1 : 0, text: (pct > 0 ? '+' : pct < 0 ? '−' : '±') + Math.abs(pct) + '%' };
  }
  // How long ago, short: 42 s, 5 min, 3 h, 2 d.
  function ago(sec) {
    const s = num(sec);
    if (s == null) return '—';
    const a = Math.max(0, Math.round(s));
    return a < 60 ? `${a} s` : a < 3600 ? `${Math.floor(a / 60)} min` : a < 86400 ? `${Math.floor(a / 3600)} h` : `${Math.floor(a / 86400)} d`;
  }
  // Samples [[t ms, v]] taken while a page was open, as trace points: a gap (null) wherever the page was closed for
  // longer than maxGap ms, so the trace never draws a line through time nobody looked.
  function gaps(samples, maxGap) {
    const out = [];
    (samples || []).forEach(([t, v], i, a) => {
      if (i && t - a[i - 1][0] > maxGap) out.push({ t: (t + a[i - 1][0]) / 2, v: null });
      out.push({ t, v: num(v) });
    });
    return out;
  }
  // A running count per key (calls seen through each part of the map since the page opened): {n, last, what}.
  function tally(counts, key, t, what) {
    const c = counts[key] || { n: 0, last: null, what: null };
    counts[key] = { n: c.n + 1, last: c.last == null || t >= c.last ? t : c.last, what: c.last == null || t >= c.last ? what || null : c.what };
    return counts;
  }
  const api = { money, trend, ago, gaps, tally };
  if (typeof module === 'object' && module.exports) module.exports.figures = api;
  else if (root.HSTelemetry) root.HSTelemetry.figures = api;
})(typeof window !== 'undefined' ? window : globalThis);
