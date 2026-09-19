# Review: the frontend side's translator implementation spec v2 (2026-09-19)

**Reviewed:** `/home/contract-proposals/translator-implementation-spec.md` with its companions
`calculation-request.md`, `calculation-request.schema.json`, `calculation-request.mockup-1.yaml`,
`measure-capabilities.md`, `note-to-contract-owner-2026-09-19.md`, `backend-changes.md`.
**Checked against:** HiSim `origin/main` at `6f127c52` (the spec cites `8f619e4a`, five commits
behind; nothing it relies on moved), the branch `renovisor-backend` at `8a90c479`, and the
decision register `challenges.md` §9 and §12.

## 1. Verdict in three sentences

It is doable, and it is a good spec: precise, verified against real HiSim code, with a test per
rule and the honest `not_implemented_yet` list at its centre. About seventy percent of what it
asks for already exists on `renovisor-backend` in a different shape, so the work is a reshape,
not a rewrite. It contradicts five decisions of 2026-09-15, and those five have to be settled
before anyone implements, because they point the two efforts in different directions.

## 2. What the spec does not know

It was written against HiSim `main`, where `hisim/renovisor/` is still the v1 translator. It
does not know steps 3–6 on `renovisor-backend` exist. Its module plan maps almost one to one:

| Spec module | Exists on `renovisor-backend` as |
|---|---|
| `request.py` (pydantic, `extra=forbid`, catalogue table) | `inventory.py` (schema validation), `catalogue.py`, `options.py` |
| `apply.py` | `registry.py`, `effects.py`, `application.py` |
| `envelope.py` (series resistance, layer defaults) | `envelope.py` (same arithmetic, `UValueComposer`) |
| `tabula.py` (all `.N.` countries) | `tabula_ie.py` (IE only; generalising is small) |
| `translate.py` (base file + switches + overrides + self-check) | `parametriser.py`, `bindings.py`, `base_files.py` |
| `report.py` | `report.py` |
| `run.py`, `simulation.py` | `calculate.py` |
| `capabilities.py` (probe set over the catalogue) | `map.py` `MapData.collect()` — the same probe run, rendered as HTML instead of JSON |
| `not_implemented_yet.yaml` | `reasons.py` `NoEffect` codes + the registry's refusals, spread over code |

What the spec has that the branch lacks: the frozen catalogue table checked against
`measures.yaml`, the `not_implemented_yet.yaml` with its two-way test (T-NIY), the probe set as
data, `problems.json` with all problems at once, and the capability document as a deliverable.
Those four are worth adopting regardless of the decisions below.

## 3. The five contradictions with decided items

### C-1. Who derives the physics (spec rule 5 and D1 vs. 2026-09-15 §12)

The spec: the request carries U-values, conductivities and areas; the frontend's country pack
turns `300mm_cavity` + `cavity_beads` into a U-value and copies material properties out of
`materials.yaml`; HiSim reads no catalogue and no country table other than TABULA. Every element
U-value is *required*, so TABULA's own U-values are never used for the base state.

The register (§12, 2026-09-15): v1's philosophy, the user's answers are the input and HiSim
derives; the categorical envelope lists were to be defined with the catalogue owners (Q31).

This is the largest disagreement and it is a clean either/or. The spec's version has real
advantages for HiSim: no materials import, no spelling table, no Q31 meeting on HiSim's critical
path, and the content hash covers the numbers that were simulated. Its costs: two sources of
building physics (frontend table for U-values, TABULA for areas, ventilation and thermal mass),
a `materials.yaml` copy on the frontend, and the frontend owning a physics table it cannot
validate against a simulation. If the country pack table is wrong, HiSim simulates it faithfully
and nobody sees the error in a report. **Recommendation: accept the spec's D1.** The country
pack exists already on the frontend, the U-value arithmetic is identical on both sides, and it
removes a meeting from the critical path. Ask for one addition: the request carries the country
pack's id and revision (`envelope_source: {pack: ie, revision: …}`) so a wrong table is
traceable from a result.

### C-2. Enum spelling (spec rules 3–4 vs. C3)

The spec: values are the catalogue's, lowercase snake_case (`air_source_heat_pump`,
`conventional_radiator`), and the translator maps them once to HiSim members (`RADIATOR`).
C3 decided UPPER_SNAKE member names on the wire and no value maps anywhere.

**Recommendation: accept the catalogue's spelling on the wire** and reconcile it inside HiSim
by giving the vocabulary enums the catalogue string as their *value*:
`HeatGenerator.AIR_SOURCE_HEAT_PUMP = "air_source_heat_pump"`. The energy-system file keeps
writing member names, the request keeps the catalogue's, and the "map" is the enum definition
itself, generated into the capability document. C3's goal, one vocabulary and no drift, holds;
only the case convention changes. The catalogue is now the source of truth for both teams,
which is what the spec's rule 4 says and what the 2026-09-17 `measures.yaml` (ids written out,
lowercase, `options: []`) already delivers.

### C-3. Occupancy in the MVP (spec D7/D10 vs. Q20, Q12)

The spec: no LoadProfileGenerator in the image; every household runs as the CHR01 couple;
`number_of_residents`, `white_appliances` and `electric_vehicles` are `not_implemented_yet`;
the battery law uses a per-residents table of daily kWh (5/8/10/12/14, "to be reviewed").

Q20 decided local LPG plus a warm cache, and step 5 built it: household matching over the LPG
catalogue, the seam that reads a household's electricity before the run, the cold end-to-end
run in 25 s including one LPG computation. A CHR01-for-everyone MVP makes the household size
meaningless in every result and turns the battery sizing into an invented table. That is the
plausible-wrong-number failure the requirements were written to prevent (G5).
**Recommendation: keep Q20.** The image ships the LPG binary; the spec's §10.2 (remove the
silent `USE_UTSP → USE_LOCAL_LPG → USE_PREDEFINED_PROFILE` fallback) becomes a prerequisite
instead of a follow-up. If the image cannot carry the binary for a deployment reason, the
fallback must be an explicit, time-boxed MVP flag, and the capability document must say that
`number_of_residents` is not honoured.

### C-4. Base files (spec §7 vs. Q18)

The spec: translator-owned copies under `hisim/renovisor/base_files/`, derived from the
recorded grouped twins and extended with groups (`pv`, `solar_thermal`, `electric_vehicle`,
`night_setback`, `air_conditioner`) and a third EMS option (`ems_without_battery`), proved
against the twins by T-BASE (realized record equality).

Q18 decided: the recorded files are the base files, additions only through the golden gate, no
second version to validate. The spec's T-BASE is a serious mitigation, but it is a second set of
eleven files whose drift from the twins is caught only by a nightly `system_setups` test.
**Recommendation: put the spec's additions into the twins themselves.** A `pv` group, the
solar-thermal and EV groups, `ems_without_battery`, the night-setback and air-conditioner groups
are energy-system changes under the golden gate; the translator then loads
`energy_systems/*.grouped.energy_system.yaml` directly, and T-BASE becomes unnecessary because
there is nothing to compare. The constructor swaps the spec wants baked into the base files
(`Building.for_tabula_code`, `Weather.for_location`, `UTSPConnector.for_household` with
placeholders) are exactly what the branch's parametriser does at translate time; no placeholder
mechanism is needed.

### C-5. Failure semantics (spec rule 6 vs. Q4, Q6, Q13, Q14 and G5)

The spec: anything the translator does not implement is a note, never a failure, if it is on
`not_implemented_yet.yaml`; a package with solar thermal on an oil boiler simulates *without the
collector* and the result carries a note; a hybrid heat pump is simulated as an air-source heat
pump; a stub material row would have to be caught by the frontend. The register chose refusals
for exactly these cases ("fail hard").

The spec's mechanism is coherent because of the capability document: the frontend learns before
submission which measures and values are `not_implemented_yet` and can hide them. Whether it
hides them is listed as an open frontend decision (§12 open item 7). **Recommendation: accept
the mechanism with one hard condition**, written into the contract: a `not_implemented_yet`
*value* of a system-selecting option (`heating_system.type_of_system=hybrid_heat_pump`,
`hot_water_system.supply=separate_direct_electric`, `solar_thermal_system` on a generator without
wiring) is **not offered** by the UI, so it never reaches a request. Measures with no physical
model (`install_new_led_lights`, `outside_shading`) may be offered with the note, as the spec
says. Distinguishing the two classes in the capability document (`no_model` vs `substituted`)
costs one field and settles the argument.

## 4. What the spec is silent about

Nothing in it covers results beyond `all_kpis.json`: no lifecycle costs, no provenance objects,
no `result.json`, no embodied CO₂, no grants, no financing, no schedule and no stages. The
register decided all of these are HiSim's (Q21–Q24, §12). The spec says "the KPI document's
shape is a separate spec". That is fine as long as the separate spec exists; today the branch's
`result.py` is the only candidate. Two concrete consequences for the spec's text:

- §2.3 post-processing options: `COMPUTE_OPEX, COMPUTE_CAPEX` are the legacy stack; the costs
  the register wants come from `COMPUTE_LIFECYCLE_COSTS`, which the `HISIM_IN_DOCKER_CONTAINER`
  whitelist strips today (`postprocessing_main.py`, finding F3 in `challenges.md` §11). The
  spec's claim "pruning already allows exactly these options" is true for its set and false
  for the one the result payload needs. One line in HiSim fixes it; it must be in §10.
- §2.3 `country: location.country` on `SimulationParameters` crashes `COMPUTE_KPIS` for `IE`
  (`configuration.py`: emission and price factors for DE and AT only; finding F2). The branch
  attaches `EconomicParameters(country)` for the cost engine and leaves the legacy path's
  country alone. The spec must either add the Irish rows to that table (data task) or adopt the
  branch's route.

## 5. Facts in the spec I checked and confirmed

`LocationEnum` has `IE`, `ES`, `NL`; presets `night_setback_controller.standard`,
`simple_air_conditioner.standard`, `generic_electric_heating.resistive`,
`generic_district_heating.standard`, `solar_thermal_system.flat_plate`,
`more_advanced_heat_pump_hplib.air_water` exist; `GenericBoilerConfig.boiler_type` with
`CONVENTIONAL`/`CONDENSING` exists; the v3 schema lists both `Building` class spellings; no
twin has `ems_without_battery`; `set_economic_parameters` exists. Two claims are stale:
`generic_car` has no `bev_medium` preset (it has the `for_household` constructor), and the
`USE_PREDEFINED_PROFILE` fallback chain is still in the connector.

## 6. Smaller points, by section

- **§3.3 `heating_reference_temperature_in_celsius` per country as a constant** (IE −3, NL −10,
  ES 2, "to review"): kept as a reviewed constant. *(Corrected 2026-09-19: this review first suggested
  reading TABULA's `Theta_e_Base` instead; that column is the 12 °C heating-degree-day base for every
  country and `Theta_e` the annual mean, so neither is a design condition. Since HiSim #771 the weather
  owns the value and requires it, so the constant is the only honest source.)*
- **§4.2 fixed layer defaults** (100/60/150/300 mm per measure) versus the register's Q11
  (target-driven thickness reaching the regulatory target U). Under D1 the frontend knows the
  current U-value and could send the thickness; either default is defensible, but the spec
  should say the Experts default is the frontend's if it computes one. Otherwise fixed defaults
  are fine and simpler.
- **§4.2 `NEW_WINDOW_U`, `NEW_DOOR_U`, fixed materials** (EPS beads λ 0.037, stone wool 0.035):
  the register (Q6, Q13) wanted these from the materials database rather than translator
  constants. Under D1 they can travel in the request like any material property; the frontend
  fills them from `materials.yaml` window and door rows once those exist. Until then the spec's
  constants are the only option; mark them `approximated` with the source, as it does.
- **§3.13 PV**: `size_in_percent_of_roof_area → share_of_maximum_pv_potential` and
  `power_in_watt` pins the array; a `pv` group for absence. Matches the branch's finding F10
  except for the group, which the twins need (C-4).
- **§3.14 battery**: the C-rate pin (`inverter = 500 W per kWh`) is a HiSim law the class
  already applies; pinning both is harmless but redundant. `days_to_cover × table` is the part
  C-3 replaces with the LPG-based estimate.
- **§5.3 TABULA variant always `001`**: correct under D1, since every U-value is sent.
  `retrofit_status` disappears from the request; the register's Q10 becomes moot.
- **§5.4 `pv_self_consumption_optimised → ems_without_battery`**: a good use of the EMS; needs
  the new variant option in the twins (C-4).
- **§6 mapping report**: same statuses as the branch's minus `ignored` and `non_simulation`,
  plus `not_implemented_yet`. Adopt the spec's vocabulary; the branch's `report.py` changes one
  enum.
- **§2.1 exit codes 0/2/3/5 with `problems.json` / `translator_error.json`**: the branch has
  0/2/3/4 with `errors.json` and `calculation.json`. Adopt the spec's, keep the reason-code
  catalogue as the `code` field, keep `calculation.json` as the success manifest.
- **§1 keeps `uploader.py`**: harmless; the branch deleted it. Nothing depends on it.
- **§2.2 `renovisor_<hash>` in the file name and `name:`**: deterministic per content, so
  consistent with R10.
- **§7 `Car_1`, `CarBattery_1`, `L1EVChargeControl_1`**: the twin's keys are
  `Car_2_22kW_Charging_Power_avg_Speed_30_kmh_0` etc.; renaming is fine but the bindings must
  follow. `count = 2 duplicates the triple`: authoring, under the gate.

## 7. What implementing it costs, given the branch

If the five recommendations above are accepted, the work is: adopt the request schema (a
swap of the validation target, plus pydantic models or the JSON Schema directly), reshape
`registry`/`effects`/`application` into the spec's `apply` with the frozen catalogue table and
the `not_implemented_yet.yaml`, generalise `tabula_ie` to `tabula`, add the groups and the EMS
option to the twins under the gate, rename exit codes and report statuses, and emit the
capability document from the existing probe collection. Roughly the size of steps 3–5 together,
with most of the physics and the parametriser reused. If instead the spec is implemented as
written on `main`, the branch's five steps are discarded and rebuilt, and the results side
(step 6) has no home.

## 8. Decisions to take

| # | Question | Recommendation |
|---|---|---|
| D-A | Physics derived by the frontend (spec D1) or by HiSim (v1 philosophy)? | Frontend, with `envelope_source` provenance in the request |
| D-B | Wire spelling: catalogue lowercase or HiSim UPPER_SNAKE? | Catalogue lowercase; HiSim enums carry it as their value |
| D-C | LPG in the image, or CHR01 for everyone in the MVP? | LPG in the image (Q20 stands); fallback removal becomes a prerequisite |
| D-D | Translator-owned base files or the twins extended under the gate? | The twins, extended |
| D-E | `not_implemented_yet` system values: hidden by the UI, or refused by HiSim? | Hidden by the UI, stated in the contract; `no_model` vs `substituted` in the capability document |
| D-F | Where does the result payload spec live? | The branch's `result.py` and `challenges.md` Q21–Q24 become the "separate spec" the document refers to; add `COMPUTE_LIFECYCLE_COSTS` to §2.3 and §10 |
