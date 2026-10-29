/* The To-do page (static/pages-todo.js): tasks for agents, as a list or a board, with a side drawer per task.
   Loaded the first time the page opens, after pages-work-admin.js (Pages in panel.js). */
// The to-do canvas (the owner, 2026-09-30: "make this so much better ... a canvas where the todo list is, and you can
// assign easily to an agent / project"). HomeShed Pro gets the real tasks; the free plan and view-only logins get the
// example list, never the real one (a view-only login never even asks for it; the server refuses it anyway). An agent
// is a project on this PC, one per folder, named as its Claude session; its tasks run in that folder only.
const TODO_STATUS = { new: 'To do', doing: 'Running', blocked: 'Blocked', 'needs-owner': 'Needs you',
  done: 'Done', failed: 'Failed', cancelled: 'Cancelled', submitted: 'Waiting for a check', verified: 'Verified' };
const TODO_CLOSED = ['done', 'failed', 'cancelled', 'verified'];
// Code work (verify): done only when a different session has checked the commit (mcp-server todo.update, 2026-10-28)
const TODO_REASON = { test_fails: 'a test fails', behaviour_mismatch: "it doesn't do what was asked",
  not_enough_evidence: 'not enough evidence', stale: 'out of date', other: 'other' };
const TODO_PRIO = { high: 'High', normal: 'Normal', low: 'Low' };
const TODO_TABS = [
  ['open', 'Open', t => !TODO_CLOSED.includes(t.status)],
  ['you', 'Needs you', t => !TODO_CLOSED.includes(t.status) && (t.waiting_on_ok || t.status === 'needs-owner' || t.status === 'blocked')],
  ['running', 'Running', t => t.status === 'doing'],
  ['finished', 'Finished', t => ['done', 'verified', 'cancelled'].includes(t.status)],
  ['failed', 'Failed', t => t.status === 'failed'],
];
const _todo = { tab: 'open', view: 'list', q: '', rows: [], example: false, agents: [], agentsAt: 0, runner: null,
  sel: new Set(), open: null, dragging: null, tabSet: false, colAll: new Set() };
const TODO_COL_MAX = 8;  // cards a board column shows before Show more
const todoNeedsYou = TODO_TABS[1][2];
function demoTodos() {
  const h = n => Date.now() / 1000 - n * 3600;
  return [
    { id: 14, text: 'Find out why the nightly backup finished 40 minutes late', assignee: 'Backup scripts', priority: 'high',
      status: 'needs-owner', waiting_on_ok: false, created: h(20), updated: h(0.5), comments: [{ by: 'Backup scripts', at: h(0.5),
        text: 'The backup disk is 94% full, which slows every run. Can I delete backups older than 90 days (38 GB)?' }] },
    { id: 15, text: 'Publish the new pricing page', assignee: 'Website redesign', priority: 'normal', status: 'new',
      waiting_on_ok: true, created: h(2), updated: h(2), comments: [] },
    { id: 9, text: "Check last night's backup finished and report its size", assignee: 'Backup scripts', priority: 'normal',
      status: 'new', repeat: 'daily', due: h(-14), runs: 12, waiting_on_ok: false, created: h(300), updated: h(6),
      last_run: { status: 'done', at: h(6), comment: 'Finished at 02:14: 38 GB, no errors.' },
      comments: [{ by: 'Backup scripts', at: h(6), text: 'Finished at 02:14: 38 GB, no errors.' }] },
    { id: 16, text: 'Tidy the README headings and fix the broken links', assignee: 'Release notes', priority: 'low',
      status: 'doing', waiting_on_ok: false, lease: { by: 'Release notes' }, created: h(5), updated: h(0.2), comments: [] },
    { id: 18, text: 'Fix the date parser that drops the time zone', assignee: 'Website redesign', priority: 'normal',
      status: 'submitted', verify: true, waiting_on_ok: false, created: h(8), updated: h(1), comments: [],
      check: { commit: '3f2a9c1', by: 'Website redesign', state: 'submitted', at: h(1), how: 'Run the date tests: the new one passes' } },
    { id: 17, text: 'Write the release notes for 1.4 from the changelog', assignee: 'Release notes', priority: 'normal',
      status: 'new', waiting_on_ok: false, created: h(1), updated: h(1), comments: [] },
    { id: 12, text: 'Check the release notes against the changelog and fill any gaps', assignee: 'Release notes',
      priority: 'normal', status: 'done', waiting_on_ok: false, created: h(30), updated: h(7), comments: [{ by: 'Release notes', at: h(7),
        text: 'Filled 2 gaps (the new install step and the --quiet flag). The docs check finds no problems.' }] },
    { id: 11, text: 'Record the voice-over for the app tour', assignee: 'Voice app', priority: 'normal', status: 'failed',
      waiting_on_ok: false, created: h(26), updated: h(9), comments: [{ by: 'Voice app', at: h(9),
        text: "Stopped: the script this task names isn't in the folder. Add it and I'll pick the task up again." }] },
  ];
}
async function loadTodo() {
  const note = document.getElementById('todoNote');
  let d = null, err = '';
  if (!_viewer) {
    try { d = await apiCall('GET', '/api/todo?status=all'); } catch (e) { err = e.message; }
  }
  const free = !!(d && d.pro === false);
  _todo.example = _viewer || free || !!(d && d.sample);
  note.hidden = !(_viewer || free);
  if (_viewer) note.textContent = "This is an example list. The owner's real tasks stay private.";
  else if (free) note.innerHTML = `${escapeHtml(d.note || 'The to-do list comes with HomeShed Pro.')} Below is an example. <a href="#" onclick="switchTab('pro'); return false;">See what Pro includes</a>`;
  document.getElementById('todoAddBox').hidden = _todo.example || !!err;
  document.getElementById('todoMsg').textContent = '';
  if (err) {
    if (window.WA) WA.live('todo', false, "Couldn't load");
    document.getElementById('todoList').innerHTML = `<div class="tdo-empty">Couldn't load the to-do list: ${escapeHtml(err)}</div>`;
    return;
  }
  if (window.WA) WA.live('todo', true, _todo.example ? 'Example list' : 'Live', Date.now());
  _todo.rows = _todo.example ? demoTodos() : (d.items || []);
  if (!_todo.example) await todoAgents();  // the owner's own agents: never asked for on the example list
  if (!_todo.example) await todoRunner();
  const ids = new Set(_todo.rows.map(t => Number(t.id)));
  _todo.sel.forEach(id => { if (!ids.has(id)) _todo.sel.delete(id); });
  if (!_todo.tabSet && _todo.rows.some(todoNeedsYou)) _todo.tab = 'you';  // first load: what's waiting on the owner comes first
  _todo.tabSet = true;
  todoPickers();
  renderTodo();
}
async function todoAgents() {
  if (Date.now() - _todo.agentsAt < 300000) return;
  try {
    const [d, h] = await Promise.all([apiCall('GET', '/api/claude/sessions?limit=40'),
      apiCall('GET', '/api/claude/sessions?hidden=1&limit=40').catch(() => ({ sessions: [] }))]), seen = new Set();
    _todo.agents = [...(d.sessions || []), ...(h.sessions || [])].filter(r => r.folder_ok && !seen.has(r.folder.toLowerCase()) && seen.add(r.folder.toLowerCase()))
      .map(r => ({ name: r.name, folder: r.folder }));
    _todo.agentsAt = Date.now();
  } catch (e) { /* the PC is off: "Someone else" still takes a typed name */ }
}
async function todoRunner() {
  try { _todo.runner = await apiCall('GET', '/api/todo/runner'); } catch (e) { _todo.runner = { error: e.message }; }
}
function todoAgentName(name) { return name === 'any' ? 'Any agent' : todoProject(name) || name; }  // the folder: a session title goes stale
function todoAgentSub(name) { return name !== 'any' && todoProject(name) && todoProject(name) !== name ? name : ''; }
function todoFolder(name) {
  const key = String(name).toLowerCase().replace(/[\\/]+$/, '');
  const a = _todo.agents.find(x => x.name === name || x.folder.toLowerCase().replace(/[\\/]+$/, '') === key);
  return a ? a.folder : (/^[a-z]:[\\/]/i.test(name) ? name : '');
}
function todoProject(name) { const f = todoFolder(name); return f ? f.replace(/[\\/]+$/, '').split(/[\\/]/).pop() : ''; }
function todoAgentList() {  // one per folder, named as its tasks name it: a session's auto title can change, its folder can't
  const used = new Map();
  _todo.rows.forEach(t => {
    const f = t.assignee !== 'any' && todoFolder(t.assignee);
    if (f && !used.has(f.toLowerCase())) used.set(f.toLowerCase(), t.assignee);
  });
  return _todo.agents.map(a => ({ name: used.get(a.folder.toLowerCase()) || a.name, folder: a.folder }));
}
function todoInitials(name) {
  const base = String(name).replace(/[\\/]+$/, '').split(/[\\/]/).pop() || String(name);
  const words = base.split(/[\s_.-]+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : base.slice(0, 2)).toUpperCase();
}
function todoAvatar(name) {
  if (name === 'any') return '<span class="tdo-av any" aria-hidden="true">?</span>';
  let hue = 0;
  for (const c of String(name)) hue = (hue * 31 + c.charCodeAt(0)) % 360;
  return `<span class="tdo-av" style="--h:${hue}" aria-hidden="true">${escapeHtml(todoInitials(todoAgentName(name)))}</span>`;
}
function todoTime(ts) { return new Date(ts * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }); }
function todoStatus(t) {
  if (t.waiting_on_ok && !TODO_CLOSED.includes(t.status)) return ['Waiting for your OK', 'warn'];
  if (t.status === 'needs-owner' || t.status === 'blocked') return [TODO_STATUS[t.status], 'warn'];
  if (t.status === 'doing') return ['Running', 'run'];
  if (t.status === 'new' && t.repeat && (t.due || 0) * 1000 > Date.now()) return ['Scheduled', 'sched'];
  return [TODO_STATUS[t.status] || t.status, { new: 'new', done: 'ok', verified: 'ok', submitted: 'sched', failed: 'bad',
    cancelled: 'off' }[t.status] || ''];
}
function todoCheck(t) {  // the check tag under a code task: what was handed over and who judged it
  if (!t.verify) return '';
  const c = t.check, who = n => escapeHtml(todoAgentName(n));
  if (t.override && t.status === 'done') return '<span class="tdo-chk">Code · done by you without a check</span>';
  if (!c) return '<span class="tdo-chk">Code · needs a check by another session</span>';
  const sha = `<code>${escapeHtml(c.commit)}</code>`;
  // the verifier's model family, and another family's second opinion when it needed one (not the builder's model)
  const fam = f => f ? ` (${escapeHtml(f)})` : '';
  const second = c.second_family ? ` + ${escapeHtml(c.second_family)} second opinion` : '';
  if (c.state === 'verified') return `<span class="tdo-chk verified">${sha} verified by ${who(c.verdict_by)}${fam(c.verdict_family)}${second}</span>`;
  if (c.state === 'rejected') return `<span class="tdo-chk rejected">${sha} rejected by ${who(c.verdict_by)}${fam(c.verdict_family)}: ${escapeHtml(TODO_REASON[c.reason] || c.reason)}</span>`;
  return `<span class="tdo-chk submitted">${sha} from ${who(c.by)} · waiting for a check</span>`;
}
function todoWhen(t) {  // when it will really run: more use than a made-up due date
  if (TODO_CLOSED.includes(t.status)) return `Finished ${agoText(t.updated * 1000)}`;
  if (t.status === 'doing') return 'Running now';
  if (t.waiting_on_ok) return 'After your OK';
  if (t.status === 'needs-owner' || t.status === 'blocked') return 'After your reply';
  if (t.repeat && (t.due || 0) * 1000 > Date.now()) return `Next ${todoTime(t.due)}`;
  if (t.assignee === 'any') return 'Pick an agent';
  const r = _todo.runner;
  if (_todo.example || !r || r.error) return 'Tonight';
  if (!r.on) return 'When the runner is on';
  if (r.in_hours) return 'In the next few minutes';
  return r.hours ? `Tonight from ${String(r.hours[0]).padStart(2, '0')}:00` : 'Tonight';
}
function todoAgentOptions(withOther, current, bare) {  // bare: names only, where the folder shows beside the list
  const seen = new Set(['any']), opts = [`<option value="any"${current === 'any' ? ' selected' : ''}>Any agent</option>`];
  const add = (name, label) => {
    if (!name || seen.has(name)) return;
    seen.add(name);
    opts.push(`<option value="${escapeHtml(name)}"${name === current ? ' selected' : ''}>${escapeHtml(label)}</option>`);
  };
  todoAgentList().forEach(a => add(a.name, bare ? todoAgentName(a.name) : `${todoAgentName(a.name)} (${a.folder})`));
  _todo.rows.forEach(t => add(t.assignee, todoAgentName(t.assignee)));
  if (withOther) opts.push('<option value="__other">Someone else…</option>');
  return opts.join('');
}
function todoPickers() {
  const show = document.getElementById('todoShow'), was = show.value;
  const names = [...new Set([..._todo.rows.map(t => t.assignee), ...todoAgentList().map(a => a.name)])];
  show.innerHTML = '<option value="">All agents</option>' + names.map(n =>
    `<option value="${escapeHtml(n)}">${escapeHtml(todoAgentName(n))}</option>`).join('');
  show.value = names.includes(was) ? was : '';
  const pick = document.getElementById('todoWhoSel'), picked = pick.value;
  pick.innerHTML = todoAgentOptions(true);
  pick.value = [...pick.options].some(o => o.value === picked) ? picked : 'any';
  todoWhoChanged();
}
function todoWhoChanged() {
  document.getElementById('todoWhoIn').hidden = document.getElementById('todoWhoSel').value !== '__other';
}
function setTodoTab(tab) { _todo.tab = tab; renderTodo(); }
function setTodoView(view) { _todo.view = view; renderTodo(); }
function renderTodo() {
  const who = document.getElementById('todoShow').value, q = _todo.q.trim().toLowerCase();
  const pool = _todo.rows.filter(t => (!who || t.assignee === who)
    && (!q || `#${t.id} ${t.text} ${t.assignee}`.toLowerCase().includes(q)));
  document.getElementById('todoTabs').innerHTML = TODO_TABS.map(([key, name, fits]) => {
    const n = pool.filter(fits).length;
    return `<button class="tdo-tab${_todo.tab === key ? ' active' : ''}${key === 'you' && n ? ' hot' : ''}" role="tab"
      aria-selected="${_todo.tab === key}" onclick="setTodoTab('${key}')">${name}<span class="tdo-n">${n}</span></button>`;
  }).join('');
  const rows = pool.filter((TODO_TABS.find(x => x[0] === _todo.tab) || TODO_TABS[0])[2]);
  document.querySelectorAll('#todoViews button').forEach(b => b.classList.toggle('active', b.dataset.view === _todo.view));
  document.getElementById('todoList').innerHTML = _todo.view === 'board' ? todoBoard(rows)
    : rows.length ? todoTable(rows) : `<div class="tdo-empty">${todoEmpty(who || q)}</div>`;
  renderTodoBulk();
  renderTodoRunner();
  renderTodoDrawer();
  if (window.WA) WA.todoDeck();
}
function todoEmpty(filtered) {
  if (filtered) return 'No tasks match.';
  return { open: 'Nothing open. Add a task above and pick the agent that should do it.', you: 'Nothing needs you right now.',
    running: 'Nothing is running. Overnight, tasks start here one at a time.', finished: 'Nothing finished in the last 30 days.',
    failed: 'No failures in the last 30 days.' }[_todo.tab];
}
function todoTable(rows) {
  const all = !_todo.example && rows.length && rows.every(t => _todo.sel.has(Number(t.id)));
  const ids = JSON.stringify(rows.map(t => Number(t.id)));
  return `<div class="tdo-table" role="table" aria-label="Tasks">
    <div class="tdo-row tdo-head" role="row"><span class="tdo-c-check">${_todo.example ? '' : `<input type="checkbox" aria-label="Select all" ${all ? 'checked' : ''} onchange='todoSelectAll(this.checked, ${ids})'>`}</span>
      <span class="tdo-c-task">Task</span><span class="tdo-c-agent">Agent</span><span class="tdo-c-prio">Priority</span>
      <span class="tdo-c-status">Status</span><span class="tdo-c-when">When</span></div>
    ${rows.map(todoRow).join('')}</div>`;
}
function todoPrioControl(t, id, edit) {
  if (!edit) return `<span class="tdo-prio ${escapeHtml(t.priority)}">${TODO_PRIO[t.priority] || escapeHtml(t.priority)}</span>`;
  return `<select class="tdo-prio ${escapeHtml(t.priority)}" aria-label="Priority of task ${id}" onclick="event.stopPropagation()"
    onchange="todoChange(${id}, {priority: this.value}, this)">${Object.entries(TODO_PRIO).map(([k, v]) =>
      `<option value="${k}"${k === t.priority ? ' selected' : ''}>${v}</option>`).join('')}</select>`;
}
function todoRow(t) {
  const id = Number(t.id), [label, cls] = todoStatus(t), edit = !_todo.example && !TODO_CLOSED.includes(t.status);
  const project = todoAgentSub(t.assignee), n = (t.comments || []).length;
  const sub = [`#${id}`, t.auto ? 'runs without asking' : '', t.repeat ? `repeats ${t.repeat === 'daily' ? 'daily' : 'weekly'}` : '',
    n ? `${n} ${n === 1 ? 'reply' : 'replies'}` : '', t.report ? 'report' : ''].filter(Boolean).join(' · ');
  const agent = edit ? `<select class="tdo-agent-sel" aria-label="Agent for task ${id}" onclick="event.stopPropagation()"
      onchange="todoChange(${id}, {assignee: this.value}, this)">${todoAgentOptions(false, t.assignee, true)}</select>`
    : `<span class="tdo-agent-name">${escapeHtml(todoAgentName(t.assignee))}</span>`;
  return `<div class="tdo-row${_todo.sel.has(id) ? ' sel' : ''}${_todo.open === id ? ' open' : ''}" role="row" tabindex="0"
      onclick="todoOpen(${id})" onkeydown="if (event.key === 'Enter' && event.target === this) todoOpen(${id})">
    <label class="tdo-c-check" onclick="event.stopPropagation()">${_todo.example ? '' : `<input type="checkbox" aria-label="Select task ${id}" ${_todo.sel.has(id) ? 'checked' : ''} onchange="todoSelect(${id}, this.checked)">`}</label>
    <span class="tdo-c-task"><span class="tdo-title">${escapeHtml(t.text)}</span><span class="tdo-sub">${escapeHtml(sub)}</span>${todoCheck(t)}</span>
    <span class="tdo-c-agent">${todoAvatar(t.assignee)}<span class="tdo-agent-col">${agent}${project ? `<span class="tdo-sub">${escapeHtml(project)}</span>` : ''}</span></span>
    <span class="tdo-c-prio">${todoPrioControl(t, id, edit)}</span>
    <span class="tdo-c-status"><span class="tdo-st ${cls}">${escapeHtml(label)}</span></span>
    <span class="tdo-c-when">${escapeHtml(todoWhen(t))}</span>
  </div>`;
}
function todoBoard(rows) {  // one column per agent: drag a card onto an agent to hand it over
  const edit = !_todo.example;
  const all = [...new Set([...(edit ? ['any', ...todoAgentList().map(a => a.name)] : []), ...rows.map(t => t.assignee)])];
  const count = n => rows.filter(t => t.assignee === n).length, asks = new Set(rows.filter(todoNeedsYou).map(t => t.assignee));
  // "Any agent" first, then columns with a task waiting on the owner, then the busiest; empty columns last
  const rank = n => (n === 'any' ? 0 : asks.has(n) ? 1 : count(n) ? 2 : 3);
  const names = all.map((n, i) => [n, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || count(b[0]) - count(a[0]) || a[1] - b[1]).map(x => x[0]);
  return `<div class="tdo-board">${names.map(name => {
    const mine = rows.filter(t => t.assignee === name), project = todoAgentSub(name);
    return `<section class="tdo-col${mine.length ? '' : ' empty'}" data-agent="${escapeHtml(name)}"${edit ? ` ondragover="todoDragOver(event)"
        ondragleave="if (!this.contains(event.relatedTarget)) this.classList.remove('drop')" ondrop="todoDrop(event, this)"` : ''}>
      <header>${todoAvatar(name)}<span class="tdo-agent-col"><b>${escapeHtml(todoAgentName(name))}</b>${project ? `<span class="tdo-sub">${escapeHtml(project)}</span>` : ''}</span><span class="tdo-n">${mine.length}</span></header>
      ${mine.length ? '<div class="tdo-col-cards">' : ''}${mine.slice(0, _todo.colAll.has(name) ? mine.length : TODO_COL_MAX).map(t => {
        const id = Number(t.id), [label, cls] = todoStatus(t), drag = edit && !TODO_CLOSED.includes(t.status);
        return `<article class="tdo-card${_todo.open === id ? ' open' : ''}" onclick="todoOpen(${id})"${drag ? ` draggable="true" ondragstart="todoDragStart(event, ${id})" ondragend="_todo.dragging = null"` : ''}>
          <div class="tdo-title">${escapeHtml(t.text)}</div>
          <div class="tdo-card-meta"><span class="tdo-st ${cls}">${escapeHtml(label)}</span>${t.priority !== 'normal' ? `<span class="tdo-prio ${escapeHtml(t.priority)}">${TODO_PRIO[t.priority] || ''}</span>` : ''}</div>
          <div class="tdo-sub">#${id} · ${escapeHtml(todoWhen(t))}</div></article>`;
      }).join('')}${mine.length > TODO_COL_MAX && !_todo.colAll.has(name) ? `<button type="button" class="ghost ui-feed-more" onclick="_todo.colAll.add(this.closest('.tdo-col').dataset.agent); renderTodo()">Show ${mine.length - TODO_COL_MAX} more</button>` : ''}${mine.length ? '</div>' : `<div class="tdo-col-empty">${edit ? 'Drop a task here to hand it to this agent' : 'Nothing here'}</div>`}
    </section>`;
  }).join('')}</div>`;
}
function todoDragStart(ev, id) { _todo.dragging = id; ev.dataTransfer.setData('text/plain', String(id)); ev.dataTransfer.effectAllowed = 'move'; }
function todoDragOver(ev) { ev.preventDefault(); ev.currentTarget.classList.add('drop'); }
function todoDrop(ev, col) {
  ev.preventDefault();
  col.classList.remove('drop');
  const id = Number(ev.dataTransfer.getData('text/plain')), agent = col.dataset.agent;
  _todo.dragging = null;
  const t = _todo.rows.find(r => Number(r.id) === id);
  if (t && t.assignee !== agent) todoChange(id, { assignee: agent });
}
function todoSelect(id, on) {
  if (on) _todo.sel.add(id); else _todo.sel.delete(id);
  renderTodo();
}
function todoSelectAll(on, ids) { ids.forEach(id => on ? _todo.sel.add(id) : _todo.sel.delete(id)); renderTodo(); }
function renderTodoBulk() {
  const bar = document.getElementById('todoBulk'), n = _todo.sel.size;
  bar.hidden = !n || _todo.example;
  if (bar.hidden) { bar.innerHTML = ''; return; }
  const picked = _todo.rows.filter(t => _todo.sel.has(Number(t.id)));
  bar.innerHTML = `<b>${n} selected</b>
    <label>Hand to <select onchange="if (this.value) todoBulk({assignee: this.value}, this)"><option value="">Choose an agent…</option>${todoAgentOptions(false, '', true)}</select></label>
    ${picked.some(t => t.waiting_on_ok) ? '<button class="ghost" onclick="todoBulk({approve: true}, this)">Approve</button>' : ''}
    <button class="ghost danger" onclick="todoBulkCancel(this)">Cancel tasks</button>
    <button class="ghost" onclick="_todo.sel.clear(); renderTodo()">Clear</button>`;
}
async function todoBulk(change, el) {
  el.disabled = true;
  const failed = [];
  for (const t of _todo.rows.filter(r => _todo.sel.has(Number(r.id)) && !TODO_CLOSED.includes(r.status))) {
    if (change.approve && !t.waiting_on_ok) continue;
    if (change.assignee && t.assignee === change.assignee) continue;
    try { await apiCall('POST', `/api/todo/${Number(t.id)}`, change); } catch (e) { failed.push(`#${t.id}: ${e.message}`); }
  }
  _todo.sel.clear();
  await loadTodo();
  document.getElementById('todoMsg').textContent = failed.join(' · ');
}
function todoBulkCancel(btn) {
  const n = _todo.rows.filter(r => _todo.sel.has(Number(r.id)) && !TODO_CLOSED.includes(r.status)).length;
  UI.confirm({ title: `Cancel ${n} ${n === 1 ? 'task' : 'tasks'}?`, permanent: true, action: `Cancel ${n === 1 ? 'it' : 'them'}`,
    body: '<p>No agent starts them, and one already running is told to stop. They stay in the list under Finished.</p>',
    run: () => todoBulk({ status: 'cancelled' }, btn) });
}
function renderTodoRunner() {
  const el = document.getElementById('todoRunner'), r = _todo.runner, settings = `<a href="#" onclick="switchTab('settings'); return false;">`;
  if (_todo.example) { el.hidden = false; el.innerHTML = '<span class="dot ok"></span><span><b>Overnight: on</b>, 23:00 to 07:00 (example)</span>'; return; }
  el.hidden = !r;
  if (!r) return;
  if (r.not_set_up) {  // not a fault: agents can still take tasks from their own sessions
    el.innerHTML = `<span class="dot"></span><span><b>Overnight runner: not set up</b>. ${escapeHtml(r.why_not || '')} Agents can still take tasks in their own sessions.</span>`;
    return;
  }
  if (r.error) { el.innerHTML = "<span class=\"dot err\"></span><span>Overnight runner: your PC's helper isn't answering</span>"; return; }
  const hh = x => `${String(x).padStart(2, '0')}:00`, run = (r.running || [])[0];
  const hours = !r.hours ? '' : r.hours[0] === r.hours[1] ? 'any time' : `${hh(r.hours[0])} to ${hh(r.hours[1])}`;
  el.innerHTML = r.on  // one text span beside the dot: the pill's gap would otherwise split "on" from its comma
    ? `<span class="dot ok"></span><span><b>Overnight: on</b>, ${hours}${run ? ` · running #${Number(run.id)} for ${escapeHtml(run.assignee || '')}`
      : r.in_hours ? ' · checking every 5 minutes' : ''}${r.tonight ? ` · ${r.tonight} of ${r.most} tonight` : ''} ${settings}Settings</a></span>`
    : `<span class="dot"></span><span><b>Overnight: off</b>. Tasks wait until it's on. ${settings}Switch it on in Settings</a></span>`;
  if (r.on && !run) el.insertAdjacentHTML('beforeend', ' <button class="ghost" onclick="todoRunNow(this)" title="Work through the waiting tasks once, now, whatever the hour; then back to the overnight hours">Run now</button>');
}
async function todoRunNow(btn) {  // R&D C25: one pass through the queue at any hour (mcp-server tools/todo/runner.py run_now)
  btn.disabled = true;
  const msg = document.getElementById('todoMsg');
  try {
    const d = await apiCall('POST', '/api/todo/runner/run-now');
    msg.textContent = d.start ? `Running #${Number(d.start.id)} now: ${d.start.outcome}. The rest follow one at a time.`
      : d.skipped ? `Couldn't run now: ${d.skipped}.` : d.why_not ? `Couldn't run now: ${d.why_not}.` : 'No task is ready to run.';
  } catch (e) { msg.textContent = `Couldn't run now: ${e.message}`; }
  await todoRunner();
  renderTodoRunner();
}
function todoOpen(id) { _todo.open = id; renderTodo(); }
function todoClose() { _todo.open = null; renderTodo(); }
function renderTodoDrawer() {
  const t = _activeTab === 'todo' && _todo.open != null ? _todo.rows.find(r => Number(r.id) === _todo.open) : null;
  const w = WA.drawer('todoDrawer', 'the task details');
  if (!t) { if (w.d.open) w.d.close(); return; }
  const id = Number(t.id), [label, cls] = todoStatus(t), edit = !_todo.example && !TODO_CLOSED.includes(t.status);
  const draft = w.d.dataset.id === String(id) ? ((w.body.querySelector('textarea') || {}).value || '') : '';  // a refresh keeps a half-typed reply
  const folder = todoFolder(t.assignee), repeat = t.repeat === 'daily' ? 'Every day' : t.repeat === 'weekly' ? 'Every week' : 'Once';
  const replies = t.comments || [], SHOW = 6;
  const reply = c => `<li><div class="tdo-act-head"><b>${escapeHtml(c.by === 'owner' ? 'You' : c.by)}</b>
    <span>${escapeHtml(agoText(c.at * 1000))}</span></div><div>${escapeHtml(c.text)}</div></li>`;
  const thread = (replies.length > SHOW ? `<li class="tdo-earlier"><details><summary>${replies.length - SHOW} earlier ${replies.length - SHOW === 1 ? 'reply' : 'replies'}</summary>
      <ol class="tdo-activity">${replies.slice(0, -SHOW).map(reply).join('')}</ol></details></li>` : '') + replies.slice(-SHOW).map(reply).join('');
  const ran = t.last_run ? `, last ${(TODO_STATUS[t.last_run.status] || t.last_run.status).toLowerCase()} ${agoText(t.last_run.at * 1000)}` : '';
  w.d.dataset.id = String(id);
  w.title.textContent = t.text;
  w.sub.innerHTML = `<span>Task #${id}</span><span class="tdo-st ${cls}">${escapeHtml(label)}</span>`;
  w.body.innerHTML = `
    ${edit && t.waiting_on_ok ? `<div class="tdo-ok"><p>It waits for your OK: it could publish, delete, push, pay, send or deploy, or it was left to any agent.</p>
      <button class="ui-primary" onclick="todoChange(${id}, {approve: true}, this)">Approve</button></div>` : ''}
    <div class="ui-block"><dl class="tdo-fields">
      <dt>Agent</dt><dd>${edit ? `<select aria-label="Agent" onchange="todoChange(${id}, {assignee: this.value}, this)">${todoAgentOptions(false, t.assignee, true)}</select>`
        : escapeHtml(todoAgentName(t.assignee))}</dd>
      <dt>Project</dt><dd>${escapeHtml(folder || (t.assignee === 'any' ? 'None yet: pick an agent' : 'Not found on this PC'))}</dd>
      <dt>Priority</dt><dd>${edit ? todoPrioControl(t, id, true) : escapeHtml(TODO_PRIO[t.priority] || t.priority)}</dd>
      <dt>Repeat</dt><dd>${edit ? `<select aria-label="Repeat" onchange="todoChange(${id}, {repeat: this.value || 'never'}, this)">${[['', 'Once'], ['daily', 'Every day'], ['weekly', 'Every week']].map(([v, n]) =>
          `<option value="${v}"${(t.repeat || '') === v ? ' selected' : ''}>${n}</option>`).join('')}</select>` : repeat}</dd>
      <dt>Asking</dt><dd>${edit ? `<select aria-label="Run without asking" onchange="todoChange(${id}, {auto: this.value === 'yes'}, this)">
          <option value="no"${t.auto ? '' : ' selected'}>Asks for your OK when it's risky</option>
          <option value="yes"${t.auto ? ' selected' : ''}>Runs without asking</option></select>`
        : (t.auto ? 'Runs without asking' : "Asks for your OK when it's risky")}</dd>
      <dt>Check</dt><dd>${edit ? `<select aria-label="Code check" onchange="todoChange(${id}, {verify: this.value === 'yes'}, this)">
          <option value="no"${t.verify ? '' : ' selected'}>Done when the agent says so</option>
          <option value="yes"${t.verify ? ' selected' : ''}>Code: another session checks it first</option></select>`
        : (t.verify ? 'Code: another session checks it first' : 'Done when the agent says so')}${todoCheck(t) ? `<div>${todoCheck(t)}</div>` : ''}</dd>
      ${t.check && t.check.how ? `<dt>How to check</dt><dd>${escapeHtml(t.check.how)}</dd>` : ''}
      ${t.check && t.check.evidence ? `<dt>Evidence</dt><dd>${escapeHtml(t.check.evidence)}</dd>` : ''}
      ${t.check && t.check.second_opinion ? `<dt>Second opinion</dt><dd>${escapeHtml(t.check.second_opinion)} <span class="hint">(${escapeHtml(t.check.second_model || '')})</span></dd>` : ''}
      ${t.check && t.check.state === 'rejected' && t.check.text ? `<dt>Why rejected</dt><dd>${escapeHtml(t.check.text)}</dd>` : ''}
      <dt>When</dt><dd>${escapeHtml(todoWhen(t))}</dd>
      ${t.runs ? `<dt>Runs</dt><dd>${Number(t.runs)}${escapeHtml(ran)}</dd>` : ''}
      <dt>Added</dt><dd>${escapeHtml(agoText((t.created || t.updated) * 1000))}</dd>
    </dl></div>
    ${t.report ? `<div class="ui-block"><h3>Report from ${escapeHtml(t.report.by === 'owner' ? 'you' : t.report.by)}, ${escapeHtml(agoText(t.report.at * 1000))}</h3>
      <pre class="tdo-report">${escapeHtml(t.report.text)}</pre></div>` : ''}
    <div class="ui-block"><h3>Activity <span class="ui-sub">${replies.length}</span></h3>
    <ol class="tdo-activity">${thread || '<li class="tdo-sub">No replies yet. The agent comments when it starts, finishes, fails or needs you.</li>'}</ol>
    ${edit ? `<div class="tdo-reply"><textarea rows="2" maxlength="500" aria-label="Reply" placeholder="Reply: the agent reads it before carrying on">${escapeHtml(draft)}</textarea>
      <div class="ui-btnrow"><button class="ui-primary" onclick="todoSendReply(${id}, this)">Send reply</button></div></div>` : ''}</div>
    ${edit && t.verify ? `<div class="ui-block"><h3>Skip the check</h3><p class="hint">Mark it done yourself without another session checking the commit. It's noted on the task.</p>
      <button class="ghost" onclick="todoOverride(${id}, this)">Mark done without a check…</button></div>` : ''}
    ${edit ? `<div class="ui-block danger-zone"><h3>Cancel</h3><p class="hint">No agent starts it, and one already running is told to stop.</p>
      <button class="ghost danger" onclick="todoCancel(${id}, this)">Cancel task…</button></div>` : ''}
    ${_todo.example ? '<p class="tdo-sub">An example task: nothing here is real.</p>' : ''}`;
  if (!w.d.open) {
    w.d.onclose = () => { const was = _todo.open; delete w.d.dataset.id; w.body.innerHTML = ''; if (was != null) { _todo.open = null; renderTodo(); } };
    w.d.showModal();
    w.title.focus();
  }
}
function todoSendReply(id, btn) {
  const value = btn.closest('.tdo-reply').querySelector('textarea').value.trim();
  if (value) todoChange(id, { comment: value }, btn);
}
function todoCancel(id, btn) {
  const t = _todo.rows.find(r => Number(r.id) === id) || { text: '' };
  UI.confirm({ title: `Cancel task #${id}?`, permanent: true, action: 'Cancel the task',
    body: `<p><b>${escapeHtml(t.text)}</b></p><p>No agent starts it, and one already running is told to stop. It stays in the list under Finished.</p>`,
    run: () => apiCall('POST', `/api/todo/${id}`, { status: 'cancelled' }).then(() => { UI.toast(`#${id} cancelled`); return loadTodo(); }) });
}
function todoOverride(id, btn) {  // the owner's override: done without a second session's check (kept on the task)
  UI.confirm({ title: `Mark #${id} done without a check?`, action: 'Mark it done',
    body: '<p>No other session has checked this code. The task records that you marked it done yourself.</p>',
    run: () => todoChange(id, { status: 'done', override: true, comment: 'Marked done by the owner without a second check.' }, btn) });
}
async function todoChange(id, change, btn) {
  if (btn) btn.disabled = true;
  try {
    await apiCall('POST', `/api/todo/${id}`, change);
    await loadTodo();
  } catch (e) {
    if (btn) btn.disabled = false;
    document.getElementById('todoMsg').textContent = `#${id}: ${e.message}`;
  }
}
function todoComposeKey(ev) { if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) addTodo(); }
async function addTodo() {
  const box = document.getElementById('todoText'), msg = document.getElementById('todoAddMsg'), btn = document.getElementById('todoAddBtn');
  const text = box.value.trim();
  if (!text) { msg.textContent = 'Say what to do first.'; box.focus(); return; }
  btn.disabled = true;
  try {
    const pick = document.getElementById('todoWhoSel').value;
    const assignee = pick === '__other' ? (document.getElementById('todoWhoIn').value.trim() || 'any') : pick;
    const t = await apiCall('POST', '/api/todo', { text, assignee, priority: document.getElementById('todoPriority').value,
      repeat: document.getElementById('todoRepeat').value, auto: document.getElementById('todoAuto').checked });
    document.getElementById('todoAuto').checked = false;  // one task at a time: never left ticked by accident
    box.value = '';
    msg.textContent = t.waiting_on_ok ? `Added #${t.id}. It waits for your OK: find it under Needs you.` : `Added #${t.id}.`;
    _todo.tab = t.waiting_on_ok ? 'you' : 'open';
    await loadTodo();
  } catch (e) { msg.textContent = e.message; }
  btn.disabled = false;
}
setInterval(() => {  // keeps Running and the runner pill current, but never redraws under someone typing or dragging
  if (document.hidden || _activeTab !== 'todo' || _todo.example || _todo.dragging) return;
  const a = document.activeElement, typing = a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)
    && (a.closest('#todoView') || a.closest('#todoDrawer'));
  const drafts = document.getElementById('todoText').value || (document.querySelector('#todoDrawer textarea') || {}).value;
  if (!typing && !drafts) loadTodo();
}, 30000);

// ---- what switchTab does for this page (panel.js's Pages) ----
Pages.on('todo', {
  enter: () => loadTodo(),
  leave() { const d = document.getElementById('todoDrawer'); if (d && d.open) d.close(); },
});
