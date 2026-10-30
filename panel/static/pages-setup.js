/* Settings > System > Set up (static/pages-setup.js; the owner, 2026-10-30): what this install has (memory, AI apps,
another PC sharing the memory, a local model, the helper PC), each ready, needing a fix or optional. A step the panel
can't do itself has a prompt to copy into your own AI app, with this PC's details filled in and never a token. The one
change made here: linking another PC's project folder to a project's memory. setup_routes.py is the server side. */
const SETUP_MARK = { ready: ['ok', 'Ready'], broken: ['bad', 'Needs fixing'], optional: ['', 'Optional'],
  unavailable: ['', 'Not available yet'] };
async function loadSetup() {
  const box = document.getElementById('set-set-up-rows');
  if (!box || _viewer) return;
  box.innerHTML = '<div class="loading">Checking this install…</div>';
  let d;
  try { d = await apiCall('GET', '/api/setup'); }
  catch (e) { box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; return; }
  box.innerHTML = (d.steps || []).map(setupStep).join('');
  if ((d.steps || []).some(s => s.link)) setupLinkProjects();
}
SET_LOADERS.push(loadSetup);
function setupStep(s) {
  const [cls, word] = SETUP_MARK[s.status] || ['', s.status];
  const prompts = s.prompts || (s.prompt ? [{ label: 'Prompt for your AI app', text: s.prompt }] : []);
  return `<div class="set-row setup-step"><div><div class="set-label"><span class="conn-dot ${cls}${cls ? '' : ' off'}"></span>${escapeHtml(s.title)}</div>
      <div class="hint">${escapeHtml(s.detail || '')}</div>
      ${prompts.map(p => `<details class="setup-prompt"><summary>${escapeHtml(p.label)}</summary>
        <p class="hint">Paste this into Claude (or another AI app) on the PC it names. It does the steps and checks they work.</p>
        <textarea readonly rows="6">${escapeHtml(p.text)}</textarea>
        <button type="button" class="ghost" onclick="setupCopy(this)">Copy prompt</button></details>`).join('')}
      ${s.link ? `<div class="setup-link" id="setupLink"></div>` : ''}</div>
    <div class="set-input"><span class="set-msg ${cls}">${escapeHtml(word)}</span></div></div>`;
}
function setupCopy(btn) {
  const text = btn.previousElementSibling.value;
  navigator.clipboard.writeText(text).then(() => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy prompt'; }, 1500); },
    () => { btn.textContent = 'Copy failed: select the text instead'; });
}
async function setupLinkProjects() {
  const box = document.getElementById('setupLink');
  let d;
  try { d = await apiCall('GET', '/api/memory/projects'); } catch (e) { return; }
  const rows = (d.projects || []).filter(p => !p.mine);
  if (!rows.length) {
    box.innerHTML = '<p class="hint">Link a folder here once a project on this PC has saved some memory.</p>';
    return;
  }
  box.innerHTML = `<div class="set-label">3. Link the other PC's folder</div>
    <div class="setup-link-row"><select id="setupLinkProject" aria-label="Project">${rows.map(p =>
      `<option value="${escAttr(p.id)}" data-name="${escAttr(p.name)}">${escapeHtml(p.name)} (${p.facts} facts)</option>`).join('')}</select>
    <input id="setupLinkFolder" placeholder="The other PC's folder, like C:\\Users\\you\\my-app" aria-label="The other PC's folder">
    <button type="button" onclick="setupLink()">Link</button></div>
    <span class="set-msg" id="setupLinkMsg" role="status"></span>`;
}
async function setupLink() {
  const sel = document.getElementById('setupLinkProject'), msg = document.getElementById('setupLinkMsg');
  const opt = sel.selectedOptions[0];
  try {
    await apiCall('POST', '/api/setup/link', { project: sel.value, name: opt && opt.dataset.name,
      folder: document.getElementById('setupLinkFolder').value });
    msg.textContent = 'Linked: AI sessions in that folder now use this project\'s memory.'; msg.className = 'set-msg ok';
  } catch (e) { msg.textContent = e.message; msg.className = 'set-msg bad'; }
}
