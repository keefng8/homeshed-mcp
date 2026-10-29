// The work and admin pages' pure parts (static/pages-work-admin.js), run by test_pages_work_admin.py with node.
// Exit 1 on a failure.
'use strict';
const assert = require('assert');
const path = require('path');
const W = require(path.join(__dirname, '..', 'static', 'pages-work-admin.js'));

const tests = [];
const test = (name, f) => tests.push([name, f]);
const DAY = 86400000;
const NOW = new Date(2026, 9, 27, 15, 30).getTime();

test('counts per day cover the window, oldest first, and leave out what falls outside it', () => {
  const s = W.dayCounts([NOW, NOW - 1000, NOW - DAY, NOW - 20 * DAY, null, NaN], 7, NOW);
  assert.strictEqual(s.length, 7);
  assert.deepStrictEqual(s.map(p => p.v), [0, 0, 0, 0, 0, 1, 2]);
  assert.strictEqual(s[6].t, W.dayStart(NOW));
  assert.ok(s[0].t < s[6].t);
});

test('a date-only entry is that day, local time; junk is no date', () => {
  assert.strictEqual(W.dateMs('2026-10-27'), new Date(2026, 9, 27).getTime());
  assert.strictEqual(W.dateMs(''), null);
  assert.strictEqual(W.dateMs('soon'), null);
});

test('pages are clamped and their range is right', () => {
  assert.deepStrictEqual(W.paginate(62, 1, 25), { page: 1, pages: 3, from: 0, to: 25 });
  assert.deepStrictEqual(W.paginate(62, 9, 25), { page: 3, pages: 3, from: 50, to: 62 });
  assert.deepStrictEqual(W.paginate(0, 4, 25), { page: 1, pages: 1, from: 0, to: 0 });
});

test('a rule counts as broken again only when it was not declined', () => {
  const r = W.ruleRepeats([{ rule: 'A', status: 'open' }, { rule: 'A', status: 'actioned' }, { rule: 'A', status: 'declined' },
    { rule: 'B', status: 'open' }, { rule: '', status: 'open' }]);
  assert.deepStrictEqual(r, { A: 2, B: 1 });
});

test('the observation filters: status, repeat, rule, none, date and words', () => {
  const items = [{ id: '1', title: 'Ran grep', status: 'open', rule: 'R1', date: '2026-10-27' },
    { id: '2', title: 'Polled', status: 'actioned', rule: 'R1', date: '2026-09-01' },
    { id: '3', title: 'Asked', status: 'open', rule: '', date: '2026-10-25', area: 'hooks' }];
  const rep = W.ruleRepeats(items), pick = f => items.filter(o => W.obsMatches(o, Object.assign({ status: 'all', rule: 'any', since: 'any', q: '' }, f), rep, NOW)).map(o => o.id);
  assert.deepStrictEqual(pick({}), ['1', '2', '3']);
  assert.deepStrictEqual(pick({ status: 'open' }), ['1', '3']);
  assert.deepStrictEqual(pick({ status: 'repeat' }), ['1', '2']);
  assert.deepStrictEqual(pick({ rule: '__none' }), ['3']);
  assert.deepStrictEqual(pick({ rule: 'R1' }), ['1', '2']);
  assert.deepStrictEqual(pick({ since: 'week' }), ['1', '3']);
  assert.deepStrictEqual(pick({ q: 'HOOKS' }), ['3']);
  assert.deepStrictEqual(pick({ q: '#2' }), ['2']);
});

test('guard events and vault changes fall into the kinds their filters offer', () => {
  assert.strictEqual(W.ruleKind('blocked-write'), 'stop');
  assert.strictEqual(W.ruleKind('reminded-stop'), 'note');
  assert.strictEqual(W.ruleKind(undefined), 'note');
  assert.deepStrictEqual(['added', 'replaced', 'updated', 'deleted', 'revealed', 'wrong reveal key'].map(W.vaultKind),
    ['change', 'change', 'change', 'delete', 'reveal', 'reveal']);
  assert.strictEqual(W.vaultKind('something new'), 'other');
});

test('the disk ring keeps the biggest folders and folds the rest into one slice, never inventing size', () => {
  const s = W.shares([{ name: 'a', v: 5 }, { name: 'b', v: 50 }, { name: 'c', v: 0 }, { name: 'd', v: 2 }, { name: 'e', v: 1 }], 2);
  assert.deepStrictEqual(s.map(x => [x.name, x.v]), [['b', 50], ['a', 5], ['2 others', 3]]);
  assert.strictEqual(s.reduce((a, x) => a + x.v, 0), 58);
  assert.deepStrictEqual(W.shares([{ name: 'a', v: 3 }], 6).map(x => x.name), ['a']);
  assert.deepStrictEqual(W.shares([], 6), []);
});

let failed = 0;
for (const [name, f] of tests) {
  try { f(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + e.message); }
}
console.log(`${tests.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
