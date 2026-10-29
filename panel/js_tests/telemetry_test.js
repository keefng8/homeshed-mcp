// The telemetry engine's pure parts (static/telemetry.js), run by test_cockpit.py with node. Exit 1 on a failure.
'use strict';
const assert = require('assert');
const path = require('path');
const T = require(path.join(__dirname, '..', 'static', 'telemetry.js'));

const tests = [];
const test = (name, f) => tests.push([name, f]);

test('numbers read short and never invent a value', () => {
  assert.strictEqual(T.fmtNum(2840000), '2.84M');
  assert.strictEqual(T.fmtNum(12345), '12.3k');
  assert.strictEqual(T.fmtNum(999), '999');
  assert.strictEqual(T.fmtNum(null), '—');
  assert.strictEqual(T.fmtNum(NaN), '—');
  assert.strictEqual(T.fmtMs(175.4), '175 ms');
  assert.strictEqual(T.fmtMs(2300), '2.3 s');
  assert.strictEqual(T.fmtPct(0.72), '72%');
});

test('axis ticks are round numbers covering the data', () => {
  const t = T.niceTicks(0, 137000, 4);
  assert.deepStrictEqual(t.ticks, [0, 50000, 100000, 150000]);
  assert.deepStrictEqual(T.niceTicks(0, 0, 4).ticks, [0, 0.5, 1]);  // nothing yet: a unit axis, not a crash
});

test('daily series: a day before the first record is a gap, a quiet day after it is a real zero', () => {
  const now = new Date(2026, 9, 27, 15);
  const rows = [{ date: '2026-10-24', saved_tokens: 100 }, { date: '2026-10-26', saved_tokens: 50 }, { date: '2026-10-26', saved_tokens: 5 },
                { date: 'bad' }, null];
  const s = T.dailySeries(rows, 5, 'saved_tokens', now);
  assert.deepStrictEqual(s.map(p => p.key), ['2026-10-23', '2026-10-24', '2026-10-25', '2026-10-26', '2026-10-27']);
  assert.deepStrictEqual(s.map(p => p.v), [null, 100, 0, 55, 0]);
  assert.deepStrictEqual(T.dailySeries([], 3, 'saved_tokens', now).map(p => p.v), [null, null, null]);
});

test('hourly series from the tool server buckets, with the same gap rule', () => {
  const H = 3600, now = 10 * H + 120;
  const hours = [{ t: 7 * H, calls: 4, errors: 1 }, { t: 9 * H, calls: 2, errors: 0 }];
  assert.deepStrictEqual(T.hourlySeries(hours, 5, now, 'calls').map(p => p.v), [null, 4, 0, 2, 0]);
  assert.deepStrictEqual(T.hourlySeries(hours, 5, now, b => b.calls + b.errors).map(p => p.v), [null, 5, 0, 2, 0]);
  assert.strictEqual(T.hourlySeries(hours, 1, now, 'calls')[0].t, 10 * H * 1000);
});

test('activity: what ran, what failed, what was refused, how fast', () => {
  const now = 1000;
  const list = [{ id: 'a', timestamp: 990, duration_ms: 10, ok: true }, { id: 'b', timestamp: 900, duration_ms: 30, ok: false },
                { id: 'c', timestamp: 980, duration_ms: 0, ok: false, refused: true }, { id: 'd', timestamp: 500, duration_ms: 20, ok: true }];
  const s = T.activityStats(list, now);
  assert.strictEqual(s.count, 4); assert.strictEqual(s.ran, 3); assert.strictEqual(s.errors, 1); assert.strictEqual(s.refused, 1);
  assert.strictEqual(s.p50, 20);
  assert.strictEqual(s.perMin5, 2 / 5);  // two calls ran in the last 300 s; the refused one doesn't count
  assert.deepStrictEqual(T.latencyPoints(list).map(p => p.v), [20, 30, 10]);  // oldest first, refused left out
  assert.strictEqual(T.activityStats(null, now).p50, null);
});

test('the ledger keeps measured and estimated apart and adds nothing twice', () => {
  const L = T.ledger({
    rtk: { available: true, total_saved: 1000, total_commands: 7, avg_savings_pct: 70 },
    estimate: { chars_per_token: 4, by_rule: { generation: 10, reading: 20, retrieval: 30 },
                front_door_total: { hidden_chars: 400, trimmed_chars: 4, listings: 2, calls: 1 },
                capabilities: { 'memory.recall': { calls: 5, errors: 1 }, 'local_ai.ask': { calls: 3, errors: 0 }, 'memory.capture': { calls: 2, errors: 0 } },
                hours: [], since: 1 },
    local_models: [{ prompt_tokens: 100, output_tokens: 11 }],
    money: { label: 'X', in: 4, out: 20, gbp_per_usd: 0.75 },
  });
  assert.strictEqual(L.measuredTotal, 1000);
  assert.strictEqual(L.estimated.frontDoor, 101);          // (400 + 4) chars / 4
  assert.strictEqual(L.estimatedTotal, 10 + 20 + 30 + 101);  // the RTK figure is not in it
  assert.strictEqual(L.measured.rtk.pct, 0.7);
  assert.strictEqual(L.workload, 111);                    // workload, not a saving: never in either total
  assert.deepStrictEqual([L.calls.total, L.calls.errors, L.calls.memoryReads, L.calls.memoryWrites, L.calls.local], [10, 1, 5, 2, 3]);
  assert.strictEqual(L.money.measuredUsd, 1000 * 4 / 1e6);
  assert.strictEqual(L.money.estimatedUsd, ((20 + 30 + 101) * 4 + 10 * 20) / 1e6);  // local writing at the output price
});

test('the ledger says why when a source is missing, and never shows zeros for it', () => {
  const off = T.ledger({ rtk: { available: false, not_set_up: true }, estimate: null });
  assert.strictEqual(off.measured.rtk, null); assert.strictEqual(off.measured.rtkWhy, 'not-set-up');
  assert.strictEqual(off.estimated, null); assert.strictEqual(off.estimatedTotal, null); assert.strictEqual(off.calls, null);
  assert.strictEqual(T.ledger({ rtk: { available: false } }).measured.rtkWhy, 'unreachable');
  const old = T.ledger({ estimate: { by_rule: {} } });  // a tool server from before the front door counts
  assert.strictEqual(old.measured.frontDoorChars, null); assert.strictEqual(old.estimated.frontDoor, null); assert.strictEqual(old.hours, null);
  assert.strictEqual(T.ledger({}).money, null);
});

test('a saved layout is cleaned up against the windows that exist', () => {
  const D = { order: ['a', 'b', 'c', 'd'], size: { a: 'm', b: 'l', c: 's', d: 'xl' } };
  const L = T.normaliseLayout({ order: ['c', 'zz', 'a', 'c'], size: { a: 'xl', b: 'huge' }, min: ['b', 'zz', 'b'] }, D);
  assert.deepStrictEqual(L.order, ['c', 'b', 'a', 'd']);  // unknown and repeated ids dropped; a missing one at its default place
  assert.deepStrictEqual(L.size, { a: 'xl', b: 'l', c: 's', d: 'xl' });
  assert.deepStrictEqual(L.min, ['b']);
  assert.deepStrictEqual(T.normaliseLayout('junk', D), { order: D.order, size: D.size, min: [] });
});

test('moving and resizing windows', () => {
  assert.deepStrictEqual(T.moveId(['a', 'b', 'c'], 'a', 1), ['b', 'a', 'c']);
  assert.deepStrictEqual(T.moveId(['a', 'b', 'c'], 'a', -1), ['a', 'b', 'c']);
  assert.deepStrictEqual(T.moveId(['a', 'b', 'c'], 'c', 5), ['a', 'b', 'c']);
  assert.deepStrictEqual(T.moveBefore(['a', 'b', 'c', 'd'], 'a', 'c', false), ['b', 'a', 'c', 'd']);
  assert.deepStrictEqual(T.moveBefore(['a', 'b', 'c', 'd'], 'a', 'c', true), ['b', 'c', 'a', 'd']);
  assert.deepStrictEqual(T.moveBefore(['a', 'b'], 'a', 'zz', true), ['a', 'b']);
  assert.deepStrictEqual(['s', 'm', 'l', 'xl'].map(T.nextSize), ['m', 'l', 'xl', 's']);
});

test('the money line: measured and estimated each with its own tokens and price, and how it was priced', () => {
  const L = T.ledger({
    rtk: { available: true, total_saved: 986000, total_commands: 7, avg_savings_pct: 70 },
    estimate: { chars_per_token: 4, by_rule: { generation: 200000, reading: 900000, retrieval: 300000 },
                front_door_total: { hidden_chars: 1080000, trimmed_chars: 0, listings: 2, calls: 1 } },
    money: { label: 'Claude Opus 5.5', in: 4, out: 20, gbp_per_usd: 0.75, rate_from: 'ECB 2026-10-27', approximate: false },
  });
  const mp = T.moneyParts(L);
  assert.strictEqual(mp.measured.tokens, '986k');
  assert.strictEqual(mp.measured.money, '£2.96');                       // 986k at $4/M = $3.944, at 0.75
  assert.strictEqual(mp.estimated.tokens, '≈1.67M');                    // 200k + 900k + 300k + 270k
  assert.strictEqual(L.money.estimatedIn, 900000 + 300000 + 270000);
  assert.strictEqual(mp.estimated.money, '£' + (((1470000 * 4 + 200000 * 20) / 1e6) * 0.75).toFixed(2));
  assert.ok(/\$4 per million/.test(mp.measured.tip) && /ECB 2026-10-27/.test(mp.measured.tip));
  assert.ok(/\$20 per million/.test(mp.estimated.tip) && /estimated/i.test(mp.estimated.tip));
  assert.ok(!('total' in mp));                                         // nothing to add them up with
  assert.strictEqual(T.moneyParts(T.ledger({})), null);
  const dollars = T.moneyParts(T.ledger({ rtk: { available: true, total_saved: 1e6 }, money: { label: 'X', in: 4, out: 20 } }));
  assert.strictEqual(dollars.measured.money, '$4.00');
  assert.strictEqual(dollars.estimated, null);                          // no estimate loaded: none shown, not zero
});

const FLOW = { lanes: [{ id: 'shell', source: 'shell', stages: ['rtk'] }, { id: 'apps', source: 'apps', stages: ['door', 'perm', 'tools', 'mem', 'local'] }],
               order: ['rtk', 'door', 'perm', 'tools', 'mem', 'local'], banded: ['rtk', 'door', 'local'] };
const flowStreams = (rtk, door, local) => ({ rtk: [{ id: 'rtk', chan: 'm', v: rtk }], door: [{ id: 'door', chan: 'e', v: door }],
                                             local: local.map((v, i) => ({ id: 'l' + i, chan: 'e', v })) });
const numbersIn = d => (d.match(/-?\d+(\.\d+)?/g) || []).map(Number);

test('flow: band width is the real count, one scale for measured and estimated, never stacked together', () => {
  [false, true].forEach(tall => {
    const G = T.flowLayout({ ...FLOW, w: tall ? 360 : 1200, h: tall ? 0 : 540, tall, streams: flowStreams(986000, 1000000, [200000, 300000, 170000]) });
    const by = id => G.bands.find(b => b.id === id);
    assert.ok(Math.abs(by('rtk').w / by('door').w - 0.986) < 1e-9, 'proportional');
    assert.ok(Math.abs(by('rtk').w - 986000 * G.k) < 1e-9 && Math.abs(by('l1').w - 300000 * G.k) < 1e-9, 'one scale');
    assert.strictEqual(by('rtk').chan, 'm');
    assert.ok(G.bands.filter(b => b.chan === 'e').every(b => b.stage !== 'rtk'));
    assert.ok(Math.abs(G.chans.e.h - (by('door').w + by('l0').w + by('l1').w + by('l2').w + 3 * 2)) < 1e-9);  // 2px gaps
    assert.ok(Math.max(G.chans.m.h, G.chans.e.h) <= G.M + 6 + 1e-9);
    assert.strictEqual(G.chans.m.total, 986000); assert.strictEqual(G.chans.e.total, 1670000);
    // the two channels never overlap: measured sits apart from the estimate
    const gap = Math.abs(G.chans.m.c - G.chans.e.c) - (G.chans.m.h + G.chans.e.h) / 2;
    assert.ok(gap >= 18, 'channels apart: ' + gap);
  });
});

test('flow: bands keep to their own slots, never cross, and every shape stays inside the window', () => {
  [[1366 - 260, 540, false], [1920 - 260, 540, false], [720, 0, false], [360, 0, true], [320, 0, true]].forEach(([w, h, tall]) => {
    const G = T.flowLayout({ ...FLOW, w, h, tall, streams: flowStreams(986000, 1000000, [200000, 300000, 170000]) });
    const ordered = G.bands.slice();  // in flow order
    for (let i = 1; i < ordered.length; i++) {
      assert.ok(ordered[i].ul >= ordered[i - 1].ur, `bands side by side along the flow at ${w}`);
      if (ordered[i].chan === ordered[i - 1].chan) assert.ok(ordered[i].vb <= ordered[i - 1].vt, `slots stack inward at ${w}`);
    }
    G.bands.forEach(b => {
      assert.ok(Math.abs((b.Ro - b.Ri) - b.w) < 1e-6 && b.Ri >= 0, 'concentric turn');
      assert.ok(b.vb - b.Ro >= b.vStart - 1e-6, 'the turn starts below the conduit');
      numbersIn(b.d).forEach(n => assert.ok(n >= -0.5 && n <= Math.max(G.w, G.h) + 0.5, `inside at ${w}: ${n}`));
      assert.ok(b.ur + b.Ri <= G.uEnd + 1e-6, 'turns before the sink');
    });
    const st = G.stages;
    assert.ok(FLOW.order.every((id, i) => !i || st[id].u > st[FLOW.order[i - 1]].u), 'stages in order');
    if (w === 720) assert.ok(G.tall, 'too narrow for side by side: it runs down the screen instead');
    if (!G.tall) {
      assert.ok(G.hero.y >= G.claude.y + G.claude.h + 6, 'the sink sits clear of Claude');
      assert.ok(G.hero.y + G.hero.h <= G.h && G.hero.x + G.hero.w <= G.w);
      assert.ok(G.legend.x + G.legend.w <= st.rtk.u - G.M / 2, 'the legend sits clear of the first band');
      // two stages on one lane leave room for their labels
      FLOW.lanes[1].stages.forEach((id, i, a) => { if (i) assert.ok(st[id].u - st[a[i - 1]].u >= 94, `label room at ${w}`); });
    } else {
      assert.ok(G.chans.e.c - G.chans.e.h / 2 >= G.hero.x, 'channels end inside the sink');
      assert.ok(G.labelMax > 120, 'room for the labels');
    }
  });
});

test('flow: no data is a stub, zero is nothing, and the layout never moves when the figures land', () => {
  const opts = { ...FLOW, w: 1100, h: 540, tall: false };
  const none = T.flowLayout({ ...opts, streams: flowStreams(null, null, [null]) });
  assert.strictEqual(none.bands.length, 0);
  assert.deepStrictEqual(none.stubs.map(s => s.stage), ['rtk', 'door', 'local']);
  assert.strictEqual(none.chans.m.total, null); assert.strictEqual(none.scale, null);
  const zero = T.flowLayout({ ...opts, streams: flowStreams(0, 0, [0, 0, 0]) });
  assert.strictEqual(zero.bands.length + zero.stubs.length, 0);
  assert.strictEqual(zero.chans.m.total, 0);  // counted, and it was zero: not "no data"
  const tiny = T.flowLayout({ ...opts, streams: flowStreams(1e7, 1, [0, 0, 0]) });
  assert.strictEqual(tiny.bands.find(b => b.id === 'door').w, 2);  // a real but tiny stream stays visible
  const loading = T.flowLayout({ ...opts, streams: {} });
  const loaded = T.flowLayout({ ...opts, streams: flowStreams(986000, 1e6, [2e5, 3e5, 1.7e5]) });
  [none, zero, loading].forEach(G => {
    assert.deepStrictEqual(Object.values(G.stages).map(s => [s.x, s.y]), Object.values(loaded.stages).map(s => [s.x, s.y]));
    assert.deepStrictEqual([G.h, G.hero, G.claude], [loaded.h, loaded.hero, loaded.claude]);
  });
  const tallA = T.flowLayout({ ...FLOW, w: 360, tall: true, streams: {} }), tallB = T.flowLayout({ ...FLOW, w: 360, tall: true, streams: flowStreams(1, 2, [3]) });
  assert.strictEqual(tallA.h, tallB.h);  // the phone's height is the width's, not the data's
  assert.deepStrictEqual(T.flowLayout({ ...opts, streams: flowStreams(1e6, 2e6, [0]) }).scale, { tokens: 1e6, px: 1e6 * (72 / 2e6) });
});

let failed = 0;
for (const [name, f] of tests) {
  try { f(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + e.message); }
}
console.log(`${tests.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
