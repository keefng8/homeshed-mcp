// The ops pages' pure parts (static/pages-ops.js), run by test_pages_ops.py with node. Exit 1 on a failure.
'use strict';
const assert = require('assert');
const path = require('path');
const O = require(path.join(__dirname, '..', 'static', 'pages-ops.js'));

const tests = [];
const test = (name, f) => tests.push([name, f]);

const TOOLS = [
  { id: 'docker.container.list', category: 'docker', risk: 'read', enabled: true, calls: 40, errors: 2, last_call: 300, avg_ms: 80, description: 'List containers' },
  { id: 'docker.container.stop', category: 'docker', risk: 'write', enabled: false, calls: 2, errors: 0, last_call: 100, avg_ms: null, description: 'Stop one' },
  { id: 'memory.recall', category: 'memory', risk: 'read', enabled: true, calls: 0, errors: 0, last_call: null, avg_ms: 400, description: 'Recall facts' },
];

test('tools: search, area, on/off and risk filter together; sorts are stable by name', () => {
  assert.deepStrictEqual(O.toolRows(TOOLS, { q: 'CONTAIN' }).map(t => t.id), ['docker.container.list', 'docker.container.stop']);
  assert.deepStrictEqual(O.toolRows(TOOLS, { q: 'facts' }).map(t => t.id), ['memory.recall']);  // the description counts
  assert.deepStrictEqual(O.toolRows(TOOLS, { on: 'off' }).map(t => t.id), ['docker.container.stop']);
  assert.deepStrictEqual(O.toolRows(TOOLS, { cat: 'docker', risk: 'read' }).map(t => t.id), ['docker.container.list']);
  assert.deepStrictEqual(O.toolRows(TOOLS, { sort: 'calls' }).map(t => t.id)[0], 'docker.container.list');
  assert.deepStrictEqual(O.toolRows(TOOLS, { sort: 'slow' }).map(t => t.id), ['memory.recall', 'docker.container.list', 'docker.container.stop']);  // no reading last
  assert.strictEqual(O.toolRows(null, {}).length, 0);
});

test('tool totals: success is null with no calls, never 100%', () => {
  const t = O.toolTotals(TOOLS);
  assert.deepStrictEqual([t.total, t.on, t.off, t.calls, t.errors, t.used], [3, 2, 1, 42, 2, 2]);
  assert.strictEqual(t.success, 40 / 42);
  assert.strictEqual(O.toolTotals([]).success, null);
});

test('containers: published ports read host to inside, once each; filters and the stopped-first sort', () => {
  assert.deepStrictEqual(O.portList({ '80/tcp': [{ HostIp: '0.0.0.0', HostPort: '8080' }, { HostIp: '::', HostPort: '8080' }], '443/tcp': null }), ['8080→80/tcp']);
  assert.deepStrictEqual(O.portList(null), []);
  const L = [{ name: 'web', image: 'nginx', status: 'running', ports: { '80/tcp': [{ HostPort: '80' }] } },
             { name: 'Db', image: 'postgres', status: 'exited' }, { name: 'cache', image: 'redis', status: 'running' }];
  assert.deepStrictEqual(O.containerRows(L, {}).map(c => c.name), ['cache', 'Db', 'web']);
  assert.deepStrictEqual(O.containerRows(L, { state: 'stopped' }).map(c => c.name), ['Db']);
  assert.deepStrictEqual(O.containerRows(L, { sort: 'state' }).map(c => c.name), ['Db', 'cache', 'web']);
  assert.deepStrictEqual(O.containerRows(L, { q: 'redis' }).map(c => c.name), ['cache']);
  assert.deepStrictEqual(O.containerCounts(L), { total: 3, running: 2, stopped: 1, images: 3, published: 1 });
});

test('uptime: down first, then no reading, then up; paused filter; Kuma times are UTC', () => {
  const M = [{ name: 'b', status: 'up' }, { name: 'a', status: 'up', active: false }, { name: 'z', status: 'down' }, { name: 'm', status: 'unknown' }];
  assert.deepStrictEqual(O.monitorRows(M, {}).map(m => m.name), ['z', 'm', 'a', 'b']);
  assert.deepStrictEqual(O.monitorRows(M, { state: 'paused' }).map(m => m.name), ['a']);
  assert.deepStrictEqual(O.monitorRows(M.concat([{ name: 'p', status: 'unknown', active: false }]), { state: 'unknown' }).map(m => m.name), ['m']);  // paused has no reading by design
  assert.strictEqual(O.kumaTime('2026-10-27 10:00:00.123'), Date.UTC(2026, 9, 27, 10) / 1000);
  assert.strictEqual(O.kumaTime('soon'), null);
  assert.strictEqual(O.kumaTime(null), null);
});

test('savings: measured and estimated stay apart; no RTK says why, never a zero', () => {
  const F = O.savingsFigures({ daily: { summary: { total_saved: 1000, total_commands: 20, avg_savings_pct: 70 }, daily: [{ date: '2026-10-27', saved_tokens: 5 }] },
    estimate: { by_rule: { generation: 10, reading: 20, retrieval: 30 }, front_door_total: { hidden_chars: 400, trimmed_chars: 0 } },
    discover: { available: true, total_commands: 10, already_rtk: 8, missed: [{ command: 'a', estimated_savings_tokens: 3 }, { command: 'b', estimated_savings_tokens: 9 }] } });
  assert.deepStrictEqual(F.rtk, { tokens: 1000, commands: 20, pct: 0.7 });
  assert.strictEqual(F.estTotal, 10 + 20 + 30 + 100);
  assert.deepStrictEqual(F.missed.rows.map(r => r.command), ['b', 'a']);
  assert.strictEqual(F.missed.tokens, 12);
  assert.strictEqual(F.missed.coverage, 0.8);
  const none = O.savingsFigures({ daily: { not_set_up: true } });
  assert.strictEqual(none.rtk, null);
  assert.strictEqual(none.rtkWhy, 'not-set-up');
  assert.strictEqual(none.est, null);
  assert.strictEqual(none.missedWhy, 'hidden');  // a viewer's reply carries no history scan
  assert.strictEqual(O.savingsFigures({}).rtkWhy, 'none');
});

test('the 12-week calendar starts on a Monday, ends today and marks days without a row as gaps', () => {
  const today = new Date(2026, 9, 28);  // a Wednesday
  const cells = O.heatCells([{ date: '2026-10-28', saved_tokens: 100 }, { date: '2026-10-27', saved_tokens: 10 }], today);
  assert.strictEqual(cells[0].dow, 0);
  assert.strictEqual(cells[cells.length - 1].key, '2026-10-28');
  assert.strictEqual(cells[cells.length - 1].today, true);
  assert.strictEqual(cells.length, 11 * 7 + 3);
  assert.strictEqual(cells[cells.length - 1].level, 4);
  assert.strictEqual(cells[cells.length - 2].level, 1);
  assert.strictEqual(cells[0].v, null);
});

test('proxmox: totals over the nodes that answered, CPU weighted by threads; history gaps stay gaps', () => {
  const d = { nodes: [{ status: 'online', cpu_pct: 10, threads: 4, mem_used: 2, mem_total: 8, root_used: 1, root_total: 4 },
                      { status: 'online', cpu_pct: 70, threads: 12, mem_used: 6, mem_total: 8, root_used: 1, root_total: 4 },
                      { status: 'offline', cpu_pct: 99, threads: 64, mem_used: 0, mem_total: 0 }],
              guests: [{ status: 'running' }, { status: 'stopped' }, { status: 'running', template: true }],
              storage: [{ shared: true, used: 5, total: 10 }, { shared: false, used: 1, total: 2 }] };
  const t = O.pveTotals(d);
  assert.strictEqual(Math.round(t.cpu * 1000) / 1000, 0.55);
  assert.deepStrictEqual([t.memUsed, t.memTotal, t.sharedUsed, t.sharedTotal, t.online, t.nodes, t.guests, t.running], [8, 16, 5, 10, 2, 3, 2, 1]);
  assert.strictEqual(O.pveTotals({ nodes: [] }).cpu, null);
  assert.deepStrictEqual(O.pvePoints([{ time: 60, cpu: 0.5 }, { time: 120, cpu: null }, { time: 0, cpu: 1 }], 'cpu', v => v * 100),
    [{ t: 60000, v: 50 }, { t: 120000, v: null }]);
});

test('run page list and the tick scale', () => {
  assert.deepStrictEqual(O.runRows(TOOLS, { risk: 'write' }).map(t => t.id), ['docker.container.stop']);
  assert.strictEqual(O.litTicks(0.5, 20), 10);
  assert.strictEqual(O.litTicks(null, 20), null);
  assert.strictEqual(O.litTicks(3, 20), 20);
  assert.strictEqual(O.frac(5, 0), null);
  assert.strictEqual(O.toneOf(0.95), 'bad');
  assert.strictEqual(O.toneOf(0.8), 'warn');
  assert.strictEqual(O.toneOf(null), 'none');
});

let failed = 0;
for (const [name, f] of tests) {
  try { f(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + (e && e.message)); }
}
console.log(`${tests.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
