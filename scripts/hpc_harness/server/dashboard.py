"""Server-rendered dashboard (spec §10): one self-contained HTML page.

All data comes from the open GET endpoints via ``fetch`` polling — no external CDNs,
theme-aware via ``prefers-color-scheme``, tables scroll inside their own containers.
"""

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HPC Harness</title>
<style>
:root {
  --bg:#f7f7f8; --fg:#1a1a1c; --card:#ffffff; --muted:#6b6b73; --line:#e3e3e8;
  --ok:#2e7d32; --warn:#b26a00; --err:#c62828; --accent:#2456a4;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#141416; --fg:#e8e8ea; --card:#1e1e22; --muted:#9a9aa3; --line:#2e2e34;
          --ok:#66bb6a; --warn:#ffb74d; --err:#ef5350; --accent:#7aa7e8; }
}
* { box-sizing:border-box; }
body { margin:0; padding:1rem 1.5rem 3rem; background:var(--bg); color:var(--fg);
       font:14px/1.45 system-ui, sans-serif; }
h1 { font-size:1.2rem; margin:.2rem 0 1rem; }
h2 { font-size:.95rem; margin:1.4rem 0 .5rem; color:var(--muted);
     text-transform:uppercase; letter-spacing:.05em; }
.banner { padding:.6rem .9rem; border-radius:8px; margin-bottom:.6rem; font-weight:600; }
.banner.warn { background:color-mix(in srgb, var(--warn) 15%, var(--card)); color:var(--warn); }
.banner.err  { background:color-mix(in srgb, var(--err) 15%, var(--card)); color:var(--err); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:.6rem; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:.6rem .8rem; }
.tile .v { font-size:1.35rem; font-weight:700; }
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
.status-done{color:var(--ok);} .status-dead{color:var(--err);}
.status-leased{color:var(--accent);} .status-cancelled{color:var(--warn);}
button { background:var(--card); color:var(--accent); border:1px solid var(--line);
         border-radius:6px; padding:.15rem .5rem; cursor:pointer; font-size:.78rem; }
button:hover { border-color:var(--accent); }
pre#console { background:var(--card); border:1px solid var(--line); border-radius:10px;
              padding:.8rem; max-height:340px; overflow:auto; white-space:pre-wrap; font-size:.78rem; }
.muted { color:var(--muted); }
input, select { background:var(--card); color:var(--fg); border:1px solid var(--line);
                border-radius:6px; padding:.2rem .4rem; }
</style>
</head>
<body>
<h1>HPC Harness <span id="drained" class="muted"></span></h1>
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

<script>
const API = '/api/v1';
let consoleWorker = null, following = false;
const fmt = (v, d=1) => v == null ? '–' : Number(v).toFixed(d);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ts = t => t ? new Date(t*1000).toLocaleTimeString() : '–';

async function get(path) { const r = await fetch(API+path); return r.ok ? r.json() : null; }

function tile(k, v) { return `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`; }

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
</script>
</body>
</html>
"""


def render_dashboard() -> str:
    """Return the dashboard page HTML."""
    return _PAGE
