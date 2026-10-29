// The panel's script files (index.html's split, 2026-10-28), run by test_main.py with node. Exit 1 on a failure.
// One page script became a small core (panel.js, panel-start.js), the Overview's own file and a file per page group,
// fetched the first time its page opens (Pages in panel.js). Splitting one classic script changes what works:
// declarations no longer hoist across files, a const or let used before its file has run throws, and onclick="fn()"
// needs fn to be global by the time someone can click. So this checks:
//   - every file parses, and no two files that share the page's scope declare the same top-level name;
//   - the page's own scripts run in order in a stand-in browser (no DOM, no network, timers never fire) without a
//     single error, and so does every page's files on top of them, with that page opened, polled and left;
//   - the core and the Overview never name what only a page file defines, except right after asking Pages whether
//     that file is in (Pages.need / Pages.ready), and a page file names only the core's, its own and the files listed
//     before it for its pages;
//   - each page's onclick and similar handlers in index.html call only the core or that page's own files;
//   - a file that is all one private block (owner-only) is loaded only from inside private blocks.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const STATIC = path.join(__dirname, '..', 'static');
const read = f => fs.readFileSync(path.join(STATIC, f), 'utf8').replace(/\r\n/g, '\n');
const PAGE = read('index.html');
const exists = f => fs.existsSync(path.join(STATIC, f));

// ---- what the page loads, and in what order ----
const SCRIPT_TAGS = [...PAGE.matchAll(/<script src="\/([\w.-]+\.js)"><\/script>/g)].map(m => m[1]);
const EAGER = SCRIPT_TAGS.filter(exists);
// A file that is nothing but one private block (and comments) is owner-only: the public copy has none of it.
const code = f => read(f).replace(/\/\*[\s\S]*?\*\//g, '').trim();
const wholePrivate = f => /^\/\* private \*\/\n[\s\S]*\n\/\* \/private \*\/\s*$/.test(read(f).replace(/^\/\*(?! private \*\/)[\s\S]*?\*\/\n/, ''));

// ---- a stand-in browser: enough for load-time code, and nothing it starts ever answers ----
function stub() {
  const own = new Map();
  return new Proxy(function () {}, {
    get(t, k) {
      if (own.has(k)) return own.get(k);
      if (k === Symbol.toPrimitive) return () => 0;
      if (k === Symbol.iterator) return function* () {};
      if (k === 'then') return undefined;  // never a promise
      if (k === 'length') return 0;
      if (k === 'toString' || k === 'valueOf') return () => '';
      const s = stub();
      own.set(k, s);
      return s;
    },
    set(t, k, v) { own.set(k, v); return true; },
    apply: () => stub(),
    construct: () => stub(),
    has: () => true,
  });
}
function storage() {
  const m = new Map();
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k), clear: () => m.clear() };
}
function browser(errors) {
  const els = new Map();
  const ctx = {
    console, Promise, JSON, Math, Date, Intl, Map, Set, WeakMap, WeakSet, URL, URLSearchParams, Symbol, Proxy, Reflect,
    TextEncoder, TextDecoder, AbortController, queueMicrotask, structuredClone,
    localStorage: storage(), sessionStorage: storage(),
    location: { hash: '', pathname: '/', search: '', href: 'http://panel.test/', origin: 'http://panel.test', protocol: 'http:', host: 'panel.test' },
    history: { replaceState() {}, pushState() {} },
    navigator: stub(), screen: stub(), performance: { now: () => Date.now(), getEntriesByType: () => [], mark() {}, measure() {} },
    fetch: () => new Promise(() => {}),  // no answers: only what runs before any reply is checked
    setTimeout: () => 1, setInterval: () => 1, clearTimeout() {}, clearInterval() {},
    requestAnimationFrame: () => 1, cancelAnimationFrame() {}, requestIdleCallback: () => 1,
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {} }),
    getComputedStyle: () => stub(), addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
    ResizeObserver: function () { return stub(); }, MutationObserver: function () { return stub(); },
    IntersectionObserver: function () { return stub(); }, PerformanceObserver: function () { return stub(); },
    Event: function () { return stub(); }, CustomEvent: function () { return stub(); }, Image: function () { return stub(); },
    CSS: { escape: s => String(s), supports: () => false }, devicePixelRatio: 1, innerWidth: 1440, innerHeight: 900,
  };
  const head = stub();
  head.appendChild = el => {  // the page-file loader: run the file, then say it loaded (like the browser, after this task)
    const src = String(el.src || '').replace(/^\//, '');
    if (!/^[\w.-]+\.js$/.test(src) || !exists(src)) return el;  // xterm and the like: never answers here
    Promise.resolve().then(() => { run(ctx, src, errors); if (typeof el.onload === 'function') el.onload(); });
    return el;
  };
  const doc = stub();
  Object.assign(doc, {
    readyState: 'complete', hidden: false, visibilityState: 'visible', head, body: stub(), documentElement: stub(),
    getElementById: id => { if (!els.has(id)) els.set(id, stub()); return els.get(id); },
    querySelector: () => stub(), querySelectorAll: () => [], getElementsByTagName: () => [], getElementsByClassName: () => [],
    createElement: () => stub(), createElementNS: () => stub(), createTextNode: () => stub(), createDocumentFragment: () => stub(),
    addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
  });
  ctx.document = doc;
  ctx.window = ctx.self = ctx.globalThis = ctx;
  vm.createContext(ctx);
  ctx.window = vm.runInContext('globalThis', ctx);
  return ctx;
}
function run(ctx, file, errors) {
  try { vm.runInContext(read(file), ctx, { filename: file }); } catch (e) { errors.push(`${file}: ${e && e.stack ? e.stack.split('\n').slice(0, 2).join(' ') : e}`); }
}
const settle = () => new Promise(r => setImmediate(() => setImmediate(r)));

// ---- each file's top-level names ----
const TOP = /^(?:async\s+)?(?:function\s*\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)/gm;
function topNames(f) {
  const src = read(f), out = new Set([...src.matchAll(TOP)].map(m => m[1]));
  for (const line of src.split('\n')) if (/^(?:let|const|var)\s/.test(line)) for (const m of line.matchAll(/,\s*([A-Za-z_$][\w$]*)\s*=(?!=|>)/g)) out.add(m[1]);
  for (const m of src.matchAll(/\b(?:root|window)\.([A-Za-z_$][\w$]*)\s*=(?![=>])/g)) out.add(m[1]);  // a module's globals
  const oa = src.match(/Object\.assign\(root, \{([\s\S]*?)\}\);/);
  if (oa) {  // its keys: the names at the object's own level (not an arrow's parameters)
    let depth = 0, item = '';
    for (const ch of oa[1] + ',') {
      if ('([{'.includes(ch)) depth++;
      if (')]}'.includes(ch)) depth--;
      if (ch === ',' && depth === 0) { const k = item.trim().match(/^([A-Za-z_$][\w$]*)\s*(?::|$)/); if (k) out.add(k[1]); item = ''; } else item += ch;
    }
  }
  return out;
}
// The code a file runs, as text with everything else blanked (lines kept): comments, the words inside strings and
// template strings (their ${...} stays code), but an onclick="..." inside a string stays too: it calls code.
function bare(src) {
  const out = src.split('');
  const blank = (a, b) => {
    const seg = src.slice(a, b);
    for (let i = a; i < b; i++) if (out[i] !== '\n') out[i] = ' ';
    for (const m of seg.matchAll(/\bon[a-z]+=\\?["']((?:[^"'\\]|\\.|'[^'\n]*')*?)\\?["']/g)) {
      const at = a + m.index + m[0].indexOf(m[1]);
      for (let i = 0; i < m[1].length; i++) out[at + i] = m[1][i];
    }
  };
  let i = 0, prev = '';
  const stack = [];  // template strings open around a ${...}: the depth of braces where each ${ began
  let depth = 0;
  while (i < src.length) {
    const c = src[i], n = src[i + 1];
    if (c === '/' && n === '/') { const e = src.indexOf('\n', i); const end = e < 0 ? src.length : e; for (let k = i; k < end; k++) out[k] = ' '; i = end; continue; }
    if (c === '/' && n === '*') { const e = src.indexOf('*/', i + 2); const end = e < 0 ? src.length : e + 2; for (let k = i; k < end; k++) if (out[k] !== '\n') out[k] = ' '; i = end; continue; }
    if (c === '\'' || c === '"') {
      let k = i + 1;
      while (k < src.length && src[k] !== c && src[k] !== '\n') k += src[k] === '\\' ? 2 : 1;
      blank(i + 1, k); i = k + 1; prev = 'x'; continue;
    }
    if (c === '/' && /[(,=:[!&|?{};+\-*%<>~^]|^$/.test(prev)) {  // a regex literal
      let k = i + 1, cls = false;
      while (k < src.length && src[k] !== '\n' && (cls || src[k] !== '/')) { if (src[k] === '\\') k++; else if (src[k] === '[') cls = true; else if (src[k] === ']') cls = false; k++; }
      blank(i + 1, k); i = k + 1; prev = 'x'; continue;
    }
    if (c === '`' || (c === '}' && stack.length && stack[stack.length - 1] === depth)) {  // template text, up to ` or ${
      if (c === '}') stack.pop();
      let k = i + 1;
      while (k < src.length && src[k] !== '`' && !(src[k] === '$' && src[k + 1] === '{')) k += src[k] === '\\' ? 2 : 1;
      blank(i + 1, k);
      if (src[k] === '$') { stack.push(depth); i = k + 2; prev = '{'; continue; }
      i = k + 1; prev = 'x'; continue;
    }
    if (c === '{') depth++;
    if (c === '}') depth--;
    if (!/\s/.test(c)) prev = c;
    i++;
  }
  return out.join('');
}

const tests = [];
const test = (name, f) => tests.push([name, f]);

// The page's tabs and their files, from the core itself (run in the stand-in browser).
const base = [];
const ctx0 = browser(base);
for (const f of EAGER) run(ctx0, f, base);
const PARTS = vm.runInContext('typeof PAGE_PARTS === "object" ? PAGE_PARTS : null', ctx0) || {};
const TABS = vm.runInContext('typeof TAB_IDS === "object" ? Object.keys(TAB_IDS) : []', ctx0);
const LAZY = [...new Set(Object.values(PARTS).flat())].map(n => n + '.js');
const ALL = [...new Set([...EAGER, ...LAZY])];
const NAMES = Object.fromEntries(ALL.map(f => [f, topNames(f)]));
const deps = f => {  // the files loaded before f for every page that loads f
  const lists = Object.values(PARTS).map(l => l.map(n => n + '.js')).filter(l => l.includes(f));
  return lists.length ? lists.map(l => l.slice(0, l.indexOf(f))).reduce((a, l) => a.filter(x => l.includes(x))) : [];
};

test('the page loads its core and the Overview at once, and every page group from its own file when it opens', () => {
  for (const f of ['panel.js', 'pages-overview.js', 'panel-start.js']) assert.ok(EAGER.includes(f), f);
  assert.strictEqual(EAGER[EAGER.length - 1], 'panel-start.js', 'the start-up runs last');
  assert.ok(EAGER.indexOf('panel.js') < EAGER.indexOf('pages-overview.js'));
  assert.ok(LAZY.length >= 8, LAZY.join(' '));
  for (const f of LAZY) { assert.ok(exists(f), f); assert.ok(!EAGER.includes(f), `${f} loads twice`); }
  for (const t of Object.keys(PARTS)) assert.ok(TABS.includes(t), `PAGE_PARTS names a page that isn't one: ${t}`);
  assert.ok(!/<script>\s*\/\/ ---- helpers/.test(PAGE), 'the big inline script is gone');
});

test('every script file parses', () => {
  for (const f of fs.readdirSync(STATIC).filter(f => f.endsWith('.js'))) {
    try { new vm.Script(read(f), { filename: f }); } catch (e) { assert.fail(`${f}: ${e.message}`); }
  }
});

test('no two files that share the page scope declare the same top-level name', () => {
  const seen = {}, dup = [];
  for (const f of ALL) for (const n of new Set([...read(f).matchAll(TOP)].map(m => m[1]))) {
    if (seen[n]) dup.push(`${n}: ${seen[n]} and ${f}`); else seen[n] = f;
  }
  assert.deepStrictEqual(dup, []);
});

test('the page\'s own scripts run in order without an error', () => {
  assert.deepStrictEqual(base, []);
  assert.strictEqual(vm.runInContext('typeof switchTab + typeof Pages.need + typeof loadAll', ctx0), 'functionfunctionfunction');
});

test('the core names only what is in when it runs: a page file\'s names only after asking Pages', () => {
  const lazyName = new Map();
  for (const f of LAZY) for (const n of NAMES[f]) if (!EAGER.some(e => NAMES[e].has(n))) lazyName.set(n, f);
  const bad = [];
  for (const f of ALL) {
    const allowed = new Set([f, ...(EAGER.includes(f) ? EAGER : [...EAGER, ...deps(f)])]);
    const raw = read(f).split('\n'), lines = bare(read(f)).split('\n');
    const local = new Set([...read(f).matchAll(/\b(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)/g)].map(m => m[1]));
    // a /* global a, b: why */ comment names what the file knowingly uses from a page file that calls it
    for (const g of read(f).matchAll(/\/\* global ([^:*]+)/g)) g[1].split(',').forEach(n => local.add(n.trim().split(':')[0]));
    lines.forEach((line, i) => {
      for (const m of line.matchAll(/(?<![\w$.])([A-Za-z_$][\w$]*)(?![\w$]|\s*:(?!:))/g)) {
        const n = m[1], g = lazyName.get(n);
        if (!g || allowed.has(g) || local.has(n)) continue;
        // asked first: Pages.need/ready for a page whose files hold it, a typeof, or window.<name> (here or 2 lines up)
        const near = raw.slice(Math.max(0, i - 2), i + 1).join(' ');
        const asked = [...near.matchAll(/Pages\.(?:need|ready)\('(\w+)'\)/g)].some(a => (PARTS[a[1]] || []).map(x => x + '.js').includes(g))
          || new RegExp(`(?:typeof |window\\.)${n.replace(/\$/g, '\\$')}\\b`).test(near);
        if (!asked) bad.push(`${f}:${i + 1} names ${n} (${g})`);
      }
    });
  }
  assert.deepStrictEqual(bad, []);
});

test('each page\'s handlers in the markup call only the core or that page\'s own files', () => {
  const body = PAGE.slice(PAGE.indexOf('<body'));
  const bad = [];
  const fileOf = {};
  for (const f of ALL) for (const n of NAMES[f]) if (!fileOf[n]) fileOf[n] = f;
  // each handler belongs to the page section around it (id="<something>View"), or to the shell outside them
  const views = Object.fromEntries(vm.runInContext('Object.entries(TAB_IDS).map(([t, [v]]) => [v, t])', ctx0));
  let view = null;
  for (const line of body.split('\n')) {
    const v = line.match(/\bid="(\w+View)"/);
    if (v && views[v[1]]) view = views[v[1]];
    for (const h of line.matchAll(/\bon[a-z]+="([^"]*)"/g)) for (const c of h[1].matchAll(/(?<![\w$.])([A-Za-z_$][\w$]*)\s*[.(]/g)) {
      const g = fileOf[c[1]];
      if (!g || EAGER.includes(g)) continue;
      if (!view || !(PARTS[view] || []).map(x => x + '.js').includes(g)) bad.push(`${c[1]} (${g}) in ${view || 'the shell'}`);
    }
  }
  assert.deepStrictEqual(bad, []);
});

test('owner-only files are loaded only from inside private blocks', () => {
  const priv = ALL.filter(wholePrivate).concat(fs.readdirSync(STATIC).filter(f => f.endsWith('.css') && wholePrivate(f)));
  const blocks = PAGE.split('<!-- private -->').slice(1).map(s => s.split('<!-- /private -->')[0]).join('\n');
  const outside = PAGE.replace(/<!-- private -->[\s\S]*?<!-- \/private -->/g, '');
  for (const f of priv) {
    assert.ok(!outside.includes(`/${f}"`), `${f} is owner-only, but index.html loads it outside a private block`);
    if (EAGER.includes(f) || f.endsWith('.css')) assert.ok(blocks.includes(`/${f}"`), f);
  }
  const core = read('panel.js');
  const coreOutside = core.replace(/\n\/\* private \*\/\n[\s\S]*?\n\/\* \/private \*\//g, '');
  for (const f of priv.filter(x => x.endsWith('.js') && !EAGER.includes(x))) {
    assert.ok(!coreOutside.includes(`'${f.slice(0, -3)}'`), `${f}: PAGE_PARTS names it outside a private block`);
  }
});

// Each page: its files on top of the page's own, then opened, polled and left (fresh each time).
for (const tab of TABS) {
  test(`the ${tab} page's files load, and it opens, polls and closes without an error`, async () => {
    const errors = [];
    const onRej = e => errors.push(`${tab} (async): ${e && e.stack ? e.stack.split('\n').slice(0, 2).join(' ') : e}`);
    process.on('unhandledRejection', onRej);
    try {
      const ctx = browser(errors);
      for (const f of EAGER) run(ctx, f, errors);
      vm.runInContext(`switchTab(${JSON.stringify(tab)})`, ctx);
      for (let i = 0; i < 4; i++) await settle();
      assert.ok(vm.runInContext(`Pages.ready(${JSON.stringify(tab)})`, ctx), `${tab}: its files never finished loading`);
      await vm.runInContext(`Pages.poll(${JSON.stringify(tab)}, true)`, ctx);
      vm.runInContext("switchTab('ops')", ctx);
      for (let i = 0; i < 2; i++) await settle();
    } finally { process.off('unhandledRejection', onRej); }
    assert.deepStrictEqual(errors, []);
  });
}

test('every page a file hooks into is a real page, and visiting them all in one go stays error-free', async () => {
  const errors = [];
  const ctx = browser(errors);
  for (const f of EAGER) run(ctx, f, errors);
  for (const tab of TABS) { vm.runInContext(`switchTab(${JSON.stringify(tab)})`, ctx); for (let i = 0; i < 3; i++) await settle(); }
  const hooked = JSON.parse(vm.runInContext('JSON.stringify(Pages.hooked())', ctx));
  assert.deepStrictEqual(hooked.filter(t => !TABS.includes(t)), []);
  for (const t of Object.keys(PARTS)) assert.ok(vm.runInContext(`Pages.ready(${JSON.stringify(t)})`, ctx), t);
  assert.deepStrictEqual(errors, []);
});

(async () => {
  let failed = 0;
  for (const [name, f] of tests) {
    try { await f(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + e.message); }
  }
  console.log(`${tests.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
