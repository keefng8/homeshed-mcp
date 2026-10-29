/* The AI pages (static/pages-ai.js): Models (the model order, decisions, the benchmark) and Reasoning (solve,
   decompose, route, delegate). Loaded the first time either page opens, after pages-ops.js (Pages in panel.js). */
async function runSolve() {
  const smtLib2 = document.getElementById('solveInput').value;
  const out = document.getElementById('solveOutput');
  out.innerHTML = '<span class="loading">Solving…</span>';
  try {
    const r = await fetch('/api/reasoning/solve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ smt_lib2: smtLib2 }),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    const cls = d.result === 'sat' ? 'rsat' : d.result === 'unsat' ? 'runsat' : 'runknown';
    let html = `<span class="${cls}">${d.result}</span>  (timeout ${d.timeout_ms}ms)\n`;
    if (d.model) {
      html += '\n' + Object.entries(d.model).map(([k, v]) => `${escapeHtml(k)} = ${escapeHtml(v)}`).join('\n');
    }
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<span class="empty">Error: ${escapeHtml(e.message)}</span>`;
  }
}

async function runDecompose() {
  const task = document.getElementById('decomposeInput').value.trim();
  const out = document.getElementById('decomposeOutput');
  if (!task) { out.innerHTML = '<span class="empty">Enter a task first.</span>'; return; }
  out.innerHTML = '<span class="loading">Asking the local model…</span>';
  try {
    const r = await fetch('/api/reasoning/decompose', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task }),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    out.innerHTML = d.subtasks.map((s, i) =>
      `<div class="reasoning-subtask"><span class="n">${i + 1}.</span><span>${escapeHtml(s)}</span></div>`
    ).join('') + `<div style="margin-top:8px;color:var(--muted);font-size:12px;">via ${escapeHtml(d.model)}</div>`;
  } catch (e) {
    out.innerHTML = `<span class="empty">Error: ${escapeHtml(e.message)}</span>`;
  }
}

async function runRoute() {
  const text = document.getElementById('routeInput').value.trim();
  const token_estimate = parseInt(document.getElementById('routeTokenEstimate').value, 10) || 0;
  const tool_count = parseInt(document.getElementById('routeToolCount').value, 10) || 0;
  const out = document.getElementById('routeOutput');
  if (!text) { out.innerHTML = '<span class="empty">Enter some text first.</span>'; return; }
  out.innerHTML = '<span class="loading">Scoring…</span>';
  try {
    const r = await fetch('/api/reasoning/route', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, token_estimate, tool_count }),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    out.innerHTML = `
      <span class="route-badge ${d.recommendation}">${escapeHtml(d.recommendation)}</span>
      <span class="route-conf">${escapeHtml(d.confidence)} confidence</span>
      <div style="margin-top:8px;">score: ${d.score}</div>
      <div style="margin-top:4px;color:var(--muted);">${escapeHtml(d.reasoning)}</div>
      <div style="margin-top:10px;color:var(--muted);font-size:12px;">advisory only — nothing is executed based on this${token_estimate === 0 && tool_count === 0 ? ' · scored with no context (token_estimate/tool_count both 0) — real calls through reasoning.pipeline normally carry real values here' : ''}</div>
    `;
  } catch (e) {
    out.innerHTML = `<span class="empty">Error: ${escapeHtml(e.message)}</span>`;
  }
}

async function runDelegate() {
  const task = document.getElementById('delegateInput').value.trim();
  const out = document.getElementById('delegateOutput');
  if (!task) { out.innerHTML = '<span class="empty">Enter a task first.</span>'; return; }
  out.innerHTML = '<span class="loading">Routing…</span>';
  try {
    const r = await fetch('/api/reasoning/delegate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task }),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    const badge = `<span class="route-badge ${d.handled_by}">${escapeHtml(d.handled_by)}</span>`;
    if (d.handled_by === 'local') {
      out.innerHTML = `
        ${badge}
        <div style="margin-top:8px;">${escapeHtml(d.answer)}</div>
        <div style="margin-top:10px;color:var(--muted);font-size:12px;">answered by ${escapeHtml(d.model)} · score ${d.route.score}</div>
      `;
    } else {
      out.innerHTML = `
        ${badge}
        <div style="margin-top:8px;color:var(--muted);">${escapeHtml(d.route.reasoning)}</div>
        <div style="margin-top:10px;color:var(--muted);font-size:12px;">no answer attempted — this needs Claude, not the local model${d.note ? ' (' + escapeHtml(d.note) + ')' : ''}</div>
      `;
    }
  } catch (e) {
    out.innerHTML = `<span class="empty">Error: ${escapeHtml(e.message)}</span>`;
  }
}

// --- Models tab --------------------------------------------------------------------
// A helper PC's readings and switches, where this setup has one: drawn by an owner-only part of the page, which sets
// this to its drawing function (d: the /api/models answer, or null with the error).
let renderGpuWatch = null;

let _mdlBusy = false, _mdlAt = 0;
async function loadModels() {
  if (_mdlBusy) return;  // a slow reply never has the next 10 s ask start on top of it
  _mdlBusy = true;
  _mdlAt = Date.now();
  let d;
  try { d = await (await fetch('/api/models')).json(); } catch (e) {
    _mdlBusy = false;
    document.getElementById('mdlDelegator').innerHTML = `<div class="ui-empty">${loadErr('the model list', e)}</div>`;
    if (renderGpuWatch) renderGpuWatch(null, e);
    return;
  }
  _mdlBusy = false;
  if (renderGpuWatch) renderGpuWatch(d);
  HSOps.Models.delegator(d.delegator || []);  // static/pages-ops.js: the order as a table, Try again now on a benched one
  document.getElementById('mdlUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  loadDecisions();
  document.getElementById('mdlStatus').textContent = (d.errors || []).join(' · ');
}


// "Why this model?": the delegator's decision log (names, reasons, timings; never prompts), drawn by pages-ops.js
async function loadDecisions() {
  let d;
  try { d = await (await fetch('/api/delegator/decisions')).json(); } catch (e) { d = { error: e.message }; }
  HSOps.Models.decisions(d);
}
async function resetBench(name) {
  const st = document.getElementById('mdlStatus');
  try {
    const r = await (await fetch(`/api/delegator/${encodeURIComponent(name)}/reset`, { method: 'POST' })).json();
    st.textContent = r.error ? `Couldn't reset ${name}: ${r.error}` : r.was_benched ? `${name} will be tried on the next question.` : `${name} wasn't benched.`;
  } catch (e) { st.textContent = 'Reset failed: ' + e.message; }
  loadModels();
}
setInterval(() => { if (!document.hidden && document.getElementById('modelsView').classList.contains('active') && Date.now() - _mdlAt > 8000) loadModels(); }, 10000);  // not straight after the tab's own load

