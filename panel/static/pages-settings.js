/* The Settings page (static/pages-settings.js): every setting by section, search, the Connect buttons, keys and
   passwords, view-only logins and the owner's password. Loaded the first time the page opens, after
   pages-work-admin.js (Pages in panel.js). */
// --- Settings page: every setting, wherever it lives, in categories -----------------------------------
// the owner 2026-09-28: "finish the full settings panel ... many options to change by now in categories".
// /api/settings gathers the helper PC's, HomeShed's and this Control Panel's settings; each change
// goes back to the one that owns it. Capabilities and Connections are drawn from their own routes.
const SET_ORDER = ['Claude context', 'Rules and monitoring', 'AI models', 'Notifications', 'API access',
  'Capabilities', 'Sharing', 'Control Panel', 'Credentials', 'APIs you can use', 'Connections',
  'Storage on your PC'];
const SET_WHERE = { 'main-pc': 'Saved on your PC', 'tool-server': 'Saved on HomeShed', panel: 'Saved on this Control Panel' };
const SET_INTRO = {
  'Claude context': "Claude's working memory fills up during long sessions. Choose when you, and Claude, get warned.",
  'Rules and monitoring': "Rules keep Claude using your own machines first. Choose how strictly they're enforced.",
  'AI models': 'How the local AI chooses between your own model and cloud models.',
  'Notifications': 'Alerts sent to your phone through ntfy.',
  'Proxmox alarms': "Your phone hears once when something on the Proxmox cluster crosses a line below, and once when it's back to normal. The Proxmox tab shows the same alarms.",
  'API access': 'Defaults for apps and projects that use HomeShed.',
  'Image generation': "Which image providers and models Claude may use. Your PC's own models are free; every paid provider is off until you switch it on, give it a daily cap and add its key under Keys and passwords. Licences: image.providers.list.",
  'Language models': "Let apps (such as a shop agent) use a paid model such as Grok through HomeShed, so they never hold the key. Off until you switch a provider on and give it a daily cap; spend per app: llm.providers.list.",
  'Online shops': "Other shop settings.",
  'Etsy': "Let apps (such as a shop agent) use your Etsy shop through HomeShed, so they never hold the keys. Buyers' personal details are left out. Each switch is off until you turn it on.",
  'Printify': "Let apps read your Printify account, make drafts, publish and update products. Publishing and live changes wait for your approval, unless an app is trusted under Approvals.",
  'Approvals': "Where your approval comes from. By default every publish and live change waits for you in the approvals bar.",
  'Threads': "Let apps post to the Threads account you connect here. Every post is public.",
  'Pinterest': "Let apps ask to pin to the Pinterest account you connect here. Every pin and board waits for your yes in the approvals bar.",
  'Capabilities': 'Switch whole groups of tools on or off. A switched-off tool answers with a clear message instead of running.',
  'Sharing': 'What people with a view-only login can see, and the address they use.',
  'Control Panel': 'How this Control Panel behaves on your devices.',
  'Credentials': "Keys, tokens, passwords and SSH keys the platform uses, stored encrypted on HomeShed. Every Claude session uses them through the tools and never sees them. Only you can show a value, with your password, for 30 seconds.",
  'Known bugs': "Claude checks known fixes before debugging, so the same problem is never solved twice. Pro members also get the shared feed of fixes.",
  'APIs you can use': "Every API HomeShed knows how to use: what it costs, and whether it runs on your own machines. What's set up on this install shows on the Overview. Add a key under Keys and passwords.",
  'Connections': 'The services this Control Panel talks to, and whether each one is answering.',
  'Storage on your PC': "Where your PC keeps the platform's own files.",
  'Pro membership': "Pro adds every curated rule pack, their updates and the known-bugs feed. Connect with a key made on the Pro website: HomeShed never asks for your Pro password.",
};
const setSlug = g => 'set-' + g.toLowerCase().replace(/[^a-z0-9]+/g, '-');
// Six sections instead of fourteen small categories (the owner, 2026-09-29: "the settings layout for more efficiency",
// "user friendly is key, combine fields if needed ... organised and friendly"). Each group keeps its own card and
// intro inside its section; a group no section names stands alone at the end.
// Seven sections (R&D batch 5, settings-page-review.md): "Claude plan usage" and "To-do list" used to stand alone as
// loose tabs, and Credentials made "Access and sharing" 2,700 px tall, so keys get their own section.
// Shops and images left "AI and tools" (the owner, 2026-10-09: "i seem to be scrolling for ages on some sections. the
// shop settings etc needs its own category"): Online shops is its own section, one card per service, and Image
// generation shows one row per provider with its cap and models folded under it. R&D (settings-page-review.md, ad5623a):
// "Shops and social" (a beginner looks for Threads with the shop), help text clamped to one line with More, Capabilities
// folded to one row, Sharing one card, and 24 px switches.
const SET_SECTIONS = [
  ['Claude', ['Claude context', 'Claude plan usage', 'Rules and monitoring']],
  ['Agents', ['To-do list']],
  ['AI and tools', ['AI models', 'Language models', 'Capabilities', 'Known bugs', 'APIs you can use']],
  ['Shops and social', ['Etsy', 'Printify', 'Approvals', 'Threads', 'Pinterest', 'Online shops']],
  ['Images and video', ['Image generation']],
  ['Keys and passwords', ['Credentials']],
  ['Alerts', ['Notifications', 'Proxmox alarms']],
  ['Account and sharing', ['Pro membership', 'Sharing', 'API access']],
  ['System', ['Control Panel', 'Connections', 'Storage on your PC']],
];
const SET_LABEL = { 'Credentials': 'Keys and passwords' };  // shown names
// Numbers that fine-tune a choice go under "Advanced", so the choices come first; so do the shops' vault entry names.
const _vaultName = it => /_name$/.test(it.key || '');
const SET_ADVANCED = { 'Rules and monitoring': it => it.type === 'int', 'Etsy': _vaultName, 'Printify': _vaultName,
  'Threads': _vaultName, 'Instagram': _vaultName, 'Pinterest': _vaultName, 'Online shops': _vaultName };
// The tool server sends one "Online shops" group; the page splits it into a card per service (first match wins).
const SHOP_CARDS = [['Approvals', k => k.startsWith('approvals_')], ['Printify', k => k.includes('printify')],
  ['Threads', k => k.includes('threads')], ['Instagram', k => k.includes('instagram')],
  ['Pinterest', k => k.includes('pinterest')], ['Etsy', k => k.includes('etsy')]];
function splitGroup(groups, from, cards) {
  const items = groups[from];
  if (!items) return;
  delete groups[from];
  items.forEach(it => {
    const card = cards.find(([, test]) => test(it.key || ''));
    const g = card ? card[0] : from;
    (groups[g] = groups[g] || []).push({ ...it, group: g });
  });
}
// Image generation: each provider's switch, with its daily cap and model switches folded under it (open while it's
// on), instead of 28 rows in a column.
function imageProviderRows(items) {
  const provs = items.map(it => (/^image_provider_(.+)_enabled$/.exec(it.key || '') || [])[1]).filter(Boolean)
    .sort((a, b) => b.length - a.length);
  const of = it => provs.find(p => (it.key || '').startsWith(`image_daily_cap_${p}`) || (it.key || '').startsWith(`image_model_${p}_`));
  const loose = items.filter(it => !/^image_provider_.+_enabled$/.test(it.key || '') && !of(it));
  return loose.map(settingRow).join('') + items.filter(it => /^image_provider_.+_enabled$/.test(it.key || '')).map(sw => {
    const p = /^image_provider_(.+)_enabled$/.exec(sw.key)[1], more = items.filter(it => it !== sw && of(it) === p);
    const open = !!sw.value, fold = more.length ? `<button type="button" class="set-fold" aria-expanded="${open}"
      onclick="toggleFold(this)" title="This provider's daily cap and models">Cap and models (${more.length}) ${open ? '▾' : '▸'}</button>` : '';
    return settingRow(paidHelp(sw), fold) + (more.length ? `<div class="set-fold-body"${open ? '' : ' hidden'}>${more.map(settingRow).join('')}</div>` : '');
  }).join('');
}
function toggleFold(btn) {
  const body = btn.closest('.set-row').nextElementSibling, o = body.hidden;
  body.hidden = !o;
  btn.setAttribute('aria-expanded', o);
  btn.textContent = btn.textContent.replace(/[▸▾]$/, o ? '▾' : '▸');
}
// 3: every paid provider repeated "Also needs <KEY> under Keys and passwords, and a daily cap above 0. Off until you
// switch it on." The card's intro says it once; a row keeps its cost and its key's name.
function paidHelp(it) {
  const m = /^Paid per image \((.*)\)\. Also needs (\S+) under Keys and passwords, and a daily cap above 0\. Off until you switch it on\.$/.exec(it.help || '');
  return m ? { ...it, help: `Paid: ${m[1]}. Key: ${m[2]}.` } : it;
}
const SET_RENDER = { 'Image generation': imageProviderRows };
const SET_SECTION_OF = Object.fromEntries(SET_SECTIONS.flatMap(([s, gs]) => gs.map(g => [g, s])));
const sectionOf = g => SET_SECTION_OF[g] || g;
// Quiet hours are one row, "from 23:00 to 07:00", instead of two separate number boxes.
function joinQuietHours(items) {
  const from = items.find(it => it.key === 'voice_quiet_from'), to = items.find(it => it.key === 'voice_quiet_to');
  if (!from || !to) return items;
  const joined = { type: 'hours', source: from.source, label: 'Quiet hours', fromKey: from.key, toKey: to.key,
    from: from.value, to: to.value, help: "Ordinary spoken lines wait until quiet hours end; urgent ones still play." };
  return items.filter(it => it !== to).map(it => it === from ? joined : it);
}
function hoursRow(it) {
  const opts = v => Array.from({ length: 24 }, (_, h) =>
    `<option value="${h}" ${h === Number(v) ? 'selected' : ''}>${String(h).padStart(2, '0')}:00</option>`).join('');
  return `<div class="set-row"><div><div class="set-label">${escapeHtml(it.label)}</div><div class="hint">${escapeHtml(it.help)}</div>
      <span class="set-msg" id="setMsg-${escAttr(it.source)}-quiet-hours" role="status"></span></div>
    <div class="set-input"><select aria-label="Quiet from" onchange="saveHour('${escAttr(it.source)}', '${escAttr(it.fromKey)}', this)">${opts(it.from)}</select>
      <span class="hint">to</span>
      <select aria-label="Quiet until" onchange="saveHour('${escAttr(it.source)}', '${escAttr(it.toKey)}', this)">${opts(it.to)}</select></div></div>`;
}
async function saveHour(source, key, el) {
  const msg = document.getElementById(`setMsg-${source}-quiet-hours`);
  try { await apiCall('PUT', '/api/settings', { source, key, value: Number(el.value) }); msg.textContent = 'Saved'; msg.className = 'set-msg ok'; }
  catch (e) { msg.textContent = e.message; msg.className = 'set-msg bad'; }
}
async function loadSettings() {
  const box = document.getElementById('setForm');
  // Park the view-only logins card outside the form before it's rebuilt: it lives inside it (after
  // Sharing), and rebuilding would destroy it (found testing a second visit, 2026-09-28).
  document.getElementById('settingsView').appendChild(document.getElementById('viewersCard'));
  let d;
  try { d = await apiCall('GET', '/api/settings'); }
  catch (e) { box.innerHTML = `<div class="empty">${loadErr('settings', e)}</div>`; loadOwnerPassword(); loadViewers(); return; }
  const groups = {};
  (d.items || []).forEach(it => (groups[it.group] = groups[it.group] || []).push(it));
  (d.info || []).forEach(it => (groups[it.group] = groups[it.group] || []).push({ ...it, type: 'info' }));
  groups['Capabilities'] = groups['Capabilities'] || [];
  groups['Connections'] = groups['Connections'] || [];
  if (!_viewer) { groups['Credentials'] = []; groups['APIs you can use'] = []; groups['Pro membership'] = []; }  // owner only
  await _installedReady;  // a group for a part this setup doesn't have (no coding agent, no voice) isn't shown
  Object.keys(groups).forEach(g => { if (_installed[INSTALL_GROUPS[g]] === false) delete groups[g]; });
  if (groups['Voice']) groups['Voice'] = joinQuietHours(groups['Voice']);
  splitGroup(groups, 'Online shops', SHOP_CARDS);
  const inSections = SET_SECTIONS.flatMap(([, gs]) => gs);
  const names =[...inSections.filter(g => groups[g]), ...SET_ORDER.filter(g => groups[g] && !inSections.includes(g)),
    ...Object.keys(groups).filter(g => !inSections.includes(g) && !SET_ORDER.includes(g))];
  const sections = [...new Set(names.map(sectionOf))];
  document.getElementById('setCats').innerHTML = sections.map(s =>
    `<button class="ghost" data-group="${escAttr(s)}" onclick="document.getElementById('setSearch').value = ''; showSetCat(this.dataset.group)">${escapeHtml(s)}</button>`).join('');
  document.getElementById('setCatSel').innerHTML = sections.map(s => `<option value="${escAttr(s)}">${escapeHtml(s)}</option>`).join('');
  const down = Object.values(d.errors || {}).map(m => `<div class="empty">${escapeHtml(m)}, so its settings aren't shown until it's back.</div>`).join('');
  const rowsOf = g => {
    if (SET_RENDER[g]) return SET_RENDER[g](groups[g]);
    const adv = SET_ADVANCED[g], items = groups[g];
    const main = adv ? items.filter(it => !adv(it)) : items, extra = adv ? items.filter(adv) : [];
    return main.map(settingRow).join('')
      + (extra.length ? `<details class="set-adv"><summary>Advanced (${extra.length})</summary>${extra.map(settingRow).join('')}</details>` : '');
  };
  box.innerHTML = down + names.map(g => `<section class="card sav-card" id="${setSlug(g)}" data-group="${escAttr(g)}"><div class="panel-title">${escapeHtml(SET_LABEL[g] || g)}</div>
      ${SET_INTRO[g] ? `<p class="hint">${escapeHtml(SET_INTRO[g])}</p>` : ''}
      <div id="${setSlug(g)}-rows">${rowsOf(g)}</div></section>`).join('');
  const sharing = document.getElementById('set-sharing');  // the view-only logins belong with Sharing
  if (sharing) sharing.appendChild(document.getElementById('viewersCard'));  // one Sharing card (R&D)
  showSetCat(sections.includes(_setCat) ? _setCat : sections[0]);
  loadOwnerPassword();
  loadViewers();
  loadCapabilitySwitches();
  loadConnections();
  loadCredentials();
  loadProKey();
  loadPlanBadge();
  loadApiCatalog();
  loadEtsyConnect();
  loadThreadsConnect();
  loadInstagramConnect();
  loadPinterestConnect();
}
let _setCat = null;

// Connect Etsy (the owner, 2026-10-06: "add a Connect Etsy button ... and it can be public use too"): Etsy's own sign-in
// (PKCE). Etsy only sends you back to an https address registered in your Etsy app. On https, that's this panel's
// /api/etsy/callback; otherwise any https address you registered, and you paste the address Etsy sends you to.
async function loadEtsyConnect() {
  const rows = document.getElementById('set-etsy-rows');
  if (!rows || _viewer) return;
  let box = document.getElementById('etsyConnect');
  if (!box) {  // above the folded vault names
    box = document.createElement('div'); box.id = 'etsyConnect'; box.className = 'etsy-connect';
    rows.insertBefore(box, rows.querySelector(':scope > details.set-adv'));
  }
  let d;
  try { d = await apiCall('GET', '/api/etsy'); }
  catch (e) { box.innerHTML = `<p class="hint">Connect Etsy: ${escapeHtml(e.message)}</p>`; return; }
  const own = location.protocol === 'https:' ? `${location.origin}/api/etsy/callback` : '';
  let saved = ''; try { saved = localStorage.getItem('cp-etsy-return') || ''; } catch (e) { /* not kept */ }
  const missing = ['keystring', 'shared_secret'].filter(k => !d.have[k]).map(k => d.names[k]);
  box.classList.toggle('on', !!d.connected);
  box.innerHTML = `<div class="panel-title" style="margin-top:14px">Connect Etsy ${d.connected ? '<span class="etsy-on">● Connected</span>' : ''}</div>
    ${missing.length ? `<p class="hint">First save your Etsy app's keys under Credentials: ${missing.map(escapeHtml).join(' and ')} (Etsy's "Your Apps" page shows them).</p>` : `
    <p class="hint">${d.reconnect_for_edits ? '<b>Connect again once</b> to allow listing edits: a listing edit switch is on, and this connection was made read-only. ' : ''}${d.connected ? 'Connected. Connect again to switch shop or renew (Etsy connections last 90 days).' : 'Sign in on Etsy once; HomeShed keeps the connection and your shop number.'} Asks Etsy for: ${escapeHtml(d.scopes)}${(d.scopes || '').includes('listings_w') ? ' (listings_w: listing edits, for the switches you turned on)' : ' (read-only)'}.</p>
    <ol class="hint etsy-steps">
      <li>In Etsy's "Your Apps" page, add this return address to your app (exactly):
        <div class="msg-cmd"><code id="etsyReturnShow">${escapeHtml(own || saved || 'type an https address below')}</code><button type="button" class="ghost" onclick="navigator.clipboard.writeText(document.getElementById('etsyReturn').value)">Copy</button></div>
        ${own ? '' : '<span>This panel isn\'t on https, so Etsy can\'t come back to it. Use any https address you own (it can be a page that doesn\'t exist); you\'ll copy the address Etsy sends you to and paste it below.</span>'}
        <input id="etsyReturn" type="url" value="${escAttr(own || saved)}" placeholder="https://…" aria-label="Return address"
          oninput="document.getElementById('etsyReturnShow').textContent = this.value; try { localStorage.setItem('cp-etsy-return', this.value); } catch (e) {}"${own ? ' hidden' : ''}></li>
      <li><button type="button" onclick="startEtsyConnect(this)">${d.connected ? 'Connect again' : 'Connect Etsy'}</button> opens Etsy's sign-in in a new tab.</li>
      ${own ? '' : `<li>After you allow it, paste the address Etsy sent you to:
        <span style="display:flex;gap:6px;margin-top:4px"><input id="etsyPasted" type="url" placeholder="https://…?code=…&state=…" aria-label="Address Etsy sent you to" style="flex:1">
        <button type="button" onclick="finishEtsyConnect(this)">Finish</button></span></li>`}
    </ol>`}
    <p class="hint" id="etsyNote"></p>`;
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  if (q.get('etsy')) {
    document.getElementById('etsyNote').textContent = q.get('etsy') === 'ok'
      ? `Etsy connected${q.get('shop') ? `: ${q.get('shop')}` : ''}.` : `Etsy didn't connect: ${q.get('why') || 'unknown reason'}`;
    // Back from Etsy: open the shops and show the result (the owner, 2026-10-09: "it opens my homeshed page?").
    showSetCat('Shops and social');
    box.scrollIntoView({ block: 'center' });
  }
}
// Connect Threads (the owner, 2026-10-06: "i need a connect app like the etsy one ... add a [posting] agent
// "). Same steps as Etsy: Meta also only returns to an https address registered in the app.
async function loadThreadsConnect() {
  const rows = document.getElementById('set-threads-rows');
  if (!rows || _viewer) return;
  let box = document.getElementById('threadsConnect');
  if (!box) {  // above the folded vault names
    box = document.createElement('div'); box.id = 'threadsConnect'; box.className = 'etsy-connect';
    rows.insertBefore(box, rows.querySelector(':scope > details.set-adv'));
  }
  let d;
  try { d = await apiCall('GET', '/api/threads'); }
  catch (e) { box.innerHTML = `<p class="hint">Connect Threads: ${escapeHtml(e.message)}</p>`; return; }
  const own = location.protocol === 'https:' ? `${location.origin}/api/threads/callback` : '';
  let saved = ''; try { saved = localStorage.getItem('cp-threads-return') || ''; } catch (e) { /* not kept */ }
  const missing = ['app_id', 'app_secret'].filter(k => !d.have[k]).map(k => d.names[k]);
  box.classList.toggle('on', !!d.connected);
  box.innerHTML = `<div class="panel-title" style="margin-top:14px">Connect Threads ${d.connected ? `<span class="etsy-on">● Connected${d.account ? ' as @' + escapeHtml(d.account) : ''}</span>` : ''}</div>
    ${missing.length ? `<p class="hint">First save your Threads app's ID and secret under Credentials: ${missing.map(escapeHtml).join(' and ')} (Meta's developer page for your app, Use cases > Access the Threads API > Settings).</p>` : `
    <p class="hint">${d.connected ? (d.enabled ? 'Posting is on.' : 'Connected. Posting stays off until you switch on "Let apps post to Threads" above.') + ' The connection renews itself.' : 'Sign in on Threads once; HomeShed keeps the connection (60 days, renewed automatically). Posts: ' + escapeHtml(d.scopes) + '.'}</p>
    <ol class="hint etsy-steps">
      <li>In your Meta app's Threads settings, add this as a Redirect Callback URL (exactly):
        <div class="msg-cmd"><code id="threadsReturnShow">${escapeHtml(own || saved || 'type an https address below')}</code><button type="button" class="ghost" onclick="navigator.clipboard.writeText(document.getElementById('threadsReturn').value)">Copy</button></div>
        ${own ? '' : '<span>This panel isn\'t on https, so Threads can\'t come back to it. Use any https address you own; you\'ll paste the address Threads sends you to below.</span>'}
        <input id="threadsReturn" type="url" value="${escAttr(own || saved)}" placeholder="https://…" aria-label="Return address"
          oninput="document.getElementById('threadsReturnShow').textContent = this.value; try { localStorage.setItem('cp-threads-return', this.value); } catch (e) {}"${own ? ' hidden' : ''}></li>
      <li><button type="button" onclick="startThreadsConnect(this)">${d.connected ? 'Connect again' : 'Connect Threads'}</button> opens Threads' sign-in in a new tab.</li>
      ${own ? '' : `<li>After you allow it, paste the address Threads sent you to:
        <span style="display:flex;gap:6px;margin-top:4px"><input id="threadsPasted" type="url" placeholder="https://…?code=…&state=…" aria-label="Address Threads sent you to" style="flex:1">
        <button type="button" onclick="finishThreadsConnect(this)">Finish</button></span></li>`}
    </ol>`}
    <p class="hint" id="threadsNote"></p>`;
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  if (q.get('threads')) {
    document.getElementById('threadsNote').textContent = q.get('threads') === 'ok'
      ? `Threads connected${q.get('account') ? ` as @${q.get('account')}` : ''}.` : `Threads didn't connect: ${q.get('why') || 'unknown reason'}`;
    showSetCat('Shops and social');  // back from Threads: show the result, as for Etsy
    box.scrollIntoView({ block: 'center' });
  }
}
async function startThreadsConnect(btn) {
  const note = document.getElementById('threadsNote'), ret = document.getElementById('threadsReturn').value.trim();
  btn.disabled = true;
  try {
    const d = await apiCall('POST', '/api/threads/connect', { redirect_uri: ret });
    window.open(d.url, '_blank', 'noopener');
    note.textContent = 'Threads opened in a new tab. Allow access there (within 10 minutes).';
  } catch (e) { note.textContent = e.message; }
  btn.disabled = false;
}
async function finishThreadsConnect(btn) {
  const note = document.getElementById('threadsNote'), url = document.getElementById('threadsPasted').value.trim();
  if (!url) { document.getElementById('threadsPasted').focus(); return; }
  btn.disabled = true;
  try {
    const d = await apiCall('POST', '/api/threads/finish', { url });
    const msg = `Threads connected${d.username ? ` as @${d.username}` : ''}.`;
    await loadThreadsConnect();
    document.getElementById('threadsNote').textContent = msg;
    loadCredentials();
  } catch (e) { note.textContent = e.message; btn.disabled = false; }
}
// Connect Instagram (the owner, 2026-10-20): the Threads card's steps, for Meta's Instagram API with Instagram Login.
async function loadInstagramConnect() {
  const rows = document.getElementById('set-instagram-rows');
  if (!rows || _viewer) return;
  let box = document.getElementById('instagramConnect');
  if (!box) {
    box = document.createElement('div'); box.id = 'instagramConnect'; box.className = 'etsy-connect';
    rows.insertBefore(box, rows.querySelector(':scope > details.set-adv'));
  }
  let d;
  try { d = await apiCall('GET', '/api/instagram'); }
  catch (e) { box.innerHTML = `<p class="hint">Connect Instagram: ${escapeHtml(e.message)}</p>`; return; }
  const own = location.protocol === 'https:' ? `${location.origin}/api/instagram/callback` : '';
  let saved = ''; try { saved = localStorage.getItem('cp-instagram-return') || ''; } catch (e) { /* not kept */ }
  const missing = ['app_id', 'app_secret'].filter(k => !d.have[k]).map(k => d.names[k]);
  box.classList.toggle('on', !!d.connected);
  box.innerHTML = `<div class="panel-title" style="margin-top:14px">Connect Instagram ${d.connected ? `<span class="etsy-on">● Connected${d.account ? ' as @' + escapeHtml(d.account) : ''}</span>` : ''}</div>
    ${missing.length ? `<p class="hint">First save your Instagram app's ID and secret under Credentials: ${missing.map(escapeHtml).join(' and ')} (Meta's developer page for your app, Instagram > API setup with Instagram login: the Instagram app ID and secret, not the Facebook app ones). The account must be a Business or Creator account.</p>` : `
    <p class="hint">${d.connected ? (d.enabled ? 'Posting is on.' : 'Connected. Posting stays off until you switch on "Let apps post to Instagram" above.') + ' The connection renews itself.' : 'Sign in on Instagram once with your Business or Creator account; HomeShed keeps the connection (60 days, renewed automatically). Asks for: ' + escapeHtml(d.scopes) + '.'}</p>
    <ol class="hint etsy-steps">
      <li>In your Meta app, Instagram > API setup with Instagram login > Business login settings, add this as an OAuth redirect URI (exactly):
        <div class="msg-cmd"><code id="instagramReturnShow">${escapeHtml(own || saved || 'type an https address below')}</code><button type="button" class="ghost" onclick="navigator.clipboard.writeText(document.getElementById('instagramReturn').value)">Copy</button></div>
        ${own ? '' : '<span>This panel isn\'t on https, so Instagram can\'t come back to it. Use any https address you own; you\'ll paste the address Instagram sends you to below.</span>'}
        <input id="instagramReturn" type="url" value="${escAttr(own || saved)}" placeholder="https://…" aria-label="Return address"
          oninput="document.getElementById('instagramReturnShow').textContent = this.value; try { localStorage.setItem('cp-instagram-return', this.value); } catch (e) {}"${own ? ' hidden' : ''}></li>
      <li><button type="button" onclick="startInstagramConnect(this)">${d.connected ? 'Connect again' : 'Connect Instagram'}</button> opens Instagram's sign-in in a new tab.</li>
      ${own ? '' : `<li>After you allow it, paste the address Instagram sent you to:
        <span style="display:flex;gap:6px;margin-top:4px"><input id="instagramPasted" type="url" placeholder="https://…?code=…&state=…" aria-label="Address Instagram sent you to" style="flex:1">
        <button type="button" onclick="finishInstagramConnect(this)">Finish</button></span></li>`}
    </ol>`}
    <p class="hint" id="instagramNote"></p>`;
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  if (q.get('instagram')) {
    document.getElementById('instagramNote').textContent = q.get('instagram') === 'ok'
      ? `Instagram connected${q.get('account') ? ` as @${q.get('account')}` : ''}.` : `Instagram didn't connect: ${q.get('why') || 'unknown reason'}`;
    showSetCat('Shops and social');
    box.scrollIntoView({ block: 'center' });
  }
}
async function startInstagramConnect(btn) {
  const note = document.getElementById('instagramNote'), ret = document.getElementById('instagramReturn').value.trim();
  btn.disabled = true;
  try {
    const d = await apiCall('POST', '/api/instagram/connect', { redirect_uri: ret });
    window.open(d.url, '_blank', 'noopener');
    note.textContent = 'Instagram opened in a new tab. Allow access there (within 10 minutes).';
  } catch (e) { note.textContent = e.message; }
  btn.disabled = false;
}
async function finishInstagramConnect(btn) {
  const note = document.getElementById('instagramNote'), url = document.getElementById('instagramPasted').value.trim();
  if (!url) { document.getElementById('instagramPasted').focus(); return; }
  btn.disabled = true;
  try {
    const d = await apiCall('POST', '/api/instagram/finish', { url });
    const msg = `Instagram connected${d.username ? ` as @${d.username}` : ''}.`;
    await loadInstagramConnect();
    document.getElementById('instagramNote').textContent = msg;
    loadCredentials();
  } catch (e) { note.textContent = e.message; btn.disabled = false; }
}
// Connect Pinterest (the owner's decision, 2026-10-10): the Threads card's steps. Trial access (sandbox) and Standard
// access each have their own sign-in, so the card says which one a Connect makes.
async function loadPinterestConnect() {
  const rows = document.getElementById('set-pinterest-rows');
  if (!rows || _viewer) return;
  let box = document.getElementById('pinterestConnect');
  if (!box) {
    box = document.createElement('div'); box.id = 'pinterestConnect'; box.className = 'etsy-connect';
    rows.insertBefore(box, rows.querySelector(':scope > details.set-adv'));
  }
  let d;
  try { d = await apiCall('GET', '/api/pinterest'); }
  catch (e) { box.innerHTML = `<p class="hint">Connect Pinterest: ${escapeHtml(e.message)}</p>`; return; }
  const own = location.protocol === 'https:' ? `${location.origin}/api/pinterest/callback` : '';
  let saved = ''; try { saved = localStorage.getItem('cp-pinterest-return') || ''; } catch (e) { /* not kept */ }
  const missing = ['app_id', 'app_secret'].filter(k => !d.have[k]).map(k => d.names[k]);
  const env = d.sandbox ? 'Trial access (sandbox: only you see the pins)' : 'Standard access (pins are public)';
  box.classList.toggle('on', !!d.connected);
  box.innerHTML = `<div class="panel-title" style="margin-top:14px">Connect Pinterest ${d.connected ? '<span class="etsy-on">● Connected</span>' : ''}</div>
    ${missing.length ? `<p class="hint">First make a free Pinterest business account and an app at developers.pinterest.com (My apps), then save the app's ID and secret key under Credentials: ${missing.map(escapeHtml).join(' and ')}.</p>` : `
    <p class="hint">Now: ${escapeHtml(env)}. ${d.connected ? (d.enabled ? 'Apps can ask to pin; each pin waits for your yes.' : 'Connected. Pinning stays off until you switch on "Let apps ask to pin to Pinterest" above.') + ' The connection renews itself.' : (d.connected_other_env ? 'Connected for the other kind of access only: press Connect Pinterest for this one.' : 'Sign in on Pinterest once; HomeShed keeps the connection.')}</p>
    <ol class="hint etsy-steps">
      <li>In your Pinterest app's settings, add this as a redirect URI (exactly):
        <div class="msg-cmd"><code id="pinterestReturnShow">${escapeHtml(own || saved || 'type an https address below')}</code><button type="button" class="ghost" onclick="navigator.clipboard.writeText(document.getElementById('pinterestReturn').value)">Copy</button></div>
        ${own ? '' : '<span>This panel isn\'t on https, so Pinterest can\'t come back to it. Use any https address you own; you\'ll paste the address Pinterest sends you to below.</span>'}
        <input id="pinterestReturn" type="url" value="${escAttr(own || saved)}" placeholder="https://…" aria-label="Return address"
          oninput="document.getElementById('pinterestReturnShow').textContent = this.value; try { localStorage.setItem('cp-pinterest-return', this.value); } catch (e) {}"${own ? ' hidden' : ''}></li>
      <li><button type="button" onclick="startPinterestConnect(this)">${d.connected ? 'Connect again' : 'Connect Pinterest'}</button> opens Pinterest's sign-in in a new tab.</li>
      ${own ? '' : `<li>After you allow it, paste the address Pinterest sent you to:
        <span style="display:flex;gap:6px;margin-top:4px"><input id="pinterestPasted" type="url" placeholder="https://…?code=…&state=…" aria-label="Address Pinterest sent you to" style="flex:1">
        <button type="button" onclick="finishPinterestConnect(this)">Finish</button></span></li>`}
    </ol>`}
    <p class="hint" id="pinterestNote"></p>`;
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  if (q.get('pinterest')) {
    document.getElementById('pinterestNote').textContent = q.get('pinterest') === 'ok'
      ? `Pinterest connected${q.get('account') ? ` as ${q.get('account')}` : ''}.` : `Pinterest didn't connect: ${q.get('why') || 'unknown reason'}`;
    showSetCat('Shops and social');
    box.scrollIntoView({ block: 'center' });
  }
}
async function startPinterestConnect(btn) {
  const note = document.getElementById('pinterestNote'), ret = document.getElementById('pinterestReturn').value.trim();
  btn.disabled = true;
  try {
    const d = await apiCall('POST', '/api/pinterest/connect', { redirect_uri: ret });
    window.open(d.url, '_blank', 'noopener');
    note.textContent = 'Pinterest opened in a new tab. Allow access there (within 10 minutes).';
  } catch (e) { note.textContent = e.message; }
  btn.disabled = false;
}
async function finishPinterestConnect(btn) {
  const note = document.getElementById('pinterestNote'), url = document.getElementById('pinterestPasted').value.trim();
  if (!url) { document.getElementById('pinterestPasted').focus(); return; }
  btn.disabled = true;
  try {
    const d = await apiCall('POST', '/api/pinterest/finish', { url });
    const msg = `Pinterest connected${d.username ? ` as ${d.username}` : ''}.`;
    await loadPinterestConnect();
    document.getElementById('pinterestNote').textContent = msg;
    loadCredentials();
  } catch (e) { note.textContent = e.message; btn.disabled = false; }
}
async function startEtsyConnect(btn) {
  const note = document.getElementById('etsyNote'), ret = document.getElementById('etsyReturn').value.trim();
  btn.disabled = true;
  try {
    const d = await apiCall('POST', '/api/etsy/connect', { redirect_uri: ret });
    window.open(d.url, '_blank', 'noopener');
    note.textContent = 'Etsy opened in a new tab. Allow access there (within 10 minutes).';
  } catch (e) { note.textContent = e.message; }
  btn.disabled = false;
}
async function finishEtsyConnect(btn) {
  const note = document.getElementById('etsyNote'), url = document.getElementById('etsyPasted').value.trim();
  if (!url) { document.getElementById('etsyPasted').focus(); return; }
  btn.disabled = true;
  try {
    const d = await apiCall('POST', '/api/etsy/finish', { url });
    note.textContent = `Etsy connected${d.shop_name ? `: ${d.shop_name}` : ''}.${d.note ? ' ' + d.note : ''}`;
    loadEtsyConnect().then(() => { document.getElementById('etsyNote').textContent = note.textContent; });
    loadCredentials();
  } catch (e) { note.textContent = e.message; btn.disabled = false; }
}

// --- Credentials (the owner, 2026-09-29: "Any ssh, any credentials to access anything, should be available in the control
// panel for owners only. So all Claude instances has access to the same"). Values live encrypted on HomeShed;
// every Claude session uses them through the tools and never sees them. Only the owner can show one, with the
// password, for 30 seconds. First draft by the local model (qwen3-coder-30b), rewritten to this page's helpers: no
// alert(), one form at a time, and a value only ever set through textContent.
const CRED_KINDS = ['api-key', 'token', 'password', 'ssh-key', 'login', 'other'];
// Free cloud AI (mnfst/awesome-free-llm-apis, 2026-09-29). Each is dormant on HomeShed until its key is saved.
const AI_PROVIDERS = [
  { key: 'NVIDIA_API_KEY', label: 'NVIDIA', url: 'https://integrate.api.nvidia.com/v1', signup: 'build.nvidia.com' },
  { key: 'GROQ_API_KEY', label: 'Groq', url: 'https://api.groq.com/openai/v1', signup: 'console.groq.com' },
  { key: 'OPENROUTER_API_KEY', label: 'OpenRouter (free models)', url: 'https://openrouter.ai/api/v1', signup: 'openrouter.ai' },
  { key: 'GEMINI_API_KEY', label: 'Google Gemini', url: 'https://generativelanguage.googleapis.com/v1beta/openai', signup: 'aistudio.google.com' },
  { key: 'MISTRAL_API_KEY', label: 'Mistral', url: 'https://api.mistral.ai/v1', signup: 'console.mistral.ai' },
  { key: 'OVH_AI_ENDPOINTS_TOKEN', label: 'OVHcloud AI Endpoints', url: 'https://oai.endpoints.kepler.ai.cloud.ovh.net/v1', signup: 'endpoints.ai.cloud.ovh.net' },
];
const _aiProvider = key => AI_PROVIDERS.find(p => p.key === key) || AI_PROVIDERS[0];
const _ago = t => { const d = Math.floor((Date.now() / 1000 - (t || 0)) / 86400); return d < 1 ? 'today' : d === 1 ? 'yesterday' : `${d} days ago`; };
async function loadCredentials() {
  const box = document.getElementById('set-credentials-rows');
  if (!box || _viewer) return;
  box.innerHTML = '<div class="loading">Loading…</div>';
  let d;
  try { d = await apiCall('GET', '/api/vault'); }
  catch (e) { box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; return; }
  if (!d.enabled) {
    box.innerHTML = '<div class="empty">The credentials store is off: HomeShed has no VAULT_KEY yet. Redeploying HomeShed creates one.</div>';
    return;
  }
  _cred.list = d.credentials || [];
  _cred.reveal = !!d.reveal;
  _cred.audit = d.audit || [];
  // The bar is drawn once, so typing in its search box never loses focus; only the rows below it are redrawn.
  const groups = [...new Set(_cred.list.map(credGroup))].sort((a, b) => (CRED_GROUP_ORDER.indexOf(a) + 1 || 99) - (CRED_GROUP_ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b));
  if (_cred.group && !groups.includes(_cred.group)) _cred.group = '';
  box.innerHTML = `<div class="wa-cred">
    <div class="ui-toolbar" role="search" aria-label="Find a key">
      <input type="search" placeholder="Find a key…" aria-label="Find a key" value="${escAttr(_cred.q)}" oninput="_cred.q = this.value; renderCredList()" autocomplete="off">
      <select aria-label="Group" onchange="_cred.group = this.value; renderCredList()"><option value="">Every group</option>${groups.map(g => `<option value="${escAttr(g)}"${g === _cred.group ? ' selected' : ''}>${escapeHtml(g)}</option>`).join('')}</select>
      <select aria-label="Kind" onchange="_cred.kind = this.value; renderCredList()"><option value="">Every kind</option>${CRED_KINDS.map(k => `<option value="${k}"${k === _cred.kind ? ' selected' : ''}>${CRED_KIND_LABEL[k]}</option>`).join('')}</select>
      <span class="ui-count" id="credCount" aria-live="polite"></span>
      <button type="button" class="ui-primary" onclick="openCredForm('custom')">Add a key</button>
      <button type="button" class="ghost" onclick="openCredForm('ai')">Connect cloud AI</button></div>
    <div class="ui-table-wrap"><table class="ui-table cred-table">
      <thead><tr><th scope="col">Key</th><th scope="col">Kind</th><th scope="col" class="ui-opt2">Group</th><th scope="col" class="ui-opt">Where</th>
        <th scope="col" class="ui-opt3">Used by</th><th scope="col">Changed</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody id="credList"></tbody></table></div>
    <section class="wa-panel" aria-labelledby="credLogH"><div class="wa-feed-head"><h3 id="credLogH">Change log</h3><span class="count">who changed or showed what; never a value</span>
      <select aria-label="Which changes" onchange="_cred.log = this.value; renderCredLog()"><option value="all">Everything</option><option value="change">Added or changed</option><option value="reveal">Shown</option><option value="delete">Deleted</option><option value="other">Other</option></select></div>
      <div class="wa-feed" id="credLog"></div></section></div>`;
  renderCredList();
  renderCredLog();
}
// --- Keys and passwords (R&D batch 5, credentials-page-design.md): grouped list, a drawer form by kind, checks that
// catch the two real mistakes of 2026-09-30 (a trailing word after a key; an SSH fingerprint saved as the key), and
// "Edit details" without re-pasting the secret (PUT without "value" changes details only, server.py vault_set).
const CRED_KIND_LABEL = { 'api-key': 'API key', token: 'Token', password: 'Password', 'ssh-key': 'SSH key', login: 'Login', other: 'Other' };  // secret-scan: allow (display labels)
const CRED_NAME_RE = /^[A-Z][A-Z0-9_]{1,63}$/;
const CRED_GROUP_ORDER = ['Cloud AI', 'Servers and SSH', 'Made by HomeShed', 'Other'];
const CRED_FIELDS = { host: 'api-key token password login ssh-key', username: 'password login ssh-key' };  // which kinds show which boxes
let _cred = { list: [], audit: [], reveal: false, q: '', group: '', kind: '', log: 'all', mode: '', name: '', nameTouched: false, sure: false, fixes: [] };
function credGroup(c) {  // the stored "service" is the group; a key without one is sorted by what it is
  const s = String(c.service || '').trim();
  if (s) return s.toLowerCase() === 'ai' ? 'Cloud AI' : s;
  if (AI_PROVIDERS.some(p => p.key === c.name)) return 'Cloud AI';
  if (c.kind === 'ssh-key' || c.kind === 'login' || c.host) return 'Servers and SSH';
  if (c.generated || /^(MCP_|TDAI_|HLC_|VAULT_)/.test(c.name)) return 'Made by HomeShed';
  return 'Other';
}
const credService = g => { const t = String(g || '').trim().slice(0, 40); return t === 'Cloud AI' ? 'ai' : t; };
function renderCredList() {
  const box = document.getElementById('credList');
  if (!box) return;
  const q = _cred.q.trim().toLowerCase();
  const hit = c => (!_cred.group || credGroup(c) === _cred.group) && (!_cred.kind || c.kind === _cred.kind)
    && (!q || [c.name, c.label, c.host, c.username, c.note, CRED_KIND_LABEL[c.kind], credGroup(c)].some(x => String(x || '').toLowerCase().includes(q)));
  const order = g => (CRED_GROUP_ORDER.indexOf(g) + 1) || 99;
  const shown = _cred.list.filter(hit).sort((a, b) => order(credGroup(a)) - order(credGroup(b)) || credGroup(a).localeCompare(credGroup(b))
    || String(a.label || a.name).localeCompare(String(b.label || b.name)));
  const n = _cred.list.length, filtered = q || _cred.group || _cred.kind;
  document.getElementById('credCount').textContent = filtered ? `${shown.length} of ${n}` : `${n} ${n === 1 ? 'key' : 'keys'}`;
  const empty = html => `<tr class="ui-empty-row"><td colspan="7"><div class="ui-empty">${html}</div></td></tr>`;
  if (!n) {
    box.innerHTML = empty(`<b>Nothing stored yet.</b><span><b>Add a key</b> stores a key, token, password or SSH key for your tools.
      No graphics card? <b>Connect cloud AI</b> adds a free cloud model (NVIDIA, Groq, OpenRouter, Gemini, Mistral or OVHcloud).</span>`);
    return;
  }
  if (!shown.length) { box.innerHTML = empty(`<b>No key matches.</b><span>Try another word, group or kind.</span>`); return; }
  UI.rerender(box, shown.map(c => {
    const used = (c.used_by || []).length, where = c.host ? `${c.host}${c.port ? ':' + c.port : ''}` : '';
    return `<tr class="cred-tr" data-name="${escAttr(c.name)}">
      <td class="ui-name-cell"><button type="button" class="ui-rowlink" data-key="cred-${escAttr(c.name)}" onclick="openCredForm('edit', this.closest('tr').dataset.name)" title="Edit its details">${escapeHtml(c.label || c.name)}</button>
        ${c.label ? `<code>${escapeHtml(c.name)}</code>` : ''}</td>
      <td data-label="Kind"><span class="ui-chip plain">${escapeHtml(CRED_KIND_LABEL[c.kind] || c.kind || 'Other')}</span></td>
      <td data-label="Group" class="ui-opt2">${escapeHtml(credGroup(c))}</td>
      <td data-label="Where" class="ui-opt">${where ? `<code class="wa-code">${escapeHtml(where)}</code>` : ''}${c.username ? ` <span class="ui-sec">${escapeHtml(c.username)}</span>` : ''}${!where && !c.username ? '<span class="ui-dim">—</span>' : ''}</td>
      <td data-label="Used by" class="ui-opt3" title="${escAttr((c.used_by || []).join(', '))}">${used ? `${used} ${used === 1 ? 'tool' : 'tools'}` : '<span class="ui-dim">—</span>'}</td>
      <td data-label="Changed" class="ui-nowrap">${escapeHtml(_ago(c.updated))}</td>
      <td class="ui-c-act"><button type="button" class="ui-kebab" aria-haspopup="menu" aria-expanded="false" data-key="cred-k-${escAttr(c.name)}"
        aria-label="Actions for ${escAttr(c.label || c.name)}" onclick="credMenu(this)"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="3" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="8" cy="13" r="1.5"/></svg></button></td></tr>`;
  }).join(''));
}
function credMenu(btn) {
  const name = btn.closest('tr').dataset.name, c = _cred.list.find(x => x.name === name) || { name };
  UI.menu(btn, [...(_cred.reveal ? [{ label: 'Show the value…', run: () => WA.reveal(c) }] : []),
    { label: 'Replace the value', run: () => openCredForm('replace', name) }, { label: 'Edit details', run: () => openCredForm('edit', name) },
    '-', { label: 'Delete…', danger: true, run: () => deleteCred(name) }]);
}
// Who changed or showed what, newest first: the vault's own audit trail (mcp-server vault.py), never a value.
function renderCredLog() {
  const box = document.getElementById('credLog');
  if (!box) return;
  const ACT = { added: 'Added', replaced: 'Replaced the value of', updated: 'Changed the details of', deleted: 'Deleted', revealed: 'Showed', 'wrong reveal key': 'Refused a show of' };
  const rows = _cred.audit.filter(e => _cred.log === 'all' || WA.pure.vaultKind(e.action) === _cred.log).map(e => {
    const k = WA.pure.vaultKind(e.action);
    return `<div class="wa-ev ui-feed-row" tabindex="-1"><span class="wa-ev-dot ${k === 'delete' || k === 'other' ? 'bad' : k === 'reveal' ? 'stop' : 'good'}" aria-hidden="true"></span>
      <span class="wa-ev-when" title="${escAttr(new Date(e.t * 1000).toLocaleString())}">${escapeHtml(agoText(new Date(e.t * 1000).toISOString()))}</span>
      <span class="wa-ev-what">${escapeHtml(ACT[e.action] || e.action)} <code>${escapeHtml(e.name || '')}</code></span>
      <span class="wa-ev-side">${escapeHtml(e.by || '')}</span></div>`;
  });
  UI.feed(box, rows, { key: 'cred-log-' + _cred.log, limit: 6, empty: _cred.audit.length ? 'Nothing of that kind recently.' : 'No changes recorded yet.' });
}
// Show value: WA.reveal in pages-work-admin.js asks for the password in a dialog, shows the value there for 30 seconds,
// then clears it (the same /reveal route and check as before).
// The PEM header text is a placeholder and a pattern, not a key: kept on their own lines so the leak guard can tell.
const CRED_PEM_EXAMPLE = '-----BEGIN OPENSSH PRIVATE KEY-----';  // secret-scan: allow (a placeholder for the box, not a key)
const CRED_PEM_START = /^-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/;  // secret-scan: allow (a pattern, not a key)
function credDrawer() {  // the shared side drawer (a modal dialog: Esc closes it, focus stays inside)
  const w = WA.drawer('credDrawer', 'the key form');
  w.d.onclose = () => { w.body.innerHTML = ''; };  // nothing typed into a value box stays in the page
  return w;
}
function credSuggestName(label) {  // "HP Proxmox API token" -> HP_PROXMOX_API_TOKEN
  let n = String(label || '').toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 64);
  if (n && !/^[A-Z]/.test(n)) n = ('KEY_' + n).slice(0, 64);
  return n.length >= 2 ? n : '';
}
function credValueFields() {  // a one-line secret for most kinds, a big box for an SSH private key
  return `<label class="f" data-kinds="api-key token password login other"><span data-value-label>Value</span>
      <span class="cred-value"><input type="password" name="value" autocomplete="new-password" spellcheck="false" oninput="credValidate(this.form)">
        <button type="button" class="ghost" onclick="credPeek(this)" aria-label="Show or hide what you typed">Show</button></span></label>
    <label class="f" data-kinds="ssh-key">Private key <span class="hint">The whole file, from the BEGIN line to the end. Not the .pub file.</span>
      <textarea name="svalue" spellcheck="false" autocomplete="off" placeholder="${escAttr(CRED_PEM_EXAMPLE)}" oninput="credValidate(this.form)"></textarea></label>`;
}
function openCredForm(mode, name = '') {
  const d = credDrawer(), c = _cred.list.find(x => x.name === name) || {};
  _cred.mode = mode; _cred.name = name; _cred.nameTouched = !!name; _cred.sure = false;
  const kind = c.kind || 'api-key', title = { ai: 'Connect a cloud AI', custom: 'Add a key or password', replace: 'Replace the value',
    edit: 'Edit details' }[mode] || 'Key';
  const groups = [...new Set([...CRED_GROUP_ORDER, ..._cred.list.map(credGroup)])];
  const who = c.name ? `<b>${escapeHtml(c.label || c.name)}</b> <code>${escapeHtml(c.name)}</code>` : '';
  let body;
  if (mode === 'ai') {
    body = `<label class="f">Provider <select name="provider" onchange="this.form.querySelector('.ai-signup').textContent = _aiProvider(this.value).signup">
        ${AI_PROVIDERS.map(p => `<option value="${escAttr(p.key)}">${escapeHtml(p.label)}</option>`).join('')}</select></label>
      <p class="hint">Get a free key at <strong class="ai-signup">${escapeHtml(AI_PROVIDERS[0].signup)}</strong>, paste it here, press Test, then Save.
        It's stored encrypted in HomeShed: your AI sessions use it through the tools and never see it. What you send these services leaves your network.</p>
      <label class="f">API key <span class="cred-value"><input type="password" name="value" autocomplete="new-password" spellcheck="false" oninput="credValidate(this.form)">
        <button type="button" class="ghost" onclick="credPeek(this)" aria-label="Show or hide what you typed">Show</button></span></label>`;
  } else if (mode === 'replace') {
    body = `<div class="cred-keep">${who}<br>Only the value changes. The name, kind, group and details stay as they are.</div>${credValueFields()}`;
  } else {
    body = `${mode === 'edit' ? `<div class="cred-keep">${who}<br>The value stays as it is: use <b>Replace value</b> to change it.</div>` : ''}
      <label class="f">What is it? <input type="text" name="label" value="${escAttr(c.label || '')}" maxlength="80" placeholder="e.g. HP Proxmox API token" oninput="credLabelInput(this)"></label>
      <label class="f">Name <span class="hint">How tools ask for it: capitals, digits and _, starting with a letter.</span>
        <input type="text" name="name" class="mono" value="${escAttr(name)}" ${mode === 'edit' ? 'readonly' : ''} maxlength="64" spellcheck="false"
          placeholder="HP_PROXMOX_API_TOKEN" oninput="this.value = this.value.toUpperCase(); _cred.nameTouched = true; credValidate(this.form)"></label>
      <fieldset class="cred-kinds"><legend>Kind</legend>${CRED_KINDS.map(k => `<label><input type="radio" name="kind" value="${k}" ${k === kind ? 'checked' : ''}
        onchange="credKindChanged(this.form)"><span>${CRED_KIND_LABEL[k]}</span></label>`).join('')}</fieldset>
      <div class="cred-two" data-kinds="${CRED_FIELDS.host}">
        <label class="f"><span data-host-label>Host</span><input type="text" name="host" value="${escAttr(c.host || '')}" maxlength="255" spellcheck="false"
          placeholder="server.example.com or 203.0.113.10" oninput="credValidate(this.form)"></label>
        <label class="f">Port <input type="number" name="port" min="1" max="65535" value="${escAttr(c.port || '')}" oninput="credValidate(this.form)"></label></div>
      <label class="f" data-kinds="${CRED_FIELDS.username}">Username <input type="text" name="username" value="${escAttr(c.username || '')}" maxlength="120" autocomplete="off" spellcheck="false"></label>
      ${mode === 'edit' ? '' : credValueFields()}
      <label class="f">Group <input type="text" name="group" list="credGroups" value="${escAttr(c.name ? credGroup(c) : '')}" maxlength="40" placeholder="Leave empty to sort it automatically">
        <datalist id="credGroups">${groups.map(g => `<option value="${escAttr(g)}">`).join('')}</datalist></label>
      <label class="f">Note (optional) <input type="text" name="note" value="${escAttr(c.note || '')}" maxlength="400"></label>`;
  }
  d.title.textContent = title;
  d.sub.innerHTML = c.name ? `<span>Keys and passwords</span><code class="wa-code">${escapeHtml(c.name)}</code>` : '<span>Keys and passwords</span>';
  d.body.innerHTML = `<form class="cred-form" autocomplete="off" onsubmit="event.preventDefault(); saveCred(this)" data-kind="${escAttr(kind)}">${body}
      <div class="cred-checks" role="status" aria-live="polite"></div>
      <div class="cred-actions"><button type="submit" class="tdo-primary">Save</button>
        <button type="button" class="ghost" data-test onclick="testCred(this.form)">Test</button>
        <button type="button" class="ghost" onclick="closeCredForm()">Cancel</button><span class="set-msg" role="status"></span></div></form>`;
  if (!d.d.open) d.d.showModal();
  const form = d.body.querySelector('form');
  credKindChanged(form);
  (form.querySelector('input:not([readonly]):not([type=radio]), textarea, select') || form).focus();
}
function closeCredForm() { const d = document.getElementById('credDrawer'); if (d && d.open) d.close(); }
function credKind(form) {
  const r = form.querySelector('[name=kind]:checked');
  return _cred.mode === 'ai' ? 'api-key' : r ? r.value : form.dataset.kind || 'other';
}
function credKindChanged(form) {
  const kind = credKind(form);
  form.querySelectorAll('[data-kinds]').forEach(el => { el.hidden = !el.dataset.kinds.split(' ').includes(kind); });
  const hl = form.querySelector('[data-host-label]');
  if (hl) hl.textContent = kind === 'api-key' || kind === 'token' ? 'Address it works with (optional)' : 'Host';
  const vl = form.querySelector('[data-value-label]');
  if (vl) vl.textContent = kind === 'password' || kind === 'login' ? 'Password' : kind === 'other' ? 'Value' : CRED_KIND_LABEL[kind];
  const port = form.querySelector('[name=port]');
  if (port) port.placeholder = kind === 'ssh-key' ? '22' : '';
  credValidate(form);
}
function credLabelInput(el) {
  if (_cred.mode === 'custom' && !_cred.nameTouched) el.form.querySelector('[name=name]').value = credSuggestName(el.value);
  credValidate(el.form);
}
function credPeek(btn) {
  const input = btn.parentElement.querySelector('input');
  input.type = input.type === 'password' ? 'text' : 'password';
  btn.textContent = input.type === 'password' ? 'Show' : 'Hide';
}
function credChecks(kind, raw) {  // what's wrong with a pasted value, in plain words; "block" means it can't work
  const v = String(raw || ''), t = v.trim(), out = [];
  if (!t) return out;
  if (t !== v) out.push({ cls: 'info', text: 'Spaces or line breaks at the start or end are removed when you save.' });
  if (kind === 'ssh-key') {
    if (/^SHA256:/.test(t) || /^[A-Za-z0-9+/]{43}=?$/.test(t))
      out.push({ cls: 'bad', block: true, text: "This is a key fingerprint: it only names a key and can't log in. Paste the private key: the whole file, from its BEGIN line." });
    else if (/^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-\S+|sk-\S+) AAAA/.test(t))
      out.push({ cls: 'bad', block: true, text: 'This is the public key (the .pub file). Paste the private key instead: the same file name without .pub.' });
    else if (!CRED_PEM_START.test(t))
      out.push({ cls: 'warn', text: "A private key's first line normally reads BEGIN … PRIVATE KEY. Check you copied the whole file." });
    return out;
  }
  if ((kind === 'api-key' || kind === 'token') && /\s/.test(t)) {
    const first = t.split(/\s+/)[0], rest = t.slice(first.length).trim();
    out.push({ cls: 'warn', text: `There's a space, then “${rest.slice(0, 40)}${rest.length > 40 ? '…' : ''}”. Keys and tokens don't contain spaces: was something extra pasted?`,
      fix: first, fixText: 'Keep only the part before the space' });
  }
  if ((kind === 'api-key' || kind === 'token') && /^[^\s=@!]+@[^\s=!]+![^\s=]+$/.test(t))
    out.push({ cls: 'warn', text: 'This looks like a Proxmox token ID without its secret. Add an = sign and the secret after the ID.' });
  return out;
}
function credValue(form) {
  const el = form.querySelector(credKind(form) === 'ssh-key' ? '[name=svalue]' : '[name=value]');
  return el ? el.value : '';
}
function credValidate(form) {  // redraws the checks; returns {block: reason or '', value}
  const mode = _cred.mode, list = [], name = ((form.querySelector('[name=name]') || {}).value || '').trim();
  if (mode === 'custom' && name && !CRED_NAME_RE.test(name))
    list.push({ cls: 'bad', block: true, text: 'The name needs capitals, digits and _ only, starting with a letter (2 to 64 characters).' });
  if (mode === 'custom' && _cred.list.some(c => c.name === name))
    list.push({ cls: 'warn', text: `A key called ${name} already exists. Saving replaces its value and details.` });
  const port = (form.querySelector('[name=port]') || {}).value;
  if (port && !(Number(port) >= 1 && Number(port) <= 65535 && Number.isInteger(Number(port))))
    list.push({ cls: 'bad', block: true, text: 'The port is a whole number from 1 to 65535.' });
  if (mode !== 'edit') list.push(...credChecks(credKind(form), credValue(form)));
  _cred.fixes = [];
  const box = form.querySelector('.cred-checks');
  box.innerHTML = list.map(x => `<div class="${x.cls}">${escapeHtml(x.text)}${x.fix !== undefined
    ? `<button type="button" class="ghost" onclick="credFix(this, ${_cred.fixes.push(x.fix) - 1})">${escapeHtml(x.fixText)}</button>` : ''}</div>`).join('');
  const test = form.querySelector('[data-test]'), host = ((form.querySelector('[name=host]') || {}).value || '').trim();
  if (test) test.hidden = !(mode === 'ai' || (host && port));
  return { block: (list.find(x => x.block) || {}).text || '', value: credValue(form).trim() };
}
function credFix(btn, i) {
  const form = btn.closest('form'), el = form.querySelector(credKind(form) === 'ssh-key' ? '[name=svalue]' : '[name=value]');
  if (el) el.value = _cred.fixes[i];
  credValidate(form);
}
async function testCred(form) {
  const msg = form.querySelector('.set-msg');
  msg.className = 'set-msg';
  msg.textContent = 'Testing…';
  const body = _cred.mode === 'ai'
    ? { preset: 'openai', url: _aiProvider(form.querySelector('[name=provider]').value).url, key: credValue(form).trim() }
    : { preset: 'tcp', host: form.querySelector('[name=host]').value.trim(), port: Number(form.querySelector('[name=port]').value) };
  try { msg.textContent = (await apiCall('POST', '/api/vault-test', body)).detail; }
  catch (e) { msg.textContent = e.message; msg.className = 'set-msg bad'; }
}
async function saveCred(form) {
  const msg = form.querySelector('.set-msg'), mode = _cred.mode, v = credValidate(form);
  const say = (t, cls = 'bad') => { msg.textContent = t; msg.className = `set-msg ${cls}`; };
  if (v.block) { say(v.block); return; }
  const f = n => ((form.querySelector(`[name="${n}"]`) || {}).value || '').trim();
  let name = _cred.name, body;
  if (mode === 'ai') {
    const p = _aiProvider(f('provider'));
    name = p.key;
    body = { value: v.value, label: `${p.label} cloud AI`, kind: 'api-key', service: 'ai', used_by: ['local_ai.ask'] };
  } else if (mode === 'replace') {
    body = { value: v.value };
  } else {
    const kind = credKind(form), shows = field => CRED_FIELDS[field].split(' ').includes(kind);
    if (mode === 'custom') name = f('name');
    if (!name) { say('Give it a name: type what it is, and a name is suggested.'); return; }
    body = { label: f('label'), kind, service: credService(f('group')), note: f('note'),
      host: shows('host') ? f('host') : '', port: shows('host') && f('port') ? Number(f('port')) : '', username: shows('username') ? f('username') : '' };
    if (mode === 'custom') body.value = v.value;
  }
  if (mode !== 'edit' && !body.value) { say('Paste the value first.'); return; }
  if (mode === 'custom' && _cred.list.some(c => c.name === name) && !_cred.sure) {
    _cred.sure = true;
    form.querySelector('[type=submit]').textContent = 'Replace the existing key';
    say(`A key called ${name} exists: press again to replace it.`, '');
    return;
  }
  const submit = form.querySelector('[type=submit]');
  submit.disabled = true;
  try { await apiCall('PUT', `/api/vault/${encodeURIComponent(name)}`, body); }
  catch (e) { submit.disabled = false; say(e.message); return; }
  closeCredForm();
  await loadCredentials();
  loadEtsyConnect();  // the owner: the Connect button appeared only after a page refresh
  loadThreadsConnect();
  loadInstagramConnect();
  loadPinterestConnect();
  const row = document.querySelector(`#credList .cred-tr[data-name="${CSS.escape(name)}"]`);
  if (row) { row.classList.add('is-new'); row.scrollIntoView({ block: 'nearest' }); }
  UI.toast(`Saved ${name}`);
}
async function deleteCred(name) {
  const c = _cred.list.find(x => x.name === name) || { name }, used = c.used_by || [];
  await UI.confirm({ title: `Delete ${c.label || name}?`, permanent: true, action: 'Delete the key',
    body: `<p><code>${escapeHtml(name)}</code> is removed from the vault for good.</p>${used.length
      ? `<p>${used.length} ${used.length === 1 ? 'tool uses' : 'tools use'} it (${escapeHtml(used.join(', '))}): ${used.length === 1 ? 'it stops' : 'they stop'} working until a new one is added.</p>` : ''}`,
    run: () => apiCall('DELETE', `/api/vault/${encodeURIComponent(name)}`) }) && (UI.toast(`Deleted ${name}`), loadCredentials(),
      loadEtsyConnect(), loadThreadsConnect(), loadInstagramConnect(), loadPinterestConnect());
}
// Settings > Pro membership: paste a key made on the Pro website (your account page, Access keys). HomeShed never asks
// for the Pro password (R&D's security review: a fork asking for one could be phishing); the box is emptied after
// every try, and the key itself is never shown again.
async function loadProKey() {
  const box = document.getElementById('set-pro-membership-rows');
  if (!box || _viewer) return;
  let d;
  try { d = await proStatus(); }  // the same answer the plan badge asks for at the same moment (panel.js)
  catch (e) { box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; return; }
  const since = d.connected_at ? new Date(d.connected_at * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
  box.innerHTML = d.connected
    ? `<div class="set-row"><div><div class="set-label">Connected${d.pro ? ': Pro is active.' : '.'}</div>
        <div class="hint">${escapeHtml(d.reason || `Using the key "${d.client_name || 'Pro key'}"${since ? `, added ${since}` : ''}.`)}</div></div>
        <div class="set-input"><button class="ghost" onclick="disconnectPro(this)">Disconnect</button></div></div>`
    : `<div class="set-row"><div><div class="set-label">Not connected</div>
        <div class="hint">${d.reason ? escapeHtml(d.reason) : `<a href="${PRO_KEYS_URL}" target="_blank" rel="noopener">Make a key</a> on the Pro website, then paste it here. HomeShed never asks for your Pro password.`}</div></div>
        <div class="set-input"><button onclick="openProKeyForm()">Paste a Pro key</button></div></div>
      <div id="proKeyForm" class="chg-form" hidden></div>`;
}
function openProKeyForm() {
  const f = document.getElementById('proKeyForm');
  if (!f) return;
  f.innerHTML = `<label class="set-label">Pro key <input type="password" name="key" autocomplete="off" spellcheck="false" placeholder="mav_…" aria-label="Pro key"></label>
    <p class="hint">Make one on the Pro website: <a href="${PRO_KEYS_URL}" target="_blank" rel="noopener">your account page, Access keys</a>.
      HomeShed checks it with the Pro website, then keeps it in its vault. Revoking it there ends this install's access.</p>
    <div class="chg-actions"><button onclick="connectProKey(this)">Connect</button>
      <button class="ghost" onclick="document.getElementById('proKeyForm').hidden = true">Cancel</button><span class="set-msg" role="status"></span></div>`;
  f.hidden = false;
  f.querySelector('input').focus();
}
async function connectProKey(btn) {
  const f = btn.closest('.chg-form'), msg = f.querySelector('.set-msg'), key = f.querySelector('[name=key]');
  btn.disabled = true;
  msg.textContent = 'Checking the key…';
  try {
    await apiCall('POST', '/api/pro/connect', { key: key.value.trim() });
    key.value = '';
    proStatus.clear();
    loadProKey();
    loadPlanBadge();
  } catch (e) { key.value = ''; msg.textContent = e.message; btn.disabled = false; }
}
async function disconnectPro(btn) {
  if (btn.dataset.sure !== '1') { btn.dataset.sure = '1'; btn.textContent = 'Really disconnect?'; return; }
  let d;
  try { d = await apiCall('DELETE', '/api/pro'); } catch (e) { btn.textContent = e.message; return; }
  proStatus.clear();
  await loadProKey();
  loadPlanBadge();
  const hint = document.querySelector('#set-pro-membership-rows .hint');
  if (d.note && hint) hint.textContent = d.note;  // the key to revoke on the Pro website too
}
async function loadApiCatalog() {
  const box = document.getElementById('set-apis-you-can-use-rows');
  if (!box || _viewer) return;
  box.innerHTML = '<div class="loading">Loading…</div>';
  let d;
  try { d = await apiCall('GET', '/api/apis'); }
  catch (e) { box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; return; }
  const apis = d.apis || [];  // folded to one row, like Capabilities (R&D re-shoot, 2026-10-09)
  box.innerHTML = apis.length ? `<details class="set-adv"><summary>${apis.length} ${apis.length === 1 ? 'API' : 'APIs'}</summary>` + apis.map(a => `<div class="set-row"><div><div class="set-label">${escapeHtml(a.name)}</div>
      <div class="hint">${escapeHtml(a.category || '')} · ${escapeHtml(a.cost || '')} · ${a.local ? 'runs on your own machines' : 'cloud'}</div>
      ${helpHtml(a.how_to_use || a.notes || '')}</div>
    <div class="set-input">${a.status && a.status !== 'live' ? `<code title="The catalog's own status">${escapeHtml(a.status)}</code>` : ''}</div></div>`).join('') + '</details>' : '<div class="empty">No APIs listed yet.</div>';
}
try { _setCat = localStorage.getItem('cp-set-cat'); } catch (e) { /* not remembered */ }
function showSetCat(g) {
  _setCat = g;
  try { localStorage.setItem('cp-set-cat', g); } catch (e) { /* not remembered */ }
  document.querySelectorAll('#setCats button').forEach(b => {
    const on = b.dataset.group === g;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  document.querySelectorAll('#setForm [data-group]').forEach(s => s.classList.toggle('set-hidden', sectionOf(s.dataset.group) !== g));
  const sel = document.getElementById('setCatSel');
  if (sel) sel.value = g;
  document.querySelectorAll('#setForm .set-nomatch').forEach(e => e.classList.remove('set-nomatch'));
  const none = document.getElementById('setNoMatch');
  if (none) none.hidden = true;
}
// Search across every section at once; an empty box goes back to the chosen section.
function settingsSearch(q) {
  q = String(q || '').trim().toLowerCase();
  if (!q) { showSetCat(_setCat); return; }
  let hits = 0;
  document.querySelectorAll('#setForm section[data-group]').forEach(s => {
    s.classList.remove('set-hidden');
    const title = ((s.querySelector('.panel-title') || {}).textContent || '').toLowerCase();
    const whole = title.includes(q);
    let any = whole;
    s.querySelectorAll(".set-row, .cred-tr").forEach(r => {
      const m = whole || r.textContent.toLowerCase().includes(q);
      r.classList.toggle('set-nomatch', !m);
      if (m) {
        any = true; hits++;
        if (r.closest('details')) r.closest('details').open = true;
        if (r.closest('.set-fold-body')) r.closest('.set-fold-body').hidden = false;
      }
    });
    s.classList.toggle('set-nomatch', !any);
  });
  document.querySelectorAll('#setCats button').forEach(b => { b.classList.remove('active'); b.removeAttribute('aria-current'); });
  document.getElementById('setNoMatch').hidden = hits > 0;
}
function settingRow(it, fold) {  // fold: a toggle shown before the input (image providers)
  fold = typeof fold === 'string' ? fold : '';  // .map(settingRow) passes the row's index here: never show it
  if (it.type === 'hours') return hoursRow(it);
  if (it.type === 'info' && it.group === 'Storage on your PC') return changeableRow('storage', it.label, it.value);
  if (it.type === 'info') {
    return `<div class="set-row"><div><div class="set-label">${escapeHtml(it.label)}</div></div><div class="set-input"><code>${escapeHtml(it.value)}</code></div></div>`;
  }
  const v = it.value, on = `saveSetting('${escAttr(it.source)}', '${escAttr(it.key)}', this)`, aria = `aria-label="${escAttr(it.label)}"`;
  let input;
  if (it.type === 'bool') input = `<label class="sw"><input type="checkbox" ${v ? 'checked' : ''} onchange="${on}" ${aria}><span></span></label>`;
  else if (it.type === 'select') input = `<select onchange="${on}" ${aria}>${(it.options || []).map(o =>
    `<option value="${escAttr(o)}" ${o === v ? 'selected' : ''}>${escapeHtml((it.option_labels || {})[o] || MODE_LABEL[o] || (o.charAt(0).toUpperCase() + o.slice(1)))}</option>`).join('')}</select>`;
  else if (it.type === 'int') input = `<input type="number" min="${it.min}" max="${it.max}" step="1" value="${v}" onchange="${on}" ${aria}>`;
  else input = `<input type="text" value="${escAttr(v || '')}" placeholder="${it.type === 'url' ? 'https://…' : ''}" onchange="${on}" ${aria}>`;
  const preview = settingExtra(it);
  // Where it's saved is a tooltip now, not a line under every row (settings-page-review.md #5).
  return `<div class="set-row"><div><div class="set-label" title="${escAttr(SET_WHERE[it.source] || '')}">${escapeHtml(it.label)}</div>${helpHtml(it.help)}${preview}</div>
    <div class="set-input">${fold}${input}<span class="set-msg" id="setMsg-${escAttr(it.source)}-${escAttr(it.key)}"></span></div></div>`;
}
// A row's help shows one line; a longer one gets More (R&D: two or three lines on most rows made every section long).
function helpHtml(text) {
  text = String(text || '');
  if (text.length <= 70) return `<div class="hint">${escapeHtml(text)}</div>`;
  return `<div class="set-helpwrap" onclick="toggleHelp(this)"><div class="hint set-help">${escapeHtml(text)}</div>`
    + '<button type="button" class="set-more" aria-expanded="false">More</button></div>';
}
function toggleHelp(wrap) {  // the whole help line is the tap target; the button is there for the keyboard
  const o = wrap.classList.toggle('open'), b = wrap.querySelector('.set-more');
  b.textContent = o ? 'Less' : 'More';
  b.setAttribute('aria-expanded', o);
}
// Anything a setting row shows under its help: an owner-only part may add its own (the sharing switch's preview).
let settingExtra = () => '';
async function saveSetting(source, key, el) {
  const msg = document.getElementById(`setMsg-${source}-${key}`);
  const value = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value;
  try {
    const d = await apiCall('PUT', '/api/settings', { source, key, value });
    msg.textContent = 'Saved'; msg.className = 'set-msg ok';
    if (el.type === 'number' || el.type === 'text') el.value = d.value;
    if (source === 'panel') applyUiSettings();  // refresh speed, animations and viewer pages apply at once
    if (/^commerce_etsy/.test(key)) loadEtsyConnect();  // a renamed Etsy entry shows on the Connect card at once
    if (/^social_threads/.test(key)) loadThreadsConnect();
    if (/^social_instagram/.test(key)) loadInstagramConnect();
    if (/^social_pinterest/.test(key)) loadPinterestConnect();  // a renamed entry or the Trial switch shows at once
  } catch (e) {
    msg.textContent = e.message; msg.className = 'set-msg bad';
    if (el.type === 'checkbox') el.checked = !el.checked;
  }
}
let _capCats = {};
async function loadCapabilitySwitches() {
  const box = document.getElementById('set-capabilities-rows');
  if (!box) return;
  let t;
  try { t = await apiCall('GET', '/api/tools'); }
  catch (e) { box.innerHTML = `<div class="empty">${loadErr('tools', e)}</div>`; return; }
  _capCats = {};
  (t.tools || []).forEach(x => (_capCats[x.category] = _capCats[x.category] || []).push(x));
  const cats = Object.keys(_capCats).sort(), offN = cats.filter(c => !_capCats[c].some(x => x.enabled)).length;
  box.innerHTML = `<details class="set-adv"><summary>${cats.length - offN} groups on, ${offN} off</summary>` + cats.map(c => {
    const tools = _capCats[c], on = tools.filter(x => x.enabled).length;
    const some = on && on < tools.length ? ` ${tools.length - on} of them switched off.` : '';
    return `<div class="set-row"><div><div class="set-label">${escapeHtml(c)} <span class="count">${tools.length} ${tools.length === 1 ? 'tool' : 'tools'}</span></div>
        ${helpHtml(tools.map(x => x.id.split('.').slice(1).join('.')).join(', ') + '.' + some)}</div>
      <div class="set-input"><label class="sw"><input type="checkbox" ${on ? 'checked' : ''} onchange="switchCategory('${escAttr(c)}', this)" aria-label="${escAttr(c)} tools on or off"><span></span></label>
        <span class="set-msg" id="capMsg-${escAttr(c)}"></span></div></div>`;
  }).join('') + '<div class="hint" style="margin-top:8px">To switch single tools on or off, use the Tools page.</div></details>';
}
async function switchCategory(cat, box) {
  const enable = box.checked, msg = document.getElementById('capMsg-' + cat);
  if (!enable && !confirm(`Switch off every ${cat} tool?\n\nClaude, the agent and apps get a clear "switched off" message until you switch them back on.`)) { box.checked = true; return; }
  let failed = 0;
  for (const x of _capCats[cat] || []) {
    if (x.enabled === enable) continue;
    try { await apiCall('POST', `/api/tools/${encodeURIComponent(x.id)}/${enable ? 'enable' : 'disable'}`); x.enabled = enable; }
    catch (e) { failed++; }
  }
  msg.textContent = failed ? `${failed} couldn't be changed` : 'Saved';
  msg.className = 'set-msg ' + (failed ? 'bad' : 'ok');
}
async function loadConnections() {
  const box = document.getElementById('set-connections-rows');
  if (!box) return;
  box.innerHTML = '<div class="loading">Checking each one…</div>';
  let d;
  try { d = await apiCall('GET', '/api/settings/connections'); }
  catch (e) { box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; refreshChanges(); return; }
  const rows = (d && d.connections) || [];  // an empty answer (a first run) is no rows, not a crash
  box.innerHTML = rows.map(c => changeableRow('connection', c.name, c.address || 'Not set up',
      `<span class="conn-dot ${c.ok ? 'ok' : ''}"></span>`,
      `<div class="hint">${c.not_set_up ? 'Not set up yet' : c.ok ? 'Answering' : 'Not answering right now'}</div>`)).join('')
    + `<div class="hint" style="margin-top:8px">${connectionsHint}</div>`;
  refreshChanges();
}
// A row whose value a request can change: an owner-only part adds the Change button and shows what's pending.
let changeButton = () => '';
let refreshChanges = () => {};
let connectionsHint = "These addresses live in each service's settings file (.env): change one there, then restart what uses it.";
function changeableRow(area, item, value, dot = '', hint = '') {
  const change = changeButton(area, item, value);
  return `<div class="set-row" data-area="${escAttr(area)}" data-item="${escAttr(item)}"><div><div class="set-label">${dot}${escapeHtml(item)}</div>${hint}
      <div class="chg-pending"></div></div>
    <div class="set-input"><code>${escapeHtml(value)}</code>${change}</div></div>
    <div class="chg-form" hidden></div>`;
}


// --- The owner's own password (Settings, owner only). Set in the .env, or saved by the panel (a first start makes one
// and prints it once): only the saved kind can be changed here.
async function loadOwnerPassword() {
  const box = document.getElementById('ownerPwBody');
  if (!box || _viewer) return;
  let d;
  try { d = await apiCall('GET', '/api/owner'); }
  catch (e) { box.innerHTML = `<div class="empty">${loadErr('your password settings', e)}</div>`; return; }
  if (d.in_env) {
    box.innerHTML = '<p class="hint">Your password is set in this Control Panel\'s <code>.env</code> file (DASHBOARD_PASSWORD). To change it, edit that file and restart the panel.</p>';
    return;
  }
  const changed = d.set_at ? ` Last set ${new Date(d.set_at * 1000).toLocaleDateString()}.` : '';
  box.innerHTML = `<p class="hint">Changing it signs out every other browser and phone. Use at least ${d.min_length || 12} characters.${changed}</p>
    <div class="api-form">
      <label>Current password<input type="password" id="opCurrent" autocomplete="current-password"></label>
      <label>New password<input type="password" id="opNew" autocomplete="new-password"></label>
      <label>New password again<input type="password" id="opAgain" autocomplete="new-password"></label>
      <label class="full"><button class="ghost" onclick="changeOwnerPassword()">Change password</button></label>
    </div>
    <div class="hint" id="opMsg" style="margin-top:8px"></div>`;
}
async function changeOwnerPassword() {
  const msg = document.getElementById('opMsg');
  const current = document.getElementById('opCurrent').value, next = document.getElementById('opNew').value;
  if (next !== document.getElementById('opAgain').value) { msg.textContent = "The two new passwords don't match."; return; }
  try {
    await apiCall('POST', '/api/owner/password', { current, new: next });
    ['opCurrent', 'opNew', 'opAgain'].forEach(id => document.getElementById(id).value = '');
    msg.textContent = 'Password changed. Every other browser and phone is signed out.';
  } catch (e) { msg.textContent = e.message; }
}

// --- View-only logins (Settings, owner only): people the owner shares the Control Panel with ----------
async function loadViewers() {
  const box = document.getElementById('viewerList');
  let d;
  try { d = await apiCall('GET', '/api/viewers'); }
  catch (e) { box.innerHTML = `<div class="empty">${loadErr('view-only logins', e)}</div>`; return; }
  if (d.unreadable) { box.innerHTML = '<div class="empty">The view-only logins file on the dashboard can\'t be read, so no view-only login works until it\'s fixed. Your own login is unaffected.</div>'; return; }
  const when = t => new Date(t * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  const viewers = d.viewers || [];  // an empty answer (a first run) is "none yet", not a crash
  document.getElementById('vwCount').textContent = `${viewers.length} ${viewers.length === 1 ? 'login' : 'logins'}`;
  if (!viewers.length) { box.innerHTML = '<div class="ui-empty"><b>No view-only logins yet.</b><span>Press <b>Add a view-only login</b> to share this Control Panel with someone.</span></div>'; return; }
  UI.rerender(box, `<div class="ui-table-wrap"><table class="ui-table viewer-table"><thead><tr><th scope="col">Login</th><th scope="col">Last signed in</th>
      <th scope="col">Expires</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${viewers.map(v => `<tr data-tone="${v.expired ? 'bad' : ''}">
      <td class="ui-name-cell"><span class="wa-title">${escapeHtml(v.name)}</span>${v.note ? `<span class="ui-sec">${escapeHtml(v.note)}</span>` : ''}</td>
      <td data-label="Last signed in">${v.last_login ? escapeHtml(agoText(new Date(v.last_login * 1000).toISOString())) : '<span class="ui-dim">Not yet</span>'}</td>
      <td data-label="Expires">${v.expired ? `<span class="ui-state bad">Expired ${escapeHtml(when(v.expires))}</span>` : v.expires ? `<span class="ui-state good">${escapeHtml(when(v.expires))}</span>` : '<span class="ui-state">Never</span>'}</td>
      <td class="ui-c-act"><button type="button" class="ui-kebab" aria-haspopup="menu" aria-expanded="false" data-key="vw-k-${escAttr(v.name)}" aria-label="Actions for ${escAttr(v.name)}"
        onclick="viewerMenu(this, '${escAttr(v.name)}')"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="3" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="8" cy="13" r="1.5"/></svg></button></td></tr>`).join('')}</tbody></table></div>`);
}
function viewerMenu(btn, name) {
  UI.menu(btn, [{ label: 'Give a new password…', run: () => resetViewer(name) }, '-', { label: 'Remove…', danger: true, run: () => removeViewer(name) }]);
}
function openViewerForm() {
  document.getElementById('vwMsg').textContent = '';
  document.getElementById('vwAdd').showModal();
  document.getElementById('vwName').focus();
}
function showViewerPassword(name, password, sent, verb) {
  const box = document.getElementById('vwToken');
  box.innerHTML = `<b>${escapeHtml(verb)} ${escapeHtml(name)}. Copy the password now: it won't be shown again.</b>
    <code id="vwTokenText">${escapeHtml(password)}</code>
    <button class="ghost" onclick="navigator.clipboard.writeText(document.getElementById('vwTokenText').textContent).then(() => this.textContent = 'Copied')">Copy password</button>
    <button class="ghost" onclick="document.getElementById('vwToken').className = 'token-box'; document.getElementById('vwToken').innerHTML = ''">Done</button>
    <div class="hint">${sent === true ? 'It was also sent to your phone. ' : sent === false ? "It couldn't be sent to your phone, so copy it from here. " : ''}They sign in at this Control Panel's usual address with just this password.</div>`;
  box.className = 'token-box show';
  box.focus({ preventScroll: true });
  box.scrollIntoView({ block: 'nearest' });
}
async function createViewer() {
  const msg = document.getElementById('vwMsg');
  const body = { name: document.getElementById('vwName').value.trim().toLowerCase(), note: document.getElementById('vwNote').value.trim(),
    expires_days: parseInt(document.getElementById('vwExpires').value, 10) || null, send_to_phone: document.getElementById('vwSend').checked };
  await UI.busy(document.getElementById('vwCreate'), async () => {
    try {
      const d = await apiCall('POST', '/api/viewers', body);
      msg.textContent = '';
      document.getElementById('vwAdd').close();
      document.getElementById('vwName').value = ''; document.getElementById('vwNote').value = '';
      showViewerPassword(d.name, d.password, d.sent, 'View-only login created for');
      loadViewers();
    } catch (e) { msg.textContent = e.message; }  // what was typed stays in the boxes
  });
}
async function resetViewer(name) {
  await UI.confirm({ title: `Give ${name} a new password?`, action: 'Give a new password', permanent: true,
    body: `<p>Their current password stops working and they're signed out straight away. The new one shows here once${document.getElementById('vwSend').checked ? ' and goes to your phone' : ''}.</p>`,
    run: async () => {
      const d = await apiCall('POST', `/api/viewers/${encodeURIComponent(name)}/reset`, { send_to_phone: document.getElementById('vwSend').checked });
      showViewerPassword(d.name, d.password, d.sent, 'New password for');
      loadViewers();
    } });
}
async function removeViewer(name) {
  await UI.confirm({ title: `Remove ${name}'s view-only login?`, action: 'Remove the login', permanent: true,
    body: "<p>They're signed out straight away and can't sign in again. You can add them again later with a new password.</p>",
    run: async () => { await apiCall('DELETE', `/api/viewers/${encodeURIComponent(name)}`); UI.toast(`Removed ${name}`); loadViewers(); } });
}

// ---- what switchTab does for this page (panel.js's Pages) ----
Pages.on('settings', { enter: () => loadSettings() });
