// The Overview's lower half (static/pages-ovlower.js): its pure parts, run by test_ovlower.py with node. Exit 1 on a failure.
'use strict';
const assert = require('assert');
const path = require('path');
const O = require(path.join(__dirname, '..', 'static', 'pages-ovlower.js'));

const tests = [];
const test = (name, f) => tests.push([name, f]);
const near = (a, b, e) => assert.ok(Math.abs(a - b) < (e || 1e-6), `${a} != ${b}`);

test('composition segments share the sweep, leave a gap between, and give a zero part no room', () => {
  const s = O.segments([3, 0, 1], 0, 4, 0.1);
  assert.strictEqual(s[1], null);
  near(s[0][0], 0.05); near(s[0][1], 2.95); near(s[2][0], 3.05); near(s[2][1], 3.95);
  assert.deepStrictEqual(O.segments([0, 0], 0, 1, 0.1), [null, null]);  // nothing yet: nothing drawn, never a fake share
  const one = O.segments([5], 0, 2, 0.3);
  near(one[0][0], 0); near(one[0][1], 2);  // a single part has no gap
});

test('slots: one per gate, the first `lit` of them on, capped so a bad count cannot flood the page', () => {
  const s = O.slots(12, 7, 0, 12, 0.2);
  assert.strictEqual(s.length, 12);
  assert.strictEqual(s.filter(x => x[2]).length, 7);
  assert.ok(s[0][1] < s[1][0]);  // a gap between neighbours
  assert.strictEqual(O.slots(5000, 1, 0, 1, 0).length, 60);
  assert.strictEqual(O.slots(null, 0, 0, 1, 0).length, 0);
});

test('arcs: a part arc and a whole ring both draw', () => {
  assert.match(O.arcD(10, 10, 5, 0, Math.PI / 2), /^M15 10A5 5 0 0 1 10 15$/);
  assert.match(O.arcD(10, 10, 5, 0, Math.PI * 1.5), / 0 1 1 /);
  assert.strictEqual((O.arcD(10, 10, 5, 0, Math.PI * 2).match(/A/g) || []).length, 2);
});

test('the call tape keeps a still scale and only real calls inside it', () => {
  const now = 100000;
  assert.strictEqual(O.tapeWindow([], now), 900);  // at least 15 minutes
  assert.strictEqual(O.tapeWindow([now - 1000], now), 1200);  // rounded up to 5 minutes
  assert.strictEqual(O.tapeWindow([now - 99999], now), 6 * 3600);  // at most 6 hours
  const marks = O.tapeMarks([
    { id: 'a.read', timestamp: now - 450, duration_ms: 1, ok: true },
    { id: 'b.write', timestamp: now, duration_ms: 10000, ok: true },
    { id: 'c', timestamp: now - 10, duration_ms: 5, ok: false },
    { id: 'd', timestamp: now - 20, refused: true },
    { id: 'old', timestamp: now - 2000, duration_ms: 5, ok: true },
    { id: 'bad' }, null,
  ], now, 900, id => id === 'b.write' ? 'write' : 'read');
  assert.deepStrictEqual(marks.map(m => m.kind), ['read', 'write', 'failed', 'refused']);
  near(marks[0].x, 0.5); near(marks[1].x, 1);
  near(marks[0].h, 0.12); near(marks[1].h, 1);  // 1 ms is the floor, 10 s the top (log scale)
  near(marks[3].h, 0.12);
  near(O.tapeMarks([{ timestamp: now, h: 0.6 }], now, 900)[0].h, 0.6);  // a fixed height when nothing was timed
});

test('tape labels are round minutes, about as many as asked for', () => {
  const t = O.tapeTicks(1800, 6);
  assert.strictEqual(t.step, 300);
  assert.strictEqual(t.ticks.length, 7);
  assert.strictEqual(t.ticks[0].x, 1);
  assert.strictEqual(O.agoLabel(0), 'now');
  assert.strictEqual(O.agoLabel(600), '−10 m');
  assert.strictEqual(O.agoLabel(5400), '−1.5 h');
});

test('trace points start at zero and keep a gap a gap', () => {
  const p = O.tracePoints([0, 5, null, 10]);
  assert.deepStrictEqual(p[0], [0, 0]);
  assert.strictEqual(p[2], null);
  assert.deepStrictEqual(p[3], [1, 1]);
  near(p[1][1], 0.5);
});

let failed = 0;
for (const [name, f] of tests) {
  try { f(); console.log('ok  ', name); } catch (e) { failed++; console.log('FAIL', name, '\n   ', e.message); }
}
console.log(`${tests.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
