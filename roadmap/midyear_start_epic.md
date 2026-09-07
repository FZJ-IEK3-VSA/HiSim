# Mid-year simulation windows: the epic

Written 2026-09-06 from three detailed analyses (irradiance/thermal chain; profile-file chain;
cross-cutting), each of which verified its load-bearing claims by running probes rather than by
reading alone. Line numbers are from the analysis date and will drift; the named constructs will
not. The parity rig's July window is fenced pending this epic (`scripts/p3_parity_matrix.py`,
R11.5 as amended 2026-09-06); this document is what unfences it.

## The problem

**Mid-year simulation windows are January in disguise, and always have been.** Every
profile-driven component in HiSim — weather (all six data sources, cached and uncached), PV,
building solar gains, LPG occupancy (deliberately, with a comment), the cars, the smart devices,
the CSV loader, and the heating-season gates in the heat-pump and CHP controllers — indexes its
year-long profile from timestep 0 as if that were the 1st of January, whatever `start_date` says.
This has been so since the initial public release: `start_date` has never appeared in
`weather.py`. The two places that *do* honour the date — the solar-thermal collector's own sun
position, and the LPG connector's unused `USE_UTSP` mode — make a mid-year run incoherent rather
than correct, because they move alone.

The wrongness is silent and large. Measured: a July week in Aachen yields **37 kWh** of PV where
the true July offset yields **426 kWh** — an 11.5× error — under a results index that is stamped
with July timestamps (`simulator.py` builds the index from `start_date`), so the mislabelling is
written to disk. The solar-thermal collector meanwhile sees a genuine July sun (apparent zenith
27.7°) wired to January irradiance.

**What is *not* affected:** every shipped parameter file, every golden reference, every recorded
twin, RenoVisor and the freshness gates run January or full-year windows — no committed number is
wrong. The latent exposure is the HPC harness's `three_months` option (starts 1 March) and anyone
who ever hands a mid-year `start_date` to `hisim_main`.

## The design: B′ — the profile owner resolves one offset against its own frame

Four designs were assessed:

- **A (absolute indexing)** — run the loop over year-absolute timesteps, keep profiles unchanged.
  Rejected: every *window-sized* array in the fleet (result caches, per-timestep outputs,
  window-scoped solar positions) breaks at once, so nothing can land independently; and it turns a
  fleet-wide silent bug into a fleet-wide loud one that must be fixed everywhere before anything
  runs.
- **A′ (simulate the prefix, discard)** — solves initial states by construction, but costs up to
  half a year of extra simulation per run (26× for a July week) and *enlarges* the cache problem:
  every window-sized cache would grow to prefix+window. Rejected; a short warm-up is a separate,
  later feature (see initial states).
- **B (slice at load, everywhere)** — right idea, forfeits the weather cache's reuse and leaves
  the offset logic scattered.
- **B′ (chosen)** — **the profile-OWNING component keeps its year internally, resolves ONE window
  offset by looking `start_date` up in the frame its source actually delivered, and publishes and
  emits only the window. Downstream stays `[0, N)` and unchanged.**

B′ has two shapes, and conflating them is the epic's most likely implementation error:

1. **"Slice a kept year"** — weather (offset resolved against the resampled series' own
   `DatetimeIndex`), the shipped predefined LPG profiles (their CSVs carry a real `Time` column
   and the JSONs a `StartTime` — the data is self-describing and the code currently throws the
   description away), the shipped wind CSV.
2. **"Ask the source for the real window; the offset is zero"** — `USE_LOCAL_LPG` (send the real
   `start_date` to the generator: this changes *content*, July household behaviour instead of
   January's, which is the point) and `USE_UTSP` (already sends the truth; only the Jan-1
   relabelling afterwards is wrong).

**The invariant, verbatim, for every implementation and review:** *resolve the window against the
frame the source actually delivered — never "skip N rows from January 1".* A blanket
`skiprows=offset` double-shifts every source that already delivered the window.

The payoff, verified rather than assumed: **PV, building and solar-thermal need zero functional
lines** (PV's `[:timesteps]` truncation degenerates to a no-op; the building's solar gains have
always come from its wired inputs — its own solar cache is dead code, see the repairs table; the
collector already follows `start_date`), and the whole occupancy/car chain is fixed inside the LPG
connector, which is the profile owner for both.

## The two traps that decide success (write their tests before the code)

1. **The offset must never be arithmetic.** The Berlin-anchored weather sources index elapsed
   instants: for 1 July 2021, the correct row is **260 550**; naive minutes from the series' own
   start give 260 610 (the March DST hour), naive minutes from Jan 1 give 260 640 (DST plus the
   source's 00:30 anchor). The error is *energy-neutral over whole days* — no aggregate or KPI
   test can ever catch it — and only a per-timestep phase test against wall-clock labels can. Pin
   the three numbers, and pin a December window too (where the DST correction is zero again).
   The LPG frame, by contrast, is gapless naive local time (uniform 1-minute diffs across all
   525 600 rows, measured), so there the hazard is not the row arithmetic but the UTC↔local hop:
   `convert_lpg_data_to_utc` hard-codes the winter +1 h, one hour short for CEST.
2. **The warm cache makes the fix a silent no-op.** Every cache key already contains
   `start_date`, so a pre-fix July entry holds January content under a key the fixed code
   validates happily — and a staged rollout poisons its own caches between stages. The epoch
   mechanism below is not optional, and the oracle runs cold-then-warm and asserts equality.

## The window contract (one chokepoint)

`SimulationParameters.__init__` is provably the only construction path (three external call sites,
all keyword construction; every factory routes through it). It gains four validations: start at
midnight; whole days; `start` and `end − 1 timestep` in the same year (a window crossing Dec 31
has no single-year source and the DST surgery is keyed to one year — refuse loudly); duration
divisible by `seconds_per_timestep` (today `timesteps` truncates silently). **Leap-year refusal
does not live here** — whether an 8760-row source can serve 2020 is the source's answer, so it
lands in the offset resolver, next to the lookup that discovers the missing label.

The simulator core itself is clean: the loop is pure position, convergence is calendar-agnostic,
the results index is already built from `start_date` (verified for six windows including DST-span
and leap year), and KPI computation groups by the frame's index with no January assumption
anywhere. Three plot/report sites do assume January (month tick labels on 365-point plots, the
carpet's midnight anchor, the single-day chart titled "january 1st" whatever the day) — a small
labelling PR, shipped with the epic so the first real July report does not say January.

## Caches: the epoch

All six caches route through one function, `utils.build_cache_key_string`, and it has **no
version slot** — `hisim/caching/local.py` documents this exact gap in its own words. The
structural fix (`hisim/caching/keys.py`'s `CacheKey`, which fingerprints the producer's import
closure) has zero adopters and requires producer extraction first; that is the cache-service
epic, not this one.

The mechanism here: a class-scoped `CacheEpoch.CURRENT` constant prepended to the key material in
`build_cache_key_string` (not in `get_unique_key`, which has a second, descriptive caller). One
line to bump, invalidating *everything at once* — deliberately, because per-producer epochs are
six constants and six chances to forget one, and the forgotten one reads green. Every PR of this
epic that changes what a cached producer writes bumps it, with the PR number as the value so the
history reads. CI never shares a cache across the epic's branches (the per-run empty cache
directory pattern the parity rig already uses).

## Initial states (storages, thermal mass, batteries)

The inventory, measured and cited in the analyses, in one line each: building thermal mass starts
at 22 °C (winter-typical; still 4.9 % off on day 7 after a 6 K perturbation — the largest and
slowest transient); the buffer storage starts at a hard-coded 35 °C (winter-typical, and its
dataclass default of 25 °C disagrees with the hard-coded value); DHW storage 60 °C
(season-neutral, correct); battery SOC 0 (arbitrary, settles in a PV day); hydrogen storage 80 %
full in one package and empty in the other (arbitrary twice over); ~25 controllers start "off"
with their minimum idle time blocking the first 15–60 minutes (season-neutral start artefact);
and one column (`WaterMassFlowHDS`, a bang-bang controller) never settles at all — it re-phases
onto a different limit cycle, permanently 16.7 % out, which is why no warm-up length can buy
exactness.

**Decision:** keep the defaults and document the measured per-component transient (the validation
design absorbs it), plus exactly one season-aware change — the buffer/space-heating storage seeds
from the heating-season gate (outside the season it starts at ambient 20 °C instead of 35 °C: a
buffer nobody has charged sits at room temperature — the one derivation defensible in a
sentence). Warm-up prefixes are rejected on the measured evidence (weeks would be needed, and the
limit-cycle columns never converge). Checkpoint handover is the honest follow-up but is blocked on
repairs first: `i_save_state` is a convergence scratchpad, not a checkpoint contract, and two live
bugs prove it (the EMS restores a reference, not a clone; the heat-distribution restore
self-assigns) — fix those on their own merits before checkpointing is discussable. One blocker to
untangle before any building-side seasonal default: `initial_internal_temperature_in_celsius`
doubles as the *permanent* window-opening comfort floor in the building's free-cooling rule — a
field named "initial" that is silently a model parameter.

## Validation: the split oracle

The strongest available truth: a full-year run is correct today, so a mid-year window must
reproduce the same calendar slice of a full-year run. Measured on a real setup, the comparison
splits perfectly:

- **The memoryless half — weather, occupancy, solar gains, meters — agrees BIT-EXACTLY from
  timestep 0** (verified: 30+ columns at exactly 0.0 deviation under a deliberately perturbed
  initial state). This is precisely the layer the epic changes, so **the gate is exact, tolerance
  zero, on those columns**.
- **The stateful half** (thermal mass, storages, the boiler they drive) carries a days-long
  transient and one permanent limit-cycle re-phasing — so it is a **report, never a gate**:
  per-day deviation tables for days 1, 2, 3, 5, 7, read by a person.
- **The memoryless column set is discovered, not maintained:** run the full-year reference twice
  with a perturbed initial-state seed; the columns whose difference is identically zero ARE the
  exact-comparable set. Self-calibrating as components change.
- **The seeded restart is the end state — the report tier exists to shrink to nothing.** Run the
  full year once, read every stateful component's state at the window start (the battery's SOC on
  the 1st of July, the storage temperatures, the thermal-mass temperature, the controllers' modes
  and timers), hand those to the window run as startup values, and the *whole* comparison becomes
  an exact gate: with its complete mid-cycle state handed over, even a bang-bang controller
  continues on the very limit cycle the year run was on, so the one column no warm-up could ever
  buy back joins the gate too. What that costs is completeness — a partially seeded component just
  re-imports its transient — and not every component can express its state as configuration today
  (controller idle timers, the meters' cumulative sums, the EMS's internals are not config
  fields). So the seeded tier grows component by component: a column moves from the report to the
  exact gate the moment its component's full state is expressible as startup configuration, and
  the cumulative meter columns move immediately, gated on first differences instead of levels.
  This is checkpoint handover at the configuration level, which is why it shares its
  prerequisites with the `i_save_state` repairs below.

The harness: `scripts/midyear_oracle.py`, modelled on the parity rig (per-run empty cache
directory, one process, one printed verdict table, a tolerance flag that shouts when nonzero),
reusing `ResultComparison` verbatim — its index check refuses a misaligned slice for free, since
both frames carry `start_date`-built indexes. Plus four unit anchors in `tests/`: first-timestep
identity against the source's own labels; the DST phase pins (260 550 / 260 610 / 260 640, and a
December mirror); each weather reader's `index[0]` pinned (the six sources use three different
anchors — Berlin 00:30, Berlin 00:00 after the padding row ``Weather.interpolate`` prepends,
UTC 00:00 — which is exactly why "resolve against the delivered frame" is the rule); and the
cold-then-warm cache equality assertion.

## The PR plan

```
PR1 (window contract; YEARLYFORECAST→window renames; plot labels; delete dead smart device)
 └─ PR2 (offset primitive + weather adopts it; leap refusal; DWD_15MIN reader; delete dead
         building solar cache; the four unit anchors; build the oracle)        [epoch bump]
     ├─ PR3 (convert_lpg_data_to_utc rewritten: localize→convert→slice)       [epoch bump]
     │        — changes January numbers too (the current code starts every window at source
     │          row 60 and fabricates the final hour): GOLDEN RE-BLESS, alone, one cause
     │   └─ PR4+5 (LPG connector owns occupancy + cars, all three modes;
     │             seasonal gates go day-of-year — MUST SHIP TOGETHER,
     │             or a July window heats the house)                          [epoch bump]
     └─ PR6 (solar thermal: drop its naive-UTC duplicate sun, consume the
             weather's published window — 3 solar-thermal goldens move)       [epoch bump]
          └─ PR7 (lift the fence in its five sites — the workflow's hand-kept window
                  options among them, which nothing derives from the code; invert the
                  fence test into "July differs from January"; bless the July goldens)
              └─ PR8+ (startup-state configuration: every stateful component learns to
                       express its complete state as startup values, one component per
                       PR, and its columns move from the oracle's report tier to the
                       seeded exact gate as it lands)
```

Blast-radius rules: PR2 and PR4+5 must not move a single January golden (offset 0 on 1 January —
each PR's own cheapest proof); PR3 is the deliberate January re-bless and ships alone so the
golden diff has exactly one cause; PR7 adds the `july_week` parameter set to the golden config and
blesses the first true July references. The PR8+ series moves no golden at all: startup values
default to today's constants, and each PR's proof is its component's columns going exactly to zero
in the seeded oracle.

## Independent repairs surfaced by the analyses

In scope, batched as noted: the leap-year refusal (PR2 — the offset lookup is where "this source
cannot serve this date" is decidable); the DWD_15MIN reader sizing its index from the *run's*
duration instead of the file (PR2 — the one reader that cannot be sliced until anchored); the dead
building solar-gain cache (PR2 — **delete, don't fix the `hasattr` typo**: repairing it would
serve stale entries past every check and add a sixth window-sensitive cache); the solar-thermal
naive-UTC sun and its duplicate sun position (PR6); the dead `generic_smart_device` (PR1 — zero
references, raises `KeyError` on first call; deleting it also retires the independently broken
`convert_lpg_timestep_to_utc` and the test that pins its wrong output).

Out of scope, each with its trigger: `generic_price_signal` emits a constant (element `[0]`
forever) — not profile-driven, cannot be made wrong by this epic; fix when a setup needs a
time-varying tariff. The `i_save_state` reference/self-assignment bugs — live convergence-loop
defects, prerequisite for checkpointing; on the cleanup list. The initial-state default
contradictions (storage 25 vs 35 °C, EV SOC 0 vs 0.5, H₂ 0 vs 400 kg) — settle when the
season-aware storage seed lands. The MPC's uninterpretable `10/15` Wh literal — flag for whoever
touches the MPC. The building's dual-purpose `initial_internal_temperature_in_celsius` — split
before any building-side seasonal default.
