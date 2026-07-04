"""Server-rendered dashboard (spec §10): self-contained HTML pages.

Three pages share one stylesheet and nav bar:

- ``/``           — overview (queue, workers, jobs, logs, console)
- ``/autoscaler`` — autoscaler state (enabled? scaling? Slurm submissions & status)
- ``/settings``   — the server's effective configuration

All data comes from the open GET endpoints via ``fetch`` polling — no external CDNs,
theme-aware via ``prefers-color-scheme``, tables scroll inside their own containers.
"""

_STYLE = r"""
:root {
  --bg:#f7f7f8; --fg:#1a1a1c; --card:#ffffff; --muted:#6b6b73; --line:#e3e3e8;
  --ok:#2e7d32; --warn:#b26a00; --err:#c62828; --accent:#2456a4;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#141416; --fg:#e8e8ea; --card:#1e1e22; --muted:#9a9aa3; --line:#2e2e34;
          --ok:#66bb6a; --warn:#ffb74d; --err:#ef5350; --accent:#7aa7e8; }
}
* { box-sizing:border-box; }
body { margin:0; padding:0 1.5rem 3rem; background:var(--bg); color:var(--fg);
       font:14px/1.45 system-ui, sans-serif; }
h1 { font-size:1.2rem; margin:.2rem 0 1rem; }
h2 { font-size:.95rem; margin:1.4rem 0 .5rem; color:var(--muted);
     text-transform:uppercase; letter-spacing:.05em; }
a { color:var(--accent); text-decoration:none; }
nav { display:flex; gap:.3rem; align-items:center; position:sticky; top:0; z-index:5;
      background:var(--bg); padding:.7rem 0 .5rem; border-bottom:1px solid var(--line);
      margin-bottom:1rem; }
nav .brand { font-weight:700; margin-right:1rem; }
nav a { padding:.3rem .8rem; border-radius:7px; color:var(--muted); }
nav a.active { background:var(--card); color:var(--fg); border:1px solid var(--line); }
nav a:hover { color:var(--fg); }
.banner { padding:.6rem .9rem; border-radius:8px; margin-bottom:.6rem; font-weight:600; }
.banner.warn { background:color-mix(in srgb, var(--warn) 15%, var(--card)); color:var(--warn); }
.banner.err  { background:color-mix(in srgb, var(--err) 15%, var(--card)); color:var(--err); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:.6rem; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:.6rem .8rem; }
.tile .v { font-size:1.35rem; font-weight:700; word-break:break-word; }
.tile .k { color:var(--muted); font-size:.78rem; }
.bar { display:flex; height:14px; border-radius:7px; overflow:hidden; border:1px solid var(--line); margin:.6rem 0; }
.bar div { height:100%; }
.b-done{background:var(--ok);} .b-leased{background:var(--accent);}
.b-pending{background:var(--line);} .b-dead{background:var(--err);} .b-cancelled{background:var(--warn);}
.tablewrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }
table { border-collapse:collapse; width:100%; min-width:640px; font-size:.85rem; }
th, td { text-align:left; padding:.35rem .6rem; border-bottom:1px solid var(--line); white-space:nowrap; }
th { color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--card); }
td.err { color:var(--err); max-width:340px; overflow:hidden; text-overflow:ellipsis; }
.status-done,.st-registered,.st-running{color:var(--ok);}
.status-dead,.st-ended{color:var(--err);}
.status-leased,.st-submitted,.st-queued{color:var(--accent);}
.status-cancelled,.st-cancelled{color:var(--warn);}
button { background:var(--card); color:var(--accent); border:1px solid var(--line);
         border-radius:6px; padding:.15rem .5rem; cursor:pointer; font-size:.78rem; }
button:hover { border-color:var(--accent); }
pre#console { background:var(--card); border:1px solid var(--line); border-radius:10px;
              padding:.8rem; max-height:340px; overflow:auto; white-space:pre-wrap; font-size:.78rem; }
tr.errrow { cursor:pointer; }
tr.errrow:hover td { background:color-mix(in srgb, var(--accent) 8%, var(--card)); }
td.trace-cell { padding:0; }
pre.trace { margin:0; padding:.7rem .9rem; white-space:pre-wrap; font-size:.78rem; overflow-x:auto;
            background:color-mix(in srgb, var(--err) 7%, var(--card)); }
.muted { color:var(--muted); }
input, select { background:var(--card); color:var(--fg); border:1px solid var(--line);
                border-radius:6px; padding:.2rem .4rem; }
.badge { display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.78rem;
         font-weight:700; border:1px solid var(--line); }
.badge.on { background:color-mix(in srgb, var(--ok) 18%, var(--card)); color:var(--ok); }
.badge.off { background:var(--card); color:var(--muted); }
.badge.active { background:color-mix(in srgb, var(--accent) 18%, var(--card)); color:var(--accent); }
.dl { background:var(--card); border:1px solid var(--line); border-radius:10px;
      display:grid; grid-template-columns:minmax(180px,max-content) 1fr; overflow:hidden; }
.dl > div { padding:.35rem .8rem; border-bottom:1px solid var(--line); }
.dl > div:nth-child(4n+1), .dl > div:nth-child(4n+2) {
      background:color-mix(in srgb, var(--muted) 6%, var(--card)); }
.dl .key { color:var(--muted); font-family:ui-monospace,monospace; font-size:.82rem; }
.dl .val { word-break:break-word; font-variant-numeric:tabular-nums; }
"""

_PRELUDE = r"""
const API = '/api/v1';
const fmt = (v, d=1) => v == null ? '–' : Number(v).toFixed(d);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ts = t => t ? new Date(t*1000).toLocaleString() : '–';
const ago = t => t ? Math.round(Date.now()/1000 - t) + ' s ago' : '–';
async function get(path) { const r = await fetch(API+path); return r.ok ? r.json() : null; }
function tile(k, v) { return `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`; }
"""


def _nav(active: str) -> str:
    """Top navigation bar with the active page highlighted."""
    def link(href: str, label: str, key: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<a href="{href}"{cls}>{label}</a>'

    return (
        '<nav><span class="brand">HPC Harness</span>'
        + link("/", "Overview", "overview")
        + link("/errors", "Errors", "errors")
        + link("/autoscaler", "Autoscaler", "autoscaler")
        + link("/settings", "Settings", "settings")
        + "</nav>"
    )


def _page(title: str, active: str, body: str, script: str) -> str:
    """Assemble a full self-contained HTML page."""
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{title}</title>\n<style>{_STYLE}</style>\n</head>\n<body>\n"
        + _nav(active)
        + body
        + f"\n<script>{_PRELUDE}\n{script}</script>\n</body>\n</html>\n"
    )


# ----------------------------------------------------------------------- overview

_OVERVIEW_BODY = r"""
<h1>Overview <span id="drained" class="muted"></span></h1>
<div id="banners"></div>
<div class="tiles" id="tiles"></div>
<div class="bar" id="bar"></div>

<h2>Workers</h2>
<div class="tablewrap"><table id="workers"><thead><tr>
<th>id</th><th>host</th><th>mode</th><th>status</th><th>hb age</th><th>slots</th>
<th>done</th><th>failed</th><th>slurm</th><th>error</th><th></th>
</tr></thead><tbody></tbody></table></div>

<h2>Console <span id="console-target" class="muted"></span>
  <button onclick="toggleFollow()" id="followbtn" style="display:none">follow</button></h2>
<pre id="console" class="muted">select a worker's console above</pre>

<h2>Next 50 jobs (lease order)</h2>
<div class="tablewrap"><table id="next"><thead><tr>
<th>id</th><th>label</th><th>prio</th><th>attempts</th>
</tr></thead><tbody></tbody></table></div>

<h2>Last 50 finished</h2>
<div class="tablewrap"><table id="last"><thead><tr>
<th>id</th><th>label</th><th>status</th><th>attempt</th><th>dur s</th><th>peak MB</th><th>error</th>
</tr></thead><tbody></tbody></table></div>

<h2>Log explorer
  <select id="loglevel"><option value="">all levels</option><option>WARNING</option>
  <option>ERROR</option><option>CRITICAL</option></select>
  <input id="logworker" placeholder="worker id" size="12">
  <input id="logjob" placeholder="job id" size="8">
  <button onclick="loadLogs()">filter</button></h2>
<div class="tablewrap"><table id="logs"><thead><tr>
<th>ts</th><th>level</th><th>worker</th><th>job</th><th>message</th>
</tr></thead><tbody></tbody></table></div>
"""

_OVERVIEW_SCRIPT = r"""
let consoleWorker = null, following = false;

async function refreshStatus() {
  const s = await get('/status'); if (!s) return;
  const c = s.counts || {};
  const eta = s.eta_seconds == null ? '–'
    : s.eta_seconds > 5400 ? (s.eta_seconds/3600).toFixed(1)+' h' : Math.round(s.eta_seconds/60)+' min';
  document.getElementById('tiles').innerHTML =
    tile('total', c.total||0) + tile('pending', c.pending||0) + tile('running', c.leased||0) +
    tile('done', c.done||0) + tile('dead', c.dead||0) + tile('cancelled', c.cancelled||0) +
    tile('workers', s.workers_alive||0) + tile('jobs/min', fmt(s.throughput_per_min)) +
    tile('ETA', eta) + tile('mem budget GB', fmt(s.per_job_mem_gb));
  const total = Math.max(c.total||0, 1);
  const seg = (n, cls) => n ? `<div class="${cls}" style="width:${100*n/total}%" title="${cls.slice(2)}: ${n}"></div>` : '';
  document.getElementById('bar').innerHTML =
    seg(c.done, 'b-done') + seg(c.leased, 'b-leased') + seg(c.pending, 'b-pending') +
    seg(c.dead, 'b-dead') + seg(c.cancelled, 'b-cancelled');
  let banners = '';
  if (s.paused) banners += `<div class="banner err">LEASING PAUSED — ${esc(s.paused)}` +
    (s.circuit_breaker && s.circuit_breaker.top_error ? `<br>top error: ${esc(s.circuit_breaker.top_error)}` : '') +
    `</div>`;
  if (s.mem_warning) banners += `<div class="banner warn">${esc(s.mem_warning.message)}</div>`;
  document.getElementById('banners').innerHTML = banners;
  document.getElementById('drained').textContent = s.drained ? '— run complete' : '';
}

async function refreshWorkers() {
  const rows = await get('/workers'); if (!rows) return;
  document.querySelector('#workers tbody').innerHTML = rows.map(w => `<tr>
    <td>${esc(w.worker_id)}</td><td>${esc(w.host)}</td><td>${esc(w.mode)}</td>
    <td class="status-${esc(w.status)}">${esc(w.status)}</td>
    <td>${w.heartbeat_age_s == null ? '–' : Math.round(w.heartbeat_age_s)+' s'}</td>
    <td>${w.slots ?? '–'}</td><td>${w.jobs_done}</td><td>${w.jobs_failed}</td>
    <td>${esc(w.slurm_job_id ?? '')}</td><td class="err">${esc(w.last_error ?? '')}</td>
    <td><button onclick="showConsole('${esc(w.worker_id)}')">console</button>
        <button onclick="logFilterWorker('${esc(w.worker_id)}')">logs</button></td>
  </tr>`).join('');
}

async function refreshJobs() {
  const next = await get('/jobs?state=pending&limit=50');
  if (next) document.querySelector('#next tbody').innerHTML = next.map(j =>
    `<tr><td>${j.id}</td><td>${esc(j.label ?? '')}</td><td>${j.priority}</td><td>${j.attempts}</td></tr>`).join('');
  const parts = await Promise.all(['done','dead','cancelled'].map(st => get(`/jobs?state=${st}&limit=50`)));
  const last = [].concat(...parts.filter(Boolean)).sort((a,b) => (b.updated_at||0)-(a.updated_at||0)).slice(0,50);
  document.querySelector('#last tbody').innerHTML = last.map(j => `<tr>
    <td>${j.id}</td><td>${esc(j.label ?? '')}</td>
    <td class="status-${esc(j.status)}">${esc(j.status)}</td><td>${j.attempts}</td>
    <td>${fmt(j.duration_s, 0)}</td><td>${fmt(j.peak_mem_mb, 0)}</td>
    <td class="err" title="${esc(j.error ?? '')}">${esc((j.error ?? '').slice(0,120))}</td></tr>`).join('');
}

async function loadLogs() {
  const lvl = document.getElementById('loglevel').value;
  const w = document.getElementById('logworker').value.trim();
  const j = document.getElementById('logjob').value.trim();
  let q = '/logs?limit=200';
  if (lvl) q += '&level='+encodeURIComponent(lvl);
  if (w) q += '&worker='+encodeURIComponent(w);
  if (j) q += '&job='+encodeURIComponent(j);
  const rows = await get(q); if (!rows) return;
  document.querySelector('#logs tbody').innerHTML = rows.map(r => `<tr>
    <td>${ts(r.ts)}</td><td>${esc(r.level)}</td><td>${esc(r.worker_id)}</td>
    <td>${r.job_id ?? ''}</td>
    <td class="err" title="${esc(r.traceback ?? '')}">${esc(r.message ?? '')}</td></tr>`).join('');
}

function logFilterWorker(w) {
  document.getElementById('logworker').value = w; loadLogs();
  document.getElementById('logs').scrollIntoView({behavior:'smooth'});
}

async function showConsole(w) {
  consoleWorker = w;
  document.getElementById('console-target').textContent = '('+w+')';
  document.getElementById('followbtn').style.display = '';
  await fetch(API+`/admin/workers/${w}/console`, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:'{}'});
  pollConsole();
}

async function pollConsole() {
  if (!consoleWorker) return;
  const snap = await get(`/workers/${consoleWorker}/console`);
  if (snap) document.getElementById('console').textContent = snap.text || '(empty)';
}

async function toggleFollow() {
  following = !following;
  document.getElementById('followbtn').textContent = following ? 'stop' : 'follow';
  await fetch(API+`/admin/workers/${consoleWorker}/console`, {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({follow: following})});
}

function refreshAll() { refreshStatus(); refreshWorkers(); refreshJobs(); }
refreshAll(); loadLogs();
setInterval(refreshAll, 5000);
setInterval(() => { if (consoleWorker && following) pollConsole(); }, 2000);
setInterval(() => { if (consoleWorker && !following) pollConsole(); }, 10000);
"""


# --------------------------------------------------------------------- autoscaler

_AUTOSCALER_BODY = r"""
<h1>Autoscaler <span id="badges"></span></h1>
<div id="banners"></div>
<div class="tiles" id="tiles"></div>

<h2>Last control period</h2>
<div class="dl" id="lasttick"></div>

<h2>Slurm submissions by state</h2>
<div class="tiles" id="subcounts"></div>

<h2>Configuration</h2>
<div class="dl" id="cfg"></div>

<h2>Tracked submissions (newest first)</h2>
<div class="tablewrap"><table id="subs"><thead><tr>
<th>slurm job id</th><th>mode</th><th>state</th><th>submitted</th><th>registered worker</th>
</tr></thead><tbody></tbody></table></div>
"""

_AUTOSCALER_SCRIPT = r"""
const dl = (rows) => rows.map(([k,v]) =>
  `<div class="key">${esc(k)}</div><div class="val">${v}</div>`).join('');

async function refresh() {
  const a = await get('/autoscale'); if (!a) return;

  const enabled = a.enabled
    ? '<span class="badge on">enabled</span>' : '<span class="badge off">disabled</span>';
  const scaling = a.trying_to_scale
    ? '<span class="badge active">scaling up</span>'
    : `<span class="badge off">${esc(a.action ?? 'idle')}</span>`;
  document.getElementById('badges').innerHTML = enabled + ' ' + scaling;

  let banners = '';
  if (!a.enabled) banners += `<div class="banner warn">Autoscaler is disabled. ` +
    `Enable it in the server config (<code>autoscale.enabled = true</code>) or scale the ` +
    `fleet manually with submit-workers.sh.</div>`;
  if (a.error) banners += `<div class="banner err">Last tick error: ${esc(a.error)}</div>`;
  document.getElementById('banners').innerHTML = banners;

  document.getElementById('tiles').innerHTML =
    tile('outstanding work', a.work ?? '–') +
    tile('current fleet', a.current ?? '–') +
    tile('alive workers', a.alive_workers ?? '–') +
    tile('in flight (slurm)', a.in_flight ?? '–') +
    tile('queued in slurm', a.slurm_queued ?? '–') +
    tile('idle cores (probe)', a.available_cores ?? '–') +
    tile('to submit', a.to_submit ?? '–') +
    tile('max workers', a.max_workers ?? '–');

  document.getElementById('lasttick').innerHTML = dl([
    ['last run', a.last_tick ? ts(a.last_tick) + ' (' + ago(a.last_tick) + ')' : 'never'],
    ['action', esc(a.action ?? '–')],
    ['reason', esc(a.reason ?? '–')],
    ['submitted this tick', a.submitted ?? 0],
    ['cancelled this tick', a.cancelled ?? 0],
    ['squeue reachable', a.squeue_available == null ? '–' : (a.squeue_available ? 'yes' : 'no (grace-timeout fallback)')],
  ]);

  const counts = a.submission_state_counts || {};
  const order = ['submitted','queued','running','registered','ended','cancelled'];
  const keys = order.filter(k => k in counts).concat(Object.keys(counts).filter(k => !order.includes(k)));
  document.getElementById('subcounts').innerHTML =
    (keys.length ? keys : ['(none)']).map(k =>
      k === '(none)' ? tile('submissions', 0) : tile(k, counts[k])).join('');

  document.getElementById('cfg').innerHTML = dl([
    ['worker_mode', esc(a.worker_mode)],
    ['period_s', a.period_s],
    ['standby_floor', a.standby_floor],
    ['max_workers', a.max_workers],
    ['worker_script', esc(a.worker_script ?? '–')],
    ['slurm_log_dir', esc(a.slurm_log_dir ?? '(server submit dir)')],
    ['partition', esc(a.partition ?? '(any)')],
    ['capacity_probe', esc(a.capacity_probe)],
    ['squeue_poll_s', a.squeue_poll_s],
    ['registration_grace_s', a.registration_grace_s],
  ]);

  const subs = a.submissions || [];
  document.querySelector('#subs tbody').innerHTML = subs.length ? subs.map(s => `<tr>
    <td>${esc(s.slurm_job_id)}</td><td>${esc(s.mode)}</td>
    <td class="st-${esc(s.state)}">${esc(s.state)}</td>
    <td>${ts(s.submitted_at)}</td><td>${esc(s.registered_worker_id ?? '')}</td></tr>`).join('')
    : '<tr><td colspan="5" class="muted">no submissions recorded</td></tr>';
}
refresh();
setInterval(refresh, 5000);
"""


# ----------------------------------------------------------------------- settings

_SETTINGS_BODY = r"""
<h1>Server configuration</h1>
<div class="banner warn">Read-only view of the server's effective configuration.
  The bearer token is redacted. Change <code>per_job_mem_gb</code> live via
  <code>POST /api/v1/admin/config</code>; other values require a server restart.</div>
<div id="sections"></div>
"""

_SETTINGS_SCRIPT = r"""
function dlOf(obj) {
  return '<div class="dl">' + Object.entries(obj).map(([k,v]) => {
    let disp;
    if (v === null || v === undefined) disp = '<span class="muted">null</span>';
    else if (typeof v === 'boolean') disp = v ? 'true' : 'false';
    else disp = esc(v);
    return `<div class="key">${esc(k)}</div><div class="val">${disp}</div>`;
  }).join('') + '</div>';
}

async function refresh() {
  const cfg = await get('/config'); if (!cfg) return;
  const scalars = {}, objects = {};
  for (const [k,v] of Object.entries(cfg)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) objects[k] = v;
    else scalars[k] = v;
  }
  let html = '<h2>Server</h2>' + dlOf(scalars);
  for (const [name, obj] of Object.entries(objects)) {
    html += `<h2>${esc(name)}</h2>` + dlOf(obj);
  }
  document.getElementById('sections').innerHTML = html;
}
refresh();
setInterval(refresh, 15000);
"""


# -------------------------------------------------------------------------- errors

_ERRORS_BODY = r"""
<h1>Errors <span id="lastseen" class="muted"></span></h1>
<div id="banners"></div>
<div class="tiles" id="tiles"></div>

<h2>By source
  <select id="source"><option value="">all sources</option></select>
  <button onclick="refresh()">refresh</button>
  <button onclick="clearErrors()">clear all</button></h2>
<div class="tiles" id="bysource"></div>

<h2>Most frequent types</h2>
<div class="dl" id="bytype"></div>

<h2>Recent errors <span class="muted">(click a row for the full traceback)</span></h2>
<div class="tablewrap"><table id="errs"><thead><tr>
<th>time</th><th>source</th><th>type</th><th>worker</th><th>job</th><th>location</th><th>message</th>
</tr></thead><tbody></tbody></table></div>
"""

_ERRORS_SCRIPT = r"""
function toggleTrace(row) {
  const d = row.nextElementSibling;
  if (d && d.classList.contains('detailrow')) d.style.display = d.style.display === 'none' ? '' : 'none';
}

async function clearErrors() {
  if (!confirm('Delete all recorded errors?')) return;
  const r = await fetch(API+'/admin/errors/clear', {method:'POST',
    headers:{'Content-Type':'application/json'}, body:'{}'});
  if (r.status === 401) { alert('Clearing needs the bearer token (admin route).'); return; }
  refresh();
}

async function refresh() {
  const sum = await get('/errors/summary'); if (!sum) return;
  const win = Math.round((sum.recent_window_s||3600)/60);
  document.getElementById('tiles').innerHTML =
    tile('total errors', sum.total||0) +
    tile(`last ${win} min`, sum.recent||0) +
    tile('sources', Object.keys(sum.by_source||{}).length) +
    tile('last error', sum.last_ts ? ago(sum.last_ts) : 'none');
  document.getElementById('lastseen').textContent =
    sum.last_ts ? '— most recent ' + ts(sum.last_ts) : '— none recorded';

  const bs = sum.by_source || {};
  document.getElementById('bysource').innerHTML =
    Object.keys(bs).length ? Object.entries(bs).map(([k,v]) => tile(k, v)).join('')
    : '<div class="muted">no errors recorded</div>';

  const sel = document.getElementById('source');
  const chosen = sel.value;
  sel.innerHTML = '<option value="">all sources</option>' +
    Object.keys(bs).map(s => `<option${s===chosen?' selected':''}>${esc(s)}</option>`).join('');

  const bt = sum.by_type || {};
  document.getElementById('bytype').innerHTML = Object.keys(bt).length
    ? Object.entries(bt).map(([k,v]) =>
        `<div class="key">${esc(k)}</div><div class="val">${v}</div>`).join('')
    : '<div class="key muted">–</div><div class="val muted">no errors</div>';

  const q = chosen ? '/errors?limit=200&source=' + encodeURIComponent(chosen) : '/errors?limit=200';
  const rows = await get(q); if (!rows) return;
  document.querySelector('#errs tbody').innerHTML = rows.map(e => `
    <tr class="errrow" onclick="toggleTrace(this)">
      <td>${ts(e.ts)}</td><td>${esc(e.source)}</td><td>${esc(e.error_type)}</td>
      <td>${esc(e.worker_id ?? '')}</td><td>${e.job_id ?? ''}</td>
      <td>${esc(e.location ?? '')}</td>
      <td class="err">${esc((e.message ?? '').slice(0,140))}</td>
    </tr>
    <tr class="detailrow" style="display:none">
      <td class="trace-cell" colspan="7"><pre class="trace">${esc(e.traceback || e.message || '(no traceback)')}</pre></td>
    </tr>`).join('');
}
refresh();
setInterval(refresh, 5000);
"""


def render_dashboard() -> str:
    """The overview page HTML (queue, workers, jobs, logs, console)."""
    return _page("HPC Harness", "overview", _OVERVIEW_BODY, _OVERVIEW_SCRIPT)


def render_errors() -> str:
    """The persistent error-overview page HTML."""
    return _page("HPC Harness — Errors", "errors", _ERRORS_BODY, _ERRORS_SCRIPT)


def render_autoscaler() -> str:
    """The autoscaler status page HTML."""
    return _page("HPC Harness — Autoscaler", "autoscaler", _AUTOSCALER_BODY, _AUTOSCALER_SCRIPT)


def render_settings() -> str:
    """The server-configuration page HTML."""
    return _page("HPC Harness — Settings", "settings", _SETTINGS_BODY, _SETTINGS_SCRIPT)
