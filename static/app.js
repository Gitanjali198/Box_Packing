/* Kwik Patch Carton Packing System - UI logic */
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const state = { info: null, fileId: null, filename: '', sheets: [], sheet: null, parsed: null, entries: [], result: null, jobId: null,
                scene: null, selMixed: 0, maxStep: 1 };

function toast(msg, ms) { const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden'); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), ms || 2600); }
async function api(url, opt) {
  const r = await fetch(url, opt);
  let j = null; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((j && j.error) || ('Request failed ' + r.status));
  return j;
}
const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const fmt = n => (n == null ? '' : Number(n).toLocaleString('en-IN'));
const colorOfIdx = idx => { const s = state.result && state.result.skus.find(x => x.idx === idx); return s ? s.color : '#888'; };
const nameOfIdx = idx => { const s = state.result && state.result.skus.find(x => x.idx === idx); return s ? s.name : ''; };

/* ------------------------------------------------------------------ steps */
function showStep(n) {
  $$('section.panel').forEach(p => p.classList.add('hidden'));
  $('#step' + n).classList.remove('hidden');
  $$('#steps button').forEach(b => { b.classList.toggle('active', +b.dataset.step === n); });
  window.scrollTo(0, 0);
}
function enableSteps() {
  $$('#steps button').forEach(b => { b.disabled = +b.dataset.step > state.maxStep; });
}
$$('#steps button').forEach(b => b.addEventListener('click', () => { if (+b.dataset.step <= state.maxStep) showStep(+b.dataset.step); }));

/* ------------------------------------------------------------------ step 1 */
async function loadInfo() {
  state.info = await api('/api/info');
  $('#masterInfo').textContent = `Master data loaded: ${state.info.master_items} products, ${state.info.cartons.length} carton sizes.`;
}
$('#viewMasterBtn')?.addEventListener('click', viewMasterData);
$('#piFile').addEventListener('change', e => { if (e.target.files[0]) uploadPI(e.target.files[0]); });
const drop = $('#drop');
['dragover', 'dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => { if (e.dataTransfer.files[0]) uploadPI(e.dataTransfer.files[0]); });
$('#masterFile').addEventListener('change', async e => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  try { const r = await api('/api/master/upload', { method: 'POST', body: fd }); toast('Master replaced: ' + r.items + ' products'); await loadInfo(); } catch (ex) { alert(ex.message); }
});
async function uploadPI(file) {
  $('#piName').textContent = 'Uploading ' + file.name + ' ...';
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await api('/api/pi/upload', { method: 'POST', body: fd });
    state.fileId = r.file_id; state.filename = r.filename; state.sheets = r.sheets;
    $('#piName').textContent = r.filename;
    const pis = r.sheets.filter(s => s.is_pi);
    if (pis.length === 1) return parsePI(pis[0].sheet);
    $('#sheetBox').classList.remove('hidden');
    $('#sheetList').innerHTML = r.sheets.map(s => `<button data-sheet="${esc(s.sheet)}" class="${s.is_pi ? '' : 'nopi'}"><b>${esc(s.sheet)}</b><small>${esc(s.title || (s.is_pi ? 'PI' : 'no product table'))}</small></button>`).join('');
    $$('#sheetList button').forEach(b => b.addEventListener('click', () => parsePI(b.dataset.sheet)));
  } catch (ex) { $('#piName').textContent = ''; alert(ex.message); }
}
async function parsePI(sheet) {
  try {
    $('#piName').textContent = 'Reading ' + sheet + ' ...';
    const r = await post('/api/pi/parse', { file_id: state.fileId, sheet });
    state.sheet = sheet; state.parsed = r;
    state.hasHL = !!r.has_highlight;
    $('#onlyYellow').checked = state.hasHL; $('#onlyYellow').disabled = !state.hasHL;
    state.entries = r.lines.map(e => Object.assign(e, { skip: state.hasHL && !e.line.highlighted }));
    $('#piName').textContent = state.filename + ' / ' + sheet;
    state.maxStep = Math.max(state.maxStep, 2); enableSteps();
    renderItems(); showStep(2);
  } catch (ex) { alert(ex.message); }
}

/* ------------------------------------------------------------------ step 2 */
function entryStatus(e) { return e.skip ? 'skip' : (e.resolution ? 'ok' : (e.suggest ? 'review' : 'missing')); }
function resolvedInfo(e) {
  const r = e.resolution; if (!r) return '';
  if (r.type === 'master') {
    const it = e.suggest && e.suggest.row === r.row ? e.suggest : (e.picked || null);
    if (it) {
      const topBadge = it.top_up ? ' <span class="chip ok" style="padding:1px 6px;font-size:10px">&#8679; Top side up (Versa)</span>' : '';
      return `<b>${esc(it.name)}</b>${topBadge}<br><small>${dimTxt(it)} &middot; ${it.inner_per_carton}/carton in ${esc(it.carton)}</small>`;
    }
    return `master row ${r.row}`;
  }
  const topBadge = r.top_up ? ' <span class="chip ok" style="padding:1px 6px;font-size:10px">&#8679; Top side up (Versa)</span>' : '';
  return `<b>Manual box</b>${topBadge}<br><small>${r.l}&times;${r.w}&times;${r.h} mm (+${r.margin ?? 2} margin)${r.per_carton ? ' &middot; ' + r.per_carton + '/carton' : ''}</small>`;
}
const dimTxt = it => it.has_dims ? `${it.l}&times;${it.w}&times;${it.h} mm &middot; ${it.units_per_inner} units/box` : 'no box size in master';
function renderItems() {
  const only = $('#onlyAttn').checked;
  let ok = 0, rev = 0, miss = 0;
  const rows = state.entries.map((e, i) => {
    const st = entryStatus(e); if (st === 'ok') ok++; else if (st === 'review') rev++; else if (st === 'missing') miss++;
    return { e, i, st };
  });
  $('#cOk').textContent = ok + ' ready'; $('#cRev').textContent = rev + ' need review'; $('#cMiss').textContent = miss + ' missing box size';
  const tb = $('#itemsTable tbody');
  tb.innerHTML = rows.filter(r => !only || (r.st !== 'ok')).map(({ e, i, st }) => {
    const l = e.line;
    let matched = '';
    if (st === 'ok') matched = resolvedInfo(e);
    else if (e.suggest) matched = `<span class="muted">Suggestion:</span> <b>${esc(e.suggest.name)}</b><br><small>${dimTxt(e.suggest)} &middot; ${e.suggest.inner_per_carton || '?'}/carton</small>` + '';
    else matched = '';
    if (!e.skip && e.issues && e.issues.length) matched += e.issues.filter(i => i.level !== 'info' || st === 'ok').map(i => `<span class="issue ${i.level}">${i.level === 'error' ? '&#10060;' : i.level === 'warn' ? '&#9888;' : '&#8505;'} ${esc(i.msg)}</span>`).join('');
    const acts = [];
    if (e.suggest && st !== 'ok' && e.suggest.has_dims) acts.push(`<button data-a="accept" data-i="${i}">Use suggestion</button>`);
    acts.push(`<button data-a="search" data-i="${i}">Find in master</button>`);
    acts.push(`<button data-a="manual" data-i="${i}">Enter box size</button>`);
    acts.push(`<button data-a="skip" data-i="${i}">${e.skip ? 'Include' : 'Skip'}</button>`);
    return `<tr class="${e.skip ? 'skip' : ''}"><td>${l.idx + 1}</td><td>${esc(l.code)}</td><td>${esc(l.name)}</td><td class="num">${l.units_per_box}</td><td class="num">${fmt(l.boxes)}</td>
      <td><span class="badge ${st}">${(st === 'skip' && state.hasHL && !l.highlighted) ? 'NOT YELLOW' : { ok: 'READY', review: 'REVIEW', missing: 'NO SIZE', skip: 'SKIPPED' }[st]}</span></td><td>${matched}</td><td><div class="acts">${acts.join('')}</div></td></tr>`;
  }).join('');
  renderMissing();
  $('#piTitle').textContent = state.parsed.title || 'Check items';
  const act = state.entries.filter(e => !e.skip);
  $('#piSub').textContent = `${state.parsed.consignee || ''} - ${act.length} of ${state.entries.length} order lines selected for packing (${fmt(act.reduce((a, e) => a + e.line.boxes, 0))} inner boxes)` + (state.hasHL ? '' : ' - no yellow highlighting found in this PI, all lines used');
}
$('#onlyAttn').addEventListener('change', renderItems);
$('#onlyYellow').addEventListener('change', e => {
  state.entries.forEach(en => { en.skip = e.target.checked && !en.line.highlighted; });
  renderItems();
});
$('#itemsTable').addEventListener('click', e => {
  const b = e.target.closest('button[data-a]'); if (!b) return;
  const i = +b.dataset.i, en = state.entries[i], a = b.dataset.a;
  if (a === 'accept') acceptSuggestion(en);
  else if (a === 'skip') { en.skip = !en.skip; renderItems(); }
  else if (a === 'search') openSearch(en);
  else if (a === 'manual') openManual(en);
});
async function acceptSuggestion(en, item) {
  const it = item || en.suggest; en.picked = it; en.suggest = it;
  en.resolution = { type: 'master', row: it.row }; en.skip = false;
  try { await post('/api/item/alias', { line: en.line, row: it.row }); } catch (e) {}
  renderItems();
}
$('#acceptAll').addEventListener('click', () => {
  let n = 0; state.entries.forEach(en => { if (!en.resolution && en.suggest && en.suggest.has_dims) { en.resolution = { type: 'master', row: en.suggest.row }; en.picked = en.suggest; n++; } });
  toast(n + ' suggestions accepted'); renderItems();
});
$('#toSettings').addEventListener('click', () => {
  const ready = state.entries.filter(e => e.resolution && !e.skip).length;
  const pend = state.entries.filter(e => !e.resolution && !e.skip).length;
  if (!ready) toast('No line has usable box data yet - nothing can be calculated. See the red panel.', 5000);
  else if (pend) toast(pend + ' line(s) with missing data will be ignored; the rest will be calculated.', 5000);
  state.maxStep = Math.max(state.maxStep, 3); enableSteps(); renderSettings(); showStep(3);
});

/* modal helpers */
function openModal(html) { $('#modalBox').innerHTML = html; $('#modal').classList.remove('hidden'); }
function closeModal() { $('#modal').classList.add('hidden'); $('#modalBox').classList.remove('wide'); }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });

async function viewMasterData() {
  openModal(`
    <div style="padding:16px 8px;text-align:center">
      <h3>Loading Master Data...</h3>
      <p class="muted">Fetching catalog items from <code>master.xlsx</code></p>
    </div>
  `);
  try {
    const data = await api('/api/master/all');
    renderMasterModal(data);
  } catch (err) {
    openModal(`
      <h3>Failed to load master data</h3>
      <p class="bad">${esc(err.message)}</p>
      <div class="row gap" style="justify-content:flex-end;margin-top:16px"><button class="btn" onclick="closeModal()">Close</button></div>
    `);
  }
}

function renderMasterModal(data) {
  const items = data.items || [];
  const html = `
    <div style="padding:4px 0">
      <div class="row between wrap gap" style="margin-bottom:14px;align-items:center">
        <div>
          <h2 style="margin:0 0 4px 0">Master Data Catalog (${items.length} products)</h2>
          <p class="muted" style="margin:0">Loaded from <code>data/master.xlsx</code> &middot; ${data.cartons?.length || 0} outer carton types</p>
        </div>
        <div class="row gap wrap" style="align-items:center">
          <input type="text" id="mCatalogSearch" placeholder="&#128269; Search code, product, carton..." style="padding:7px 12px;border:1px solid var(--line);border-radius:6px;width:240px;font-size:13px">
          <a class="btn ghost small" href="/api/master/download" download="master.xlsx">&#128190; Download .xlsx</a>
          <button class="btn small" onclick="closeModal()">Close</button>
        </div>
      </div>

      <div class="tablewrap" style="max-height:65vh;overflow-y:auto;border:1px solid var(--line);border-radius:8px">
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead style="position:sticky;top:0;background:var(--card);z-index:2;box-shadow:0 1px 0 var(--line)">
            <tr>
              <th style="padding:9px 10px;text-align:left;width:40px">#</th>
              <th style="padding:9px 10px;text-align:left">Item Code</th>
              <th style="padding:9px 10px;text-align:left">Product Name</th>
              <th style="padding:9px 10px;text-align:center">Top Up</th>
              <th style="padding:9px 10px;text-align:left">Inner Box (L&times;W&times;H mm)</th>
              <th style="padding:9px 10px;text-align:center">Units/Inner</th>
              <th style="padding:9px 10px;text-align:center">Inner/Carton</th>
              <th style="padding:9px 10px;text-align:left">Outer Carton Size (in)</th>
              <th style="padding:9px 10px;text-align:left">Carton Name</th>
            </tr>
          </thead>
          <tbody id="mCatalogBody"></tbody>
        </table>
      </div>
      <div class="row between" style="margin-top:12px;font-size:12px;color:var(--muted);align-items:center">
        <span id="mCatalogCount">Showing ${items.length} of ${items.length} products</span>
        <button class="btn ghost small" onclick="closeModal()">Close</button>
      </div>
    </div>
  `;
  openModal(html);
  $('#modalBox').classList.add('wide');

  const searchInput = $('#mCatalogSearch');
  const tbody = $('#mCatalogBody');
  const countSpan = $('#mCatalogCount');

  const renderRows = (list) => {
    countSpan.textContent = `Showing ${list.length} of ${items.length} products`;
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:28px;color:var(--muted)">No matching products found.</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(it => {
      const dims = it.has_dims ? `${it.l} &times; ${it.w} &times; ${it.h}` : '<span class="chip bad" style="font-size:10px;padding:1px 5px">Missing</span>';
      const pos = it.top_up ? '<span class="chip ok" style="font-size:10px;padding:2px 6px">&#8679; Top Up</span>' : '<span style="color:var(--muted)">-</span>';
      const cartonLabel = it.carton_label || it.carton_key || '-';
      return `
        <tr style="border-bottom:1px solid var(--line)">
          <td style="padding:7px 10px;color:var(--muted)">${it.row}</td>
          <td style="padding:7px 10px;font-weight:600">${esc(it.code || '-')}</td>
          <td style="padding:7px 10px">${esc(it.name || '')}</td>
          <td style="padding:7px 10px;text-align:center">${pos}</td>
          <td style="padding:7px 10px;font-family:monospace">${dims}</td>
          <td style="padding:7px 10px;text-align:center">${it.units_per_inner ?? '-'}</td>
          <td style="padding:7px 10px;text-align:center">${it.inner_per_carton ?? '-'}</td>
          <td style="padding:7px 10px">${esc(cartonLabel)}</td>
          <td style="padding:7px 10px"><span class="chip" style="font-size:11px">${esc(it.carton || '-')}</span></td>
        </tr>
      `;
    }).join('');
  };

  renderRows(items);

  searchInput.addEventListener('input', () => {
    const q = searchInput.value.trim().toLowerCase();
    if (!q) { renderRows(items); return; }
    const filtered = items.filter(it => {
      const code = (it.code || '').toLowerCase();
      const name = (it.name || '').toLowerCase();
      const carton = (it.carton || '').toLowerCase();
      const cLabel = (it.carton_label || '').toLowerCase();
      const dims = `${it.l}x${it.w}x${it.h}`.toLowerCase();
      return code.includes(q) || name.includes(q) || carton.includes(q) || cLabel.includes(q) || dims.includes(q);
    });
    renderRows(filtered);
  });
  searchInput.focus();
}

function openSearch(en) {
  openModal(`<h3>Find in master data</h3><p class="muted">For: <b>${esc(en.line.name)}</b> (${en.line.units_per_box} units/box)</p>
    <input id="sq" style="width:100%" placeholder="type product name or code" value="${esc(en.line.name.replace(/\(.*?\)/g, '').trim().slice(0, 14))}"><div id="sres"></div>
    <div class="row gap" style="margin-top:12px;justify-content:flex-end"><button class="btn" onclick="closeModal()">Close</button></div>`);
  const run = async () => {
    const r = await api('/api/master/search?q=' + encodeURIComponent($('#sq').value));
    $('#sres').innerHTML = r.map((it, k) => `<div class="result" data-k="${k}"><b>${esc(it.name)}</b> ${it.code ? '<small>(' + esc(it.code) + ')</small>' : ''}<br><small>${dimTxt(it)} &middot; ${it.inner_per_carton}/carton in ${esc(it.carton)}${it.units_per_inner !== en.line.units_per_box ? ' <span style="color:var(--warn)">&#9888; master box holds ' + it.units_per_inner + ' units, PI says ' + en.line.units_per_box + '</span>' : ''}</small></div>`).join('') || '<p class="muted">no match</p>';
    $$('#sres .result').forEach(d => d.addEventListener('click', () => { const it = r[+d.dataset.k]; if (!it.has_dims) return alert('This master item has no box size.'); acceptSuggestion(en, it); closeModal(); }));
  };
  $('#sq').addEventListener('input', run); run(); $('#sq').focus();
}
function openManual(en) {
  const prev = en.resolution && en.resolution.type === 'custom' ? en.resolution : {};
  const opts = state.info.cartons.map(c => `<option value="${c.key}" ${prev.carton_key === c.key ? 'selected' : ''}>${esc(c.name)} - ${c.label} in</option>`).join('');
  openModal(`<h3>Enter inner box size</h3><p class="muted"><b>${esc(en.line.name)}</b> &middot; ${en.line.units_per_box} units per inner box &middot; ${fmt(en.line.boxes)} boxes ordered</p>
    <div class="form">
      <label class="field">Length (mm)<input id="mL" type="number" step="0.5" value="${prev.l ?? ''}"></label>
      <label class="field">Width (mm)<input id="mW" type="number" step="0.5" value="${prev.w ?? ''}"></label>
      <label class="field">Height (mm)<input id="mH" type="number" step="0.5" value="${prev.h ?? ''}"></label>
      <label class="field">Safety margin added (mm)<input id="mM" type="number" step="0.5" value="${prev.margin ?? 2}"></label>
      <label class="field">Boxes per FULL carton <small>(blank = calculate)</small><input id="mP" type="number" value="${prev.per_carton ?? ''}"></label>
      <label class="field">Full-carton type<select id="mC"><option value="">Auto (best fit)</option>${opts}</select></label>
      <label class="chk full" style="margin:0"><input type="checkbox" id="mTopUp" ${prev.top_up ? 'checked' : ''}> &#8679; Top side facing upward (Versa patch)</label>
      <label class="chk full" style="margin:0"><input type="checkbox" id="mS" checked> Remember this box size for future PIs</label>
    </div>
    <p class="muted" style="font-size:12px">Measure the inner box (outside size). The margin is added to every side-dimension so the plan has a little slack.</p>
    <div class="row gap" style="margin-top:12px;justify-content:flex-end"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="mOk">Save</button></div>`);
  $('#mOk').addEventListener('click', async () => {
    const l = +$('#mL').value, w = +$('#mW').value, h = +$('#mH').value;
    if (!(l > 0 && w > 0 && h > 0)) return alert('Please enter length, width and height.');
    const spec = { l, w, h, margin: +$('#mM').value || 0, per_carton: +$('#mP').value || null, carton_key: $('#mC').value || null, top_up: $('#mTopUp').checked };
    en.resolution = Object.assign({ type: 'custom' }, spec); en.skip = false;
    if ($('#mS').checked) { try { await post('/api/item/custom', { line: en.line, spec }); } catch (e) {} }
    closeModal(); renderItems();
  });
}
window.closeModal = closeModal;

/* ------------------------------------------------------------------ step 3 */
function renderSettings() {
  const allowed = new Set(state.allowed || state.info.default_allowed);
  state.allowed = Array.from(allowed);
  const cs = state.info.cartons.slice().sort((a, b) => (b.usage - a.usage) || (a.l_in * a.w_in * a.h_in - b.l_in * b.w_in * b.h_in));
  $('#cartonList').innerHTML = cs.map(c => {
    const calcStr = c.calc_label || `${(c.l_in - 1)} x ${(c.w_in - 1)} x ${(c.h_in - 1)}`;
    return `<label class="${allowed.has(c.key) ? 'on' : ''}"><input type="checkbox" value="${c.key}" ${allowed.has(c.key) ? 'checked' : ''}>
     <span><b>${esc(c.name)}</b><br><small>${c.label} in (calc: ${calcStr} in)${c.usage ? ' &middot; ' + c.usage + ' products' : ''}</small></span></label>`;
  }).join('');
  $$('#cartonList input').forEach(i => i.addEventListener('change', () => { i.parentElement.classList.toggle('on', i.checked); }));
}
$('#ccAdd').addEventListener('click', async () => {
  const n = $('#ccName').value.trim(), l = +$('#ccL').value, w = +$('#ccW').value, h = +$('#ccH').value;
  if (!n || !(l > 0 && w > 0 && h > 0)) return alert('Enter a name and L / W / H in inches.');
  const r = await post('/api/cartons', { name: n, l_in: l, w_in: w, h_in: h });
  await loadInfo(); state.allowed = (state.allowed || []).concat([r.key]); renderSettings(); toast('Carton added');
});
$('#calcBtn').addEventListener('click', startCalc);

async function startCalc() {
  const keys = $$('#cartonList input:checked').map(i => i.value);
  if (!keys.length) return alert('Select at least one carton size.');
  state.allowed = keys;
  const settings = {
    mode: $('input[name=mode]:checked') ? $('input[name=mode]:checked').value : 'uniform',
    upright_only: $('#upright') ? $('#upright').checked : false,
    fill_full_cartons: $('input[name=fullCartonCap]:checked') ? $('input[name=fullCartonCap]:checked').value === 'geom' : true,
    carton_tolerance_mm: $('#tol') ? (+$('#tol').value || 0) : 0,
    carton_reduction_inch: $('#cartonReduction') ? (+$('#cartonReduction').value || 1.0) : 1.0,
    allowed_cartons: keys,
    min_fill: ($('#minFill') ? (+$('#minFill').value || 75) : 75) / 100,
    time_budget: $('#timeBudget') ? (+$('#timeBudget').value || 20) : 20
  };
  const lines = state.entries.map(e => ({ line: e.line, resolution: e.resolution, skip: e.skip }));
  state.maxStep = 4; enableSteps(); showStep(4);
  $('#results').classList.add('hidden'); $('#progressBox').classList.remove('hidden'); $('#barFill').style.width = '3%';
  try {
    const r = await post('/api/solve', { lines, settings, margin_adjust: $('#adj') ? (+$('#adj').value || 0) : 0,
      meta: { title: state.parsed.title, consignee: state.parsed.consignee, file: state.filename } });
    state.jobId = r.job; pollJob(settings.time_budget);
  } catch (ex) { alert(ex.message); showStep(3); }
}
async function pollJob(budget) {
  const j = await api('/api/job/' + state.jobId);
  if (j.status === 'running') {
    const p = Math.min(0.94, j.elapsed / (budget * 1.9));
    $('#barFill').style.width = (5 + p * 90) + '%';
    $('#progTxt').textContent = `Searching packing arrangements... ${j.elapsed}s`;
    return setTimeout(() => pollJob(budget), 900);
  }
  if (j.status === 'error') { alert('Calculation failed: ' + j.error); $('#progressBox').classList.add('hidden'); return showStep(3); }
  $('#barFill').style.width = '100%';
  state.result = j.result; setTimeout(() => { $('#progressBox').classList.add('hidden'); renderResult(); }, 250);
}

/* ------------------------------------------------------------------ step 4 : results */
function renderResult() {
  const R = state.result, S = R.summary;
  $('#results').classList.remove('hidden');
  $('#dlXlsx').href = '/api/export/' + state.jobId + '.xlsx';
  const stat = (v, l) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`;
  $('#summary').innerHTML = stat(S.total_cartons, 'TOTAL cartons') + stat(S.full_cartons, 'Full cartons') + stat(S.mixed_cartons, 'Mixed cartons') +
    stat(S.avg_mixed_fill != null ? (S.avg_mixed_fill * 100).toFixed(1) + '%' : '-', 'Avg fill of mixed cartons') +
    stat(S.mixed_lower_bound, 'Theoretical min. mixed cartons') + stat(fmt(S.mixed_boxes), 'Inner boxes in mixed cartons') + stat(S.low_fill_cartons, 'Cartons below ' + Math.round(R.settings.min_fill * 100) + '% fill');
  const ign = [].concat(R.excluded || [], R.skipped || []);
  let ib = $('#ignoredBox');
  if (!ib) { ib = document.createElement('div'); ib.id = 'ignoredBox'; $('#warnBox').before(ib); }
  if (ign.length) { ib.className = 'mpanel'; ib.innerHTML = `<h3>&#9888; ${ign.length} product line(s) were IGNORED - data missing in master / input</h3><div>The packing below is calculated only for the lines that have complete data. Fix the master sheet (or enter the box size on step 2) and calculate again to include them.</div><ul>${ign.map(w => '<li><b>' + esc(w.name) + '</b> &ndash; ' + esc(w.reason) + '</li>').join('')}</ul>`; }
  else { ib.className = 'hidden'; ib.innerHTML = ''; }
  if (!R.full.length && !R.mixed.length) { ib.insertAdjacentHTML('beforeend', '<p><b>Nothing could be packed</b> - none of the selected lines has complete box data.</p>'); }
  const wl = [].concat(R.warnings.map(w => '&#9888; ' + esc(w)), []);
  const wb = $('#warnBox');
  if (wl.length) { wb.classList.remove('hidden'); wb.innerHTML = `<details><summary><b>${wl.length} note(s)</b> - click to show (items not packed, master-quantity differences, low-fill cartons)</summary><ul>${wl.map(x => '<li>' + x + '</li>').join('')}</ul></details>`; } else wb.classList.add('hidden');
  renderMixed(); renderList(); renderFull();
  switchTab('mixed');
}
$$('#resTabs button').forEach(b => b.addEventListener('click', () => switchTab(b.dataset.tab)));
function switchTab(t) {
  $$('#resTabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  ['mixed', 'list', 'full'].forEach(x => $('#tab-' + x).classList.toggle('hidden', x !== t));
  if (!state.result) return;
  if (t === 'mixed' && state.result.mixed.length) showMixed(state.selMixed || 0);
  if (t === 'full' && state.result.full.length) showFull(state.selFull || 0);
}

function renderList() {
  const R = state.result;
  $('#tab-list').innerHTML = `<div class="tablewrap"><table><thead><tr><th>Product code</th><th>Description of goods</th><th>Carton nos</th><th>No of cartons<br><small>units/box &times; boxes &times; cartons</small></th><th class="num">Total qty (units)</th><th>Carton dimension (in)</th></tr></thead><tbody>` +
    R.packing_list.map(r => `<tr style="${r.type === 'MIXED' ? 'background:rgba(255,200,60,.12)' : ''}"><td>${esc(r.code)}</td><td>${esc(r.name)}</td><td><b>${esc(r.carton_nos)}</b></td><td>${esc(r.formula)}</td><td class="num">${fmt(r.units)}</td><td>${esc(r.dimension)}</td></tr>`).join('') +
    `</tbody></table></div><p class="muted">Shaded rows are mixed cartons (several products in one carton). Same layout as your manual packing list; the Excel download has all sheets.</p>`;
}
function fullCj(f) {
  const L = f.layout || {};
  return { no: f.carton_from, carton: f.carton, fill: L.fill || 0, boxes: L.achieved || 0, units: (L.achieved || 0) * f.units_per_inner, contents: L.contents || [], blocks: L.blocks || [], layers: L.layers || [], steps: L.steps || [],
    title: `Full carton ${f.n > 1 ? f.carton_from + ' TO ' + f.carton_to + ' (' + f.n + ' identical cartons)' : f.carton_from} - ${f.name}` };
}
function renderFull() {
  const R = state.result, el = $('#tab-full');
  if (!R.full.length) { el.innerHTML = '<p class="muted">No full cartons.</p>'; return; }
  el.innerHTML = `<div class="mixwrap"><div class="cartonlist" id="fullList"></div><div id="fullDetail"></div></div>`;
  $('#fullList').innerHTML = R.full.map((f, i) => `<button data-i="${i}" class="${i === 0 ? 'active' : ''}"><b>Carton ${f.n > 1 ? f.carton_from + ' TO ' + f.carton_to : f.carton_from}</b> &middot; ${esc(f.carton.name)}<small>${esc(f.name)}</small><small>${f.n} carton(s) &times; ${f.boxes_per_carton} boxes &middot; ${fmt(f.units_per_carton)} units each</small><div class="fillbar"><i style="width:${((f.layout.fill || 0) * 100).toFixed(0)}%"></i></div><small>${((f.layout.fill || 0) * 100).toFixed(0)}% full</small></button>`).join('');
  $$('#fullList button').forEach(b => b.addEventListener('click', () => { $$('#fullList button').forEach(x => x.classList.remove('active')); b.classList.add('active'); showFull(+b.dataset.i); }));
  state.selFull = 0;
}
function showFull(i) {
  state.selFull = i;
  const f = state.result.full[i], el = $('#fullDetail');
  if (!f.layout || !f.layout.achieved) {
    if (state.scene) { state.scene.dispose(); state.scene = null; }
    el.innerHTML = `<h3>${esc(fullCj(f).title)}</h3><p class="muted">Master says ${f.boxes_per_carton} boxes per carton (${fmt(f.units_per_carton)} units) in ${esc(f.carton.name)} (${esc(f.carton_label_in)} in &middot; ${f.carton.l.toFixed(0)} &times; ${f.carton.w.toFixed(0)} &times; ${f.carton.h.toFixed(0)} mm).</p><div class="warnbox">&#9888; ${esc(f.note || 'A 3D layout could not be generated for this product - pack as per the master standard.')}</div>`;
    return;
  }
  const cj = fullCj(f);
  mountDetail(el, cj, 'full');
  if (f.note) {
    const isUnder = f.layout && f.layout.achieved < f.boxes_per_carton;
    const noteHtml = isUnder
      ? `<div class="warnbox" style="margin-bottom:8px">&#9888; ${esc(f.note)}</div>`
      : `<div style="margin-bottom:8px;padding:8px 12px;background:rgba(46,125,50,.1);border:1px solid rgba(46,125,50,.3);border-radius:6px;color:#1b5e20;font-size:13px">&#10004; <b>Full carton packing:</b> ${esc(f.note)}</div>`;
    el.insertAdjacentHTML('afterbegin', noteHtml);
  }
}

function renderMixed() {
  const R = state.result, el = $('#tab-mixed');
  if (!R.mixed.length) { el.innerHTML = '<p class="muted">No leftover boxes - every product packs into full cartons.</p>'; return; }
  el.innerHTML = `<div class="mixwrap"><div class="cartonlist" id="mixList"></div><div id="mixDetail"></div></div>`;
  $('#mixList').innerHTML = R.mixed.map((m, i) => `<button data-i="${i}" class="${i === 0 ? 'active' : ''}"><b>Carton ${m.no}</b> &middot; ${esc(m.carton.name)}<small>${m.contents.length} products &middot; ${m.boxes} boxes</small><div class="fillbar"><i class="${m.low_fill ? 'low' : ''}" style="width:${(m.fill * 100).toFixed(0)}%"></i></div><small>${(m.fill * 100).toFixed(0)}% full</small></button>`).join('');
  $$('#mixList button').forEach(b => b.addEventListener('click', () => { $$('#mixList button').forEach(x => x.classList.remove('active')); b.classList.add('active'); showMixed(+b.dataset.i); }));
  showMixed(0);
}
function showMixed(i) { state.selMixed = i; mountDetail($('#mixDetail'), state.result.mixed[i], 'mixed'); }

/* shared carton detail view: 3D + legend + layer maps + steps */
function badgesFor(cj) { const m = {}; cj.contents.forEach((c, i) => { m[c.sku] = String(i + 1); }); return m; }
function mountDetail(el, cj, kind) {
  if (state.scene) { state.scene.dispose(); state.scene = null; }
  const B = badgesFor(cj), badgeOf = s => B[s] || '?';
  const cartonIn = n => (n / 25.4).toFixed(1);
  const title = cj.title || `Mixed carton ${cj.no}`;
  el.innerHTML = `<div class="row between wrap"><h3 style="margin:0">${esc(title)} &mdash; ${esc(cj.carton.name)} <small>(${cj.carton.l.toFixed(0)} &times; ${cj.carton.w.toFixed(0)} &times; ${cj.carton.h.toFixed(0)} mm &middot; ${cartonIn(cj.carton.l)} &times; ${cartonIn(cj.carton.w)} &times; ${cartonIn(cj.carton.h)} in)</small></h3>
      <span class="badge ${cj.low_fill ? 'missing' : 'ok'}">${(cj.fill * 100).toFixed(0)}% full &middot; ${cj.boxes} inner boxes &middot; ${fmt(cj.units)} units</span></div>
    <div class="viewer" style="margin-top:10px">
      <div><div class="canvaswrap" id="cv"></div>
        <div class="vctl"><label>Show layers 1 &rarr; <b id="lyTxt"></b></label><input type="range" id="lySl" min="1" max="${Math.max(1, cj.layers.length)}" value="${cj.layers.length}">
          <button class="btn" id="playBtn">&#9654; Build</button>
          <label class="chk" style="margin:0"><input type="checkbox" id="ghostCk" checked> fade lower layers</label>
          <label class="chk" style="margin:0"><input type="checkbox" id="expCk"> exploded</label>
          <label class="chk" style="margin:0"><input type="checkbox" id="markCk" checked> direction marks</label></div>
        <div class="axes">Drag to rotate &middot; scroll to zoom. <b style="color:#b8860b">Yellow arrow</b> = direction of the box LENGTH (L) &middot; <b>darker face</b> = top (lid, H) side of the inner box &middot; numbered circles = product.</div></div>
      <div><div class="legendnote">Placement key - how every product sits in the carton (<span style="color:${AXCOL.L}">L</span>=box length, <span style="color:${AXCOL.W}">W</span>=box width, <span style="color:${AXCOL.H}">H</span>=box height; the letters show which box side points along the carton length &#8600;, carton width &#8601; and up &#8593;):</div>
        <div class="legend" id="legend"></div></div></div>
    <h3>Layer-by-layer packing (bottom to top)</h3><div class="layers" id="layers"></div>
    <div class="steps"><h3>Step list</h3><ol id="stepList"></ol></div>`;
  // legend
  $('#legend', el).innerHTML = cj.contents.map((c, i) => {
    const col = colorOfIdx(c.sku);
    const icons = c.orientations.map(o => isoIcon(o.extent, o.perm, col, 104)).join('');
    const topBadge = c.top_up ? ' <span class="chip ok" style="padding:1px 6px;font-size:10px">&#8679; Top side facing upward (Versa patch)</span>' : '';
    return `<div class="leg"><div class="sw" style="background:${col}">${i + 1}</div><div><b>${esc(c.name)}</b> <small>${esc(c.code || '')}</small>${topBadge}<br><b>${c.boxes}</b> inner boxes &middot; ${fmt(c.units)} units<br><span class="ori">${c.dims.map(d => d.toFixed(0)).join(' &times; ')} mm (L&times;W&times;H)<br>${c.orientations.map(o => esc(o.text)).join('<br>')}</span></div><div>${icons}</div></div>`;
  }).join('');
  // layers
  let prev = [];
  $('#layers', el).innerHTML = cj.layers.map(L => {
    const svg = layerSVG(cj.carton, L, prev, colorOfIdx, badgeOf, 340);
    prev = prev.concat(L.items);
    const items = L.items.map(it => {
      const o = orientTxt(it);
      return `<li><span class="pill" style="background:${colorOfIdx(it.sku)}">${badgeOf(it.sku)}</span><span><b>${it.count}</b> &times; ${esc(nameOfIdx(it.sku))} <small>(${it.nx} along length &times; ${it.ny} along width)</small><br><small>${o}</small></span></li>`;
    }).join('');
    return `<div class="layer"><h4>Layer ${L.no} <small>&middot; ${L.count} boxes &middot; height ${L.z.toFixed(0)}&ndash;${L.top.toFixed(0)} mm</small></h4>${svg}<ul>${items}</ul></div>`;
  }).join('');
  $('#stepList', el).innerHTML = cj.steps.map(s => `<li><b>${s.count}</b> &times; <span class="pill" style="background:${colorOfIdx(s.sku)}">${badgeOf(s.sku)}</span> ${esc(s.name)} - grid ${s.grid} - layer ${s.layer_from === s.layer_to ? s.layer_from : s.layer_from + '-' + s.layer_to}, start ${s.x.toFixed(0)} mm from left wall &amp; ${s.y.toFixed(0)} mm from front wall.<br><small>${esc(s.orientation)}</small></li>`).join('');
  // 3D
  const sc = new CartonScene($('#cv', el), { height: 460 }); state.scene = sc;
  sc.load(cj.carton, { blocks: cj.blocks, layers: cj.layers }, colorOfIdx, badgeOf);
  const sl = $('#lySl', el), txt = $('#lyTxt', el); txt.textContent = sl.value + ' / ' + sl.max;
  sl.addEventListener('input', () => { txt.textContent = sl.value + ' / ' + sl.max; sc.setOpts({ upTo: +sl.value }); });
  $('#ghostCk', el).addEventListener('change', e => sc.setOpts({ ghost: e.target.checked }));
  $('#expCk', el).addEventListener('change', e => sc.setOpts({ explode: e.target.checked }));
  $('#markCk', el).addEventListener('change', e => sc.setOpts({ marks: e.target.checked }));
  $('#playBtn', el).addEventListener('click', () => {
    let k = 0; const max = +sl.max; clearInterval(sc._play);
    sc._play = setInterval(() => { k++; sl.value = k; sl.dispatchEvent(new Event('input')); if (k >= max) clearInterval(sc._play); }, 900);
    sl.value = 0; sl.dispatchEvent(new Event('input'));
  });
}
function orientTxt(it) {
  const p = PERMS[it.perm], nm = a => AXN[a];
  const posture = p[2] === 2 ? 'upright' : (p[2] === 0 ? 'stood on end (L up)' : 'laid on side (W up)');
  return `${nm(p[0])} along carton length, ${nm(p[1])} along width, ${nm(p[2])} up &middot; ${posture}`;
}

/* ------------------------------------------------------------------ print */
$('#printBtn').addEventListener('click', async () => {
  const R = state.result; if (!R) return;
  toast('Preparing print view...', 1500);
  const pa = $('#printArea'); pa.innerHTML = '';
  const head = `<div class="pcarton"><h2>Packing plan - ${esc(R.meta.title || '')}</h2><p>${esc(R.meta.consignee || '')}</p>
    <p><b>${R.summary.total_cartons}</b> cartons in total: <b>${R.summary.full_cartons}</b> full + <b>${R.summary.mixed_cartons}</b> mixed.
    Rule: ${R.settings.mode === 'uniform' ? 'all boxes in a carton face ONE direction' : 'one direction per product'}.</p>
    <table style="width:100%;border-collapse:collapse;font-size:10pt"><thead><tr><th>Code</th><th>Description</th><th>Carton nos</th><th>Units/box x boxes x cartons</th><th>Units</th><th>Carton (in)</th></tr></thead><tbody>${R.packing_list.map(r => `<tr style="${r.type === 'MIXED' ? 'background:#fff3cf' : ''}"><td>${esc(r.code)}</td><td>${esc(r.name)}</td><td>${esc(r.carton_nos)}</td><td>${esc(r.formula)}</td><td>${fmt(r.units)}</td><td>${esc(r.dimension)}</td></tr>`).join('')}</tbody></table></div>`;
  pa.insertAdjacentHTML('beforeend', head);
  const items = R.mixed.map(m => ({ m, h: 'Mixed carton ' + m.no })).concat(R.full.filter(f => f.layout && f.layout.achieved).map(f => { const c = fullCj(f); return { m: c, h: c.title }; }));
  for (const { m, h } of items) {
    const B = badgesFor(m), badgeOf = s => B[s] || '?';
    const img = snapshotCarton(m.carton, { blocks: m.blocks, layers: m.layers }, colorOfIdx, badgeOf);
    let prev = [];
    const layers = m.layers.map(L => { const svg = layerSVG(m.carton, L, prev, colorOfIdx, badgeOf, 320); prev = prev.concat(L.items);
      return `<div class="layer"><h4>Layer ${L.no} <small>(${L.count} boxes)</small></h4>${svg}<ul>${L.items.map(it => `<li><span class="pill" style="background:${colorOfIdx(it.sku)}">${badgeOf(it.sku)}</span><span><b>${it.count}</b> &times; ${esc(nameOfIdx(it.sku))}<br><small>${orientTxt(it)}</small></span></li>`).join('')}</ul></div>`; }).join('');
    const leg = m.contents.map((c, i) => `<div class="leg" style="grid-template-columns:30px 1fr 110px"><div class="sw" style="background:${colorOfIdx(c.sku)}">${i + 1}</div><div><b>${esc(c.name)}</b> <small>${esc(c.code || '')}</small><br><b>${c.boxes}</b> boxes (${fmt(c.units)} units)<br><span class="ori">${esc(c.orientations.map(o => o.text).join(' | '))}</span></div><div>${c.orientations.map(o => isoIcon(o.extent, o.perm, colorOfIdx(c.sku), 96)).join('')}</div></div>`).join('');
    const calcSub = m.carton.calc_label ? ` &middot; Usable: ${m.carton.calc_label} in` : '';
    pa.insertAdjacentHTML('beforeend', `<div class="pcarton"><h2>${esc(h)} - ${esc(m.carton.name)} (Outer: ${m.carton.label || ''} in${calcSub}) &middot; ${(m.fill * 100).toFixed(0)}% full</h2>
      <img src="${img}"><div class="legend" style="margin:8px 0">${leg}</div><div class="layers">${layers}</div></div>`);
  }
  setTimeout(() => window.print(), 300);
});

/* ------------------------------------------------------------------ init */
loadInfo().catch(e => { $('#masterInfo').textContent = 'Could not load master data: ' + e.message; });
enableSteps();

/* ------------------------------------------------------------------ master-data problems panel */
function renderMissing() {
  const pn = $('#missingPanel');
  const act = state.entries.filter(e => !e.skip);
  // problems = active lines that are not resolved, or resolved from master but with warnings/errors
  const probs = act.filter(e => e.issues && e.issues.some(i => i.level === 'error' || i.level === 'warn') && !(e.resolution && e.resolution.type === 'custom'));
  const unresolved = act.filter(e => !e.resolution);
  const manual = act.filter(e => e.resolution && e.resolution.type === 'custom');
  pn.classList.remove('hidden');
  if (!probs.length && !unresolved.length && !manual.length) {
    pn.className = 'mpanel okpanel'; pn.innerHTML = '<h3>&#10004; Master data complete for all selected lines</h3><small>Every line has box size, units per box and carton information.</small>'; return;
  }
  pn.className = 'mpanel';
  const list = [...new Set([...probs, ...unresolved])];
  const items = list.map(e => {
    const msgs = (e.issues || []).filter(i => i.level !== 'info');
    return `<li class="${msgs.some(m => m.level === 'error') ? '' : 'warn'}"><b>${esc(e.line.name)}</b> ${e.line.code ? '(' + esc(e.line.code) + ') ' : ''}&ndash; ${msgs.map(m => esc(m.msg)).join(' &middot; ') || 'needs a decision'}</li>`;
  }).join('');
  pn.innerHTML = `<h3>&#9888; ${unresolved.length} line(s) cannot be packed - master sheet data is missing or inconsistent</h3>
    <div>${list.length} product(s) have problems. Fix them <b>here</b> (Enter box size / Find in master) <b>or</b> update your master sheet and re-check.
    ${manual.length ? `<br><small>${manual.length} line(s) use manual box sizes that are not in the master sheet.</small>` : ''}</div>
    <ul>${items}</ul>
    <div class="row gap">
      <button class="btn" id="mpTemplate">&#128190; Download sheet of missing items (master layout)</button>
      <label class="btn" style="cursor:pointer">&#8635; I updated the master - upload &amp; re-check<input type="file" id="mpMaster" accept=".xlsx" hidden></label>
      ${manual.length ? '<a class="btn" href="/api/master/export">&#128190; Download master + my manual items</a>' : ''}
    </div>`;
  $('#mpTemplate').onclick = async () => {
    const r = await fetch('/api/missing_template', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ entries: list }) });
    const b = await r.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = 'missing_master_data.xlsx'; a.click();
  };
  $('#mpMaster').onchange = async ev => {
    const f = ev.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    try {
      await api('/api/master/upload', { method: 'POST', body: fd }); await loadInfo();
      const old = state.entries; const r = await post('/api/pi/parse', { file_id: state.fileId, sheet: state.sheet });
      state.parsed = r;
      state.entries = r.lines.map((e, i) => { const o = old[i]; e.skip = o ? o.skip : false;
        if (o && o.resolution && o.resolution.type === 'custom' && !e.resolution) { e.resolution = o.resolution; e.status = 'ok'; } return e; });
      renderItems(); toast('Master reloaded - problems re-checked');
    } catch (ex) { alert(ex.message); }
  };
}
