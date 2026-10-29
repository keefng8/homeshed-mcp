/* The Control Panel's start-up (static/panel-start.js): the last of the page's scripts, so everything it calls is
   defined. It hands over to boot.js, starts the poll and the header's own refreshes, learns whether this is a view-only
   login, and opens a deep link (#models etc.), whose page file the loader then fetches. */
// Retry runs a full cycle (tools and memory too), not just the 4-second part.
if (window.HomeShedBoot) HomeShedBoot.mount({ enter: finishBoot, retry: () => { _pollCycle = 0; loadAll(); } });
else setTimeout(finishBoot, 0);

document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
  if (e.key === '[' && matchMedia('(min-width: 861px)').matches) { toggleNavRail(); return; }
  if (e.key === 'Escape') { toggleNavSheet(false); if (document.body.classList.contains('summary')) setSummary(false); }
  const btn = /^[1-9]$/.test(e.key) ? _navKeys[Number(e.key) - 1] : null;
  if (btn) btn.click();
});

initCollapsibles();
loadAll();
_refreshTimer = setInterval(loadAll, 4000);
applyUiSettings();
document.addEventListener('visibilitychange', () => { if (!document.hidden) { loadAll(); loadTokens(); } });
fetch('/api/me').then(r => r.json()).then(d => {
  if (d.role !== 'viewer') return;
  _viewer = true;
  document.body.classList.add('viewer');
  window.HSCockpit && HSCockpit.ingest('viewer');  // no chats or GitHub window for a view-only login
  if (d.name) document.querySelector('.viewer-pill').textContent = `View only (${d.name})`;
  applyUiSettings();
  if (!VIEWER_TABS.has(_activeTab)) switchTab('ops');
  // opened before the role was known: swap the refused real list for the example (still loading: its start does that)
  if (_activeTab === 'todo' && Pages.ready('todo')) loadTodo();
  numberNavKeys();
  moveNavPill(false);
}).catch(() => { /* treated as the owner; the server still decides what's served */ });
loadObsBadge();
setInterval(() => { if (!document.hidden && !_viewer) loadObsBadge(); }, 120000);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && (_appr.open || _repl.open || _msg.open)) { _appr.open = _repl.open = _msg.open = false; renderApprovals(); renderReplies(); renderMessages(); }
});
loadApprovals();
loadReplies();
loadMessages();
setInterval(() => { if (!document.hidden && !_viewer) { loadApprovals(); loadReplies(); loadMessages(); } }, 30000);
// While the welcome screen is up, a new user is likely connecting their first chat app: check for sign-ins more often.
setInterval(() => { if (!document.hidden && !_viewer && !document.getElementById('welcomeCard').hidden) loadApprovals(); }, 8000);
loadAnnouncements();
setInterval(() => { if (!document.hidden && !_viewer && _activeTab === 'ops') loadAnnouncements(); }, 30000);
loadTokens();
setInterval(() => { if (!document.hidden) loadTokens(); }, 30000);
// Deep link: #models etc. opens that page, so a refresh keeps you where you were.
const _hashTab = location.hash.slice(1).split('?')[0];  // '#settings?etsy=ok': Connect Etsy's return
if (TAB_IDS[_hashTab] && _hashTab !== 'ops') switchTab(_hashTab);
if (_hashTab === 'summary') setSummary(true);
