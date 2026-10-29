/* The Control Panel's core (static/panel.js): the helpers every page uses, the start-up hand-off from boot.js, the
   sidebar and switchTab (pages, deep links, keys 1-9), the page-file loader (Pages: each page's own file loads the first
   time the page opens), the themes, the shared list kit (UI), the poll engine (feed, feedDue, loadAll, pollOnce), the
   header's Waiting for you (approvals, messages, replies), announcements and the first-run welcome.
   Load order (index.html): telemetry.js, cockpit.js, pages-ovlower.js, this file, pages-overview.js, then
   panel-start.js, which starts everything. Nothing here runs a page's code before that page's file is in. */
// ---- helpers shared by the theme-aware canvas drawing ----
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function rgba(hex, a) {
  let h = (hex || '#888888').replace('#', '');
  if (h.length === 3) h = h.split('').map(ch => ch + ch).join('');
  const n = parseInt(h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// ---- start-up: boot.js (HomeShedBoot) shows the real start over the page and calls finishBoot the moment the first
// data is in (or when the person opens the panel early). Without boot.js the page simply starts.
let _booted = false;
function finishBoot() {
  if (_booted) return;
  _booted = true;
  document.getElementById('shell').classList.remove('pre-boot');
  loadPlanBadge();  // Free / Pro badge beside the name (owner only; returns at once for viewers)
  showWelcome();
  loadWeekValue();
  setInterval(() => { if (!document.hidden) loadWeekValue(); }, 30 * 60 * 1000);
  enterView(TAB_IDS[_activeTab][0]);
  moveNavPill(false);
}
// ---- pages: sidebar navigation, page header, deep links (#models etc.), keys 1-9 ----
// The homelab's own pages and panels sit between private marker lines (a comment saying "private", then "/private";
// HTML comments in the markup). The public copy leaves them out, so public code never calls into one.
const TAB_IDS = {
  ops: ['opsView', 'tabOps'], models: ['modelsView', 'tabModels'], reasoning: ['reasoningView', 'tabReasoning'],
  docker: ['dockerView', 'tabDocker'], monitors: ['monitorsView', 'tabMonitors'], observations: ['observationsView', 'tabObservations'],
  todo: ['todoView', 'tabTodo'], savings: ['savingsView', 'tabSavings'], tools: ['toolsView', 'tabTools'], api: ['apiView', 'tabApi'],
  settings: ['settingsView', 'tabSettings'], rules: ['rulesView', 'tabRules'], guide: ['guideView', 'tabGuide'],
  pro: ['proView', 'tabPro'], projects: ['projectsView', 'tabProjects'], run: ['runView', 'tabRun'],
  proxmox: ['proxmoxView', 'tabProxmox'], terminal: ['terminalView', 'tabTerminal'],
};
const PAGES = {
  ops: ['Overview', "What's running, what it's doing, and what it has saved you."],
  models: ['Models', 'The AI models your tools use, the order they try them in, and why each answer came from where it did.'],
  reasoning: ['Reasoning', 'Try the reasoning tools by hand: solve, decompose, route and delegate.'],
  docker: ['Containers', 'Every container on the server. Restart and stop ask before they act.'],
  terminal: ['Terminal', 'SSH in the browser to the machines on your network. Signs in with this Control Panel\'s own key; a session survives a reload for 5 minutes.'],
  proxmox: ['Proxmox', 'Your Proxmox cluster live: each node\'s CPU, RAM and disks, every VM and container, backups and alarms. Select a node or a guest for its graphs. Refreshes every 15 seconds while open.'],
  monitors: ['Uptime', 'Uptime Kuma monitors, with anything that is down listed first.'],
  projects: ['Projects', 'Every folder your agents work in: who owns it, what is working in it now, its GitHub repo, its size, and the files pinned for agents to read.'],
  todo: ['To-do', 'Tasks for your agents. Add them here and name who does each; agents work through them, overnight too, and say how it went.'],
  observations: ['Observations', 'Rule breaks and corrections. A rule broken twice gets a mechanical guard.'],
  api: ['API access', 'Apps, agents and people that use HomeShed besides you, each with its own token, tools and limits.'],
  run: ['Run', 'Run any tool by hand: fill in its form (or a JSON body), see the answer, and save runs you repeat.'],
  tools: ['Tools', 'Everything HomeShed offers, how much each is used, and a switch to turn any of them off.'],
  savings: ['Savings', 'How many tokens stay out of Claude, day by day, and where more could be saved.'],
  settings: ['Settings', 'Change how HomeShed behaves. Each change saves as soon as you make it.'],
  rules: ['Rules', "The rules Claude works by, how often their guards stopped something, and how each request followed the protocol."],
  guide: ['Guide', 'What every page is for and how to use it, in plain words.'],
  pro: ['Pro', 'HomeShed is free and complete. HomeShed Pro adds every curated pack, their updates and the known-bugs feed.'],
};
// View-only login (for a friend): only these pages. The server refuses the rest.
let _viewer = false;
const VIEWER_TABS = new Set(['ops', 'savings', 'guide', 'todo']);  // todo: the example list only

function enterView(id) {
  const v = document.getElementById(id);
  v.classList.remove('view-enter'); void v.offsetWidth;
  v.classList.add('view-enter');
  clearTimeout(v._enterT);
  v._enterT = setTimeout(() => v.classList.remove('view-enter'), 900);
}

function moveNavPill(animate) {
  const pill = document.getElementById('navPill');
  const btn = document.getElementById(TAB_IDS[_activeTab][1]);
  if (!pill || !btn) return;
  if (!animate) pill.style.transition = 'none';
  pill.style.setProperty('--pill-y', btn.offsetTop + 'px');
  if (!animate) { void pill.offsetWidth; pill.style.transition = ''; }
}
window.addEventListener('resize', () => moveNavPill(false));

// Related pages share one sidebar entry (R&D's punch list #10: 18 entries became about 10). The groups are built from
// the pages this copy has: one that's left with a single page is just that page's button. The entry opens the page
// last used in its group; the group of the page showing lists its pages below it.
// lead: the group is named after its first page, so that page's own button is the group's entry (never listed twice).
const NAV_GROUPS = [
  { id: 'overview', label: 'Overview', tabs: ['ops'], lead: true },
  { id: 'projects', label: 'Projects', tabs: ['projects'] },
  { id: 'ai', label: 'AI', tabs: ['models', 'reasoning'] },
  { id: 'servers', label: 'Servers', tabs: ['proxmox', 'terminal', 'docker', 'monitors', 'tools', 'run'] },
  { id: 'quality', label: 'Rules', tabs: ['rules', 'observations'], lead: true },
  { id: 'settings', label: 'Settings', tabs: ['settings', 'guide'], lead: true },
];
const PHONE_BAR = ['overview', 'todo', 'ai', 'settings'];  // the phone bar's four entries; More holds the rest
const _groupLast = {};
const navBtn = tab => TAB_IDS[tab] && document.getElementById(TAB_IDS[tab][1]);
const shown = el => !!el && !el.hidden && getComputedStyle(el).display !== 'none';
function groupOf(tab) { return NAV_GROUPS.find(g => g.tabs.includes(tab) && document.querySelector(`.nav-grp[data-grp="${g.id}"]`)); }
function buildNavGroups() {
  NAV_GROUPS.forEach(g => {
    const btns = g.tabs.map(navBtn).filter(Boolean);
    if (btns.length < 2) return;
    const box = document.createElement('div'), items = document.createElement('div');
    box.className = 'nav-grp';
    box.dataset.grp = g.id;
    items.className = 'nav-grp-items';
    btns[0].before(box);
    let head;
    if (g.lead) {  // the first page's own button leads the group: it opens that page, as before
      head = btns.shift();
      head.classList.add('nav-grp-btn');
    } else {  // a made entry: it opens the page last used in the group
      head = document.createElement('button');
      head.type = 'button';
      head.className = 'tab-btn nav-grp-btn nav-grp-made';
      head.innerHTML = btns[0].querySelector('svg').outerHTML + `<span>${escapeHtml(g.label)}</span>`;
      head.addEventListener('click', () => openGroup(g.id));
    }
    head.dataset.grpHead = g.id;
    head.setAttribute('aria-expanded', 'false');
    box.append(head, items);
    btns.forEach(b => { b.querySelector('.kbd')?.remove(); items.append(b); });
  });
  for (const id of PHONE_BAR) {  // a group's box, or the page's own button when it isn't grouped
    const el = document.querySelector(`.nav-grp[data-grp="${id}"]`) || navBtn(id === 'overview' ? 'ops' : id)
      || navBtn((NAV_GROUPS.find(g => g.id === id) || { tabs: [] }).tabs[0]);
    if (el) el.dataset.phone = '1';
  }
}
function openGroup(id) {
  const g = NAV_GROUPS.find(x => x.id === id), tabs = g.tabs.filter(t => shown(navBtn(t)));
  // From another group: the page last used here. Already in this group: its first page, so the entry is never a trap.
  const tab = tabs.includes(_activeTab) ? tabs[0] : tabs.includes(_groupLast[id]) ? _groupLast[id] : tabs[0];
  if (tab) switchTab(tab);
}
function syncNavGroups(tab) {  // open the group of the page showing; hide a group whose pages are all hidden
  const grp = groupOf(tab);
  if (grp) _groupLast[grp.id] = tab;
  document.querySelectorAll('.nav-grp').forEach(box => {
    const open = !!grp && box.dataset.grp === grp.id, head = box.querySelector('.nav-grp-btn');
    box.classList.toggle('open', open);
    head.classList.toggle('nav-grp-open', open);  // its group is showing; "active" stays the page's own
    head.setAttribute('aria-expanded', String(open));
    box.hidden = false;  // measured unhidden, then hidden only when none of its pages shows
    box.hidden = ![...box.querySelectorAll('.tab-btn:not(.nav-grp-made)')].some(b => !b.hidden && getComputedStyle(b).display !== 'none');
  });
  mirrorBadges();
}
function toggleNavSheet(open) {  // phones: More opens the whole list over the page, and any choice closes it
  const side = document.querySelector('.sidebar'), btn = document.getElementById('navMore');
  const on = open === undefined ? !side.classList.contains('sheet-open') : open;
  side.classList.toggle('sheet-open', on);
  if (btn) {
    btn.setAttribute('aria-expanded', String(on));
    btn.querySelector('.more-label').textContent = on ? 'Close' : 'More';
  }
}
function mirrorBadges() {  // a closed group shows its pages' counts (Observations' open items) on its own entry
  document.querySelectorAll('.nav-grp').forEach(box => {
    const n = [...box.querySelectorAll('.nav-grp-items .tab-badge')]
      .reduce((a, b) => a + (b.style.display === 'none' ? 0 : Number(b.textContent) || 0), 0);
    const head = box.querySelector('.nav-grp-btn');
    let tag = head.querySelector('.tab-badge.mirror');
    if (!tag) {
      tag = document.createElement('span');
      tag.className = 'tab-badge mirror';
      head.insertBefore(tag, head.querySelector('.kbd'));
    }
    tag.textContent = n;
    tag.style.display = n && !box.classList.contains('open') ? 'inline-grid' : 'none';
  });
}
buildNavGroups();
syncNavGroups('ops');  // the page that's open at load (a deep link switches after)

// Keys 1-9: the sidebar's entries (a group or a page), counted in order among the visible ones, so a page this setup
// doesn't have (or hides) leaves no gap. Numbered again whenever a page is shown or hidden.
let _navKeys = [];
function numberNavKeys() {
  _navKeys = [];
  document.querySelectorAll('#nav > .tab-btn:not(.nav-more), #nav > .nav-grp > .nav-grp-btn').forEach(btn => {
    let kbd = btn.querySelector('.kbd');
    if (!kbd) {
      kbd = document.createElement('span');
      kbd.className = 'kbd';
      btn.append(kbd);
    }
    const on = shown(btn) && !btn.closest('[hidden]') && btn.id !== 'tabPro' && _navKeys.length < 9;
    if (on) _navKeys.push(btn);
    kbd.textContent = on ? String(_navKeys.length) : '';
    kbd.hidden = !on;
  });
}
numberNavKeys();
// The menu: pinned open, or a rail that opens on hover (the owner, 2026-10-01). Desktop only; the choice is saved per browser.
const NAV_PIN_ICONS = {
  close: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9 4.5v15M15.5 9.5L13 12l2.5 2.5"/></svg>',
  open: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9 4.5v15M13 9.5l2.5 2.5-2.5 2.5"/></svg>',
};
function setNavRail(on, save = true) {
  const shell = document.querySelector('.shell'), btn = document.getElementById('navPin');
  if (!shell) return;
  shell.classList.toggle('nav-rail', on);
  if (btn) {
    const label = on ? 'Keep the menu open' : 'Collapse the menu';
    btn.innerHTML = on ? NAV_PIN_ICONS.open : NAV_PIN_ICONS.close;
    btn.setAttribute('aria-label', label);
    btn.title = label + ' ([)';
  }
  if (save) {
    try { localStorage.setItem('cp-nav-rail', on ? '1' : '0'); } catch (e) { /* this visit only */ }
  }
  setTimeout(() => { moveNavPill(false); window.dispatchEvent(new Event('resize')); }, 260);  // canvases re-measure
}
function toggleNavRail() {
  const on = !document.querySelector('.shell').classList.contains('nav-rail');
  setNavRail(on);
  if (on && document.activeElement) document.activeElement.blur();  // collapsing: don't hold it open by focus
}
let _navRailStart = false;
try { _navRailStart = localStorage.getItem('cp-nav-rail') === '1'; } catch (e) { /* pinned open */ }
setNavRail(_navRailStart, false);
document.querySelector('.sidebar').addEventListener('transitionend', e => {  // opened over the page: the pill finds its row
  if (e.propertyName === 'width') moveNavPill(false);
});
// ---- themes: light, dark, mavis. Saved per browser; switching reveals the new theme in a
// circle growing from the button you clicked (View Transitions; instant where unsupported).
function syncThemeButtons() {
  const t = document.documentElement.dataset.theme;
  document.querySelectorAll('[data-theme-btn]').forEach(b => {
    b.classList.toggle('on', b.dataset.themeBtn === t);
    b.setAttribute('aria-pressed', String(b.dataset.themeBtn === t));
  });
}
// Quick successive switches can leave an earlier view transition's callback pending; every
// callback applies the LATEST choice, so the last click always wins (found in testing 2026-09-28).
let _wantTheme = document.documentElement.dataset.theme;
function setTheme(name, ev) {
  if (_wantTheme === name) return;
  _wantTheme = name;
  const apply = () => {
    document.documentElement.dataset.theme = _wantTheme;
    try { localStorage.setItem('cp-theme', _wantTheme); } catch (e) { /* private window: theme just isn't remembered */ }
    syncThemeButtons();
    Core.recolor();
    redrawSparks();
  };
  if (!document.startViewTransition || matchMedia('(prefers-reduced-motion: reduce)').matches) { apply(); return; }
  const x = ev && ev.clientX ? ev.clientX : innerWidth / 2, y = ev && ev.clientY ? ev.clientY : innerHeight / 2;
  const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  document.startViewTransition(apply).ready.then(() => {
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
      { duration: 650, easing: 'cubic-bezier(.32,.72,0,1)', pseudoElement: '::view-transition-new(root)' });
  }).catch(() => {});
}
syncThemeButtons();

// ---- page files: each page's own code (static/pages-<area>.js) loads the first time the page opens ----
// The Overview's code comes with the page (pages-overview.js). Every other page's files are fetched by Pages.need the
// first time switchTab opens it, in the order listed (a shared file first), and each file runs once (2026-10-28: the
// page carried about 940 KB of script for every page on its first load). A page file says what switchTab and the poll
// do for its pages with Pages.on(tab, { enter, leave, poll }): enter when its page opens (once its files are in), leave
// when another page opens, poll(slow) on each poll cycle while its page is the one showing. The sidebar, keys 1-9 and
// deep links (#models) all go through switchTab, so they all load the same way. js_tests/panel_files_test.js checks
// that no file runs anything before what it uses is in, and that the core never calls into a page file directly.
const PAGE_PARTS = {
  models: ['pages-ops', 'pages-ai'], reasoning: ['pages-ops', 'pages-ai'],
  savings: ['pages-ops'], tools: ['pages-ops'], run: ['pages-ops'], docker: ['pages-ops'], monitors: ['pages-ops'],
  proxmox: ['pages-ops'], terminal: ['pages-servers'], api: ['pages-api'], guide: ['pages-guide'],
  todo: ['pages-work-admin', 'pages-todo'], rules: ['pages-work-admin', 'pages-rules'],
  settings: ['pages-work-admin', 'pages-settings'], observations: ['pages-work-admin'], projects: ['pages-work-admin'],
};
const Pages = window.Pages = (() => {
  const files = {}, hooks = {};
  function file(name) {  // one <script> per file; a failed fetch is tried again the next time its page opens
    if (!files[name]) {
      files[name] = new Promise((ok, fail) => {
        const s = document.createElement('script');
        s.src = '/' + name + '.js';
        s.async = false;  // run in the order asked: a shared file before the page's own
        s.onload = () => { files[name].done = true; ok(); };
        s.onerror = () => { s.remove(); delete files[name]; fail(new Error(`${name}.js didn't load`)); };
        document.head.appendChild(s);
      });
    }
    return files[name];
  }
  const ready = tab => (PAGE_PARTS[tab] || []).every(f => files[f] && files[f].done);
  const need = tab => Promise.all((PAGE_PARTS[tab] || []).map(file));
  const each = (tab, fn) => (hooks[tab] || []).forEach(fn);
  return {
    need, ready,
    on(tab, h) { (hooks[tab] = hooks[tab] || []).push(h); },
    hooked: () => Object.keys(hooks),  // the pages with hooks so far (the tests check each is a real page)
    opened(tab) {  // switchTab: the pages already loaded let go of what they had open, then this one starts
      for (const t of Object.keys(hooks)) if (t !== tab) each(t, h => h.leave && h.leave(tab));
      need(tab).then(() => { if (_activeTab === tab) each(tab, h => h.enter && h.enter()); },
        e => { if (_activeTab === tab) UI.toast(`This page didn't load (${e.message}). Open it again to retry.`); });
    },
    async poll(tab, slow) {  // pollOnce: the showing page's own refresh, awaited where it returns a promise
      if (!ready(tab)) return;
      for (const h of hooks[tab] || []) if (h.poll) await h.poll(slow);
    },
  };
})();
let _activeTab = 'ops';

function switchTab(tab) {
  if (!TAB_IDS[tab] || (_viewer && !VIEWER_TABS.has(tab))) return;
  const changed = tab !== _activeTab;
  _activeTab = tab;
  document.documentElement.dataset.tab = tab;  // for CSS: cheaper than a :has() over .main, re-checked on every update
  if (tab !== 'ops') document.body.classList.remove('summary');  // the summary screen is the Overview's
  document.getElementById('sumOpen').hidden = document.getElementById('ckMode').hidden = tab !== 'ops';
  document.getElementById('announceOpen').hidden = tab !== 'ops' || !_announceOk;
  if (tab !== 'ops') announcePopover(false);
  for (const [t, [view, btn]] of Object.entries(TAB_IDS)) {
    document.getElementById(view).classList.toggle('active', t === tab);
    document.getElementById(btn).classList.toggle('active', t === tab);
  }
  document.getElementById('pageTitle').textContent = PAGES[tab][0];
  document.getElementById('pageDesc').textContent = PAGES[tab][1];
  syncNavGroups(tab);
  toggleNavSheet(false);
  moveNavPill(true);
  if (changed) enterView(TAB_IDS[tab][0]);
  try { history.replaceState(null, '', tab === 'ops' ? location.pathname : '#' + tab); } catch (e) { /* cosmetic */ }
  if (tab === 'ops') Core.start(); else Core.stop();
  if (window.WA) WA.leave(tab);  // a drawer open on another of these pages closes with it
  Pages.opened(tab);  // each page's own start (loading its file the first time) is in that file: Pages.on
}
Pages.on('pro', { enter: () => loadPro() });  // the Pro page's code is here: the welcome screen shares it
// Settings, opened on the section that holds a group (or on a section by its name), from any page: its file first, since
// the chosen section is its state (_setCat, sectionOf, showSetCat are pages-settings.js's).
function openSettingsAt(name) {
  Pages.need('settings').then(() => { _setCat = sectionOf(name); switchTab('settings'); showSetCat(_setCat); });
}

function cssEscape(s) { return s.replace(/[^a-zA-Z0-9_-]/g, '_'); }

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

// --- Observations tab (mcp-server observe.*) ---------------------------------
const OBS_RULE_HINTS = [];  // rule ids the log form suggests besides the ones already logged
// The page itself: pages-work-admin.js (Obs).

// --- Shared list-page kit (2026-10-27): the API page uses it first; any long list or repeated control block can. CSS:
// .ui-pagebar .ui-metrics .ui-toolbar .ui-table .ui-drawer .ui-dialog .ui-menu .ui-feed-row .ui-toast.
const UI = (() => {
  // Page state that survives a refresh and a reload of the tab (never tokens: only filters and what's open).
  function keep(key, init) {
    try { return Object.assign({}, init, JSON.parse(sessionStorage.getItem(key) || '{}')); } catch (e) { return { ...init }; }
  }
  function save(key, obj) { try { sessionStorage.setItem(key, JSON.stringify(obj)); } catch (e) { /* private window: state just isn't kept */ } }
  const ago = sec => sec ? agoText(new Date(sec * 1000).toISOString()) : 'never';
  const day = sec => { if (!sec) return ''; const d = new Date(sec * 1000); return d.toLocaleDateString(undefined, d.getFullYear() === new Date().getFullYear() ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' }); };
  // Runs fn once per press: the button is disabled and marked busy until the server answers (no double submits).
  async function busy(btn, fn) {
    if (btn && (btn.disabled || btn.getAttribute('aria-busy') === 'true')) return;
    if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
    try { return await fn(); } finally { if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); } }
  }
  // Replaces el's content and puts focus back on the element with the same data-key (a 20 s refresh never steals focus).
  function rerender(el, html, scroller) {
    const a = document.activeElement, key = a && el.contains(a) ? a.dataset.key : null, box = scroller || el, top = box.scrollTop;
    el.innerHTML = html;
    box.scrollTop = top;
    if (key) { const again = el.querySelector(`[data-key="${CSS.escape(key)}"]`); if (again) again.focus({ preventScroll: true }); }
  }
  function refocus(key) { const el = key && document.querySelector(`[data-key="${CSS.escape(key)}"]`); if (el) el.focus({ preventScroll: true }); return !!el; }
  // A short note in the corner after the server confirms. A popover, so it shows above an open drawer.
  function toast(text, tone) {
    let t = document.getElementById('uiToast');
    if (!t) {
      t = document.createElement('div'); t.id = 'uiToast'; t.className = 'ui-toast'; t.setAttribute('role', 'status');
      if (t.showPopover) t.setAttribute('popover', 'manual');
      document.body.appendChild(t);
    }
    t.textContent = text; t.classList.toggle('bad', tone === 'bad');
    const show = on => { try { on ? (t.hidePopover(), t.showPopover()) : t.hidePopover(); } catch (e) { t.classList.toggle('show', on); } };
    show(true);
    clearTimeout(t._h); t._h = setTimeout(() => show(false), tone === 'bad' ? 7000 : 3500);
  }
  // A confirmation that names the thing and says what happens, tagged undoable or not. run() does the work: the
  // dialog stays open and busy until the server answers, an error shows inside it, and it resolves with run()'s
  // result only on success (false if cancelled). Esc can't cancel a request already sent.
  function confirm({ title, body, action, danger = true, permanent = false, run }) {
    return new Promise(resolve => {
      let d = document.getElementById('uiConfirm');
      if (!d) {
        d = document.createElement('dialog'); d.id = 'uiConfirm'; d.className = 'ui-dialog';
        d.setAttribute('aria-labelledby', 'uiConfirmTitle'); d.setAttribute('aria-describedby', 'uiConfirmBody');
        document.body.appendChild(d);
      }
      d.innerHTML = `<form class="ui-dialog-in"><div class="ui-dialog-body"><h2 id="uiConfirmTitle">${escapeHtml(title)}</h2>
          <span class="ui-tag${permanent ? ' perm' : ''}">${permanent ? "Can't be undone" : 'You can change this back'}</span>
          <div class="ui-dialog-text" id="uiConfirmBody">${body}</div><div class="ui-err" role="alert"></div></div>
        <div class="ui-dialog-foot"><button type="button" class="ghost" data-x>Cancel</button>
          <button type="submit" class="${danger ? 'ui-danger' : 'ui-primary'}" data-ok>${escapeHtml(action)}</button></div></form>`;
      const ok = d.querySelector('[data-ok]'), err = d.querySelector('.ui-err');
      let result;
      d.querySelector('[data-x]').onclick = () => { if (!ok.disabled) d.close(); };
      d.oncancel = e => { if (ok.disabled) e.preventDefault(); };
      d.onclose = () => resolve(result === undefined ? false : result);
      d.querySelector('form').onsubmit = e => {
        e.preventDefault(); err.textContent = '';
        busy(ok, async () => {
          try { const r = run ? await run() : true; result = r === undefined ? true : r; d.close(); }
          catch (x) { err.textContent = x.message || String(x); }
        });
      };
      d.showModal();
      d.querySelector('[data-x]').focus();  // the safe choice has focus; Enter on it cancels
    });
  }
  // A row's action menu: arrow keys move, Esc or a click outside closes, focus goes back to the button.
  let _menu = null;
  function closeMenu(refocusBtn) {
    if (!_menu) return;
    const { el, btn, off } = _menu; _menu = null;
    el.remove(); btn.setAttribute('aria-expanded', 'false'); document.removeEventListener('pointerdown', off, true);
    if (refocusBtn) btn.focus();
  }
  function menu(btn, items) {
    const again = _menu && _menu.btn === btn;
    closeMenu(false);
    if (again) return;
    const el = document.createElement('div');
    el.className = 'ui-menu'; el.setAttribute('role', 'menu'); el.setAttribute('aria-label', btn.getAttribute('aria-label') || 'Actions');
    el.innerHTML = items.map((it, i) => it === '-' ? '<hr>'
      : `<button type="button" role="menuitem" tabindex="-1" data-i="${i}" class="${it.danger ? 'danger' : ''}">${escapeHtml(it.label)}</button>`).join('');
    document.body.appendChild(el);
    const r = btn.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight;
    el.style.left = Math.max(8, Math.min(r.right - w, innerWidth - w - 8)) + 'px';
    el.style.top = (r.bottom + 4 + h > innerHeight - 8 ? Math.max(8, r.top - h - 4) : r.bottom + 4) + 'px';
    const off = e => { if (!el.contains(e.target) && e.target !== btn && !btn.contains(e.target)) closeMenu(false); };
    _menu = { el, btn, off };
    document.addEventListener('pointerdown', off, true);
    btn.setAttribute('aria-expanded', 'true');
    const btns = [...el.querySelectorAll('button')];
    btns[0].focus();
    el.onclick = e => { const b = e.target.closest('button'); if (!b) return; closeMenu(true); items[+b.dataset.i].run(); };
    el.onkeydown = e => {
      const i = btns.indexOf(document.activeElement);
      const go = j => { e.preventDefault(); btns[(j + btns.length) % btns.length].focus(); };
      if (e.key === 'ArrowDown') go(i + 1); else if (e.key === 'ArrowUp') go(i - 1);
      else if (e.key === 'Home') go(0); else if (e.key === 'End') go(btns.length - 1);
      else if (e.key === 'Escape') { e.preventDefault(); closeMenu(true); } else if (e.key === 'Tab') closeMenu(false);
    };
  }
  addEventListener('resize', () => closeMenu(false)); addEventListener('scroll', () => closeMenu(false), true);
  // A bounded list: the first `limit` rows, then "Show more" adds as many again. rows are ready HTML strings.
  const _shown = {};
  function feed(el, rows, { key, limit = 8, empty = 'Nothing yet.' }) {
    const n = _shown[key] || limit;
    el.innerHTML = rows.length ? rows.slice(0, n).join('') + (rows.length > n
      ? `<button type="button" class="ghost ui-feed-more" data-key="${escAttr(key)}-more">Show ${Math.min(limit, rows.length - n)} more (${rows.length - n} left)</button>` : '')
      : `<div class="empty">${escapeHtml(empty)}</div>`;
    const more = el.querySelector('.ui-feed-more');
    if (more) more.onclick = () => { _shown[key] = n + limit; feed(el, rows, { key, limit, empty }); refocus(`${key}-more`) || el.querySelectorAll('.ui-feed-row')[n]?.focus?.(); };
  }
  // A .ui-work list: the column heads stick just under its toolbar, whatever height the toolbar wraps to.
  function sticky(work) {
    const bar = work.querySelector('.ui-toolbar');
    if (!bar || !window.ResizeObserver) return;
    new ResizeObserver(() => work.style.setProperty('--ui-bar-h', bar.offsetHeight + 'px')).observe(bar);
  }
  return { keep, save, ago, day, busy, rerender, refocus, toast, confirm, menu, closeMenu, feed, sticky };
})();

// One wording for every failed load (control-panel-review #1): what failed, the likely cause, and what to do.
// The raw message stays in the tooltip for anyone debugging. Returns escaped HTML text.
function loadErr(what, err) {
  const msg = String((err && err.message) || err || 'no reply');
  if (err && err.notSetUp) return `<span class="not-set-up">${escapeHtml(msg)}</span>`;
  const next = /not part of the demo/i.test(msg) ? 'Not part of the demo.'
    : /\b(401|403)\b|sign.?in|not authenticated|view-only/i.test(msg) ? 'Your sign-in may have ended: reload the page and sign in again.'
    : /gpu service/i.test(msg) ? "Your PC's AI service isn't answering. Check your PC is on and the service is running (Models page), then press Refresh."
    : /tool server|mcp/i.test(msg) ? "HomeShed isn't answering; it may be restarting. Press Refresh in a minute."
    : /\b(502|503|504)\b|unreachable|failed to fetch|networkerror|timed? ?out/i.test(msg) ? "The server didn't answer. Press Refresh in a minute; if it keeps happening, check the Uptime page."
    : 'Press Refresh to try again.';
  return `<span title="${escAttr(msg)}">Couldn't load ${escapeHtml(what)}. ${escapeHtml(next)}</span>`;
}
async function apiCall(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d.error) {
    const err = new Error(d.error || (typeof d.detail === 'string' ? d.detail : '') || `HTTP ${r.status}`);
    err.notSetUp = !!d.not_set_up;  // a part this install doesn't have: say what it needs, never "try again"
    throw err;
  }
  return d;
}
// A GET that several parts of a page ask for at about the same time: one request while it's on its way, and its answer
// reused for `ms` after it lands (short, so nothing on screen is older than that). A failed answer is never kept.
// get.clear() forgets it, for after a change.
function shared(url, ms) {
  let at = 0, last = null;
  const get = () => {
    if (last && (!at || Date.now() - at < ms)) return last;
    at = 0;
    const p = apiCall('GET', url).then(d => { if (last === p) at = Date.now(); return d; },
      e => { if (last === p) last = null; throw e; });
    return (last = p);
  };
  get.clear = () => { last = null; at = 0; };
  return get;
}
// A guard's mode in plain words (the owner: "instead of the word block ... its vague"): the Rules page's packs and the
// Settings page's mode choices both show it.
const MODE_LABEL = { block: 'Stop', warn: 'Warn', remind: 'Note only', off: 'Off' };
// --- HomeShed Pro (branding-and-pro-review.md; facts from .claude/pro-and-paid-plan.md): plan badge, Pro page and
// Settings > Pro membership. On sale from the first public release (the owner, 2026-10-01: "its live from publish").
// Real facts only, no price and no dates. Links carry ?src=homeshed&m=<moment> so the Pro site counts
// which button was used, without any telemetry from this install.
let _isPro = false;
// The Pro page's table: [what, free, Pro]. The free column claims only what this install ships (the prepper's
// clean-VM test: voice, the guard engine and the Core pack aren't in the public release).
const PRO_ROWS = [['HomeShed and this Control Panel: tools, memory, your own and cloud models', '✓', '✓'],
  ['Every curated pack: Token Saver, Safe Operator, Team Sessions, Release Ready, Beginner Mode', '', '✓'],
  ['Pack updates and new packs as they come', '', '✓'],
  ['The known-bugs feed: fixes other users already found, looked up when Claude hits an error', '', '✓']];
// The Pro membership's status ({connected, pro, effective_pro, client_name, reason}), owner only. One answer serves
// everyone who asks within a few seconds (the plan badge, Settings > Pro membership, the Pro page, the welcome screen):
// opening Settings asked it twice at once (the 2026-10-28 perf check). Connect and Disconnect clear it.
const proStatus = shared('/api/pro', 5000);
const PRO_URL = m =>`https://mavis-ai.co.uk/homeshed?src=homeshed&m=${encodeURIComponent(m)}`;  // HomeShed's own page, live 2026-09-30
const PRO_KEYS_URL = 'https://mavis-ai.co.uk/dashboard?tab=accessKeys';  // where a member makes a key (live 2026-10-06)
// First-run welcome screen (R&D's mockup v3, 2026-10-06; the owner: "what to do to get working, how to connect a Claude
// session or an agent, Pro, and visually appealing"). Shown until "Got it"; "Show me later" hides it for this visit;
// the Guide page reopens it. Never for view-only logins. The checklist reads the panel's own status, nothing new.
const WELCOME_CHECKS = [
  { id: 'password', need: true, title: 'Control Panel password set', why: 'Only you can sign in.' },
  { id: 'tools', need: true, title: 'Tool server is answering', why: 'Memory, tools and rules are ready for your sessions.',
    todo: "The Control Panel can't reach it yet.", act: ['Check', "welcomeGo('ops')"] },
  { id: 'project', need: true, title: 'Connect your first project', why: 'One command in Claude Code, shown just below.',
    act: ['Show me', "welcomeJump('wsClaude')"] },
  { id: 'local', title: 'Local AI model', why: 'Optional. Does drafts and summaries on your PC, so you use less of your Claude plan.',
    act: ['Set up', "welcomeGo('models')"] },
  { id: 'keys', title: 'Cloud AI keys', why: 'Optional. Kept in the vault: tools use them, Claude never sees them.',
    act: ['Add a key', "welcomeGo('settings')"] },
];
async function welcomeState() {
  const get = p => apiCall('GET', p).catch(() => null);
  const [health, ctx, ai, vault] = await Promise.all([get('/api/health'), get('/api/context'), get('/api/local-ai'), get('/api/vault')]);
  return {
    password: true,  // this page opens only signed in as the owner (no open mode since 2026-10-01)
    tools: !!health && health.mcp_server === 'ok',
    project: !!ctx && Array.isArray(ctx.sessions) && ctx.sessions.length > 0,  // a joined session reports its status line
    local: !!ai && (ai.servers || []).some(s => s.type === 'llamacpp' && s.online),
    keys: !!vault && (vault.credentials || []).some(c => c.service === 'ai'),
  };
}
function welcomeChecks(state) {
  let n = 0;
  return WELCOME_CHECKS.map(c => {
    const done = state ? !!state[c.id] : false, num = c.need ? ++n : 0;
    const cls = done ? 'done' : c.need ? 'todo' : 'opt';
    const right = !state ? '' : done ? '<span class="ws-pill ok">Done</span>'
      : c.act ? `<button type="button" class="ws-pill ${c.need ? 'go' : 'soft'}" onclick="${c.act[1]}">${c.act[0]}</button>` : '';
    return `<li class="ws-check ${cls}"><div class="dot">${done ? '✓' : c.need ? num : '+'}</div>
      <div><b>${c.title}</b><span class="why">${!done && state && c.todo ? c.todo : c.why}</span></div>${right}</li>`;
  }).join('');
}
// Pro, for a new user (a member never sees it). Each pack named is a real one, and the Safe Operator count matches its
// file (test_private_blocks.py checks both). "Coming to Pro" is the owner's stated plan, not built yet.
function welcomePro() {
  return `<section class="ws-sheet ws-pro" id="welcomePro" aria-labelledby="wsProH">
      <div class="ws-pro-top">
        <div><span class="ws-tag">HomeShed Pro</span>
          <h3 id="wsProH">Everything above is free. Pro gives your AI a safety net, a night shift and a memory of other people's fixes.</h3></div>
        <div class="ws-price"><span><b>£4.99</b> / month</span><a class="btn" href="${PRO_URL('welcome')}" target="_blank" rel="noopener">Get Pro</a></div>
      </div>
      <div class="ws-feats">
        <article class="ws-feat"><span class="ic">◉</span><h4>Overnight to-do list</h4>
          <p>Give jobs to your helpers before bed. They work through them one at a time, leave a full report on each, and send one summary to your phone in the morning.</p></article>
        <article class="ws-feat"><span class="ic">⛨</span><h4>Safe Operator</h4>
          <p>Checks every step before Claude takes it. It keeps passwords out of the chat and catches failures that look like success. All 11 checks come from real incidents.</p></article>
        <article class="ws-feat"><span class="ic">⚡</span><h4>Token Saver</h4>
          <p>Stops endless polling and huge file dumps, and hands drafts to your local model, so your Claude plan lasts much longer.</p></article>
        <article class="ws-feat"><span class="ic">⇄</span><h4>Team Sessions</h4>
          <p>Run several Claude sessions at once. They message each other, announce restarts and never overwrite each other's work.</p></article>
        <article class="ws-feat"><span class="ic">✦</span><h4>Known-bugs feed</h4>
          <p>When Claude hits an error, it looks up the fix other HomeShed users already found, before it burns time working it out again.</p></article>
        <article class="ws-feat"><span class="ic">⬆</span><h4>Release Ready</h4>
          <p>Keeps secret files like .env out of anything you publish, and checks a repo is clean before it goes public.</p></article>
        <article class="ws-feat"><span class="ic">☺</span><h4>Beginner Mode</h4>
          <p>Plain words and one step at a time, and Claude asks before installing anything. Good for anyone new to the terminal.</p></article>
        <article class="ws-feat"><span class="ic">↻</span><h4>New packs, always</h4>
          <p>Every pack and every update as they're released. Packs you've installed keep working even if Pro ends.</p></article>
      </div>
      <div class="ws-soon"><span class="ws-soon-tag">Coming to Pro</span>
        <span><b>The rules and decision engine as an API.</b> Plug HomeShed's tested safety rules and its fast local
          decision model straight into your own apps and agents, tied to your account.</span></div>
    </section>`;
}
function welcomeHtml() {
  return `<div class="ws-wrap">
    <header class="ws-sheet ws-hero">
      <svg class="ws-core" viewBox="0 0 96 96" aria-hidden="true">
        <circle class="ring" cx="48" cy="48" r="40"/><circle class="ring b" cx="48" cy="48" r="30"/>
        <circle class="lit" cx="48" cy="48" r="11"/>
        <circle cx="48" cy="8" r="3.2"/><circle cx="88" cy="48" r="3.2"/><circle cx="48" cy="88" r="3.2"/><circle cx="8" cy="48" r="3.2"/>
      </svg>
      <div><h1 id="wsTitle">Welcome to HomeShed</h1>
        <p>One home base for your AI helpers. Every Claude session and agent you connect shares the same memory, tools and
          safety rules, and runs on your own machines.</p></div>
    </header>
    <section class="ws-sheet ws-section" aria-labelledby="wsRun">
      <div class="ws-head"><h2 id="wsRun">Get running</h2>
        <div class="ws-progress"><span id="wsProgress">Checking…</span><div class="ws-bar" aria-hidden="true"><i id="wsBar"></i></div></div></div>
      <ul class="ws-checks" id="wsChecks">${welcomeChecks(null)}</ul>
    </section>
    <div class="ws-two">
      <section class="ws-sheet ws-card" id="wsClaude" aria-labelledby="wsClaudeH">
        <h3 id="wsClaudeH">Connect a Claude Code session</h3>
        <p class="lead">Takes one command and one "yes". Do it once per project folder.</p>
        <ol class="ws-steps">
          <li>Open the project's folder in File Explorer.</li>
          <li>Click the address bar, type <kbd>cmd</kbd> and press Enter.</li>
          <li>Type <kbd>claude</kbd> and press Enter.</li>
          <li>Type this, press Enter, and answer <b>yes</b> when it asks:</li>
        </ol>
        <div class="ws-code"><code>/join homeshed</code><button type="button" class="ghost" onclick="welcomeCopy(this, '/join homeshed')">Copy</button></div>
        <p class="ws-note">Then start a new session in the same folder. It now has shared memory, tools and the safety rules. Nothing secret is written into the project.</p>
      </section>
      <section class="ws-sheet ws-card" aria-labelledby="wsAgentH">
        <h3 id="wsAgentH">Connect another agent or app</h3>
        <p class="lead">Any tool that speaks MCP (Cursor, another AI agent, a script) gets its own token with only the tools it needs.</p>
        <ol class="ws-steps">
          <li>Open <b>API access</b>. Under <b>Add a client</b>, give it a name and say what it's for.</li>
          <li>Pick its access to start with (all read-only tools is the safe start) and press <b>Create client</b>. You can grant or take away single tools on its row later.</li>
          <li>Copy its token straight away: it's shown only once.</li>
          <li>Paste the ready-made connection settings shown under the token into the app's MCP settings.</li>
        </ol>
        <p class="ws-note">Every call it makes shows on the Overview under its name, and you can suspend it at any time.</p>
        <div><button type="button" class="ghost" onclick="welcomeGo('api')">Open API access</button></div>
      </section>
    </div>
    ${welcomePro()}
    <div class="ws-exits"><p class="hint">You can open this again any time from the Guide page.</p>
      <div><button type="button" class="ghost" onclick="hideWelcome(true)">Show me later</button><button type="button" onclick="hideWelcome()">Got it</button></div></div>
  </div>`;
}
async function refreshWelcome() {
  const state = await welcomeState(), list = document.getElementById('wsChecks');
  if (!list) return;
  list.innerHTML = welcomeChecks(state);
  const need = WELCOME_CHECKS.filter(c => c.need), done = need.filter(c => state[c.id]).length;
  document.getElementById('wsProgress').textContent = `${done} of ${need.length} needed steps done`;
  document.getElementById('wsBar').style.width = `${Math.round(100 * done / need.length)}%`;
}
function showWelcome(force) {
  const box = document.getElementById('welcomeCard');
  let seen = false;
  try { seen = !!localStorage.getItem('cp-welcome-v2-done') || !!sessionStorage.getItem('cp-welcome-later'); }
  catch (e) { seen = true; }  // no storage: don't nag every visit
  if (!box || _viewer || (seen && !force)) return;
  if (box.parentElement !== document.body) document.body.appendChild(box);  // its home is on the Overview: from the
  // Guide page (another tab, so a hidden parent) it opened with no size at all
  box.innerHTML = welcomeHtml();
  box.hidden = false;
  document.body.classList.add('ws-open');
  box.onkeydown = e => { if (e.key === 'Escape') hideWelcome(true); };
  box.querySelector('.ws-exits button:last-child')?.focus({ preventScroll: true });
  refreshWelcome();
  proStatus().then(d => {  // a member doesn't need the pitch
    if (d && (d.pro || d.effective_pro)) document.getElementById('welcomePro')?.remove();
  }).catch(() => {});
}
function hideWelcome(later) {
  try { if (later) sessionStorage.setItem('cp-welcome-later', '1'); else localStorage.setItem('cp-welcome-v2-done', '1'); }
  catch (e) { /* hidden for this visit */ }
  const box = document.getElementById('welcomeCard');
  if (box) { box.hidden = true; box.innerHTML = ''; }
  document.body.classList.remove('ws-open');
}
function welcomeGo(tab) { hideWelcome(true); switchTab(tab); }
function welcomeJump(id) { document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
function welcomeCopy(btn, text) {
  const done = () => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); };
  try { navigator.clipboard.writeText(text).then(done, () => { btn.textContent = 'Select and copy'; }); }
  catch (e) { btn.textContent = 'Select and copy'; }
}
async function loadPlanBadge() {
  const b = document.getElementById('planBadge');
  if (!b || _viewer) return;
  let d = {};
  try { d = await proStatus(); } catch (e) { return; }
  if (!('pro' in d)) return;  // no Pro connection here: no badge
  // effective_pro: what Pro features actually get. A recent membership holds while the membership site can't be reached
  // (KB-0052), so say that rather than "Free".
  const pro = !!(d.pro || d.effective_pro), unsure = pro && !d.pro;
  _isPro = pro;
  const tp = document.getElementById('tabPro');
  if (tp && tp.hidden !== pro) { tp.hidden = pro; numberNavKeys(); }  // a Pro member doesn't need the Pro page in the menu
  [b, document.getElementById('planBadgeM')].filter(Boolean).forEach(x => {  // sidebar, and page header on phones
    x.hidden = false;
    x.textContent = unsure ? "Pro (can't check)" : (pro ? 'Pro' : 'Free');
    x.title = unsure ? "Pro (can't check right now): the membership check will try again shortly" : 'Your plan';
    x.classList.toggle('is-pro', pro);
  });
}
async function loadPro() {
  const box = document.getElementById('proBody');
  if (!box) return;
  let d = {};
  try { d = await proStatus(); } catch (e) { d = {}; }
  const rows = PRO_ROWS;
  // The user's own numbers at the moment they decide (measured, this install only)
  const week = _sys.week ? `This week HomeShed kept ${fmtTok(_sys.week)} tokens out of Claude on this PC.` : '';
  box.innerHTML = `<div class="pro-head"><div><div class="set-label">${d.pro ? 'Pro is active. Thank you: it keeps HomeShed going.' : 'HomeShed is free and complete.'}</div>
      ${d.pro ? '' : '<div class="pro-why">One independent developer builds HomeShed. Pro keeps it going, and gives you every pack that stops costly AI mistakes.</div>'}
      <div class="hint">Installed packs keep working if Pro ends.</div>
      ${week ? `<div class="hint">${escapeHtml(week)}${d.pro ? '' : ' Pro adds the Token Saver pack, which cuts wasted tokens further.'}</div>` : ''}</div>
      ${d.pro ? '' : `<a class="btn" href="${PRO_URL('pro-page')}" target="_blank" rel="noopener">Get Pro</a>`}</div>
    <table class="pro-table"><thead><tr><th></th><th>Free</th><th>Pro</th></tr></thead><tbody>
      ${rows.map(([f, a, b]) => `<tr><td>${escapeHtml(f)}</td><td>${a}</td><td>${b}</td></tr>`).join('')}</tbody></table>
    <div class="hint">${d.connected === false ? 'Already a member? Paste your Pro key in Settings, Account and sharing.' : ''}</div>`;
}

// Settings that change how this page behaves, for every login: refresh speed, animations, viewer pages.
let _refreshTimer = null;
async function applyUiSettings() {
  let u;
  try { u = await (await fetch('/api/ui-settings')).json(); } catch (e) { return; }
  if (!u || u.refresh_seconds === undefined) return;
  document.getElementById('brandSub').textContent = u.panel_name || 'Control Panel';  // Settings > Control Panel
  clearInterval(_refreshTimer);
  _refreshTimer = setInterval(loadAll, u.refresh_seconds * 1000);
  window._noAnim = !u.animations;
  document.body.classList.toggle('no-anim', window._noAnim);
  if (window._noAnim) Core.stop(); else if (_activeTab === 'ops') Core.start();
  const rate = document.getElementById('apRate');
  if (rate && u.client_default_rate) rate.value = u.client_default_rate;
  if (_viewer) {
    if (u.viewers_see_savings) VIEWER_TABS.add('savings'); else VIEWER_TABS.delete('savings');
    document.getElementById('tabSavings').style.display = u.viewers_see_savings ? '' : 'none';
    document.getElementById('ctxCard').style.display = u.viewers_see_context ? '' : 'none';
    numberNavKeys();
    if (!VIEWER_TABS.has(_activeTab)) switchTab('ops');
  }
}

// Short durations and ages for rows ("850 ms", "2.4s", "5m ago"), and text made safe inside an attribute.
function fmtMs(ms) { return ms == null ? '—' : ms < 1000 ? ms + ' ms' : ms < 60000 ? (ms / 1000).toFixed(1) + 's' : Math.floor(ms / 60000) + 'm ' + Math.round(ms % 60000 / 1000) + 's'; }
function agoText(iso) { const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? 'just now' : s < 3600 ? Math.floor(s / 60) + 'm ago' : s < 86400 ? Math.floor(s / 3600) + 'h ago' : Math.floor(s / 86400) + 'd ago'; }
function escAttr(v) { return escapeHtml(String(v)).replace(/"/g, '&quot;'); }

async function loadFeatures() {
  try {
    const d = await (await fetch('/api/features')).json();
    if (Array.isArray(d.features)) { _sys.features = d.features; renderHero(); }
  } catch (e) { /* the hero falls back to its own checks */ }
}

// Efficiency (the owner: "ensure this is efficient and not resource hungry"): nothing polls while the
// tab is hidden (it refreshes the moment you come back), and data that rarely changes (the tool
// list, memory) refreshes every 5th cycle (20 s) instead of every 4 s.
// 2026-10-27 (the optimisation check: about 150 calls a minute from the Overview): feeds that change slowly ask every
// 15 or 30 s, not every cycle, and a cycle never starts while the last one is still waiting on a reply.
let _pollCycle = 0, _polling = false;
const _lastAsked = {};
function feedDue(feed, ms) {
  const now = Date.now();
  if (now - (_lastAsked[feed] || 0) < ms) return false;
  _lastAsked[feed] = now;
  return true;
}
const _inFlight = {};
function feed(name, ms, load) {
  if (_inFlight[name] || !feedDue(name, ms)) return;
  _inFlight[name] = true;
  Promise.resolve().then(load).catch(() => {}).finally(() => { _inFlight[name] = false; });
}
async function loadAll(force) {
  if (force === true) for (const k in _lastAsked) delete _lastAsked[k];  // the Refresh button asks for everything now
  if (document.hidden || _polling) return;
  _polling = true;
  try { await pollOnce(); } finally { _polling = false; }
}
async function pollOnce() {
  const slow = _pollCycle++ % 5 === 0;
  // The Overview's own feeds start first: none of them waits for a slow reply below (or for each other).
  if (_activeTab === 'ops') {  // each on its own clock, never asked again while its last answer is still on the way
    if (!_viewer) { feed('clients', 15000, loadClientsLive); feed('sessions', 30000, loadPastSessions); feed('progress', 30000, loadProgress); }
    feed('context', 15000, loadContextLive);
    feed('features', 30000, loadFeatures);
  }
  // Capabilities/risk map must land before activity, so activity flashes can look up risk. Tool calls (activity) are
  // asked every cycle: they are what the live parts of the page move on. Health every 8 s; the rest less often.
  await Promise.all([feedDue('health', 7500) ? loadHealth() : null, feedDue('caps', 30000) ? loadCapabilities() : null,
    feedDue('memory', 30000) ? loadMemory() : null, feedDue('local-ai', 15000) ? loadLocalAi() : null]);
  await loadActivity();
  // Only the page showing refreshes its own data (its file's Pages.on poll): polling a page forever after a single visit
  // was real, if pointless, background API traffic (Monitors and Docker, found live 2026-09-23).
  await Pages.poll(_activeTab, slow);
  document.getElementById('refreshTag').textContent = document.getElementById('sumUpdated').textContent =
    'Updated ' + new Date().toLocaleTimeString('en-GB', { hour12: false });
  renderHero();
  if (_activeTab === 'ops') syncOverview();
  if (window.HomeShedBoot) HomeShedBoot.loaded();
}

// Collapsible cards: a button in each card's header hides the rest of the card, remembered per browser. A card opts
// in with data-card (its key) and a .live-head or .card-head header; data-name names it for screen readers.
function initCollapsibles() {
  let closed = [];
  try { closed = JSON.parse(localStorage.getItem('cp-collapsed') || '[]'); } catch (e) { closed = []; }
  document.querySelectorAll('[data-card]').forEach(card => {
    const head = card.querySelector(':scope > .live-head, :scope > .card-head');
    if (!head) return;
    const key = card.dataset.card, name = card.dataset.name || head.querySelector('.live-title')?.textContent || 'this card';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'card-toggle';
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 5l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    const show = shut => {
      card.classList.toggle('collapsed', shut);
      btn.setAttribute('aria-expanded', String(!shut));
      btn.setAttribute('aria-label', `${shut ? 'Show' : 'Hide'} ${name} details`);
      btn.title = shut ? 'Show details' : 'Hide details';
    };
    show(closed.includes(key));
    btn.addEventListener('click', () => {
      const shut = !card.classList.contains('collapsed');
      show(shut);
      closed = shut ? [...new Set([...closed, key])] : closed.filter(k => k !== key);
      try { localStorage.setItem('cp-collapsed', JSON.stringify(closed)); } catch (e) { /* not remembered */ }
    });
    head.appendChild(btn);
  });
}
// Observations change rarely -- once at boot plus every 60s keeps the tab badge honest without
// adding an MCP handshake to the 4s poll.
// Badge only: the lightweight summary route, not observe.list (which would count as a capability
// read every minute). The full list loads when the Observations tab is opened.
async function loadObsBadge() {
  try {
    const d = await (await fetch('/api/observations/summary')).json();
    const badge = document.getElementById('obsBadge');
    badge.textContent = d.open || 0; badge.style.display = d.open ? 'inline-grid' : 'none';
    mirrorBadges();
    _sys.obsOpen = d.open || 0;
    renderHero();
  } catch (e) { /* badge is cosmetic */ }
}
// Only show what's installed (packaging plan, Release UX): a page or Settings group for a part this setup doesn't
// have is hidden; a part that's merely down keeps its page and says why (the server decides "installed").
const INSTALL_TABS = {};    // page: the part it needs
const INSTALL_GROUPS = {};  // Settings group: the part it needs
let _installed = {};
async function loadInstalled() {
  try { _installed = await (await fetch('/api/installed')).json(); } catch (e) { return; }
  Object.entries(INSTALL_TABS).forEach(([tab, part]) => {
    const btn = document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1));
    if (btn) btn.hidden = _installed[part] === false;
  });
  if (_installed[INSTALL_TABS[_activeTab]] === false) switchTab('ops');
  numberNavKeys();
  moveNavPill(false);
}
const _installedReady = loadInstalled();
// Approvals (the owner, 2026-10-06, to-do #51: "notified in the dash if something is waiting ... a pop-out notification
// at the top, dynamic if the page is scrolled; then I can approve or deny from there"): to-do tasks waiting for an OK,
// apps' writes to the shared memory, and queued Proxmox changes, from /api/approvals.
const APPROVAL_KINDS = {
  todo: { label: 'To-do task', no: 'Cancel task',
    act: (id, ok) => apiCall('POST', `/api/todo/${encodeURIComponent(id)}`,
      ok ? { approve: true } : { status: 'cancelled', comment: 'Declined from the approvals bar' }) },
  memory: { label: 'Shared memory write', no: 'Decline',
    act: (id, ok) => apiCall('POST', `/api/shared-memory/waiting/${encodeURIComponent(id)}/${ok ? 'approve' : 'decline'}`) },
  proxmox: { label: 'Proxmox change', no: 'Decline',
    act: (id, ok) => apiCall('POST', `/api/proxmox/waiting/${encodeURIComponent(id)}/${ok ? 'approve' : 'decline'}`) },
  printify: { label: 'Publish to Etsy', no: 'Decline',  // to-do #59: approving puts it live on the shop
    act: (id, ok) => apiCall('POST', `/api/printify/waiting/${encodeURIComponent(id)}/${ok ? 'approve' : 'decline'}`) },
  // the same failed tool call in 2+ sessions (2026-10-24): keep makes it a known bug, so every session gets its fix
  bug: { label: 'Suggested bug', yes: 'Keep as a bug', no: 'Not a bug',
    act: (id, ok) => apiCall('POST', `/api/bugs/suggested/${encodeURIComponent(id)}/${ok ? 'keep' : 'dismiss'}`) },
  // who's who among the agents (2026-10-24): an agent added or changed a row; the owner's wording wins once confirmed
  roster: { label: 'Agent roster', yes: 'Confirm', no: 'Decline',
    act: (id, ok) => apiCall('POST', `/api/agents/roster/${encodeURIComponent(id)}/${ok ? 'confirm' : 'decline'}`) },
  // HomeShed signs a read-only library script (Homeshed-addon #58): runners that trust the key then run it unasked
  sign: { label: 'Sign a library script', yes: 'Sign it', no: 'Decline', detailLabel: 'Read the code',
    act: (id, ok) => apiCall('POST', `/api/publisher/sign/${encodeURIComponent(id)}/${ok ? 'approve' : 'decline'}`) },
  // a chat app signing in with OAuth (oauth_server.py, 2026-10-27): only the app name and the access level are sent
  signin: { label: 'Sign-in waiting', no: 'Decline',
    act: (id, ok, item) => apiCall('POST', `/api/oauth/pending/${encodeURIComponent(id)}/${ok ? 'approve' : 'decline'}`,
      ok ? { app: item.querySelector('.signin-app').value.trim().toLowerCase(), preset: item.querySelector('.signin-preset').value,
             trust: !!item.querySelector('.signin-trust-in')?.checked } : undefined) },
};
let _appr = { items: [], unavailable: [], open: false, seenSignins: new Set(), apps: [] };
// A sign-in lasts 10 minutes, so a new one opens the list by itself (once), and its time left counts down every second.
function noticeSignins() {
  const ids = _appr.items.filter(i => i.kind === 'signin').map(i => i.id);
  const fresh = ids.filter(id => !_appr.seenSignins.has(id));
  ids.forEach(id => _appr.seenSignins.add(id));
  if (!fresh.length) return;
  apiCall('GET', '/api/clients').then(d => { _appr.apps = (d.clients || []).map(c => c.name); renderSigninApps(); }).catch(() => {});
  if (!_appr.open) { _repl.open = _msg.open = false; _appr.open = true; renderReplies(); renderMessages(); }
}
function renderSigninApps() {
  const dl = document.getElementById('signinApps');
  if (dl) dl.innerHTML = _appr.apps.map(n => `<option value="${escAttr(n)}">`).join('');
}
function signinLeftText(deadline) {
  const s = Math.max(0, Math.round((deadline - Date.now()) / 1000));
  return s ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s left` : 'expired: ask the app to sign in again';
}
// Counts down each second while a sign-in card shows and the tab is visible; no timer otherwise (2026-10-28).
let _signinT = 0;
function signinTick() {
  clearTimeout(_signinT); _signinT = 0;
  const els = document.hidden ? [] : document.querySelectorAll('.signin-left');
  els.forEach(el => { el.textContent = signinLeftText(Number(el.dataset.deadline)); });
  if (els.length) _signinT = setTimeout(signinTick, 1000);
}
document.addEventListener('visibilitychange', signinTick);
async function loadApprovals() {
  if (_viewer) return;
  let d;
  try { d = await apiCall('GET', '/api/approvals'); } catch (e) { return; }  // a view-only login is refused: nothing shows
  _appr.items = (d.items || []).filter(i => APPROVAL_KINDS[i.kind]);
  _appr.items.forEach(i => { if (i.signin) i.deadline = Date.now() + (i.signin.expires_in_s || 0) * 1000; });
  _appr.unavailable = d.unavailable || [];
  noticeSignins();
  const n = _appr.items.length;
  document.getElementById('apprPill').hidden = !n;
  const sIn = _appr.items.filter(i => i.kind === 'signin').length;
  // a first sign-in often comes while the welcome screen is up: lift the bar above it so it's seen (the prepper, 27 Oct)
  document.getElementById('approvals').classList.toggle('has-signin', sIn > 0);
  document.getElementById('apprCount').textContent = `${n} waiting for you${sIn ? `, ${sIn} ${sIn === 1 ? 'is a sign-in' : 'are sign-ins'}` : ''}`;
  if (!n) _appr.open = false;
  showTopPills();
  renderApprovals();
}
function showTopPills() {
  document.getElementById('approvals').hidden = ['apprPill', 'replPill', 'msgPill'].every(id => document.getElementById(id).hidden);
  for (const [pill, chip] of [['apprPill', 'sumAppr'], ['replPill', 'sumRepl'], ['msgPill', 'sumMsg']]) {  // the summary bar's copies
    const p = document.getElementById(pill), c = document.getElementById(chip);
    c.hidden = p.hidden;
    c.lastElementChild.textContent = p.lastElementChild.textContent;
  }
}
function openFromSummary(name) { setSummary(false); onlyPopup(name); }
// One popup open at a time: opening one closes the others.
function onlyPopup(name) {
  _appr.open = name === 'appr' && !_appr.open; _repl.open = name === 'repl' && !_repl.open; _msg.open = name === 'msg' && !_msg.open;
  renderApprovals(); renderReplies(); renderMessages();
}
// Messages from agents (the owner, 2026-10-06: "a notification like the others for agents to leave me a message. i can
// reply to them through it too, say if i need to run a command, or you need something"): inbox.send on the tool
// server; he replies or marks one done here, and the agent reads it with inbox.replies.
let _msg = { rows: [], open: false };
const MSG_NEEDS = { run_command: 'Run a command', decision: 'Decision needed', info: 'Message', other: 'Message' };
async function loadMessages() {
  if (_viewer) return;
  let d;
  try { d = await apiCall('GET', '/api/inbox'); } catch (e) { return; }
  _msg.rows = d.items || [];
  const n = _msg.rows.length;
  document.getElementById('msgPill').hidden = !n;
  document.getElementById('msgCount').textContent = `${n} ${n === 1 ? 'message' : 'messages'} from agents`;
  if (!n) _msg.open = false;
  showTopPills();
  const typing = [...document.querySelectorAll('#msgList textarea')].some(t => t.value.trim() || t === document.activeElement);
  if (!typing) renderMessages();
}
function renderMessages() {
  const list = document.getElementById('msgList');
  list.hidden = !_msg.open;
  document.getElementById('msgPill').setAttribute('aria-expanded', String(_msg.open));
  if (!_msg.open) return;
  list.innerHTML = _msg.rows.map(r => {
    const id = escAttr(r.id), last = (r.replies || []).slice(-1)[0];
    return `<div class="appr-item">
      <p class="hint"><b>${MSG_NEEDS[r.need] || 'Message'}</b> from ${escapeHtml(r.sender || '?')}${r.at ? `, ${agoText(new Date(r.at * 1000).toISOString())}` : ''}</p>
      <div class="appr-text">${escapeHtml(r.text || '')}${r.edited_at ? ' <span class="hint" title="The agent corrected this message after sending it">(edited)</span>' : ''}</div>
      ${r.command ? `<div class="msg-cmd"><code>${escapeHtml(r.command)}</code><button type="button" class="ghost" onclick="copyMsgCommand(this, '${id}')">Copy</button></div>` : ''}
      ${last ? `<p class="hint">You replied: ${escapeHtml(last.text)}</p>` : ''}
      <div class="appr-reply"><textarea rows="2" maxlength="2000" aria-label="Reply to ${escapeHtml(r.sender || 'the agent')}" placeholder="Reply: the agent reads it with inbox.replies"></textarea></div>
      <div class="appr-act"><button type="button" onclick="answerMessage(this, '${id}', false)">Send reply</button>
        <button type="button" class="ghost" onclick="answerMessage(this, '${id}', true)">${r.need === 'run_command' ? 'Ran it, done' : 'Done'}</button></div>
    </div>`;
  }).join('');
}
function toggleMessages() { onlyPopup('msg'); }
function copyMsgCommand(btn, id) {
  const r = _msg.rows.find(x => x.id === id);
  try { navigator.clipboard.writeText(r.command); btn.textContent = 'Copied'; } catch (e) { btn.textContent = 'Copy failed'; }
}
// Send reply keeps it open (the agent may answer back); Done closes it, with the typed reply if there is one.
async function answerMessage(btn, id, done) {
  const box = btn.closest('.appr-item').querySelector('textarea'), text = box.value.trim();
  if (!done && !text) { box.focus(); return; }
  btn.disabled = true;
  try {
    if (text) await apiCall('POST', `/api/inbox/${id}/reply`, { text, done });
    else await apiCall('POST', `/api/inbox/${id}/done`);
  } catch (e) { btn.disabled = false; btn.textContent = e.message; return; }
  box.value = '';
  btn.textContent = done ? 'Done' : 'Sent';
  setTimeout(loadMessages, 600);
}
// Replies on the to-do list (the owner, 2026-10-06: "i need to know when i have replies on todo list too ... like i
// do approvals"): helpers' comments since he last marked them read, from /api/todo/replies.
let _repl = { rows: [], open: false };
async function loadReplies() {
  if (_viewer) return;
  let d;
  try { d = await apiCall('GET', '/api/todo/replies'); } catch (e) { return; }
  _repl.rows = d.replies || [];
  const n = d.count || 0;
  document.getElementById('replPill').hidden = !n;
  document.getElementById('replCount').textContent = `${n} new ${n === 1 ? 'reply' : 'replies'}`;
  if (!n) _repl.open = false;
  showTopPills();
  const typing = [...document.querySelectorAll('#replList textarea')].some(t => t.value.trim() || t === document.activeElement);
  if (!typing) renderReplies();  // never wipe a reply being written
}
function renderReplies() {
  const list = document.getElementById('replList');
  list.hidden = !_repl.open;
  document.getElementById('replPill').setAttribute('aria-expanded', String(_repl.open));
  if (!_repl.open) return;
  list.innerHTML = `<div class="repl-head"><span class="hint">Replies on your to-do list</span>
      <button type="button" class="ghost" onclick="markRepliesRead()">Mark all read</button></div>`
    + _repl.rows.map(r => `<div class="appr-item">
      <p class="hint"><b>#${escapeHtml(String(r.id))}</b> ${escapeHtml(r.task || '')}</p>
      <div class="appr-text"><b>${escapeHtml(r.by || '?')}</b>: ${escapeHtml(r.text || '')}</div>
      <p class="hint">${r.at ? agoText(new Date(r.at * 1000).toISOString()) : ''}${r.status ? ` · ${escapeHtml(r.status)}` : ''}</p>
      <div class="appr-reply"><textarea rows="2" maxlength="500" aria-label="Reply to #${escapeHtml(String(r.id))}" placeholder="Reply: the agent reads it before carrying on"></textarea></div>
      <div class="appr-act"><button type="button" onclick="replyFromPopup(this, ${Number(r.id)})">Send reply</button>
        <button type="button" class="ghost" onclick="openReply(${Number(r.id)})">Open task</button></div>
    </div>`).join('');
}
function toggleReplies() { onlyPopup('repl'); }
// The owner, 2026-10-06: "open the to-do list goes to that one and opens the side panel? or do it from that popup?"
// Both: Open task lands on that task with its side panel open; Send reply answers from the popup, which counts as read.
function openReply(id) {  // the To-do page's file first: the task to open is its state
  _repl.open = false; renderReplies();
  Pages.need('todo').then(() => { _todo.open = Number(id); switchTab('todo'); });
}
async function replyFromPopup(btn, id) {
  const box = btn.closest('.appr-item').querySelector('textarea'), text = box.value.trim();
  if (!text) { box.focus(); return; }
  btn.disabled = true;
  try { await apiCall('POST', `/api/todo/${id}`, { comment: text }); }
  catch (e) { btn.disabled = false; btn.textContent = e.message; return; }
  btn.textContent = 'Sent';
  setTimeout(loadReplies, 600);  // replying reads it: it leaves the list
}
async function markRepliesRead() {
  try { await apiCall('POST', '/api/todo/replies/seen'); } catch (e) { return; }
  _repl.open = false;
  loadReplies();
}
function renderApprovals() {
  queueMicrotask(signinTick);  // once this render is done: a sign-in card starts its countdown
  const list = document.getElementById('apprList'), pill = document.getElementById('apprPill');
  list.hidden = !_appr.open;
  pill.setAttribute('aria-expanded', String(_appr.open));
  if (!_appr.open) return;
  const typed = {};  // a sign-in's app name and access level, kept across the 30-second refresh
  list.querySelectorAll('.appr-item.signin').forEach(el => {
    typed[el.dataset.id] = [el.querySelector('.signin-app').value, el.querySelector('.signin-preset').value,
                            el.querySelector('.signin-trust-in').checked];
  });
  list.innerHTML = _appr.items.map(i => {
    const k = APPROVAL_KINDS[i.kind], when = i.at ? `, ${agoText(new Date(i.at * 1000).toISOString())}` : '';
    if (i.kind === 'signin') return signinCard(i, k, typed[i.id]);
    return `<div class="appr-item">
      <p class="hint"><b>${({ update: 'Update on Etsy', etsy: 'Etsy listing details', etsy_shop: 'Etsy shop texts', pinterest_pin: 'Pinterest pin', pinterest_board: 'Pinterest board' })[i.action] || k.label}</b>${i.from ? ` from ${escapeHtml(i.from)}` : ''}${when}</p>
      <div class="appr-text">${escapeHtml(i.title || '')}</div>
      ${i.detail ? `<details class="appr-detail"><summary>${k.detailLabel || 'Read the full new text'}</summary><div class="appr-text" style="white-space:pre-wrap;max-height:40vh;overflow:auto">${escapeHtml(i.detail)}</div></details>` : ''}
      ${(i.images || []).length ? `<div class="appr-imgs">${i.images.map(m => m.thumb
        ? `<a href="${escAttr(m.thumb)}" target="_blank" rel="noopener"><img src="${escAttr(m.thumb)}" alt="${escAttr(m.label)}" title="${escAttr(m.label)}" loading="lazy"></a>`
        : `<span class="hint">${escapeHtml(m.label)}</span>`).join('')}</div>` : ''}
      <div class="appr-act" data-kind="${escAttr(i.kind)}" data-id="${escAttr(i.id)}"><button type="button" onclick="decideApproval(this, true)">${k.yes || 'Approve'}</button>
        <button type="button" class="ghost" onclick="decideApproval(this, false)">${k.no}</button></div>
    </div>`;
  }).join('') + (_appr.unavailable.length ? `<div class="appr-note">Couldn't check: ${escapeHtml(_appr.unavailable.join(', '))}.</div>` : '');
  list.querySelectorAll('.appr-item.signin').forEach(el => {
    const t = typed[el.dataset.id];
    if (t) { el.querySelector('.signin-app').value = t[0]; el.querySelector('.signin-preset').value = t[1];
             el.querySelector('.signin-trust-in').checked = !!t[2]; }
  });
  renderSigninApps();
}
// A chat app (Claude's custom connector, ChatGPT...) signing in: the owner checks the code matches the one on the
// sign-in page, picks which app it becomes (a new name, or one already on the API page) and what it may use.
function signinCard(i, k, typed) {
  const s = i.signin || {};
  return `<div class="appr-item signin" data-id="${escAttr(i.id)}">
      <p class="hint"><b>${k.label}</b>: <span class="signin-left" data-deadline="${Number(i.deadline) || 0}">${signinLeftText(Number(i.deadline) || 0)}</span></p>
      <div class="appr-text"><b>${escapeHtml(s.app_name || 'An app')}</b> wants to connect to HomeShed.</div>
      <p class="hint">It says it comes from <b>${escapeHtml(s.client_host || 'an unknown place')}</b>${s.verified
        ? ' (checked: it named itself by its own https web address, its published identity).'
        : ' <span class="pill bad" title="It registered itself just now, so HomeShed cannot check who it really is">unverified</span> (it registered itself just now, so its name can\'t be checked).'}</p>
      <div class="signin-code" aria-label="Sign-in code">${escapeHtml(s.match || '')}</div>
      <p class="signin-warn"><b>Approve only if you just started this sign-in yourself and the code matches the one on your screen.</b></p>
      <p class="hint">Check this matches the code on the sign-in page. If it doesn't, or you didn't start this sign-in, press Decline.</p>
      <p class="hint">Once approved, it sends the person back to <b>${escapeHtml(s.redirect_host || 'the app')}</b>.${s.client_id ? ` The app calls itself <span style="font-family:var(--mono)">${escapeHtml(s.client_id)}</span>.` : ''}</p>
      <div class="signin-form">
        <label>App name (how it shows on the Access &gt; API page; pick an existing one to sign in as it)
          <input class="signin-app" list="signinApps" maxlength="32" value="${escAttr(s.suggested_name || '')}" aria-label="App name"></label>
        <label>Access (which tools it may use; change them later on the API page)
          <select class="signin-preset" aria-label="Access">
            <option value="none">No tools</option><option value="read" selected>Read-only (recommended)</option><option value="all">Everything</option>
          </select></label>
      </div>
      <p class="hint">An existing app keeps the tools it already has. Read-only lets it look things up but not change anything.</p>
      <label class="signin-trust"><input type="checkbox" class="signin-trust-in"> Trust this app on the browser that asked
        <span class="hint">For 90 days, this same app signing in again from that browser goes straight through, with no code.
          Only that browser: anyone else still needs your OK. Remove it any time on the API page.</span></label>
      <div class="appr-act" data-kind="signin" data-id="${escAttr(i.id)}"><button type="button" onclick="decideApproval(this, true)">Approve</button>
        <button type="button" class="ghost" onclick="decideApproval(this, false)">${k.no}</button></div>
    </div>`;
}
function toggleApprovals() { onlyPopup('appr'); }
async function decideApproval(btn, ok) {
  // kind and id ride in data- attributes: inside a JS string in onclick, a backslash in an id (a session named after a
  // Windows folder) was dropped, and the server got no such row (2026-10-24)
  const row = btn.closest('.appr-act'), { kind, id } = row.dataset, buttons = row.querySelectorAll('button');
  buttons.forEach(b => { b.disabled = true; });
  let res;
  try { res = await APPROVAL_KINDS[kind].act(id, ok, btn.closest('.appr-item')); }
  catch (e) {  // it stays waiting: say why, and leave the buttons for another go
    btn.closest('.appr-item').querySelector('.hint').insertAdjacentHTML('beforeend', ` · <span style="color:var(--bad)">${escapeHtml(e.message)}</span>`);
    buttons.forEach(b => { b.disabled = false; });
    return;
  }
  if (kind === 'signin') {  // say what happened for a few seconds before the card goes
    btn.closest('.appr-item').querySelector('.appr-text').textContent = ok
      ? `Approved: it's connected as "${(res || {}).app || 'the app'}". The sign-in page sends the person back by itself.`
      : 'Declined: that sign-in ends and the app gets nothing.';
    await new Promise(r => setTimeout(r, 4000));
  }
  await loadApprovals();
  if (kind === 'signin' && _activeTab === 'api' && Pages.ready('api')) loadApi();
  if (kind === 'memory' && Pages.ready('api')) loadSharedWaiting();  // the API page's own list
  if (kind === 'todo' && _activeTab === 'todo' && Pages.ready('todo')) loadTodo();
}
// Announcements (the owner, 2026-10-01: "send an announcement to all active agents"): the helper on the PC posts it to
// the notice board every Claude session's hooks read; each takes it once, at its next prompt or step. Owner only.
async function loadAnnouncements(error) {
  const card = document.getElementById('announceCard'), list = document.getElementById('announceList');
  let d;
  const opener = document.getElementById('announceOpen');
  try { d = await apiCall('GET', '/api/announce'); } catch (e) { _announceOk = false; opener.hidden = true; announcePopover(false); return; }
  _announceOk = !_viewer;
  opener.hidden = !_announceOk || _activeTab !== 'ops';
  if (!_announceOk) announcePopover(false);
  const items = d.announcements || [], open = announceHistoryOpen();
  // The history (the owner, 2026-10-01: "allow it to be collapsible. and removeable entries or clear"): open or shut
  // per browser; removing takes an entry off this list only, the sessions already have it.
  const head = items.length ? `<div class="announce-head"><button type="button" class="ghost" aria-expanded="${open}"
      onclick="toggleAnnounceHistory()">${open ? 'Hide' : 'Show'} sent (${items.length})</button>
      <button type="button" class="ghost" onclick="hideAnnouncements()" title="Take every one off this list">Clear</button></div>` : '';
  const lines = !open ? [] : items.map(a => {
    const when = agoText(new Date(a.at * 1000).toISOString());
    const who = !a.have.length && !a.waiting.length ? 'No session is open.'
      : !a.waiting.length ? `Every open session has it (${a.have.join(', ')}).`
      : `${a.have.length ? `${a.have.join(', ')} ${a.have.length === 1 ? 'has' : 'have'} it. ` : ''}`
        + `${a.waiting.join(', ')} ${a.waiting.length === 1 ? 'gets' : 'get'} it at the next step.`;
    return `<div class="announce-item"><span><q>${escapeHtml(a.message)}</q> Sent ${when}. ${escapeHtml(who)}</span>
      <button type="button" class="ghost" data-id="${escAttr(a.id || '')}" onclick="hideAnnouncements(this.dataset.id)"
        aria-label="Remove from this list" title="Remove from this list">×</button></div>`;
  });
  list.innerHTML = (error ? [`<div class="bad">${escapeHtml(error)}</div>`] : []).concat(head ? [head] : [], lines).join('');
}
// The owner, 2026-10-27: not a full-width row at the top of the Overview but a small button in the header that opens
// this as a popover (Esc or a click outside closes it).
var _announceOk = false;  // var: switchTab can run before this line does
function announcePopover(open) {
  const card = document.getElementById('announceCard'), btn = document.getElementById('announceOpen');
  if (open === undefined) open = card.hidden;
  if (open && !_announceOk) return;
  card.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
  if (open) document.getElementById('announceIn').focus();
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !document.getElementById('announceCard').hidden) { announcePopover(false); document.getElementById('announceOpen').focus(); }
});
document.addEventListener('pointerdown', e => {
  const card = document.getElementById('announceCard');
  if (!card.hidden && !card.contains(e.target) && !e.target.closest('#announceOpen')) announcePopover(false);
});
function announceHistoryOpen() {
  try { return localStorage.getItem('cp-announce-open') !== '0'; } catch (e) { return true; }
}
function toggleAnnounceHistory() {
  try { localStorage.setItem('cp-announce-open', announceHistoryOpen() ? '0' : '1'); } catch (e) { /* not remembered */ }
  loadAnnouncements();
}
async function hideAnnouncements(id) {  // one entry by its id, or (no id) every one
  try { await apiCall('POST', '/api/announce/hide', id ? { ids: [id] } : { all: true }); await loadAnnouncements(); }
  catch (e) { await loadAnnouncements(`Not removed: ${e.message}`); }
}
async function sendAnnouncement(ev) {
  ev.preventDefault();
  const input = document.getElementById('announceIn'), btn = document.getElementById('announceBtn');
  const message = input.value.trim();
  if (!message || btn.disabled) return;
  btn.disabled = true;
  let error = '';
  try { await apiCall('POST', '/api/announce', { message }); input.value = ''; }
  catch (e) { error = `Not sent: ${e.message}`; }
  await loadAnnouncements(error);
  btn.disabled = false;
}
