// The small pure helpers at the end of static/telemetry.js (HSTelemetry.figures), run by test_cockpit.py with node.
// Exit 1 on a failure.
'use strict';
const assert = require('assert');
const path = require('path');
const F = require(path.join(__dirname, '..', 'static', 'telemetry.js')).figures;

const tests = [];
const test = (name, f) => tests.push([name, f]);

test('money: margin and shares only from figures that were sent', () => {
  const m = F.money({ revenue_today_pence: 20000, costs_today_pence: 8000, profit_today_pence: 12000 });
  assert.strictEqual(m.margin, 0.6);
  assert.deepStrictEqual(m.segments.map(s => [s.name, s.v, s.tone]), [['Costs', 8000, 'ink-3'], ['Kept', 12000, 'measured']]);
  assert.strictEqual(F.money({ revenue_today_pence: 0, profit_today_pence: 0 }).margin, null);  // no sales: no margin, not 0%
  assert.strictEqual(F.money({}).profit, null);
  assert.deepStrictEqual(F.money({ revenue_today_pence: 100, costs_today_pence: 150, profit_today_pence: -50 }).segments, []);  // a loss: no ring
  assert.strictEqual(F.money({ source: 'simulated', revenue_today_pence: 10, costs_today_pence: 4, profit_today_pence: 6 }).segments[1].tone, 'estimated');
});

test('trend: a signed whole percent, none when there is no score', () => {
  assert.deepStrictEqual(F.trend(1.42), { pct: 42, dir: 1, text: '+42%' });
  assert.deepStrictEqual(F.trend(0.81), { pct: -19, dir: -1, text: '−19%' });
  assert.strictEqual(F.trend(1).text, '±0%');
  assert.strictEqual(F.trend(null), null);
  assert.strictEqual(F.trend(NaN), null);
});

test('ago reads short', () => {
  assert.strictEqual(F.ago(42), '42 s');
  assert.strictEqual(F.ago(300), '5 min');
  assert.strictEqual(F.ago(3 * 3600 + 5), '3 h');
  assert.strictEqual(F.ago(2 * 86400), '2 d');
  assert.strictEqual(F.ago(-3), '0 s');
  assert.strictEqual(F.ago(undefined), '—');
});

test('gaps: no line drawn through time the page was closed', () => {
  const pts = F.gaps([[0, 1], [60000, 2], [600000, 3]], 150000);
  assert.deepStrictEqual(pts.map(p => p.v), [1, 2, null, 3]);
  assert.strictEqual(pts[2].t, 330000);
  assert.deepStrictEqual(F.gaps([], 1), []);
});

test('tally counts and keeps the newest', () => {
  const c = {};
  F.tally(c, 'a', 10, 'x'); F.tally(c, 'a', 5, 'old'); F.tally(c, 'a', 20, 'y');
  assert.deepStrictEqual(c.a, { n: 3, last: 20, what: 'y' });
});

let failed = 0;
for (const [name, f] of tests) {
  try { f(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n  ' + e.message); }
}
console.log(`${tests.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
