# HiSim Cache Service — Specification (v1 draft)

A shared cache service for precomputed HiSim artifacts (LPG occupancy profiles, weather
preprocessing, PV yields, heat-pump maps, solar gains, …), served over REST by the existing
RenoVisor C# server, plus a client library inside HiSim that every component uses instead of
talking to the filesystem directly. Goal: compute each expensive feed-forward artifact **once**
across all three execution environments — GitHub Actions golden runs, 10 000+-job cluster sweeps,
and RenoVisor backend containers — and never again.

The service is **strictly an optimization**. Every simulation must produce identical results and
complete successfully with the server unreachable, misconfigured, or absent. No simulation ever
fails because of the cache service.

```
component ──▶ CacheClient.get_or_compute(key, compute_fn)
                 │
                 ├─ 1. local file cache (inputs/cache, atomic writes)      hit ▶ return
                 ├─ 2. remote GET /cache/{key}                             hit ▶ store locally, return
                 ├─ 3. compute_fn()  ── local atomic write ── best-effort PUT /cache/{key}
                 ▼
              result path
```

---

## 1. Two kinds of server content — keep them separate

| | Opportunistic cache | Curated datasets |
|---|---|---|
| Examples | PV yield series, LPG results, preprocessed weather frames, heat-pump maps | Raw weather files (TRY/DWD, currently 121 MB in-repo), pregenerated standard LPG profiles |
| Key | content hash of (component, code version, config) | human-assigned id + dataset version, e.g. `weather/dwd-try-2015/AACHEN/v2` |
| Written by | any authorized HiSim client, on cache miss | maintainers only, out-of-band upload |
| Miss means | normal — client computes locally | client falls back to bundled/offline copy; if none, hard error with clear message |
| Eviction | LRU by last access | never evicted automatically |

Both live behind the same server and client library, but they have different write policies,
different miss semantics, and different lifecycles. Conflating them (e.g. letting an
opportunistic writer overwrite a curated weather file) is the main design error to avoid.

## 2. Server API

All endpoints under a common prefix, e.g. `https://<host>/hisim-cache/api/v1`. Bodies are
zstd-compressed (`Content-Encoding: zstd`); the payload format inside is whatever the component
already writes to its `.cache` file today (the server treats it as an opaque blob).

### 2.1 Opportunistic cache

```
GET  /cache/{component}/{sha}
     200  blob + headers: ETag, X-Producer-Commit, X-Cache-Version
     404  miss (server logs the miss — see §9)

PUT  /cache/{component}/{sha}
     Headers (required): Authorization, X-Producer-Commit, X-Producer-HiSim-Version,
                         X-Cache-Version, Content-Length
     201  stored          200  already existed, body identical → no-op
     409  already existed with *different* content (server keeps the old blob, logs both
          producers — this indicates a key-schema bug and must be investigated, not overwritten)
     413  over size limit (per-blob limit, e.g. 256 MB)
     401/403  auth failure
```

Design notes:
- **One round trip.** No separate "is it available?" endpoint; `GET` returns the blob or 404.
- **Single flat namespace.** Content-complete keys (§3) make branch scoping unnecessary: a
  code change produces new keys, an unchanged computation produces identical bytes. The 409
  rule is the tripwire for key-schema bugs; 409 investigations compare blobs within numeric
  tolerance first, since same-version floats may differ across BLAS/CPU variants.
- **PUT is idempotent.** Ten cluster jobs racing on the same miss all compute the same bytes and
  PUT; first write wins, the rest get 200. Identical-content re-PUT is always harmless.
- Commit id and HiSim version are **metadata, not key material** (§3). They are stored per blob
  so that entries produced by a commit range later found buggy can be purged server-side.

### 2.2 Curated datasets

```
GET  /datasets/{kind}/{content_hash}           content-addressed blob fetch; 200 + ETag / 404
GET  /datasets/{kind}                          index: human-readable name → content hash (JSON),
                                               e.g. "dwd-try-2015-AACHEN" → "3fa9c2..."
PUT  /datasets/{kind}/{content_hash}           maintainer token only; server verifies the hash;
                                               immutable by construction
```

### 2.3 Operational

```
GET  /health                                   200, no auth — used by the client's network probe
GET  /stats                                    hit/miss counters, storage usage (maintainer token)
GET  /misses?since=...                         top missed keys with counts (maintainer token, §9)
DELETE /cache?producer_commit={sha}[..{sha}]   purge by producing commit range (maintainer token)
```

## 3. Cache key schema

The remote key is the local filename stem, unchanged: `{component}/{sha}`. What changes is what
goes **into** the sha. Today (`utils.get_cache_file`) it is
`sha256(config_json + simulation_parameter_key)`. For a shared multi-environment cache this is
extended to:

```
sha256( artifact_kind                       # e.g. "pv_series" — names the producer
      + ":" + code_fingerprint              # auto: sha over the producer module's source AND
                                            #       every hisim module in its transitive import closure
      + ":" + third_party_fingerprint       # auto: name==version for every third-party package
                                            #       imported (transitively) by the closure
      + ":" + dto_json )                    # canonical JSON of the calculation's DTO (§3.1)
```

Fingerprinting is **fully automatic — no declarations**. This is only possible because
producers are standalone modules under the strict import lint (§12): their transitive import
closure is small by construction, so hashing 100% of it neither degenerates into commit-keying
nor needs an author-maintained `CACHE_CODE_DEPS` list (error-prone, and obsolete here).

- **`code_fingerprint`**: static AST walk over the producer module's `import` statements,
  recursing into every `hisim.*` module reached; sha over all their source files. Any edit to
  code that can influence the calculation invalidates exactly the artifacts that depend on
  it — while edits to component plumbing (KPIs, I/O declarations) invalidate nothing.
- **`third_party_fingerprint`**: third-party packages found in the closure contribute
  `name==version` via `importlib.metadata`; the stdlib contributes only the Python
  major.minor. Deliberately NOT a hash of the whole installed environment — the cluster, the
  CI container, and the RenoVisor image never have identical full dependency sets, so
  environment-wide hashing would reduce cross-environment hits to zero.
- **The lint is what makes this sound**: the producer layer bans dynamic imports (invisible to
  AST analysis) and imports of component/simulator machinery. Every import has a visible
  cache cost — importing a frequently-edited registry like `loadtypes` would invalidate all of
  a producer's artifacts on every enum addition — which is healthy pressure to pass plain
  values through the DTO instead.
- **Version-as-proxy caveat**: same package version does not guarantee bit-identical floats
  across BLAS/CPU variants. A 409 conflict (§2.1) is investigated by first comparing the two
  blobs within numeric tolerance; only a genuine value difference indicates incomplete keys.
- The **full repo commit is deliberately NOT key material** for the same reason. It travels as
  metadata (§2.1) instead, enabling purge-by-commit.

### 3.1 Calculation DTOs

Every producer function takes exactly **one parameter**: a frozen dataclass in the producer's
own module holding everything the calculation depends on. Its canonical JSON (sorted keys,
enums by value) is the `dto_json` key material above. Rules:

- **Flat and primitive.** Fields are primitives, enums, lists, and artifact keys — never
  component references or a `SimulationParameters` object. The component extracts what the
  calculation needs (`year`, `seconds_per_timestep`, tilt, azimuth, …) when building the DTO;
  the DTO is the layering firewall between component plumbing and pure calculation.
- **Only result-relevant fields.** Cost, CO₂, and display fields from the component config
  never enter the DTO — so tweaking a price no longer invalidates a physics result. This also
  retires the "null out `component_id.building` before hashing" special case: identity fields
  simply aren't DTO fields.
- **Upstream artifacts as references, not payloads.** A producer needing the weather series
  gets `weather_artifact_key: str` as key material plus the loaded frame in a payload field
  that is excluded from hashing (dataclass `field(metadata={"key_material": False})`).
  Invariant: every payload field pairs with a key field identifying it; the component fills
  both. Keys therefore compose Merkle-style — an upstream weather change propagates into the
  PV key through the reference.
- **Input data files by content hash.** A producer that reads a data file (weather CSVs,
  module databases) references it in the DTO by the file's content hash — which is also how
  curated datasets are indexed and retrieved (§6). Editing a bundled data file therefore
  changes every downstream key automatically; no version-bump discipline required.
- **Nondeterminism must be a field.** Anything random (LPG guid/seed) appears explicitly in
  the DTO; producers themselves are deterministic functions of their DTO.
- No separate DTO versioning: the DTO class lives in the producer module, so shape changes
  are already captured by `code_fingerprint`.

`utils.get_cache_file` keeps its current hashing (config JSON + sim-parameter key) as the
legacy path for not-yet-migrated components; each producer extraction moves its component to
the DTO scheme.
- **Residual staleness risk** — a dynamic import that escapes the AST closure (banned by the
  producer lint) or nondeterminism not routed through a DTO field — is backstopped twice: the
  golden tests on main catch changed results, and the server's 409 rule (§2.1) flags any key
  that two producers computed different bytes for.
- **Keying is a staleness defense, not a poisoning defense.** No key scheme protects against a
  client that uploads wrong bytes under a valid key (buggy fork, tampered client, leaked token) —
  the server cannot recompute values to verify them. Poisoning is handled by the write-auth
  tiers (§7), main-branch-only CI writes, producer metadata, and purge (§2.3).

## 4. Client library: `hisim/caching/`

A new package at the same layer as `hisim/config/`: imported by components and by `utils.py`,
imports nothing from components or the simulator. All cache behavior — local paths, atomic
writes, key construction, remote tiers, dataset retrieval, configuration — lives here.
`utils.get_cache_file` becomes a thin delegating wrapper (kept for backward compatibility,
same signature, same `(exists, path)` return).

```
hisim/caching/
    __init__.py        # public API re-exports
    settings.py        # CacheSettings: env resolution, internal/external selection (§5)
    keys.py            # CacheKey dataclass + sha construction (§3)
    local.py           # local tier: lookup, atomic write (tmp + os.replace), size accounting
    remote.py          # HTTP tier: GET/PUT with timeouts, compression, auth, silent failure
    client.py          # CacheClient: orchestrates local → remote → compute → writeback
    datasets.py        # curated-dataset retrieval: weather files, standard LPG profiles (§6)
```

### 4.1 Public API (sketch)

```python
from hisim.caching import CacheClient, CacheEntry

class MyComponent(Component):
    # No cache declarations: fingerprints derive automatically from the producer
    # module's import closure (§3).

    def i_prepare_simulation(self):
        entry: CacheEntry = self.cache_client.get_or_fetch(
            component=self,                # names the artifact kind; fingerprints come from the producer module
            config=self.my_config,         # hashed as today (building nulled)
            simulation_parameters=self.my_simulation_parameters,
        )
        if entry.exists:
            self.data = pd.read_csv(entry.path)   # component reads its own format, as today
        else:
            self.data = self.compute_expensive_thing()
            self.data.to_csv(entry.path_tmp)      # component writes to the tmp path
            entry.commit()                         # atomic rename + best-effort remote PUT
```

`get_or_fetch` behavior, in order:
1. Local hit → return `exists=True` immediately (no network).
2. Remote enabled and reachable → `GET`; on 200, decompress, atomic-write locally, return hit.
3. Shared directory configured (§5.1) → copy in on hit, return.
4. Miss → return `exists=False` with a **temp path**; `entry.commit()` performs
   `os.replace(tmp, final)` and then, if the environment is write-authorized, PUTs in a
   background thread (fire-and-forget with bounded queue; process exit joins with a short cap).

Atomic local writes fix a latent bug that exists independently of this project: today, two
processes sharing a cache dir (e.g. cluster jobs on a shared filesystem) can read a half-written
`.cache` file. `local.py` ships first for that reason alone (§10, phase 1).

### 4.2 Failure semantics

- Connect timeout ~2 s, read timeout scaled to blob size; any network/HTTP/auth error → log at
  debug level, fall through to the next tier. Repeated failures trip a per-process circuit
  breaker: after N consecutive errors the remote tier is skipped for the rest of the process.
- Malformed remote blob (decompression error, size mismatch vs Content-Length) → discard,
  treat as miss, never write it locally.
- `HISIM_CACHE_NETWORK=off` disables all networking; behavior is bit-identical to today.

## 5. Configuration: internal vs. external endpoints

Follows the existing `.env` precedent (`UTSP_URL` / `UTSP_API_KEY`). All variables optional;
with none set, the library is local-only and HiSim behaves exactly as before.

```
HISIM_CACHE_URL_INTERNAL=https://10.x.x.x/hisim-cache        # institute network / cluster
HISIM_CACHE_URL_EXTERNAL=https://cache.example.fzj.de/hisim-cache
HISIM_CACHE_NETWORK=auto | internal | external | off          # default: auto
HISIM_CACHE_API_KEY=...                                        # write token (read may not need one)
HISIM_CACHE_DIR=...                                            # optional local-dir override
HISIM_CACHE_SHARED_DIR=...                                     # optional shared-directory tier (§5.1)
```

**Standalone guarantee:** a plain `pip install hisim` with no environment variables gives
exactly today's behavior — a local cache directory, no network activity, no FZJ
infrastructure involved. The shared-dir and server tiers are strictly additive opt-ins; the
library never requires them, and users outside FZJ lose nothing by not having them.

### 5.1 Shared-directory tier

For groups with a shared filesystem (HPC `$PROJECT` paths, an institute NAS),
`HISIM_CACHE_SHARED_DIR` enables an additional tier with the same read-through / write-back
semantics as the REST tier. Same keys, same blob format — an entry is interchangeable
between a shared directory and the server. All shared-dir writes are atomic (temp file +
`os.replace`), safe under concurrent writers.

Lookup order when everything is configured: **local dir → server → shared dir → compute.**
The server ranks above the shared dir because it is readable by everyone — external users
query the public endpoint anonymously — while writing to it requires a token. The shared dir
is therefore where clients without write access bank their misses: an external group reads
the public server for free and accumulates everything the server doesn't have in their own
shared dir. Write-back on a hit populates the tiers checked before it; a computed result is
written to every tier the client can write (local always, shared dir if configured, server
PUT only with a write token).

`auto`: probe `GET {internal}/health` once per process with a 1 s timeout; on success use the
internal URL, otherwise the external one (if set), otherwise local-only. The result is cached
for the process lifetime. `internal`/`external` skip probing and pin one endpoint.
`CacheSettings` reads all of this once, in one place; nothing else in HiSim touches these
variables.

## 6. Curated-dataset retrieval (`datasets.py`)

Typed retrieval functions, one per dataset kind, used by the weather component and the LPG
connector instead of hard-coded repo paths:

```python
get_weather_files(location: str, dataset: WeatherDataset, year: int) -> Path
get_pregenerated_lpg_profile(household_template: str, year: int, resolution_s: int) -> Path
```

Resolution order: local dataset directory (`inputs/weather/…` as today) → remote
`GET /datasets/…` with local write-through → **error** with an actionable message naming the
dataset id and the prewarm command. Additionally:

- `python -m hisim.caching prewarm [--datasets all|weather|lpg] [--setups ...]` downloads
  everything a given setup needs, for offline/air-gapped use and for baking warm caches into the
  RenoVisor container image.
- Once the remote path is proven in CI, the bulk weather data (121 MB) is removed from the repo;
  a small default subset (enough to run the example setups and base tests offline after
  `pip install`) stays bundled. Exact subset decided in phase 3.
- Datasets are content-addressed (§2.2): the blob's hash is its identity, a human-readable
  index maps names to hashes, and DTOs reference the hash (§3.1) — so a corrected weather file
  is a new hash, and it propagates into every downstream cache key automatically.
- License check for redistributing DWD/TRY data beyond the institute happens before the external
  endpoint serves weather data.

## 7. Authentication

Reads: anonymous on both endpoints — the public (external) endpoint is explicitly world-readable
so that users outside FZJ benefit from the cache; the client sends a token on reads only if it
happens to have one.

Writes are the integrity boundary — a writable endpoint feeds simulation inputs to every user.
Two mechanisms, chosen per environment:

### 7.1 GitHub runners: OIDC, no stored secrets

GitHub Actions mints short-lived, signed JWTs on request (`permissions: id-token: write`). The
workflow fetches a token with audience `hisim-cache` and sends it as the bearer token. The C#
server validates it as a standard JWT (ASP.NET Core `AddJwtBearer`, authority
`https://token.actions.githubusercontent.com`, GitHub's published JWKS) and then enforces
claims:

```
aud   == "hisim-cache"
repository == "FZJ-IEK3-VSA/HiSim"
```

Why this over a shared API key in GitHub Secrets: nothing to store, rotate, or leak — tokens
expire in minutes and are audience-bound. All CI runs of the repository may write, PR branches
included: with fully automatic fingerprints and content-addressed data files (§3), keys are
complete by construction, so an unchanged computation writes identical bytes and a changed one
writes under new keys — a PR's first push computes its new entries once and every later push
(and the post-merge main run) gets hits. Fork PRs cannot mint OIDC tokens under GitHub's
rules, so they are automatically read-only — the right trust boundary with zero configuration.

Two limits of this argument, accepted deliberately: fingerprints are client-computed claims,
so they defend against *accidents*, not a deliberately lying client — the adversary control is
the token boundary itself, and same-repo PR authors are inside the institute's trust boundary.
And the 409 rule (§2.1) remains the tripwire for key-schema bugs. Branch-scoped write
namespaces (the `actions/cache` isolation model) were considered and dropped as unnecessary
under complete keys; reintroduce them only if the conflict log ever shows real poisoning.

### 7.2 Cluster, RenoVisor containers, developers: static bearer tokens

Long-lived per-environment tokens (`hisim-cluster-2026`, `renovisor-backend`, one per developer
who needs write), issued and revocable on the server, supplied via `HISIM_CACHE_API_KEY` in
`.env` — the same handling as `UTSP_API_KEY` today. Tokens are tiered: `write` (opportunistic
cache PUT), `maintain` (dataset PUT, purge, stats). Compromise recovery = revoke the token +
purge by that token's producer metadata (§2.3).

## 8. Server-side storage & eviction

- Blobs on disk (or object storage) named by key; a small DB table per blob: key, size,
  created, last_accessed, producer commit, producer HiSim version, producing token id.
- Opportunistic tier: quota (e.g. 200 GB) with LRU eviction by `last_accessed`. This replaces
  any bespoke LRU logic — eviction is a storage policy, not an algorithm HiSim implements.
- Curated tier: never auto-evicted.
- Server-side writes are temp-file + rename; a killed upload never leaves a readable partial.

## 9. Miss tracking (and optional prewarming, later)

Every 404 on `/cache/` is logged (key, component, timestamp, client environment from the token /
User-Agent). `GET /misses` aggregates the top missed keys. If prewarming ever becomes worthwhile,
it is a scheduled **HiSim client** job (nightly CI or cluster job) that reads the miss report,
runs the corresponding configs with ordinary HiSim, and lets the normal write-back populate the
cache. The server never computes anything itself — this keeps the C# server free of any HiSim
version coupling.

## 10. Rollout phases

1. **`hisim/caching/` local tier** — package skeleton, `CacheKey` with code/helper/dependency
   fingerprints, atomic writes, `utils.get_cache_file` delegating. Pure refactor,
   no network, fixes the shared-filesystem race. Golden tests must stay green (all local keys
   change once due to the new schema — one-time full recompute, coordinated with a golden run).
2. **Remote read/write tier** — `remote.py`, `client.py`, settings, circuit breaker; the two
   server endpoints (§2.1) + `/health`; static-token auth; enable on the cluster and in
   RenoVisor first (highest volume, simplest auth).
3. **Curated datasets** — `/datasets/` endpoints, `datasets.py`, prewarm CLI, weather component
   + LPG connector switched to retrieval functions; then shrink the in-repo weather data.
4. **CI integration** — OIDC validation on the server, `id-token: write` + token fetch in the
   golden workflows, main-branch-only write policy. (Independently useful regardless of phases
   2–3: `actions/cache` on `inputs/cache` remains worthwhile for PR-branch runs, which are
   read-only against the service.)

Each phase is independently shippable and independently reversible (`HISIM_CACHE_NETWORK=off`
returns to phase-0 behavior at any time).

## 11. Out of scope for v1

- Server-side computation of any kind (see §9 for the client-side alternative).
- Caching anything inside the convergence loop (coupled components: building thermal model,
  heat pump interaction, EMS decisions) — only feed-forward `i_prepare_simulation` artifacts.
- Cross-user result sharing beyond the institute (external endpoint exists, but opening write
  access to third parties needs a separate review).
- Branch-scoped write namespaces (dropped: unnecessary under content-complete keys; reintroduce
  only if the 409 conflict log ever shows real poisoning).

## 12. Survey: the cached computations and their static producer forms

Result of reviewing every cache user (2026-08-22). Goal: each cacheable artifact becomes a
**static producer function** — pure, callable without a `Simulator`, signature
`f(dto) -> artifact` with a single calculation DTO per §3.1 — so the prewarm CLI and
miss-driven prewarming can populate the cache without running simulations.

| Component | Cached artifact | True inputs | Static? |
|---|---|---|---|
| `weather` | resampled full-year series | config + sim params + weather files | **already is** — `read_test_reference_year_data` + resampling, only needs extraction |
| `loadprofilegenerator_utsp_connector` | occupancy/consumption profiles | config + sim params (UTSP or local LPG) | **already is** — external calculation by design |
| `generic_pv_system` | AC power ratio series | weather series + PV config | **yes** — the predictive branch already computes the full series up front; needs weather passed as an argument instead of `SingletonSimRepository`/stsv |
| `building` (solar gains) | gains-through-windows series | weather series + window config | **yes** — `get_solar_heat_gain_through_windows` is nearly pure; full-series form exists in the predictive branch |
| `generic_car` | location / driven-meters series | LPG car data + sim params | **yes**, chained: consumes the LPG artifact |
| `generic_windturbine` | (today: in-memory memo per operating point) | weather series + turbine config | **yes, and it's an upgrade** — inputs are purely weather-driven, so the memo can become a full-series precompute like PV |
| `advanced_/more_advanced_heat_pump_hplib` | in-memory memo per rounded operating point | runtime loop state (return temperature) | **no full series** — operating points come from the convergence loop. Option: quantized full-grid map per heat-pump model as a curated dataset. Excluded from v1. |
| `solar_thermal_system` | per-timestep `flat_plate_precalc` frames | weather + config + **collector inlet temperature from storage (feedback!)** | **no — and the current cache is unsound, see below** |

Producer dependency DAG (what the prewarm CLI walks):

```
weather ──▶ pv_series
        ──▶ solar_gains
        ──▶ windturbine_series
lpg ──▶ car_series
```

Findings that fall out of the survey:

- **Solar thermal caching bug (exists today, locally).** The cached precalc frames depend on the
  collector inlet temperature, which is fed back from the storage — i.e. on the *rest of the
  system* — but the cache key covers only the collector config and sim params. Two setups sharing
  a collector config but differing elsewhere (storage size, heating system) share a key while
  having different correct values. This must be fixed before the shared service ships (either
  compute the precalc from the nominal inlet temperature `delta_temperature_n_k` implies — a
  model change — or remove this cache); shared, it would poison across users by design.
- **Deferred cache writes.** PV (non-predictive), building, and solar thermal fill their cache
  during `i_simulate` and write the file only at the final timestep — an aborted run populates
  nothing. Static producers fix this as a side effect: compute → write → simulate.
- **PV vectorization win.** The miss path calls pvlib once per timestep in a Python loop
  (525 600 calls for a minutely year). pvlib is natively vectorized over Series; the static
  producer should pass full series, likely turning the miss cost from minutes into seconds —
  which also lowers the stakes of every cache miss.
- **Producers live in their own modules**, next to their component in the same directory
  (e.g. `generic_pv_calculation.py` beside `generic_pv_system.py`), mirroring the ongoing
  `building/` split. Component modules stay at their current paths — the scenario JSONs
  resolve components by fully-qualified class name via `json_executor.py`, so moving them
  breaks every `.scenario.json`; if a component is ever moved into a subpackage, a re-export
  shim keeps the old path importable. This placement also tightens the cache key: the
  `code_fingerprint` hashes the producer module alone, so edits to component plumbing (KPIs,
  I/O declarations, docstrings) no longer invalidate cached artifacts.
- Producer modules obey strict one-way layering, like `hisim/config/`: they may import
  numpy/pandas/pvlib and config dataclasses, never `Component`, the simulator, or the
  singleton repository. This keeps them callable without starting HiSim and keeps their
  fingerprints from transitively swallowing the package. Splits land as pure-move PRs
  (goldens bit-identical); numeric changes such as the PV vectorization follow separately.
- `hisim/caching/producers.py` only maps artifact kinds to producer functions for the
  prewarm CLI.

## 13. Open questions

1. Blob size cap and total quota — need measured sizes of the largest current `.cache` files
   (UTSP full-year minutely results are the likely maximum).
2. Does the internal endpoint require read auth? (Recommendation: no — friction for zero gain
   inside the institute network.)
3. Which weather subset stays bundled in the repo for offline-after-install use?
4. Should the RenoVisor container prewarm at build time (bigger image, instant start) or first
   start (small image, slow first request)? Recommendation: build time for weather datasets,
   first-start for opportunistic entries.
